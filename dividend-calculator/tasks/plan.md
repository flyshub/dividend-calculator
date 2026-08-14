# Implementation Plan: 评审问题修复（P1-1 / P1-2 / P2-1 / P2-2 / P2-4 / P1-4）

## Overview

修复 2026-08-13 项目评审报告（`dividend-calculator项目实战评审报告.md`）中经核验真实存在的口径问题。核验结论：9 个问题中 8 个真实存在（P1-1 历史股本、P1-2 PR 口径不一致、P1-3 回测样本局限、P1-4 依赖不锁版本、P2-1 push2 f9 字段、P2-2 A+H 文档表述、P2-3 mootdx 正则、P2-4 偶数年中位数），1 个为设计使然（P2-5）。报告自身一处事实错误：JS 测试数为 77 而非 82。

本期范围（用户已确认全量）：**P1-1、P1-2、P2-1、P2-2、P2-4、P1-4 + Phase 3（周期股 5 年 ROE 中位数入选股器）+ Phase 4（提示层）**。

## Architecture Decisions

- **P1-1 只修历史聚合，不动最新财年股息率公式**：最新财年 `DPS×当前股本 ÷ 当前市值 = DPS/股价` 数学自洽（转送股后除权价同步调整），改动反而破坏双端一致性。真正失真的是可持续性模块的历史聚合（连续年数/曾削减/突击分红）。东财 `RPT_SHAREBONUS_DET` 每行自带历史 `TOTAL_SHARES`（已实测：600900 2021 行 22.74B、2025 行 24.47B），按行取用；cninfo/mootdx 路径无此字段 → 回退当前股本参数。
- **P1-2 统一到修正 PR**：单股页（Python `pr.py:468` + JS `calculator.js:447`）已用修正 PR，选股器 `screening.py:94` 用基础 PR 是唯一不一致点。修正 PR ≥ 基础 PR（N∈[1,2]），统一后选股器更保守、与单股页一致。`payout_ratio` 已在 `FinanceSnapshot` 中，`compute_n_factor` 为纯函数，**无需改 schema**。
- **P2-1 先实测后切换**：东财字段标准语义 f9=动态市盈率、f115=PE-TTM，但实测 push2 对 600900 返回 f115=0。实现时先 live 对照腾讯 PE-TTM：f115 可用则切换并加测试；不可用则保留 f9 但如实标注"动态市盈率"（数据铁律：口径标注必须诚实）。
- **P2-2 只改文档**：`SUSTAINABILITY.md:244` 的"低估股息率"表述数学上错误（总额法 = DPS/P_A 恰为 A 股持有人真实股息率），仅"总市值"展示值偏高。不接入港股行情（收益低且破坏持有人口径）。
- **P1-4 只加上限**：`akshare`/`mootdx` 是接口易变的两库，加上限（`<2.0`）；其余保持下限。

## Task List

### Phase 1: P1-1 历史股本修复（双端）

- [ ] **Task 1: Python 侧行级股本**（M，3-4 文件）
  - `src/datasource/base.py`：`DividendRecord` 增加 `total_shares: Optional[float] = None`
  - `src/dividend_records.py`：`summarize_dividend_rows` 从东财行取 `TOTAL_SHARES` 写入记录；cninfo/mootdx 路径不填（None）
  - `src/sustainability.py::aggregate_dividend_history`：各年总额按行股本计算（`rec.total_shares or total_shares` 参数回退）
  - 测试：新增股本变动样例（600900 2021 vs 2025 场景），断言历史总额用对应行股本
  - 验证：`pytest tests/test_dividend_records.py tests/test_sustainability.py -q`
  - 依赖：None

- [ ] **Task 2: JS 侧行级股本**（M，2 文件）
  - `site/js/calculator.js`：`parseDividendRecords` 解析 `TOTAL_SHARES` 到记录；`_aggregateDividendHistory` 用行股本（回退参数）
  - `site/js/calculator.test.js`：新增股本变动样例
  - 验证：`node --test site/js/calculator.test.js`
  - 依赖：Task 1 的口径定义（文件独立，可并行实现，验证合并）

