# 数据源架构说明

## 概述

项目采用 **腾讯 + mootdx + akshare + 东方财富 datacenter 多引擎**：腾讯 HTTP 提供实时价格/总股本/PE/PB/K线（主引擎），mootdx 通达信二进制协议提供 F10 财务/行业与降级兜底，akshare 提供分红明细/财务/新浪行情，东方财富 datacenter 提供可持续性全部字段并作为 JS 静态版（浏览器直连）数据源。

实时价格/股本/PE/PB/K线以腾讯为主源（全球可用）；mootdx 与 akshare 作为降级链；mootdx 不可用（海外或受限网络）时自动落到 akshare 同花顺。

## 架构图

```
                         ┌──────────────────────────┐
                         │   dividend.py / pr.py     │
                         │      核心计算逻辑           │
                         └──────────┬───────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
    ┌─────────▼─────────┐  ┌───────▼───────┐  ┌──────────▼──────────┐
    │   api.py          │  │  datasource/  │  │  tencent_quote.py   │
    │  多源降级编排       │  │  Protocol层   │  │  腾讯行情解析        │
    └─────────┬─────────┘  └───────┬───────┘  └──────────┬──────────┘
              │                    │                      │
              │         ┌──────────▼──────────┐           │
              │         │  DataSourceManager  │           │
              │         │  按优先级自动降级     │           │
              │         └──────────┬──────────┘           │
              │                    │                      │
    ┌─────────▼────────────────────▼──────────────────────▼──────────┐
    │                        数据源层                                 │
    │                                                                 │
    │  ┌──────────────────────┐    ┌──────────────────────────────┐  │
    │  │   mootdx_source      │    │      腾讯行情 API             │  │
    │  │                      │    │                              │  │
    │  │ • quotes()  实时行情  │    │ • PE_TTM / PB               │  │
    │  │ • bars()    K线数据   │    │ • 总股本 (Index 73)          │  │
    │  │ • xdxr()   除权除息   │    │ • 实时价格                   │  │
    │  │ • finance() 财务快照  │    │ • fqkline 月度K线            │  │
    │  │ • F10()    公司资料   │    │                              │  │
    │  └──────────────────────┘    └──────────────────────────────┘  │
    │                                                                 │
    │  ┌──────────────────────┐    ┌──────────────────────────────┐  │
    │  │   新浪行情 (备用)      │    │  东方财富 push2 (最后备用)    │  │
    │  │ • 实时价格            │    │ • PE/PB/行业                 │  │
    │  └──────────────────────┘    └──────────────────────────────┘  │
    └─────────────────────────────────────────────────────────────────┘
```

## 数据源对比

| 特性 | mootdx | 腾讯行情 | 新浪行情 | 东方财富push2 |
|------|--------|---------|---------|-------------|
| **协议** | 通达信二进制 | HTTP | HTTP | HTTP |
| **速度** | 极快 (30ms) | 快 | 快 | 中等 |
| **全球可用** | ✅ | ✅ | ✅ | ⚠️ 不稳定 |
| **需要Token** | 否 | 否 | 否 | 否 |
| **K线数据** | ✅ 日/周/月/季/年 | ✅ 日/周/月 | ❌ | ⚠️ |
| **实时行情** | ✅ 五档盘口 | ✅ 最新价 | ✅ 最新价 | ✅ |
| **PE/PB** | ❌ | ✅ PE_TTM/PB | ❌ | ⚠️ |
| **总股本** | ✅ finance | ✅ Index 73 | ❌ | ⚠️ |
| **除权除息** | ✅ xdxr | ❌ | ❌ | ❌ |
| **财务指标** | ✅ F10 + finance | ❌ | ❌ | ⚠️ |
| **行业分类** | ✅ F10行业分析 | ❌ | ❌ | ⚠️ |
| **Python库** | mootdx | requests | requests | requests |

## 数据职责分工

### mootdx — 通达信协议引擎

通过通达信协议获取以下数据：

#### 1. 实时行情 (`quotes`)
```python
from mootdx.quotes import Quotes
client = Quotes.factory(market='std')
df = client.quotes(symbol='600036')
# 返回 46 字段：price, open, high, low, vol, amount, 五档买卖盘
```

#### 2. K线数据 (`bars`)
```python
# frequency: 4=日, 5=周, 6=月, 9=日, 10=季, 11=年
# 前复权通过 mootdx tools/reversion 实现
df = client.bars(symbol='600036', frequency=6, offset=120)  # 120个月（10年）月K线
```

