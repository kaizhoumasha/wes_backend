---
status: Approved
version: 1.1
effective_date: 2026-08-23
audience: 设备供应商、ECS 集成商、WES 开发与现场联调人员
authority: WES 与固定式设备 ECS 的四个冻结 wire 接口
implementation_status: Implemented in WES core and local Mock; supplier conformance pending
---

# WES 第三方设备统一接口白皮书

## 1. 范围

本文冻结 WES 与固定式设备 ECS 的四个交互接口：

- ECS 提供 `POST /api/v1/device/command`；
- ECS 提供 `GET /api/v1/device/status`；
- WES 提供 `POST /api/v1/callback/result`；
- WES 提供 `POST /api/v1/callback/event`。

本文只定义 WES 与 ECS 之间的 wire。WES 内部 `DeviceCommand`、合同治理、幂等 evidence、Celery 派发和业务推进不进入
ECS 包络；ECS 内部 PLC、运动控制、安全互锁和设备实现也不进入 WES。顶层协议不提供 Cancel；本轮也不增加供应商私有
认证、自动重试或其他供应商私有路径。WES 的 `MANUAL_DEBUG` 在创建前和实际发送前都调用本白皮书 Status 接口执行准入。

### 1.1 双向职责

- ECS 作为服务端接收 Command、响应 Status；WES 作为客户端调用这两个接口；
- WES 作为服务端接收 Result/Event；ECS 作为客户端主动回调；
- 物理动作采用 `Command → Ack → Result Callback` 三段式异步闭环；Event 的 HTTP ACK 不携带业务动作，WES 如需动作必须
  另行发送 Command。

## 2. 公共规则

- HTTP/JSON，`Content-Type: application/json`，UTF-8；
- 固定路径，不由 Swagger 请求覆盖；
- 顶层字段为闭集，业务字段只能放入 `params` 或 `data`；
- `device_code` 和 `command_code` 直接使用，不做 `device_id`/`command_id` 转换；
- wire 时间字段使用 Unix Epoch 毫秒整数；
- 单个 JSON 消息上限为 `256 KiB`；
- 同步 ACK 只证明 ECS 接纳命令，不证明物理完成；物理终态由 Result Callback 证明。
- `params`、Result `data`、Event `data` 的二级字段由设备合同附录拥有；`MANUAL_DEBUG` 不约束这些二级业务字段，也不推进
  WMS/WorkLine 业务能力；
- `null` 仅在字段表明确允许时有效；可选对象省略时按空对象处理，不得把二级业务字段提升到顶层。

`contract_key`、`contract_version`、`source_event_id` 和 WES trace 是 WES 内部治理字段，不发送给 ECS，也不要求 ECS 回传。

## 3. ECS 接口

### 3.1 接收作业指令（Receive Command）

```text
POST <ECS_BASE_URL>/api/v1/device/command
```

请求顶层字段严格为：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `device_code` | `string` | 是 | 目标设备编码 |
| `command_code` | `string` | 是 | WES 生成的全局命令身份 |
| `task_type` | `string` | 是 | 设备动作类型 |
| `priority` | `integer` | 是 | 本轮固定为 `1` |
| `timeout` | `integer` | 是 | 期望完成时间，毫秒 |
| `timestamp` | `integer` | 是 | Unix Epoch 毫秒命令创建时间 |
| `params` | `object` | 是 | 业务参数 |

现场首条联调样例：

```json
{
  "device_code": "STATION_SCAN1",
  "command_code": "019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
  "task_type": "MOVE_FORWARD",
  "priority": 1,
  "timeout": 30000,
  "timestamp": 1787440800000,
  "params": {}
}
```

统一 wire 同样只约定 `params` 是对象，不约定其中的业务字段；具体命令参数由对应设备合同附录定义。

同步接纳应答：

```json
{
  "code": 200,
  "message": "Accepted"
}
```

ECS 可以额外返回可选 `trace_id`，仅用于供应商日志定位。HTTP 非 2xx、非 JSON、`code != 200` 或 `message != "Accepted"`
均不视为确定接纳。