### Checkpoint 1: 双端一致性
- [ ] `pytest tests/ -q`（419 通过 + 4 deselected）
- [ ] `node --test site/js/calculator.test.js`（77+ 通过）
- [ ] `python scripts/verify_js_vs_python.py` 通过（双端逐字段一致）

### Phase 2: PR 口径 + 小修

- [ ] **Task 3: pr.py 注释修正 + 中位数**（S，1 文件）
  - `src/pr.py:52`：注释"取两者较低值"改为"优先用修正 PR（N≥1 故 ≥ 基础 PR，判定更保守）"
  - `src/pr.py:243`：`sorted()[len//2]` 改用 `statistics.median`（偶数年取均值）
  - 验证：`pytest tests/test_pr.py -q`
  - 依赖：None

- [ ] **Task 4: push2 字段实测与修正**（S，1 文件）
  - 先 live 实测 `f115` vs 腾讯 PE-TTM（600900/600036 抽样）
  - f115 可用 → `src/pr.py:106-127` 切换 f9→f115 + 测试；不可用 → 保留 f9 但变量/日志标注"动态市盈率"
  - 验证：live 对照 + `pytest tests/test_pr.py -q`
  - 依赖：None（与 Task 3 同文件，合并执行）

- [ ] **Task 5: 选股器统一修正 PR**（S，2 文件）
  - `src/screening.py::default_pr_evaluator`：`compute_basic_pr` → `compute_corrected_pr(pe, roe, compute_n_factor(payout_ratio))`（payout_ratio 缺失时回退基础 PR）
  - 测试：更新 screening 相关测试
  - 验证：`pytest tests/test_screening*.py -q`
  - 依赖：None
  - ⚠️ 行为变化：每日选股输出更保守（修正 PR ≥ 基础 PR），需用户确认（见 Open Questions）

- [ ] **Task 6: A+H 文档修正**（XS，1 文件）
  - `docs/SUSTAINABILITY.md:244`：改为"股息率口径正确（=DPS/P_A，恰为 A 股持有人真实股息率）；仅总市值展示值偏高（H 股按 A 价计）"
  - 验证：文档审阅
  - 依赖：None

- [ ] **Task 7: 依赖上限**（XS，2 文件）
  - `requirements.txt` + `pyproject.toml`：`akshare>=1.12.0,<2.0`、`mootdx>=0.11.0,<1.0`
  - 验证：`.venv/bin/pip install -e .` 无冲突
  - 依赖：None

### Checkpoint 2: 全量回归
- [ ] `pytest tests/ -q` 全绿
- [ ] `node --test site/js/calculator.test.js` 全绿
- [ ] `python scripts/verify_js_vs_python.py` 通过
- [ ] 每日选股器 dry-run 一次（`scripts/export_screener_json.py` 或等价）确认输出正常

### Phase 3: 周期股 5 年 ROE 中位数入选股器（已确认本期）

- [ ] **Task 8: FinanceSnapshot 扩展**（L，5+ 文件）
  - `src/screener_cache.py`：`FinanceSnapshot` 加 `roe_5y_median` + `is_cyclical` 列（DB 迁移 + 兼容旧库）
  - 财务拉取管线补算 5 年 ROE 中位数与周期行业判定（复用 pr.py 既有逻辑）
  - `src/screening.py::default_pr_evaluator`：周期股用中位数算 PR（对齐单股页 `pr.py:460`）
  - 验证：全量回归 + 选股器 dry-run 对比
  - 依赖：Task 5（口径统一后）

### Phase 4: 提示层（已确认本期）

