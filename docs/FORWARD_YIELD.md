# 下一财年前瞻股息率（Forward Yield）

Status: 已实施（2026-11-17 落地 P1–P4，P5 未做；见 §9）

目标：在现有「真实股息率（最近完整财年）」之外，增加一个**下一财年**的股息率估算，
让用户在分红公布之前就能看到"按当前盈利预期与公司分红习惯，明年大概能拿到多少"。
口径与落点以本文件为准；实现与本文冲突时，以本文为准则修代码或改本文，不允许长期背离。

---

## 1. 口径定义

| 口径 | 定义 | 现状 |
| --- | --- | --- |
| 真实股息率（主口径） | 最近完整财年现金分红总额 ÷ 当前总市值 | 已有 |
| TTM 股息率 | 近 12 个月按除权日实际派发总额 ÷ 当前总市值 | 已有 |
| **前瞻股息率（新增）** | **预测下一财年每股分红 ÷ 当前股价** | 本提案 |
| TSR | (ROE − 股息率×PB) + 股息率 | 已有，展示用 |

- 财年 `L` = `DividendSummary.latest_year`（有年报的最新年份，现行单一实现
  `sustainability.classify_fiscal_report`）。
- 前瞻财年 `F = L + 1`。**不滚动到"未来 12 个月"**：全站所有口径都以财年归因，
  混入 NTM 会让"财年"这个词失去意义（NTM 是「FY 末期 + 下一财年中期」的拼盘，
  A 股中期分红节奏不稳定，容易算成 0 或 1.5 倍）。
- 输出**税前**一个数；与主口径同为税前，便于并排比较。税后可按 0.9 / 0.8 折算，
  但不在卡片上堆三个数。

> 命名建议：卡片标题写「前瞻股息率（2026E，模型估算）」，不要只写"下一年度"，
> 页面上"下一年度"容易被读成"明年能收到的现金"，而 FY2026 的末期分红实际在 2027 年 6-7 月到账。

---

## 2. 数据源研究（2026-11-17 实测）

### 2.1 为什么不能用"公司公告的下一财年分红预案"

- 时点太晚：预案披露通常在年报同时（次年 3-4 月），届时"前瞻"已无意义。
- 覆盖太窄：中报分红预案只覆盖少数公司，且不含末期。
- 结论：前瞻只能走**盈利预测 × 分红政策假设**的模型路线，并明确标注"模型估算"。

### 2.2 东财一致预期 `RPT_WEB_RESPREDICT`（推荐，唯一免费前瞻输入）

```
https://datacenter-web.eastmoney.com/api/data/v1/get
  ?reportName=RPT_WEB_RESPREDICT&columns=ALL
  &filter=(SECURITY_CODE="600519")&pageNumber=1&pageSize=1
```

实测结论：

1. 字段：`YEAR1..4` / `YEAR_MARK1..4`（`A`=实际，`E`=预测）/ `EPS1..4`（元/股）/
   `RATING_ORG_NUM`（覆盖机构数）/ `DEC_AIMPRICEMIN/MAX`（目标价，不用）。
   **没有每股分红或股息率预测字段** —— 只有 EPS 预测，分红必须自己用支付率推。
2. CORS：带 `Origin` 头请求，返回 `Access-Control-Allow-Origin: *`（与现有分红接口同源），
   浏览器直连可用，不需要 Python 代理。
3. 覆盖：招商银行 21 家、茅台 43 家、宁德 38 家；小盘无覆盖时 `result.data = null`
   （鲍斯股份 300441）。*必须*把"无覆盖"与"网络失败"分开处理。
4. 批量：`filter=(SECURITY_CODE in ("600519","600036","000858"))` 可行，
   未来进选股器可按批取数。
5. **基年校准（关键）**：`EPS1 × 当前总股本 == 归母净利润(FY2025)`，误差 1e-11。
   说明一致预期的 EPS 是**当前股本口径**（不是财报 EPSJB）。

   - 反例：东鹏饮料 605499，`EPS1=6.0137`，财报 `EPSJB=8.4907`，差 29%——
     因为 2025 年内有过送转，EPSJB 是加权平均股本口径。
   - 所以不能用"EPS1 vs EPSJB"做一致性校验，只能用 `EPS1 × 当前股本 vs 归母净利润`。
   - 这也说明**不能用「上年 DPS × EPS 增速」推前瞻 DPS**：历史 DPS 是除权时点名义值，
     与当前股本口径不同（东鹏会算出 40% 的假增速）。

