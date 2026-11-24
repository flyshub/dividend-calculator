# 真实股息率计算工具

基于 Python 的 A 股真实股息率 + 市赚率计算工具。技术栈：Python 3.9+、mootdx（通达信协议）、腾讯行情、ECharts 前端。

## ⚠️ 路径规范（必读，防漂移）

- **目录结构**：git 根在外层 `/Users/fly/Claude/dividend-calculator`，实际项目在内层 `dividend-calculator/`（非嵌套 git，仅子目录）。
- **macOS 大小写不敏感**：`/Users/fly/Claude` 与 `/Users/fly/claude` 是**同一物理目录**。混用大小写会导致 `pwd` 漂移、相对路径解析错位（表现为"文件不存在"）。
- **铁律**：所有命令用**绝对路径 + 大写 `Claude`**：`cd /Users/fly/Claude/dividend-calculator/dividend-calculator`。每个 Bash 命令开头固定 `cd`，不依赖 `pwd` 继承。
- 项目内相对路径一律以 `dividend-calculator/`（内层）为基准，避免两层前缀 + 大小写叠加出错。

## 常用命令

```bash
# 股息率计算
cd dividend-calculator && python -m src.main 600987

# 市赚率计算
cd dividend-calculator && python calc_pr.py 600900

# 组合股息分析（持仓：代码:股数，逗号分隔）
cd dividend-calculator && python -m src.main --portfolio "600900:1000,601398:5000"

# 启动 Web 服务
cd dividend-calculator && python -m src.web

# 运行全部测试
cd dividend-calculator && python -m pytest -v

# 同步生产数据（本地 screener.db 不自动更新；排查选股数据前先跑）
cd dividend-calculator && python scripts/sync_seed_db.py

# 运行单个测试
cd dividend-calculator && python -m pytest tests/test_dividend.py -v
```

## 核心架构决策

### 总额法 > 每股法

```
真实股息率 = 最近完整财年现金分红总额 / 当前总市值 × 100%
```

转送股会导致「每股分红/股价」被动变化，总额法分子分母来自同一截面，不受影响。

**走势图口径（2026-11 审计后钉死）**：价格线画**前复权价**（已按分红送转回溯调整，非当年实际成交价，图例/轴/tooltip 必须写「前复权股价」，不得写「收盘价」）；股息率仍用总额法 = 该财年分红总额 ÷（当月名义价 × 当月股本），所以**末月与主卡同口径**。非送转股本变动（增发、回购注销、转股）会让历史月份偏离每股口径，偏差约等于股本变动比例（格力 2022-04 +0.87pp、长电 2023-01 −0.28pp），属定义性质而非 bug——要改成每股口径，等于让末月与主卡不一致，必须先改主卡定义。除权标记的 x 必须是月K线类目轴上的**月末日期**（用月中 ex-date 时 ECharts `convertToPixel` 返回 NaN，标记根本不渲染，2026-11 实测确认）。

### 最新完整财年 > TTM（滚动12个月）

TTM 会把不同财年的分红混在一起（如招行 2025/7 发 2024年报分红 + 2026/1 发 2025半年报分红），虚高股息率。
「最新完整财年」只取已公布年报的最新财年，半年报/季报分红属于未完成财年的中期分配，不单独构成完整财年。
新年报除权后分子自动切换。

### 三档税率

| 持股时长  | 税率  | 字段                            |
| ----- | --- | ----------------------------- |
| >1年   | 0%  | `dividend_yield_before_tax`   |
| 1月~1年 | 10% | `dividend_yield_after_tax`    |
| <1月   | 20% | `dividend_yield_after_tax_20` |

## 市赚率公式

```
基础市赚率 = PE_TTM / ROE_latest
修正市赚率 = N × PE_TTM / ROE_latest
PB-市赚率  = PB / ROE² × 100
```

> ROE 取最新年报 ROE（`ROE_latest`）；5 年 ROE 中位数一般仅供展示，**周期股 PB-市赚率例外**（用中位数抗单年失真，见下）。

N 因子 = 50% / 股利支付率，区间 [1.0, 2.0]（支付率 ≥50% → N=1.0，≤25% → N=2.0）。N 因子是市赚率发明人（雪球 ericwarn丁宁）自认的「经验公式」，非理论推导、无学术出处：50% 基准取贵州茅台多年平均股利支付率（「赚真钱」锚），支付率越低 N 越大。

