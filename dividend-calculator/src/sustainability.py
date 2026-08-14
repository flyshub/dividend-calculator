"""
股息可持续性 — 解析 + 编排层

数据取数（东财 datacenter / 腾讯 HTTP）已抽取到 eastmoney_fetcher.py（#43 L6）：
本模块保留解析纯函数（parse_financial_rows / parse_dividend_rows /
select_latest_annual / aggregate_dividend_history）与编排入口
（assess_for_stock / assess_with_auto_fetch），取数函数从 eastmoney_fetcher import。

设计理由：
  1. 东财 HTTP 全球可用，mootdx 通达信协议在部分环境受限；
  2. 与 JS 端同源，verify_js_vs_python.py 双端一致性校验天然对齐；
  3. 模块自洽：可持续性评估所需数据全部内聚取数，不耦合 pr.py/dividend.py 的 mootdx 路径。

字段名来自对 600900/600036 的实地验证，单位均为元、比率为百分数。严禁虚构数据。
"""
import dataclasses
import json
import logging
import re
from datetime import datetime
from typing import List, Optional, Tuple

from .datasource.base import DividendRecord
from .eastmoney_fetcher import (
    fetch_cashflow_rows,
    fetch_dividend_rows,
    fetch_financial_rows,
    fetch_industry,
    fetch_price_change_1y,
    fetch_top10_holding,
)
from .screener_cache import SustainabilitySnapshot
from .screener_rate_limit import batch_wait
from .sustainability_calculator import (
    AnnualFinancial,
    CUT_WINDOW_YEARS,
    DividendHistory,
    SustainabilityResult,
    assess_sustainability,
)

logger = logging.getLogger(__name__)

# 东财财务字段名 → AnnualFinancial 语义（实地验证 600036 招行/600887 伊利确认真实存在）
# 注意：东财字段命名有坑——NON_PERFORMING_LOAN 是"不良贷款余额"(元)非比率，
#       RISK_COVERAGE 恒为 None，DEBT_ASSET_RATIO/ADEQUACY_RATIO 不存在。
_FIELD_MAP = {
    "PARENTNETPROFIT": "net_profit",
    "PARENTNETPROFITTZ": "net_profit_yoy",
    "NETCASH_OPERATE_PK": "operating_cf",
    "NETCASH_INVEST_PK": "investing_cf",
    "TOTAL_ASSETS_PK": "total_assets",
    "LIABILITY": "total_liabilities",
    # debt_ratio 无直接字段，靠 AnnualFinancial.debt_ratio_decimal() 用 LIABILITY/TOTAL_ASSETS_PK 推算
    "INTEREST_DEBT_RATIO": "interest_debt_ratio",
    "INTEREST_COVERAGE_RATIO": "interest_coverage",
    "ROEJQ": "roe",
    "NEWCAPITALADER": "capital_adequacy_ratio",   # 总资本充足率（监管红线8%；非FIRST_ADEQUACY_RATIO一级口径）
    "NET_INTEREST_MARGIN": "net_interest_margin",
    "NONPERLOAN": "npl_ratio",                    # 不良贷款率（%；非NON_PERFORMING_LOAN余额）
    "LOAN_PROVISION_RATIO": "provision_coverage",  # 拨贷比（%；非RISK_COVERAGE恒空）
}


def _to_float(value) -> Optional[float]:
    """严格数值转换：空字符串/null/None 视为缺失（避免空串被解析为 0 污染）。"""
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s.lower() == "none":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 财务解析（纯函数，可被 verify 复用）
# ---------------------------------------------------------------------------

def parse_financial_rows(rows: List[dict]) -> List[AnnualFinancial]:
    """解析东财财务行 → AnnualFinancial 列表，仅保留年报（12-31）行，按报告期降序。

    只保留年报行的原因：东财 MAINFINADATA 把年报与各季报累计值混在一起，
    若不滤掉季报，select_latest_annual 会取到季报累计净利润，与分红总额
    （某完整财年）错配，导致支付率/覆盖率虚高失真。
    """
    result: List[AnnualFinancial] = []
    for row in rows:
        date_str = str(row.get("REPORT_DATE") or "")[:10]  # YYYY-MM-DD
        if len(date_str) < 10 or date_str[5:10] != "12-31":
            continue  # 仅保留年报行
        year = int(date_str[:4])
        kwargs = {"year": year}
        for src_field, dst_field in _FIELD_MAP.items():
            kwargs[dst_field] = _to_float(row.get(src_field))
        result.append(AnnualFinancial(**kwargs))
    return result


