---
status: Approved
version: 2.3
effective_date: 2026-08-07
audience: 设备供应商、设备控制系统集成商、WES 二次开发团队
authority: 本文是第三方固定式自动化设备接入 WES 的顶层统一接口合同
implementation_status: Approved contract; runtime cutover pending
---

# WES 第三方设备统一接口白皮书（Third-Party Device Integration White Paper）

## 1. 文档定位（Document Positioning）

本文定义 WES 与第三方固定式自动化设备之间长期生效的顶层统一接口（wire）。所有供应商必须适配 WES 的统一接口
（wire）；WES 核心不为单个供应商保留私有路径、字段别名、兼容载荷（compatibility payload）或动态协议分支。

本文只冻结跨设备稳定的通信骨架：

- 固定传输协议（transport protocol）、方法（method）和路径（path）；
- 命令请求（command request）、同步接纳应答（ACK）和异步回调（callback）的公共包络（envelope）；
- 设备身份（device identity）、命令身份（command identity）、事件身份（event identity）和幂等语义（idempotency semantics）；
- 超时、结果未知、重复和冲突的处理原则；
- WES、供应商设备控制系统（ECS）和 WorkLine 插件（WorkLine plugin）的职责边界。

具体设备支持的任务类型（`task_type`）、事件类型（`event_type`）、业务参数（`params`）、结果数据（`data`）和设备特有错误码
必须写入该设备获批的合同附录（contract annex），不得反向扩张本文的公共包络，也不得提升为 WES 核心全局枚举。只有跨多个
已确认设备都稳定成立的错误语义，才能按共享错误治理进入核心 `DeviceErrorCode`。

历史版本 1.1 保存在项目外
`../archive_docs/wes_backend/docs/integration/third_party_integration_whitepaper.md`，仅用于历史追溯，不再生效。

本文从批准之日起就是供应商设计、设备合同附录和新实现的唯一接口真源；“生效”不表示当前收敛前运行时代码已经全部
符合 2.3。现有实现差异必须按总控计划通过测试驱动开发（TDD）直接替换，未通过统一接口验收前不得作为供应商联调基线，
也不得保留 1.1 兼容入口。

2.3 直接替代 2.2：在四个固定接口上冻结命令、结果、事件和状态的一致合同版本身份，明确单设备单活动命令、部署级事件
身份、命令唯一终态、当前请求追踪响应、可信状态新鲜度和稳定身份载荷不变规则。项目内不保留 2.2 副本、兼容字段或双路径；
历史内容只通过 Git 记录追溯。

## 2. 适用范围与非目标（Scope and Non-goals）

### 2.1 适用范围

本文适用于机械臂、输送线、堆垛机、贴标机、固定式分拣机、扫码设备以及由供应商 ECS 统一控制的其他固定式自动化设备。

供应商可以在设备控制器或局域网网关实现本文接口，但对 WES 暴露的协议必须完全一致。

### 2.2 非目标

本文不定义：

- WMS 的业务单据、库存、来源分配、目标决策或确认接口；
- RCS 管理的 AGV、CTU 等移动运输任务；
- PLC 点位、物理坐标、关节角度、速度曲线、安全回路和急停复位；
- 具体工作线何时下发命令、如何处理 OK/NG 或如何推进业务对象；
- 一份适用于所有设备的 `task_type`、`event_type` 或设备错误码全集。

上述能力分别由 WMS 合同、运输合同（Transport contract）、供应商 ECS、设备合同附录和 WorkLine 插件拥有。

## 3. 核心原则（Core Principles）

1. **统一接口（uniform wire）**：所有供应商实现相同路径和公共包络；差异只允许存在于获批附录定义的
   `task_type`、`event_type`、`params`、`data` 和设备错误详情中。
2. **WES 核心零供应商适配（zero vendor-specific code in WES core）**：接入新设备不得修改 WES 核心协议分支。
3. **异步完成（asynchronous completion）**：同步应答（ACK）只表示设备已接纳命令；物理动作终态只能由结果回调
   （result callback）证明。
4. **稳定身份（stable identity）**：命令、结果和事件都有稳定且唯一的身份；同一身份不得代表不同载荷。
5. **失败关闭（fail closed）**：结果未知、关联失败、载荷冲突或合同不明时，WES 不推进业务对象，也不猜测成功。
6. **逻辑位置（logical location）**：WES 只发送已授权的逻辑位置；供应商 ECS 负责逻辑位置到物理动作的映射和安全互锁。

## 4. 通信技术规范（Technical Specification）