估值区间：≤0.5 低估 / 0.5-1.0 合理偏低 / 1.0-3.0 合理 / >3.0 高估（阈值基于 PR 历史回测，见 docs/BACKTEST_REPORT_V3.md；市赚率用于避贵 + 选低估值——PR≤1 有超额，PR>3 跑输）

> 🚨 **选股判据（漏斗③、`screening.default_pr_evaluator`、`screener_pr.compute_pr`）一律用基础市赚率 PE_TTM/ROE**，与 V2 回测 L3（`docs/BACKTEST_METHODOLOGY.md` §3.5）同口径。**修正市赚率只做页面展示，不进判据**：N 是作者自认经验公式，低支付率会把 PR 抬到 2 倍（银行支付率 21–31% → N≈2），而 0.5/1.0/3.0 阈值是在基础 PR 上标定的。2026-11-15 事故：财务回补填入 payout 后全员切到修正 PR，银行/保险整批跌出 ③（平安 0.71→1.03），生产实际跑成了回测里最差的「剔金融」变体（超额 -38.68%）。改这一条前先看回测。

> 📌 **判定口径单一真相 = `pr_calculator.judge_pr(pe, roe_latest, roe_5y_median, is_cyclical) → (roe, pr, zone)`**。单股页（`pr.py` 的 `valuation_zone`、`pr_judge`/`roe_judge` 字段 + `site/js/calculator.js::judgePR`）与选股器（`screening.default_pr_evaluator`、`screener_pr.compute_pr`）全部经此函数；禁止任何调用点自行 `classify_valuation(...)` 选 PR 值，否则页面与选股器会再次给出不同结论（2026-11-15 前：页面 0.97「合理偏低」vs 选股器 1.03「合理」）。

> 🎯 **漏斗③ 只收「低估 / 合理偏低」是设计选择，不是数据缺陷**。2026-11 实测 164 只 >5% 股息候选里，③ 淘汰的 111 只中 77 只落「合理」（PR 1-3）、27 只「高估」、仅 7 只「无法判定」（财务快照缺失，已从 13% 覆盖率回补到 97%）。看到 ③ 数量远小于 ②，先看日志的「漏斗③ 淘汰原因」分桶，不要误以为是取数问题。

> ⚠️ 回测数字以 docs/BACKTEST_REPORT_V3.md 为准：页面回测提示、估值提示语、docstring 中的超额/跑输数字（+4%/~0%/-5.3% 等）散布在多处，改回测结论时**先更新报告，再同步所有引用点**。

> 修正市赚率仅适用 ROE 稳定+分红大方的价值股；周期/科技/成长股不适用（页面按周期>科技>成长优先级警示）。周期股 PB-市赚率用 5 年 ROE 中位数（抗单年失真）；漏斗③ 对周期股用 5 年 ROE 中位数算基础 PR。

> 📌 **行业关键词唯一来源 = `src/industry_policy.py`**（周期/科技/成长/防御/金融五组 + 两个分类函数）。`pr_calculator`、`sustainability_calculator` 只做 import，页面镜像是 `site/js/calculator.js` 顶部同名数组。词条一律是**子串匹配**，所以取「能唯一指认该行业的最短专名」：`'电力行业'` 而不是 `'电力'`（否则东财标签「电力设备-电源设备-太阳能」会被误判成防御性行业——#149 实测过的真实漂移，已修正 JS），`'化工'` 则要保留（东财「基础化工-化学制品」靠它）。新增关键词只改 industry_policy，双端一致性由 `scripts/policy_parity.check_industry_keywords` 兜底（`verify_js_vs_python.py` 每次运行都会打印 `[policy]` 段落）。

> 📌 **页面阈值唯一来源 = `site/js/policy.js` 的 `PR_ZONE_CUTOFFS`**，其数值镜像 `pr_calculator` 的 `PR_UNDERVALUED_MAX / PR_FAIR_LOW_MAX / PR_FAIR_MAX`（回测 `backtest_pr.PR_BUCKETS` 的边界与标签也由这三个常量派生）。页面文案不写死阈值：静态段落用 `<span data-policy="fair_low|undervalued|fair_max" data-format="int|one-decimal">` 占位由 policy.js 填充，脚本拼装文字用 `PRPolicy.zoneLegend()`。改阈值只需改 Python 常量 + policy.js 镜像，`check_pr_cutoffs` 会在 CI 里拦住漏改。

## 关键约束

### 🚫 数据铁律（最高优先级）

> **数据可靠性、真实性、准确性是本项目的生命线，高于一切功能与性能考虑。**

