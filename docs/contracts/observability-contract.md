# Phase 3 Observability Contract

本文档锁定 Phase 3 runtime / callback / device / WMS 的稳定观测口径。临时 debug log 不能替代本合同中的 span、metric、log event 和 evidence。

## Stable Signals

| Signal | Type | Required attributes |
| --- | --- | --- |
| `callback.normalize` | span + metric + log event | `trace_id`, `correlation_id`, `provider_code`, `source_event_id` |
| `runtime_inbox.claim` | span + metric | `trace_id`, `correlation_id`, `operation_kind`, `inbox_id` |
| `runtime_intent.dispatch` | span + metric + log event | `trace_id`, `correlation_id`, `provider_code`, `operation_kind` |
| `device_command.ack` | span + metric | `trace_id`, `correlation_id`, `command_code`, `provider_code` |
| `device_command.result` | span + metric | `trace_id`, `correlation_id`, `command_code`, `source_event_id` |
| `wms_breaker.transition` | metric + log event | `trace_id`, `provider_code`, `operation_kind`, `breaker_state` |

## Attribute Rules

- `trace_id`：跨 API、inbox、intent、device command 和 WMS evidence 的主追踪标识。
- `correlation_id`：引用 `ExecutionCorrelation.correlation_id`，跨域引用不得使用 runtime session FK。
- `provider_code`：外部 provider 或内部 capability provider 的稳定编码。
- `operation_kind`：幂等和观测统一操作类型，例如 `callback`, `fulfillment`, `device_command`, `reconciliation`。
- `command_code`：DeviceCommand 的业务命令编码，不使用 device FK 作为观测主键。
- `source_event_id`：外部 callback / event 的原始事件标识。

## Prohibitions

- 禁止只写临时日志字段替代 metric/span/evidence。
- 禁止把 provider DTO 原始字段名作为稳定 attribute 名。
- 禁止在安全失败时丢失 `trace_id`、`provider_code` 或 `operation_kind`。
