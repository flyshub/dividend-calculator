# 股息可持续性分析 — 设计与实现文档

> 当某只股票的**税前股息率 > 4%** 时，工具会给出该股息率"能否持续"的判断（可持续 / 偏弱 / 不可持续），并附上判断理由与支撑数据。
>
> 本文档记录其业务逻辑、判断模型、数据来源与代码结构，供维护与调参参考。

---

## 1. 背景与目标

高股息率可能是"真高分红"，也可能是"分红陷阱"：

- **周期股景气顶点**：利润暴增 → 静态股息率算得畸高 → 周期反转利润断崖 → 分红难继。
- **透支式分红**：靠借钱或吃老本派息（分红金额 > 自由现金流）。
- **被动高股息**：股价暴跌导致分母缩小，股息率被动推高。
- **突击/特别分红**：当年分红远超历史，往往配合大股东减持。

本功能的目标：**不只给股息率数字，还要给"这个股息率能否持续"的结构化判断**，帮使用者区分真高分红与陷阱。

---

## 2. 判断模型：分层级联（Layered Cascade）

采用"行业路由 → 致命红旗一票否决 → 加权评分 → 情境红旗降档"四层结构。所有阈值与权重集中在 `src/sustainability_calculator.py` 模块顶部常量，便于调参。

### Layer 0 — 行业路由

按行业字符串判定是否**银行/保险**（关键词 `银行`/`保险`）：
- 是 → 走 **金融分支**（看资本充足率/净息差/不良率/拨备）；Layer 1 现金流类致命红旗短路（见下）。
- 否 → 走 **通用分支**（六维加权评分）。

### Layer 1 — 致命红旗（一票否决 → 直接"不可持续"）

满足任一即判不可持续，并输出对应理由（带数值）。**注意**：致命红旗层会先算好维度评分再判否决，故否决时各维度评分仍会展示。

| # | 致命红旗 | 触发条件 | 适用对象 |
|---|---------|---------|---------|
| 1 | 分红超过自由现金流 | FCF 覆盖 < 1.0x（`FATAL_CF_COVERAGE = 1.0`） | 非金融 |
| 2 | 经营现金流为负却分红 | 经营现金流净额 < 0 且分红 > 0 | 非金融 |
| 3 | 亏损却分红 | 净利润 < 0 且分红 > 0 | 全部 |
| 4 | 资本充足率不足（监管约束） | 总资本充足率 < 10.5%（`FATAL_BANK_CAR = 10.5`） | 仅银行/保险 |

> **银行/保险短路**：致命红旗 #1、#2 对金融机构无意义（银行经营CF含存贷款净变动、扩张期为负属常态；FCF 概念对银行不适用——其"投资"是放贷而非固定资产），故 `is_bank=True` 时跳过。仅 #3、#4 适用。

> **支付率 > 100% 不再是致命红旗**（T2 修正）：成熟期/高折旧股（双汇、长江电力、高速公路）支付率结构性 > 100% 属健康信号，单年不否决，改为 Layer 3 情境红旗。

### Layer 2 — 加权评分

#### 通用分支：六维加权评分（每维 0/1/2 分 × 权重）

| 维度 | 权重 | 0 分（危险） | 1 分（警戒） | 2 分（健康） |
|------|------|------------|------------|------------|
| 现金流覆盖（经营CF/分红） | 25% | <1.0x | 1.0–1.5x | ≥1.5x |
| 股利支付率（分红/净利润） | 20% | >80% | 60–80% | <60% |
| 盈利稳定性（ROE+利润趋势） | 15% | ROE<10% 或 利润同比为负降档 | ROE 10–15% | ROE≥15%（利润同比为负时降一档） |
| 资产负债表（负债率+利息覆盖取较低） | 15% | 负债率>70% 或 利息覆盖<3x | 中间 | 负债率<50% 且 利息覆盖>5x |
| 分红历史（连续年数+是否曾削减） | 15% | <3年 或 曾削减 | 3–10年 | ≥10年且无削减 |
| 行业属性 | 10% | 强周期 | 一般 | 防御/成熟稳定 |

