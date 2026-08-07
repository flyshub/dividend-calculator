"""
mootdx 数据源适配器 — 基于通达信协议（pytdx）

提供三类数据：
  1. 实时行情 + K线（替代 akshare 行情接口）
  2. 除权除息 / 分红记录（替代 akshare history_dividend_detail）
  3. F10 公司资料 — 财务指标 + 行业分类（替代东方财富 push2）

通达信协议是二进制协议，全球可用，不依赖东方财富服务器。
"""
import logging
import math
import re
from typing import Optional, List, Tuple, Dict
from collections import OrderedDict

from mootdx.quotes import Quotes

from .base import StockInfo, DividendDetail, DataSource
from ..utils import infer_fiscal_year, ensure_6digit

logger = logging.getLogger(__name__)

# 模块级单例 — 避免每次调用重新连接通达信服务器
_quotes_client: Optional[Quotes] = None


def get_quotes_client() -> Quotes:
    """获取 mootdx Quotes 客户端单例，自动重连"""
    global _quotes_client
    if _quotes_client is None or _quotes_client.closed:
        _quotes_client = Quotes.factory(
            market='std', multithread=True, heartbeat=True
        )
    return _quotes_client



# ────────────────────────────────────────────────────────────────
# MootdxSource — 实现 DataSource 协议
# ────────────────────────────────────────────────────────────────