### 2.3 已有数据（无需新增取数）

| 用途 | 来源 | 状态 |
| --- | --- | --- |
| 历年现金分红（每股 + 登记股本） | 东财 `RPT_SHAREBONUS_DET` | 已有，`DividendRecord.total_shares` |
| 历年归母净利润 | 东财 `RPT_F10_FINANCE_MAINFINADATA` | 已有，`parse_financial_rows` 年报行 |
| 当前股价 / 总股本 | 腾讯行情 | 已有 |
| 行业（周期股标注） | 东财 EM2016 | 已有 |

### 2.4 实测样本（模型跑出来的真实数字，非估算示例）

| 代码 | 名称 | 现价 | 主口径股息率 | 3 年支付率 | 2026E EPS | 前瞻股息率 | 覆盖 |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |
| 600519 | 贵州茅台 | 1457.70 | 3.57% | 79.0 / 75.1 / 51.9 | 66.81 | 3.44% | 43 |
| 600036 | 招商银行 | 43.45 | 4.64% | 33.9 / 34.0 / 33.9 | 5.976 | 4.67% | 21 |
| 600900 | 长江电力 | 30.60 | 3.27% | 70.9 / 71.0 / 73.6 | 1.402 | 3.25% | 20 |
| 601398 | 工商银行 | 9.06 | 3.42% | 30.0 / 30.0 / 30.0 | 1.053 | 3.49% | 14 |
| 601088 | 中国神华 | 50.57 | 3.97% | 79.1 / 80.5 / 75.2 | 2.810 | 4.40% | 25 |
| 000858 | 五粮液 | 73.98 | 6.97% | **223.6** / 70.0 / 60.0 | 3.282 | 3.11% | 15 |
| 600585 | 海螺水泥 | 18.38 | 4.62% | 55.5 / 48.9 / 48.8 | 0.805 | 2.14% | 16 |
| 605499 | 东鹏饮料 | 117.00 | 4.27% | 62.2 / 69.1 / 49.0 | 6.750 | 3.59% | 39 |
| 002714 | 牧原股份 | 45.82 | 2.96% | 48.6 / 43.0 / 30.4 | **−1.085** | — | 23 |
| 300441 | 鲍斯股份 | 6.29 | 1.03% | 21.6 / 34.8 / 16.3 | **无预测** | — | 0 |

读法：

- 招行/长电/工行——支付率极稳，前瞻≈主口径，可信度高。
- 五粮液——2025 年净利润骤降但分红照发（支付率 223%），单纯拿"最近支付率"外推会得出
  10%+ 的荒谬结果；3 年中位数把前瞻拉回 3.1%。**这就是必须平滑支付率的实证理由。**
- 海螺/神华——周期股 EPS 预测波动大（海螺 2026E 比 2025A 低 48%），前瞻数字要挂警示。
- 牧原（预测亏损）、鲍斯（无覆盖）——必须明确返回"无前瞻"，而不是 0 或负股息率。

---

## 3. 计算模型

### 3.1 推荐公式（模型 E1）

```
payout_i   = Σ_{rec∈FY i}(10派/10 × 该次登记股本) / 归母净利润_i      # 口径无关股本，可跨送转
payout_hat = clamp(median(payout_L, payout_{L-1}, payout_{L-2}), 0, 1.0)
DPS_F      = 一致预期 EPS_F × payout_hat
forward_yield = DPS_F / 当前股价 × 100
```

- 只取**有年报、净利润>0、有现金分红**的最近 3 个财年；不足 3 年就用可用样本；
  1 年都没有 → 返回"无前瞻"。
- `clamp(·, 1.0)`：长期支付率不可能稳定超过 100%；截断后挂 warning「最近支付率超 100%」。
- 周期股不特殊处理公式（仍用 3 年中位数），只在展示层加"周期行业盈利预测波动大"提示。

### 3.2 备选方案与取舍

| 方案 | 做法 | 结论 |
| --- | --- | --- |
| **E1 推荐** | EPS_F × 3 年支付率中位数 | 支付率是公司政策，粘性最强；对特别分红/盈利骤降稳健 |
| E2 | 上年 DPS × (1 + EPS 增速) | 隐含"支付率不变"，去年若含特别分红会被等比放大（五粮液 6.97%→7.0%，错得离谱）；且上年 DPS 是旧股本名义值，送转公司会错配 |
| E3 | 3 年支付率加权平均（Σ分红/Σ净利） | 更"经济"，但盈利骤降年份权重低，反而向高支付率倾斜；不如中位数透明 |
| E4 | 悲观/中性/乐观三档（min/median/max 支付率） | 信息量最大，但 v1 先不铺开；中位数单值 + 假设明示已够，后续可作为第二行小字 |

