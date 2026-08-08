#!/usr/bin/env python3
"""PR 因子历史回测（离线研究脚本，不进静态站，见 #25）。

目的：验证「市赚率 PR<0.8 买入能赚钱」是否有 alpha。
结论驱动后续：回测证 PR 有 alpha → 做行业中性/组合；证伪 → 转向 FCF yield/魔法公式。

口径（与项目 pr.py 一致）：
- PR = PE_TTM / ROE_latest（ROE 取最新已披露年报，非 5Y 中位数）
- 调仓时点：每年 6 月最后交易日（A 股年报披露截止次年 4 月 30 日，此时上年年报数据完备）
- 持有期：1 年
- 分组：按 PR 升序分 5 组（Q1 最低 PR=最便宜，Q5 最高 PR=最贵），组内等权
- 基准：沪深 300 同期收益（用成分股等权近似，避免指数接口口径差异）

数据源（实测可用）：
- 历史成分：unliftedq/index-constitution 仓库 history/csi300.csv（opt-in/opt-out 字段，
  可重建任一历史时点成分，消除幸存者偏差；数据来自中证官网公告人工整理）
- 历史 PE(TTM)：akshare stock_zh_valuation_baidu(symbol, "市盈率(TTM)", "全部")
- 年报 ROE：akshare stock_financial_analysis_indicator(symbol, start_year)，取 12-31 报告期行
- 价格：腾讯 fqkline 周线（qfqweek，东财 push2his 海外/受限环境易断连）

口径要点（2026-08-08 V2 版，issue #53）：
- 成分股按「当年 6/30 时点」取：每年回测只用当年真实成分（Q1..Q5 分组、基准都基于当年成分）
- 收益用腾讯 qfqweek（前复权）：已含分红除权调整，近似总收益（非纯价格收益）
- 亏损股（PE<0）、ROE<=0 排除在 PR 体系外（与项目一致：PR 只对盈利公司有意义）
- PE 极端值（PE>300）剔除（PE_MAX=300，避免微利股失真污染分组）
- 收益毛刺（|ret_1y|>5，如复权错位 +1000% 级异常）剔除，避免污染夏普/均值
- P1 增强（T6/T7）：bucket_absolute_pr 绝对 PR 区间、industry_neutralize 行业中性化

用法:
    python scripts/backtest_pr.py            # 默认沪深300，数据存 data/backtest.db
    python scripts/backtest_pr.py --clear    # 清库重新拉取
"""
import argparse
import json
import sqlite3
import sys
import time
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "backtest.db"
CONSTITUENTS_CSV = PROJECT_ROOT / "data" / "csi300.csv"  # 历史成分源，首次自动下载
CONSTITUENTS_URL = "https://raw.githubusercontent.com/unliftedq/index-constitution/master/history/csi300.csv"
REBALANCE_MONTH = 6  # 每年 6 月调仓（取该月 30 日前最近已收盘交易日）
ROE_START_YEAR = 2012  # 保证早期调仓点（2016）有上年年报 ROE
BACKTEST_START, BACKTEST_END = 2016, 2024  # 调仓年份区间（2025 仅作最后一期 t_next）
PE_MIN, ROE_MIN = 0.0, 0.0  # 排除亏损股/负 ROE
PE_MAX = 300.0  # 排除 PE 极端值（亏损边缘股/微利股 >300 视为失真），见 #56

UA_SLEEP = 0.8  # 单股请求间隔，避免东财限流（实测 0.2s 过密会 RemoteDisconnected）


def _get_ok(data, key):
    """兼容 dict/list 取值的容错读。"""
    return data.get(key) if isinstance(data, dict) else None


def log(msg: str):
    """实时输出（nohup 重定向下 print 是块缓冲，必须 flush 才能看到进度）。"""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _fetch_timeout(fn, seconds: int = 90):
    """akshare 调用加超时保护（防卡死）：请求 hang 时 90s 后抛异常，由调用方捕获。"""
    import signal

    def _handler(signum, frame):
        raise TimeoutError(f"akshare 调用超时（>{seconds}s）")

    old = signal.signal(signal.SIGALRM, _handler)
    try:
        signal.alarm(seconds)
        return fn()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _http_session():
    import requests.adapters
    from urllib3.util.retry import Retry

    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    s.mount(
        "https://",
        requests.adapters.HTTPAdapter(
            max_retries=Retry(total=3, connect=3, read=3, backoff_factor=1.0,
                              status_forcelist=[429, 500, 502, 503, 504]),
        ),
    )
    return s