class MootdxSource:
    """mootdx 统一数据源

    同时提供 StockInfo（行情+股本）和 Dividend（除权除息）两类数据。
    通过 F10 文本解析额外提供财务指标和行业分类。

    Args:
        client: 可选的 mootdx Quotes 客户端。不传则使用全局单例。
    """

    def __init__(self, client: Optional[Quotes] = None):
        self._injected_client = client

    def _get_client(self) -> Quotes:
        """获取 mootdx 客户端：优先用注入的，否则用全局单例"""
        if self._injected_client is not None and not self._injected_client.closed:
            return self._injected_client
        return get_quotes_client()

    @property
    def name(self) -> str:
        return "mootdx"

    @property
    def priority(self) -> int:
        return 5  # 最高优先级

    # ── StockInfoProvider ──────────────────────────────────────

    def get_stock_info(self, stock_input: str) -> Optional[StockInfo]:
        """获取股票基本信息：当前价格 + 总股本

        价格来自实时行情 quotes()，总股本来自财务快照 finance()。
        """
        stock_code = ensure_6digit(stock_input)
        if not stock_code:
            return None

        try:
            client = self._get_client()

            # 获取实时价格
            price = self._get_price(client, stock_code)
            if price is None:
                logger.debug("mootdx 无法获取 %s 价格", stock_code)
                return None

            # 获取总股本
            total_shares = self._get_total_shares(client, stock_code)
            if total_shares is None:
                logger.debug("mootdx 无法获取 %s 总股本", stock_code)
                return None

            return StockInfo(
                stock_code=stock_code,
                current_price=price,
                total_shares=total_shares,
            )

        except Exception as e:
            logger.debug("MootdxSource.get_stock_info(%s) 失败: %s", stock_code, e)
            return None

    def _get_price(self, client: Quotes, stock_code: str) -> Optional[float]:
        """通过实时行情获取最新价"""
        try:
            df = client.quotes(symbol=stock_code)
            if df is not None and len(df) > 0 and 'price' in df.columns:
                price = float(df['price'].iloc[0])
                if price > 0:
                    return price
        except Exception as e:
            logger.debug("mootdx quotes 获取价格失败 %s: %s", stock_code, e)
        return None

    def _get_total_shares(self, client: Quotes, stock_code: str) -> Optional[float]:
        """通过财务快照获取总股本"""
        try:
            df = client.finance(symbol=stock_code)
            if df is not None and len(df) > 0 and 'zongguben' in df.columns:
                shares = float(df['zongguben'].iloc[0])
                if shares > 0:
                    return shares
        except Exception as e:
            logger.debug("mootdx finance 获取总股本失败 %s: %s", stock_code, e)
        return None

    # ── DividendProvider ───────────────────────────────────────

    def get_latest_dividend(
        self, stock_code: str, stock_info: StockInfo
    ) -> Tuple[float, Optional[str], List[DividendDetail], str]:
        """获取最近一个完整财年的现金分红总额

        通过 xdxr() 获取除权除息记录，按财年分组，取最新有年报的完整财年。
        """
        try:
            client = self._get_client()
            xdxr_df = client.xdxr(symbol=stock_code)

            if xdxr_df is None or xdxr_df.empty:
                return 0.0, None, [], "mootdx xdxr 无分红数据"

            return self._parse_xdxr(xdxr_df, stock_info)

        except Exception as e:
            logger.debug("MootdxSource.get_latest_dividend(%s) 失败: %s",
                           stock_code, e)
            return 0.0, None, [], f"mootdx 分红获取失败: {e}"

    def _parse_xdxr(
        self, df, stock_info: StockInfo
    ) -> Tuple[float, Optional[str], List[DividendDetail], str]:
        """解析 xdxr 除权除息数据，提取最近完整财年分红"""
        # 只取除权除息记录（category=1）
        df = df[df['category'] == 1].copy()
        if df.empty:
            return 0.0, None, [], "无除权除息记录"

        # 推断每条记录的财年和报告类型
        records = []
        for _, row in df.iterrows():
            try:
                y, m, d = int(row['year']), int(row['month']), int(row['day'])
            except (ValueError, KeyError):
                continue

            fenhong = round(float(row.get('fenhong', 0) or 0), 4)
            if math.isnan(fenhong) or fenhong <= 0:
                continue

            result = infer_fiscal_year(y, m)
            fiscal_year, is_annual = result.year, result.is_annual
            records.append({
                'fiscal_year': fiscal_year,
                'is_annual': is_annual,
                'fenhong': fenhong,
                'date': f"{y:04d}-{m:02d}-{d:02d}",
            })

        if not records:
            return 0.0, None, [], "无有效除权除息记录"

        # 按财年分组
        from collections import defaultdict
        yearly: Dict[int, Dict] = defaultdict(
            lambda: {'total': 0.0, 'has_annual': False, 'details': []}
        )
        for r in records:
            fy = r['fiscal_year']
            yearly[fy]['total'] += r['fenhong']
            yearly[fy]['has_annual'] = yearly[fy]['has_annual'] or r['is_annual']
            yearly[fy]['details'].append(
                DividendDetail(
                    report_time=f"{fy}{'年报' if r['is_annual'] else '中报'}",
                    dividend_per_10=r['fenhong'],
                )
            )

        # 选最新有年报的财年
        for fy in sorted(yearly.keys(), reverse=True):
            if yearly[fy]['has_annual']:
                return self._build_dividend_result(fy, yearly[fy], stock_info)

        # 回退：最近财年（无年报时）
        latest_fy = max(yearly.keys())
        logger.warning("未找到有年报的财年，回退到最近财年: %s", latest_fy)
        return self._build_dividend_result(latest_fy, yearly[latest_fy], stock_info)

    def _build_dividend_result(
        self, fiscal_year: int, year_data: dict, stock_info: StockInfo
    ) -> Tuple[float, Optional[str], List[DividendDetail], str]:
        """根据年度汇总数据构建返回结果"""
        total_per_10 = year_data['total']
        dps = total_per_10 / 10.0
        total_shares = stock_info.total_shares
        total_dividend = dps * total_shares

        dividend_list = [
            f"{d.report_time}: 10派{d.dividend_per_10}元"
            for d in year_data['details']
        ]
        explanation = (
            f"{fiscal_year}年度 {'，'.join(dividend_list)}，"
            f"合计10派{total_per_10:.3f}元(每股{dps:.4f}元)，"
            f"总股本{total_shares / 1e8:.2f}亿股，"
            f"总分红{total_dividend / 1e8:.2f}亿元"
        )

        return total_dividend, str(fiscal_year), year_data['details'], explanation

    # ── F10 资料解析（财务 + 行业）──────────────────────────────

    def get_f10_category(self, stock_code: str, category: str) -> Optional[str]:
        """获取 F10 资料的指定分类文本

        Args:
            stock_code: 6位股票代码
            category: 分类名，如 '财务分析'、'行业分析'、'分红扩股'

        Returns:
            该分类的原始文本，或 None
        """
        try:
            client = self._get_client()
            f10 = client.F10(symbol=stock_code)
            if isinstance(f10, dict) and category in f10:
                return str(f10[category])
        except Exception as e:
            logger.debug("mootdx F10(%s)[%s] 失败: %s", stock_code, category, e)
        return None

    def get_industry(self, stock_code: str) -> str:
        """解析 F10「行业分析」获取行业分类字符串

        Returns:
            行业分类字符串，如 "银行--股份制银行Ⅱ--股份制银行Ⅲ"
            无法获取时返回 "未知行业"
        """
        text = self.get_f10_category(stock_code, '行业分析')
        if not text:
            return "未知行业"

        # 匹配「所属行业」段落后的行业分类行
        # 格式: 银行--股份制银行Ⅱ--股份制银行Ⅲ共(9)家
        match = re.search(r'【所属行业】\s*\n\s*([^\n]+)', text)
        if match:
            industry = match.group(1).strip()
            # 去掉尾部的 "共(N)家"
            industry = re.sub(r'共\(\d+\)家.*$', '', industry).strip()
            if industry:
                logger.debug("mootdx F10 行业: %s -> %s", stock_code, industry)
                return industry

        return "未知行业"

    def get_roe_history(self, stock_code: str) -> Dict[int, float]:
        """解析 F10「财务分析」获取历年加权净资产收益率

        F10 财务分析表格示例：
        ｜加权净资产收益率(%)   ｜   3.37｜   13.44｜   14.49｜   16.22｜   ...

        Returns:
            {2025: 13.44, 2024: 14.49, 2023: 16.22, ...}
            空字典表示无法获取
        """
        text = self.get_f10_category(stock_code, '财务分析')
        if not text:
            return {}

        # 找「加权净资产收益率」所在行
        roe_pattern = re.compile(r'加权净资产收益率[(\%)]*\s*[｜|]')
        match = roe_pattern.search(text)
        if not match:
            logger.debug("F10 财务分析中未找到加权净资产收益率行")
            return {}

        # 从匹配位置截取该行
        line_start = match.start()
        line_end = text.find('\n', line_start)
        line = text[line_start:line_end] if line_end > 0 else text[line_start:]

        # 从表头行找年份
        result = {}
        header_start = text.rfind('┌', 0, line_start)
        if header_start < 0:
            header_start = text.rfind('｜财务指标', 0, line_start)
        header_section = text[header_start:line_start]

        # 提取所有日期列（表头行）
        year_matches = re.findall(r'(\d{4})-(\d{2})-(\d{2})', header_section)

        # 提取 ROE 值（从该行的数字），过滤掉表头年份数字
        roe_values = [v for v in re.findall(r'([0-9]+\.?[0-9]*)', line)
                      if float(v) < 100]

        # 配对年份和值，仅保留年报（12-31 列）
        for i, ym in enumerate(year_matches):
            if ym[1] == '12' and ym[2] == '31' and i < len(roe_values):
                try:
                    result[int(ym[0])] = float(roe_values[i])
                except (ValueError, IndexError):
                    continue

        logger.debug("mootdx F10 ROE 历史 %s: %s", stock_code, result)
        return result

    def get_net_profit_annual(self, stock_code: str) -> Dict[int, float]:
        """解析 F10「财务分析」获取历年净利润（元）

        Returns:
            {2025: 150181000000.0, 2024: 148391000000.0, ...}
        """
        text = self.get_f10_category(stock_code, '财务分析')
        if not text:
            return {}

        # 找「净利润(元)」行的起始位置
        profit_start = text.find('净利润(元)')
        if profit_start < 0:
            return {}

        line_end = text.find('\n', profit_start)
        line = text[profit_start:line_end] if line_end > 0 else text[profit_start:]

        # 从表头获取年份
        header_start = text.rfind('┌', 0, profit_start)
        header_section = text[header_start:profit_start]
        year_matches = re.findall(r'(\d{4})-(\d{2})-(\d{2})', header_section)

        # 解析净利润值：支持 "1501.81亿" 和纯数字两种格式
        result = {}
        yi_matches = re.findall(r'([0-9]+\.?[0-9]*)亿', line)
        numeric_matches = re.findall(r'([0-9]+\.[0-9]+)', line)

        values = []
        if yi_matches:
            for v in yi_matches:
                values.append(float(v) * 1e8)
        elif numeric_matches:
            for v in numeric_matches:
                values.append(float(v))

        # 配对年份和值，仅保留年报（12-31 列）
        for i, ym in enumerate(year_matches):
            if ym[1] == '12' and ym[2] == '31' and i < len(values):
                try:
                    result[int(ym[0])] = values[i]
                except (ValueError, IndexError):
                    continue

        logger.debug("mootdx F10 净利润 %s: %s", stock_code, result)
        return result


# ── 工具函数 ────────────────────────────────────────────────────


