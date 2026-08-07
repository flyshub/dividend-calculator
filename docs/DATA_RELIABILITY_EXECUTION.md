# 数据可靠性修复 — 执行计划（供 /implement 批量执行）

> 来源：`docs/DATA_RELIABILITY.md` 审查报告 → GitHub Issues #8-#17。
> 每个 ticket 的完整规格在对应 issue body，本文件只记顺序、依赖与验收。

## 批次总览

| 批次 | Tickets | 说明 |
|------|---------|------|
| P1 地基 | #8 | warnings 字段，解锁 P2 |
| P2 高优先级 | #9 → #10 → #11 | 数据准确性，须按序，都依赖 #8 |
| P3 口径联动 | #14 → #13 | 财年口径 → 数据时效（有依赖 + 共享文件） |
| P4 独立并行 | #12 → #13 同批展示层 | #12 与 #13 共享展示层，放一起做 |
| P5 收尾 | #15、#16、#17 | 独立，可并行；#17 CI 最后 |

## 依赖图

```
#8（共享基建）
 ├→ #9 sanity bound 校验层     [P2]
 │    └→ #10 A+H 股本回退      [P2]（#9 的 validation.py/warnings 是其地基）
 │         └→ #11 跨源交叉验证  [P2]
#14（财年口径）                  [P3]
 └→ #13 数据新鲜度              [P3]
#12 net_profit_ttm              [P3/P4]（与 #13 共享展示层，同批做）
#15 浮点回归测试 / #16 数据来源  [P5] 独立
#17 CI 接入                     [P5] 最后（全量测试当门禁）
```

## 逐批次执行细节

### P1 — #8 StockInfo warnings 字段（地基）
- **为什么最先**：#9/#10/#11 的告警都写进 `StockInfo.warnings`，无它则三个都改不了
- **验收**：`StockInfo()` 默认 `warnings == []`；`python -m pytest tests/ -q` 全绿
- **产出**：`src/datasource/base.py` 加字段

### P2 — #9 → #10 → #11（按序，勿乱）
- **#9 sanity bound 校验层**：新增 `src/datasource/validation.py`（纯函数），插桩 api/dividend/pr/sustainability
  - 验收：新增 `tests/test_validation.py`；软界只增 warning 不改返回值
- **#10 A+H 股本回退**：`_resolve_total_shares` 优先 mootdx zongguben
  - 验收：`test_tencent_source.py` 更新（mootdx 失败回退带 warning / 成功用 zongguben）；中远海控冒烟
- **#11 跨源交叉验证**：`get_stock_info` 主源 + mootdx best-effort 比对
  - 验收：mock 差异值断言 warning；mootdx 不可用静默跳过

> P2 三个都只依赖 #8，但按 #9→#10→#11 顺序做最顺（前者的 validation.py/warnings 载体是后者的地基）。

### P3 — #14 → #13（口径联动）
- **#14 三套财年口径交叉校验**：新建 `test_fiscal_year_crosscheck.py`，固化「已知差异」
  - 验收：`pytest tests/test_fiscal_year_crosscheck.py -v` 全绿；现有 `test_fiscal_year.py` 不回归
- **#13 数据新鲜度**：`SustainabilityResult.latest_annual_year` + stale note
  - 验收：latest.year=2023 + 2026 → note；2025 → 无 note

> #13 依赖 #14（财年正确性），且 #14 的 `select_latest_annual` 交会点先行。

### P4 — #12 net_profit_ttm 口径（与 #13 同批）
- `net_profit_ttm` → `net_profit_latest_period`；同花顺路径真 TTM；mootdx 标注累计
- **与 #13 共享展示层**（PRResult/SustainabilityResult 序列化 + CLI/Web），建议 #13 做时一并处理展示文案，避免两次碰同一批文件
- 验收：字段改名后 `verify_js_vs_python.py` 同步仍一致；`tests/test_web.py` expected_keys 更新

### P5 — #15、#16、#17（独立并行，CI 最后）
- **#15 浮点回归测试**：`TestMootdxParseXdxr` 构造 df 测 `_parse_xdxr`
  - 验收：`pytest tests/test_mootdx_injection.py -v` 全绿
- **#16 股息率数据来源**：`DividendResult.dividend_source`（独立字段，不进 explanation）
  - 验收：`test_web.py` expected_keys 加字段；600887 冒烟显示来源
- **#17 CI 接入**：`.github/workflows/ci.yml` + verify 假绿修复
  - **最后做**：等 P1-P5 主要修复落地，CI 跑全量测试当门禁才有意义

## 通用验收（每 ticket 必跑）

```bash
cd dividend-calculator
python -m pytest tests/ -q          # Python 全量
node --test site/js/calculator.test.js  # JS 全量
PYTHONIOENCODING=utf-8 python scripts/verify_js_vs_python.py  # 双端一致
```

## 执行约定（对齐 ask-matt 流程）

- 每个 ticket 开新分支 `feat/data-reliability-<#>`，从 main 切
- 用 `/implement` 按本 ticket 驱动 `/tdd`，完成后 `/code-review` 两轴审查
- 每个 ticket 完成后删除分支，再开下一个（context 隔离）
- 全部完成后更新 `docs/DATA_RELIABILITY.md` §4 状态列（⬜ → ✅）