| 项目 | 统一要求 |
| --- | --- |
| 传输协议（transport protocol） | HTTP/1.1 |
| 数据格式（data format） | JSON，`Content-Type: application/json` |
| 字符编码（character encoding） | UTF-8 |
| 时间戳（timestamp） | Unix 毫秒 |
| 网络环境（network environment） | 纯局域网；设备或网关使用稳定地址并与 WES 双向可达 |
| 应用层认证（application authentication） | 本协议不要求专用 Token、签名、Nonce 或 HMAC；网络隔离和访问控制由部署边界负责 |
| 单个 JSON 消息上限（message size limit） | `256 KiB`；设备合同附录可以收窄，不能放大 |

除非本文发布新版本，不得为单个供应商增加 TCP、Modbus、MQTT、私有认证或可配置路径等协议分支。供应商内部可以使用
任意现场协议，但其网关必须把它收敛为本文的统一接口（wire）。

### 4.1 公共 JSON 规则（Common JSON Rules）

- 本文路径均相对于接收方服务端点（Endpoint）的 Base URL；Base URL 由设备合同附录或部署配置绑定，路径本身不得配置。
- 所有字段名区分大小写。公共包络是字段闭集，出现未定义的顶层字段返回 HTTP `400`；设备扩展只能放入 `params`、`data`
  或 `supplier_raw_data`，并按获批附录校验。
- `null` 与字段省略含义不同。只有字段矩阵明确标记“可空”的字段才能发送 `null`；非必填字段未提供时应省略。
- `integer` 必须使用 JSON 整数，不接受字符串或带小数部分的数字；Unix 毫秒时间戳必须是大于 `0` 的有符号 64 位整数。
- `device_code` 和 `contract_key` 长度为 `1..100`；`command_code`、`source_event_id`、`task_type` 和 `event_type` 长度为
  `1..160`；`trace_id` 长度为 `1..120`；`contract_version` 长度为 `1..40`。
- 上述身份和类型字段统一使用 ASCII 令牌（token），词法为 `[A-Za-z0-9][A-Za-z0-9._:-]*`；不接受空白、控制字符、
  非 ASCII 字符或 Unicode 近似字符。
- `GET` 请求不得携带 Body。所有响应都使用 `Content-Type: application/json`，包括错误响应。
- JSON 请求 Body 超过 `256 KiB` 时返回 HTTP `413` 和 `PAYLOAD_TOO_LARGE`，不得读取或部分处理超限业务内容；响应超过
  `256 KiB` 属于合同违例，调用方不得把截断或部分内容当作成功结果。
- WES 生成命令 `timestamp`。设备或 ECS/网关具有可信时钟时，由其生成 `finish_time` 和事件 `timestamp`；否则由 ECS/网关
  在首次观察到最终结果或设备事件时生成。设备合同附录必须记录时间来源、同步方式和允许偏差；同一事实重传时不得修改时间。
  WES 保留接收时间用于诊断和对账。发送方时间戳是不可变证据，不是事实排序、过期淘汰或业务推进的唯一权威；时钟偏差
  不能成为丢弃真实物理回调的理由，需要时间判断时必须结合接收时间和附录允许偏差失败关闭或进入人工对账。

### 4.2 公共应答与错误详情（Common Response and Error Detail）

同步 ACK 和错误响应统一使用以下包络：

```json
{
  "code": 422,
  "message": "ANNEX_VALIDATION_FAILED",
  "trace_id": "TRACE-0001"
}
```

| 字段 | JSON 类型 | 必填 | 可空 | 语义 |
| --- | --- | --- | --- | --- |
| `code` | `integer` | 是 | 否 | 必须与实际 HTTP 状态码一致 |
| `message` | `string` | 是 | 否 | 稳定的 `UPPER_SNAKE_CASE` 结果或原因，不承载设备业务结果 |
| `trace_id` | `string` | 否 | 否 | 接收方返回的诊断关联身份；不得作为幂等身份 |

公共 HTTP 结果使用以下 `message`；设备合同附录不得改名或赋予其他含义：

| HTTP 状态 | `message` | 语义 |
| --- | --- | --- |
| `200` | `ACCEPTED` 或 `ACK` | 命令已接纳，或回调证据已可靠接收 |
| `400` | `INVALID_ENVELOPE` | JSON 或公共包络不合法 |
| `404` | `DEVICE_NOT_FOUND` 或 `COMMAND_NOT_FOUND` | 设备未绑定，或结果回调引用的命令不存在；按具体接口取值 |
| `405` | `METHOD_NOT_ALLOWED` | 请求方法与固定接口不一致 |
| `409` | `IDEMPOTENCY_CONFLICT` | 同一稳定身份对应不同载荷 |
| `413` | `PAYLOAD_TOO_LARGE` | JSON 消息超过统一上限 |
| `422` | `ANNEX_VALIDATION_FAILED` | 公共包络合法，但设备附录字段或值不合法 |
| `429` | `CAPACITY_EXCEEDED` | 接收方暂时达到接纳上限；必须返回 `Retry-After` |
| `500`、`502`、`503`、`504` | `TEMPORARILY_UNAVAILABLE` | 接收方未能可靠完成处理或响应；调用方按具体接口的未知/重传规则处理 |