def select_latest_annual(financials: List[AnnualFinancial],
                         target_year: Optional[str] = None) -> Optional[AnnualFinancial]:
    """选出年报行，优先匹配分红所属财年。

    parse_financial_rows 已只保留 12-31 年报行（过滤掉季报累计值），
    避免与分红总额（某完整财年）错配导致支付率虚高。

    target_year: 分红所属财年字符串（如 '2025'）。优先返回该年年报；无匹配则返回最新年报。
    """
    if target_year:
        for fin in financials:  # 已降序
            if str(fin.year) == str(target_year):
                return fin
    for fin in financials:
        if fin.net_profit is not None or fin.operating_cf is not None:
            return fin
    return financials[0] if financials else None


# ---------------------------------------------------------------------------
# 分红明细解析 → DividendRecord（对齐 JS parseDividendRecords 的口径）
# ---------------------------------------------------------------------------

def _is_implemented(progress: str) -> bool:
    """东财分红方案进度是否已实施落地（排除预案/预披露/批准/未实施等）。

    实际值含"实施分配"等。判定：包含"实施"且不含"未实施"。
    """
    return "实施" in progress and "未实施" not in progress


def classify_fiscal_report(year: int, month: int) -> Tuple[bool, str]:
    """财年判定**单一实现**（#37 M4）：仅 12 月报告期是完整财年年报。

    其余月份（3/4 月 Q1、6/9 月半年报）均为中期分配，不构成完整财年；
    季度分红监管扩散下防御性收紧为 month == 12（与 JS calculator.js 同步）。
    本模块 parse_dividend_rows 与 dividend_records 各源 adapter 均调用此函数，
    不允许在其他位置重复实现 is_annual = (m == 12)。

    Returns:
        (is_annual, label)，label 为 "YYYY年报" / "YYYY中期分配"（NOT "半年报"）
    """
    is_annual = month == 12
    return is_annual, f"{year}年报" if is_annual else f"{year}中期分配"


def parse_dividend_rows(rows: List[dict]) -> Tuple[List[DividendRecord], Optional[str]]:
    """解析东财分红明细行 → DividendRecord 列表 + 最新有年报的财年字符串。

    纯函数，与 JS calculator.parseDividendRecords 同口径：
      - 仅保留已实施分红（_is_implemented，T5）
      - 仅保留 PRETAX_BONUS_RMB > 0 的现金分红
      - report_time 取报告期年份 + '年报'/'半年报'
    返回: (records, latest_year_str)
    """
    yearly: dict = {}  # {year: {'total': dp10合计, 'has_annual': bool}}
    records: List[DividendRecord] = []
    date_re = re.compile(r"(\d{4})-(\d{2})")

    for row in (rows or []):  # None（取数失败）按空处理，不抛（api.py 降级路径传入 None）
        progress = str(row.get("ASSIGN_PROGRESS") or "")
        # T5：仅保留已实施分红（含"实施"但排除"未实施"/"预案"/"预披露"/"批准"等未落地状态）
        if not _is_implemented(progress):
            continue
        dp10 = _to_float(row.get("PRETAX_BONUS_RMB"))
        if dp10 is None or dp10 <= 0:
            continue
        report_date = str(row.get("REPORT_DATE") or "")
        m = date_re.match(report_date)
        if not m:
            continue
        year = int(m.group(1))
        month = int(m.group(2))
        # 财年判定单一实现（classify_fiscal_report，见模块内定义与 dividend_records 复用）
        is_annual, label = classify_fiscal_report(year, month)

        ex_date = str(row.get("EX_DIVIDEND_DATE") or "")[:10]
        # 预案公告日：该财年股息「生效」的起点（走势图按此归因，非除权日）。
        # 年报预案次年 4 月公告（如 2019 年报 → 2020-04-30），中期分配预案当期 12 月。
        plan_date = str(row.get("PLAN_NOTICE_DATE") or "")[:10]
        records.append(DividendRecord(
            ex_dividend_date=ex_date,
            dividend_per_10=dp10,
            report_time=label,
            plan_notice_date=plan_date,
            total_shares=_to_float(row.get("TOTAL_SHARES")),
        ))

        if year not in yearly:
            yearly[year] = {"total": 0.0, "has_annual": False}
        yearly[year]["total"] += dp10
        yearly[year]["has_annual"] = yearly[year]["has_annual"] or is_annual

    # 最新有年报的财年（降序找第一个 has_annual）
    latest_year = None
    for y in sorted(yearly.keys(), reverse=True):
        if yearly[y]["has_annual"]:
            latest_year = str(y)
            break

    return records, latest_year