### 3.3 边界与警告规则

| 场景 | `forward_status` | 行为 | 文案要点 |
| --- | --- | --- | --- |
| 无完整财年 / 无分红记录 | `no_complete_year` | 不估 | 无完整财年分红，不做前瞻 |
| 无机构覆盖（`code=9201` 或 `data` 为空） | `no_coverage` | 不估，**保留目标财年** | 无机构盈利预测，未做前瞻估算 |
| 有覆盖但缺 `F=L+1` 年 EPS | `no_target_year` | 不估，保留目标财年 | 无 FYxxxx 盈利预测 |
| `EPS_F <= 0`（预测亏损） | `no_positive_eps` | 不估 | 机构预测下一财年亏损，不估股息率 |
| 支付率样本为空 | `no_payout_history` | 不估 | 无可用分红历史 |
| 基准年 − 最新分红财年 ≥ 2 | `stale` | 不估，**目标年置空**（`L+1` 已是过去年份） | 近年无分红，不做前瞻 |
| 缺现价（股本缺失不阻断，见 §5.1） | `missing_inputs` | 不估 | 缺少现价，不做前瞻 |
| 网络 / HTTP / 接口故障（非 9201） | `fetch_error` | 不估，不阻塞主结果 | 前瞻取数失败（来源），稍后重试 |
| 覆盖机构 < 3 | `ok` | 照算 + warning | 覆盖不足，预测可靠性低 |
| 最新支付率 > 100% 或与中位数偏离 > 30pct | `ok` | 照算 + warning（两者合成一条） | 最新财年支付率 X%（超出当年净利），与中位數 Y% 偏离超 30 个百分点，分红政策可能变化 |
| 中位数 > 100% | `ok` | 截断为 100% + warning | 近3年支付率中位数 X% > 100%，假设截断 |
| 支付率样本不足 3 年 | `ok` | 照算 + warning | 支付率样本不连续（最近 3 个财年仅 N 年可用） |
| 周期行业 | `ok` | 照算 + warning | 周期行业，盈利预测波动大，前瞻仅供参考 |
| 前瞻 vs 主口径偏离 > 50% | `ok` | 照算 + warning | 请核对 |
| 前瞻 > 15% | `ok` | 照算 + warning | 数值异常，请核对数据 |

**`no_coverage` 与 `fetch_error` 必须分开**：东财对两者都回 HTTP 200 + `success=false`，
判据是 `code`——`9201`（返回数据为空）是真·无覆盖，其他非 0 code（如 `9501` 参数错误）
是接口故障。判据实现在 `eastmoney_fetcher._forecast_rows_from_payload` 与
`datasources.js::forecastRowsFromPayload`，两端各有单测。

### 3.4 一致性内核

`eps_forecast × 当前总股本 ≈ 归母净利润(F)` 这条等式是模型的物理基础。
实现时对**基准年**（`YEAR1..4` 中 `MARK='A'` 的**最新**一年，不一定是 `YEAR1`）做校验：
`|EPS(基准年) × 当前股本 − 归母净利润(基准年)| / 归母净利润 > 3%` →
warning「一致预期基准年(YYYY) EPS x × 股本 与归母净利偏差 N%，股本口径可能不一致」。
校验在早退分支之前执行，因此不估（如无分红史）时也会保留该提示。
不通过也不拒绝计算（数据源滞后常见），只提示。

---

## 4. 架构落点（单一来源）

原则沿用仓库现有 seam：**纯计算一处实现，取数在 fetcher，编排层注入**。

### Python