_SESSION = _http_session()


def _get(url: str, params: dict | None = None):
    return _SESSION.get(url, params=params, timeout=(5, 30))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS constituents (
    year INTEGER NOT NULL, code TEXT NOT NULL,
    PRIMARY KEY (year, code));
CREATE TABLE IF NOT EXISTS pe (
    code TEXT NOT NULL, date TEXT NOT NULL, pe REAL,
    PRIMARY KEY (code, date));
CREATE TABLE IF NOT EXISTS roe (
    code TEXT NOT NULL, report_date TEXT NOT NULL, roe REAL,
    PRIMARY KEY (code, report_date));
CREATE TABLE IF NOT EXISTS px (
    code TEXT NOT NULL, date TEXT NOT NULL, close REAL,
    PRIMARY KEY (code, date));
"""


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def _db_load(table: str, code: str) -> pd.DataFrame | None:
    """从 SQLite 读单股数据（date 列转回 datetime）。"""
    with _db() as conn:
        df = pd.read_sql_query(f"SELECT * FROM {table} WHERE code = ?", conn, params=(code,))
    if df.empty:
        return None
    # 主键 date/report_date 存为 'YYYY-MM-DD'，还原为 datetime
    date_col = "report_date" if table == "roe" else "date"
    df[date_col] = pd.to_datetime(df[date_col])
    return df.drop(columns=["code"])


def _db_save(table: str, code: str, df: pd.DataFrame, date_col: str, val_col: str):
    """写入单股数据（覆盖该股旧数据）。"""
    if df.empty:
        return
    rows = [(code, str(r[date_col].date()), float(r[val_col])) for _, r in df.iterrows()]
    with _db() as conn:
        conn.execute(f"DELETE FROM {table} WHERE code = ?", (code,))
        conn.executemany(
            f"INSERT OR REPLACE INTO {table} (code, {date_col}, {val_col}) VALUES (?, ?, ?)", rows
        )


# ---------------------------------------------------------------------------
# 数据获取（akshare，带本地缓存）
# ---------------------------------------------------------------------------

def fetch_constituents(year: int) -> list:
    """某年 6/30 时点的沪深300成分（消除幸存者偏差：每年用当年真实成分）。

    数据源：unliftedq/index-constitution 的 csi300.csv（opt-in/opt-out 字段），
    首次运行自动下载到 data/csi300.csv 并入库。
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT code FROM constituents WHERE year = ? ORDER BY code", (year,)
        ).fetchall()
    if rows:
        return [r[0] for r in rows]

    if not CONSTITUENTS_CSV.exists():
        log("  下载历史成分源（csi300.csv）...")
        resp = _get(CONSTITUENTS_URL)
        resp.raise_for_status()
        CONSTITUENTS_CSV.write_bytes(resp.content)
    hist = pd.read_csv(CONSTITUENTS_CSV)
    hist["opt-in"] = pd.to_datetime(hist["opt-in"])
    hist["opt-out"] = pd.to_datetime(hist["opt-out"])

    # 重建 2016-2024 每年 6/30 快照并入库
    with _db() as conn:
        for y in range(BACKTEST_START, BACKTEST_END + 1):
            d = pd.Timestamp(f"{y}-06-30")
            mem = hist[(hist["opt-in"] <= d) & (hist["opt-out"].isna() | (hist["opt-out"] > d))]
            codes = sorted(mem["symbol"].str[-6:].tolist())
            conn.execute("DELETE FROM constituents WHERE year = ?", (y,))
            conn.executemany(
                "INSERT OR REPLACE INTO constituents (year, code) VALUES (?, ?)",
                [(y, c) for c in codes],
            )
    return fetch_constituents(year)