# ---------------------------------------------------------------------------
# 历史聚合（纯函数）
# ---------------------------------------------------------------------------

def aggregate_dividend_history(records: List[DividendRecord],
                               latest_year: Optional[str],
                               total_shares: float) -> DividendHistory:
    """聚合分红记录 → DividendHistory（连续年数 / 是否曾削减 / 均值）。

    连续年数：从最新年向前连续递减计数，遇中断即停。
    曾削减：历史任意年分红额 < 前一年 ×0.7 视为明显削减。
    """
    if not records:
        return DividendHistory(consecutive_years=0, ever_cut=False,
                               latest_year_amount=None, history_mean_amount=None)

    # 按财年聚合分红总额（元）——全部记录（含中期分配）参与总额/连续年数
    year_amount: dict = {}
    # 仅年报记录聚合——ever_cut 相邻年削减比较只用同类型（年报）口径（#39 M6），
    # 避免半年报 3 元 vs 上年年报 8 元被误判为削减
    annual_amount: dict = {}
    for rec in records:
        ym = re.match(r"(\d{4})", rec.report_time or "")
        if not ym:
            continue
        year = ym.group(1)
        # 行股本优先（东财 RPT_SHAREBONUS_DET 每行自带历史 TOTAL_SHARES，股本变动公司
        # 各年总额用各自行股本折算）；缺失（cninfo/mootdx 路径）回退参数股本
        shares = rec.total_shares if rec.total_shares else total_shares
        amount = rec.dividend_per_10 / 10.0 * shares
        year_amount[year] = year_amount.get(year, 0.0) + amount
        # 仅年报记录参与削减比较（#39 M6）。排除"半年报"子串：旧 label「半年报」
        # 含「年报」子串，遗留数据会被误判为年报——与 JS（indexOf('半年报') === -1）完全一致
        _t = rec.report_time or ""
        if "年报" in _t and "半年报" not in _t:
            annual_amount[year] = annual_amount.get(year, 0.0) + amount

    if not year_amount:
        return DividendHistory(consecutive_years=0, ever_cut=False,
                               latest_year_amount=None, history_mean_amount=None)

    years_sorted = sorted(year_amount.keys(), reverse=True)
    target_year = latest_year if (latest_year and latest_year in year_amount) else years_sorted[0]

    # 连续年数：从 target_year 向前逐年递减
    consecutive = 0
    try:
        y = int(target_year)
        while str(y) in year_amount:
            consecutive += 1
            y -= 1
    except ValueError:
        pass

    history_years = [yy for yy in years_sorted if yy != target_year]
    history_mean = None
    if history_years:
        history_mean = sum(year_amount[yy] for yy in history_years) / len(history_years)

    # 近3年均值（target_year 之前最近的3年）——突击分红判断用，避免早期低基数拉低全历史均值
    # 导致稳定增长股被误判为"突兀"（如伊利逐年提升分红，全历史均值偏低）
    try:
        tgt_int = int(target_year)
        recent3 = [yy for yy in years_sorted if yy != target_year and int(yy) < tgt_int][:3]
    except ValueError:
        recent3 = history_years[:3]
    history_3y_mean = None
    if recent3:
        history_3y_mean = sum(year_amount[yy] for yy in recent3) / len(recent3)

    ever_cut = False
    # 近 CUT_WINDOW_YEARS 年窗口（含最新财年）内相邻年分红降幅 > 30% 视为曾削减。
    # 窗口之外的久远波动（如行业早期调整）对当前分红可持续性无参考价值，
    # 避免连年提升分红的股票（如伊利 2016~2025 逐年递增）被早期低基数误判。
    # 只比较年报口径（annual_amount）：中期分配不参与削减比较（#39 M6）。
    window_start = int(target_year) - (CUT_WINDOW_YEARS - 1)
    asc = sorted(annual_amount.keys())
    for i in range(1, len(asc)):
        prev_y, cur_y = asc[i - 1], asc[i]
        if int(cur_y) < window_start:
            continue  # 仅检查窗口内相邻年
        prev, cur = annual_amount[prev_y], annual_amount[cur_y]
        if prev > 0 and cur < prev * 0.7:
            ever_cut = True
            break

    return DividendHistory(
        consecutive_years=consecutive,
        ever_cut=ever_cut,
        latest_year_amount=year_amount.get(target_year),
        history_mean_amount=history_mean,
        history_3y_mean=history_3y_mean,
    )


