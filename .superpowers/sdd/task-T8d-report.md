# T8d 实施报告：EFFECT 单调 reducer 与独立对账 case

## 结论

T8d 已完成。`EffectReducer` 成为 `RuntimeIntentLog.effect_status` 的唯一写入口，并严格接收 T8a 冻结的十一类
event。transport、typed callback 与 reconciliation 只负责把已持久化事实翻译为 reducer event，不自行推进语义状态。
terminal 永不改写；重复或迟到事实只追加 evidence；矛盾 callback 保留 terminal 并打开独立
`ReconciliationCase`；OPEN case 只能由显式 `RECONCILIATION_RESOLVED` 关闭。

本任务没有实现 T8e lease/fencing、T8f target/credential snapshot、T8g resilience matrix，也没有接入具体 WMS
callback payload、增加兼容映射或执行旧业务数据迁移。

## 最小设计与职责边界

brainstorming 选择“唯一 reducer + 窄 Repository 行锁 + 独立 case + typed bridge”的最小方案：

- `EffectReducerRepository` 只锁定权威 `RuntimeIntentLog` 与当前 OPEN case，并负责新增 case；不承载状态机。
- `EffectReducer` 是唯一语义写者，集中执行事件矩阵、terminal 单调性、case 生命周期与 evidence 追加。
- `EffectTransportBridge` 只消费 T8c 的 `ExternalHttpTransportResult`；`EffectCallbackBridge` 只接受已归一化的 callback
  outcome；`EffectReconciliationBridge` 只产生 OPEN、RESOLVED 与 idempotency conflict event。
- generic `SystemOutboxEngine` 与 workline `OutboxDispatchService` 都在 outbox/attempt evidence 落库后、同一事务提交前
  调用 transport bridge；不存在 WMS 专用 dispatcher 或平行账本。
- 通用 EXTERNAL_HTTP outbox 若没有对应 intent，bridge 按 `require_intent=False` 无副作用返回；已有 capability intent
  必须通过 dispatch key 精确关联，不从 payload 或 correlation 猜测。

未选择把状态机放回 `RuntimeIntentLogRepository`，因为这会混合持久化与业务裁决；也未引入 event-sourcing 框架，
因为 T8d 只需要封闭事件矩阵与可审计 evidence，额外抽象不符合 YAGNI。

## 事件矩阵与单调不变量

- `INTENT_PROPOSED`、`ATTEMPT_STARTED` 只追加 evidence，不改变已存在 intent。
- `TRANSPORT_NOT_SENT` 保持 `PROPOSED`；仅 retry budget 耗尽时进入 `TECHNICAL_FAILED`。
- `TRANSPORT_ACCEPTED` 只推进到 `ACCEPTED`，不会把 transport `SENT` 误写为 capability `COMPLETED`。
- `TRANSPORT_AMBIGUOUS` 将 `PROPOSED/ACCEPTED` 推进为 `UNKNOWN`；transport bridge 随即发出
  `RECONCILIATION_OPENED`，形成 `RECONCILING + OPEN case`。
- 无 OPEN case 时，typed callback 可按矩阵推进 `ACCEPTED/COMPLETED/REJECTED`；duplicate、late callback 只追加
  evidence。
- 矛盾 callback 和 `IDEMPOTENCY_CONFLICT` 保留原状态并创建或更新 OPEN case；已有 terminal 绝不倒退或互换。
- OPEN case 下 ordinary callback/transport 只追加 evidence；只有 `RECONCILIATION_RESOLVED` 可关闭 case，且仅将
  非终态 `RECONCILING` 推进为 `COMPLETED/REJECTED`。
- 没有 OPEN case 的 RESOLVED event fail closed；case 关闭后再次 resolve 同样拒绝。
- intent 与 case evidence 都保留完整事件顺序；`effect_updated_at_ms` 取已有值和 event 时间的最大值，不因乱序事实
  倒退。