| 文件 | 改动 | 职责 |
| --- | --- | --- |
| `dividend-calculator/src/eastmoney_fetcher.py` | +`fetch_earnings_forecast(code) -> Optional[List[dict]]` | 只做 HTTP + `result.data`；沿用分红接口的 #38 M5 语义：`None`=取数失败，`[]`=无覆盖（东财 `result.data = null`，是真实结论），调用方给不同文案 |
| `dividend-calculator/src/forward_yield.py` | **新增**，纯函数 + `ForwardYieldResult` | `dividend_totals_by_year`（分年分红总额）/ `net_profit_by_year` / `compute_forward_yield`（纯函数）/ `estimate_forward_yield`（编排，取数可注入）；纯函数无网络 |
| `dividend-calculator/src/analysis.py` | `StockAnalysisResult` +`forward_yield`；流水线末尾调 `estimate_forward_yield(latest_year=..., trailing_yield=...)` | 编排；失败静默 None |
| `dividend-calculator/src/web.py` | `/api/pr` payload +`forward`（`asdict`）；`/api/calculate` **不动**（保持纯股息率，见 #248） | 序列化 |
| `dividend-calculator/tests/test_forward_yield.py` | **新增** | 纯函数全边界 |
| `dividend-calculator/tests/test_eastmoney_forecast.py` | **新增** | 取数三分类（行 / 无覆盖 / 故障），含实测的 9201 与 9501 形状 |
| `dividend-calculator/tests/test_web.py` | 断言 payload 含 `forward`（含 `forward_status`） | 契约 |

> 不在 `dividend.calculate_true_dividend_yield` 里加：那是 `/api/calculate` 的"纯股息率"
> 路径，加进去会让快照测试全部触网；前瞻需要财务+预测，属于综合分析。

### 浏览器（页面主路径，`site/index.html` 直接走 DividendApp）

| 文件 | 改动 |
| --- | --- |
| `site/js/datasources.js` | +`fetchEarningsForecast(stockCode)`（CORS ✓，5s 超时）+`forecastRowsFromPayload`（三分类，镜像 Python） |
| `site/js/datasources.test.js` | **新增** | 三分类与 Python 用例一一对应 |
| `site/js/calculator.js` | `parseDividendRecords` 追加返回 `yearlyDividendTotals`（复用 `_aggregateDividendHistory` 已算的 yearAmount，避免二次循环）；`parseFinancials` 追加返回 `netProfitByYear`；+`computeForwardYield({price, totalShares, latestYear, latestAnnualYear, yearlyDividendTotals, netProfitByYear, forecast, isCyclical})` |
| `site/js/app.js` | `computeFromRaw` 组装 `dividend.forward`；预测**不进** 主 `Promise.all`，由 `fetchForward` 在渲染后二次取数（`forecast_rows === undefined` = 尚未取数） |
| `site/index.html` | TTM 卡之后新增前瞻卡；`clearState` 复位；渲染假设文案 |
| `site/js/formatResult.js` | 拍平 `forward_*` 字段（双端对账） |
| `site/js/calculator.test.js` | 与 Python 相同的用例镜像 |
| `scripts/verify_js_vs_python.py` | fixture 加 `forecast_rows`；`FIELDS_NUMERIC` 加 6 个前瞻数值字段，`FIELDS_STR` 加 `forward_eps_kind / forward_status / forward_warnings`（集合） |

`verify_raw.js` 不用改：fixture 带着 `forecast_rows` 进 `computeFromRaw` 即可。

### 选股器（P4，另立 ADR）

- 取数 CI 安全：`RPT_WEB_RESPREDICT` 是东财 HTTP，不受 TDX 封锁影响（#334）。
- 落库不能直接照搬：`FinanceSnapshot` 只存最新年净利润与支付率，**没有 3 年净利润序列**。
  建议在刷新时把 `payout_3y_median` 算成一个标量存进新的 `forecast_snapshot`
  （code / forward_year / eps_forecast / payout_3y_median / coverage / source / updated_at），
  避免为历史财务扩表。
- 前瞻股息率**不进漏斗判据**：没有历史一致预期快照，无法回测；先像 TSR 一样只展示。
  想进判据，先补 12-24 个月的一致预期落库，再做一次回测。

---

## 5. 数据契约

```json
"forward": {
  "forward_status": "ok",
  "forward_year": 2026,
  "forward_eps": 66.807,
  "forward_eps_kind": "E",
  "forward_payout_assumed": 0.751,
  "forward_payout_basis": "近3年支付率中位数 75.1%",
  "forward_payout_samples": [0.79, 0.751, 0.519],
  "forward_dps": 50.177,
  "forward_yield_before_tax": 3.44,
  "forward_coverage": 43,
  "forward_source": "东财一致预期",
  "forward_warnings": [],
  "forward_note": "2026E EPS 66.81 × 支付率 75.1% ÷ 现价 1457.70"
}
```