# ---------------------------------------------------------------------------
# 网络层（#43 L6：已抽取到 eastmoney_fetcher.py，本模块只保留解析与编排）
# ---------------------------------------------------------------------------

def merge_capex(financials: List[AnnualFinancial],
                cashflow_rows: List[dict]) -> List[AnnualFinancial]:
    """把现金流量表资本开支(CONSTRUCT_LONG_ASSET)按年合并，返回带 capex 的新列表（不改原对象）。

    东财现金流量表 CONSTRUCT_LONG_ASSET 为正数（购建固定资产/无形资产支付的现金）。
    仅取年报(12-31)行匹配年份；无匹配年份的行原样保留（capex 保持 None）。
    """
    # 按年聚合年报 CAPEX
    capex_by_year: dict = {}
    for row in cashflow_rows:
        date_str = str(row.get("REPORT_DATE") or "")[:10]
        if len(date_str) < 10 or date_str[5:10] != "12-31":
            continue
        year = int(date_str[:4])
        val = _to_float(row.get("CONSTRUCT_LONG_ASSET"))
        if val is None:
            continue
        capex_by_year[year] = capex_by_year.get(year, 0.0) + val
    # 不修改原对象：用 replace 生成带 capex 的新副本
    return [
        dataclasses.replace(fin, capex=capex_by_year[fin.year]) if fin.year in capex_by_year else fin
        for fin in financials
    ]


# ---------------------------------------------------------------------------
# 编排：assess_for_stock
# ---------------------------------------------------------------------------

def assess_for_stock(*,
                     stock_code: str,
                     total_shares: float,
                     dividend_total: Optional[float],
                     dividend_yield_before_tax: Optional[float],
                     latest_dividend_year: Optional[str],
                     industry: str,
                     dividend_records: List[DividendRecord],
                     financial_rows: Optional[List[dict]] = None,
                     cashflow_rows: Optional[List[dict]] = None,
                     price_change_1y: Optional[float] = None,
                     top10_holding: Optional[float] = None,
                     dividend_fetch_failed: bool = False) -> SustainabilityResult:
    """可持续性评估编排入口：取数据 → 喂纯评估器。

    dividend_records / industry / financial_rows / cashflow_rows 可外部注入（verify 复用），
    未注入时现场走东财 HTTP 取数。

    dividend_fetch_failed（#38 M5）：网络取数路径（assess_with_auto_fetch）下
    分红记录为空时无法区分"真无分红"与"取数失败"，由调用方置 True 后本函数
    强制 history=None（走评估器历史缺失分支）并追加显式失败 note，避免
    网络失败被静默当成"0 年连续分红"的负面结论。
    """
    if dividend_yield_before_tax is None or dividend_yield_before_tax <= 0:
        return SustainabilityResult(triggered=False, verdict="未评估", score=None,
                                    notes=["无股息率数据，未评估"])

    # 财务数据
    if financial_rows is None:
        financial_rows = fetch_financial_rows(stock_code)
    financials = parse_financial_rows(financial_rows)
    # 资本开支（现金流量表，修正 FCF 口径）——merge_capex 返回新列表，不改原对象
    if cashflow_rows is None:
        cashflow_rows = fetch_cashflow_rows(stock_code)
    financials = merge_capex(financials, cashflow_rows)
    latest = select_latest_annual(financials, latest_dividend_year)

    # 分红历史（#38 M5：取数失败 → history=None 走缺失分支，而非 0 年负面结论）
    if dividend_fetch_failed and not dividend_records:
        history = None
    else:
        history = aggregate_dividend_history(dividend_records, latest_dividend_year, total_shares)

    result = assess_sustainability(
        dividend_yield_before_tax=dividend_yield_before_tax,
        dividend_total=dividend_total,
        latest=latest,
        history=history,
        industry=industry,
        price_change_1y=price_change_1y,
        top10_holding=top10_holding,
        current_year=datetime.now().year,
    )
    if dividend_fetch_failed and not dividend_records:
        result.notes.append("分红历史数据获取失败，历史维度不参与评分")
    return result