ECS 必须按 `command_code` 幂等：重复收到相同命令和相同载荷时返回相同接纳事实，不重复执行物理动作；相同身份对应不同
载荷时返回冲突。

Command 应答顶层字段严格为 `code`、`message`，可选 `trace_id` 不得为 `null`。WES 按 HTTP 状态与下表固定语义分类：

| HTTP | `message` | 含义 |
| --- | --- | --- |
| `200` | `Accepted` | 已接纳，等待匹配 Result Callback |
| `400` | `INVALID_ENVELOPE` | 包络错误，未接纳 |
| `404` | `DEVICE_NOT_FOUND` | 设备不存在，未接纳 |
| `405` | `METHOD_NOT_ALLOWED` | 方法错误，未接纳 |
| `409` | `IDEMPOTENCY_CONFLICT` | 相同身份对应不同载荷，结果未知，禁止自动重放 |
| `413` | `PAYLOAD_TOO_LARGE` | 超过消息上限，未接纳 |
| `422` | `ANNEX_VALIDATION_FAILED` | 设备附录校验失败，未接纳 |
| `429` | `CAPACITY_EXCEEDED` | 未接纳；必须同时返回有效 `Retry-After` 才可按原身份延后重提 |
| `500/502/504` | `TEMPORARILY_UNAVAILABLE` | 交付结果未知，进入对账，禁止自动重放 |
| `503` | `TEMPORARILY_UNAVAILABLE` | 明确未接纳，可按原身份和原载荷延后重提 |

非 JSON、未知 HTTP/`message` 组合、缺失必填字段或额外字段均按结果未知处理。只有“请求未离开 WES”或上表明确未接纳时，
才允许复用原 `command_code` 和完全相同的载荷重提。

### 3.2 设备状态查询（Device Status）

```text
GET <ECS_BASE_URL>/api/v1/device/status[?device_code=<DEVICE_CODE>]
```

`device_code` 是唯一可选 Query 参数：传入时查询单设备，不传时返回当前 ECS 服务器上的全部设备。成功响应顶层字段严格为
`devices`；其值为数组，每项严格包含 `device` 和 `state`：

| 对象 | 字段 | 类型 / null | 说明 |
| --- | --- | --- | --- |
| `device` | `device_code` | `string`，非 null | 设备编码 |
| `device` | `device_name` | `string \| null` | 设备名称，仅诊断展示 |
| `device` | `device_type` | `string \| null` | 设备类型，仅诊断展示 |
| `device` | `role` | `string \| null` | 设备角色，仅诊断展示 |
| `device` | `supported_commands` | `string[] \| null` | 当前 ECS 声明的命令能力；`MANUAL_DEBUG` 创建和发送准入使用 |
| `device` | `supported_events` | `string[] \| null` | 当前 ECS 声明的事件能力，仅诊断展示 |
| `state` | `device_code` | `string`，非 null | 必须与同项 `device.device_code` 一致 |
| `state` | `mode` | `AUTO \| MANUAL \| MAINTENANCE \| UNKNOWN`，非 null | 运行模式 |
| `state` | `status` | `IDLE \| RUNNING \| ERROR \| PAUSED \| STOPPED \| OFFLINE \| UNKNOWN`，非 null | 设备状态 |
| `state` | `is_online` | `boolean`，非 null | ECS 在线判断 |
| `state` | `current_command_code` | `string \| null` | 当前活动命令；无活动命令为 `null` |
| `state` | `scenario` | `string \| null` | ECS 联调/诊断场景，不参与业务决策 |
| `state` | `updated_at` | `integer`，非 null | Unix Epoch 毫秒状态更新时间 |

单设备成功示例：

```json
{
  "devices": [
    {
      "device": {
        "device_code": "STATION_SCAN1",
        "device_name": "扫码工位1",
        "device_type": "SCANNER",
        "role": "SCAN_STATION",
        "supported_commands": ["MOVE_FORWARD", "MOVE_BACKWARD", "MOVE_LEFT", "MOVE_RIGHT"],
        "supported_events": ["SCAN_COMPLETED"]
      },
      "state": {
        "device_code": "STATION_SCAN1",
        "mode": "AUTO",
        "status": "IDLE",
        "is_online": true,
        "current_command_code": null,
        "scenario": "success",
        "updated_at": 1787431993388
      }
    }
  ]
}
```