1. **严禁虚构数据** — 所有数据必须来自公开市场的真实信息。数据源不可用时**返回错误**，绝不编造、推算补缺或伪造示例值。
2. **数据必须有真实来源** — 每个数据字段都必须能追溯到一个具体数据源（mootdx / 腾讯 / 东方财富 / akshare / 新浪）。来源不可用即返回错误或明确标注缺失，不得用假数据填充。
3. **口径必须准确** — 金融指标的计算口径必须与公开定义一致（如真实股息率 = 完整财年分红总额 / 当前总市值）。文档声称的公式必须与代码实现逐字一致，发现不一致立即修正。
4. **数据必须可验证** — 新增/修改任何数据功能前，必须先验证数据的可获得性与真实准确性（见下「数据功能先验证」纪律）。涉及数据的代码必须配测试。

> 违反铁律的改动禁止合并。完整审查结论与风险清单见 `docs/DATA_RELIABILITY.md`。

### 数据功能先验证（开发纪律）

任何涉及数据的功能（新增数据源、新增字段、修改口径、改解析逻辑），**必须先验证数据的可获得性、真实性与准确性**：

1. **可获得性**：数据源接口可访问、返回结构符合预期、数据非空
2. **真实性**：抽样与公开信息核对（真实股票代码、真实财报数字），严禁用假数据测试
3. **准确性**：口径正确、与既有实现（Python/JS 双端）一致，字段映射实地验证过
4. **回归**：涉及数据的功能必须配测试；跑通 `python -m pytest tests/ -q`、`node --test site/js/calculator.test.js`、`python scripts/verify_js_vs_python.py` 三项后方可提交

验证不通过的功能不实现、不合并。

### 数据保留策略（选股器）

CSV/JSON 按 **90 天保留周期**自动清理；DB 快照表**不做时间裁剪**（两者互不相关，勿混改）：

- **CSV / 按日 JSON**：`scripts/export_screener_json.py` 每次运行时删除日期 < 保留期（默认 90 天，`--retention-days` 可覆盖）的过期 CSV 与孤儿按日 JSON；`history.json` 由全量重扫天然截断。解析失败的文件**跳过不删**（宁留不误删）
- **DB（screener.db）**：只做**孤儿行清理**——`ScreenerCache.prune_orphan_rows()` 删除 `dividend_snapshot`/`finance_snapshot`/`sustainability_snapshot` 中代码已不在 `stock_list` 的行；`stock_list` 为空时不动（防误判全市场退市）。**严禁按 `updated_at` 裁剪**：月频刷新断跑时旧数据会被当垃圾删掉，2026-11 事故即由此而来（股息/财务/可持续性三表在每日任务里被整表清空，漏斗② 180 只 → 0 只）。清理有两个入口：`scripts/merge_shard_rows.py`（CI 分片合并后、上传前——分片模式永不满足
  `fill_screener_data.py` 的「整轮成功」条件，清理只能挂在合并后）与 `scripts/fill_screener_data.py`
  （本地全量、未被 `--max-runtime-minutes` 截断时）。两者都要求 `stock_list` ≥ 1000 只
  （`MIN_UNIVERSE_FOR_PRUNE`），否则视为股票池写坏而跳过；每日任务不碰它没刷新的表
- **Release seed DB 历史备份**：每日 CI 覆盖上传 `screener.db` 到 Release `screener-seed` 前，先把当前版（上一日结果）另存为 `screener_YYYYMMDD.db` 历史版（命名取**下载日期**，与旧 DB 内容解耦，运行空档多久都不被立即剪掉），**保留最近 7 天**后自动清理过期备份。备份段独立容错——备份失败仅告警，绝不阻断当日 DB 上传。上传前有守卫：`scripts/check_seed_guard.py` 检查 `dividend_snapshot` < 1000 行则拒绝覆盖 seed（日频/月频共用，有单测）
- **git 历史即回滚层**：被清理的 CSV/JSON 仍可从 git log 取回，更早数据无需在线保留

### 数据刷新管线（分片）

全 A 股息刷新实测 **4.9s/只 ≈ 7.5 小时**，单 job 上限 6h 跑不完，所以刷新按代码分片并行（`.github/workflows/screener_monthly.yml`，显示名 Screener Data Refresh）：

`prepare`（判过期覆盖）→ `shard ×8`（`fill_screener_data.py --shard i/8 --export-rows shard.json`）→ `merge`（`merge_shard_rows.py` 合并 + `prefetch_sustainability.py` + 上传）