请求包含合法 `trace_id` 时，响应应原样回传；请求没有合法 `trace_id` 或尚未完成 JSON 解析时，接收方可以省略或生成新的
`trace_id`。已接纳请求的重复提交必须复用首次接纳的 `code` 和 `message`，但响应中的 `trace_id` 始终关联当前 HTTP 请求，
不复用首次请求的追踪值。明确未接纳请求恢复后仍按当前接纳条件重新判断。

设备物理错误统一放入结果或状态中的 `error_detail`：

```json
{
  "code": "DEVICE_FAULT",
  "message": "设备执行失败",
  "supplier_raw_code": "2002",
  "supplier_raw_data": {
    "axis": "Z"
  }
}
```

| 字段 | JSON 类型 | 必填 | 可空 | 语义 |
| --- | --- | --- | --- | --- |
| `code` | `string` | 是 | 否 | 跨设备稳定错误使用共享 `DeviceErrorCode`；设备特有值由获批附录定义 |
| `message` | `string` | 是 | 否 | 人类可读说明，不作为程序分支条件 |
| `supplier_raw_code` | `string` | 否 | 否 | 供应商原始错误码，只作为证据 |
| `supplier_raw_data` | `object` | 否 | 否 | 供应商原始错误数据，只作为证据 |

协议错误应答与设备物理 `error_detail` 是不同层级：HTTP/包络/附录校验失败不得伪装成设备执行失败；WES 核心和插件也不得
读取 `supplier_raw_code` 或 `supplier_raw_data` 作业务判断。

## 5. 统一交互模型（Uniform Interaction Model）

```text
设备事件 ──事件回调──> WES ──同步 ACK──> 供应商 ECS
                         │
                         ├─业务决策与命令持久化
                         │
WES ──命令请求────────> 供应商 ECS ──同步 ACK──> WES
                                      │
                                      └─物理动作完成 ──结果回调──> WES
```

事件回调的 HTTP 响应不得携带下一条设备动作。所有设备动作都必须通过独立命令请求下发，以保持命令身份、持久化证据和
异步终态边界清晰。

## 6. 供应商实现的接口（Supplier-Provided Endpoints）

### 6.1 接收设备命令（Receive Device Command）

- 方法（method）：`POST`
- 路径（path）：`/api/v1/device/command`

公共请求包络（request envelope）：

```json
{
  "device_code": "ARM_01",
  "command_code": "CMD-20260807-0001",
  "contract_key": "arm.pick",
  "contract_version": "2.0",
  "task_type": "DEVICE_CONTRACT_TASK",
  "timestamp": 1786032000000,
  "params": {
    "source_location": "STATION_A",
    "target_location": "STATION_B"
  },
  "trace_id": "TRACE-0001"
}
```

字段约束：

| 字段 | JSON 类型 | 必填 | 可空 | 语义 |
| --- | --- | --- | --- | --- |
| `device_code` | `string` | 是 | 否 | WES 与供应商共同确认的独立命令资源编码 |
| `command_code` | `string` | 是 | 否 | WES 生成的全局唯一命令身份；供应商必须据此防止重复物理动作 |
| `contract_key` | `string` | 是 | 否 | WES 对本命令冻结的获批设备附录身份 |
| `contract_version` | `string` | 是 | 否 | WES 对本命令冻结的获批设备附录版本 |
| `task_type` | `string` | 是 | 否 | 设备合同附录定义的任务类型，不是 WES 核心全局枚举 |
| `timestamp` | `integer` | 是 | 否 | WES 创建命令请求的 Unix 毫秒时间戳 |
| `params` | `object` | 是 | 否 | 设备合同附录定义的字段闭集；不得把业务字段拍平到顶层 |
| `trace_id` | `string` | 否 | 否 | 跨系统诊断关联身份；是唯一不参与幂等载荷摘要的公共字段 |

命令优先级、排队顺序和完成截止时间由 WES 负责，不属于供应商接口（wire）。每个 `device_code` 最多存在一个已接纳且未终态
的命令；供应商 ECS 不为同一设备维护本协议可见的待执行队列。ECS 必须在一个原子接纳判断中确认设备实际加载的
`contract_key`/`contract_version` 与命令一致、设备为 `AUTO + IDLE` 且没有活动命令；任一条件不满足都不得启动物理动作。
已接纳命令的相同身份、相同载荷重复请求直接返回首次 ACK，不重新进入设备准入，也不受当前 `RUNNING` 状态影响；上述原子
判断只用于首次接纳新命令。不同 `device_code` 可以并行执行，供应商不得通过私有优先级字段改变 WES 已确定的对象顺序。

