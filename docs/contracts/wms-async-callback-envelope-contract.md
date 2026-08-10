---
title: WMS 异步回调统一信封合同
status: Approved
created_at: 2026-08-09
updated_at: 2026-08-09
scope: WMS 到 WES 的异步业务事件回调及同步接收应答
system_stage: pre_release
---

# WMS 异步回调统一信封合同

## 1. 边界

本文只定义 WMS → WES 异步回调的公共线上信封（wire envelope）和同步接收应答（ACK）。当前统一入口为：

```text
POST {{WES_BASE_URL}}/api/v1/wms/events
```

本文不定义：

- WES → WMS 的搬运提交、业务决定或事实报告信封；
- operation 专属 `data` 字段、业务状态或聚合规则；
- 认证、超时、请求体上限、数据库模型或生产路由装配。

这些内容继续由各业务合同拥有。新增公共字段或应答码必须先修改本文；新增业务字段只修改对应 operation 合同。

## 2. 回调请求信封

顶层是严格闭集，只允许：

```text
request_id
operation
timestamp
data
```

| 字段 | 含义 |
| --- | --- |
| `request_id` | 单次 HTTP 提交的关联号；响应未知后使用相同值重提原请求 |
| `operation` | 已批准的回调操作名和版本 |
| `timestamp` | UTC Unix 毫秒时间戳 |
| `data` | operation 专属闭集 DTO；必须包含该 operation 规定的业务幂等号 |

`request_id` 不能替代业务幂等号。接收方按 `operation + 业务幂等号` 保存规范化 Payload 摘要；相同身份和相同 Payload
幂等收敛，相同身份和不同 Payload 稳定冲突。

## 3. 同步接收应答

应答顶层同样是严格闭集：

```text
request_id
code
message
timestamp
data
```

| HTTP / `code` | 含义 |
| --- | --- |
| `202 / RECEIVED` | 首次可靠持久化，允许 WMS 结束本次提交 |
| `200 / DUPLICATE` | 相同业务身份和相同 Payload 已可靠持久化 |
| `409 / CONFLICT` | 相同业务身份对应不同 Payload，或违反 operation 明确的不可变约束 |
| `400|422 / REJECTED` | JSON、信封、operation 或专属 DTO 不合法，未形成有效输入 |
| `429 / BUSY` | 瞬时无接收容量，未接纳；`data.retry_after_ms` 必须为正整数 |
| `503 / UNAVAILABLE` | 当前无法可靠持久化，未接纳 |

`request_id` 必须原样回显。`message` 只用于简短诊断，不承载机器判断；机器判断只使用 HTTP 状态、`code` 和 operation
规定的 `data`。异步回调不使用 HTTP `Retry-After`，`BUSY` 的重试延迟只使用 `data.retry_after_ms`。

## 4. operation 合同责任

每个异步回调 operation 必须在自己的业务合同中定义：

- `operation` 字面量和 `data` 闭集 DTO；
- 业务幂等号字段；
- 请求体上限和允许的应答子集；
- 首次持久化内容、重复摘要算法及冲突条件；
- 持久化后的异步处理责任。

接收应答只证明回调已可靠持久化，不证明后续业务处理、搬运或设备动作已经完成。
