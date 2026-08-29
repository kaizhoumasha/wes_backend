# Runtime Observability Contract

> 状态：`implementation_baseline`。本合同只约束当前仍在发射的观测信号；目标执行对象以 2026-07-31 顶层 SPEC 为准。

本文档锁定 runtime / callback / device / WMS 的稳定观测口径。临时 debug log 不能替代本合同中的 span、metric、log event 和 evidence。

## Stable Signals

| Signal | Type | Required attributes |
| --- | --- | --- |
| `callback.normalize` | span + metric + log event | `trace_id`, `correlation_id`, `provider_code`, `source_event_id` |
| `runtime_inbox.claim_batch` | metric | `claimed_count`, `duration_ms` |
| `runtime_inbox.processing` | span + metric | `inbox_id`, `duration_ms`, `outcome` |
| `runtime_inbox.lease_reclaim` | metric | `reclaimed_count` |
| `runtime_inbox.fencing_reject` | metric + log event | `inbox_id`, `target_state` |
| `runtime_inbox.resource_wait` | metric | `inbox_id` |
| `runtime_inbox.dead_letter` | metric + log event | `inbox_id` |
| `runtime_intent.dispatch` | span + metric + log event | `trace_id`, `correlation_id`, `provider_code`, `operation_kind` |
| `device_command.ack` | span + metric | `trace_id`, `correlation_id`, `command_code`, `provider_code`, `ack_age_ms` |
| `device_command.dispatch_policy` | span + metric | `trace_id`, `correlation_id`, `command_code`, `device_code`, `provider_code`, `policy_decision`, `reason`, `dispatch_allowed`, `runtime_hold_required` |
| `device_command.result` | span + metric | `trace_id`, `correlation_id`, `command_code`, `source_event_id` |
| `wms_breaker.transition` | metric + log event | `trace_id`, `provider_code`, `operation_kind`, `breaker_state` |
| `wms_evidence.persistence_failure` | metric + log event | `trace_id`, `provider_code`, `operation_kind`, `evidence_key`, `reason_code` |
| `northbound.dispatch.health` | metric + log event | backlog、active lease、UNKNOWN、oldest age、rate-limit、pause、lease contention/loss 数值 |
| `northbound.credential.resolve` | metric + log event | `provider_kind`, `outcome`, `sample_count` |
| `northbound.operation.query_inventory` | span + metric + log event | provider profile、outcome、latency、trace/correlation/evidence、stage |
| `northbound.operation.confirm_inbound` | span + metric + log event | provider profile、outcome、latency、trace/correlation/evidence、stage |
| `northbound.operation.notify_pkg_binding` | span + metric + log event | provider profile、outcome、latency、trace/correlation/evidence、stage |
| `northbound.operation.request_rack_supply` | span + metric + log event | provider profile、outcome、latency、trace/correlation/evidence、stage |
| `wms_effect.submit` | span + metric + log event | operation、submit outcome、latency、retry count、dispatch key hash |
| `wms_effect.status_query` | span + metric + log event | operation、五态、latency、retry count、age、dispatch key hash |
| `wms_effect.status_backlog` | metric + log event | backlog、max age、claimed count、batch duration |
| `wms_effect.status_backpressure` | span + metric + log event | operation、backpressure outcome、Retry-After、actual backoff、dispatch key hash；已知真实熔断状态可选 |
| `wms_effect.recovery` | span + metric + log event | operation、recovery outcome、age、dispatch key hash |
| `wms_effect.callback_hint` | span + metric + log event | callback outcome；已校验提示可附 operation 与 dispatch key hash |

## Exact Allow-list 与 Metric Projection

- 所有 signal 都声明 `allowed_attributes`、不可覆盖的固定属性、闭集 label 值和非负有限数值测量；未知 signal、额外属性、固定属性覆盖、非标量、非法枚举、NaN/无穷/负测量均 fail-closed。
- `payload`、`canonical_payload`、`header`、`Authorization`、`signature`、`secret`、`token`、`password`、`credential_reference` 等字段名禁止进入事件；字符串值中出现 `secret://`、Bearer、签名或 Authorization 痕迹同样拒绝。
- Span/log 接收完整的已验证属性；metric 只接收 `metric_label_attributes ∪ metric_measurement_attributes`。`trace_id`、`correlation_id`、`evidence_ref`、业务键、bucket、tenant、用户 ID 不得成为 metric label。
- 所有 metric 固定带 `capability_identity` 与 `policy_version=northbound-observability.v1`。通用北向 operation metric 只允许以下标签：
  `capability_identity`、`operation_identity`、`provider_profile_identity`、`outcome`、`policy_version`。