`device_code` 标识可被独立下发命令和独立判断忙闲的执行资源，不要求与 PLC、ECS 服务端点或整机一一对应。例如三段滚筒线
可由同一 PLC/Endpoint 控制，但能够并行动作的各段应使用不同 `device_code`；同一 Endpoint 可以服务多个
`device_code`。首版只采用“每个 `device_code` 单活动命令”，不增加并发数量配置或设备侧命令队列。

同步接纳应答（ACK response）：

```json
{
  "code": 200,
  "message": "ACCEPTED",
  "trace_id": "TRACE-0001"
}
```

HTTP 状态语义：

| HTTP 状态 | 语义 | 是否表示物理动作完成 |
| --- | --- | --- |
| `200` | 首次接纳，或相同 `command_code` 与相同载荷的重复请求 | 否 |
| `400` | JSON 或公共包络不合法 | 否 |
| `404` | `device_code` 未绑定到该 Endpoint | 否 |
| `405` | HTTP 方法与固定接口不一致 | 否 |
| `409` | 相同 `command_code` 对应不同载荷 | 否 |
| `413` | JSON 消息超过统一上限 | 否 |
| `422` | 合同身份不匹配，或 `task_type`、`params` 不符合命令指定的设备合同附录 | 否 |
| `429` | 同一设备已有活动命令，或原子接纳竞争失败；必须返回 `Retry-After` | 否 |
| `503` | ECS/网关暂时无法可靠接纳命令 | 否 |

错误响应必须使用 4.2 的公共应答包络；成功 ACK 的 `code` 必须为 `200`，`message` 必须为 `ACCEPTED`。相同
`command_code` 与相同载荷的重复请求必须返回首次 ACK，不得生成新的接纳身份或重复执行物理动作。

HTTP `400`、`404`、`405`、`413`、`422`、`429`、`503` 是明确未接纳：ECS/网关不得启动物理动作。`429`、`503` 可以在条件
恢复后使用原 `command_code` 和原载荷重提。HTTP `409` 表示该身份已经关联其他载荷，必须人工对账。网络中断、HTTP 超时
以及 `500`、`502`、`504` 属于结果未知，不得自动重放物理命令。稳定身份与载荷绑定及合同修正规则见第 8 节。

### 6.2 查询设备状态（Query Device Status）

- 方法（method）：`GET`
- 路径（path）：`/api/v1/device/status?device_code={device_code}`

`device_code` 是必填查询参数。一个 ECS/网关可以服务多台设备，但每次请求只查询一台设备；不得依赖响应体反向猜测请求目标。

响应包络（response envelope）：

```json
{
  "device_code": "ARM_01",
  "contract_key": "arm.pick",
  "contract_version": "2.0",
  "mode": "AUTO",
  "status": "RUNNING",
  "current_command_code": "CMD-20260807-0001",
  "error_detail": null,
  "timestamp": 1786032005000
}
```

| 字段 | JSON 类型 | 必填 | 可空 | 语义 |
| --- | --- | --- | --- | --- |
| `device_code` | `string` | 是 | 否 | 必须与查询参数完全一致 |
| `contract_key` | `string` | 是 | 否 | 当前设备实例实际加载的获批附录身份 |
| `contract_version` | `string` | 是 | 否 | 当前设备实例实际加载的获批附录版本 |
| `mode` | `string` | 是 | 否 | 只允许 `AUTO`、`MANUAL`、`MAINTENANCE`、`UNKNOWN` |
| `status` | `string` | 是 | 否 | 只允许 `IDLE`、`RUNNING`、`ERROR`、`OFFLINE`、`UNKNOWN` |
| `current_command_code` | `string` | 是 | 是 | 当前执行命令；没有或无法确认时为 `null` |
| `error_detail` | `object` | 是 | 是 | `status=ERROR` 时必须符合 4.2；无设备错误时为 `null` |
| `timestamp` | `integer` | 是 | 否 | 按 4.1 时间来源规则生成的状态观察 Unix 毫秒时间戳 |

状态响应必须反映请求处理时的当前设备事实，并返回 `Cache-Control: no-store`；不得用缓存成功响应掩盖设备不可达或状态未知。
设备合同附录必须给出准入允许的最大状态观察年龄，WES 发现 `timestamp` 超过该年龄时失败关闭，不发送命令。

供应商必须把内部模式和状态映射为上述共享值；设备合同附录只能声明该设备支持的子集和映射证据，不能新增或重新定义共享值。
维护是控制模式，不是运行状态。WES 只有在 `contract_key`/`contract_version` 与活动 `LineRunEpoch` 精确匹配，并且
`mode=AUTO`、`status=IDLE` 且 `current_command_code=null` 时才能发送新命令；其他组合一律失败关闭。HTTP `404` 表示
`device_code` 未绑定到该 Endpoint；
HTTP `503` 表示暂时无法获得可信状态。`current_command_code` 只用于诊断和人工对账，不能单独作为物理动作成功证据。顶层
白皮书不定义通用取消接口；只有具体设备和业务合同共同批准取消语义后，才能另行定义且不得伪装为本协议的默认能力。