- 资产负债率口径：优先取东财 `DEBT_ASSET_RATIO`（百分数→小数），缺失时用 `总负债/总资产` 推算。
- **缺失维度计 0 分（T4 修正）**：某维度数据缺失时**按 0 分计入**加权（分母为全部权重），不再归一化分摊——避免数据稀疏的股票因缺失而虚高得分。缺失权重 ≥ 30% 时额外标注"结论置信度偏低"。
- **银行 general-fallback 屏蔽负债率（T7）**：银行专项全缺失降级通用分支时，资产负债表维度强制设为 None（银行天然负债率 90%+，通用阈值必踩坑）。

#### 金融分支：银行专项评分（等权平均，0~2；CAR 另有致命否决）

| 维度 | 2 分（健康） | 1 分（警戒） | 0 分（危险） |
|------|------------|------------|------------|
| 资本充足率 | ≥12% | 10.5–12% | <10.5%（监管红线 8%） |
| 净息差 | ≥1.8% | 1.4–1.8%（2026 行业冰点约 1.4%） | <1.4% |
| 不良贷款率 | <1.0% | 1.0–2.0% | ≥2.0% |
| 拨备覆盖率 | ≥150% | 120–150% | <120% |

- **资本充足率字段口径（T1 修正）**：取东财 `ADEQUACY_RATIO`（**总**资本充足率），非 `FIRST_ADEQUACY_RATIO`（一级资本充足率）——后者恒 ≤ 总 CAR，用总 CAR 口径的阈值（≥12/10.5）校准才正确。
- **CAR 升致命红线（T1）**：资本充足率 < 10.5% 升为 Layer 1 致命否决（监管约束分红），不再仅作等权评分项——避免"CAR 擦边 8%、其他三项满分"的银行被等权稀释后判"可持续"。
- **降级机制**：若银行专项指标全部缺失，自动降级为通用分支（balance_sheet 维度屏蔽），并在结果 `notes` 标注"银行专项指标不可用"。

### 三档结论（加权总分 0~2）

| 总分 | 结论 |
|------|------|
| ≥ 1.5 | 可持续 |
| 1.0 – 1.5 | 偏弱 |
| < 1.0 | 不可持续 |

### Layer 3 — 情境红旗（不否决，但降一档 + 列入理由）

触发任一情境红旗，结论降一档（可持续→偏弱→不可持续）：

| 情境红旗 | 触发条件 |
|---------|---------|
| 股利支付率 > 100% | 单年支付率 > 100%（`WARN_PAYOUT_OVER_100 = 1.0`，T2；成熟期/高折旧股结构性偏高属健康信号，仅警示不否决） |
| 被动高股息 | 近1年股价跌幅 < -30%（`WARN_PRICE_DROP`） |
| 特别/突击分红 | 最新财年分红 > **近3年均值** × 2.0（`WARN_SPECIAL_DIV_MULTIPLE`，T3；近3年均值缺失时回退全历史均值） |
| 一股独大 + 高派息 | 前十大持股 > 50% 且 支付率 > 80%（当前大股东数据未接入，暂不触发） |
| 周期顶点信号 | 强周期行业 + 净利润同比为负 + 支付率 > 80% |
| 证监会红线画像 | 高负债(>70%) + 弱现金流覆盖(<1.5x) + 高派息(>80%) 三者并存（银行跳过——T7，负债率对银行无意义） |

---