- 每个部署只有一个 active `provider_profile_identity`；它是部署级低基数常量，不能由 payload 选择，也不能建立
  profile catalog/router/fallback。`outcome` 仅允许
  `SUCCESS`、`BUSINESS_REJECT`、`TECHNICAL_FAILURE`、`CONTRACT_FAILURE`、`UNKNOWN`、`RECONCILING`。
- WMS EFFECT signal 只允许额外使用低基数 `state` 与 `reason_code` 标签；`state` 仅为五态，
  `reason_code` 仅为合同登记的稳定码。`breaker_state` 只允许作为 span/log 的可选诊断属性，
  且必须来自真实熔断器状态，不能为补齐合同而臆造；当前仅 `CIRCUIT_OPEN` 发射 `OPEN`。Submit outcome 仅为
  `ACCEPTED/AMBIGUOUS/NOT_SENT`；callback hint outcome 仅为
  `RECEIVED/REJECTED/DUPLICATE/QUERY_TRIGGERED/ENQUEUE_DEGRADED`。
- 通用北向 operation 数值只包含 `latency_ms`、`sample_count`、`unknown_count`；WMS EFFECT signal 可增加
  `retry_count`、`age_ms`、`backlog_count`、`max_overdue_age_ms`、`max_confirmation_age_ms`、
  `claimed_count`、`duration_ms`、
  `retry_after_ms`、`actual_backoff_ms`。所有值必须非负有限；不得携带 bucket、关联键或业务 ID。

## WMS EFFECT 可执行采集口径

以下六类名称已进入 `RuntimeObservabilityRegistry` allow-list，并由 submit、status worker、reconciliation 与
callback hint 边界真实发射。沿用既有 `RuntimeOpenTelemetryBridge` 和
`northbound-operation-day1` 运营面，不建设第二套看板或 exporter 框架。

| Signal | 观测事实 | 发射边界 |
| --- | --- | --- |
| `wms_effect.submit` | accepted/ambiguous/not-sent 数量与延迟 | Outbox、Attempt、Reducer evidence 成功提交后 |
| `wms_effect.status_query` | 五态数量、延迟、重试次数和 age | status worker 获得并校验 typed snapshot 后 |
| `wms_effect.status_backlog` | backlog、最大逾期 age、最大确认 age、单批领取量/耗时 | status scanner 单批 claim/执行完成后 |
| `wms_effect.status_backpressure` | 429、`Retry-After`、circuit-open、实际退避 | status query failure 归约并计算下次到期时间后 |
| `wms_effect.recovery` | 超宽限期 `NOT_FOUND`、耗尽、幂等冲突、open reconciliation | recovery/reconciliation 事务提交后 |
| `wms_effect.callback_hint` | 接收、拒绝、重复、触发查询、enqueue 降级 | typed callback router 与持久化 hint 调度边界 |

- Submit 记录 accepted/ambiguous/not-sent 数量与延迟；三类结果只说明 WES 观察到的传输边界，不解释 WMS
  内部是否排队、执行或补偿。
- Status query 记录五态数量、延迟、重试次数和 age；backlog 记录数量、最大 age、单批领取量和批次耗时。
- 429 记录合法 `Retry-After` 与实际退避时长；timeout/5xx 记录闭集 outcome 和实际 backoff；
  circuit-open 仅在已知真实状态时附带 breaker 状态，不保存任意远端 body。
- 恢复/对账口径包含 `NOT_FOUND` 超宽限期、查询耗尽、幂等冲突和 open reconciliation 数量。
- Callback hint 口径包含接收、拒绝、重复、触发查询和 enqueue 降级数量。Callback 不记录或推动业务终态。
- 指标和日志只陈述 WES 可观察的 submit/status/callback 交互事实，禁止推断 WMS 内部状态机、队列或根因。
- `dispatch_key` 只能由统一 helper 投影为 16 位 SHA-256 摘要，且该摘要只进入 span/log，不进入 metric；原值、
  idempotency key、业务 payload 和远端响应体禁止进入观测属性。

## 北向 Operation SLO 与 Trace

- 可执行目录版本为 `northbound-operation-slo.v1`，覆盖静态 registry 的全部 31 项 WMS operation。Provider binding authoring 时缺少目录条目必须阻塞。
- 统一窗口为 30 天，可用性目标为 99.5%，UNKNOWN 比例上限为 0.1%，open reconciliation age 上限为 900 秒；各 operation 的 p95 延迟目标、burn rate 与告警责任人见
  [`northbound-operation-slo-catalog.md`](../operations/northbound-operation-slo-catalog.md)。
- 当前可执行 Trace stage 闭集为：
  `QUERY_EVIDENCE → POLICY_DECISION → RUNTIME_INTENT_LOG → DISPATCH_ATTEMPT → CALLBACK → RECONCILIATION`。
  Status query/callback hint 的专用 signal 不得擅自发送未登记的 `STATUS_QUERY/CALLBACK_HINT` stage；若未来新增，
  必须先修改 `NorthboundTraceStage` 与 registry 合同测试。
