# T8c 实施报告：统一 EXTERNAL_HTTP typed transport result

## 结论

T8c 已完成。所有 `EXTERNAL_HTTP` sender 已收敛到唯一封闭 typed result，不再存在 bool sender 合同或兼容
fallback。transport 明确区分 `NOT_SENT`、`ACCEPTED` 与 `AMBIGUOUS`，并同时记录 phase、HTTP/协议结果、
安全重试结论和错误摘要。通用 `SystemOutboxEngine` 与 workline dispatcher 使用相同终态规则，所有外部 HTTP
attempt 都会保存 typed evidence。若 typed result 的 outbox/attempt 证据或提交失败，两条 dispatcher 会回滚原事务，
再以独立短事务强制收口 `UNKNOWN`，避免已产生或可能产生的外部副作用被自动重放。

本任务没有实现 capability semantic reducer、callback/reconciliation 生命周期、lease/fencing 新协议、credential
snapshot、WMS 专用 dispatcher 或旧数据迁移；也没有写入 `RuntimeIntentLog` 语义状态。

## 合同与状态映射

- 新增 `ExternalHttpTransportResult`、`ExternalHttpTransportOutcome`、`ExternalHttpTransportPhase` 与
  `ExternalHttpProtocolResult`；结果对象冻结且校验组合不变量。
- HTTP 2xx：`ACCEPTED + protocol ACCEPTED`，outbox/attempt 均进入 `SENT`。
- HTTP 3xx/4xx：请求已被远端明确拒绝，因此是 `ACCEPTED + protocol REJECTED`；transport outbox/attempt 仍进入
  `SENT`，不把 `SENT` 误当 capability 语义成功。
- HTTP 5xx：语义不明，归类 `AMBIGUOUS + protocol UNKNOWN`；outbox/attempt 进入 `UNKNOWN`，禁止自动重放。
- connect timeout、write/read timeout、connection reset/read/write error 与未知 sender 异常：归类 `AMBIGUOUS`，
  进入 `UNKNOWN` 且不重试。
- 只有能证明请求未离开本地边界的 `ConnectError` 才是可安全重试的 `NOT_SENT`，进入现有有界
  `RETRY_WAIT`；非法 URL、endpoint/canonical 准备失败是不可重试 `NOT_SENT`，直接进入 `FAILED`。
- sender 返回非 typed 对象时 fail closed 为 `AMBIGUOUS`，没有 bool fallback。
- EXTERNAL_HTTP 沙箱出口返回 `ACCEPTED + SANDBOX + protocol NOT_AVAILABLE`，不伪造 HTTP 状态码。
- `AMBIGUOUS + RESPONSE_RECEIVED` 必须携带 `protocol UNKNOWN` 和非空 HTTP 状态码；响应前 AMBIGUOUS phase
  必须是 `protocol NOT_AVAILABLE` 且不得携带 HTTP 状态码。`SANDBOX + AMBIGUOUS` 没有实际语义，构造时直接拒绝。

## 证据持久化失败收口

- typed result 的 outbox 终态、attempt evidence 与 commit 保持原子写入；任一步骤抛错都会先回滚当前会话。
- 回滚后通过独立数据库会话锁定原 outbox，并用专用 Repository 方法强制写入 `UNKNOWN`、清除 retry/block 信息，
  同时保存不含请求正文的最小错误证据。
- 专用恢复允许覆盖 `DISPATCHING/RETRY_WAIT/SENT/FAILED/UNKNOWN`：原 commit 可能已在数据库端成功，仅是客户端
  未收到确认，因此常规状态边不足以表达这类提交歧义。
- 隔离恢复本身失败时抛出 `ExternalHttpEvidenceRecoveryError`；workline dispatcher 显式让该异常穿透，绝不落入
  通用 `mark_as_failed` 路径。
- `mark_as_dispatching` 是 sender 前的最后一道领取 fence：过期的 EXTERNAL_HTTP `DISPATCHING` lease 先转为
  `UNKNOWN` 并记录 `STALE_EXTERNAL_HTTP_DISPATCH_LEASE_EXPIRED`，随后返回未领取；DEVICE_COMMAND 与
  INTERNAL_SIGNAL 继续沿用原 stale lease reclaim 语义。
- stale HTTP claim fence 显式写入 `next_retry_at = None`，不依赖通用 block 清理器的附带行为，与
  `mark_as_unknown` 的不可重试终态字段保持一致。
- 若该 UNKNOWN flush/commit 仍失败，当前 worker 会在 sender 前退出；下一 worker 继续执行相同 fence，绝不会把旧
  EXTERNAL_HTTP lease 直接重发。
- generic 与 workline 失败注入回归均验证 sender 只调用一次；真实 Repository 双 worker 回归进一步覆盖“隔离恢复
  失败 → lease 过期 → 下一 worker scan/claim”，第二次 sender 调用保持为零。

## Attempt evidence 与迁移

- `WorklineDispatchAttempt` 新增 nullable `transport_outcome`、`transport_phase`、`protocol_result`、
  `safe_to_retry`、`http_status_code`；非 HTTP 历史 attempt 保持为空。