带 `device_code` 的查询必须恰好返回一个匹配条目；不带参数时返回全部条目。零条、重复条目、身份不一致、字段缺失/额外、
类型或枚举错误均是不可信状态。正常业务派发只在 `is_online=true`、`mode=AUTO`、`status=IDLE`、
`current_command_code=null` 且 `updated_at` 未过期时准入；否则失败关闭，不发送 Command。设备元数据和 `scenario` 只作诊断。
`MANUAL_DEBUG` 同样要求上述实时状态，并要求选定 `task_type` 位于非空 `supported_commands`；它不使用业务 binding 或状态新鲜度合同。
未知设备返回 HTTP `404`；Status 的非 2xx 响应体不属于公共 wire，WES 只依据 HTTP 状态判定查询失败。

## 4. WES 回调接口

### 4.1 任务结果回传（Report Result）

```text
POST <WES_BASE_URL>/api/v1/callback/result
```

请求顶层字段严格为：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `command_code` | `string` | 是 | 原命令编码 |
| `device_code` | `string` | 是 | 执行设备编码 |
| `result` | `SUCCESS \| FAILED` | 是 | 物理动作终态 |
| `finish_time` | `integer` | 是 | Unix Epoch 毫秒完成时间 |
| `data` | `object` | 否 | 设备合同附录定义的业务结果数据 |
| `error_detail` | `object \| null` | 条件 | `FAILED` 必填，`SUCCESS` 省略或为 `null` |

成功示例：

```json
{
  "command_code": "019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
  "device_code": "STATION_SCAN1",
  "result": "SUCCESS",
  "finish_time": 1787440804000,
  "data": {
    "result": "MOVE_FINISHED"
  },
  "error_detail": null
}
```

失败示例（供应商 `error_code/error_message` 适配为统一 `code/msg`）：

```json
{
  "command_code": "019f12d0-58d7-7b4d-a23a-1b90aa5d4471",
  "device_code": "STATION_SCAN1",
  "result": "FAILED",
  "finish_time": 1787440804000,
  "data": {
    "result": "MOVE_FAILED"
  },
  "error_detail": {
    "code": "TARGET_BLOCKED",
    "msg": "Path blocked"
  }
}
```

WES 使用 `RESULT:{command_code}` 作为内部幂等身份。相同结果重复上报只处理一次；相同命令对应不同规范化载荷时返回冲突。
未知 `command_code` 返回 `404`，不推进任何业务对象。

### 4.2 设备事件上报（Event Push）

```text
POST <WES_BASE_URL>/api/v1/callback/event
```

请求顶层字段严格为：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `device_code` | `string` | 是 | 上报设备编码 |
| `event_type` | `string` | 是 | 设备合同附录定义的事件类型，例如 `ESTOP_PRESSED`、`MATERIAL_ARRIVED`、`SCAN_COMPLETED` |
| `timestamp` | `integer` | 是 | Unix Epoch 毫秒事件时间 |
| `data` | `object` | 否 | 设备合同附录定义的业务事件数据 |

```json
{
  "device_code": "STATION_SCAN1",
  "event_type": "SCAN_COMPLETED",
  "timestamp": 1787440805000,
  "data": {
    "barcode": "BIN_104"
  }
}
```

流水线扫码示例中的 `barcode` 只是无业务语义的设备附录字段，值仅包含料箱 BIN 编号，不包含物料“6 合 1”等复合数据。
统一 wire 不约定任何 `data` 二级字段；每台设备可以不同，必须由对应设备合同附录定义并放在 `data` 内，不得提升到顶层。
WES 使用完整外部事件包络的规范化 SHA-256 摘要生成内部事件身份，同一 wire 载荷只处理一次。

Result 和 Event 成功持久接收后统一应答：

```json
{
  "code": 200,
  "message": "ACK"
}
```