- QUERY 在 typed evidence 落库后发射；EFFECT submit 在 Outbox、Attempt、Reducer evidence 成功提交后发射。
  后续 callback/reconciliation 复用既有稳定 correlation/evidence 锚点，禁止从 payload 推导追踪或权限。

## Attribute Rules

- `trace_id`：跨 API、inbox、intent、device command 和 WMS evidence 的主追踪标识；legacy 设备结果缺少 trace 时，`DeviceCommand RESULT` 可复用稳定 `source_event_id` 作为 fallback。
- `correlation_id`：优先引用 `ExecutionCorrelation.correlation_id`，跨域引用不得使用 runtime session FK；`RuntimeInbox claim` 尚未完成 correlation 解析时允许使用 `session:{id}` / `workline:{id}` / `device:{id}` / claim bucket / `inbox:{id}` 作为稳定 fallback；`RuntimeIntent dispatch` 尚未完成 correlation 解析时允许使用 `operation_key` / `dispatch_key` / `outbox:{id}` 作为稳定 fallback；`callback.normalize` 允许使用 `dispatch_key` / `command_code` / `exchange_request_code` / `event:{device_code}:{event_type}` / ingress `request_id` 作为稳定 fallback；`DeviceCommand RESULT` 缺少跨域 correlation 时允许使用 `command:{command_code}` 作为稳定 fallback。
- `provider_code`：外部 provider 或内部 capability provider 的稳定编码；external `callback.normalize` 优先使用 `source_system`，缺失时使用 `callback_type` 前缀；device result/event `callback.normalize` 缺省为 `ECS`；RuntimeIntent/Outbox dispatch 优先使用 payload provider 字段，设备命令缺省为 `ECS`，其它外部请求缺省为 `target_code`。
- `operation_kind`：幂等和观测统一操作类型，例如 `callback`, `fulfillment`, `device_command`, `reconciliation`。
- `command_code`：DeviceCommand 的业务命令编码，不使用 device FK 作为观测主键。
- `device_code`：DeviceCommand 目标设备编码，用于派发策略、ACK 和 RESULT 诊断，不使用 device FK 作为观测主键。
- `ack_age_ms`：DeviceCommand 从 `sent_at` 到 `ack_received_at` 的耗时毫秒数，用于 ACK age SLO 和现场设备链路诊断。
- `policy_decision` / `reason`：DeviceDispatchPolicy 的稳定决策和原因，例如 `ALLOW_DISPATCH`、`WAIT_FOR_IDLE`、`RETRY_STATUS_PROBE`、`CREATE_RUNTIME_HOLD` 与 `DEVICE_BUSY`。
- `dispatch_allowed` / `runtime_hold_required`：DeviceDispatchPolicy 决策是否允许本轮派发，以及是否要求 RuntimeHold。
- `source_event_id`：外部 callback / event 的原始事件标识；`callback.normalize` 缺少原始事件 ID 时允许使用 ingress `request_id` 作为稳定 fallback；legacy 设备结果缺少事件 ID 时，`DeviceCommand RESULT` 可使用 `command_result:{command_code}:{finish_time}` 作为稳定 fallback。
- `inbox_id`：RuntimeInbox 持久化消息主键，用于定位 processor、fencing、RESOURCE_WAIT 和 dead-letter 边界。
- `PRE_CUTOVER_AUDIT_ONLY`：切换前缺少 canonical payload 的 audit-only 终态；不可 claim、retry 或 replay，
  不得计入可行动 `runtime_inbox.dead_letter` 指标。
- `claimed_count` / `duration_ms`：单次 Celery 批次实际 claim 数量与累计 claim SQL + commit 耗时；不把批量 SQL 延迟按行数摊薄。
- `reclaimed_count`：本批次事务提交后实际回收的 stale lease 数量。
- `outcome`：单条 processing 的稳定结果分类：`success` / `failed` / `skipped` / `resource_wait` / `timeout` / `error`。
- `target_state`：旧 processor token 被 fencing 拒绝时尝试写入的目标状态。
- `evidence_key`：WMS evidence 幂等键，用于定位失败留痕尝试。
- `reason_code`：稳定失败原因码，例如 `WMS_EVIDENCE_PERSISTENCE_FAILED`。

## Instrumentation Binding

