# 回测方法说明（按实际代码，2026-08-14）

> 本文档基于当前 `feature/backtest-four-funnel` 分支的实际代码撰写，覆盖回测「怎么做」与数据「怎么来」。
> 涉及文件：`scripts/build_backtest_db.py`（数据构建）、`scripts/backtest_engine.py`（分层回测引擎）、
> `src/backtest_factors.py`（因子计算）、`scripts/backtest_portfolio.py`（组合绩效）、
> `scripts/backtest_report.py`（报告生成）、`scripts/backtest_sensitivity.py` / `backtest_robustness.py` / `backtest_significance.py`（敏感性/稳健性/显著性）。

---

## 0. 一句话总览

```
数据层（build_backtest_db.py，9 张表，断点续传）
   ↓ 预加载进内存
BacktestLookup（backtest_engine.py，asof 无未来函数过滤）
   ↓
因子层（src/backtest_factors.py，纯函数）
   ↓
run_backtest（backtest_engine.py）：54 个季度调仓日，四层漏斗逐层标记 → 5 档季度收益 + 逐层增量超额
   ↓
run_portfolio（backtest_portfolio.py）：税后分红复投 + 换手成本 → 绩效指标 + 基准对比
   ↓
backtest_report.py：生成 docs/BACKTEST_REPORT_V3.md
```

---

## 1. 数据怎么来（scripts/build_backtest_db.py）

产出 `data/backtest.db`（SQLite，WAL 模式），**9 张表**：

| 表 | 内容 | 数据源 | 说明 |
| --- | --- | --- | --- |
| `stock_list` | 全 A 股票（含退市 361 只） | akshare `stock_info_a_code_name` + `stock_info_sh_delist` + `stock_info_sz_delist` | 现存 + 沪退市 + 深退市合并，消除幸存者偏差；`delist_date` 沪市为暂停上市日期（无正式退市字段，暂停上市日≈最后交易日）；现存股无上市日期字段 → `list_date` 留 NULL |
| `daily_price` | 日频不复权收盘价 | 腾讯 `ifzq.gtimg.cn/appstock/app/fqkline/get` | **不复权**（param 空复权参数 → `day` 键）；单次最多 ~2000 根（实测 3000/5000 被拒），2013-2026 分两段：2013-01-01~2018-12-31、2019-01-01~今天 |
| `daily_pe` | 日频 PE_TTM | akshare `stock_zh_valuation_baidu`（百度估值） | 列名 date/value，全历史 |
| `dividend_history` | 历史分红 | 东财 datacenter `RPT_SHAREBONUS_DET`（经 `src/eastmoney_fetcher.fetch_dividend_rows`） | 字段：`announce_date`（公告日，无未来函数约束关键）、`report_date`、`ex_dividend_date`、`cash_div_10shares`（PRETAX_BONUS_RMB 每 10 股派息，如实反映单位不折算）、`bonus_ratio`（每 10 股送股）、`trans_ratio`（每 10 股转增，实为东财 `BONUS_IT_RATIO` 字段） |
| `finance_history` | 历史财务 | 东财 datacenter `MAINFINADATA`（经 `fetch_financial_rows`） | **仅保留 12-31 完整财年**（`month==12` 规则，其余报告期为中期分配）；字段：`roe`（取 `ROEJQ` 加权净资产收益率，实测 `ROE_WEIGHTED` 在该接口全为 None 死字段）、`net_profit`、`net_cash_operate`、`bps`、`newcapitalader`（资本充足率，银行专项）、`loan_provision_ratio`（拨备覆盖率，银行专项）、`notice_date`（实际披露日，消除财报未来函数的关键） |
| `index_daily` | 基准指数 | akshare `stock_zh_index_hist_csindex` | 中证全收益指数 `H00922`（中证红利全收益，主基准）+ `H00300`（沪深300全收益，次基准）；中证官网 closeweight xls 路径已失效，走同一中证官方数据源 |
| `total_shares` | 总股本 | 腾讯 `qt.gtimg.cn/q=` 批量行情（**Index 73**，含 A+H） | 50 只/批，全量 ~60s；**当前快照无历史**（A+H 必须用 Index 73 而非 Index 72 流通股本） |
| `industry` | 行业 | 东财 `fetch_industry` | 当前快照；EM2016 优先 / INDUSTRYCSRC1 降级 |
| `build_progress` | 断点续传标记 | — | 按 表×code 记录已完成；**拉取异常不标记、下次重试；真无数据（0 行）也标记**，避免重复请求 |