- typed finalize 同时写入结构化列和完整 `response_json.transport`，并保存实际 outbox finalization（包括
  `sent`、`retry_wait`、`failed`、`unknown` 或 `fenced`）。
- 通过 Alembic 生成器创建 revision `8de7cb4de434`，增加五列、三项封闭枚举约束和两个索引；没有 backfill。
- 本地 PostgreSQL 已验证 upgrade、downgrade、再次 upgrade，最终版本为 `8de7cb4de434 (head)`。
- PostgreSQL attempt evidence 往返集成测试通过；`alembic check` 仍报告仓库既有大范围 schema 漂移，但过滤
  T8c 五列、约束与索引后没有新增 drift。

## 验证结果

- P1/P2 定向回归：typed 不变量、Repository 终态覆盖、generic/workline 证据失败及双 worker lease 场景全部通过。
- outbox/transport/attempt 相关回归：`82 passed`。
- 测试拓扑守卫：`6 passed`；显式 collect-only：`3663 tests collected`。
- T8c typed result、双 dispatcher、attempt evidence 与 EFFECT 相关回归：`74 passed`。
- T8c 最终核心复验（含 SANDBOX evidence）：`24 passed`。
- PostgreSQL typed attempt evidence 集成测试：`1 passed`。
- `tests/sys` 加 dispatcher observability：`30 passed`。
- `./scripts/git-quality-gate.sh --profile quality`：通过（Ruff format/check、Bandit、runtime guardrails、
  import-linter、architecture guardrails、测试拓扑均通过）。
- 默认快速全集：`3635 passed, 5 skipped, 3 failed`。3 个失败与 T8b 已记录基线完全一致：cleanup matrix CSV、
  legacy matrix CSV 和 northbound inventory 的 `notify_pkg_binding` 清单漂移；与 T8c transport/attempt 路径无关。
- `rg` 审计确认代码和测试中的 EXTERNAL_HTTP bool sender 合同归零。

## 影响分析与提交边界

GitNexus 对 `WorklineDispatchAttemptBase/WorklineDispatchAttempt` 报告 MEDIUM，直接依赖分别为 7/6；
`SystemOutboxRepository` 与 `dispatch_external_http` 为 MEDIUM。本轮 P1/P2 修复中，`SystemOutboxRepository` 为
MEDIUM（72 个上游影响、6 个直接调用方），`SystemOutboxEngine` 与 generic/workline dispatch 路径为 LOW。
dispatcher、attempt service 与其方法为 LOW。
新建的 transport 类型尚未进入索引，impact 查询返回 UNKNOWN/0；逐符号写前分析没有 HIGH/CRITICAL。

提交前 staged `gitnexus_detect_changes` 覆盖 19 个文件、72 个 changed symbols、9 个 affected processes，汇总风险为
HIGH；风险来自共享 workline/SystemOutbox `dispatch` 流程的跨社区影响面。设备分支语义未改，受影响 dispatcher、
安全检查与设备解析路径已由全量回归、相关 74 项回归和 quality profile 覆盖。

本轮 P1/P2 修复的 staged 检测覆盖 10 个文件、23 个已索引符号、9 个 affected processes，汇总风险同样为 HIGH；
原因仍是 generic/workline 两个共享 `dispatch` 入口的跨社区传播。实际实现只在 typed EXTERNAL_HTTP 分支增加证据
失败收口，设备分支未改；相关 76 项回归、348 项 runtime contract guardrails 与完整 quality profile 均通过。

本轮领取侧补强的写前 impact：`mark_as_dispatching` 为 LOW（1 个直接调用方、1 条受影响流程）；现有 stale lease
回归测试为 LOW；新提交后尚未重建索引的 `ExternalHttpTransportResult/__post_init__` 为 UNKNOWN/0。无
HIGH/CRITICAL，非 HTTP reclaim 语义由 DEVICE_COMMAND、INTERNAL_SIGNAL 双参数回归覆盖。

末轮 staged `gitnexus_detect_changes` 覆盖 7 个文件、5 个已索引符号、1 个 affected process，汇总风险为 MEDIUM；
变更边界与预期一致，仅涉及 transport 不变量、SystemOutbox claim fence、相应测试和本报告。

P3 显式 retry 时钟清理的写前 impact 为 LOW（1 个直接调用方、1 条受影响流程）；定向测试隔离通用清理器副作用，
验证 claim fence 自身拥有 `UNKNOWN` 的 `next_retry_at = None` 不变量。

P3 staged `gitnexus_detect_changes` 覆盖 3 个文件、2 个已索引符号、1 个 affected process，汇总风险为 MEDIUM；
GitNexus 将单行仓储 hunk 映射到相邻的 `claim_blocked_resource_wait_for_dispatch`，staged diff 复核确认实际变更只位于
`mark_as_dispatching` 的 stale EXTERNAL_HTTP claim fence，提交边界与预期一致。

提交范围只包含 T8c 计划、实现、迁移、测试与本报告，不包含用户维护的 `AGENTS.md`、`CLAUDE.md`。