## 7. WES 提供的回调接口（WES-Provided Callback Endpoints）

### 7.1 上报命令结果（Report Command Result）

- 方法（method）：`POST`
- 路径（path）：`/api/v1/callback/result`

请求包络（request envelope）：

```json
{
  "command_code": "CMD-20260807-0001",
  "device_code": "ARM_01",
  "contract_key": "arm.pick",
  "contract_version": "2.0",
  "result": "SUCCESS",
  "finish_time": 1786032004000,
  "source_event_id": "RESULT-ARM_01-0001",
  "data": {
    "device_contract_result": "COMPLETED"
  },
  "error_detail": null,
  "trace_id": "TRACE-0001"
}
```

字段约束：
| 字段 | JSON 类型 | 必填 | 可空 | 语义 |
| --- | --- | --- | --- | --- |
| `command_code` | `string` | 是 | 否 | 必须与原命令完全一致，同时表达结果的命令因果关系 |
| `device_code` | `string` | 是 | 否 | 必须与原命令目标设备完全一致 |
| `contract_key` | `string` | 是 | 否 | 必须与原命令冻结的附录身份完全一致 |
| `contract_version` | `string` | 是 | 否 | 必须与原命令冻结的附录版本完全一致 |
| `result` | `string` | 是 | 否 | 只允许 `SUCCESS` 或 `FAILED` |
| `finish_time` | `integer` | 是 | 否 | 按 4.1 时间来源规则生成的物理动作最终结果 Unix 毫秒时间戳 |
| `source_event_id` | `string` | 是 | 否 | 供应商生成的唯一结果事件身份；同一结果重传时必须保持不变 |
| `data` | `object` | 是 | 否 | 获批附录定义的结果字段闭集；没有业务结果字段时发送空对象 `{}` |
| `error_detail` | `object` | 是 | 是 | `FAILED` 时必须符合 4.2，`SUCCESS` 时必须为 `null` |
| `trace_id` | `string` | 否 | 否 | 诊断关联身份，不替代 `source_event_id` |

WES 必须先按 `command_code` 查找原命令；不存在时返回 HTTP `404` 和 `COMMAND_NOT_FOUND`，不建立已接纳回调证据。一个
`command_code` 最多只能绑定一个已接纳的终态结果身份和载荷摘要：已有该已接纳终态时，相同 `source_event_id` 与相同摘要
重传返回首次 ACK；同一命令出现不同 `source_event_id`、不同摘要或相互矛盾的终态时返回 HTTP `409`，保存冲突证据但不
推进对象，并进入人工对账。尚无已接纳终态时，原身份和相同摘要按第 8.1 节重新执行当前接纳判断。

### 7.2 上报设备事件（Report Device Event）

- 方法（method）：`POST`
- 路径（path）：`/api/v1/callback/event`

请求包络（request envelope）：

```json
{
  "device_code": "CONVEYOR_01",
  "contract_key": "conveyor.transfer",
  "contract_version": "1.3",
  "event_type": "DEVICE_CONTRACT_EVENT",
  "timestamp": 1786032000000,
  "source_event_id": "EVENT-CONVEYOR_01-0001",
  "data": {
    "location": "STATION_A"
  },
  "trace_id": "TRACE-0002"
}
```

字段约束：

| 字段 | JSON 类型 | 必填 | 可空 | 语义 |
| --- | --- | --- | --- | --- |
| `device_code` | `string` | 是 | 否 | 产生事件的设备编码 |
| `contract_key` | `string` | 是 | 否 | 产生事件时设备实例实际加载的获批附录身份 |
| `contract_version` | `string` | 是 | 否 | 产生事件时设备实例实际加载的获批附录版本 |
| `event_type` | `string` | 是 | 否 | 获批设备合同附录定义的事件类型 |
| `timestamp` | `integer` | 是 | 否 | 按 4.1 时间来源规则生成的设备事件 Unix 毫秒时间戳 |
| `source_event_id` | `string` | 是 | 否 | 供应商生成的唯一事件身份；同一事件重传时必须保持不变 |
| `data` | `object` | 是 | 否 | 获批附录定义的事件字段闭集；业务字段不得拍平到顶层 |
| `trace_id` | `string` | 否 | 否 | 诊断关联身份，不替代 `source_event_id` |

`source_event_id` 必须在整个 WES 部署范围内、跨所有供应商、设备、结果回调和设备事件回调全局唯一且永久不复用。供应商
必须使用能够保证部署级唯一的命名方案；WES 不以供应商或设备字段缩小幂等作用域。具体事件确有因果关联需求时，由设备合同
附录在该 `event_type` 的 `data` 字段闭集中定义类型明确的关联字段；公共包络不提供无类型的通用 `causation_id`。