ACK 不携带业务指令。WES 的下一步动作必须通过新的 DeviceCommand 下发。

Result/Event 的错误应答顶层字段严格为整数 `code` 和字符串 `message`：

| HTTP / `code` | `message` | 处理语义 |
| --- | --- | --- |
| `400` | `INVALID_ENVELOPE` | 字段、类型、枚举、条件必填或 JSON 不合规；修正载荷后使用正确身份重报 |
| `404` | `COMMAND_NOT_FOUND` | 仅 Result；WES 不认识该 `command_code`，不推进业务 |
| `409` | `IDEMPOTENCY_CONFLICT` | 同一幂等身份对应不同载荷；停止自动重报并人工对账 |
| `409` | `RESULT_BEFORE_DISPATCH` | 仅 Result；命令尚未下发却收到结果，WES 封锁命令并进入人工对账，禁止自动重报或重新下发 |
| `413` | `PAYLOAD_TOO_LARGE` | 超过 `256 KiB`；缩小载荷后按正确身份重报 |
| `503` | `TEMPORARILY_UNAVAILABLE` | WES 尚未持久接收，ECS 可保留原载荷延后重报 |

首次已成功持久接收的相同规范化 Result/Event 重报返回相同 `200 ACK` 且不重复推进。ECS 未收到确定 `200 ACK` 时可以重报
完全相同的回调；收到 `409` 时不得换身份绕过冲突。完全相同的提前 Result 重报仍返回 `RESULT_BEFORE_DISPATCH` 并复用首次拒绝证据；该响应
不表示物理动作完成，ECS 和 WES 都不得自动重新下发，必须人工核对设备事实。

## 5. WES 内部边界

- `contract_key/version` 由 WES 命令、活动设备绑定或 wire 治理配置提供，不要求 Status 返回；
- Status 的 `device` 元数据与 `scenario` 只用于诊断；业务准入只读取同一条目的 `state` 公共字段；
- Result 通过 `command_code` 找回命令及其内部合同元数据；
- Event 若匹配活动设备 binding，则冻结该 binding 的设备合同和 `LineRunEpoch`；无 binding 事件使用 WES wire
  治理合同 `third_party_integration@1.1`；
- 外部时间直接使用 Unix 毫秒并原样进入规范化 evidence；
- `MANUAL_DEBUG` Result 只闭合 DeviceCommand 和 evidence，不推进 WorkLine；
- `MANUAL_DEBUG` 的 Command `params` 与 Callback `data` 保持不透明，不应用 WMS/WorkLine 设备附录的二级字段约束；
- WES 不把 ACK、Result Callback 或 Event ACK 误当成后续业务 Decision。

## 6. 手动联调

这些 WES 诊断 API 仅允许超级用户访问，不属于供应商需要实现的 wire。页面先调用
`POST /api/v1/device/commands/debug/preflight`，传入 `endpoint_base_url` 并通过既有 Status wire 枚举当前 ECS 的全部设备。

`POST /api/v1/device/commands/debug` 要求：

- `client_request_id`；
- `endpoint_base_url`；
- `device_code`；
- `timeout`；
- `task_type`；
- `params`；
- `reason`（1–500 个非空字符）。

WES 记录 `reason` 和当前超级用户 `created_by`，固定内部合同元数据和 `priority=1`，仅在创建准入通过后持久化，并在 Celery
实际发送前再次查询 Status。操作者通过
`GET /api/v1/device/commands/{command_code}` 查看同步 ACK、最终 Result Callback、失败原因和 evidence 应用状态。

`GET /api/v1/device/evidences/stream` 使用 Bearer token 提供活动会话内的 Result/Event callback 尝试和 evidence 应用更新。
该 SSE 是 WES 内部、live-only、best-effort 的诊断旁路：不提供 replay，不改变 callback `200 ACK`、ECS 重报或业务验收语义。

现场闭环要求 ECS 能反向访问 WES 的 Result/Event 地址。开发机默认仅绑定 `127.0.0.1` 时不能作为现场回调地址；联调运行配置
必须显式绑定可达接口，并由 ECS 侧配置对应 WES 地址。