## 3. 输出结构（`SustainabilityResult`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `triggered` | bool | 是否因股息率 > 阈值触发判断 |
| `verdict` | str | 可持续 / 偏弱 / 不可持续 / 未评估 |
| `score` | float\|None | 加权总分 0~2（红旗否决/未触发时为 0 或 None） |
| `fatal_flags` | list[str] | 致命红旗理由（带数值） |
| `warning_flags` | list[str] | 情境红旗理由（带数值） |
| `dimension_scores` | dict | 各维度 0/1/2 分 |
| `metrics` | dict | 支撑数据（payout_ratio、operating_cf、capex、free_cash_flow、cf_coverage、fcf_coverage、debt_ratio、interest_coverage、roe_latest、net_profit_yoy、consecutive_dividend_years、银行专项等） |
| `branch` | str | general / finance / general-fallback |
| `notes` | list[str] | 缺失数据说明 |

---

## 4. 数据来源

可持续性模块**全部走东方财富 datacenter HTTP 接口**（不依赖 mootdx 通达信协议），与 GitHub Pages 静态版同源，便于双端一致性校验。**严禁虚构数据**，任一字段不可用即为缺失并标注。

### 4.1 接口清单

| 数据 | 东财接口（reportName） | host | 关键字段 |
|------|----------------------|------|---------|
| 财务主表 | `RPT_F10_FINANCE_MAINFINADATA` | datacenter.eastmoney.com | ROEJQ / PARENTNETPROFIT / PARENTNETPROFITTZ / NETCASH_OPERATE_PK / NETCASH_INVEST_PK / LIABILITY / TOTAL_ASSETS_PK / DEBT_ASSET_RATIO / INTEREST_DEBT_RATIO / INTEREST_COVERAGE_RATIO / **ADEQUACY_RATIO**（总资本充足率，T1）/ NET_INTEREST_MARGIN / NON_PERFORMING_LOAN / RISK_COVERAGE |
| 现金流量表 | `RPT_F10_FINANCE_GCASHFLOW` | datacenter.eastmoney.com | **CONSTRUCT_LONG_ASSET**（资本开支；主表无此字段） |
| 分红明细 | `RPT_SHAREBONUS_DET` | datacenter-web.eastmoney.com | PRETAX_BONUS_RMB / ASSIGN_PROGRESS / EX_DIVIDEND_DATE / REPORT_DATE |
| 行业分类 | `RPT_F10_BASIC_ORGINFO` | datacenter-web.eastmoney.com | EM2016（东财行业）/ INDUSTRYCSRC1（证监会） |
| 行情（价格/股本/PE/PB） | 腾讯 `qt.gtimg.cn` | — | 价格 f[3] / PE-TTM f[33] / PB f[46] / 总股本 f[73] |

### 4.2 字段语义说明

- **金额单位**：元（如 PARENTNETPROFIT 34502809176.39 = 345 亿元）。
- **比率口径**：ROEJQ、PARENTNETPROFITTZ、DEBT_ASSET_RATIO 等为**百分数**（如 52.0 表示 52%）；资产负债率在评分时转为小数。
- **年报过滤**：`RPT_F10_FINANCE_MAINFINADATA` 把年报与季报累计值混在一起，**只保留 `REPORT_DATE` 月日为 `12-31` 的年报行**，否则净利润取到季报累计值会与分红总额（某完整财年）错配，导致支付率虚高失真。
- **资本开支（CAPEX）**：来自现金流量表 `CONSTRUCT_LONG_ASSET`（购建固定资产/无形资产支付的现金，正数），主表无此字段。

### 4.3 关键衍生指标

| 指标 | 公式 | 口径说明 |
|------|------|---------|
| **自由现金流 FCF** | 经营CF − CAPEX | 有 CAPEX 时用此正确口径。**降级**：CAPEX 缺失时用 经营CF + 投资活动现金流净额（`NETCASH_INVEST_PK` 为负数），但投资活动现金流含买理财/金融投资，会**系统性低估 FCF**（如伊利买理财导致 FCF 被算成负值），仅作兜底。 |
| 经营现金流覆盖 | 经营CF / 分红总额 | 宽松口径（未扣资本开支） |
| 自由现金流覆盖 | FCF / 分红总额 | 严格口径（黄金标准） |
| 股利支付率 | 分红总额 / 净利润 | 净利润 ≤ 0 时不计算（走致命红旗） |
| 利息保障倍数 | 东财 `INTEREST_COVERAGE_RATIO` 直接取 | 已由数据源算好 |