`LineRunEpoch` 是一条 WorkLine 在一套冻结插件、配置、流程模式、物理拓扑和设备合同下的连续可信运行代际。它不是时间戳，
也不是 PickingTask；同一 Epoch 可以依次执行多张任务。重启、清线或切换上述冻结内容后，WES 创建新 Epoch，旧 Epoch 的事实
不得推进新 Epoch。该身份只存在于 WES 内部，不进入供应商接口（wire）。

结果回调沿用原命令已经冻结的 Epoch，并把合同身份与原命令冻结值比较。设备事件第一次被观察时，WES 在现有入站幂等记录中
原子保存 `source_event_id`、载荷摘要和可空的 `line_run_epoch_id`：存在活动 `LineRunEpoch` 时保存其身份；不存在时保存
`null`。该字段一经写入就不再改变，直接复用现有记录，不增加其他状态对象。

存在绑定 Epoch 时，WES 以其冻结值校验合同身份；不一致时返回 HTTP `422`，保存允许保留的诊断信息但不推进对象。没有活动
Epoch 的设备事件作为诊断证据持久化并返回 ACK，但不调用插件；后续重传仍使用 `null`，不得绑定新 Epoch。ACK 后的异步处理
也只使用原绑定；原 Epoch 已关闭或不再活动时，事件只保留为诊断证据，不推进新对象。

### 7.3 WES 回调应答（WES Callback ACK）

WES 成功持久化回调证据后返回：

```json
{
  "code": 200,
  "message": "ACK",
  "trace_id": "TRACE-0001"
}
```

HTTP `200` 只表示 WES 已可靠接收该回调，不表示业务对象已推进。供应商必须按以下分类处理失败：

| 结果 | 分类 | 供应商行为 |
| --- | --- | --- |
| 网络中断、HTTP `408`、`429`、`500`、`502`、`503`、`504` | 瞬态失败 | 使用原 `source_event_id` 和相同载荷有界重传；`429` 遵循 `Retry-After` |
| HTTP `400`、`404`、`405`、`413`、`422` | 永久合同错误 | 停止原载荷自动重传；修正后按第 8 节的稳定身份与载荷规则决定沿用或更换身份 |
| HTTP `409` | 幂等冲突 | 立即停止自动重传并进入人工对账；不得用新身份绕过冲突 |

WES 返回非上述状态时失败关闭，供应商不得无限重传；由设备合同附录明确现场告警和人工处置。

## 8. 幂等、截止时间与安全重提（Idempotency, Deadline, and Safe Resubmission）

### 8.1 稳定身份与幂等

接收方只要能够取得语法合法的稳定身份并计算规范化语义载荷，就必须先固定该身份与载荷摘要的不可变对应关系，再判断是否
接纳；明确拒绝的尝试也不得让同一身份在之后代表不同载荷。已有对应关系时先执行本节的重复/冲突判断。判断载荷时排除唯一
诊断字段 `trace_id`，并采用以下 JSON 语义：对象字段顺序和无意义
空白不影响相等性，数组元素顺序参与相等性，字段省略与显式 `null` 不相等，字段值及其附录声明的 JSON 类型必须一致。不得
直接比较原始 HTTP 字节。接收方可以在内部保存规范化载荷摘要（normalized payload digest），但摘要算法和存储方式属于实现
细节，不是 wire 字段，也不得改变上述可观察行为。

命令接口明确拒绝时不得执行物理动作；回调接口明确拒绝时不得建立可推进对象的已接纳证据。接收方可以保存拒绝和冲突诊断，
但稳定身份一旦与可计算的规范化载荷绑定就永久不改。若请求因缺失合法身份、JSON 无法解析或超限而无法同时确定身份和完整
语义载荷，则不建立绑定。回调瞬态失败可能发生在证据持久接收之后，重复上报仍按原 `source_event_id` 幂等处理。

供应商必须以 `command_code` 和上述载荷摘要共同判断重复或冲突：

- 相同 `command_code`、相同载荷且已有接纳记录：返回首次接纳结果，不重复执行物理动作；
- 相同 `command_code`、相同载荷但只有明确未接纳记录：保留既有身份与摘要绑定，按当前设备和合同条件重新执行准入；仍不
  满足时返回本次判断的错误，首次满足时建立唯一接纳记录并执行物理动作；
- 相同 `command_code`、不同载荷：返回 HTTP `409`，不执行物理动作；
- 已接纳命令的身份绑定至少保留到命令终态完成并超过附录约定的对账窗口；明确拒绝请求的身份绑定至少保留到最后一次拒绝
  后超过同一对账窗口。发送方无论接收方保留时限如何都不得复用稳定身份；不能使用固定一小时作为通用规则。

结果回调和设备事件回调使用部署级唯一的 `source_event_id` 与同一摘要规则判定重复或冲突。重传可以沿用或省略原
`trace_id`，但不得改变任何参与摘要的字段：

