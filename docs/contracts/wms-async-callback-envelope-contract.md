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
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
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

WMS 为每条异步回调生成 UUIDv7 `operation_id`，并在首次提交前与完整请求正文一起持久化。超时和背压重试复用原值。同步 HTTP
响应只回显请求 `operation_id`，不生成新身份。

回调通过业务 DTO 引用上游请求或稳定业务对象，不复用上游请求 ID。例如，每批
`outbound.picking_task.plan_delta@v1` 使用新的 `operation_id`，通过
`prepare_operation_id + task_id + execution_id + plan_revision` 建立因果与顺序。每条 Transport 结果回调也使用新的
`operation_id`，通过 `transport_task_id` 关联原提交。

ID 边界固定如下：

| ID | 生成方 | 本合同中的职责 |
| --- | --- | --- |
| `operation_id` | 每条主动事件、分批回调和终局回调都由 WMS 生成；同步响应只回显请求 ID | 唯一的异步消息身份和幂等身份组成部分 |
| `task_id`、`transport_task_id`、证据 ID | 对应业务对象的权威方 | 只建立业务关联，不替代 `operation_id` |
| `previous_operation_id`、`decision_operation_id` | 不生成新值，由业务 DTO 引用已有 `operation_id` | 表达业务因果，不是新的消息身份 |

业务 JSON 不定义 `event_id` 或 `request_id`。HTTP 中间件可以使用 `X-Request-ID` 记录单次访问，但该值不得进入业务请求正文、
参与幂等或替代 `operation_id`。具体业务 ID 规则由业务合同定义；自动出库以
[WMS / WES 自动出库 PickingTask 交互要求](wms-outbound-picking-task-integration-requirements.md#6-id-和版本由谁生成)为准。

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
