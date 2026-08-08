# 真实股息率 & 市赚率计算器（A 股）

**A 股真实股息率 + 市赚率（Graham Number）计算工具**：用「总额法」算真实股息率，用「PE/ROE」算市赚率。Python CLI / 本地 Web / 纯前端 GitHub Pages 三形态，共享同一套计算口径与数据铁律。

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![License: GPL-3.0](https://img.shields.io/badge/License-GPLv3-blue.svg)](#-许可证)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-online-brightgreen)](https://flyshub.github.io/dividend-calculator/)
[![Tests](https://img.shields.io/badge/Tests-242%20Python%20%2B%2078%20JS-green)](dividend-calculator/tests/)

> 📈 **在线体验**（纯前端，无需安装）：<https://flyshub.github.io/dividend-calculator/>
>
> 输入 6 位股票代码（如 `600900`）或股票名称（如 `长江电力`），浏览器本地完成全部计算。

---

## ✨ 功能特性

- **真实股息率**：最近完整财年现金分红总额 ÷ 当前总市值，三档税率口径（>1 年免税 / 1 月~1 年扣 10% / <1 月扣 20%）
- **市赚率（PR）**：基础版 / 修正版 / PB 版三套公式 + 估值四档区间 + N 因子修正
- **分红可持续性评估**：股利支付率 / 利润趋势 / 特别分红 / 连续分红年数 / 未分配利润覆盖 / 净现比 六维判据 + 银行专项模块
- **股价与股息率走势图**：近三年双轴图，含高股息区间与除权日标记
- **A+H 股正确处理**：使用总股本（腾讯 Index 73）而非流通股本
- **多引擎降级**：mootdx（通达信协议）+ 腾讯 + 东方财富，全球可用
- **双端一致性**：Python 与 JS 实现逐字段对齐，脚本交叉验证

## 🚀 快速开始

### 在线使用（零安装）

直接打开 **<https://flyshub.github.io/dividend-calculator/>**，输入股票代码或名称即可。

### 本地 Python

```bash
git clone https://github.com/flyshub/dividend-calculator.git
cd dividend-calculator/dividend-calculator
pip install -r requirements.txt
```

计算贵州茅台的股息率：

```bash
python -m src.main 600519
```

计算长江电力的市赚率：

```bash
python calc_pr.py 600900
```

### 启动本地 Web 服务

```bash
python -m src.web
# 打开 http://127.0.0.1:8000
```

## 📖 使用方法

### Web API 端点

| 端点 | 参数 | 功能 |
|------|------|------|
| `GET /api/calculate?stock=600987` | 股票代码或名称 | 股息率计算 |
| `GET /api/pr?stock=600987` | 股票代码或名称 | 市赚率计算 |
| `GET /api/historical-data?stock=600987` | 股票代码 | 走势图数据 |
| `GET /health` | 无 | 健康检查 |

### 作为库使用

```python
from src.dividend import calculate_true_dividend_yield
result = calculate_true_dividend_yield("600987")
print(f"含税股息率: {result.dividend_yield_before_tax:.2f}%")

from src.pr import calculate_pr
result = calculate_pr("600987")
print(f"市赚率: {result.pr_basic:.3f}")
```

### 运行测试

```bash
python -m pytest tests/ -q      # Python 221 个测试
node --test site/js/            # JS 70 个测试
python scripts/verify_js_vs_python.py   # 双端一致性验证
```

## 🧮 计算口径

### 为什么用「总额法」而不是「每股法」？

| 计算方式 | 问题 |
|---------|------|
| 每股分红 / 股价 | 转送股会导致每股分红被动变化 |
| 过去 12 个月滚动分红 | 跨财年分红混合，虚高 |
| **总额法（最新完整财年）** ✓ | 分子分母同一时间截面，不受转送股/除权除息影响 |

```
真实股息率 = 最近完整财年现金分红总额 / 当前总市值
```

### 为什么取「最新完整财年」而不是 TTM？

TTM 会把不同财年的分红混在一起（如招行 2025/7 发 2024 年报分红 + 2026/1 发 2025 半年报分红），虚高股息率。「最新完整财年」只取已公布年报的最新财年，新年报除权后分子自动切换。

### 三档税率

| 持股时长 | 税率 | 字段 |
|---------|------|------|
| >1 年 | 0% | `dividend_yield_before_tax` |
| 1 月 ~ 1 年 | 10% | `dividend_yield_after_tax` |
| <1 月 | 20% | `dividend_yield_after_tax_20` |

### 财年判断（报告期口径）

分红记录按**报告期（REPORT_DATE）**判定是否年报，与披露时间无关：

- 报告期 **12-31** → **年报**；报告期 03-31 / 06-30 / 09-30 → 中期分配
- 半年报与年报合并计入同一财年

> mootdx xdxr 数据源无报告期字段，仅能按除权除息日近似推断（3-8 月除权 → 上年度年报；9-12 月 → 当年度中报）。该近似在常规情形（年报除权 6-7 月、中报除权 9-11 月）与报告期口径一致；边界情形自动降级到含报告期字段的东财数据源。

### 市赚率公式

```
基础市赚率  = PE / ROE / 100
修正市赚率  = N × PE / ROE / 100
PB-市赚率   = PB / ROE² / 100
```

> 周期股（煤炭/钢铁/有色/化工/航运/证券/保险等行业）PB-市赚率使用 **5 年 ROE 中位数**而非最新年报 ROE——周期股单年 ROE 受景气波动失真，用多年中位数平滑（对齐市赚率原始定义的「多年 ROE」思路，中位数抗单年极端值）。
>
> 修正市赚率（N 因子版）仅适用于 ROE 稳定且分红大方的价值股。周期股（景气年分红失真）、科技股（回购代替分红）、成长股（高成长需留存利润，分红率低导致 N 因子失真）均不适用，页面会给出对应警示。

N 因子 = 50% / 股利支付率，区间 [1.0, 2.0]（支付率 ≥50% → N=1.0，≤25% → N=2.0）。

估值区间：≤0.5 低估 / 0.5-1.0 合理偏低 / 1.0-3.0 合理 / >3.0 高估

> 阈值基于 PR 历史回测（2016-2024 沪深300，[回测报告](docs/BACKTEST_REPORT.md)）：超额集中在 PR 1~3，PR>3 显著跑输（>14pct），PR<1 无超额。市赚率用于「避贵」而非「抄底」。

### A+H 股两地上市

必须用**总股本**（含全部股份），不能用流通股本：腾讯行情 Index 72 仅 A 股股本 ❌；**Index 73 总股本** ✓。

## 🏗️ 项目结构

```
dividend-calculator/
├── src/                  # Python 实现（CLI + 本地 Web）
│   ├── main.py           # CLI 入口
│   ├── web.py            # 本地 Web 服务
│   ├── dividend.py       # 股息率计算
│   ├── pr.py             # 市赚率计算
│   ├── sustainability.py # 分红可持续性评估
│   └── datasource/       # mootdx/腾讯/东财 多数据源降级
├── site/                 # GitHub Pages 纯前端（browser 直连数据源）
├── scripts/              # JS 与 Python 一致性验证
├── tests/                # Python 单元测试
└── .github/workflows/    # CI + Pages 自动部署
```

## 🔌 数据源架构

| 数据 | 主数据源 | 备用 |
|------|---------|------|
| 实时价格 + K线 | mootdx（通达信协议） | 腾讯 fqkline |
| PE_TTM / PB | 腾讯行情 | 东方财富 push2 |
| 总股本 | 腾讯 Index 73 | mootdx finance |
| 除权除息 / 分红 | mootdx xdxr | 东方财富 datacenter |
| ROE / 净利润 | mootdx F10 财务分析 | 东方财富 push2 |
| 行业分类 | mootdx F10 行业分析 | 东方财富 datacenter |

全部数据源全球可用（mootdx 走二进制通达信协议，腾讯走 HTTP）。详见 [数据源说明](docs/DATASOURCE_README.md)。

## 🚫 数据铁律

数据可靠性、真实性、准确性是本项目生命线，高于一切功能与性能考虑：

1. **严禁虚构数据** — 所有数据必须来自公开市场的真实信息，数据源不可用时**返回错误**，绝不编造、推算补缺
2. **数据必须有真实来源** — 每个字段都能追溯到一个具体数据源（mootdx / 腾讯 / 东方财富 / akshare / 新浪）
3. **口径必须准确** — 金融指标的计算口径与公开定义一致，文档公式与实现逐字一致
4. **数据必须可验证** — 任何数据功能先验证可获得性与真实性，涉及数据的代码必须配测试

## ✅ 质量保证

- **221 Python 单元测试 + 70 JS 单元测试**：财年推断、市赚率公式、可持续性评分、数据源注入、双端对齐
- **跨语言一致性验证**：`scripts/verify_js_vs_python.py` 让 JS 与 Python 消费**相同原始数据**逐字段对比，含可持续性全部字段
- **CI 自动运行**：GitHub Actions 每次提交跑全部测试（见 `.github/workflows/ci.yml`）

## ❓ 常见问题

**Q: 为什么不使用每股分红/股价？**
A: 转送股会导致每股分红被动变化，总额法可规避此问题。

**Q: 为什么取完整财年而不是过去 12 个月？**
A: 过去 12 个月可能包含两个不同财年的分红，虚高股息率。

**Q: 基础、修正、PB 三个市赚率有什么区别？**
A: 基础版 = PE/ROE；修正版引入股利支付率修正（适用 ROE 稳定价值股）；PB 版通过 PB/ROE² 计算（适用周期股）。

**Q: 为什么启动时有 mootdx WARNING 日志？**
A: mootdx 需要连接通达信服务器。在受限网络下 mootdx 不可用属正常现象，系统自动降级到 akshare + 腾讯行情，不影响功能。

**Q: 亏损股如何处理？**
A: 净利润 ≤0 的股票被标记为亏损股，市赚率不适用。

## 🤝 参与贡献

欢迎提交 [Issue](https://github.com/flyshub/dividend-calculator/issues) 报告问题或建议，也接受 Pull Request。

> 涉及数据的改动必须遵守 [数据铁律](#-数据铁律)：先验证数据可获得性与准确性，配测试，跑通三项回归（pytest / node --test / verify_js_vs_python）后方可合并。

## 📄 相关文档

- [数据源说明](docs/DATASOURCE_README.md)
- [股息可持续性分析](docs/SUSTAINABILITY.md)
- [GitHub Pages 站点说明](dividend-calculator/site/README.md)

## ⚠️ 免责声明

本工具完全使用公开市场真实数据，结果依赖数据源实时可用性，仅供投资研究参考，**不构成投资建议**。

## 📝 许可证

[GPL-3.0](https://github.com/flyshub/dividend-calculator/blob/main/LICENSE) © flyshub