def fetch_pe_history(code: str) -> pd.DataFrame:
    """历史 PE(TTM) 序列：date, pe（百度股市通估值）。"""
    cached = _db_load("pe", code)
    if cached is not None:
        return cached
    import akshare as ak

    try:
        df = _fetch_timeout(
            lambda: ak.stock_zh_valuation_baidu(symbol=code, indicator="市盈率(TTM)", period="全部")
        )
        df = df.rename(columns={"date": "date", "value": "pe"})
        df["date"] = pd.to_datetime(df["date"])
        df = df.dropna(subset=["pe"]).sort_values("date")
        _db_save("pe", code, df, "date", "pe")
    except Exception as e:  # 数据源失败 → 空表，绝不造数
        log(f"  ! {code} PE 获取失败: {type(e).__name__} {str(e)[:60]}")
        return pd.DataFrame(columns=["date", "pe"])
    time.sleep(UA_SLEEP)
    return df


def fetch_roe_annual(code: str) -> pd.DataFrame:
    """年报 ROE 序列：report_date, roe（只留 12-31 报告期行）。"""
    cached = _db_load("roe", code)
    if cached is not None:
        return cached
    import akshare as ak

    try:
        df = _fetch_timeout(
            lambda: ak.stock_financial_analysis_indicator(symbol=code, start_year=str(ROE_START_YEAR))
        )
        df = df.rename(columns={"日期": "report_date", "净资产收益率(%)": "roe"})
        df["report_date"] = pd.to_datetime(df["report_date"])
        df = df[df["report_date"].dt.month == 12]  # 仅年报行
        df = df.dropna(subset=["roe"]).sort_values("report_date")
        _db_save("roe", code, df, "report_date", "roe")
    except Exception as e:
        log(f"  ! {code} ROE 获取失败: {type(e).__name__} {str(e)[:60]}")
        return pd.DataFrame(columns=["report_date", "roe"])
    time.sleep(UA_SLEEP)
    return df


_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"  # 全球可用，首选


