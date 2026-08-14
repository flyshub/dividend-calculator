# 评审修复任务清单（2026-08-13 评审报告核验后）

## Phase 1: P1-1 历史股本（双端）
- [x] Task 1: Python 行级股本（base.py / sustainability.py / 测试）— 55+5 passed
- [x] Task 2: JS 行级股本（calculator.js / 测试）— 80 passed
- [x] Checkpoint 1: 双端回归 — pytest 446 / node 89 / verify 全部字段一致

## Phase 2: PR 口径 + 小修
- [x] Task 3: pr.py 注释修正（P1-2）+ 中位数 statistics.median（P2-4，两处）
- [x] Task 4: push2 字段 f9→f164 + fltt=2（P2-1，实测证据驱动）
- [x] Task 5: 选股器统一修正 PR + 周期股 5 年 ROE 中位数（P1-2 + Phase 3）
- [x] Task 6: SUSTAINABILITY.md A+H 表述修正（P2-2）
- [x] Task 7: 依赖上限 akshare<2.0 / mootdx<1.0（P1-4）
- [x] Checkpoint 2: 全量回归 — pytest 446 passed (4 deselected) / node 89 pass / verify 一致

## Phase 4: 提示层
- [x] Task 8: 小盘未验证徽标（总市值<100亿）+ 偏弱核实分红性质徽标（screener_render.js / screener.html）— 9/9

## 遗留观察跟进（fix-4 报告）
- [x] Task 9: screener_pr.py::evaluate_pr_batch 对齐修正 PR + 周期股中位数（P1-2 统一收尾）— 58 passed
- [x] 发现（已提交 issue #122）：fill_screener_data.py --finance 实际为空操作；finance_snapshot 靠 init_screener（backtest.db 导入）刷新
