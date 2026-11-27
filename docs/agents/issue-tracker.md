# Issue tracker: GitHub

Issues 和 PRD 存放在 GitHub Issues（`flyshub/dividend-calculator`），通过 `gh` CLI 执行所有操作。

## 常用命令

- **创建 Issue**：`gh issue create --title "..." --body "..."`。多行正文使用 heredoc。
- **查看 Issue**：`gh issue view <number> --comments`，通过 `jq` 过滤评论并获取标签。
- **列出 Issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，可配合 `--label` 和 `--state` 过滤。
- **评论 Issue**：`gh issue comment <number> --body "..."`
- **添加/移除标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **关闭 Issue**：`gh issue close <number> --comment "..."`

仓库信息从 `git remote -v` 自动推断 — `gh` 在 clone 目录内执行时会自动识别。

## 子工单与依赖（tracer bullet 拆分）

GitHub 原生支持，但只能走 REST API，且**数字参数必须用 `-F`（类型化），不能用 `-f`（字符串）**——用 `-f` 会静默失败或 422：

```bash
DB_ID=$(gh api repos/<owner>/<repo>/issues/<number> --jq .id)   # 注意：是数据库 id，不是 issue 号

# 把 #158 挂成 #156 的子工单
gh api -X POST repos/<owner>/<repo>/issues/156/sub_issues -F sub_issue_id=$DB_ID

# 声明阻塞：#158 被 #157 阻塞（先取 #157 的 DB_ID）
gh api -X POST repos/<owner>/<repo>/issues/158/dependencies/blocked_by -F issue_id=<157的DB_ID>
```

- 顺序：先按依赖顺序建 issue，再挂子工单与阻塞边（后者要引用已存在的 issue）。
- 检查：`gh api repos/<owner>/<repo>/issues/<n>/sub_issues --jq '.[].number'`、`.../dependencies/blocked_by --jq '.[].number'`。
- 用原生边时，工单正文不要再写「Blocked by」小节，避免两处不一致。

## 正文含反引号时

`gh issue create/comment/edit --body "..."` 的双引号串里出现反引号、`$`、`!` 会被 shell 展开（本次实测反引号触发命令替换，正文被吞一段）。一律改用 `--body-file <path>` 或 heredoc 写正文。

## 当技能说"发布到事务跟踪器"

创建 GitHub Issue。

## 当技能说"获取相关工单"

执行 `gh issue view <number> --comments`。