### 数据铁律（实现内嵌）

- 数据源不可用 → 记缺失（0 行 + 标记完成），**绝不编造、推算补缺**（`build_dividend` 区分：`fetch_dividend_rows` 返回 `None`=取数失败不标记完成，`[]`=真无分红标记完成）。
- 网络请求统一 3 次退避重试（429/500/502/503/504）+ 30s 读取超时；批量拉取每请求间隔 0.15s 限速友好（`_pace`）。
- 坏行跳过不虚构（`parse_kline` 对 IndexError/TypeError/ValueError continue）。
- 抽样模式 `--sample` 跑 5 只 + 数据合理性断言（分红条数、估值行数、日K最早日期、财务全部 12-31）。

### 断点续传与迁移

- `--table <表名>` 只构建单表；`--codes` 自定义代码；全量默认按 stock_list 全 A。
- 历史库演进：旧表缺新列时 `_migrate` ALTER ADD COLUMN（`dividend_history` 加 bonus_ratio/trans_ratio、`finance_history` 加 notice_date）。

### 已知数据缺口（如实标注）

- `total_shares`/`industry` 为**当前快照非历史**：回测期内增发/分红送股会改变真实股本，历史市值失真（已标注近似；股息率每股口径数学等价，仅 sustainability 支付率受影响）。
- `top10_holding` 未入库（一股独大红旗不触发）。
- 财务字段覆盖 8 项，`net_profit_yoy`/`investing_cf`/`total_assets`/`interest_coverage` 等缺失 → None，可持续性部分维度降级计分。

---

## 2. 数据如何进入回测（BacktestLookup）

`scripts/backtest_engine.py:BacktestLookup` 是引擎的 DB 实现：

- **预加载**：一次性把 `daily_price`/`daily_pe`/`dividend_history`/`finance_history`/`stock_list`(delist/listdt)/`total_shares`/`industry`/`index_daily` 读进内存（dict + 二分查找）。
- **交易日历**：用 H00922 全收益指数的交易日（2013-2026 完整覆盖）作为 `trading_days`。
- **双 lookup 契约**：`__getitem__`（下标访问，T3 因子层用）+ `get`（dict 风格，T3 `_industry` 用）——缺一会让对应消费方崩。

### 无未来函数过滤（asof）

| 数据 | 过滤规则 |
| --- | --- |
| 价格 / PE | `date ≤ T` 最近值（`_latest` 二分查找） |
| 分红 | **`announce_date ≤ T`**（公告日；公告日缺失时视为可见，不排除） |
| 财报 | **优先 `notice_date ≤ T`（实际披露日，T11 修复），notice_date 缺失/解析失败回退 `report_date ≤ T`**（12-31 年报）。1-4 月调仓时当年年报未披露 → 不可见 → 用前一年年报，杜绝未来函数 |
| 总股本 / 行业 | 当前快照（无时序，直接取） |
| 退市 | `delist_date ≤ T` 的股票不可入选（T5 修复：已退市无法持有） |
| 上市 | `list_date ≥ T` 的股票不可入选（T16 修复：未上市无法持有；现存股 list_date 为 NULL → 不过滤） |

---

## 3. 因子怎么算（src/backtest_factors.py，纯函数零网络）

### 3.1 真实股息率 `real_dividend_yield`（L1 漏斗门槛）

```
真实股息率 = 最新完整财年现金分红总额 / T 日总市值 × 100%
```

- 完整财年 = 报告期 12-31 的最新财年，**该财年内全部报告期（含中期分配）现金分红合计**（对齐现网 `_parse_fhps_detail`，#37 M4 仅 12 月报告期构成完整财年）。
- 只用 `announce_date ≤ T` 的分红（无未来函数）。
- 无分红记录 → 0.0；price/股本缺失 → None。