> **为什么 FCF 用 CAPEX 口径**：分红是用现金发的，判断"能否持续"要看扣完维持竞争力必需投资（CAPEX）后还剩多少可分配现金。经营现金流覆盖偏宽松（忽略了还要留钱投资），自由现金流覆盖更严格更权威（S&P DJI、Morningstar 股息安全评级的主用指标）。

---

## 5. 代码结构

### Python 端

| 文件 | 职责 |
|------|------|
| `src/sustainability_calculator.py` | **纯评估器**（无 IO/网络）：`AnnualFinancial` / `DividendHistory` / `SustainabilityResult` dataclass + 衍生指标计算 + 致命红旗 + 维度评分 + 情境红旗 + `assess_sustainability` 主入口。所有阈值/权重常量在此。 |
| `src/sustainability.py` | **数据获取层**：东财接口 fetch（财务/现金流量表/分红/行业）+ 解析纯函数（`parse_financial_rows` / `merge_capex` / `parse_dividend_rows` / `aggregate_dividend_history`）+ 编排入口（`assess_for_stock` / `assess_with_auto_fetch`）。 |
| `src/analysis.py` | 主编排：`run_stock_analysis` 第 4 步，股息率 > 阈值时调 `assess_with_auto_fetch`，结果挂到 `StockAnalysisResult.sustainability`。 |
| `src/web.py` | `/api/pr` 序列化 `sustainability` 字段。 |
| `calc_pr.py` | CLI 打印可持续性结论段。 |

### JS 端（GitHub Pages 静态版，逐字段对齐 Python）

| 文件 | 职责 |
|------|------|
| `site/js/calculator.js` | 纯评估器：`parseSustainabilityFin` / `mergeCapex` / `computeFreeCashFlow` / `checkFatalFlags` / `scoreDimensions` / `scoreFinanceBranch` / `checkWarningFlags` / `assessSustainability`。 |
| `site/js/datasources.js` | 东财接口 fetch：`fetchFinancials` / `fetchCashflow` / `fetchDividendRecords` / `fetchIndustry` + 腾讯行情。 |
| `site/js/app.js` | 编排：`analyzeStock` 多 fetch，`computeFromRaw` 在股息率 > 4% 时调 `assessSustainability`。 |
| `site/index.html` | UI 卡片 + `renderSustainability`。 |

### 一致性校验

`scripts/verify_js_vs_python.py`：取同一批东财 fixture，分别喂给 JS `computeFromRaw` 和 Python `assess_for_stock` 纯函数，逐字段对比（容差 1e-9）。可持续性字段：`sustainability_triggered`、`sustainability_verdict`、`sustainability_score`。

---

## 6. 阈值与权重速查（调参参考）

所有值在 `src/sustainability_calculator.py` 顶部，JS 端对应常量在 `site/js/calculator.js` 的 `SUS_*` 系列。