- `RuntimeObservabilityRegistry.emit()` 是当前 Python 运行时的稳定事件发射入口；所有 adapter 必须先通过 required attributes 校验，再转成实际 metric/log/span。
- `RuntimeOpenTelemetryBridge` 是 registry observer 到 OpenTelemetry-style exporter 的无依赖桥接层；span/log 使用完整已验证属性，metric 只使用低基数投影，不允许 exporter 绕过 registry 直接消费临时字段。
- 当前项目不提供同步 OpenTelemetry HTTP backend；registry、稳定 signal 与 `RuntimeOpenTelemetryBridge` 仅定义内部观测边界，具体 exporter 必须另行评审后在桥接层之后接入。
- Callback ingress 在 external normalize allow-list 校验、device result 命令锚点解析、device event 入库 trace 解析完成后发出 `callback.normalize`；观测发射失败不得影响 callback ACK、落库或业务编排。
- RuntimeInbox Celery worker 聚合本批次 claim 调用，在 claim/reclaim 事务提交后发出 `runtime_inbox.claim_batch` 与 `runtime_inbox.lease_reclaim`；不得从 repository 热路径逐条同步发射。
- RuntimeInbox processor 为每条已 claim 消息发出 `runtime_inbox.processing`；fenced terminal 未命中、RESOURCE_WAIT 与 DEAD_LETTER 分别发出对应稳定 signal。观测发射失败不得回滚 Inbox 事务或改变 worker 结果。
- Workline `OutboxDispatchService._dispatch_single()` 进入设备、HTTP 或内部信号派发出口时发出 `runtime_intent.dispatch`；观测发射失败不得阻塞 outbox 派发或改变终态更新。
- `DeviceCommandGateway.dispatch()` 在本地 `DeviceDispatchPolicy` 准入决策后发出 `device_command.dispatch_policy`；观测发射失败不得改变派发、重试、等待或 HOLD 决策。
- `DeviceCommandService.handle_callback_result()` 在设备结果被接受并更新命令终态后发出 `device_command.result`；观测发射失败不得回滚或阻塞 callback result 处理。
- WMS breaker OPEN/HALF_OPEN/CLOSED 状态变化使用 `wms_breaker.transition`；typed port 必须从请求 `trace_id` 透传，不能在缺失 trace 时伪造追踪标识。
- WMS 成功响应后的本地 evidence/breaker 留痕失败使用 `wms_evidence.persistence_failure`；该事件必须保留原始 `trace_id` 和 `evidence_key`，供系统诊断而非业务 HOLD。
- `northbound.dispatch.health` 在 claim 扫描事务完成后发射平台级队列/lease/rate-limit 摘要；观测失败不得改变公平调度或 lease/fencing 决策。
- 北向 QUERY transport 与 EXTERNAL_HTTP outbox delivery 分别在 typed evidence / typed result 固化后发射 operation signal；观测失败不得改变 delivery、retry、UNKNOWN 或 reconciliation 语义。
- WMS EFFECT 六类 signal 统一经薄 projection/helper 补齐静态 operation、闭集 outcome/state、数值测量与
  dispatch key hash；helper 或 exporter 故障只写脱敏 warning，不改变 transport result、callback ACK、status
  事务、重试到期时间或 reconciliation 结果。
- Callback best-effort enqueue 失败发射
  `wms_effect.callback_hint{outcome=ENQUEUE_DEGRADED}`；持久化到期调度和 scanner 仍是恢复真源，指标不得替代恢复机制。
- 凭据 Provider 统一由审计 wrapper 包裹，只允许发射闭集 `provider_kind/outcome`；凭据 ref、secret material、header 与异常文本不得进入日志或指标。
- 具体 backend（Jaeger / Tempo / SkyWalking 等）必须通过 `RuntimeOpenTelemetryBridge` 后方挂载，不能从业务热路径同步发送，也不能新增临时字段替代稳定 attributes。

## Acceptance Evidence

- 严格 PostgreSQL CI 入口为 `Jenkinsfile.backend-ci` 与
  `scripts/run_runtime_inbox_postgresql_acceptance_ci.sh`，只连接当次构建的隔离 PG17。
- Runner `scripts/run_runtime_inbox_postgresql_acceptance.py` 依次执行 migration、processing、两个 crash
  window、benchmark 与 evidence validator；任一步失败返回非零。
- 正式 benchmark evidence 文件名为 `runtime-inbox-claim-benchmark.json`，必须记录完整 commit、
  `dirty=false`、PostgreSQL metadata、样本、指标、阈值、生产 statement fingerprint、query plan 与 verdict。
- JUnit、suite log、脱敏 diagnostic 和 evidence 统一归档在 `reports/runtime-inbox-acceptance/`。

## Prohibitions

- 禁止只写临时日志字段替代 metric/span/evidence。
- 禁止把 provider DTO 原始字段名作为稳定 attribute 名。
- 禁止在安全失败时丢失 `trace_id`、`provider_code` 或 `operation_kind`。
- 禁止把 payload、tenant、用户、业务单号、trace/correlation/evidence、凭据引用或 bucket 作为 metric label。
- 禁止为未登记的 operation 绕过 `northbound-operation-slo.v1` 创建 provider binding。