- [ ] **Task 9: 小盘股 PR 未验证提示**（S，site/ UI）— P1-3
- [ ] **Task 10: 特别分红人工核实提示**（XS，site/ UI）— P2-5

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| P1-1 双端不同步破坏 verify_js_vs_python | High | Task 1/2 同口径实现，Checkpoint 1 强制跑 verify 脚本 |
| P2-1 f115 实测不可用 | Med | 先实测再定；不可用则保留 f9 并如实标注（不虚构口径） |
| Task 5 改变每日选股输出 | Med | 用户确认后执行；输出变化在 PR 描述中明示 |
| 周期股中位数涉及 DB schema 迁移 | Med | 独立批次（Phase 3），旧库兼容迁移 |
| pytest 本地跑挂起（tdxpy 心跳线程 #243） | Low | 用 `.venv/bin/python -m pytest` + 已知 workaround |

## Open Questions

1. ~~Task 5 选股器统一修正 PR~~ → 已确认：统一为修正 PR
2. ~~Phase 3 周期股中位数~~ → 已确认：本期做
3. ~~Phase 4 提示层~~ → 已确认：本期做
4. **P2-1**：f115 实测结果决定切换或标注，实现时先验证。
---

# Issue #122：月频管线补真正的财务拉取

## 现状（已核实）

- `fill_screener_data.py --finance` → `fill_finance` → `evaluate_pr_batch`（只读缓存、返回值被丢弃）→ **空操作**
- 可复用链路已存在：`pr.py::_get_financial(code)` 返回 (roe_latest, roe_5y_median, net_profit_annual, src, errors, roe_period)，mootdx F10 → akshare 同花顺；`pr.py::_get_industry(code)` 返回行业（→ classify_industry 判周期）
- `upsert_finance(FinanceSnapshot)` 已支持全部字段（含 roe_5y_median/is_cyclical/updated_at）
- 模式参照：`screener_dividend.py::compute_dividends_for_candidates`（batch + batch_wait 限流 + upsert + 进度）

## 方案

新建 `src/screener_finance.py`（镜像 screener_dividend.py）：

- `compute_finance_for_candidates(batch, cache, fresh_days=7)`：逐股
  1. 新鲜度检查：`finance_snapshot.updated_at` 在 fresh_days 内 → 跳过（增量复用）
  2. `_get_financial(code)` → roe_latest/roe_5y_median/net_profit_annual/roe_period
  3. `_get_industry(code)` → classify_industry → is_cyclical
  4. payout_ratio = dividend_snapshot.total_dividend / net_profit_annual（净利润缺失/≤0 → None，漏斗回退基础 PR）
  5. `cache.upsert_finance(...)`，finance_source 标注实际来源
- `fill_finance` 改用该函数，batch_wait 限流 0.8s/只 + 进度汇报（对齐 fill_dividends）

## 验收标准（issue #122）

- [ ] 对含过期 finance_snapshot 的样本执行 `--finance` 后，DB 中 ROE/中位数/周期标记确实更新
- [ ] 限流与增量复用生效（fresh_days 内数据跳过）
- [ ] 相关测试通过（mock 网络）；真实数据冒烟 `--limit 3` 验证可获得性（数据铁律）

## 测试

- tests/test_screener_finance.py（新）：mock `_get_financial`/`_get_industry` → upsert 字段正确（含 payout_ratio/is_cyclical）；新鲜跳过/过期拉取；净利润缺失 → payout None；batch_wait mock（同 test_screener_pr.py 模式）
- 回归：pytest 全量 + verify_js_vs_python（不动 JS，仅确认无回归）

## 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 全 A 逐股拉取耗时（~0.8s/只） | 月频 +~30-60 分钟 | 只拉 get_dividend_codes（有股息候选），非全市场 |
| mootdx F10 海外可用性 | 回退 akshare 同花顺（已实测 10/10） | 复用 pr.py 既有降级链 |
| 真实数据冒烟需网络 | CI 排除 | 本地手动 `--limit 3` 验证后合并 |