约定：

- 数值一律**不四舍五入**进契约（展示层再 toFixed），保证 JS/Python 在 1e-9 内对齐。
- `forward_yield_before_tax` 单位是百分数（与主口径一致）。
- 状态只由 `forward_status` 表达，`forward_note` 仅是人类文案，**不允许被解析**。
- 能确定目标财年（`latest + 1`）时就保留 `forward_year`，即使不估——
  UI 才能显示「FY2026E 无预测」。唯一例外是 `stale`：目标年已是过去年份，置 null。

### 5.1 `forward_status` 枚举

| 值 | 含义 | `forward_year` | UI 文案 |
| --- | --- | --- | --- |
| `ok` | 已估 | L+1 | 数值 + 假设链 |
| `no_complete_year` | 无完整财年分红 | null | 不做前瞻 |
| `fetch_error` | 取数失败（分红/财务/预测任一路，**不含** `code=9201`） | L+1 | 前瞻取数失败（来源），稍后重试 |
| `no_coverage` | 东财无机构覆盖（`success=true` 无 data，或 `code=9201`） | L+1 | FYxxxx 无机构盈利预测（无 `E` 后缀＝没有预测） |
| `no_target_year` | 有覆盖但缺目标年 EPS | L+1 | 无 FY2026E 盈利预测 |
| `no_positive_eps` | 目标年预测亏损/为 0 | L+1 | 机构预测亏损，不估 |
| `no_payout_history` | 近 3 年无可用支付率样本 | L+1 | 无可用分红历史 |
| `missing_inputs` | 缺现价（股本缺失仅跳过口径守卫，不阻断） | L+1 | 缺少现价，不做前瞻 |
| `stale` | 基准年 − 最新分红财年 ≥ 2 | **null** | 近年无分红 |

---

## 6. 页面呈现

在「股息率 / YIELD」区块，TTM 卡之后新增一张卡（不置顶、不替换主口径）：

```
下一财年前瞻股息率（FY2026E，模型估算）        3.44%
EPS 66.81 × 支付率 75.1% = 每股 50.18 元
税前估算；税后按持股期限折算 | 一致预期覆盖 43 家 | 近3年支付率中位数 75.1%
较最新财年主口径 3.57% 低 0.12pct（不同财年）
[最新财年支付率 223.6% > 100%…；模型估算，非公司指引，仅展示不参与选股判据]
```

- 命名统一为「下一财年（FYxxxxE）」：与主口径同为财年口径，避免"年度/财年"混用。
- 必须写清**税前**（用户要的是税后到手，税后折算在上方三张卡里）与**非公司指引**。
- 比较行标"较最新财年主口径"并注明"不同财年"，不与同财年的上年比较混为一谈。
- 假设链完整可见，不做"黑箱一个数"；无预测时保留卡片（静默缺失比 0 更危险）。
- **非阻塞**：主结果先渲染，预测到达后二次填充（5s 超时 + 请求序号防旧结果覆盖新结果）。
- **三条兜底**：① 页面资源版本不一致（旧 JS 缓存）→ 卡片直接提示强制刷新；② 8s 看门狗 → 「前瞻取数超时，稍后重试」；③ 正常返回 → 填充。卡片不允许停在「正在获取」
- **版本戳**：`index.html` 的本地脚本带 `?v=BUILD`，改 JS 必须升版，否则新 HTML 配旧缓存 JS 会报 `fetchForward is not a function`

### 6.1 选股结果页列（#157）

每日选股结果表在「真实股息率%」之后展示「前瞻股息率%」。数据由每日选股任务离线算好、随 CSV/JSON 发布，页面只渲染、不发请求：

| CSV 列 | 含义 |
| --- | --- |
| `前瞻股息率%` | 仅 `forward_status == ok` 时给两位小数；不可估留空（页面显示「—」，绝不填 0） |
| `前瞻财年` | 目标财年 L+1；`stale` / `no_complete_year` 为空 |
| `前瞻状态` | §5.1 枚举原样落库，供页面解释不可估原因（文案映射见本节下表） |

