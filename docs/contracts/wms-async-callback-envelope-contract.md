---
title: WMS 异步回调统一信封合同
status: Approved
created_at: 2026-08-09
updated_at: 2026-08-13
scope: WMS 到 WES 的异步业务事件回调及同步接收应答
system_stage: pre_release
migration_strategy: direct_replacement
related:
  - docs/contracts/transport-fulfillment-contract.md
  - docs/contracts/wms-inbound-putaway-integration-requirements.md
---

# WMS 异步回调统一信封合同

## 1. 边界

WMS 通过以下入口向 WES 发送异步业务事件：

```text
POST {{WES_BASE_URL}}/api/v1/wms/events
```

本文只定义公共线上信封（wire envelope）、同步接收应答（ACK）和共享入口的原始 Body 上限。各业务合同负责 operation 名称、
`data` 字段、业务状态和允许的应答码。WES 到 WMS 的请求、事实报告以及 Transport submit/同步 ACK 不在本文范围内；WMS 到
WES 的 Transport evidence 回调仍使用本文信封。

共享入口的原始 Body 上限固定为 `256 KiB`，必须在 JSON 解码前检查。业务 operation 如需更小限制，只能在解码并取得合法
`operation_id` 后作为 DTO 校验处理，返回 `422 / REJECTED`，不能影响预关联 `413` 判定。

## 2. operation_id

每个 WMS 回调 operation 都只有一个 `operation_id`。发起方在首次提交前生成 UUIDv7，并把它与 Payload 一起持久化；接收方
可靠接纳后，该 operation 下的 Payload 冻结为不可变。相同回调的超时和背压重试使用原值，不能在发送时重新生成。

只有上游请求本身携带 `operation_id`，并且业务合同明确把请求与终局定义为同一交互时，异步终局回调才沿用原值。例如 WES
发起 `outbound.picking_task.start@v1` 后，WMS 返回的 `outbound.picking_task.start_decided@v1` 使用同一个
`operation_id`。两条消息的 operation 不同，不会发生幂等冲突。

主动事件由 WMS 为回调生成 `operation_id`，并使用业务合同规定的对象身份建立因果关联。例如 PickingTask 发布、队列更新和
来源恢复决定由 WMS 生成 ID。Transport submit 虽然使用统一 `operation_id` 信封，但位置和结果 evidence 是独立事实交互，
仍由 WMS 为每条 evidence 生成新的 `operation_id`，通过 `transport_task_id` 关联，不沿用 submit `operation_id`。

公共信封只使用 `operation_id`。业务对象仍保留自己的稳定身份，例如 `task_id`、`queue_revision` 和 `scan_evidence_id`。

ID 边界固定如下：

| ID | 生成方 | 本合同中的职责 |
| --- | --- | --- |
| `operation_id` | 独立主动回调由 WMS 生成；关联型异步终局沿用上游请求发起方生成的 ID | 唯一的回调交互身份和幂等身份组成部分 |
| `task_id`、`transport_task_id`、证据 ID | 对应业务对象的 owner | 只建立业务关联，不替代 `operation_id` |
| `previous_operation_id`、`decision_operation_id` | 不生成新值，由业务 DTO 引用已有 `operation_id` | 表达业务因果，不是新的消息身份 |

