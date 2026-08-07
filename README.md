# 真实股息率 & 市赚率计算器（A 股）

> 📈 在线查询（GitHub Pages，纯前端，无需安装）：**https://flyshub.github.io/dividend-calculator/**

A 股估值参考工具，提供 **真实股息率** 与 **市赚率（PR）** 两项指标。采用「总额法」计算股息率，避免转送股带来的每股口径偏差。

> 🚫 **数据铁律**：数据可靠性、真实性、准确性是本项目生命线，高于一切功能与性能考虑。
> 1. **严禁虚构数据** — 所有数据来自公开市场真实信息，数据源不可用时返回错误，绝不编造。
> 2. **数据必须有真实来源** — 每个字段可追溯到一个具体数据源（mootdx / 腾讯 / 东方财富 / akshare / 新浪）。
> 3. **口径必须准确** — 金融指标计算口径与公开定义一致，文档公式与实现逐字一致。
> 4. **数据必须可验证** — 任何数据功能先验证可获得性与真实性，涉及数据的代码必须配测试。
>
> 完整审查结论与风险清单见 `docs/DATA_RELIABILITY.md`。

本项目包含两种形态，共享同一套计算口径：

| 形态 | 说明 | 使用方式 |
|------|------|---------|
| **GitHub Pages 静态站**（`site/`） | 纯前端，浏览器直连腾讯/东财数据源，无需任何本地环境 | 直接访问上方链接 |
| **Python CLI / 本地 Web**（`src/`） | mootdx + 腾讯 + akshare 三引擎，全球可用 | `pip install -r requirements.txt` 后运行 |

---

## ✨ 功能

- **真实股息率**：最近完整财年现金分红总额 ÷ 当前总市值，按「总额法」计算
- **三档税率口径**：持股 >1 年免税 / 1 月 ~ 1 年扣 10% / <1 月扣 20%
- **分红明细**：报告期 + 每 10 股派息金额
- **市赚率（PR）**：基础版 / 修正版 / PB 版三套公式 + 估值四档区间
- **N 因子**：基于股利支付率的修正系数（50%/支付率，夹在 [1.0, 2.0]）
- **分红可持续性评估**：股利支付率 / 利润趋势 / 特别分红 / 连续分红年数 / 未分配利润覆盖 / 净现比 六维判据 + 银行专项模块
- **股价与股息率走势图**：近三年双轴图，含高股息区间与除权日标记
- **A+H 股正确处理**：使用总股本（腾讯 Index 73）而非流通股本
- **支持股票代码或名称查询**

## 🔍 在线查询（GitHub Pages）

**https://flyshub.github.io/dividend-calculator/**

输入 6 位股票代码（如 `600900`）或股票名称（如 `长江电力`）即可查询，全部计算在浏览器本地完成，无后端服务。

## 🐍 Python 使用

### 环境要求

- Python 3.9+
- 依赖：`mootdx>=0.11.0`、`akshare>=1.12.0`、`pandas>=2.0.0`、`click>=8.0.0`、`requests>=2.22.0`

### 安装

```bash
git clone https://github.com/flyshub/dividend-calculator.git
cd dividend-calculator/dividend-calculator
pip install -r requirements.txt
```

### CLI 查询

```bash
# 用股息率工具
python -m src.main 600987

# 或用市赚率工具
python calc_pr.py 600987
```

### 本地 Web 服务

```bash
python -m src.web
# 打开 http://127.0.0.1:8000
```

支持 6 位股票代码或精确股票名称，同时展示股息率和市赚率。

### 作为库使用

```python
# 股息率
from src.dividend import calculate_true_dividend_yield
result = calculate_true_dividend_yield("600987")
print(f"含税股息率: {result.dividend_yield_before_tax:.2f}%")

# 市赚率
from src.pr import calculate_pr
result = calculate_pr("600987")
print(f"市赚率: {result.pr_basic:.3f}")

# 一站式
from src.analysis import run_stock_analysis
analysis = run_stock_analysis("600987")
print(f"股息率: {analysis.dividend_yield:.2f}%, 市赚率: {analysis.pr_result.pr_basic:.3f}")
```

### Web API 端点

| 端点 | 参数 | 功能 |
|------|------|------|
| `GET /api/calculate?stock=600987` | 股票代码或名称 | 股息率计算 |
| `GET /api/pr?stock=600987` | 股票代码或名称 | 市赚率计算 |
| `GET /api/historical-data?stock=600987` | 股票代码 | 走势图数据 |
| `GET /health` | 无 | 健康检查 |

### 运行测试

```bash
pip install pytest
python -m pytest tests/ -q
```

## 📐 核心公式

### 真实股息率

```
真实股息率 = 最近完整财年现金分红总额 / 当前总市值
```

按财年分组 → 选含年报的最近财年 → 每股派息 = 每 10 股合计 / 10 → 总额 = 每股 × 总股本。

**为什么用总额法？**

| 计算方式 | 问题 |
|---------|------|
| 每股分红 / 股价 | 转送股会导致每股分红被动变化 |
| 过去12个月滚动分红 | 跨财年分红混合，不准确 |
| **总额法（最新完整财年）** | ✓ 分子分母同一时间截面，不受转送股/除权除息影响 |

### 市赚率（PR）

市赚率是基于巴菲特"40美分买1美元"理念的简化估值指标：

```
基础市赚率  = PE / ROE / 100
修正市赚率  = N × PE / ROE / 100
PB-市赚率   = PB / ROE² / 100
```