### 3.2 TTM 股息率 `ttm_dividend_yield`（L2 漏斗门槛）

```
TTM 股息率 = 近 12 个月实际派发分红总额 / T 日总市值 × 100%
```

- 按 `ex_dividend_date ∈ (T-365, T]`（起点开、终点闭）的实际派发，对齐 `utils.compute_ttm_dividend`。
- 同时要求 `announce_date ≤ T`；窗口内无派息 → None。

### 3.3 市赚率 `pr`（L3 漏斗）

```
基础市赚率 PR = PE_TTM / ROE_latest   （百分数直接相除，无 ×100，round 2 位）
```

- `PE_TTM` 取 T 当日快照；`ROE_latest` 取按披露日过滤的最新年报 ROE（见 §2）。
- ROE 缺失或 ≤ 0 → PR=None（不入选 L3）。
- 周期股警示规则保留（周期 > 科技 > 成长，重叠只报最优先一类），供页面/报告用，不影响入选。

### 3.4 股息可持续性 `sustainability`（L4 漏斗）

复用现网 `sustainability_calculator.assess_sustainability`：六维判据 + 银行专项（CAR/净息差/不良率/拨贷比，低 ROE 银行不因 ROE 判弱）+ 情境红旗（被动高股息、突击分红、一股独大）。返回 verdict ∈ {可持续, 偏弱, 不可持续}；股息率 ≤ 4% → 未评估。L4 入选 = verdict ∈ {可持续, 偏弱}。

### 3.5 漏斗判定 `funnel_layer`

```
L2: TTM > 5% 且 真实 > 5%      （阈值参数化：yield_thr/real_yield_thr）
L3: 基础 PR ≤ 1.0              （pr_thr）
L4: verdict ∈ {可持续, 偏弱}
任一层不通过即短路（对齐现网筛选口径）
```

---

## 4. 回测怎么做（scripts/backtest_engine.py）

### 4.1 调仓日历与建仓

- 调仓日 = 每月/季/半年末的**最后交易日**（`rebalance_dates`，freq=monthly/quarterly/semiannual）。默认季度 → 54 个调仓日（2013-01 ~ 2026-08）。
- 建仓日 = T 之后第 `build_offset` 个交易日（`build_day_after`，默认 1 = **T+1**；稳健性检验用 T+5）。
- 结算日 = 下个调仓日 T（末季无结算日 → 该期收益 None 跳过）。

### 4.2 五档分层（每季度独立重算）

对每个调仓日 T、对全 A 每只股票（退市/未上市过滤后）算 4 因子 → `funnel_layer` 得层数 → 分层入桶：

```
base  = 全部
l2    = layer ≥ 2（TTM 且 真实 > 5%）
l3    = layer ≥ 3（+ PR ≤ 1）
l4    = layer ≥ 4（+ 可持续）
full  = layer ≥ 4（与 l4 同池，恒等）
```

### 4.3 单期收益 `portfolio_return`（纯价格 + 送转因子）

```
单期收益 = 等权平均( 送转因子 × 结算价/建仓价 − 1 − 2×成本 )
```

- **送转除权因子（T10 修复）**：持有期（build_day, settle_day] 内发生送转时，
  `送转因子 = Π (1 + (bonus_ratio + trans_ratio) / 10)`，每 10 股送+转合计。
  例：10送10 → 因子 2.0，股数翻倍，除权价格腰斩不再是伪亏损。
- 双边交易成本：每期全换手，进出各 0.3%（`cost=0.003`，合计 0.6%/期）。
- 无价格（停牌/退市）个股剔除；全部无价格 → None。

### 4.4 逐层增量超额（比值口径，T3 修复）

```
增量超额(单期) = (1 + r_cur) / (1 + r_prev) − 1     （非 r_cur − r_prev 线性近似）
增量超额(累计) = Π(1 + 单期超额) − 1
```