- 同一身份、相同摘要且已有已接纳证据：返回首次 ACK，不重复持久化证据或调用插件；响应追踪当前请求；
- 同一身份、相同摘要但此前明确未接纳：保留既有身份、摘要和 Epoch 关联。结果回调使用原命令 Epoch；设备事件使用入站
  幂等记录中的可空 `line_run_epoch_id`。只有该 Epoch 仍然活动时才重新执行当前接纳判断，字段为 `null` 或 Epoch 已结束时
  不得改绑新 Epoch。仍未接纳时返回本次判断的错误，首次接纳时持久化唯一证据并返回 ACK；不得把首次拒绝响应作为永久
  幂等结果重放；
- 同一身份、不同摘要：返回 HTTP `409`。

结果回调还必须满足 7.1 的单命令唯一终态约束。

### 8.2 WES 完成截止时间

WES 根据获批设备合同附录，在首次发送命令前一次性确定不可变的完成截止时间。该截止时间不进入供应商公共包络，命令重复
请求也不得延长它；具体字段名、持久化结构和计时机制属于 WES 实现，不由本文规定。供应商 ECS 使用自身安全时限控制内部
动作，并且无论结果在 WES 截止时间之前还是之后形成，都必须使用原 `source_event_id` 上报真实最终结果；WES 对晚到结果
失败关闭并按当前对象状态决定关联或人工对账。

### 8.3 WES 命令重提

- 可以证明请求尚未离开 WES，或供应商明确返回 6.1 定义的“未接纳”响应时，WES 才能安全重提。HTTP `429`、`503` 恢复后
  必须使用原身份和原载荷；HTTP `400`、`404`、`405`、`413`、`422` 修正后，若规范化语义载荷不变则沿用原身份，若
  `contract_key`、`contract_version`、`task_type`、`params` 等任何参与摘要的内容改变，则原身份保持已拒绝绑定，修正后的
  语义请求使用新的 `command_code`。明确未接纳证明旧请求没有触发动作，因此这种新身份不属于绕过未知结果。
- HTTP 超时、连接中断以及非本文合同化的 HTTP `500`、`502`、`504` 等“可能已送达”场景属于结果未知
  （delivery unknown）；WES 不自动重放命令。状态查询只能确认原命令是否仍在活动，不能以 `IDLE` 或
  `current_command_code=null` 证明原命令未接纳或已经完成；终态只能由匹配回调或人工核验事实闭合。
- 已收到同步 ACK 的命令不自动重放，等待结果回调或进入明确的超时处置。
- 请求处于 delivery unknown、已经接纳或已返回 HTTP `409` 时，任何重提都不得生成新的命令身份绕过原状态。

### 8.4 供应商回调重传

对于 7.3 定义的瞬态失败，供应商应持久保存尚未取得 WES HTTP `200` 的回调，并使用相同事件身份、相同载荷进行有界重传。
永久合同错误必须先停止自动重传并完成修正：规范化语义载荷不变时沿用原 `source_event_id`，参与摘要的任何内容改变时，
原身份保持已拒绝绑定，修正后的上报使用新的部署级唯一 `source_event_id`。只有接收方明确未接纳时才允许这种更换；delivery
unknown、已接纳或 HTTP `409` 场景不得更换身份。幂等冲突必须进入人工对账。重传策略由设备合同附录和现场运行要求确定；
本文不规定固定缓存时长、固定次数或指数退避参数。

## 9. 位置、物理控制与安全边界（Location, Motion, and Safety Boundary）

WES 只下发逻辑位置和业务动作意图。供应商 ECS 独立拥有：

- 逻辑位置到物理坐标、PLC 点位和运动轨迹的映射；
- 设备能力、当前模式、互锁、急停、安全门和现场安全条件；
- 原子动作拆解、执行顺序和设备内部恢复；
- 不安全或不可执行命令的拒绝。

WES 不越过 ECS 直接控制 PLC，也不以 HTTP `200`、状态查询或推测替代物理动作终态回调。

## 10. 合同附录（Contract Annex）

每一种实际接入设备必须先批准一份最小合同附录。供应商负责提交真实设备能力和原始证据，业务负责人确认业务动作及结果
含义，WES 架构负责人确认其没有改变本文公共协议，项目交付负责人确认设备实例、固件和现场配置绑定；四项责任没有闭合时，
附录不得标记为获批，也不得作为插件开发或联调输入。

获批附录统一放在 `docs/contracts/device-annexes/<annex-key>.md`；没有真实设备和各方批准内容时不创建空模板或占位合同。