```python
THRESHOLD_YIELD = 4.0              # 触发判断的税前股息率下限（%）
SCORE_SUSTAINABLE = 1.5            # 加权总分 ≥ 此值 → 可持续
SCORE_WEAK = 1.0                   # ≥ 此值 → 偏弱；更低 → 不可持续

# 致命红旗
FATAL_CF_COVERAGE = 1.0            # FCF 覆盖 < 1.0x（银行短路）
FATAL_BANK_CAR = 10.5              # 银行总资本充足率 < 10.5%（监管约束，仅银行/保险）
# 注：支付率 > 100% 已移至 Layer 3 情境红旗（T2），不再致命否决

# 六维评分阈值 (low, high)：值 < low → 0 分，[low,high) → 1 分，≥ high → 2 分
DIM_CF_COVERAGE = (1.0, 1.5)       # 现金流覆盖（x）
DIM_PAYOUT = (0.60, 0.80)          # 股利支付率（小数）
DIM_ROE = (10.0, 15.0)             # ROE（%）
DIM_DEBT_RATIO = (0.50, 0.70)      # 资产负债率（小数；银行 fallback 时屏蔽）
DIM_INTEREST_COVERAGE = (3.0, 5.0) # 利息保障倍数（x）
DIM_CONSECUTIVE_YEARS = (3, 10)    # 连续分红年数

# 六维权重（合计 1.0）
WEIGHTS = {"cf_coverage": 0.25, "payout": 0.20, "profitability": 0.15,
           "balance_sheet": 0.15, "dividend_history": 0.15, "industry": 0.10}

# 情境红旗
WARN_PAYOUT_OVER_100 = 1.0         # 股利支付率 > 100%（T2，单年不否决）
WARN_PRICE_DROP = -0.30            # 近1年股价跌幅
WARN_SPECIAL_DIV_MULTIPLE = 2.0    # 突击分红倍数（相对近3年均值，T3）
WARN_HOLDING_CONCENTRATION = 0.50  # 前十大持股
WARN_HIGH_PAYOUT = 0.80            # 高派息门槛

# 数据缺失惩罚（T4）
# _weighted_score 返回 (score, missing_ratio)；缺失维度计 0 分（非归一化），missing_ratio ≥ 0.30 标低置信
```

---

## 7. 已知限制与后续可改进

1. **大股东持股数据未接入**：一股独大 + 高派息的情境红旗当前不触发（属 Layer 3 非必需）。可接入东财股东数据接口。
2. **被动高股息（股价跌幅）数据未接入**：需近1年股价涨跌幅，当前未取（走势图有月度价格可算）。
3. **行业字段上游口径**：`/api/pr` 的 `industry` 来自 pr.py（走 mootdx F10），mootdx 不可用时为"未知行业"；可持续性模块内部已走东财重取行业保证银行/周期判定准确，但 UI 上"行业"字段可能显示"未知行业"。
4. **阈值为主观经验值**：六维阈值、权重、三档分界参考 CFA Institute / Investopedia / S&P DJI 等业界共识设定，并非绝对，可按实际案例调参。
5. **银行专项数据**：资本充足率/净息差/不良率/拨备覆盖率达自东财主表（普通股这些字段为空，银行股有值），已验证可用。
6. **A+H 股总市值口径**：当前用 `A股股价 × 总股本(含H股)`，对 A+H 股（如中国银行 H 股占 34.6%、中国神华 24%）会用 A 股价给 H 股估值，若 A 股溢价则高估市值、低估股息率。正确口径应为 `A股股价×A股股本 + H股股价×H股股本`，需额外接入港股行情接口。此为股息率本身的口径问题（非可持续性功能范围），暂以 A 股价近似，后续接入港股行情后修正。

---

## 8. 参考

- [CFA Institute - Analysis of Dividends and Share Repurchases](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/analysis-of-dividends-and-share-repurchases)
- [Investopedia - 4 Ratios to Evaluate Dividend Stocks](https://www.investopedia.com/articles/markets/060116/4-ratios-evaluate-dividend-stocks.asp)
- [S&P DJI - Incorporating Free Cash Flow Yield in Dividend Analysis](https://www.spglobal.com/spdji/en/documents/research/research-incorporating-free-cash-flow-yield-in-dividend-analysis.pdf)
- [Damodaran - A Framework for Analyzing Dividend Policy](https://pages.stern.nyu.edu/~adamodar/pdfiles/ovhds/ch11.pdf)
- [Wall Street Prep - Dividend Coverage Ratio](https://www.wallstreetprep.com/knowledge/dividend-coverage-ratio/)
- A 股监管：证监会《监管指引第3号——上市公司现金分红》、2024 退市新规（累计分红 < 年均净利润 30% 且 < 5000万 → ST）
