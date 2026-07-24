# T8g 实施报告：EFFECT 发送与证据边界 crash matrix

## 结论

T8g 已在现有 generic `SystemOutboxEngine` 与 workline `OutboxDispatchService` 中建立相同的确定性命名故障边界，
并以真实 PostgreSQL 覆盖 claim、外部 send、outbox/attempt evidence、reducer evidence、commit、lease loss 与
unexpected exception。测试模拟的进程崩溃使用 `BaseException` 绕过业务异常处理；重启 worker 只消费现有
SystemOutbox/T8e lease fence，不存在第二 dispatcher、ledger 或 retry engine。

本任务没有修改 T8a 状态集合、T8c typed transport 分类、T8d reducer/case 语义、T8e lease/fencing 或 T8f frozen
binding；没有 schema/migration/backfill，也没有实施具体 WMS capability、Jenkins、GitLab、T11 或业务迁移。

## 最小设计与故障注入边界

brainstorming 比较了三种方案：A）构造器注入、默认禁用的共享命名 hook；B）测试 monkeypatch dispatcher 内部方法；
C）全局或数据库 fault registry。最终选择 A：它能精确命中 reducer 前后和 commit 前后，又不引入全局状态或生产配置
开关。B 无法稳定表达全部事务边界；C 会扩大生产控制面并形成新的运行时机制，不符合 YAGNI。

`ExternalHttpDispatchFaultPoint` 固定十一点：`BEFORE_CLAIM`、`AFTER_CLAIM_COMMIT`、`BEFORE_SEND`、
`AFTER_SEND`、outbox evidence 前后、attempt evidence 前后、reducer evidence 前后，以及
`AFTER_EVIDENCE_COMMIT`。两个 dispatcher 通过相同 helper 顺序发出这些点；production singleton 不注入 hook，
没有环境变量、全局 registry 或动态启用面。hook 只接收 point 与 outbox 对象，不接收 secret、header、canonical body
或签名。

## PostgreSQL crash matrix

| 崩溃边界 | 重启前 durable 状态 | 第二 worker 行为 | 最终状态与证据 | 外部发送次数 |
| --- | --- | --- | --- | --- |
| `BEFORE_CLAIM` | outbox 仍为 `NEW` | 正常 claim/send | outbox/attempt `SENT`，intent `ACCEPTED` | 1 |
| `AFTER_CLAIM_COMMIT` | outbox/attempt `DISPATCHING` | expiry 后执行 T8e fence | outbox/attempt `UNKNOWN`，intent `RECONCILING`，OPEN case | 0 |
| `BEFORE_SEND` | outbox/attempt `DISPATCHING` | expiry 后执行 T8e fence | 同上；没有盲目假设本地未发送 | 0 |
| `AFTER_SEND` 至 reducer evidence 前后 | claim 已提交，终态/evidence 事务未提交 | 原事务 rollback，expiry 后执行 T8e fence | 三账本原子收口 UNKNOWN，OPEN case | 1 |
| `AFTER_EVIDENCE_COMMIT` | outbox/attempt `SENT`，transport evidence 已提交 | 无可领取消息 | intent `ACCEPTED`，无 OPEN case | 1 |

每个 UNKNOWN 场景都断言 `next_retry_at=None`、attempt `safe_to_retry=False`、lease-loss evidence、唯一 attempt、
`TRANSPORT_AMBIGUOUS → RECONCILIATION_OPENED` 顺序和 OPEN case reason；第二 worker 的 sender 调用保持为零。
只有现有 typed `NOT_SENT(safe_to_retry=true)` 路径允许进入有界 `RETRY_WAIT`，crash 不凭测试 hook 位置推导生产
送达事实。

独立 lease-loss race 在 sender 返回 `ACCEPTED` 前由第二会话推进同源 outbox/attempt expiry 并执行 T8e fence；旧
worker 随后的 `mark_as_sent` 被 owner/expiry 条件拒绝，统计为 fenced/skipped，外部仍只调用一次。unexpected sender
`RuntimeError` 继续由 T8c 映射为 `AMBIGUOUS`，同事务落为 `UNKNOWN + RECONCILING/OPEN`，第二 worker 不发送；异常
原文中的测试 secret 未进入 outbox、attempt 或 transport evidence。

## TDD 与验证

- RED：generic/workline 两个顺序合同先失败于 constructor 不支持 `external_http_fault_hook`（`2 failed`）；最小接线
  后同组 `2 passed`，两份完整 dispatcher 文件 `18 passed`。
- PostgreSQL 首轮 RED 暴露 fake clock 只推进 outbox expiry、未同步推进同源 attempt expiry；T8e 正确拒绝越 fence
  改写未过期 attempt。修正测试时钟后 crash matrix、unexpected exception 与 lease-loss race 为 `3 passed`。
- EFFECT/outbox/reducer/lease 定向快速回归：`300 passed`。
- sys、workline runtime、runtime orchestration、system capability contracts 与 rack 扩大回归：`1593 passed`，仅有
  5 条既有 deprecation warning。
- Docker PostgreSQL 相关集合：`19 passed`，包括本轮 3 项 resilience、T8c attempt evidence、T8e claim/fencing 与
  canonical BYTEA；真实测试使用随机安全前缀临时数据库并在退出时清理。
- 测试拓扑守卫：`6 passed`；定向 Ruff 与 `git diff --check` 通过。
- 显式默认快速收集：`3782 tests collected`。
- `./scripts/git-quality-gate.sh --profile quality` 通过：1020 个文件格式检查、Ruff、Bandit 0 issues、348 项
  runtime contract guardrails、11 项 process naming、import-linter、architecture 0 violations 与测试拓扑均通过。

## 影响分析与提交边界

写前 GitNexus impact 均无 HIGH/CRITICAL：`SystemOutboxEngine` 为 LOW（3 个直接、10 个总上游）；其
`dispatch/dispatch_single/finalize/reducer record` 为 LOW。`OutboxDispatchService` 为 LOW（2 个直接、6 个总上游）；
其 `dispatch/_dispatch_single/finalize` 均为 LOW，最多 3 个直接调用与 1 条受影响流程。新增 fault 类型尚未进入索引。

提交范围只包含共享命名 hook、两个现有 dispatcher 的默认禁用接线、快速顺序合同、PostgreSQL resilience matrix 与
本报告；明确排除用户维护的 `AGENTS.md`、`CLAUDE.md`。

提交前 GitNexus 增量刷新发现本 worktree 的可再生成 `variable_fts` 索引损坏；按 CLI 维护规范仅清理并重建
`.gitnexus`，没有改写仓库入口文件。最终 staged detect 为 `7 files / 65 symbols / 5 affected processes / MEDIUM`；
受影响流程均为既有 `Dispatch` 流程，文件范围与本任务一致。
