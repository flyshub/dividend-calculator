"""可持续性评估的阈值与权重常量（从 sustainability_calculator.py 抽出，#24 纯移动）。

集中在此便于调参；数值与拆分前逐字一致（改任何值必须先同步 JS 镜像并跑 verify）。
"""

# 触发判断的股息率下限（税前，百分比）
THRESHOLD_YIELD = 4.0

# 三档分界（加权总分 0~2）
SCORE_SUSTAINABLE = 1.5   # ≥ 该值 → 可持续
SCORE_WEAK = 1.0          # ≥ 该值 → 偏弱；更低 → 不可持续

# Layer 1 致命红旗阈值
FATAL_CF_COVERAGE = 1.0        # 自由现金流覆盖 < 1.0x → 分红 > FCF（银行短路）
FATAL_BANK_CAR = 10.5          # 银行总资本充足率 < 10.5% → 监管约束分红，致命红线

# Layer 2 六维评分阈值（通用分支）
# 现金流覆盖（经营现金流/分红）
DIM_CF_COVERAGE = (1.0, 1.5)
# 股利支付率
DIM_PAYOUT = (0.60, 0.80)
# ROE（百分比）
DIM_ROE = (10.0, 15.0)
# 资产负债率（百分比）
DIM_DEBT_RATIO = (0.50, 0.70)
# 利息保障倍数（x）
DIM_INTEREST_COVERAGE = (3.0, 5.0)
# 连续分红年数
DIM_CONSECUTIVE_YEARS = (3, 10)
# 曾削减判定窗口（年）：仅考察最新财年往前 CUT_WINDOW_YEARS 年内的相邻年降幅。
# 10 年以上久远的波动（如行业早期调整）对当前分红可持续性无参考价值。
CUT_WINDOW_YEARS = 10

# 六维权重（合计 1.0）
WEIGHTS = {
    "cf_coverage": 0.25,
    "payout": 0.20,
    "profitability": 0.15,
    "balance_sheet": 0.15,
    "dividend_history": 0.15,
    "industry": 0.10,
}

# Layer 3 情境红旗阈值
WARN_PAYOUT_OVER_100 = 1.0        # 股利支付率 > 100%（成熟期股结构性偏高属健康信号，仅警示）
WARN_PRICE_DROP = -0.30        # 近1年股价跌幅 < -30%
WARN_SPECIAL_DIV_MULTIPLE = 2.0  # 当年分红 > 近3年均值 × 2.0（避免早期低基数拉偏误判稳定增长股）
WARN_HOLDING_CONCENTRATION = 0.50  # 前十大持股 > 50%
WARN_HIGH_PAYOUT = 0.80        # 高派息门槛（情境画像用）