def assess_with_auto_fetch(stock_code: str,
                          total_shares: float,
                          dividend_total: Optional[float],
                          dividend_yield_before_tax: Optional[float],
                          latest_dividend_year: Optional[str],
                          industry: Optional[str] = None,
                          dividend_rows: Optional[List[dict]] = None,
                          financial_rows: Optional[List[dict]] = None,
                          cashflow_rows: Optional[List[dict]] = None,
                          price_change_1y: Optional[float] = None,
                          top10_holding: Optional[float] = None,
                          dividend_fetch_failed: bool = False) -> SustainabilityResult:
    """全自取数版编排：财务/分红/行业/近1年涨跌/股东集中度全走 HTTP 取数。

    供 analysis.py 调用 —— 可持续性模块自洽，无需 pr.py 的 mootdx 行业、
    也无需 dividend.py 的 mootdx 分红记录。
    price_change_1y / top10_holding / cashflow_rows 可选注入（测试/预缓存用），
    默认 None 现场自取；网络失败返回 None 不阻塞评估（#40 B1）。
    dividend_fetch_failed（#95 透传）：调用方已知分红取数失败时可置 True 强制走
    失败分支（history=None + 失败 note）；默认 False 由本函数按
    `dividend_rows is None` 自动判定（#38 M5），行为与透传前完全一致。
    """
    if not industry or industry in ("未知行业", "无", ""):
        # 上游（pr.py 走 mootdx）行业不可用时，走东财重取，保证银行/周期判定准确
        industry = fetch_industry(stock_code)
    if dividend_rows is None:
        dividend_rows = fetch_dividend_rows(stock_code)
    # #38 M5：fetch_dividend_rows 网络失败返回 None（区别于真无分红的 []）
    if dividend_rows is None:
        records, em_latest_year = [], None
    else:
        records, em_latest_year = parse_dividend_rows(dividend_rows)
    # 分红财年：优先用外部传入（来自股息率口径），否则用东财分红明细推断
    div_year = latest_dividend_year or em_latest_year
    if price_change_1y is None:
        price_change_1y = fetch_price_change_1y(stock_code)
    if top10_holding is None:
        top10_holding = fetch_top10_holding(stock_code)

    return assess_for_stock(
        stock_code=stock_code,
        total_shares=total_shares,
        dividend_total=dividend_total,
        dividend_yield_before_tax=dividend_yield_before_tax,
        latest_dividend_year=div_year,
        industry=industry,
        dividend_records=records,
        dividend_fetch_failed=dividend_fetch_failed or dividend_rows is None,
        financial_rows=financial_rows,
        cashflow_rows=cashflow_rows,
        price_change_1y=price_change_1y,
        top10_holding=top10_holding,
    )


# ---------------------------------------------------------------------------
# 缓存序列化契约（#95）
#
# SustainabilitySnapshot 的行数据列（financial/cashflow/dividend_rows）以 JSON
# 字符串存 SQLite，但 JSON 的写/读（json.dumps/loads）只发生在本模块：
#   - 写入：_dict_to_snapshot（prefetch_and_cache 内部）
#   - 读取：_snapshot_to_dict（assess_from_cache 内部）
# 调用方（scripts/prefetch_sustainability.py、src/screener_sustainability.py）
# 不接触快照列名、不 import json。
# 新增评估输入字段时，只需改本模块：_dict_to_snapshot / _snapshot_to_dict
# 两个序列化函数 + assess_from_cache 的 assess_with_auto_fetch 调用，调用方零改动。
# ---------------------------------------------------------------------------

def _rows_to_json(rows) -> Optional[str]:
    """原始行数据 → JSON 字符串（None=取数失败，原样保留，不序列化）。"""
    return json.dumps(rows, ensure_ascii=False) if rows is not None else None


def _json_to_rows(s: Optional[str]):
    """快照 JSON 字符串 → 原始行数据（None/空串 → None）。"""
    return json.loads(s) if s else None


def _snapshot_to_dict(snap: SustainabilitySnapshot) -> dict:
    """SustainabilitySnapshot → 原始数据 dict（行数据反序列化为列表）。

    仅 sustainability.py 内部使用（#95 序列化契约：JSON 读写不跨模块暴露）。
    """
    return {
        "code": snap.code,
        "financial_rows": _json_to_rows(snap.financial_rows),
        "cashflow_rows": _json_to_rows(snap.cashflow_rows),
        "dividend_rows": _json_to_rows(snap.dividend_rows),
        "industry": snap.industry,
        "price_change_1y": snap.price_change_1y,
        "top10_holding": snap.top10_holding,
    }