- 键：`l2_over_base` / `l3_over_l2` / `l4_over_l3` / `full_over_base`。
- **`full_over_l4` 恒等行已删除**（l4 ≡ full 同池，无信息量）。
- 修复动机：线性 `r − p` 在 2015 年 ±30-50% 大波动期失真甚至符号翻转（报告曾出现 +L4 累计 > +L3 但增量超额为负的矛盾）。

### 4.5 引擎输出结构（T5/T6 消费契约）

```python
{
  "rebalance_dates": [...],        # 54 个调仓日
  "pools": {"base": [[codes], ...], "l2": ..., "l3": ..., "l4": ..., "full": ...},
  "quarterly_returns": {"base": [r, ...], ...},        # 每档每期收益（含 None）
  "cumulative_returns": {k: 累计},                     # Π(1+r) − 1
  "incremental_excess": {k: 累计超额},
  "excess_series": {k: [单期超额, ...]},               # 显著性检验输入
}
```

---

## 5. 组合绩效（scripts/backtest_portfolio.py）

### 5.1 含分红真实全收益（headline 口径，T5）

```
组合区间总收益 = Σ w_i × [ (1 + 价格收益_i) × (1 + 分红复投_i) − 1 ] − 换手缩放成本
```

- **价格收益** = 送转因子 × 结算价/建仓价 − 1（与引擎 §4.3 同口径，T10 双端口径修复）。
- **税后分红复投**（`after_tax_dividend_contrib`）：区间内每笔分红按除权日持仓时长定三档税率
  （>1 年 0% / 1 月~1 年 10% / <1 月 20%），税后净额于除权日按当日价格再买入；
  贡献 = Σ(税后每股分红 / 除权日价格)。无未来函数：只取公告日 ≤ settle_day 的记录。
- **成本按实际换手缩放（T7 修复）**：`scaled_cost = 2 × cost × turnover_ratio`，
  `turnover_ratio = 1 − 交集/并集`（首期全建仓 = 1.0）。修复前 base 零换手被收满额成本。
- 加权方式：等权（默认）/ 市值加权（price × total_shares）/ 股息率加权（TTM 股息率）。

### 5.2 绩效指标

| 指标 | 实现 |
| --- | --- |
| 累计 | Π(1+r) − 1，跳过 None |
| **年化** | **优先按日历跨度（T9 修复）**：`(1+累计)^(1/日历年数) − 1`，日历年数 = (末调仓日−首调仓日).days/365.25，**空仓期计入分母**；无 rebalance_dates 时回退 n_periods/ppy |
| 波动 | 单期收益率样本标准差（小数，报告显示 %） |
| 最大回撤 | NAV 从峰到谷最大跌幅 |
| 夏普 | (mean − rf/ppy) / std × √ppy，**rf=3%**（T8 修复：2013-2026 中国 10 年期国债均值 ~3.2%） |
| 索提诺 | 同上但分母只用下行偏离 |
| 卡玛 | 年化 / 最大回撤 |
| 胜率 | 正收益期占比 |
| 下行风险 | 仅负偏离标准差，年化 |
| 盈亏比 | 平均盈利期 / 平均亏损期 |

**periods_per_year 按频率传（T4 修复）**：月=12、季=4、半年=2。旧实现固定 n/4 把月调仓 147 期当 36.75 年、半年 24 期当 6 年，年化严重失真（「半年调仓 32.76% 最优」是此 bug 伪影）。

### 5.3 基准对比

- 主基准：中证红利全收益 `H00922`；次基准：沪深300全收益 `H00300`（均从 `index_daily` 读取）。
- 基准季度收益按调仓日对齐（`load_benchmark`：取 T 当日或之前最近收盘）。组合建仓日为 T+1，与基准计量窗（T→下T）相差 1 个交易日，对季度收益影响 <0.1%（已知时点差，#130 M-12）。

### 5.4 已知限制（代码内标注）

- **税率按 build_day → ex_date 持仓时长定档、每期独立结算**（T8 标注）：跨期继承持仓时不重置建仓日，实际 >1 年的分红可能被误判 10% 档，税拖累被略微高估。完整 FIFO 需跟踪每只股票最早建仓日，属结构性改造暂缓。

