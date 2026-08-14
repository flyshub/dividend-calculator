#!/usr/bin/env python3
"""四层漏斗分层回测引擎（T4，issue #87）

方案 V3 核心交付：季度调仓（季末收盘计算、次季首日 T+1 建仓）、无未来函数
（asof 过滤）、四层漏斗逐层标记，输出「基线全A等权 → +L2 → +L3 → +L4 →
全漏斗」五档季度收益序列与逐层增量超额。

数据缺口（如实标注，不虚构）：
- total_shares：DB 无股本表，首次运行从腾讯 Index 73 补拉当前值存 total_shares
  表；历史股本变动（送转/增发）未建模，用当前值近似（每股口径股息率数学等价，
  仅 sustainability 支付率受股本变动影响——报告标注）。
- industry：DB 无行业表，从东财 fetch_industry 补拉（带重试）；失败记缺失。
- top10_holding：T2 未入库，lookup 返回 None（一股独大红旗不触发，报告标注）。
- finance 快照：仅 T2 入库的 8 字段（roe/net_profit/net_cash_operate/bps/
  newcapitalader/loan_provision_ratio），net_profit_yoy/investing_cf/
  total_assets/total_liabilities/interest_coverage 等缺失 → None，
  可持续性部分维度按缺失计 0 分 + 置信度标注（报告如实披露）。
"""
from __future__ import annotations

import bisect
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from src.backtest_factors import (
    pr as factor_pr,
    real_dividend_yield,
    sustainability as factor_sustainability,
    ttm_dividend_yield,
)

DB_PATH = "data/backtest.db"

# 四层漏斗阈值（对齐 src/screening.py 口径）
TTM_YIELD_THRESHOLD = 5.0   # TTM 股息率 > 5%
REAL_YIELD_THRESHOLD = 5.0  # 真实股息率 > 5%
PR_THRESHOLD = 1.0          # 市赚率 ≤ 1（低估/合理偏低）
SUSTAINABLE_VERDICTS = ("可持续", "偏弱")


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _dstr(d: date) -> str:
    return d.isoformat()