本合同不定义业务 JSON 字段 `event_id` 或 `request_id`。operation 类型由 `operation` 表达，交互身份由 `operation_id` 表达，
再增加同义 ID 只会形成多套幂等键。HTTP 中间件可以独立使用 `X-Request-ID` 做单次访问日志追踪，但不能进入业务 Payload、
参与业务幂等或替代 `operation_id`。
具体业务 ID 的生成方、用途和不可变规则由对应业务合同定义；自动出库以
[WMS / WES 自动出库 PickingTask 交互要求](wms-outbound-picking-task-integration-requirements.md#31-id-分类生成方与用途)为准。

## 3. 回调请求信封

顶层是严格闭集：

```text
operation_id
operation
timestamp
data
```

```json
{
  "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
  "operation": "outbound.picking_task.issued@v1",
  "timestamp": 1786060800000,
  "data": {}
}
```

| 字段 | 含义 |
| --- | --- |
| `operation_id` | 业务交互身份。首次生成后保持不变 |
| `operation` | 已批准的业务动作和合同版本 |
| `timestamp` | 发送方首次形成并可靠保存该不可变请求或事件时的 UTC Unix 毫秒时间戳 |
| `data` | operation 专属闭集 DTO |

`timestamp` 只用于审计和链路诊断，不参与业务排序、fencing、超时判断或设备事实发生时间判断。相同交互重试或幂等重放时，
必须保持首次保存的 `timestamp`，不能按每次 HTTP 尝试刷新。

接收方使用 `operation + operation_id` 作为幂等身份，并保存规范化 Payload 摘要：

- 相同身份和相同 Payload 返回 `DUPLICATE`。
- 相同身份和不同 Payload 返回 `CONFLICT`。
- operation 不同但 `operation_id` 相同，表示同一业务交互中的不同阶段。

## 4. 同步接收应答

能够从请求中提取合法 `operation_id` 后，应答顶层是严格闭集：

```text
operation_id
code
timestamp
data
```

```json
{
  "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
  "code": "RECEIVED",
  "timestamp": 1786060800123,
  "data": {}
}
```

应答方在首次形成并可靠保存完整应答时写入 `timestamp`。相同请求的幂等重放返回 `DUPLICATE`，同时复用首次应答的
`timestamp + data`，不能生成新的响应时间或改写业务数据。该时间同样只用于审计和诊断。

| 字段 | 生成方与用途 |
| --- | --- |
| `operation_id` | 应答方原样回显已解析的请求身份，用于把 ACK 关联到原交互；应答方不得另行生成 |
| `code` | 应答方根据可靠持久化结果和合同校验生成的闭集协议结果；调用方据此决定结束、重试或对账 |
| `timestamp` | 应答方首次形成并可靠保存完整应答的 UTC Unix 毫秒时间；只用于审计和诊断 |
| `data` | `code` 专属闭集 DTO；只承载 `reason_code`、`retry_after_ms` 等有程序消费者的结构化信息，无内容时为 `{}` |

| HTTP / `code` | 含义 |
| --- | --- |
| `202 / RECEIVED` | 首次可靠持久化，WMS 可以结束本次提交 |
| `200 / DUPLICATE` | 相同身份和相同 Payload 已持久化 |
| `409 / CONFLICT` | 相同身份对应不同 Payload，或违反 operation 的不可变约束 |
| `400`，空响应体 | 请求不是合法 JSON，或无法提取合法 `operation_id`；尚未建立消息关联 |
| `413`，空响应体 | 原始 Body 超过共享入口 `256 KiB` 上限，在解码前拒绝；尚未建立消息关联 |
| `422 / REJECTED` | 已有合法 `operation_id`，但信封其余字段、operation 或专属 DTO 不合法 |
| `429 / BUSY` | 暂时没有接收容量，未接纳；`data.retry_after_ms` 必须为正整数 |
| `503 / UNAVAILABLE` | 当前无法可靠持久化，未接纳 |

`400` 和 `413` 预关联失败不使用 ACK 信封，响应体长度为 0，接收方不得生成或猜测 `operation_id`。除此以外，接收方必须
原样回显已解析的 `operation_id`。诊断原因使用 operation 专属 `data.reason_code` 等闭集字段表达，不增加与 `code` 重复且
无程序消费者的自由文本字段。异步回调不使用 HTTP `Retry-After`，`BUSY` 的重试延迟只读取 `data.retry_after_ms`。

## 5. ACK 与业务结果

接收 ACK 只证明消息已可靠持久化，不证明业务处理、运输或设备动作已经完成。需要异步终局结果的 operation 必须另行定义
结果回调，并沿用原 `operation_id`。

收到 `BUSY`、`UNAVAILABLE` 或响应未知时，发送方使用原 `operation_id` 和原 Payload 重试。主动事件收到 `400 | 413 | 422`
后停止重试原 Payload，修正内容后创建新的 `operation_id`。业务合同明确沿用上游请求 ID 的关联型终局回调收到这三类“确认
未接纳”结果时，修正 Payload 后仍必须使用上游请求的 `operation_id`；接收方不能把非法、超限或被拒绝内容保存为该
operation 的幂等摘要。收到 `CONFLICT` 后进入合同对账，不能通过更换 ID 掩盖冲突。

## 6. operation 合同责任

每个异步回调 operation 必须在自己的业务合同中定义：

- operation 字面量和 `data` 闭集 DTO；
- 首次持久化内容和冲突条件；
- 允许的应答子集；
- 持久化后的处理责任；
- 需要异步终局时的结果 operation、状态和超时处置。