代码审计确认 `transition_runtime_intent(...)` 在生产代码中只有 `EffectReducer` 一个调用点；原 Repository
`record_outcome` writer 已删除，已有本地 outcome 入口也改为产生 reducer event。

## ReconciliationCase 与迁移

- 新增 runtime schema 自有 `ReconciliationCase`，生命周期仅为 `OPEN/RESOLVED`，包含 dispatch key、reason、
  evidence history、decision、打开与关闭时间。
- 只建立 `wes_runtime.reconciliation_cases.runtime_intent_log_id` 到同 schema intent 的外键；没有 runtime/biz
  跨 schema FK。
- partial unique index 保证每个 dispatch key 最多一个 OPEN case；状态/关闭时间 CHECK 保证 OPEN 没有
  `resolved_at_ms`，RESOLVED 必须有关闭时间。
- 通过 Alembic generator 创建 revision `c325aab03400`，父 revision 为 T8c 的 `8de7cb4de434`；迁移只建表、
  约束与索引，没有 UPDATE、INSERT、backfill 或业务迁移。
- 显式短外键名避免 PostgreSQL 63 字符限制；CHECK 名使用 `op.f(...)` 固定 naming convention 后的物理名称。

## TDD 与验证结果

- 首轮 RED：新增 61 项 reducer 表驱动/property 测试，结果为 `60 failed, 1 passed`；实现 reducer、case 与 bridge
  后为 `61 passed`。
- 第二轮 RED：唯一 writer、两个 dispatcher transport bridge、local outcome bridge 共 `50 failed, 81 passed`；
  接线后为 `131 passed`。
- schema contract 初始 `3 failed`，generator migration 与模块导出完成后为 `3 passed`。
- 扩大 EFFECT/outbox/transport/attempt 回归：`247 passed`。旧纯内存 dispatcher fixture 显式注入 no-op bridge，
  生产路径没有降低数据库或 reducer 要求。
- 测试拓扑守卫：`6 passed`；显式 collect-only：`3734 tests collected`。
- Docker PostgreSQL reducer/case/partial unique/FK/CHECK 集成场景：`1 passed`。
- Docker PostgreSQL migration：全量 `upgrade head` 成功，降级到 `8de7cb4de434` 后新表不存在，再次升级成功，
  最终为 `c325aab03400 (head)`；物理约束名与模型合同一致。
- `./scripts/git-quality-gate.sh --profile quality`：通过（Ruff format/check、Bandit、348 项 runtime contract
  guardrails、import-linter、architecture guardrails 与测试拓扑均通过）。
- `git diff --check`：通过。

## 影响分析与提交边界

写前 GitNexus impact：`RuntimeIntentLogRepository` 为 MEDIUM（7 个直接、75 个总上游影响）；
`SystemCapabilityIntentService`、`SystemOutboxEngine`、`OutboxDispatchService` 与原 `record_outcome` 为 LOW；新建
reducer/case/bridge 符号尚未进入索引，返回 UNKNOWN/0。测试共享 helper `_service` 为 HIGH（27 个直接测试调用），
已在修改前报告，并用依赖注入保持既有测试语义。生产符号写前分析没有 HIGH/CRITICAL。

staged `gitnexus_detect_changes` 覆盖 22 个文件、41 个已索引 symbols、9 条 affected processes，汇总风险为 HIGH；
风险来自 generic/workline 两个共享 `dispatch` 入口的跨社区传播。staged diff 复核确认只在 EXTERNAL_HTTP typed
result 的 outbox/attempt evidence 持久化后增加 reducer bridge，DEVICE_COMMAND、INTERNAL_SIGNAL 与普通业务分支
语义未改；247 项相关回归、Docker PostgreSQL、348 项 runtime guardrails 与完整 quality profile 已覆盖该风险。

提交范围只包含 T8d 实现、generator migration、测试与本报告，不包含用户维护的 `AGENTS.md`、`CLAUDE.md`。
