# Phase 8 双远端与状态真源治理计划

**状态：** IN_PROGRESS

**目标：** 在不覆盖现有文档工作、不重写 Git 历史且不误触现场部署的前提下，统一 GitHub/GitLab `develop` 历史、Phase 8
当前状态真源和后端 RC 不可变证据。

## 1. 冻结基线

| 对象 | 冻结值 |
| --- | --- |
| GitHub `origin/develop` | `bda2079d523984f25265c113b2fb213429da40f0` |
| GitLab `gitlab/develop` | `f51677b62f5da906d4b60fa5a528d04692aff7a2` |
| 本地 RC 关闭记录 | `6e27227e50502c2bc5e681942c34030f8be1244b` |
| 已保护文档提交 | `bec1ccd7d4535f2d967c6a6263c7ced8eaec437a` |
| GitHub 保护分支 | `codex/docs-workline-task-return-boundaries` |
| 治理分支 | `codex/governance-phase8-remote-convergence`，从 `gitlab/develop` 创建 |

GitLab 最终 tree 相对 GitHub 只包含 CI、测试治理、发布文档和归档索引变化；没有 `src/`、`deployment/`、`workline_plugins/`、
`packages/` 或 migration 的净生产差异。不得按提交时间、标题或本地 ahead/behind 数量推断真源。

## 2. 永久治理规则

1. GitHub `origin/develop` 是唯一评审与合入真源。
2. GitLab `gitlab/develop` 只接收已经合入 GitHub 的精确 Commit，并通过 webhook 触发 Jenkins。
3. GitLab 发布前必须证明当前 GitLab HEAD 是目标 GitHub SHA 的祖先；不满足时停止，禁止 force push 和聚合 cherry-pick。
4. GitHub Merge 与 GitLab Push 分别取得授权；GitHub PR、GitLab MR 和 Jenkins 手工构建不发布镜像。
5. GitLab `develop` PUSH 必须校验 webhook `gitlabBefore` → `gitlabAfter` 为 fast-forward，并以前一 SHA 为基线运行 Mock
   合同与 selector 选中的 HEAVY；字段缺失、HEAD 不匹配或 ancestry 不成立时不得发布。
6. 发布 Job 必须是由 GitLab webhook 触发的普通 Pipeline，并使用 per-project Secret Token 认证；Poll SCM、Multibranch Pipeline
   和手工构建均不得替代发布触发。
7. RC 和现场选版只使用 immutable tag、manifest digest 与 OCI revision；`develop` channel 不是验收证据。
8. `docs/integration/rough-sorter-joint-acceptance.md` 是 Phase 8 当前状态、RC 证据和外部验收边界的唯一真源；实施计划只保存历史和门禁。

## 3. 执行顺序

- [x] 冻结三方 refs、dirty 路径与 staged 指纹。
- [x] 将原 staged 文档提交到 GitHub 保护分支，不创建 PR、不合入 `develop`。
- [x] 从 `gitlab/develop@f51677b6` 创建独立治理 worktree，并验证 Jenkins 发布边界基线。
- [x] 审计 GitLab-only 历史和最终 tree，确认没有净生产代码或 migration 差异。
- [x] 重放 `6e27227e` 的 RC 关闭语义，统一主计划、实施历史和验收真源。
- [x] 更新 Jenkins 文档，冻结 GitHub 真源、GitLab 发布镜像和 fast-forward 门禁。
- [x] 完成纯文档检查、Jenkins 聚焦测试、HEAVY selector 与 staged GitNexus 范围检测。
- [x] 修复 GitLab `develop` PUSH 的 previous-SHA、Mock/HEAVY 与 webhook 认证门禁，并用 Jenkins/selector 合同测试锁定。
- [x] 提交治理分支并推送 GitHub；经单独授权已创建历史汇合 PR #120。
- [ ] GitHub 合入后确认 GitLab HEAD 是 merge SHA 的祖先，再单独授权 fast-forward 推送 GitLab。
- [ ] 新规范 `develop` 稳定后，只把 `bec1ccd7` 的文档提交重放到新分支并独立评审。

## 4. 退出标准

- `origin/develop` 与 `gitlab/develop` 指向同一 Commit；整个过程没有 force push。
- 当前文档不再出现 `SOURCE_READY_IMAGE_NOT_PUBLISHED`、`RC 镜像尚未发布` 或等待 GitLab Push 等过期结论。
- Phase 8 明确为后端 RC `CLOSED`，供应商一致性、现场联调和业务验收保持 `NOT RUN`。
- 不可变镜像 `88-f51677b` 及其 digest/OCI revision 可独立追溯；`develop` channel 不作为关闭证据。
- 保护分支 `codex/docs-workline-task-return-boundaries@bec1ccd7` 保持不变，原文档修改可以在治理完成后安全重放。
- 未经独立授权不创建 PR、不 Merge、不 Push GitLab、不触发新镜像或部署。