#### 3. 除权除息 (`xdxr`)
```python
df = client.xdxr(symbol='600036')
# 返回字段: year, month, day, category(1=除权除息), fenhong(每股现金分红×10)
```

#### 4. 财务快照 (`finance`)
```python
df = client.finance(symbol='600036')
# 返回 37 字段: zongguben(总股本), jinglirun(净利润), jingzichan(净资产)
# ROE = jinglirun / jingzichan × 100
# EPS = jinglirun / zongguben
```

#### 5. F10 公司资料 (`F10`)
```python
f10 = client.F10(symbol='600036')
# 返回 dict，包含以下分类:
#   财务分析 → 多年加权ROE、净利润（结构化表格）
#   行业分析 → 行业分类字符串
#   分红扩股 → 分红历史明细
#   公司概况 → 基本信息
#   股东研究 → 股东结构
```

### 腾讯行情 — 实时价格/PE/PB/总股本/K线（主引擎）

```python
from src.tencent_quote import fetch_tencent_quote
quote = fetch_tencent_quote('600036')
# TencentQuote:
#   .pe_ttm        — 市盈率(TTM)
#   .pb            — 市净率
#   .total_shares  — 总股本 (Index 73, 含A+H)
#   .price         — 最新价
```

### 新浪行情 — 价格备用

```python
# HTTP GET https://hq.sinajs.cn/list=sh600036
# 响应字段3 = 最新价
```

### 东方财富 push2 — 最后备用

仅在 mootdx 和腾讯均不可用时启用，用于 PE/PB 的兜底；行业分类兜底走东方财富 datacenter（RPT_F10_BASIC_ORGINFO，与 JS 静态版/可持续性同源）。

## 降级优先级

### 股票信息获取 (`get_stock_info`)
```
1. 腾讯行情（价格+总股本，一次请求）
   ↓ 失败
2. 新浪行情（价格） + 腾讯/mootdx 总股本
   ↓ 失败
3. mootdx finance（价格 + 总股本兜底）
```

### 月度K线 (`get_historical_data`)
```
1. 腾讯 fqkline（HTTP，全球可用）
   ↓ 失败
2. mootdx bars（通达信协议，全球可用）
```

### 分红数据
```
1. akshare fhps_detail_em（东财 datacenter，含报告期字段 → 精确判财年）
   ↓ 失败
2. akshare cninfo（全国统一格式，含报告期）
   ↓ 失败
3. mootdx xdxr（通达信协议，无报告期，按除权日近似推断，兜底）
```

### 财务数据（ROE/净利润）
```
1. mootdx F10 财务分析（多年年报数据，含5年ROE中位数）
   ↓ 失败
2. akshare 同花顺 stock_financial_abstract_ths（年度+季度，含ROE/净利润）
```

### 行业分类
```
1. mootdx F10 行业分析（文本解析）
   ↓ 失败
2. 东方财富 datacenter RPT_F10_BASIC_ORGINFO（EM2016，与 JS/可持续性同源）
```

## mootdx 注意事项

1. **浮点精度**：xdxr 返回的 fenhong 值可能有浮点误差（如 2.1 显示为 2.09999...），计算时需 `round(, 4)`
2. **F10 年份过滤**：财务分析表格第一列通常是 Q1 数据（如 2026-03-31），解析 ROE/净利润时需过滤到仅 12-31 年报数据
3. **单例连接**：`get_quotes_client()` 维护模块级单例，避免每次调用重新连接
4. **市场代码**：mootdx 市场代码 0=深圳, 1=上海，股票代码为纯6位数字

## 修复历史

- 2026-08-10：文档对齐代码 —— 分红主源改为 akshare fhps_detail_em（按报告期判财年，与 JS 双端一致）；实时价格/股本/K线主源为腾讯；行业备用改为东财 datacenter；补充可持续性东财 datacenter 链路与选股器说明
- 2026-06-01：新增 akshare 降级链路 —— mootdx 不可用时自动回退 akshare 同花顺（分红/ROE/净利润），确保受限网络环境可用
- 2026-05-31：数据源从 akshare/baostock 迁移到 mootdx + 腾讯双引擎，不再依赖东方财富服务器
- 2026-05-26：修复中远海控、中国铝业等 A+H股公司错误使用流通股本的问题