- **幂等可续跑**：刷过的行变成「新鲜」（30 天窗口），下一轮自动跳过。因此 6 小时一次的 cron 只在 `check_stale_coverage.py` 判断确有过期行时才启动分片，无缺口时空转约 2 分钟
- **分片互不重叠**：`shard_codes()` 按排序后的代码切 i/n 段，各片并集等于全量（有单测）
- **分片产物必须同名唯一**：各片导出 `shard-<n>.json`。若都叫 `shard.json`，`download-artifact` 的 `merge-multiple` 会把同名文件**拼接**成一个非法 JSON（run 43593390858 合并失败的根因）
- **财务步有聚合护栏**：取数全部返回 None 时不会抛异常也不写行（静默冻结）。因此「尝试 `effective_mass_failure_min(本轮代码数)` 只仍零写入」即判定数据源失效并中止，门槛 = `min(200, max(20, 代码数/2))`：固定 200 在分片里永不触发（每片 ~86 只财务代码，2026-11-14 run 43593390858 财务零写入、8 片全绿并照常上传）。`attempted` 只计真正发起的取数，所以批次几乎全被缓存跳过时护栏不会触发（此时旧行完好，符合预期）。正常覆盖率 ~97%，零写入只可能是数据源整体不可用
- **合并只覆盖导出的行**：某片失败/未处理的代码，主库原值保留（宁旧不缺）
- 单只取数超时（`call_with_timeout`，90s/120s）+ 单只异常跳过 + 连续 30 只失败中止本轮；三个 CLI 都 `os._exit` 硬退出（tdxpy 心跳线程会拖住进程）
- **退出码语义**：`fill_screener_data` 返回 2 = 被 `--max-runtime-minutes` 截断（部分完成，本片导出仍会执行）；workflow 把 2 映射为 `::warning::`，其它非零码仍走失败路径。合并步 `merge_shard_rows.py` 在合并后做孤儿行清理（`--skip-prune` 可跳过）
- **超时数值**：分片 job 180 分钟（股息步 110 + 财务步 60 + 上传），merge 120 分钟，prepare 15 分钟。早期方案写的「月频 150 分钟」在改分片后被上面这组具体值取代——单 job 150 分钟装不下 695 只×2 类取数，故按步骤拆
- **分片失败必须让 job 失败**：两个刷新步是 `continue-on-error`（截断不中断本片），副作用是 `needs.shard.result` 永远 success，merge 的告警条件形同虚设。因此分片末尾有 `Mark shard failed` 步，按 `steps.*.outcome` 在产物上传后补回失败语义

改 CSV/JSON 保留天数时，需同时更新 `export_screener_json.py` 的 `RETENTION_DAYS` 与此处文档。Release 备份的 7 天窗口在 `screener_daily.yml` 的 Upload 步（`cutoff=date -u -d '7 days ago'`），改窗口时同步改此处文档。

### A+H 股两地上市

对于中远海控、中国铝业等 A+H 股，**必须用总股本**（含全部股份），不能用流通股本：

- 腾讯行情 **Index 72**：仅A股股本/流通股本 ❌
- 腾讯行情 **Index 73**：总股本 ✓

### 财年判断

**仅 12 月报告期 = 完整财年年报**（单一实现 `sustainability.classify_fiscal_report`，Python 全链路经 `dividend_records.summarize_dividend_rows`，JS `parseDividendRecords` 同规则，双端一致，见 #37 M4）：其余月份报告期（3/4 月 Q1、6/9 月半年报）均为**中期分配**，不构成完整财年。季度分红监管扩散下，防御性收紧为 `month == 12`。

## 数据源架构（mootdx + 腾讯双引擎）

| 数据          | 主数据源                                          | 备用                               |
| ----------- | --------------------------------------------- | -------------------------------- |
| 实时价格 + 总股本  | 腾讯行情                                          | 新浪(价) → mootdx finance           |
| K线（走势图）     | 腾讯 fqkline                                    | 东财 push2his klt=103 → mootdx bars |
| PE_TTM / PB | 腾讯行情                                          | akshare 同花顺(EPS 推算) → 东方财富 push2 |
| 总股本         | 腾讯 Index 73                                   | mootdx finance                   |
| 除权除息 / 分红   | akshare fhps_detail_em（东财 datacenter，按报告期判财年） | akshare cninfo → mootdx xdxr     |
| ROE / 净利润   | 东方财富 datacenter（RPT_F10_FINANCE_MAINFINADATA）  | mootdx F10 → akshare 同花顺         |
| 行业分类        | mootdx F10 行业分析                               | 东方财富 datacenter                  |
| 可持续性字段      | 东方财富 datacenter                               | —                                |