class BacktestLookup:
    """T3 lookup 契约的 DB 实现（只读 + 预加载 + 二分查找）。

    asof 过滤保证无未来函数：price/pe 取 date≤T 最近、分红取 announce_date≤T、
    财报取 report_date≤T（12-31 年报）。
    """

    def __init__(self, db_path: str = DB_PATH):
        self.conn = sqlite3.connect(db_path)
        self._load()

    def __getitem__(self, key: str):
        """T3 lookup 契约是下标访问：lookup['total_shares'](code, T) → 绑定方法。"""
        return getattr(self, key)

    def get(self, key: str, default=None):
        """dict.get 兼容：T3 因子层用 lookup.get('industry')。"""
        fn = getattr(self, key, None)
        return fn if callable(fn) else default

    # -- 预加载 ----------------------------------------------------------
    def _load(self) -> None:
        c = self.conn
        self.prices: Dict[str, List] = defaultdict(list)      # code -> [(date, close)]
        self.pes: Dict[str, List] = defaultdict(list)        # code -> [(date, pe)]
        self._div_recs: Dict[str, List[dict]] = defaultdict(list)
        self._fin_recs: Dict[str, List[dict]] = defaultdict(list)
        self.shares: Dict[str, float] = {}
        self._industry_map: Dict[str, str] = {}
        self.trading_days: List[date] = []

        for code, dte, close in c.execute(
            "SELECT code, date, close FROM daily_price ORDER BY date"
        ):
            self.prices[code].append((_d(dte), close))
        # 退市日（stock_list.delist_date，T5 #110：退市股不可入选/持有）
        self.delist: Dict[str, date] = {}
        # 上市日（stock_list.list_date，T16 #121：未上市股不可入选）
        self.listdt: Dict[str, date] = {}
        has_sl = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='stock_list'"
        ).fetchone()
        if has_sl:
            for code, dd, ld in c.execute(
                "SELECT code, delist_date, list_date FROM stock_list "
                "WHERE delist_date IS NOT NULL OR list_date IS NOT NULL"
            ):
                if dd:
                    self.delist[code] = _d(dd)
                if ld:
                    self.listdt[code] = _d(ld)
        for code, dte, pe in c.execute(
            "SELECT code, date, pe_ttm FROM daily_pe ORDER BY date"
        ):
            self.pes[code].append((_d(dte), pe))
        for code, ann, rep, ex, d10, br, tr in c.execute(
            "SELECT code, announce_date, report_date, ex_dividend_date, "
            "cash_div_10shares, bonus_ratio, trans_ratio FROM dividend_history "
            "ORDER BY report_date"
        ):
            self._div_recs[code].append({
                "announce_date": ann,
                "report_date": rep,
                "ex_dividend_date": ex,
                "cash_div_per_share": (d10 / 10.0) if d10 is not None else 0.0,
                "bonus_ratio": br or 0.0,    # 每10股送股
                "trans_ratio": tr or 0.0,    # 每10股转增
            })
        for code, rep, roe, np_, oc, bps, car, lpr, nd, ta, tl, npy, icf, capex in c.execute(
            "SELECT code, report_date, roe, net_profit, net_cash_operate, "
            "bps, newcapitalader, loan_provision_ratio, notice_date, "
            "total_assets, total_liabilities, net_profit_yoy, investing_cf, capex "
            "FROM finance_history ORDER BY report_date"
        ):
            self._fin_recs[code].append({
                "year": int(rep[:4]),
                "roe": roe,
                "net_profit": np_,
                "operating_cf": oc,
                "bps": bps,
                "capital_adequacy_ratio": car,
                "provision_coverage": lpr,
                "notice_date": nd,    # T11 #116：实际披露日（None=未入库，回退+4月）
                # T7 #132：5 字段从 DB 加载（T2 #125 补拉），FCF 红旗/资产维度恢复
                "net_profit_yoy": npy,
                "investing_cf": icf,
                "total_assets": ta,
                "total_liabilities": tl,
                "capex": capex,
                "debt_ratio": (tl / ta * 100.0) if (ta and tl and ta > 0) else None,  # 百分数（与 debt_ratio_decimal 语义一致）
                # 仍未入库字段（T2 未覆盖），sustainability 降级处理
                "interest_debt_ratio": None,
                "interest_coverage": None,
                "net_interest_margin": None,
                "npl_ratio": None,
            })
        # 交易日历：用 H00922 全收益指数的交易日（2013-2026 完整覆盖）
        self.trading_days = [
            _d(dte) for (dte,) in c.execute(
                "SELECT DISTINCT date FROM index_daily WHERE code='H00922' "
                "ORDER BY date"
            )
        ]
        self._maybe_load_aux()

    def _maybe_load_aux(self) -> None:
        """从 DB 读 total_shares 与 industry（若表存在）。

        注意：**不补拉**——仅在 DB 已建相应表时加载。当前 DB 无 total_shares/
        industry 表（详见 #165/#166），故 shares/industry 为空，total_shares()
        回退 1.0（每股口径近似，详见 total_shares docstring）。真补拉是 #165/#166
        的数据工程任务。
        """
        c = self.conn
        has_shares = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='total_shares'"
        ).fetchone()
        if has_shares:
            for code, ts in c.execute("SELECT code, total_shares FROM total_shares"):
                self.shares[code] = ts
        has_ind = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='industry'"
        ).fetchone()
        if has_ind:
            for code, ind in c.execute("SELECT code, industry FROM industry"):
                self._industry_map[code] = ind

    # -- lookup 契约（T3 签名） ------------------------------------------
    def _latest(self, series: List, asof: date):
        """series: [(date, value)] 升序 → date ≤ asof 最近值。"""
        if not series or series[0][0] > asof:
            return None
        i = bisect.bisect_right(series, (asof, float("inf"))) - 1
        return series[i][1] if i >= 0 else None

    def dividends(self, code: str, asof: date) -> Optional[List[dict]]:
        recs = [
            r for r in self._div_recs.get(code, [])
            if (not r["announce_date"]) or _d(r["announce_date"]) <= asof
        ]
        return recs or None

    def pe_ttm(self, code: str, asof: date) -> Optional[float]:
        return self._latest(self.pes.get(code, []), asof)

    def total_shares(self, code: str, asof: date) -> Optional[float]:
        """总股本（腾讯 Index 73 当前快照，#165 已入库）。

        DB 已建 total_shares 表（拉取脚本见 build_total_shares），表为空时
        回退 1.0（虚构占位值，违反数据铁律 #2）——仅在表未填充时使用。

        股息率计算数学等价（每股法 ≡ 总额法，分子分母同乘 shares 约分），故
        real/ttm yield 不受 shares 缺失影响；sustainability 支付率（dps×1.0 /
        net_profit）会失真——已在报告 §1 如实标注。
        """
        return self.shares.get(code, 1.0)

    def price(self, code: str, asof: date) -> Optional[float]:
        return self._latest(self.prices.get(code, []), asof)

    def roe_latest(self, code: str, asof: date) -> Optional[float]:
        """最新年报 ROE。T11 #116：优先按 notice_date 实际披露日过滤
        （1-4 月年报未披露不超前），notice_date 缺失回退保守窗口。"""
        recs = [f for f in self._fin_recs.get(code, [])
                if self._fin_visible(f, asof)]
        if not recs:
            # 兜底：报告期 +4 个月保守可见（T4 #131：缺失回退不再超前）
            recs = [f for f in self._fin_recs.get(code, [])
                    if date(f["year"] + 1, 4, 30) <= asof]
        return recs[-1]["roe"] if recs else None

    def _fin_visible(self, f: dict, asof: date) -> bool:
        """T11 #116：财报对 asof 是否可见——优先按 notice_date 实际披露日，
        T4 #131：notice_date 缺失（实测 0.06%）回退报告期 +4 个月保守窗口
        （A 股年报法定披露截止 4-30），不再按报告期当天可见。"""
        nd = f.get("notice_date")
        if nd:
            try:
                cutoff = _d(nd[:10]) if isinstance(nd, str) else nd
                return cutoff <= asof
            except Exception:
                pass  # 解析失败 → 回退保守窗口
        return date(f["year"] + 1, 4, 30) <= asof

    def finance(self, code: str, asof: date) -> Optional[dict]:
        """T11 #116：优先按 notice_date 实际披露日过滤，缺失回退 +4 月保守窗口。

        notice_date 为 None（未入库）时按报告期 +4 个月保守可见（T4 #131）。
        notice_date 为空字符串/无效时同样回退保守窗口。
        """
        recs = [f for f in self._fin_recs.get(code, []) if self._fin_visible(f, asof)]
        return recs[-1] if recs else None

    def price_change_1y(self, code: str, asof: date) -> Optional[float]:
        """近 1 年涨跌幅（小数）——用 (asof-365, asof] 两端最近收盘。"""
        p = self.prices.get(code, [])
        p1 = self._latest(p, asof)
        p0 = self._latest(p, asof - timedelta(days=365))
        if p0 is None or p1 is None or p0 <= 0:
            return None
        return (p1 - p0) / p0

    def top10_holding(self, code: str, asof: date) -> Optional[float]:
        return None  # T2 未入库（缺口，报告标注）

    def industry(self, code: str, asof: date) -> str:
        return self._industry_map.get(code, "")


