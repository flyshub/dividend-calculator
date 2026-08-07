"""
数据源管理器 - 管理多个数据源，支持自动降级

数据源架构（mootdx + 腾讯双引擎）：
  mootdx（通达信协议）→ 行情/K线/除权除息/财务快照/F10资料
  腾讯行情（HTTP）    → PE_TTM/PB/总股本（已验证准确）
"""
import logging
import time
from typing import Optional, List, Tuple

from .base import StockInfo, DividendDetail, DataSource

logger = logging.getLogger(__name__)
from .mootdx_source import MootdxSource
from .sina_source import SinaSource
from .tencent_source import TencentSource

# 跨源交叉验证结果缓存（#44 L7）：key=stock_code → (monotonic 时间戳, 比对结果)。
# _cross_check 在 get_stock_info 主链路同步调用 mootdx quotes+finance，较慢；
# 60s TTL 内复用比对结论（price/shares diff 是否已产生 warning），避免重复网络调用。
_cross_check_cache: dict = {}
_CROSS_CHECK_TTL = 60.0


class DataSourceManager:
    """数据源管理器，按优先级尝试多个数据源"""

    # 跨源交叉验证阈值（审查 #2）：相对差超过即追加 warning
    PRICE_DIFF_THRESHOLD = 0.01    # 价格相对差 >1%
    SHARES_DIFF_THRESHOLD = 0.005  # 总股本相对差 >0.5%

    def __init__(self, sources=None):
        if sources is not None:
            self._sources = sorted(sources, key=lambda s: s.priority)
        else:
            self._sources = []
            self._register_default_sources()

    def _register_default_sources(self):
        """注册默认数据源（tencent 行情 + sina 备用 + mootdx 分红）"""
        self.register_source(TencentSource())  # priority 3（价格+股本）
        self.register_source(SinaSource())     # priority 4（新浪价格+腾讯股本备用）
        self.register_source(MootdxSource())   # priority 5（分红+K线）

    def register_source(self, source: DataSource):
        """注册新的数据源，按优先级插入"""
        inserted = False
        for i, existing in enumerate(self._sources):
            if source.priority < existing.priority:
                self._sources.insert(i, source)
                inserted = True
                break
        if not inserted:
            self._sources.append(source)

    def get_stock_info(self, stock_input: str) -> Optional[StockInfo]:
        """按优先级尝试获取股票信息"""
        last_error = None
        for source in self._sources:
            try:
                info = source.get_stock_info(stock_input)
                if info is not None:
                    # 跨源交叉验证（审查 #2）：主源为腾讯/新浪时用 mootdx best-effort 比对
                    self._cross_check(stock_input, source.name, info)
                    return info
            except Exception as e:
                last_error = e
                continue

        if last_error is not None:
            logger.warning("所有数据源获取 %s 失败，最后错误: %s", stock_input, last_error)
        return None

    def _cross_check(self, stock_code: str, primary_name: str, info: StockInfo) -> None:
        """跨源交叉验证（审查 #2）：单源错值拦截。

        主源为 tencent/sina 时，best-effort 用 mootdx 取 price + zongguben 比对，
        相对差超阈值追加 StockInfo.warnings。所有异常只跳过不抛（非阻塞）。
        结果按 stock_code 缓存 60s（#44 L7）：warnings 是**实例属性**，TTL 内命中时
        必须把缓存的比对结论重放到当次新建的 StockInfo 实例，不能只跳过——否则
        第二次调用响应会丢失跨源不一致告警（行为回归）。
        """
        if primary_name == "mootdx":
            return  # 主源已是 mootdx，无独立第二源
        now = time.monotonic()
        cached = _cross_check_cache.get(stock_code)
        if cached is not None and now - cached[0] < _CROSS_CHECK_TTL:
            info.warnings.extend(cached[1])  # 重放缓存的比对结论（幂等：同一代码同一 TTL 内只比对一次）
            return
        found: List[str] = []
        try:
            from .mootdx_source import get_quotes_client
            client = get_quotes_client()
            df = client.quotes(symbol=stock_code)
            if df is None or len(df) == 0:
                return
            m_price = float(df['price'].iloc[0])
            m_shares = None
            try:
                fin = client.finance(symbol=stock_code)
                if fin is not None and len(fin) > 0 and 'zongguben' in fin.columns:
                    m_shares = float(fin['zongguben'].iloc[0])
            except Exception:
                pass  # 股本比对失败静默跳过

            if m_price > 0 and info.current_price > 0:
                rel = abs(m_price - info.current_price) / info.current_price
                if rel > self.PRICE_DIFF_THRESHOLD:
                    found.append(
                        f"价格跨源不一致: {primary_name}={info.current_price:.2f}, "
                        f"mootdx={m_price:.2f}（相对差 {rel*100:.1f}%，可能为行情时差）"
                    )
            if m_shares is not None and m_shares > 0 and info.total_shares > 0:
                rel = abs(m_shares - info.total_shares) / info.total_shares
                if rel > self.SHARES_DIFF_THRESHOLD:
                    found.append(
                        f"总股本跨源不一致: {primary_name}={info.total_shares:.0f}, "
                        f"mootdx={m_shares:.0f}"
                    )
            _cross_check_cache[stock_code] = (now, found)  # #44 L7：仅成功比对后缓存
        except Exception as e:
            logger.debug("跨源交叉验证跳过 %s: %s", stock_code, e)
        info.warnings.extend(found)

    def get_latest_dividend(
        self,
        stock_code: str,
        stock_info: StockInfo
    ) -> Tuple[float, Optional[str], List[DividendDetail], str]:
        """按优先级尝试获取分红数据"""
        last_error = None
        for source in self._sources:
            try:
                total_div, year, details, expl = source.get_latest_dividend(
                    stock_code, stock_info
                )
                if total_div > 0:
                    return total_div, year, details, expl
            except Exception as e:
                last_error = e
                continue

        if last_error is not None:
            logger.warning("所有数据源获取 %s 分红失败，最后错误: %s", stock_code, last_error)
        return 0.0, None, [], "所有数据源都无法获取分红数据"

    def get_source_names(self) -> List[str]:
        """获取所有已注册的数据源名称"""
        return [source.name for source in self._sources]


# 全局单例
_data_source_manager: Optional[DataSourceManager] = None


def get_data_source_manager() -> DataSourceManager:
    """获取数据源管理器单例"""
    global _data_source_manager
    if _data_source_manager is None:
        _data_source_manager = DataSourceManager()
    return _data_source_manager