- 口径与主页面一致（EPS × 支付率中位数 ÷ 现价，**税前、模型估算、非公司公告**），悬停展示目标财年与口径。
- 只对漏斗幸存行计算（约 30 行）；命中可持续性快照时只发 1 次一致预期请求，快照未命中（当日行情新进漏斗、预拉名单还没覆盖）时按需补拉分红/财务明细（共 3 次），与主页面同源同口径。
- 预拉名单按库内旧行情筛选（real_yield ≥5 且 ttm ≥5），股价涨进漏斗的股票可能没有快照——这是按需补拉存在的原因，不能据此判「缺失」。
- 仅展示：不参与漏斗判定、排序与回测。
- 历史兼容：上线前的 14 列 CSV 仍可导出（`LEGACY_FIELDS_14_NO_FORWARD`）；历史按日 JSON 无前瞻 key → 页面显示「—」，不回溯补算。
- 改 `screener_render.js` 必须同步升 `site/screener.html` 的脚本版本戳。

**失败隔离与整批护栏**（#159）：

- 单只异常（一致预期返回 `None`、快照 JSON 损坏等）只让该行变 `fetch_error`，不中断当日选股。
- 实际发起请求的行 ≥10 且失败率 >50% → 判定数据源故障：本日三列全部留空并输出 `::warning::`，退出码与 CSV/JSON 产出不变（宁整列留空，也不要「全市场都没有预测」的静默假象）。
- 分母只计真正发过请求的行：快照缺失（`missing_inputs`）、无完整财年不发请求，不计入；`stale` 的判定发生在取数之后（请求已发出），计入；市场事实（`no_coverage` 等）计分母但不计失败。
- 实测（2026-11-26，GitHub runner，run 45833096362）：31 只入选、26 只可估，该接口当日未观察到限流，护栏未触发。

**不可估原因的悬停文案**（`forward_status` → 页面，市场事实与数据缺口分开，避免把取数故障读成「没有机构看好」）：

| `forward_status` | 类别 | 悬停文案 |
| --- | --- | --- |
| `no_coverage` | 市场事实 | 市场：无机构盈利预测 |
| `no_positive_eps` | 市场事实 | 市场：机构预测亏损 |
| `no_target_year` | 市场事实 | 市场：无目标财年预测 |
| `no_complete_year` | 市场事实 | 市场：近年无完整财年分红 |
| `stale` | 市场事实 | 市场：近年无分红 |
| `fetch_error` | 数据缺口 | 数据缺口：前瞻取数失败 |
| `missing_inputs` | 数据缺口 | 数据缺口：缺少分红/财务快照 |
| `no_payout_history` | 数据缺口 | 数据缺口：无可用分红历史 |

历史按日 JSON 无 `前瞻状态` key → 只显示「—」，不给提示；未知枚举同样不渲染（宁可不解释，也不泄漏英文状态名）。第九个状态 `ok` 有数值，悬停只给目标财年与口径，不写原因。

---

## 7. 测试与验证

**纯函数单测（Python + JS 镜像）**

1. 正常：支付率 3 年 + 预测 EPS → DPS / yield 精确值。
2. 支付率样本 2 年（偶数中位数）/ 样本不连续 warning / 0 年。
3. 中位数被特别分红年拉高 → clamp 到 1.0 + warning。
4. 预测 EPS 为 0 / 负 → None。
5. `latest_year` 落后最新年报年（停发一年）→ None。
6. 预测行缺失 / 无覆盖 → None。
7. 股本变化（东鹏）：payout 用行股本折算，结果不随股本口径漂移。
8. 价格为 0 / 缺失 → None。
9. 基准年（最新 `MARK='A'` 年）EPS×股本一致性 warning，且在早退分支仍生效。
10. 周期行业 warning。

**双端对账**

```bash
python scripts/verify_js_vs_python.py 600519 600036 000858 601088 002714 300441 605499
```

覆盖：正常 / 特别分红失真 / 周期 / 预测亏损 / 无覆盖 / 送转。

**人工抽检**

- 用 `python -m src.web` 起页面，逐只核对卡片的假设链与数字，尤其五粮液（3.11% vs 6.97%）
  要在 warning 里讲清楚"为什么差这么多"。
- 抽 3 只不同行业，核对 `forward_eps` 与东财网页"盈利预测"一致。

**回归**

- `pytest -m "not integration"` 全绿；`node --test site/js/calculator.test.js` 全绿。
- `tests/test_web.py` 增加 `/api/pr` forward 字段断言。

---

## 8. 风险与已知局限

1. **预测不是事实**：EPS 是一致预期（可能会被下修），支付率是假设。页面必须与"真实股息率"
   在视觉与文案上区分，不允许把两者混为"股息率"。
