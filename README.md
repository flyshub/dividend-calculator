# 真实股息率 & 市赚率计算器（A 股）

> 📈 在线查询（GitHub Pages，纯前端，无需安装）：**https://flyshub.github.io/dividend-calculator/**

A 股估值参考工具，提供 **真实股息率** 与 **市赚率（PR）** 两项指标。采用「总额法」计算股息率，避免转送股带来的每股口径偏差。

> 🚫 **数据铁律**：数据可靠性、真实性、准确性是本项目生命线。所有数据来自公开市场真实信息，**严禁虚构数据**；数据源不可用时返回错误，绝不编造。完整条款与风险清单见 `docs/DATA_RELIABILITY.md`。

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
- **股价与股息率走势图**：近三年双轴图，含高股息区间与除权日标记
- **A+H 股正确处理**：使用总股本（腾讯 Index 73）而非流通股本
- **支持股票代码或名称查询**

## 🔍 在线查询（GitHub Pages）

**https://flyshub.github.io/dividend-calculator/**

输入 6 位股票代码（如 `600900`）或股票名称（如 `长江电力`）即可查询，全部计算在浏览器本地完成，无后端服务。

## 🐍 Python 使用

### 环境要求

- Python 3.9+
- 依赖：`mootdx`、`pandas`、`click`、`requests`

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
# 打开 http://localhost:8000
```

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

### 市赚率（PR）

```
基础版  = PE_TTM / ROE
修正版  = N × PE_TTM / ROE
PB 版   = PB / (ROE/100)² / 100
N 因子  = 50% / 股利支付率，夹在 [1.0, 2.0]
```

估值区间：≤0.5 低估 / 0.5-0.7 合理偏低 / 0.7-1.0 合理 / >1.0 高估。

### 财年推断

除权除息日 3-8 月 → 上年度年报；9-12 月 → 当年度中报；1-2 月 → 上年度中报。半年报与年报合并计入同一财年。

## 🗂️ 项目结构

```
├── src/                  # Python 实现（CLI + 本地 Web）
│   ├── main.py           # CLI 入口
│   ├── web.py            # 本地 Web 服务
│   ├── dividend.py       # 股息率计算
│   ├── pr.py             # 市赚率计算
│   └── datasource/       # mootdx/腾讯/新浪 多数据源降级
├── site/                 # GitHub Pages 纯前端实现
│   ├── index.html        # 页面
│   └── js/               # calculator.js（纯函数）/ datasources.js / app.js
├── scripts/              # JS 与 Python 一致性验证脚本
├── tests/                # Python 单元测试
└── .github/workflows/    # Pages 自动部署
```

## ✅ 质量保证

- **105+ Python 单元测试**：财年推断、市赚率公式、数据源注入等
- **45 JS 单元测试**：对齐 Python 测试用例
- **跨语言一致性验证**：`scripts/verify_js_vs_python.py` 让 JS 与 Python 消费**相同原始数据**逐字段对比（10 只覆盖周期/银行/消费/亏损的股票全一致）
- **真实浏览器测试**：Playwright 验证代码/名称查询、图表、边界场景

## 📄 相关文档

- [Python 数据源说明](dividend-calculator/DATASOURCE_README.md)
- [Pages 站点说明](dividend-calculator/site/README.md)

## ⚠️ 免责声明

本工具完全使用公开市场真实数据，结果依赖数据源实时可用性，仅供投资研究参考，不构成投资建议。
