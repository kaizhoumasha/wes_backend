# Phase 3 Observability Contract

本文档锁定 Phase 3 runtime / callback / device / WMS 的稳定观测口径。临时 debug log 不能替代本合同中的 span、metric、log event 和 evidence。

## Stable Signals

| Signal | Type | Required attributes |
| --- | --- | --- |
| `callback.normalize` | span + metric + log event | `trace_id`, `correlation_id`, `provider_code`, `source_event_id` |
| `runtime_inbox.claim` | span + metric | `trace_id`, `correlation_id`, `operation_kind`, `inbox_id` |
| `runtime_intent.dispatch` | span + metric + log event | `trace_id`, `correlation_id`, `provider_code`, `operation_kind` |
| `device_command.ack` | span + metric | `trace_id`, `correlation_id`, `command_code`, `provider_code`, `ack_age_ms` |
| `device_command.result` | span + metric | `trace_id`, `correlation_id`, `command_code`, `source_event_id` |
| `wms_breaker.transition` | metric + log event | `trace_id`, `provider_code`, `operation_kind`, `breaker_state` |
| `wms_evidence.persistence_failure` | metric + log event | `trace_id`, `provider_code`, `operation_kind`, `evidence_key`, `reason_code` |

## Attribute Rules

- `trace_id`：跨 API、inbox、intent、device command 和 WMS evidence 的主追踪标识；legacy 设备结果缺少 trace 时，`DeviceCommand RESULT` 可复用稳定 `source_event_id` 作为 fallback。
- `correlation_id`：优先引用 `ExecutionCorrelation.correlation_id`，跨域引用不得使用 runtime session FK；`RuntimeInbox claim` 尚未完成 correlation 解析时允许使用 `session:{id}` / `workline:{id}` / `device:{id}` / claim bucket / `inbox:{id}` 作为稳定 fallback；`RuntimeIntent dispatch` 尚未完成 correlation 解析时允许使用 `operation_key` / `dispatch_key` / `outbox:{id}` 作为稳定 fallback；`callback.normalize` 允许使用 `dispatch_key` / `command_code` / `exchange_request_code` / `event:{device_code}:{event_type}` / ingress `request_id` 作为稳定 fallback；`DeviceCommand RESULT` 缺少跨域 correlation 时允许使用 `command:{command_code}` 作为稳定 fallback。
- `provider_code`：外部 provider 或内部 capability provider 的稳定编码；external `callback.normalize` 优先使用 `source_system`，缺失时使用 `callback_type` 前缀；device result/event `callback.normalize` 缺省为 `ECS`；RuntimeIntent/Outbox dispatch 优先使用 payload provider 字段，设备命令缺省为 `ECS`，其它外部请求缺省为 `target_code`。
- `operation_kind`：幂等和观测统一操作类型，例如 `callback`, `fulfillment`, `device_command`, `reconciliation`。
- `command_code`：DeviceCommand 的业务命令编码，不使用 device FK 作为观测主键。
- `ack_age_ms`：DeviceCommand 从 `sent_at` 到 `ack_received_at` 的耗时毫秒数，用于 ACK age SLO 和现场设备链路诊断。
- `source_event_id`：外部 callback / event 的原始事件标识；`callback.normalize` 缺少原始事件 ID 时允许使用 ingress `request_id` 作为稳定 fallback；legacy 设备结果缺少事件 ID 时，`DeviceCommand RESULT` 可使用 `command_result:{command_code}:{finish_time}` 作为稳定 fallback。
- `inbox_id`：RuntimeInbox / WorklineInbox 的持久化消息主键，用于定位 claim worker 处理边界。
- `evidence_key`：WMS evidence 幂等键，用于定位失败留痕尝试。
- `reason_code`：稳定失败原因码，例如 `WMS_EVIDENCE_PERSISTENCE_FAILED`。

## Instrumentation Binding

- `RuntimeObservabilityRegistry.emit()` 是当前 Python 运行时的稳定事件发射入口；所有 adapter 必须先通过 required attributes 校验，再转成实际 metric/log/span。
- `RuntimeOpenTelemetryBridge` 是 registry observer 到 OpenTelemetry-style exporter 的无依赖桥接层；按 `signal_type` 将同一已验证事件 fan-out 为 span、metric 和 log event，不允许 exporter 绕过 registry 直接消费临时字段。
- `RuntimeOpenTelemetryHttpExporter` 是生产 backend adapter 接线；FastAPI lifespan 通过 `configure_runtime_open_telemetry_backend()` 按 `WES_RUNTIME_OTEL_ENABLED=true` + `WES_RUNTIME_OTEL_ENDPOINT` 注册命名 observer，重复初始化必须幂等，默认关闭。
- Callback ingress 在 external normalize allow-list 校验、device result 命令锚点解析、device event 入库 trace 解析完成后发出 `callback.normalize`；观测发射失败不得影响 callback ACK、落库或业务编排。
- WorklineInbox worker 在 `claim_pending_messages()` 成功提交释放行锁后发出 `runtime_inbox.claim`；观测发射失败不得回滚或阻塞 claim。
- Workline `OutboxDispatchService._dispatch_single()` 进入设备、HTTP 或内部信号派发出口时发出 `runtime_intent.dispatch`；观测发射失败不得阻塞 outbox 派发或改变终态更新。
- `DeviceCommandService.handle_callback_result()` 在设备结果被接受并更新命令终态后发出 `device_command.result`；观测发射失败不得回滚或阻塞 callback result 处理。
- WMS breaker OPEN/HALF_OPEN/CLOSED 状态变化使用 `wms_breaker.transition`；typed port 必须从请求 `trace_id` 透传，不能在缺失 trace 时伪造追踪标识。
- WMS 成功响应后的本地 evidence/breaker 留痕失败使用 `wms_evidence.persistence_failure`；该事件必须保留原始 `trace_id` 和 `evidence_key`，供系统诊断而非业务 HOLD。
- 具体 backend（Jaeger / Tempo / SkyWalking 等）必须通过 `RuntimeOpenTelemetryBridge` 后方的 HTTP adapter / collector endpoint 挂载，不能新增临时字段替代稳定 attributes。

## Prohibitions

- 禁止只写临时日志字段替代 metric/span/evidence。
- 禁止把 provider DTO 原始字段名作为稳定 attribute 名。
- 禁止在安全失败时丢失 `trace_id`、`provider_code` 或 `operation_kind`。
