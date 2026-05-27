# WMS Caller Checklist

本文档约束业务调用方接入 `src.app.wms_integration` typed ports 时的异常处理边界。WMS typed ports 只负责把 HTTP、熔断、证据留痕结果转换成 typed exception；是否暂停工作线、创建 RuntimeHold、返回用户可见错误，由业务调用方按本 checklist 落地。

## 调用方必须做的事

1. 调用 WMS typed port 时，必须保留当前业务上下文中的 `request_id` 与 `trace_id`。
2. 捕获 `WmsUnavailableError` 系列后，必须转换成 RuntimeHold 或诊断暂停协议，不能只记录日志后继续执行。
3. 生成 RuntimeHold 或诊断暂停协议时，证据字段必须包含：
   - `operation_name`
   - `evidence_key`
   - `reason_code`
   - `http_status`
   - `trace_id`
   - `request_id`
4. `WmsBusinessRejectedError` 表示 WMS 明确返回 4xx 业务拒绝，默认不创建系统 RuntimeHold；调用方应返回业务拒绝或用户可见错误协议。
5. `WmsEvidencePersistenceError` 表示 WMS 已返回成功，但本地 evidence/breaker 成功留痕失败；它不是 WMS 不可用，不能被当作 WMS unavailable hold，必须进入系统错误或诊断协议。

## 异常分类

| typed exception | 典型来源 | 调用方协议 |
| --- | --- | --- |
| `WmsTimeoutError` | HTTP connect/read/write/pool timeout | RuntimeHold 或诊断暂停 |
| `WmsCircuitOpenError` | 本地 WMS 熔断器 OPEN，未发起 HTTP 请求 | RuntimeHold 或诊断暂停 |
| `WmsUnavailableError` | WMS 5xx、网络错误、2xx 响应结构无法解析等 | RuntimeHold 或诊断暂停 |
| `WmsBusinessRejectedError` | WMS 4xx 业务拒绝 | 业务拒绝或用户可见错误，不默认建系统 hold |
| `WmsEvidencePersistenceError` | WMS 成功后本地证据持久化失败 | 系统错误或诊断协议，不按 WMS 不可用建 hold |

## 证据字段约定

调用方落 RuntimeHold、诊断暂停、业务拒绝或系统错误协议时，证据字段应直接带上 typed exception 的字段，并补齐业务上下文中的 request/trace 信息。

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `operation_name` | typed exception | WMS 操作名，例如 `query_inventory`、`reserve_inventory` |
| `evidence_key` | typed exception | WMS evidence 幂等键；允许为空时仍要显式传递 |
| `reason_code` | typed exception | 标准原因码，例如 `WMS_TIMEOUT`、`WMS_CIRCUIT_OPEN` |
| `http_status` | typed exception | HTTP 状态码；timeout/network/circuit open 可为空 |
| `trace_id` | 业务调用上下文 | 用于串联调用链、诊断事件和 RuntimeHold |
| `request_id` | 业务调用上下文 | WMS request 幂等键或业务请求号 |

## 验收检查

- `WmsTimeoutError`、`WmsCircuitOpenError`、通用 `WmsUnavailableError` 均能触发 RuntimeHold 或诊断暂停协议。
- `WmsBusinessRejectedError` 不进入 RuntimeHold 分支，而是返回业务拒绝或用户可见错误。
- `WmsEvidencePersistenceError` 不进入 WMS unavailable hold 分支，而是返回系统错误或诊断协议。
- 所有协议输出都带齐 `operation_name/evidence_key/reason_code/http_status/trace_id/request_id`。
