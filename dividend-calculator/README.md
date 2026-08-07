# 真实股息率 & 市赚率计算工具

基于 Python 的 A 股估值参考工具，支持：
- **真实股息率**：总额法计算，避免转送股带来的偏差
- **市赚率（PR）**：基于巴菲特估值理念的简化估值指标

数据源：**mootdx（通达信协议）+ 腾讯行情 + akshare 三引擎**，自适应降级。

## 核心原则

### 🚫 数据铁律（最高优先级）

> 数据可靠性、真实性、准确性是本项目生命线，高于一切功能与性能考虑。

1. **严禁虚构数据** — 所有数据来自公开市场真实信息，数据源不可用时返回错误，绝不编造。
2. **数据必须有真实来源** — 每个字段可追溯到一个具体数据源（mootdx / 腾讯 / 东方财富 / akshare / 新浪）。
3. **口径必须准确** — 金融指标计算口径与公开定义一致，文档公式与实现逐字一致。
4. **数据必须可验证** — 任何数据功能先验证可获得性与真实性，涉及数据的代码必须配测试。

> 完整审查结论与风险清单见 `docs/DATA_RELIABILITY.md`。

### 重要：A+H股两地上市公司处理

对于同时在A股和H股上市的公司（如中远海控、中国铝业等）：
- **必须使用总股本**（包含A股+H股等所有股份），而非仅A股股本
- **必须使用总市值**（股价 × 总股本），而非流通市值
- 腾讯行情接口字段说明：
  - **Index 72**：仅A股股本 / 流通股本
  - **Index 73**：总股本（包含所有股份）✓

## 核心公式

### 真实股息率

```
真实股息率 = 最近完整财年现金分红总额 / 当前总市值
```

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

### 为什么用总额法？

| 计算方式 | 问题 |
|---------|------|
| 每股分红 / 股价 | 转送股会导致每股分红被动变化 |
| 过去12个月滚动分红 | 跨财年分红混合，不准确 |
| **总额法（最新完整财年）** | ✓ 分子分母同一时间截面，不受转送股/除权除息影响 |

## 安装

### 环境要求

- Python 3.9+

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖列表：
- `mootdx>=0.11.0` — 通达信协议数据源（行情/K线/除权除息/财务/行业）
- `akshare>=1.12.0` — 备用数据源（同花顺财报/分红，mootdx 不可用时自动降级）
- `pandas>=2.0.0` — 数据处理
- `click>=8.0.0` — 命令行界面
- `requests>=2.22.0` — HTTP 请求（腾讯、新浪行情）

### 使用 pip 安装

```bash
pip install .
pip install ".[dev]"    # 含测试框架
```

## 使用方法

### 命令行

```bash
# 股息率计算
python -m src.main 600987    # 航民股份

# 市赚率计算
python calc_pr.py 600900     # 长江电力
```

### Web 页面

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

## 数据源架构

项目使用 **mootdx + 腾讯双引擎**，全部数据源全球可用：

| 数据 | 主数据源 | 备用 |
|------|---------|------|
| 实时价格 + K线 | mootdx（通达信协议） | 腾讯 fqkline |
| PE_TTM / PB | 腾讯行情 | 东方财富 push2 |
| 总股本 | 腾讯 Index 73 / mootdx finance | — |
| 除权除息 / 分红 | mootdx xdxr | — |
| ROE / 净利润 | mootdx F10 财务分析 | 东方财富 push2 |
| 行业分类 | mootdx F10 行业分析 | 东方财富 push2 |

### 数据源特性

| 数据源 | 协议 | 需要Token | 全球可用 | 说明 |
|--------|------|----------|---------|------|
| mootdx | 通达信二进制 | 否 | ✅ | 行情/K线/除权除息/F10财务 |
| 腾讯行情 | HTTP | 否 | ✅ | PE/PB/总股本/价格 |
| 新浪行情 | HTTP | 否 | ✅ | 价格备用 |
| 东方财富 push2 | HTTP | 否 | ⚠️ 偶发不稳定 | PE/PB/行业备用 |

详细说明请见 [DATASOURCE_README.md](DATASOURCE_README.md)。

## 项目结构

```
dividend-calculator/
├── src/
│   ├── __init__.py              # 包入口
│   ├── api.py                   # 数据获取 + 多源降级
│   ├── analysis.py              # 一站式计算（股息率+市赚率）
│   ├── dividend.py              # 股息率核心计算
│   ├── pr.py                    # 市赚率计算
│   ├── tencent_quote.py         # 腾讯行情解析器
│   ├── main.py                  # 命令行入口
│   ├── utils.py                 # 公共工具
│   ├── web.py                   # Web 服务
│   ├── static/
│   │   └── index.html           # 前端页面（ECharts走势图+市赚率）
│   └── datasource/
│       ├── __init__.py          # DataSourceManager
│       ├── base.py              # Protocol 接口定义
│       └── mootdx_source.py     # mootdx 数据源适配器
├── tests/
│   ├── test_dividend.py
│   ├── test_historical.py
│   ├── test_tencent_quote.py
│   └── test_web.py
├── calc_pr.py                   # 市赚率 CLI
├── requirements.txt
├── pyproject.toml
└── README.md
```

## 测试

```bash
pytest -v                          # 全部 25 个测试
pytest tests/test_dividend.py -v   # 股息率测试
pytest tests/test_pr.py -v         # 市赚率测试
```

## 注意事项

- 需要网络连接从数据源获取实时数据
- 完全使用真实数据，不虚构任何数据
- 同时提供含税和扣税10%后两种口径
- 持股满1年免征红利税

## 注意事项

- 需要网络连接从数据源获取实时数据
- **mootdx 不可用时**（海外/受限网络），系统自动降级到 akshare + 腾讯行情
- 完全使用真实数据，不虚构任何数据
- 同时提供含税和扣税10%后两种口径
- 持股满1年免征红利税

## 常见问题

### 股息率相关

**Q: 为什么不使用每股分红/股价？**
A: 转送股会导致每股分红被动变化，总额法可规避此问题。

**Q: 为什么取完整财年而不是过去12个月？**
A: 过去12个月可能包含两个不同财年的分红，虚高股息率。

**Q: 扣税10%是怎么来的？**
A: A股现金分红个人所得税率通常为10%（持股1月~1年），持股满1年免征。

### 市赚率相关

**Q: 市赚率的理论基础是什么？**
A: 源自巴菲特"40美分买1美元"理念。PR<1 表示低估，PR>1 表示高估。

**Q: 基础、修正、PB 三个市赚率有什么区别？**
A: 基础版 = PE/ROE；修正版引入股利支付率修正（适用ROE稳定价值股）；PB版通过 PB/ROE² 计算（适用周期股）。

**Q: 亏损股如何处理？**
A: 净利润 ≤0 的股票被标记为亏损股，市赚率不适用。

### 网络相关

**Q: 为什么启动时有 mootdx WARNING 日志？**
A: mootdx 需要连接通达信服务器（中国大陆网络）。在受限网络环境下，mootdx 不可用属于正常现象，系统会自动降级到 akshare + 腾讯行情，不影响功能。WARNING 日志在 v2026-06-01 已降级为 DEBUG。

## 许可证

MIT License
