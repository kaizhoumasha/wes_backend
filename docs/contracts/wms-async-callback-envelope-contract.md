---
title: WMS 异步回调公共消息格式
status: Approved
created_at: 2026-08-09
updated_at: 2026-08-18
scope: WMS 到 WES 的异步业务事件回调及同步接收应答
system_stage: pre_release
migration_strategy: direct_replacement
related:
  - docs/contracts/transport-fulfillment-contract.md
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/contracts/wms-inbound-putaway-integration-requirements.md
---

# WMS 异步回调公共消息格式

## 1. 这份文档规定什么

WMS 通过以下入口向 WES 发送异步业务事件：

```text
POST {{WES_BASE_URL}}/api/v1/wms/events
```

本文只规定所有 WMS 回调共用的四个 JSON 字段、WES 的同步响应格式，以及请求大小上限。每个业务 operation 的 `data` 字段和
业务结果由对应业务合同规定。

WES 到 WMS 的业务请求和结果上报不使用本接口。Transport 结果由 WMS 回调 WES 时，仍使用本文格式。

请求原始 Body 不能超过 `256 KiB`。WES 必须在解析 JSON 前检查大小，超限直接返回空 Body 的 `413`。如果某个 operation 还有限制，
WES 在读到合法 `operation_id` 后按业务字段错误处理，返回 `422 / REJECTED`。

## 2. `operation_id` 怎么生成和重试

WMS 每发送一条新回调，都要生成一个 UUIDv7 `operation_id`。第一次发送前，WMS 要保存 `operation`、`operation_id` 和完整请求 JSON。
网络超时或 `UNAVAILABLE` 后重试时，继续使用原来的值和原请求内容。

WES 的同步响应必须原样返回请求中的 `operation_id`，不能生成另一个 ID。

不同业务数据使用自己的字段建立关联。例如，计划回调用 `task_id + plan_revision` 关联 PickingTask，Transport 结果用
`transport_task_id` 关联原运输任务。不要复用上一个请求的 `operation_id` 作为新回调 ID。

各类 ID 的用途如下：

| ID | 生成方 | 本合同中的职责 |
| --- | --- | --- |
| `operation_id` | 每条新回调由 WMS 生成；同步响应只返回请求值 | 与 `operation` 一起判断是不是同一条消息 |
| `task_id`、`transport_task_id`、扫码或设备记录 ID | 对应业务数据的负责方 | 用来查找业务数据，不能代替 `operation_id` |
| `previous_operation_id`、`decision_operation_id` | 业务字段引用已有 `operation_id` | 用来找到前一条请求，不是新消息的 ID |

业务 JSON 不增加 `event_id` 或 `request_id`。HTTP 日志可以使用 `X-Request-ID` 记录一次访问，但不能把它写入业务 JSON，也不能
用它判断重复提交。自动出库还要遵守
[WMS / WES 自动出库 PickingTask 交互要求](wms-outbound-picking-task-integration-requirements.md#6-id-和版本由谁生成)中的 ID 规则。

## 3. 回调请求格式

请求顶层只能有以下四个字段：

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
| `operation_id` | 本次回调的唯一 ID，重试时保持不变 |
| `operation` | 业务动作名称和接口版本 |
| `timestamp` | WMS 第一次保存这条回调时的 UTC Unix 毫秒时间，C# 使用 `long` |
| `data` | 当前 operation 的业务字段，只能使用对应业务合同列出的字段 |

`timestamp` 只用于日志和问题排查。业务排序看业务版本字段，设备发生时间看业务 `data` 中的时间字段。
同一条回调重试时，不能刷新 `timestamp`。

WES 用 `operation + operation_id` 判断是不是重复消息，并比较完整请求内容：

- `operation + operation_id` 相同，请求内容也相同：返回 `DUPLICATE`。
- `operation + operation_id` 相同，但请求内容不同：返回 `CONFLICT`。
- `operation` 不同时，即使 `operation_id` 相同，也按两条不同消息处理。业务上的前后关系由 `task_id`、`transport_task_id` 等字段表示。

## 4. WES 同步响应格式

只要 WES 能从请求中读到合法 `operation_id`，响应就使用以下四个字段：

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

WES 第一次保存响应时写入 `timestamp`。相同请求再次到达时返回 `DUPLICATE`，并继续使用第一次响应的 `timestamp + data`。

| 字段 | 生成方与用途 |
| --- | --- |
| `operation_id` | 原样返回请求值 |
| `code` | WES 根据保存结果和字段校验返回；WMS 根据它结束、重试或转人工检查 |
| `timestamp` | WES 第一次保存响应时的 UTC Unix 毫秒时间，C# 使用 `long` |
| `data` | 当前 `code` 需要的字段，例如 `reason_code`；没有字段时返回 `{}` |

| HTTP / `code` | 含义 |
| --- | --- |
| `202 / RECEIVED` | 首次成功保存，WMS 可以结束本次提交 |
| `200 / DUPLICATE` | 这条消息以前已经保存且内容相同，或 operation 专属合同定义的相同业务版本已经保存 |
| `409 / CONFLICT` | 相同消息 ID 对应的内容不同，或者业务数据与已保存内容冲突 |
| `400`，空响应体 | 请求不是合法 JSON，或无法提取合法 `operation_id`；尚未建立消息关联 |
| `413`，空响应体 | 原始 Body 超过共享入口 `256 KiB` 上限，在解码前拒绝；尚未建立消息关联 |
| `422 / REJECTED` | `operation_id` 合法，但其他公共字段、operation 或业务字段不合法 |
| `503 / UNAVAILABLE` | 当前无法成功保存，未接收 |

`400` 和 `413` 返回空 Body，因为 WES 还不能确认这条请求的 `operation_id`。其他响应必须返回请求中的 `operation_id`。
错误原因只使用业务合同规定的 `data.reason_code`，不增加自由文本错误字段。异步回调不使用 HTTP `Retry-After`。
本合同不使用 `429 / BUSY`。WES 暂时无法保存请求时返回 `503 / UNAVAILABLE`；只有成功保存后才返回 `RECEIVED`。

## 5. 收到响应后怎么处理

`RECEIVED` 只表示 WES 已经保存回调，不表示业务处理、运输或设备动作已经完成。如果某个业务还要返回异步执行结果，
对应业务合同会定义另一条结果回调。结果回调必须使用新的 `operation_id`，再通过业务字段关联原请求。

收到 `UNAVAILABLE`，或者没有收到明确响应时，WMS 使用原 `operation_id` 和原请求内容重试。
收到 `400 | 413 | 422` 后不要继续重试原内容。修正请求后生成新的 `operation_id`。
收到 `CONFLICT` 后停止自动发送并转人工检查，不能只换一个 ID 再发。

`transport.task.resulted@v1` 的专属合同定义了一项接收兼容：新的消息身份若复用同一 `transport_task_id + outcome_revision` 且
`data` 与已保存结果完全相同，WES 返回 `200 / DUPLICATE` 且不保存第二份 evidence；同版本 `data` 不同仍返回
`409 / CONFLICT`。同一 `operation + operation_id` 对应不同完整消息的公共冲突规则不变。

## 6. 每个业务 operation 还要说明什么

每个异步回调 operation 必须在自己的业务合同中写清楚：

- operation 的完整字符串和 `data` 字段；
- 什么情况下算重复，什么情况下算冲突；
- 可能返回哪些 HTTP 状态和 `code`；
- WES 保存消息后要继续做什么；
- 如果还有异步结果，结果 operation、状态和超时怎么处理。