| 项目 | 必须明确的内容 |
| --- | --- |
| 合同身份 | `contract_key`、附录版本、批准日期、批准角色和替代关系 |
| 设备身份 | `device_code`、独立命令资源边界、设备角色、服务端点（Endpoint）、设备实例和责任方 |
| 固件边界 | 已验收的 ECS/网关版本和设备固件版本 |
| 命令集合 | 支持的 `task_type` 及每种 `params` 的字段闭集、类型、必填性和示例 |
| 事件集合 | 支持的 `event_type` 及每种 `data` 的字段闭集、类型、必填性和示例 |
| 结果集合 | 每种命令成功结果、失败结果和 `error_detail` 语义 |
| 状态映射 | 内部模式/状态到共享 `mode`、`status` 的映射证据及该设备支持的共享值子集 |
| 时限 | ACK 超时、状态最大观察年龄、WES 确定不可变完成截止时间所用的物理完成时限、时间来源与允许偏差、事件保留和人工对账窗口 |
| 能力限制 | 单设备单活动命令、`AUTO + IDLE` 原子接纳、不可取消条件和安全拒绝条件 |
| 验收场景 | 正常、同设备竞争、跨设备并行、版本不匹配、重复、终态冲突、超时、乱序、离线和恢复场景 |

附录只能收窄具体设备允许的值，不能修改本文固定路径、公共包络、身份和 ACK/CALLBACK 语义。设备状态接口必须返回该实例
实际运行的 `contract_key` 和 `contract_version`；WES 在新命令准入前将其与活动 `LineRunEpoch` 冻结值比较，不一致时失败
关闭。获批附录版本必须与设备实例、现场配置版本和活动 `LineRunEpoch` 一同冻结。任何会改变 wire 行为的 `task_type`、
`event_type`、字段、结果、错误、时限、ECS/网关或固件变化都必须形成新附录版本，重新执行一致性验收，并在停止新接纳、
闭合或人工清理活动对象后创建新 Epoch；
不得在活动 Epoch 内静默替换。纯排版或不改变语义的说明修订不提升附录版本。

项目内对每个 `contract_key` 只保留当前获批附录；被替代版本按项目归档规则移出项目目录，Git 历史保留变更记录。供应商
原始协议和联调资料继续原样保存在 `docs/hardware/`；附录负责说明如何满足本统一接口，不得反向改写厂商原文。

## 11. 分层实现与测试所有权（Implementation and Test Ownership）

| 层级 | 拥有内容 | 不得用来证明 |
| --- | --- | --- |
| WES 核心基础能力 | 命令持久化、单次发送、ACK/CALLBACK 证据、幂等、未知结果和诊断 | 具体工作线业务正确 |
| 统一接口合同测试 | 固定路径、公共包络、身份、重复和冲突语义 | 某个供应商内部 PLC/ECS 正确 |
| 插件 SDK 合同（plugin SDK contract） | 类型化输入、封闭 Decision、依赖注入及 Decision 到可靠对象的公共接线 | 具体工作线业务或真实网络正确 |
| 供应商一致性验收 | 供应商实现与本文及获批设备附录的一致性 | WES 核心可靠性或工作线业务正确 |
| WorkLine 插件逻辑测试 | 何时返回哪一种 Decision，以及如何推进、暂停、隔离或请求对账 | 数据库、HTTP、供应商设备安全或厂商内部动作正确 |
| 部署级端到端验收（deployment E2E） | 安装后的插件经 WES 公共入口、真实持久化、HTTP/CALLBACK 和并发环境形成闭环 | 单独替代任一层的合同与逻辑测试 |

具体供应商不在 WES 核心增加私有适配器（Adapter）包。供应商或其网关直接实现统一接口（wire）；WES 中只有跨所有供应商稳定的
公共协议代码。若公共协议实现尚未满足本文，必须在对应代码阶段按测试驱动开发（TDD）完成替换，不得通过兼容字段、双路径、
动态注册表或供应商特例绕过。

插件逻辑测试只使用类型化输入和只读投影替身，并断言其返回的封闭 Decision；不得依赖真实数据库、网络、消息队列或供应商
设备。真实持久化、HTTP/CALLBACK、故障注入和并发闭环属于部署级端到端验收；它通过安装后的 WES 公共边界驱动插件，
不向插件开放基础设施依赖。供应商一致性验收继续独立证明 ECS/网关，不得并入插件测试。

## 12. 接入与验收流程（Onboarding and Acceptance）

1. 供应商、业务负责人、WES 架构负责人和项目交付负责人共同批准设备合同附录。
2. 供应商在 ECS 或局域网网关实现本文固定接口。
3. 双方按公共包络、明确拒绝、安全重提、单设备竞争、跨设备并行、附录版本不匹配、部署级事件身份、命令终态冲突、时钟
   偏差、重复、超时、乱序和离线恢复场景执行一致性验收。
4. WorkLine 插件独立验证业务触发和对象推进，不以接口连通代替业务验收。
5. WES 核心可靠性独立验证，不以某条业务线或某台真实设备代替基础能力验收。
6. 所有阻断项关闭后再接入生产组合根（Composition Root）；不存在旧协议并行期或兼容切换期。