def fetch_price_daily(code: str) -> pd.DataFrame:
    """前复权价格序列：date, close（腾讯 fqkline 周线）。

    腾讯日线 count 上限 ~640（仅约 2.5 年），周线 640 根覆盖 2014-2026 共 12 年，
    回测只需每年 6 月末调仓点价格，周线精度足够。东财 push2his 海外/受限环境易断连，不用。
    """
    cached = _db_load("px", code)
    if cached is not None:
        return cached
    try:
        prefix = "sh" if code.startswith("6") else (
            "bj" if code.startswith(("8", "4", "92")) else "sz")
        url = f"{_TENCENT_KLINE_URL}?param={prefix}{code},week,,,1000,qfq"
        resp = _SESSION.get(url, timeout=(5, 30))
        resp.raise_for_status()
        data = resp.json()
        key = f"{prefix}{code}"
        node = (data.get("data") or {}).get(key) or {}
        rows = node.get("qfqweek") or node.get("week") or []
        # 腾讯行格式：[date, open, close, high, low, ...]，索引 2 为收盘价
        df = pd.DataFrame([(r[0], float(r[2])) for r in rows], columns=["date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.dropna(subset=["close"]).sort_values("date")
        _db_save("px", code, df, "date", "close")
    except Exception as e:
        log(f"  ! {code} 价格获取失败: {type(e).__name__} {str(e)[:60]}")
        return pd.DataFrame(columns=["date", "close"])
    time.sleep(UA_SLEEP)
    return df


# ---------------------------------------------------------------------------
# 回测
# ---------------------------------------------------------------------------

def rebalance_dates(px: pd.DataFrame, target_dates: list) -> list:
    """给定目标调仓日（如每年 6/30），返回每个目标日前最近一个已收盘交易日。

    修复口径（#55）：原实现用 `date.month == REBALANCE_MONTH` 过滤，但腾讯周线把
    6/30 当周 bar 归到 7 月（7/01~7/07），导致实际选中 6/24~6/30、与「6 月最后
    交易日」不符。现改为：对每个 target_date，取 px 中 date <= target_date 的
    最后一条——语义精确、周线/日线通用。
    """
    dates = []
    for target in target_dates:
        t = pd.Timestamp(target)
        before = px[px["date"] <= t]
        if not before.empty:
            dates.append(before["date"].iloc[-1])
    return dates


def rebalance_targets(start_year: int, end_year: int, day: int = 30) -> list:
    """生成每年目标调仓日（默认每年 6/30）。"""
    return [f"{y}-{REBALANCE_MONTH:02d}-{day:02d}" for y in range(start_year, end_year + 1)]


def filter_pe_outliers(points: list, pe_max: float) -> list:
    """剔除 PE 极端值（> pe_max）的调仓点，避免单只失真污染分组（#56）。"""
    return [p for p in points if p["pe"] is not None and p["pe"] <= pe_max]


def analyze_panel(points: list) -> dict:
    """纯价格收益分组回测统计：过滤 → 分组 → 各组年化/超额/赢率/最差/夏普 → rho。

    返回 dict：{bench_annual, groups, spearman_rho, valid_points, q1_by_year}。
    逻辑与原 main 完全一致（回归锚点：#54 要求与报告已知结果一致）。
    """
    panel = pd.DataFrame(points)
    if panel.empty:
        return {"bench_annual": float("nan"), "groups": [], "spearman_rho": float("nan"),
                "valid_points": 0, "q1_by_year": {}}
    panel = panel[(panel["pe"] > PE_MIN) & (panel["roe"] > ROE_MIN)]
    panel["pr"] = panel["pe"] / panel["roe"]

    # 分组：每年按 PR 升序分 5 组（点数不足时降级为单组，避免 qcut 崩溃）
    def _assign_group(s):
        n = s.notna().sum()
        if n < 5:
            return pd.Series(["Q1"] * n, index=s.index)
        return pd.qcut(s.rank(method="first"), 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"])
    panel["group"] = panel.groupby("year")["pr"].transform(_assign_group)

    # 基准：同期全体成分等权收益
    bench = panel.groupby("year")["ret_1y"].mean().mean()

    group_stats = []
    for g in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        sub = panel[panel["group"] == g]
        rets = sub["ret_1y"]
        pr_min, pr_max = sub["pr"].min(), sub["pr"].max()
        ann = (1 + rets.mean()) ** 1 - 1  # 1年持有，均值即年化（几何近似）
        excess = ann - bench
        win = (rets > 0).mean()
        worst = rets.min()
        sharpe = rets.mean() / rets.std() if rets.std() > 0 else float("nan")
        group_stats.append({"group": g, "pr_min": float(pr_min), "pr_max": float(pr_max),
                            "ann": ann, "excess": excess, "win": win, "worst": worst, "sharpe": sharpe})

    # 单调性：组序 vs 年化收益 Spearman
    means = pd.Series({gs["group"]: gs["ann"] for gs in group_stats})
    order = [1, 2, 3, 4, 5]
    rank_means = [means[f"Q{i}"] for i in order]
    rho = _spearman(order, rank_means)

    # Q1 逐年明细
    q1 = panel[panel["group"] == "Q1"].groupby("year")["ret_1y"].mean()
    q1_by_year = {str(y): float(r) for y, r in q1.items()}

    return {
        "bench_annual": float(bench),
        "groups": group_stats,
        "spearman_rho": float(rho),
        "valid_points": len(panel),
        "q1_by_year": q1_by_year,
    }


# ---------------------------------------------------------------------------
# P1 增强（issue #53 T6/T7）：绝对 PR 区间 + 行业中性化
# ---------------------------------------------------------------------------

PR_BUCKETS = [
    ("低估(≤0.5)", 0.0, 0.5),
    ("合理偏低(0.5-1)", 0.5, 1.0),
    ("合理(1-3)", 1.0, 3.0),
    ("高估(>3)", 3.0, float("inf")),
]


def bucket_absolute_pr(panel: pd.DataFrame) -> dict:
    """按绝对 PR 阈值分桶（对应 classify_valuation 估值四档），输出各组统计。

    面板需含 year/pr/ret_1y 列。返回 {bench_annual, buckets, n}。
    """
    bench = panel.groupby("year")["ret_1y"].mean().mean()
    buckets = []
    for name, lo, hi in PR_BUCKETS:
        sub = panel[(panel["pr"] > lo) & (panel["pr"] <= hi)]
        if sub.empty:
            buckets.append({"bucket": name, "n": 0})
            continue
        rets = sub["ret_1y"]
        ann = rets.mean()
        excess = ann - bench
        sharpe = rets.mean() / rets.std() if rets.std() > 0 else float("nan")
        win = (rets > 0).mean()
        buckets.append({"bucket": name, "n": len(sub), "ann": float(ann),
                        "excess": float(excess), "sharpe": float(sharpe), "win": float(win)})
    return {"bench_annual": float(bench), "buckets": buckets, "n": len(panel)}


def industry_neutralize(panel: pd.DataFrame) -> dict:
    """行业中性化分组超额（T7）：行业内调整收益后重算 Q1-Q5 超额。

    面板需含 year/group/ret_1y/ind1（一级行业）列。方法：ret_1y 减去
    当年×行业的均值（行业调整收益），再算各 PR 分组超额，检验 PR 因子
    是否独立于行业。返回 {raw_excess, neutral_excess, bench, n_industries}。
    """
    if "ind1" not in panel.columns or panel["ind1"].isna().all():
        return {"error": "缺少 ind1（一级行业）列"}
    bench = panel.groupby("year")["ret_1y"].mean().mean()
    # 行业调整收益：ret - 当年行业均值
    adj = panel.copy()
    adj["ret_ind"] = adj.groupby(["year", "ind1"])["ret_1y"].transform(lambda x: x - x.mean())
    bench_ind = adj.groupby("year")["ret_ind"].mean().mean()

    raw_excess, neutral_excess = {}, {}
    for g in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        sub = panel[panel["group"] == g]
        sub_adj = adj[adj["group"] == g]
        raw_excess[g] = sub["ret_1y"].mean() - bench
        neutral_excess[g] = sub_adj["ret_ind"].mean() - bench_ind

    return {
        "bench_annual": float(bench),
        "bench_annual_neutral": float(bench_ind),
        "raw_excess": {k: float(v) for k, v in raw_excess.items()},
        "neutral_excess": {k: float(v) for k, v in neutral_excess.items()},
        "n_industries": int(panel["ind1"].nunique()),
    }


def build_points(code: str):
    """单股全部调仓时点的 (调仓日, 次年调仓日, PE, ROE, 未来1年收益)。"""
    pe = fetch_pe_history(code)
    roe = fetch_roe_annual(code)
    px = fetch_price_daily(code)
    if pe.empty or roe.empty or len(px) < 60:
        return []

    rb = rebalance_dates(px, rebalance_targets(2016, 2025))
    points = []
    for i, t in enumerate(rb):
        if i + 1 >= len(rb):
            break  # 最后一期无未来收益
        t_next = rb[i + 1]

        # PE：调仓日 T 之前最近值
        pe_t = pe[pe["date"] <= t]
        if pe_t.empty:
            continue
        pe_val = float(pe_t["pe"].iloc[-1])

        # ROE：报告期(12-31) <= T 的最近年报（6/30 时上年年报已披露）
        roe_t = roe[roe["report_date"] <= t]
        if roe_t.empty:
            continue
        roe_val = float(roe_t["roe"].iloc[-1])

        # 价格
        px_t = px[px["date"] <= t]
        px_next = px[px["date"] <= t_next]
        if px_t.empty or px_next.empty:
            continue
        p0 = float(px_t["close"].iloc[-1])
        p1 = float(px_next["close"].iloc[-1])
        if p0 <= 0 or p1 <= 0:
            continue

        ret_1y = p1 / p0 - 1
        # 收益合理性过滤：|ret| > 5 视为数据毛刺（腾讯周线异常值，如复权错位），
        # 剔除避免污染夏普/均值（审查 #25 发现 Q2/Q3 std≈4-5.5 必有 +1000% 级异常）
        if abs(ret_1y) > 5:
            continue

        points.append({
            "code": code,
            "date": t,
            "year": t.year,
            "pe": pe_val,
            "roe": roe_val,
            "ret_1y": ret_1y,
        })
    # PE 极端值过滤（#56）：剔除 PE>PE_MAX 的调仓点
    return filter_pe_outliers(points, PE_MAX)


def main():
    parser = argparse.ArgumentParser(description="PR 因子历史回测（#25）")
    parser.add_argument("--clear", action="store_true", help="清空缓存重新拉取")
    parser.add_argument("--limit", type=int, default=0, help="调试用：只取前 N 只成分股")
    args = parser.parse_args()

    if args.clear and DB_PATH.exists():
        DB_PATH.unlink()
        log("已清空数据库（下次运行重新拉取）")

    # 每年 6/30 的真实成分（消除幸存者偏差）
    membership = {}  # year -> set(code)
    for y in range(BACKTEST_START, BACKTEST_END + 1):
        membership[y] = set(fetch_constituents(y))
    all_codes = sorted(set().union(*membership.values()))
    log(f"历史成分加载：{BACKTEST_START}-{BACKTEST_END} 共 {len(all_codes)} 只（各年成分见下）")
    for y, s in sorted(membership.items()):
        log(f"  {y}: {len(s)} 只")

    if args.limit:
        all_codes = all_codes[: args.limit]

    # 逐股拉取 + 计算调仓点
    all_points = []
    for idx, code in enumerate(all_codes, 1):
        n_pts = 0
        for pt in build_points(code):
            # 仅保留「该股在当年属于成分」的调仓点
            if pt["year"] in membership and code in membership[pt["year"]]:
                all_points.append(pt)
                n_pts += 1
        if idx % 20 == 0 or idx == len(all_codes):
            log(f"  [{idx}/{len(all_codes)}] 累计调仓点 {len(all_points)}")
        else:
            log(f"  [{idx}/{len(all_codes)}] {code} +{n_pts}")

    if not all_points:
        log("✘ 无有效数据，终止")
        return 1

    stats = analyze_panel(all_points)
    bench = stats["bench_annual"]
    group_stats = stats["groups"]
    rho = stats["spearman_rho"]

    log(f"\n有效调仓点: {stats['valid_points']}（PE 极端值已按 >{PE_MAX:.0f} 过滤）")
    log("\n===== PR 分组回测结果（1 年持有，组内等权） =====")
    log(f"{'组':<4}{'PR区间':<16}{'年化收益':>9}{'超额(vs基准)':>12}{'赢率':>7}{'最差年份':>9}{'夏普':>7}")
    for gs in group_stats:
        log(f"{gs['group']:<4}{gs['pr_min']:>7.2f}-{gs['pr_max']:<7.2f}{gs['ann']*100:>8.2f}%{gs['excess']*100:>11.2f}%{gs['win']*100:>6.0f}%{gs['worst']*100:>8.2f}%{gs['sharpe']:>7.2f}")

    log(f"\n基准（沪深300成分等权年均）: {bench*100:.2f}%")
    log(f"\n单调性检验（Spearman, 组序 vs 年化收益）: rho={rho:.3f}")
    if rho < -0.5:
        log("→ PR 越低收益越高，支持「低估组有 alpha」假说")
    elif rho > 0.5:
        log("→ 收益与 PR 正相关，PR 方向性存疑（高 PR 组反而更赚）")
    else:
        log("→ 无明显单调性，PR 分组区分度弱")

    # Q1 逐年明细（最便宜组是否稳定跑赢）
    log("\nQ1（最低 PR）逐年收益：")
    for y, r in sorted(stats["q1_by_year"].items()):
        log(f"  {y}: {r*100:+.2f}%  {'↑正收益' if r > 0 else '↓负收益'}")

    # 数据库统计
    with _db() as conn:
        n_pe = conn.execute("SELECT COUNT(DISTINCT code) FROM pe").fetchone()[0]
        n_roe = conn.execute("SELECT COUNT(DISTINCT code) FROM roe").fetchone()[0]
        n_px = conn.execute("SELECT COUNT(DISTINCT code) FROM px").fetchone()[0]
    log(f"\n数据库 {DB_PATH}：PE {n_pe} 只 / ROE {n_roe} 只 / 日线 {n_px} 只（--clear 重拉）")

    # 汇总输出供 issue 结论
    summary = {
        "bench_annual": bench,
        "groups": group_stats,
        "spearman_rho": rho,
        "valid_points": stats["valid_points"],
        "q1_by_year": stats["q1_by_year"],
    }
    (DB_PATH.parent / "result_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _spearman(x, y):
    """极简 Spearman 秩相关（无 scipy 依赖）。仅返回 rho——组数 n=5 样本过小，
    p 值无论算法都无统计意义（且 Python <3.13 无 math.betainc，近似失真），不输出。"""

    def rank(v):
        s = pd.Series(v)
        return s.rank().tolist()

    rx, ry = rank(x), rank(y)
    n = len(rx)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
    return 1 - 6 * d2 / (n * (n * n - 1))


if __name__ == "__main__":
    sys.exit(main())
