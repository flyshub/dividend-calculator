# 真实股息率计算工具

基于 Python 的 A 股真实股息率 + 市赚率计算工具。技术栈：Python 3.9+、mootdx（通达信协议）、腾讯行情、ECharts 前端。

## 常用命令

```bash
# 股息率计算
cd dividend-calculator && python -m src.main 600987

# 市赚率计算
cd dividend-calculator && python calc_pr.py 600900

# 启动 Web 服务
cd dividend-calculator && python -m src.web

# 运行全部测试
cd dividend-calculator && python -m pytest -v

# 运行单个测试
cd dividend-calculator && python -m pytest tests/test_dividend.py -v
```

## 核心架构决策

### 总额法 > 每股法

```
真实股息率 = 最近完整财年现金分红总额 / 当前总市值 × 100%
```

转送股会导致「每股分红/股价」被动变化，总额法分子分母来自同一截面，不受影响。

### 最新完整财年 > TTM（滚动12个月）

TTM 会把不同财年的分红混在一起（如招行 2025/7 发 2024年报分红 + 2026/1 发 2025半年报分红），虚高股息率。
「最新完整财年」只取已公布年报的最新财年，半年报/季报分红属于未完成财年的中期分配，不单独构成完整财年。
新年报除权后分子自动切换。

### 三档税率

| 持股时长 | 税率 | 字段 |
|---------|------|------|
| >1年 | 0% | `dividend_yield_before_tax` |
| 1月~1年 | 10% | `dividend_yield_after_tax` |
| <1月 | 20% | `dividend_yield_after_tax_20` |

## 市赚率公式

```
基础市赚率 = PE_TTM / ROE_latest
修正市赚率 = N × PE_TTM / ROE_latest
PB-市赚率  = PB / ROE² × 100
```

> ROE 取最新年报 ROE（`ROE_latest`）；5 年 ROE 中位数仅供展示，不参与计算（见 `docs/DATA_RELIABILITY.md` 审查发现 #1）。

N 因子 = 50% / 股利支付率，区间 [1.0, 2.0]（支付率 ≥50% → N=1.0，≤25% → N=2.0）

估值区间：≤0.5 低估 / 0.5-1.0 合理偏低 / 1.0-3.0 合理 / >3.0 高估（阈值基于 PR 历史回测，见 docs/BACKTEST_REPORT.md；市赚率用于避贵而非抄底）

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

### A+H 股两地上市

对于中远海控、中国铝业等 A+H 股，**必须用总股本**（含全部股份），不能用流通股本：
- 腾讯行情 **Index 72**：仅A股股本/流通股本 ❌
- 腾讯行情 **Index 73**：总股本 ✓

### 财年判断

半年报日期（9-12月除权）≠ 年报，不能误判为完整财年。只有 3-8月除权的才是年报。

## 数据源架构（mootdx + 腾讯双引擎）

| 数据 | 主数据源 | 备用 |
|------|---------|------|
| 实时价格 + K线 | mootdx（通达信协议，全球可用） | 腾讯 fqkline |
| PE_TTM / PB | 腾讯行情 | 东方财富 push2 |
| 总股本 | 腾讯 Index 73 / mootdx finance | — |
| 除权除息 / 分红 | mootdx xdxr | — |
| ROE / 净利润 | mootdx F10 财务分析 | 东方财富 push2 |
| 行业分类 | mootdx F10 行业分析 | 东方财富 push2 |

所有数据源均为全球可用（mootdx 走二进制通达信协议，腾讯走 HTTP），不再依赖东方财富服务器。

> 注：GitHub Pages 静态版（`site/`）因浏览器无法访问 mootdx 二进制协议，改用东方财富 datacenter HTTP 接口（分红/财务/行业）作为浏览器数据源，全部接口已验证支持 CORS。计算逻辑与 Python 实现逐字段一致（见 `scripts/verify_js_vs_python.py`）。

## 开发坑位

- mootdx 通达信协议全球可用，不依赖东方财富（解决海外 IP 限流问题）
- 东财 datacenter HTTP 接口对海外 IP 限流，CI（GitHub Actions 海外 runner）访问易超时：所有 requests 调用须带重试（3 次退避 + 30s 读取超时），参考 `scripts/verify_js_vs_python.py` 的 `_get()` 统一会话
- 腾讯 fqkline 接口全球可用，走势图数据源首选
- 半年报除权日通常在 9-12 月，要用除权日而非公告日推断财年
- A+H 股必须用总股本（腾讯 Index 73），不能用流通股本（Index 72）
- F10 财务分析表格第一列通常是 Q1 数据（非年报），解析时需过滤到仅 12-31
- mootdx 的 fenhong 值有浮点精度问题（如 2.1 显示为 2.09999...），计算时需 `round(, 4)`

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