2. **无法回测**：免费源没有历史一致预期快照，无法验证"前瞻股息率"的选股/择时价值 →
   不进漏斗判据（与 TSR 同处理）。想验证只能从现在开始把预测落库，事后用实际分红校准。
3. **支付率政策突变**：回购替代分红、特别分红、股东回报规划切换都会让 3 年中位数失真；
   因此 warning 里保留"上年支付率"原始值，让用户自己判断。
4. **周期股 EPS 预测失真**（海螺 2026E 比 2025A 低 48%、神华高 15%）：
   前瞻在周期股上的置信度低，提示必须在卡片上，不能只写在文档里。
5. **股本变动**：模型对送转免疫（payout 用行股本；EPS 用当前股本），但对"大额增发"
   敏感——一致预期 EPS 已按增发后股本摊薄，DPS 与现价同口径，逻辑上自洽；
   唯一风险是增发发生在预测之后。
6. **覆盖偏差**：有覆盖的多是大中盘，前瞻模块天然偏向大票；小盘无覆盖时明确显示缺失。
7. **静默失败**：东财任何一路失败都只能降级为 None，不能把 0 当结果（沿用 TTM 的降级语义）。

---

## 9. 实施顺序（每阶段可独立验收）

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P1 | `fetch_earnings_forecast` + `forward_yield.py` + 单测 | `pytest tests/test_forward_yield.py` 全绿，离线 |
| P2 | JS 纯函数 + 单测（`calculator.test.js`） | `node --test` 全绿，与 P1 用例一一对应 |
| P3 | 页面接入（datasources/app/index.html）+ `/api/pr` 序列化 | 本地起服务，7 只样本核对；`test_web` 过 |
| P4 | 双端对账脚本 + 文档（CONTEXT.md 词条、README 功能行） | `verify_js_vs_python.py` 全字段一致 |
| P5（可选） | 选股器：`forecast_snapshot` 表 + 列 + 分片取数 | 另立 ADR；先跑一轮全量回填，再看是否进漏斗 |

工作量估计：P1-P4 约 600-700 行（含测试），不触碰现有计算路径，
`calculate_true_dividend_yield`、`judge_pr`、漏斗阈值全部零改动。

**实现状态（2026-11-17 落地，P1-P4 已完成，P5 未做）**

| 阶段 | 落点 |
| --- | --- |
| P1 | `src/eastmoney_fetcher.fetch_earnings_forecast`、`src/forward_yield.py`、`tests/test_forward_yield.py`（27 例） |
| P2 | `site/js/calculator.js::computeForwardYield` + `calculator.test.js`（21 例，与 Python 同输入同期望）；`parseFinancials` 增 `netProfitByYear`，`parseDividendRecords` 增 `yearlyDividendTotals` |
| P3 | `site/js/datasources.js::fetchEarningsForecast`、`app.js` 组装、`formatResult.js` 拍平、`index.html` 前瞻卡片；`src/analysis.py` + `src/web.py`（`data.forward`）+ `calc_pr.py` 输出 |
| P4 | `scripts/verify_js_vs_python.py` 增 `forecast_rows` 与 7 个前瞻字段；README 功能行与 API 行 |

页面与 Python 侧的行为差异仅一处：`/api/pr` 在无完整财年时不调用前瞻（`forward: null`），
页面同样隐藏该卡片，因此两侧在"无财年"场景下都只保留主口径，不展示前瞻。

---

## 10. 待确认的决策

| # | 决策 | 推荐（已确认） | 备选 |
| --- | --- | --- | --- |
| D1 | 前瞻年份 | `L+1`（下一财年） | 未来 12 个月（含末期+中期拼盘） |
| D2 | 支付率假设 | 近 3 年支付率**中位数**，上限 100% | 最近财年 / 3 年加权平均 / 三档区间 |
| D3 | 落地范围 | 页面卡片 + `/api/pr` + CLI，**不进选股器** | 同步进选股器（需先补历史预测落库与回测） |

---

## 11. 评审整改记录（2026-11-17，P0 全部 + P1-1/2）

外部评审的 5 条 P0 逐条处置。**先核现状再动手**：2 条已满足、1 条不成立。