---

## 6. 敏感性 / 稳健性 / 显著性

### 6.1 参数敏感性（scripts/backtest_sensitivity.py）

| 维度 | 档位 |
| --- | --- |
| 股息率阈值 | >4% / >5% / >6% |
| PR 阈值 | ≤0.8 / ≤1.0 / ≤1.2 |
| 调仓频率 | 月 / 季 / 半年（**每档同时跑纯价格 + 含分红两口径**，T4 修复后按实际期长年化） |
| 持仓 | 全池 / Top20 / Top10（按真实股息率降序） |
| 加权 | 等权 / 市值加权 / 股息率加权 |
| 随机起点 | ≥20 组（`scan_random_starts`，T15 新增），报告分布 |
| 3×3 网格 | 股息率阈值 × PR 阈值（T15 新增） |

输出顶部带**多重比较警示**（参数扫描属事后挑选未校正，标注非显著性）。

### 6.2 稳健性（scripts/backtest_robustness.py）

四变体（每个调仓日 T 逐期重过滤，防未来函数）：
- 主回测 T+1（基准）
- 剔微盘（市值 < 50 亿，用真实 total_shares × 当日价格）
- 剔金融（真实 industry 分类，125 只金融股；旧版名称近似会漏剔金控/投资类）
- 延迟 T+5 调仓

另有 `random_start_offsets`（起始季平移，seed=42）。

### 6.3 显著性检验（scripts/backtest_significance.py，T12）

- 对逐期超额收益做 **t 检验**（p 值正态近似，n≥8 可接受）。
- **block bootstrap**（真重叠块重采样，T12 修复）：序列切成 n−block_size+1 个重叠块，有放回抽取 ⌈n/block_size⌉ 块拼接取均值，1000 次 → 均值 95% CI；block_size 默认 √n。
- 样本不足（<8 期）如实标注，不强行给结论。
- 当前结果（2026-08-14）：全漏斗 vs 全A基线 49 期逐期均值 +2.05%，t=0.904，p=0.366，95% CI [-1.62%, +7.42%] —— **未达统计显著**。

---

## 7. 报告生成（scripts/backtest_report.py）

`generate_report` 按顺序组装 9 段（`section_data_scope` / `section_layered_incremental` / `section_portfolio_perf` / `section_hfq_comparison` / `section_sensitivity` / `section_robustness` / `section_attribution` / `section_conclusion` / 验证结论 + 已知限制 + 复现）写入 `docs/BACKTEST_REPORT_V3.md`。

- **口径分离**：§2 纯价格收益（仅方向性验证，显著低估高股息策略）；§3 含分红真实全收益（headline）；§3.1 hfq 无税对照（tax_override=0.0，数学等价 hfq 全收益，衡量红利税拖累）。
- **结论段如实表述**（2026-08-14 修复）：引用显著性检验数字，未达统计显著不写「显著超额」。
- **归因分析**（T14）：逐年收益表 + 子期间拆分（2013-2019 含 2015 牛熊 / 2020-2026 疫后），不做多因子归因（属后续工程）。
- 报告数字变化后，`docs/BACKTEST_REPORT_V3_PLAIN.md` 大白话解读需人工/大模型按新数字重写（程序不生成解读）。

---

## 8. 复现命令

```bash
# 数据构建（断点续传）
python scripts/build_backtest_db.py            # 全量（或 --sample / --table X / --codes 600519,000858）

# 分层回测引擎（控制台输出）
python -m scripts.backtest_engine   # 或 python scripts/backtest_engine.py

# 组合绩效 + 基准对比
python scripts/backtest_portfolio.py

# 敏感性 / 稳健性 / 显著性
python scripts/backtest_sensitivity.py
python scripts/backtest_robustness.py
python scripts/backtest_significance.py --db data/backtest.db

# 生成报告
python scripts/backtest_report.py --db data/backtest.db --out docs/BACKTEST_REPORT_V3.md

# 全量测试
python -m pytest tests/ -q
```