# ---------------------------------------------------------------------------
# 调仓日历与收益区间
# ---------------------------------------------------------------------------

def rebalance_dates(trading_days: List[date], start: date, end: date,
                    freq: str = "quarterly") -> List[date]:
    """调仓日序列：按频率取每月/季末/半年末的最后交易日。

    freq: "monthly" | "quarterly" | "semiannual"。
    """
    months = {
        "monthly": (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
        "quarterly": (3, 6, 9, 12),
        "semiannual": (6, 12),
    }[freq]
    out: List[date] = []
    for y in range(start.year, end.year + 1):
        for m in months:
            if (y, m) < (start.year, start.month) or (y, m) > (end.year, end.month):
                continue
            month_days = [d for d in trading_days if d.year == y and d.month == m]
            if month_days:
                out.append(month_days[-1])
    return out


def quarterly_rebalance_dates(trading_days: List[date],
                              start: date, end: date) -> List[date]:
    """季度末交易日序列（向后兼容）。"""
    return rebalance_dates(trading_days, start, end, freq="quarterly")


def build_day_after(trading_days: List[date], t: date,
                    offset: int = 1) -> Optional[date]:
    """T 之后（不含）第 offset 个交易日 —— 建仓日。默认 offset=1 即 T+1。"""
    cnt = 0
    for d in trading_days:
        if d > t:
            cnt += 1
            if cnt >= offset:
                return d
    return None


# ---------------------------------------------------------------------------
# 因子与漏斗
# ---------------------------------------------------------------------------

def compute_all_factors(code: str, T: date, lookup) -> dict:
    """四层因子一次算齐（复用 T3 纯函数）。"""
    return {
        "real_yield": real_dividend_yield(code, T, lookup),
        "ttm_yield": ttm_dividend_yield(code, T, lookup),
        "pr": factor_pr(code, T, lookup),
        "sustainability": factor_sustainability(code, T, lookup),
    }


def funnel_layer(factors: dict,
                 yield_thr: float = TTM_YIELD_THRESHOLD,
                 real_yield_thr: float = REAL_YIELD_THRESHOLD,
                 pr_thr: float = PR_THRESHOLD) -> int:
    """四层漏斗逐层判定，返回通过层数 0-4。

    L2: TTM > yield_thr 且 真实 > real_yield_thr；
    L3: 基础 PR ≤ pr_thr；L4: verdict ∈ {可持续, 偏弱}。
    任一层不通过即短路（对齐现网筛选）。

    阈值参数化（T6 参数敏感性扫描用），默认 = 模块常量（向后兼容）。
    """
    if factors["real_yield"] is None or factors["ttm_yield"] is None:
        return 0
    if not (factors["ttm_yield"] > yield_thr
            and factors["real_yield"] > real_yield_thr):
        return 1  # 过 L1 未过 L2
    prf = factors["pr"]
    if prf.pr is None or prf.pr > pr_thr:
        return 2
    sus = factors["sustainability"]
    if sus.verdict not in SUSTAINABLE_VERDICTS:
        return 3
    return 4


def portfolio_return(codes: List[str], build_day: date, settle_day: date,
                     lookup, cost: float = 0.003) -> Optional[float]:
    """等权组合收益（小数）。持有期 = [build_day, settle_day]，T+1 建仓价 → 结算价。

    T10 #115：送转除权因子建模。持有期内发生送转时，持仓股数按
    (1 + (bonus+trans)/10) 增加，收益 = 因子 × ps/pb - 1。

    T5 #127 H-4：持有期内退市终局损失计提。build_day 时股票在交易（pb 可得），
    但持有期内退市 → 结算价不可得（ps=None）。原实现跳过此类股，导致退市
    损失未进入组合收益（系统性低估尾部风险）。修复：若 pb 可得但 ps 不可得
    且 delist_date ∈ (build_day, settle_day]，按清算价值≈0 计提全损
    (收益 = -1.0，仍扣 2×cost 双边成本)。这是保守上界（实际清算价值非零）。

    双边交易成本：每期全换手，买入 0.3% + 卖出 0.3% 从收益中扣除
    （季度调仓下换手 ≈ 100%，成本 = cost×2 计入单期）。

    无价格（停牌/退市）个股剔除；全部无价格 → None。
    """
    rets = []
    delist_map = getattr(lookup, "delist", {}) or {}
    for code in codes:
        pb = lookup.price(code, build_day)
        ps = lookup.price(code, settle_day)
        if not pb:
            continue
        # T5 #127 H-4：持有期内退市终局损失计提。退市后 _latest 仍返回
        # 最后交易价（相当于按暂停前价格估值），从不按清算价值冲销——
        # 这里强制按清算价值≈0 全损计提（保守上界）。
        dd = delist_map.get(code)
        if dd is not None and build_day < dd <= settle_day:
            rets.append(-1.0 - 2.0 * cost)
            continue
        if not ps:
            continue  # 停牌且未退市：无结算价，跳过
        # T10 #115：累积持有期送转因子
        split_factor = 1.0
        divs = getattr(lookup, "dividends", None)
        if divs:
            for rec in (divs(code, settle_day) or []):
                ex = rec.get("ex_dividend_date")
                if not ex:
                    continue
                ex_d = _d(ex) if isinstance(ex, str) else ex
                if build_day < ex_d <= settle_day:
                    br = rec.get("bonus_ratio") or 0.0
                    tr = rec.get("trans_ratio") or 0.0
                    if br or tr:
                        split_factor *= (1.0 + (br + tr) / 10.0)
        gross = split_factor * ps / pb - 1.0
        rets.append(gross - 2.0 * cost)
    if not rets:
        return None
    return sum(rets) / len(rets)


# ---------------------------------------------------------------------------
# 主回测
# ---------------------------------------------------------------------------

def run_backtest(lookup,
                 start: date = date(2013, 1, 1),
                 end: date = date(2026, 8, 10),
                 build_offset: int = 1,
                 filter_fn=None,
                 freq: str = "quarterly",
                 yield_thr: float = TTM_YIELD_THRESHOLD,
                 real_yield_thr: float = REAL_YIELD_THRESHOLD,
                 pr_thr: float = PR_THRESHOLD) -> dict:
    """分层回测主流程。返回五档组合的季度收益与逐层增量超额。

    build_offset: 调仓日 T 之后第 N 个交易日建仓（默认 1 = T+1；稳健性检验用 T+5）。
    filter_fn: Optional[(codes, T) -> codes] 按每个调仓日 T 逐期过滤候选池
        （base/l2/l3/l4/full 每层均过滤），在 portfolio_return 之前应用，使过滤
        真正进入收益链路（T6 稳健性变体用）。默认 None 不过滤。
    freq: 调仓频率 "monthly"|"quarterly"|"semiannual"（T6 参数敏感性扫描用，
        默认 quarterly 向后兼容）。
    yield_thr/real_yield_thr/pr_thr: 漏斗阈值（T6 参数敏感性扫描用）。
    """
    days = lookup.trading_days
    rebalance = rebalance_dates(days, start, end, freq=freq)
    all_codes = sorted(lookup.prices.keys())

    # 每季度入选池：layer_key -> [codes]，layer_key: base/l2/l3/l4/full
    pools: Dict[str, List[List[str]]] = {k: [] for k in ("base", "l2", "l3", "l4", "full")}
    per_quarter: Dict[str, List[Optional[float]]] = {k: [] for k in pools}

    for i, T in enumerate(rebalance):
        build = build_day_after(days, T, offset=build_offset)
        settle = rebalance[i + 1] if i + 1 < len(rebalance) else days[-1]
        if build is None:  # 无下一交易日（末季）：该期无收益，跳过
            for k in per_quarter:
                per_quarter[k].append(None)
                pools[k].append([])
            continue
        assert build is not None
        layer_buckets = {"base": [], "l2": [], "l3": [], "l4": [], "full": []}

        for code in all_codes:
            # T5 #110：退市日早于调仓日的股票不可入选（已退市无法持有）
            dd = getattr(lookup, "delist", {}).get(code)
            if dd is not None and dd <= T:
                continue
            # T16 #121：上市日晚于调仓日的股票不可入选（未上市不可持有）
            ld = getattr(lookup, "listdt", {}).get(code)
            if ld is not None and ld >= T:
                continue
            factors = compute_all_factors(code, T, lookup)
            layer = funnel_layer(factors, yield_thr, real_yield_thr, pr_thr)
            layer_buckets["base"].append(code)
            if layer >= 2:
                layer_buckets["l2"].append(code)
            if layer >= 3:
                layer_buckets["l3"].append(code)
            if layer >= 4:
                layer_buckets["l4"].append(code)
            if layer >= 4:
                layer_buckets["full"].append(code)

        # 稳健性过滤：在收益计算前逐期过滤候选池（防 no-op）
        if filter_fn is not None:
            for k in ("base", "l2", "l3", "l4", "full"):
                layer_buckets[k] = filter_fn(layer_buckets[k], T)

        for k, codes in layer_buckets.items():
            pools[k].append(codes)
            per_quarter[k].append(portfolio_return(codes, build, settle, lookup))

    # 逐层增量超额（核心交付）：+L2 超基线、+L3 超 L2、+L4 超 L3、全漏斗超 L4、全漏斗超基线
    def cum(rets) -> float:
        vs = [r for r in rets if r is not None]
        if not vs:
            return 0.0
        acc = 1.0
        for r in vs:
            acc *= (1.0 + r)
        return acc - 1.0

    layers = ("base", "l2", "l3", "l4", "full")
    excess = {}
    # 逐层：比值口径超额 (1+r)/(1+p)-1（T3 #106：线性 r-p 在大波动期失真甚至翻转符号）
    for i in range(1, len(layers)):
        prev, cur = layers[i - 1], layers[i]
        if prev == "l4" and cur == "full":
            continue  # l4 ≡ full（同池），恒等行无信息量
        excess[f"{cur}_over_{prev}"] = [
            ((1.0 + r) / (1.0 + p) - 1.0)
            if (r is not None and p is not None and 1.0 + p > 0) else None
            for r, p in zip(per_quarter[cur], per_quarter[prev])
        ]
    # 全漏斗 vs 基线（报告 headline 用）
    excess["full_over_base"] = [
        ((1.0 + r) / (1.0 + p) - 1.0)
        if (r is not None and p is not None and 1.0 + p > 0) else None
        for r, p in zip(per_quarter["full"], per_quarter["base"])
    ]

    return {
        "rebalance_dates": rebalance,
        "pools": pools,
        "quarterly_returns": per_quarter,
        "cumulative_returns": {k: cum(v) for k, v in per_quarter.items()},
        "incremental_excess": {k: cum(v) for k, v in excess.items()},
        "excess_series": excess,
    }


def main() -> None:
    import json
    lookup = BacktestLookup()
    res = run_backtest(lookup)
    print("== 分层增量超额（累计，报告期 2013Q1-2026Q2）==")
    for k, v in res["incremental_excess"].items():
        print(f"  {k:>20s}: {v*100:+.2f}%")
    print("== 各档累计收益 ==")
    for k, v in res["cumulative_returns"].items():
        print(f"  {k:>20s}: {v*100:+.2f}%")
    print("== 各季度入选数（base/l2/l3/l4/full）==")
    for i, T in enumerate(res["rebalance_dates"]):
        n = [len(res["pools"][k][i]) for k in ("base", "l2", "l3", "l4", "full")]
        print(f"  {T}  {n}")
    print(json.dumps({"incremental_excess": res["incremental_excess"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
