"""走势图月度价格：多源降级的 seam（腾讯 → 东财 push2his → mootdx）。

为什么单独成 module：三个源此前写在 src/api.py 里，靠 `_get_monthly_prices_*`
三个函数 + 一段 if 链串起来，没有统一的 interface——
- 新增/下线一个源要改 api 的私有函数与调用链；
- 用哪个源兜住的只能看 debug 日志猜（数据铁律：来源可追溯）；
- 测试只能 patch api 的私有函数名，改实现就断。

interface 只有两个：PriceSource（名字 + 取数函数）与
fetch_monthly_prices(code) → (rows, source_name)。
"""
import logging
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from .datasource.base import MonthlyPrice

logger = logging.getLogger(__name__)

Provider = Callable[[str], List[MonthlyPrice]]

MONTH_COUNT = 120   # 近 10 年；三个源统一窗口，换源不换区间


def _fetch_tencent(stock_code: str) -> List[MonthlyPrice]:
    """腾讯 fqkline：双口径（前复权画图 / 不复权算股息率）。

    不复权请求失败仅 close_nominal=None（该段股息率 null，价格线仍可画）。
    """
    try:
        from .tencent_quote import fetch_kline_rows
        rows = fetch_kline_rows(stock_code, period="month", count=MONTH_COUNT)
        if not rows:
            logger.warning("腾讯K线接口无 qfqmonth 数据: %s", stock_code)
            return []

        # 逐行防护：单行脏数据只丢该行，不破坏前复权主链路
        nominal_map = {}
        for row in fetch_kline_rows(stock_code, period="month", count=MONTH_COUNT, fq="") or []:
            try:
                nominal_map[row[0]] = float(row[2])
            except (ValueError, IndexError, TypeError):
                continue

        out = []
        for row in rows:
            try:
                out.append(MonthlyPrice(
                    date=row[0],
                    close=float(row[2]),
                    close_nominal=nominal_map.get(row[0]),
                ))
            except (ValueError, IndexError, TypeError):
                continue
        logger.debug("腾讯K线获取月度价格 %s: %d 条", stock_code, len(out))
        return out
    except Exception as e:
        logger.warning("腾讯K线获取月度价格失败 %s: %s", stock_code, e)
        return []


def _fetch_eastmoney(stock_code: str, count: int = MONTH_COUNT) -> List[MonthlyPrice]:
    """东财 push2his klt=103：备份源（腾讯 WAF 限频时的退路）。

    fqt=1 前复权 + fqt=0 不复权，字段 f51=日期、f53=收盘。klt=103 返回全历史
    （平安 235 个月、格力 355 个月），按最近 count 根截取以对齐腾讯窗口。
    """
    try:
        import requests

        market = "1" if stock_code.startswith("6") else "0"
        base = (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={market}.{stock_code}&fields1=f1,f2,f3&fields2=f51,f53"
            "&klt=103&beg=0&end=20500101&fqt="
        )
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

        def _get(fqt: str) -> dict:
            resp = requests.get(base + fqt, headers=headers, timeout=10)
            if resp.status_code != 200:
                return {}
            payload = (resp.json() or {}).get("data") or {}
            out = {}
            for line in payload.get("klines") or []:
                parts = str(line).split(",")
                if len(parts) < 2:
                    continue
                try:
                    out[parts[0]] = float(parts[1])
                except ValueError:
                    continue
            return out

        qfq = _get("1")
        if not qfq:
            logger.warning("东财K线接口无月度数据: %s", stock_code)
            return []
        try:
            nominal = _get("0")
        except Exception as e:  # 不复权仅影响股息率，不该拖垮价格线
            logger.debug("东财不复权月度价获取失败 %s: %s", stock_code, e)
            nominal = {}

        out = []
        for date_str in sorted(qfq)[-count:]:
            close = qfq[date_str]
            if close <= 0:
                continue
            out.append(MonthlyPrice(
                date=date_str, close=close, close_nominal=nominal.get(date_str)))
        logger.debug("东财K线获取月度价格 %s: %d 条", stock_code, len(out))
        return out
    except Exception as e:
        logger.warning("东财K线获取月度价格失败 %s: %s", stock_code, e)
        return []


def _fetch_mootdx(stock_code: str) -> List[MonthlyPrice]:
    """通达信协议：不复权原始价，close 与 close_nominal 同值（最后兜底）。"""
    try:
        from .datasource.mootdx_source import get_quotes_client
        client = get_quotes_client()
        df = client.bars(symbol=stock_code, frequency=6, offset=MONTH_COUNT)
        if df is None or df.empty:
            return []
        out = []
        for _, row in df.iterrows():
            try:
                date_str = str(row.get("datetime", ""))[:10]
                close = float(row["close"])
                if close <= 0:
                    continue
                out.append(MonthlyPrice(date=date_str, close=close, close_nominal=close))
            except (ValueError, KeyError, TypeError):
                continue
        out.sort(key=lambda r: r.date)
        logger.debug("mootdx 获取月度价格 %s: %d 条", stock_code, len(out))
        return out
    except Exception as e:
        logger.warning("mootdx 获取月度价格失败 %s: %s", stock_code, e)
        return []


@dataclass(frozen=True)
class PriceSource:
    """一个月度价格源：名字用于如实标注来源，fetch 返回空列表表示这个源没数据。"""
    name: str
    fetch: Provider


# 降级顺序即此列表顺序（改顺序 = 改策略，测试钉住）
DEFAULT_PROVIDERS: Tuple[PriceSource, ...] = (
    PriceSource("腾讯", _fetch_tencent),
    PriceSource("东财", _fetch_eastmoney),
    PriceSource("mootdx", _fetch_mootdx),
)


def fetch_monthly_prices(
    stock_code: str,
    providers: Optional[Sequence[PriceSource]] = None,
) -> Tuple[List[MonthlyPrice], str]:
    """按顺序试各源，返回 (月度价格, 实际来源名)；全部无数据时返回 ([], "")。"""
    for source in DEFAULT_PROVIDERS if providers is None else providers:
        rows = source.fetch(stock_code)
        if rows:
            return rows, source.name
    logger.warning("所有数据源均无法获取 %s 的月度价格", stock_code)
    return [], ""