def _dict_to_snapshot(code: str, data: dict, *, source: str) -> SustainabilitySnapshot:
    """原始数据 dict → SustainabilitySnapshot（行数据 JSON 序列化）。

    仅 sustainability.py 内部使用（#95 序列化契约：JSON 读写不跨模块暴露）。
    """
    return SustainabilitySnapshot(
        code=code,
        financial_rows=_rows_to_json(data.get("financial_rows")),
        cashflow_rows=_rows_to_json(data.get("cashflow_rows")),
        dividend_rows=_rows_to_json(data.get("dividend_rows")),
        industry=data.get("industry"),
        price_change_1y=data.get("price_change_1y"),
        top10_holding=data.get("top10_holding"),
        source=source,
    )


def prefetch_and_cache(cache, code: str) -> Optional[SustainabilitySnapshot]:
    """预取 6 类数据 + 限流 + 写缓存，一次调用完成。返回写入的快照。

    S2 完整性检查：financial/cashflow 同时为空数组视为拉取失败（正常公司必有
    财报），此时**不写缓存**（避免空数据投毒导致假阴性 verdict，数据铁律：
    不缓存失败数据），直接返回 None。
    限流：与 scripts/prefetch_sustainability.py 一致，内部先 batch_wait()。
    """
    batch_wait()  # 限流
    financial = fetch_financial_rows(code)
    cashflow = fetch_cashflow_rows(code)
    dividend = fetch_dividend_rows(code)
    industry = fetch_industry(code)
    price_change = fetch_price_change_1y(code)
    top10 = fetch_top10_holding(code)
    # S2：财务/现金流同时为空 → 拉取失败（正常公司必有财报），标记不缓存
    if (financial is not None and len(financial) == 0
            and cashflow is not None and len(cashflow) == 0):
        logger.warning("[%s] 财务/现金流同时为空，判为拉取失败，不缓存", code)
        return None
    snap = _dict_to_snapshot(code, {
        "financial_rows": financial,
        "cashflow_rows": cashflow,
        "dividend_rows": dividend,
        "industry": industry,
        "price_change_1y": price_change,
        "top10_holding": top10,
    }, source="东财预拉")
    cache.upsert_sustainability(snap)
    return snap


def assess_from_cache(cache, code: str,
                      total_shares: float,
                      dividend_total: Optional[float],
                      dividend_yield_before_tax: Optional[float],
                      latest_dividend_year: Optional[str],
                      industry: Optional[str],
                      dividend_fetch_failed: bool = False) -> SustainabilityResult:
    """读缓存快照 → 内部反序列化 → assess_with_auto_fetch。

    缓存命中（未过期）时注入预拉数据（零网络，行业优先用快照值）；
    未命中/过期时按需取数（与既有 _assess_and_cache 一致——限流由调用方在
    评估前统一处理，如 make_sustainability_evaluator 未命中时先 batch_wait()）。
    调用方不接触快照 JSON 列（序列化契约见本模块 #95 注释）。
    """
    snap = cache.get_sustainability(code)
    if snap is not None and not cache.is_sustainability_stale(code):
        data = _snapshot_to_dict(snap)
        return assess_with_auto_fetch(
            stock_code=code,
            total_shares=total_shares,
            dividend_total=dividend_total,
            dividend_yield_before_tax=dividend_yield_before_tax,
            latest_dividend_year=latest_dividend_year,
            industry=data["industry"] or industry,  # 快照行业优先
            financial_rows=data["financial_rows"],
            cashflow_rows=data["cashflow_rows"],
            dividend_rows=data["dividend_rows"],
            price_change_1y=data["price_change_1y"],
            top10_holding=data["top10_holding"],
            dividend_fetch_failed=dividend_fetch_failed,
        )
    # 缓存未命中/过期 → 按需补拉（限流行为与既有路径一致）
    return assess_with_auto_fetch(
        stock_code=code,
        total_shares=total_shares,
        dividend_total=dividend_total,
        dividend_yield_before_tax=dividend_yield_before_tax,
        latest_dividend_year=latest_dividend_year,
        industry=industry,
        dividend_fetch_failed=dividend_fetch_failed,
    )


__all__ = [
    "parse_financial_rows",
    "parse_dividend_rows",
    "select_latest_annual",
    "aggregate_dividend_history",
    "merge_capex",
    "assess_for_stock",
    "assess_with_auto_fetch",
    "prefetch_and_cache",
    "assess_from_cache",
]