| 评审项 | 判定 | 处置 |
| --- | --- | --- |
| P0-1 `forward_status` 契约 | 真缺口 | 新增 9 值枚举（§5.1），`forward_note` 降为纯文案；能定目标年就保留 `forward_year` |
| P0-2 命名/文案 | 成立 | 「下一财年（FYxxxxE）」+ 税前说明 + 非公司指引 + 「较最新财年主口径（不同财年）」 |
| P0-3 样本规则 | 基本已满足 | 滑窗本就取最近连续 3 财年、偶数中位数双端同语义且有测试、clamp warning 条件即"是否被截断"；只把「仅 N 年样本」改成「样本不连续」 |
| P0-3 至少 2 年样本 | **不采纳** | 1 年样本仍给结果 + 强警示，展示层返回空的信息量更差；进选股器时再抬门槛 |
| P0-4 财年映射 | 已满足 | 按 `YEARn == F` 取 EPS、按该年 mark 定 A/E、分红按 `report_time` 归财年 |
| P0-4 基年交叉校验 | 真缺口（最有价值） | 原校验只在 `kind == 'A'` 触发、常规场景从不执行；改为用 `base_year` 的已披露 EPS 校验 `EPS × 当前股本 ≈ 基准年归母净利`，并在早退分支前执行 |
| P0-5 阻塞主结果 | 真缺陷 | 预测移出主 `Promise.all`：主结果先渲染 → 5s 超时取数 → `paintForwardCard` 二次填充，请求序号防旧结果覆盖；`forecast_rows === undefined` 表示"尚未取数"，与 `null`（失败）区分 |
| P1-1 支付率趋势 | 可加 | 最新财年与中位数偏离 >30pct → 「分红政策可能变化」；与「>100%」合成一条，同一数字不报两次 |
| P1-2 测试补充 | 成立 | 补 `forward_status` 全分支、样本不连续、趋势偏离、基年校验在早退分支仍生效；`verify_js_vs_python.py` 增 `forward_status` 与 `forward_warnings` 集合对账 |
| P1-3 `forecast_snapshot` | 不做 | 属 P5 回测，需与落库一起设计 |
| P1-4 feature flag | 不做 | `forward_status = fetch_error` 已可驱动降级，另加开关是过度设计 |
| P1-5 样本 tooltip | 不做 | 样本已明文展示 |

评审另指出「600-700 行略低估」——实际 diff 626 行 + 3 个新文件，属实。

### 第二轮（`$code-review` 自查后的修复）

| 项 | 处置 |
| --- | --- |
| `no_coverage` / `fetch_error` 无法区分 | 新增 `_forecast_rows_from_payload` 与 `forecastRowsFromPayload`：`code=9201` → 无覆盖，其他非 0 code（实测 `9501` 参数错误）→ 取数失败；两端各加 6 例单测 |
| `missing_price` 曾要求同时有股本 | 状态名保留 `missing_inputs`，但门只查现价：股本仅用于 `EPS×股本≈归母净利` 口径守卫，缺失时跳过告警继续估算（页面 quote 无股本时不应与 `/api/pr` 分叉） |
| 取数失败/无覆盖文案与 §5.1 不一致 | 统一为「前瞻取数失败（来源），稍后重试」/「无机构盈利预测，未做前瞻估算」，双端同步 |
| 孤儿 `logger` | 删除 `import logging` 与 logger（从未使用） |
| 模块 docstring 只写 7 个对账字段 | 更新为 9 个 + warnings 集合 |
| §3.3 / §3.4 / §4 / §7 与实现脱节 | 全部按代码重写：状态列、基准年取最新 `MARK='A'`、函数与参数真名、对账字段数 |
| `tests/test_web.py` 未断言新字段 | 补 `forward_status == "ok"` |

未处理（有意留待有需求时）：Python `%.1f` 与 JS `toFixed` 在 `.x5` 上的舍入不对称；`compute_forward_yield` 的 8 参数收成 `ForwardInputs`。

---

## 附：研究依据与实测命令

- 一致预期端点（2026-11-17）：`RPT_WEB_RESPREDICT`，`columns=ALL`，
  `YEAR1..4 / YEAR_MARK1..4 / EPS1..4 / RATING_ORG_NUM`。
- 基年校准：`EPS1 × 当前总股本 = 归母净利润(YEAR1)`，600519/600036/000858/300750/601398
  误差均 < 1e-9（验证脚本可复用）。
- 支付率口径：`Σ(10派/10 × 行 TOTAL_SHARES) / PARENTNETPROFIT(12-31)`。
- 样本数字见 §2.4，均由上述端点 + 现有分红/财务/腾讯行情实算。