所有数据源均为全球可用（mootdx 走二进制通达信协议，腾讯走 HTTP，akshare/同花顺经 2026-08 海外探针实测 10/10 可用、平均 2s）；可持续性模块与 JS 静态版刻意走东方财富 datacenter（浏览器可直连、与 Python 同源），其余主链路不依赖东方财富服务器。

> 注：GitHub Pages 静态版（`site/`）因浏览器无法访问 mootdx 二进制协议，改用东方财富 datacenter HTTP 接口（分红/财务/行业）作为浏览器数据源，全部接口已验证支持 CORS。计算逻辑与 Python 实现逐字段一致（见 `scripts/verify_js_vs_python.py`）。

## 开发坑位

- **GitHub Actions 出网限制（2026-11 实测）**：TDX 7709 端口被封（mootdx 全部 Timeout/Refused）；同花顺 `*.10jqka.com.cn` 对机房 IP 返回 403；akshare ≥1.20.41 移除了 `stock_financial_abstract_ths`。因此**能在 CI 上跑的财务源只剩东财 HTTP**——`pr._get_financial` 的降级链是 东财 → mootdx → 同花顺，不要把它改回 mootdx 优先（改了 CI 上财务快照会静默冻结，返回全 None 且不抛异常）
- mootdx 通达信协议在住宅/办公网络可用，但**在 GitHub Actions 上不可用**（注意别被「全球可用」这句话误导）；本地开发与 CI 的行为差异大多来自这里
- 东财 datacenter HTTP 接口对海外 IP 限流，CI（GitHub Actions 海外 runner）访问易超时：所有 requests 调用须带重试（3 次退避 + 30s 读取超时），参考 `scripts/verify_js_vs_python.py` 的 `_get()` 统一会话
- 腾讯 fqkline 接口全球可用，走势图数据源首选
- 半年报除权日通常在 9-12 月，要用除权日而非公告日推断财年
- A+H 股必须用总股本（腾讯 Index 73），不能用流通股本（Index 72）
- F10 财务分析表格第一列通常是 Q1 数据（非年报），解析时需过滤到仅 12-31
- mootdx 的 fenhong 值有浮点精度问题（如 2.1 显示为 2.09999...），计算时需 `round(, 4)`
- **本地 `data/screener.db` 不随生产更新**：生产库在 Release `screener-seed`（CI 每日上传），本地那份可能停在几个月前（财务/可持续性尤其）。任何「某只股票缺数据 / 被漏斗淘汰」的本地判断，先跑 `python scripts/sync_seed_db.py`（下载 + 校验 + 原子替换），否则结论可能是本地库滞后造成的假象（2026-11 实际踩过）
- **页面脚本必须带版本戳**：`site/index.html` 里 4 个本地 `<script src="js/*.js?v=BUILD">`，改任何 `js/*.js` 都要同步升 `BUILD`。GitHub Pages 的 JS 有缓存，HTML 已更新而 JS 是旧缓存时，页面会报 `xxx is not a function`（2026-11-17 前瞻卡曾因此永远停在「正在获取机构盈利预测…」）

## 开发规范

本项目遵循以下 AI 编码原则：

1. **先思考，再编码** — 明确任务目标，理清思路再动手
2. **极简优先** — 用最简逻辑实现功能，不堆砌无用代码
3. **精准限定修改范围** — 只改动需求相关代码
4. **目标导向执行** — 先界定验收标准，分步落地开发

## 参考文档

- `README.md` — 完整使用文档（给人看，仓库根，GitHub 默认展示）
- `docs/DATASOURCE_README.md` — 数据源架构详细说明
- `docs/SUSTAINABILITY.md` — 股息可持续性分析：判断模型（分层级联）、数据来源、阈值与代码结构
- `docs/DATA_RELIABILITY.md` — **数据铁律与可靠性审查报告**：数据源清单、口径、风险分级、修复路线图

## Agent skills

### Issue tracker

Issues 存放在 GitHub Issues（`flyshub/dividend-calculator`），通过 `gh` CLI 操作。详见 `docs/agents/issue-tracker.md`。

### Triage labels

使用标准五标签体系：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

单一上下文布局 — 一个 `CONTEXT.md` + `docs/adr/` 在项目根目录。详见 `docs/agents/domain.md`。