**修正系数 N 规则**：
| 股利支付率 | N 值 |
|-----------|------|
| ≥ 50% | 1.00 |
| 25% ~ 50% | 0.5 / 支付率（如40%→1.25） |
| ≤ 25% | 2.00 |

**估值四档**：≤0.5 低估 / 0.5–0.7 合理偏低 / 0.7–1.0 合理 / >1.0 高估

### 财年推断

分红记录按**报告期（REPORT_DATE）**判定是否年报，与披露时间无关：

- **报告期 12-31 → 年报**（3 月除权的 Q1 报告期分红归中期分配）
- 报告期 06-30 / 09-30 / 03-31 → 中期分配（中报 / 三季报 / 一季报）
- 半年报与年报合并计入同一财年

mootdx xdxr 数据源无报告期字段，仅能按**除权除息日**近似推断：3-8 月除权 → 上年度年报；9-12 月 → 当年度中报；1-2 月 → 上年度中报。该近似在常规情形（年报除权 6-7 月、中报除权 9-11 月）与报告期口径一致；8 月除权的中报等边界情形存在误判可能，取数失败时会自动降级到含报告期字段的东财数据源。

### A+H 股两地上市

对于中远海控、中国铝业等 A+H 股，**必须用总股本**（含全部股份），不能用流通股本：
- 腾讯行情 **Index 72**：仅A股股本/流通股本 ❌
- 腾讯行情 **Index 73**：总股本 ✓

## 🔌 数据源架构

项目使用 **mootdx + 腾讯双引擎**，全部数据源全球可用：

| 数据 | 主数据源 | 备用 |
|------|---------|------|
| 实时价格 + K线 | mootdx（通达信协议） | 腾讯 fqkline |
| PE_TTM / PB | 腾讯行情 | 东方财富 push2 |
| 总股本 | 腾讯 Index 73 / mootdx finance | — |
| 除权除息 / 分红 | mootdx xdxr | 东方财富 datacenter |
| ROE / 净利润 | mootdx F10 财务分析 | 东方财富 push2 |
| 行业分类 | mootdx F10 行业分析 | 东方财富 datacenter |

### 数据源特性

| 数据源 | 协议 | 需要Token | 全球可用 | 说明 |
|--------|------|----------|---------|------|
| mootdx | 通达信二进制 | 否 | ✅ | 行情/K线/除权除息/F10财务 |
| 腾讯行情 | HTTP | 否 | ✅ | PE/PB/总股本/价格 |
| 新浪行情 | HTTP | 否 | ✅ | 价格备用 |
| 东方财富 | HTTP | 否 | ⚠️ 偶发不稳定 | 分红/财务/行业备用 |

详细说明请见 [DATASOURCE_README.md](docs/DATASOURCE_README.md)。

## 🗂️ 项目结构

```
dividend-calculator/
├── src/                  # Python 实现（CLI + 本地 Web）
│   ├── main.py           # CLI 入口
│   ├── web.py            # 本地 Web 服务
│   ├── api.py            # 数据获取 + 多源降级
│   ├── dividend.py       # 股息率计算
│   ├── pr.py             # 市赚率计算
│   ├── sustainability.py # 分红可持续性评估
│   ├── eastmoney_fetcher.py  # 东财 datacenter 共享取数
│   └── datasource/       # mootdx/腾讯/新浪 多数据源降级
├── site/                 # GitHub Pages 纯前端实现
│   ├── index.html        # 页面
│   └── js/               # calculator.js（纯函数）/ datasources.js / app.js
├── scripts/              # JS 与 Python 一致性验证脚本
├── tests/                # Python 单元测试
├── calc_pr.py            # 市赚率 CLI
└── .github/workflows/    # Pages 自动部署
```

## ✅ 质量保证

- **220+ Python 单元测试**：财年推断、市赚率公式、可持续性评分、数据源注入等
- **70+ JS 单元测试**：对齐 Python 测试用例
- **跨语言一致性验证**：`scripts/verify_js_vs_python.py` 让 JS 与 Python 消费**相同原始数据**逐字段对比（含可持续性全部字段全一致）
- **真实浏览器测试**：Playwright 验证代码/名称查询、图表、边界场景

## ❓ 常见问题

### 股息率相关

**Q: 为什么不使用每股分红/股价？**
A: 转送股会导致每股分红被动变化，总额法可规避此问题。

**Q: 为什么取完整财年而不是过去12个月？**
A: 过去12个月可能包含两个不同财年的分红，虚高股息率。

**Q: 扣税10%是怎么来的？**
A: A股现金分红个人所得税率通常为10%（持股1月~1年），持股满1年免征。

### 市赚率相关

**Q: 基础、修正、PB 三个市赚率有什么区别？**
A: 基础版 = PE/ROE；修正版引入股利支付率修正（适用ROE稳定价值股）；PB版通过 PB/ROE² 计算（适用周期股）。

**Q: 亏损股如何处理？**
A: 净利润 ≤0 的股票被标记为亏损股，市赚率不适用。

### 网络相关

**Q: 为什么启动时有 mootdx WARNING 日志？**
A: mootdx 需要连接通达信服务器（中国大陆网络）。在受限网络环境下，mootdx 不可用属于正常现象，系统会自动降级到 akshare + 腾讯行情，不影响功能。

## 📄 相关文档

- [数据源说明](docs/DATASOURCE_README.md)
- [数据可靠性审查报告](docs/DATA_RELIABILITY.md)
- [GitHub Pages 站点说明](dividend-calculator/site/README.md)

## ⚠️ 免责声明

本工具完全使用公开市场真实数据，结果依赖数据源实时可用性，仅供投资研究参考，不构成投资建议。

## 📝 许可证

MIT License
