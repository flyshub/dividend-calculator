"""
数据源抽象层 - 定义 Protocol 接口

StockInfo 和 DividendDetail 是全局唯一定义，
所有模块（api.py、dividend.py、datasource 等）统一引用此处的定义。
"""
from typing import Protocol, Optional, List, Tuple
from dataclasses import dataclass, field


@dataclass
class StockInfo:
    """股票基本信息（全局唯一定义）"""
    stock_code: str
    current_price: float
    total_shares: float
    warnings: List[str] = field(default_factory=list)


@dataclass
class DividendDetail:
    """分红明细（全局唯一定义）"""
    report_time: str
    dividend_per_10: float


@dataclass
class MonthlyPrice:
    """月度价格数据"""
    date: str        # YYYY-MM-DD 月末日期
    close: float     # 前复权收盘价（走势图画图用）
    close_nominal: Optional[float] = None  # 不复权名义收盘价（走势图股息率总额法分母口径；缺 None）


@dataclass
class DividendRecord:
    """分红记录（含除权除息日，用于走势图）"""
    ex_dividend_date: str   # 除权除息日 YYYY-MM-DD
    dividend_per_10: float  # 每10股派息金额
    report_time: str        # 报告期
    plan_notice_date: str = ""  # 预案公告日 YYYY-MM-DD（该财年股息生效起点；mootdx 兜底无此字段）
    total_shares: Optional[float] = None  # 股权登记日总股本 = 该次除权前股本（东财 RPT_SHAREBONUS_DET 行自带；cninfo/mootdx 路径为 None）
    transfer_per_10: Optional[float] = None  # 送转比例（10送转X，东财 IT_RATIO；走势图股本锚点回退用）


@dataclass
class HistoricalData:
    """历史走势数据"""
    stock_code: str
    stock_name: Optional[str]
    monthly_prices: List[MonthlyPrice]
    dividend_records: List[DividendRecord]
    total_shares_now: Optional[float] = None  # 当前总股本（腾讯 Index 73；走势图最新月份股本锚点，与主卡口径自洽）


class StockInfoProvider(Protocol):
    """股票信息提供者协议"""

    @property
    def name(self) -> str:
        """数据源名称"""
        ...

    @property
    def priority(self) -> int:
        """优先级，数字越小优先级越高"""
        ...

    def get_stock_info(self, stock_input: str) -> Optional[StockInfo]:
        """
        获取股票基本信息

        Args:
            stock_input: 股票代码或名称

        Returns:
            StockInfo 对象或 None
        """
        ...


class DividendProvider(Protocol):
    """分红数据提供者协议"""

    @property
    def name(self) -> str:
        """数据源名称"""
        ...

    @property
    def priority(self) -> int:
        """优先级，数字越小优先级越高"""
        ...

    def get_latest_dividend(
        self,
        stock_code: str,
        stock_info: StockInfo
    ) -> Tuple[float, Optional[str], List[DividendDetail], str]:
        """
        获取最近一个完整财年的现金分红

        Args:
            stock_code: 股票代码
            stock_info: 股票基本信息

        Returns:
            (总分红金额, 年份, 分红明细, 说明)
        """
        ...


class DataSource(StockInfoProvider, DividendProvider, Protocol):
    """完整数据源协议"""
    pass
