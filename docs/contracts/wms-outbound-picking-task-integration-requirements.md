---
title: WMS / WES 自动出库 PickingTask 交互要求
status: ReviewRequired
created_at: 2026-08-07
updated_at: 2026-08-14
audience: WMS 与 WES 初级开发工程师、联调与测试人员
scope: WMS/WES API、任务队列、异步资源计划、计划增量、执行中增删、逐盘决定、事实确认和任务状态确认
related:
  - docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md
  - docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/contracts/wms-async-callback-envelope-contract.md
  - docs/contracts/transport-fulfillment-contract.md
  - docs/architecture/authority-matrix.md
  - docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
---

# WMS / WES 自动出库 PickingTask 交互要求

## 1. 文档定位

本文定义 WMS 与 WES 自动出库 PickingTask 的调用方向、operation、请求和响应字段、重复提交处理、版本顺序和失败边界。业务状态、不变量和物理流程以
[WES 出库操作顶层设计](../superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md)为准；TransportTask 和
DeviceCommand 分别遵循其独立合同，不能由 PickingTask 消息直接改写。

本文是 `ReviewRequired` 的联合评审基线，不代表相关业务能力已经实施，也不构成 Phase 9 实施授权。

系统尚未发布，不提供旧 operation、兼容字段、双路径或迁移逻辑。JSON Schema 必须与本文字段一致，不得增加扩展字段或通用动作流程。

## 2. 主流程

| 阶段 | 触发条件 | WMS/WES 交互 | 成功后的处理 |
| --- | --- | --- | --- |
| 任务入队 | WMS 形成可执行 PickingTask | WMS 发送 `outbound.picking_task.issued@v1` | WES 持久化任务并返回 `RECEIVED`，不分配来源或目标资源 |
| 准备执行 | WES 选中任务和就绪 WorkLine | WES 发送 `outbound.picking_task.prepare@v1` | WMS 返回 `PREPARE_ACCEPTED`，开始异步资源运算 |
| 分批计划 | WMS 形成一批已锁定资源或取消决定 | WMS 发送连续的 `outbound.picking_task.plan_delta@v1` | WES 先持久化并应用，再返回 `RECEIVED`；局部资源完整后立即执行 |
| 货架与 Bin 搬运 | 已接纳成员的位置和目标位明确 | WES 创建独立 TransportTask | 五层货架、退料货架和转运货架可并行搬运；结果由 Transport 合同闭环 |
| Bin 执行 | 五层货架、Bin 或退箱候选到达约定位置 | WES 请求入站批次、工作计划或退箱批次 | WMS 返回本次可执行 Bin、Cell 或退箱目标 |
| 逐盘执行 | 料盘到达扫码台并形成完整扫码证据 | WES 请求 `outbound.material.decide@v1`，PUT 后报告 `outbound.material.movement_report@v1` | WMS 决定目标或 NG 去向，并在位置事实确认后更新权威库存和占用 |
| 执行中变更 | WMS 追加或取消 Bin | WMS 发送更高版本的 `outbound.picking_task.plan_delta@v1` | WES 应用新增成员；取消按第 11.2 节安全点处理 |
| 任务完成 | WES 没有未闭合业务义务、逐盘事实或取消动作 | WES 发送 `outbound.picking_task.completion_confirm@v1` | WMS 返回 `COMPLETED` 或 `NOT_COMPLETED`，不接收成员完成明细 |

## 3. 权威边界

| 参与方 | 唯一权威 | 不承担 |
| --- | --- | --- |
| WMS | PickingTask、业务优先序、库存、来源和目标分配、来源锁、转运货架容量、物料资格、执行中追加和取消 | 工作线设备状态、物理防撞和设备命令终态 |
| WES | WorkLine 准入、计划版本接纳、执行对象、位置与设备证据、可靠外部义务 | 库存重算、目标选址、容量计算和设备间安全互锁 |
| RCS/AGV/CTU | 货架与 Bin 的路径、搬运和运输终态 | PickingTask 业务状态 |
| ECS/PLC/设备 | 扫码、取放、输送、硬件互锁、防撞和设备终态 | 库存、来源或目标业务分配 |

计划增量只表达业务资源。WES 根据已接纳资源、可靠 `PositionProjection` 和 WorkLine 固定目标位创建 TransportTask；WMS 不直接
下发 TransportTask 或 DeviceCommand。

## 4. 公共信封、端点和 HTTP 语义

### 4.1 公共信封

请求顶层固定为：

```json
{
  "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
  "operation": "outbound.picking_task.issued@v1",
  "timestamp": 1786060800000,
  "data": {
    "task_id": "PICK-20260811-001",
    "queue_revision": 1,
    "dispatch_sequence": 100
  }
}
```

响应顶层固定为：

```json
{
  "operation_id": "019f12d0-58d7-7b4d-a23a-1b90aa5d4472",
  "code": "RECEIVED",
  "timestamp": 1786060800123,
  "data": {}
}
```

顶层字段和 operation 专属 `data` 均为严格闭集。发起方在首次发送前原子持久化全局唯一的 UUIDv7 `operation_id` 和 Payload；
重试保持 ID、Payload 和顶层 `timestamp` 不变。

请求信封字段字典：

| JSON Path | 必填 | 类型/格式 | 生成方 | 语义与约束 |
| --- | --- | --- | --- | --- |
| `operation_id` | 是 | UUIDv7 字符串 | 当前消息发起方 | 本次不可变交互的唯一幂等身份；首次外发前持久化，重试原样复用 |
| `operation` | 是 | 本文第 5 节固定枚举 | 当前消息发起方 | 决定 `data` 的唯一 DTO；大小写敏感，不接受别名或旧版本 |
| `timestamp` | 是 | UTC Unix 毫秒整数 | 当前消息发起方 | 首次形成并持久化消息的时间；重试不得刷新 |
| `data` | 是 | object | 当前消息发起方 | operation 专属严格闭集；即使无专属字段也使用 `{}`，不得使用 `null` |

响应信封字段字典：

| JSON Path | 必填 | 类型/格式 | 生成方 | 语义与约束 |
| --- | --- | --- | --- | --- |
| `operation_id` | 是 | UUIDv7 字符串 | 接收方回显 | 原样回显已解析请求；不得生成新的响应身份 |
| `code` | 是 | 第 4.3 节固定枚举 | 接收方 | 表示协议接纳、业务决定、事实提交或失败类别 |
| `timestamp` | 是 | UTC Unix 毫秒整数 | 接收方 | 首次形成并可靠保存完整响应的时间；同一请求重放首次值 |
| `data` | 是 | code 专属 object | 接收方 | 严格闭集；无字段时使用 `{}`，不得省略或使用 `null` |

业务 Payload 不定义 `event_id` 或 `request_id`。HTTP 链路追踪可以使用 `X-Request-ID`，但不得进入业务 DTO 或参与幂等。

### 4.2 端点

| 发起方 | 接收方 | 方法和路径 | 模式 |
| --- | --- | --- | --- |
| WMS | WES | `POST {{WES_BASE_URL}}/api/v1/wms/events` | Event + 持久化 ACK |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/decisions` | 准备请求 ACK 或同步业务决定 |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/facts` | 同步事实确认 |

三个接口的原始 Body 上限均为 `256 KiB`。非法 JSON、无法提取合法 `operation_id` 返回空响应体 `400`；解码前超限
返回空响应体 `413`。建立合法消息关联后，所有响应必须回显 `operation_id` 并使用公共响应信封。

### 4.3 HTTP 与 code

| HTTP / `code` | 含义 |
| --- | --- |
| `200 / DECIDED` | 同步业务决定已经形成 |
| `200 / RECORDED` | Fact 及其要求的 WMS 业务状态已经在一个事务中提交 |
| `200 / DUPLICATE` | 相同 Event、回调或 Fact 已接纳 |
| `202 / RECEIVED` | Event 或计划增量已经可靠持久化并完成顺序接纳 |
| `202 / PREPARE_ACCEPTED` | 耗时资源运算请求已可靠接纳，尚不代表存在可执行计划 |
| `422 / REJECTED` | 信封、operation 或专属 DTO 非法 |
| `409 / CONFLICT` | 幂等内容冲突、版本冲突或不可变约束冲突 |
| `429 / BUSY` | 暂时没有接收容量；返回 `retry_after_ms` |
| `503 / UNAVAILABLE` | 当前无法可靠持久化或处理 |

业务否决仍使用 `200 / DECIDED` 并读取 `data.result`。同步决定以原身份重试时必须重放首次完整决定，不能退化为
`DUPLICATE` ACK。

通用失败响应的 `data` 固定如下；operation 专属业务 `reason_code` 只出现在对应的 `200 / DECIDED` 联合中，不能与本表混用：

| HTTP / `code` | `data` 必填字段 | 枚举/约束 |
| --- | --- | --- |
| `422 / REJECTED` | `reason_code`，`INVALID_DATA` 时可带 `field_path` | `reason_code=INVALID_ENVELOPE \| UNSUPPORTED_OPERATION \| INVALID_DATA`；`field_path` 是长度 `1..256` 的 RFC 6901 JSON Pointer，例如 `/data/six_in_one/Qty` |
| `409 / CONFLICT` | `reason_code` | `IDEMPOTENCY_CONFLICT \| REVISION_CONFLICT \| STATE_CONFLICT \| REFERENCE_CONFLICT` |
| `429 / BUSY` | `retry_after_ms` | `1..60000` 毫秒；调用方到期后仍使用原消息身份和 Payload |
| `503 / UNAVAILABLE` | 无 | `data={}`；调用方使用原消息身份和 Payload 重试 |

### 4.4 字段字典和公共数据类型

表内“条件”表示满足条件时必填，否则禁止出现。通用规则如下：

- HTTP Body 使用 UTF-8 `application/json`。字段名和枚举大小写敏感；未知字段、重复 JSON key、错误类型和枚举外值返回
  `422 / REJECTED + INVALID_DATA`。
- 可选字段无值时必须省略。除非字段表明确允许，所有字段、对象元素和数组元素都禁止 `null`；空字符串、空对象和空数组也
  禁止代替省略。
- `operation_id` 使用 RFC 9562 UUIDv7 规范字符串。其他 ID、业务编码和位置编码必须匹配
  `[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}`；接收方按精确字符串比较，不根据前缀猜测类型。
- `six_in_one` 的六个设备值是长度 `1..256` 的非空 UTF-8 字符串；其他明确标注“扫码设备原文”的字段使用其字段表长度。所有
  UTF-8 字符串长度均按 Unicode code point 计数；WES 按设备规范化结果原样传递，不做数值、日期或主数据转换。
- 时间字段均为 `0..9223372036854775807` 的 UTC Unix 毫秒整数。revision、generation、sequence 和 outcome version 均为
  `1..9223372036854775807` 的整数；仅任务状态确认中的 `last_applied_plan_revision` 和 `current_plan_revision` 允许为 `0`，表示
  准备请求已经接纳但尚无任何计划增量。`retry_after_ms` 是 `1..60000` 的整数。
- 条件数组出现时必须包含 `1..N` 项；最大可发送项数由 `256 KiB` 原始 Body 上限自然约束，不另设无法与 Body 上限独立验证的
  隐含数量。数组顺序只有在对应 operation 明确声明时才有业务含义。
- 字段表中的 `a.b` 表示对象字段，`items[]` 表示数组每一项，`x | y` 表示闭集枚举，不表示自由字符串。

位置对象使用 `type` 判别的严格联合：

| `type` | 必填字段 | 语义 | 禁止字段 |
| --- | --- | --- | --- |
| `RACK_SLOT` | `rack_id + rack_face + slot_id` | 退料货架或转运货架上的单料盘储位 | `bin_id`、`cell_id`、`location_code`、`zone_code` |
| `RACK_BIN_SLOT` | `rack_id + rack_face + slot_id` | 五层货架上的单 Bin 储位 | `bin_id`、`cell_id`、`location_code`、`zone_code` |
| `BIN_CELL` | `rack_id + rack_face + bin_id + cell_id` | 当前五层货架来源 Bin 内的工作料格 | `slot_id`、`location_code`、`zone_code` |
| `HANDOFF_POSITION` | `location_code` | WorkLine 中经静态拓扑批准的 Bin 交接/缓存位置 | 货架、Bin、Cell 和 `zone_code` 字段 |
| `RACK_POSITION` | `location_code` | 经部署配置批准的货架工作位或业务库位 | 货架、Bin、Cell、SLOT 和 `zone_code` 字段 |
| `NG_ZONE` | `zone_code` | 单料盘确定 NG 放置区 | 货架、Bin、Cell、SLOT 和 `location_code` 字段 |

同名位置对象在不同 operation 中继续遵守本表，operation 专属表只补充引用关系和现场事实来源，不重新定义结构。

### 4.5 实现术语

| 术语 | 本文含义 | 实现要求 |
| --- | --- | --- |
| operation | 一种固定的跨系统动作，名称中的 `@v1` 是合同版本 | 必须使用第 5 节完整字面量，不能缩写或自定义别名 |
| DTO | operation 对应的 `data` 请求或响应结构 | 只接收字段表定义的字段；未知字段按 `INVALID_DATA` 拒绝 |
| Payload | 包含顶层信封和 `data` 的完整请求正文 | 同一 `operation_id` 重试时必须保持内容不变 |
| Event | WMS 主动发送给 WES 的异步消息 | WES 必须先可靠持久化，再返回 ACK |
| ACK | 对请求或 Event 的同步接收结果 | ACK 只表示接纳或持久化，不表示运输、设备动作或业务已经完成 |
| 决定 | WMS 对当前请求返回的同步业务结果 | HTTP/code 为 `200 / DECIDED`，调用方必须按 `data.result` 分支处理 |
| Fact | WES 上报的已发生物理事实 | WMS 返回 `RECORDED | DUPLICATE` 后，WES 才能关闭对应外部确认义务 |
| 幂等 | 同一消息可安全重试 | 重试必须复用 `operation_id`、Payload 和时间戳；同一 ID 改内容属于冲突 |
| revision | 同一任务内按顺序发布的业务版本 | 必须连续递增，不能跳号、倒退或覆盖已接纳版本 |
| generation | 货架面窗口或来源锁的围栏值 | 只能接受当前有效值；旧值不能继续执行 |
| 终态 | 已确定且不能由普通重试改写的结果 | `UNKNOWN` 不是失败终态，必须等待更高权威证据或进入对账 |
| 权威 | 对某类数据有最终决定权的系统 | 发生冲突时按第 3 节处理，其他系统不能用本地推测覆盖 |
| 闭集 | 只允许文档列出的字段或枚举值 | 未列出的字段、别名和扩展值必须拒绝 |
| 原子持久化 | 多个字段或状态在同一事务中全部成功或全部失败 | 不允许只保存一部分后返回成功 |
| 位置投影 | WES 根据可靠事件保存的现场位置视图 | 只能用于执行和门禁，不能替代 WMS 库存或全局位置主账 |
| 对账 | 自动执行无法安全继续时的人工核对流程 | 停止自动推进，保留现场资源、原始消息和设备证据 |

## 5. Operation 实现索引

| operation | 方向 | 触发条件 | 首次成功响应 | 详见 |
| --- | --- | --- | --- | --- |
| `outbound.picking_task.issued@v1` | WMS 到 WES | WMS 发布新 PickingTask | `202 / RECEIVED` | §7.1 |
| `outbound.picking_task.queue_changed@v1` | WMS 到 WES | WMS 调整尚未准备任务的队列信息 | `202 / RECEIVED` | §7.1 |
| `outbound.picking_task.prepare@v1` | WES 到 WMS | WES 选中任务和候选 WorkLine | `202 / PREPARE_ACCEPTED`，随后接收计划增量 | §7.2 |
| `outbound.picking_task.plan_delta@v1` | WMS 到 WES | WMS 形成一批资源、追加或取消 | `202 / RECEIVED` | §8 |
| `outbound.bin.inbound_batch@v1` | WES 到 WMS | 五层货架可靠到位并预留入料位置 | `200 / DECIDED`：`READY \| NO_BATCH \| FACE_DONE` | §9.2.1 |
| `outbound.bin.return_batch@v1` | WES 到 WMS | `RETURN_BUFFER` 出现可退箱 Bin | `200 / DECIDED`：`READY \| NO_BATCH \| RACK_PREPARATION_REQUIRED` | §9.2.2 |
| `outbound.bin.work_plan@v1` | WES 到 WMS | Bin 到达工作位并完成扫码 | `200 / DECIDED`：`READY \| NO_WORK \| WAIT` | §9.3 |
| `outbound.rack.clearance_decide@v1` | WES 到 WMS | 货架满足清场门禁 | `200 / DECIDED`：`READY \| WAIT` | §9.4 |
| `outbound.material.decide@v1` | WES 到 WMS | 料盘形成完整扫码证据 | `200 / DECIDED`：`ACCEPT \| REJECT \| WAIT` | §10.2 |
| `outbound.source.empty_decide@v1` | WES 到 WMS | 设备形成可靠空取证据 | `200 / DECIDED`：`RETRY \| WAIT \| SOURCE_DONE` | §12.2 |
| `outbound.bin.ng_exit_report@v1` | WES 到 WMS | Bin 可靠到达 `NG_EXIT` | `200 / RECORDED` | §12.3 |
| `outbound.material.movement_report@v1` | WES 到 WMS | 正常 PUT 或单盘 NG 放置完成 | `200 / RECORDED` | §12.4 |
| `outbound.picking_task.completion_confirm@v1` | WES 到 WMS | WES 本地完成门禁成立 | `200 / DECIDED`：`COMPLETED \| NOT_COMPLETED` | §13 |
| `outbound.picking_task.transport_recovery_decided@v1` | WMS 到 WES | WMS 为确定失败的 Transport 形成替代方案 | `202 / RECEIVED` | §14.1 |

不定义独立的来源恢复 operation。来源缺口补充、业务追加和指定 Bin 取消都使用
`outbound.picking_task.plan_delta@v1`，共享同一条 `plan_revision` 顺序链。

## 6. ID 和版本由谁生成

| 字段 | 生成方 | 规则 |
| --- | --- | --- |
| `task_id` | WMS | PickingTask 稳定身份，发布后不可变 |
| `execution_id` | WES | 本次任务在一条 WorkLine 上的执行实例；准备请求前持久化，不跨任务复用 |
| `operation_id` | 当前消息发起方 | 一次不可变消息的幂等身份；每批计划增量使用新的 ID |
| `previous_operation_id` | 引用既有请求 | `WAIT`、`NO_BATCH` 或 `NOT_COMPLETED` 后，以新请求重新求值时引用同一证据/执行实例的直接前序请求；首次请求禁止，不产生新身份 |
| `prepare_operation_id` | 引用 WES 准备请求 | 每批计划增量引用其所属准备请求，不产生新身份 |
| `queue_revision` | WMS | 从 1 开始连续递增，只用于尚未准备任务的队列更新 |
| `plan_revision` | WMS | 同一 `task_id + execution_id` 从 1 开始连续递增，统一排序初始资源、补充、追加和取消 |
| `last_applied_plan_revision` | WES 引用已接纳版本 | 状态确认时证明 WES 已连续应用到哪个计划版本；尚无计划时为 `0`，只作版本围栏，不表达完成数量 |
| `direct_pick_execution_id` | WMS | 一个退料货架 SLOT 的稳定业务成员，来源接纳后不可改写 |
| `bin_work_execution_id` | WMS | 一个候选 Bin 的稳定业务成员；取消和完成都必须引用该 ID，不能只用 `bin_id` |
| `bin_execution_id` | WES | 一个物理 Bin 本次进入 WorkLine 的执行实例；同一 Bin 再次入线必须新建 |
| `cell_execution_id` | WMS | SCAN2 后形成的 Cell 工作成员，绑定父 Bin 工作成员后不可变 |
| `material_execution_id` | WES | 一盘已形成扫码证据的本地执行身份 |
| `target_assignment_id` | WMS | 任务内不可变目标窗口身份，绑定货架、面和窗口代际 |
| `bin_scan_evidence_id` | WES | SCAN2 的 Bin 身份和到位证据；同一证据只能形成一个终局工作计划 |
| `scan_evidence_id` | WES | 一盘完整六合一码的不可变扫码证据；同一证据只能形成一个终局接受或拒绝 |
| `source_observation_id` | WES | 一次可靠空取证据；与来源、位置和设备结果一起不可变 |
| `bin_observation_id` | WES | 一次触发 Bin NG 的身份或方向观察证据 |
| `ng_evidence_id` | WES | 一次物理 NG 到位事实；每次可靠到位生成独立身份 |
| `cause_ng_evidence_id` | 引用既有事实 | CELL NG 后 Bin 出口 Fact 对前序料盘 NG Fact 的引用，不产生新身份 |
| `client_request_id` | WES 出库业务模块 | 调用 Transport 的稳定幂等号，与完整 Transport 输入原子持久化 |
| `transport_task_id` | WES Transport | 一个可靠搬运执行对象身份 |
| `command_code` | WES DeviceCommand | 一个设备命令的稳定身份；逐盘位置 Fact 只引用执行本次放置的原值，不生成业务别名 |

`source_lock_generation`、`face_window_generation` 和 Transport `outcome_version` 是围栏或版本计数，不是 ID。各自只能由对应事实的权威方
生成，不能相互代替。

## 7. PickingTask 发布与准备

### 7.1 任务发布

WMS 发布任务：

```json
{
  "operation_id": "019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
  "operation": "outbound.picking_task.issued@v1",
  "timestamp": 1786060800000,
  "data": {
    "task_id": "PICK-20260811-001",
    "queue_revision": 1,
    "dispatch_sequence": 100,
    "not_before": 1786060800000
  }
}
```

`not_before` 可省略。发布消息禁止携带 `workline_code`、来源货架、Bin、Cell、料盘、目标货架、SLOT、容量、TransportTask 或
DeviceCommand。`queue_changed@v1` 只允许在任务尚未进入 `PREPARING` 前以连续 `queue_revision` 修改 `dispatch_sequence` 或
`not_before`。

队列更新示例：

```json
{
  "operation_id": "019f12d1-1198-72cb-a980-d83af6ab9df8",
  "operation": "outbound.picking_task.queue_changed@v1",
  "timestamp": 1786061000000,
  "data": {
    "task_id": "PICK-20260811-001",
    "queue_revision": 2,
    "dispatch_sequence": 90,
    "not_before": 1786061000000
  }
}
```

字段字典：

| JSON Path | `issued` | `queue_changed` | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- | --- |
| `data.task_id` | 必填 | 必填 | string / WMS | PickingTask 稳定身份；不要求等于上游拣料单号，WES 不得推导或重建 |
| `data.queue_revision` | 必填且固定为 `1` | 必填且为当前值 `+1` | positive integer / WMS | 只排序队列更新；不能代替 `plan_revision` |
| `data.dispatch_sequence` | 必填 | 条件 | positive integer / WMS | 自动出库任务池内唯一业务优先序，值越小优先级越高；更新至少改变本字段或 `not_before` |
| `data.not_before` | 可选 | 条件 | UTC Unix 毫秒 / WMS | 任务最早可领取时间；省略于发布表示立即具备时间条件，省略于更新表示保持原值 |

需要把已有 `not_before` 恢复为立即可执行时，`queue_changed` 必须发送不晚于当前时间的明确值；禁止发送 `null` 或清除标志。
同一任务只能被 `issued` 首次接纳一次；换 `operation_id` 重复发布相同 `task_id` 返回 `409 / CONFLICT + STATE_CONFLICT`。
`queue_changed` 只作用于 `QUEUED`，不能抢占 `PREPARING | EXECUTING`。两个 Event 成功时均返回 `202 / RECEIVED`，同身份同内容
重放返回 `200 / DUPLICATE`。

### 7.2 准备请求

WES 选择任务和候选 WorkLine 后发送：

```json
{
  "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804da",
  "operation": "outbound.picking_task.prepare@v1",
  "timestamp": 1786060810000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "workline_code": "SORTING-LINE-01"
  }
}
```

WMS 在可靠持久化请求并建立异步运算义务后返回 `202 / PREPARE_ACCEPTED`。WES 保持候选 WorkLine，不能因为等待首批结果而换线
或发起第二次准备。响应未知、`BUSY` 或 `UNAVAILABLE` 时使用原 `operation_id` 和原 Payload 重试。

相同 `operation_id` 和相同 Payload 的准备请求重试，WMS 必须重放首次 `202 / PREPARE_ACCEPTED` 的完整响应，包括原
`timestamp + data`，不能改为 `DUPLICATE`。相同 ID 但 Payload 不同仍返回 `409 / CONFLICT`。

字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 必须引用当前仍为 `QUEUED`、且已被 WES 原子领取的 PickingTask |
| `data.execution_id` | 是 | string / WES | 本次本地执行实例；首次外发前与候选 WorkLine 原子持久化，重试不变 |
| `data.workline_code` | 是 | code / WES 配置 | WES 根据本地准入选择的具体 WorkLine；WMS 只据此计算关联 STATION 资源，不得改派另一条线 |

成功 ACK 的 `data={}`。同一 `task_id` 同时出现另一 `execution_id`、同一 `execution_id` 改变 `workline_code`，或任务已开始另一
执行实例时，WMS 返回 `409 / CONFLICT + STATE_CONFLICT`。`PREPARE_ACCEPTED` 只表示耗时运算义务已持久化，不表示存在目标窗口、
来源成员、TransportTask 或 DeviceCommand。

## 8. 分批计划增量

### 8.1 DTO

#### 8.1.1 首批目标窗口与直接取料成员

WMS 每完成一批可独立锁定的资源，就发送一个不可变增量：

```json
{
  "operation_id": "019f3401-4a10-7b1a-aab5-f2df785324d1",
  "operation": "outbound.picking_task.plan_delta@v1",
  "timestamp": 1786060815000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "prepare_operation_id": "019f3400-0e17-7d2a-b944-3cf7953804da",
    "plan_revision": 1,
    "added_target_windows": [
      {
        "target_assignment_id": "TARGET-WINDOW-001",
        "rack_id": "TRANSFER-RACK-01",
        "rack_face": "A",
        "face_window_generation": 1
      }
    ],
    "added_direct_picks": [
      {
        "direct_pick_execution_id": "DIRECT-PICK-001",
        "source_lock_generation": 7,
        "source_locator": {
          "type": "RACK_SLOT",
          "rack_id": "RETURN-RACK-01",
          "rack_face": "A",
          "slot_id": "A-03"
        },
        "target_assignment_id": "TARGET-WINDOW-001"
      }
    ]
  }
}
```

`plan_revision=1` 是唯一初始增量，`added_target_windows` 必须且只能包含一个初始目标窗口，可以同时新增来源成员，也可以只
定义目标窗口。`plan_revision>=2` 都是普通后续增量，不区分业务变更和来源恢复，并禁止再次携带
`added_target_windows`。后续目标窗口只允许由逐盘 `outbound.material.decide@v1` 的终局 `ACCEPT` 原子定义，避免计划回调和
同步决定形成两个无共同顺序的窗口写入通道。后续增量必须新增至少一个来源成员或者取消一个 Bin 工作成员；不发送空增量。

#### 8.1.2 追加 Bin 工作成员

后续增量可以引用既有目标窗口追加一个或多个候选 Bin。没有直接取料成员或取消项时，省略对应字段，不发送空数组：

```json
{
  "operation_id": "019f3402-5b21-7c2b-bbc6-03e0896435e2",
  "operation": "outbound.picking_task.plan_delta@v1",
  "timestamp": 1786060820000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "prepare_operation_id": "019f3400-0e17-7d2a-b944-3cf7953804da",
    "plan_revision": 2,
    "added_bin_works": [
      {
        "bin_work_execution_id": "BIN-WORK-001",
        "bin_id": "BIN-001",
        "source_lock_generation": 11,
        "source_locator": {
          "type": "RACK_BIN_SLOT",
          "rack_id": "RACK-5F-001",
          "rack_face": "A",
          "slot_id": "A-01"
        },
        "target_assignment_id": "TARGET-WINDOW-001"
      },
      {
        "bin_work_execution_id": "BIN-WORK-002",
        "bin_id": "BIN-002",
        "source_lock_generation": 12,
        "source_locator": {
          "type": "RACK_BIN_SLOT",
          "rack_id": "RACK-5F-001",
          "rack_face": "A",
          "slot_id": "A-02"
        },
        "target_assignment_id": "TARGET-WINDOW-001"
      }
    ]
  }
}
```

`added_bin_works[]` 每项必须完整携带新的 `bin_work_execution_id`、物理 `bin_id`、本成员的
`source_lock_generation`、精确 `RACK_BIN_SLOT source_locator` 和有效 `target_assignment_id`。它只增加顶层候选 Bin，不能提前
携带 Cell；Cell 仍在该 Bin 实际到达 SCAN2 后由 `outbound.bin.work_plan@v1` 创建。

#### 8.1.3 取消指定 Bin 工作成员

取消通过更高版本引用已经接纳的 `bin_work_execution_id`，不能只按物理 `bin_id` 取消：

```json
{
  "operation_id": "019f3403-6c32-7d3c-acd7-14f1907546f3",
  "operation": "outbound.picking_task.plan_delta@v1",
  "timestamp": 1786060825000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "prepare_operation_id": "019f3400-0e17-7d2a-b944-3cf7953804da",
    "plan_revision": 3,
    "cancelled_bin_works": [
      {
        "bin_work_execution_id": "BIN-WORK-002"
      }
    ]
  }
}
```

取消原因没有 WES 程序消费者，因此不进入 `cancelled_bin_works[]`；WMS 在自己的业务审计中保存原因。实际处理始终依据第 11.2 节的不可逆物理
安全点。取消项不得改写原成员的 `bin_id`、来源、锁代际或目标窗口，也不得携带替代成员；需要追加替代 Bin 时，在同一增量的
`added_bin_works[]` 中创建新成员，或通过下一连续版本追加。

`added_target_windows` 仅在 `plan_revision=1` 必填且固定一项。其余三个数组 `added_direct_picks`、`added_bin_works`、
`cancelled_bin_works` 均为条件可选；字段出现时必须包含 `1..N` 项，没有该类变化时省略。首批的目标窗口本身就是有效变化；
`plan_revision>=2` 必须至少携带一个非空的来源新增或 Bin 取消数组。

字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 必须等于准备请求引用的 PickingTask |
| `data.execution_id` | 是 | string / WES 原值 | 必须等于准备请求中的本地执行实例 |
| `data.prepare_operation_id` | 是 | UUIDv7 / WES 原值 | 引用已经取得 `PREPARE_ACCEPTED` 的准备请求，不生成新身份 |
| `data.plan_revision` | 是 | positive integer / WMS | 同一 `task_id + execution_id` 从 1 连续递增；每批增量唯一 |
| `data.added_target_windows[]` | revision 1 必填 | array[1] / WMS | 唯一初始目标窗口；revision 2 及以后禁止出现 |
| `data.added_target_windows[].target_assignment_id` | 条件 | string / WMS | 任务内唯一；首次接纳后永久绑定本项窗口定义 |
| `data.added_target_windows[].rack_id` | 条件 | string / WMS | WMS 已建立业务占用的转运货架 |
| `data.added_target_windows[].rack_face` | 条件 | code / WMS | 本窗口允许 PUT 的目标面 |
| `data.added_target_windows[].face_window_generation` | 条件 | positive integer / WMS | 目标工作位窗口围栏；应用新窗口时必须等于当前代际 `+1` |
| `data.added_direct_picks[]` | 条件 | array / WMS | 新增退料货架直接取料成员 |
| `data.added_direct_picks[].direct_pick_execution_id` | 条件 | string / WMS | 新成员稳定身份，不得在后续版本复用或改写 |
| `data.added_direct_picks[].source_lock_generation` | 条件 | positive integer / WMS | 当前来源锁围栏；后续决定必须精确引用 |
| `data.added_direct_picks[].source_locator` | 条件 | `RACK_SLOT` / WMS | 精确退料货架、面和 SLOT；成员接纳后不可变 |
| `data.added_direct_picks[].target_assignment_id` | 条件 | string / WMS | 引用首批初始窗口，或满足第 8.2 节位置 Fact 确认条件的既有窗口 |
| `data.added_bin_works[]` | 条件 | array / WMS | 新增候选五层货架 Bin 工作成员；禁止提前携带 Cell |
| `data.added_bin_works[].bin_work_execution_id` | 条件 | string / WMS | 新成员稳定身份；取消和后续工作计划按此引用 |
| `data.added_bin_works[].bin_id` | 条件 | string / WMS | WMS 权威候选 Bin 身份；同一任务不得同时存在两个未终态成员指向同一 Bin |
| `data.added_bin_works[].source_lock_generation` | 条件 | positive integer / WMS | 当前来源锁围栏 |
| `data.added_bin_works[].source_locator` | 条件 | `RACK_BIN_SLOT` / WMS | 精确五层货架、面和 Bin SLOT；成员接纳后不可变 |
| `data.added_bin_works[].target_assignment_id` | 条件 | string / WMS | 引用首批初始窗口，或满足第 8.2 节位置 Fact 确认条件的既有窗口 |
| `data.cancelled_bin_works[]` | 条件 | array / WMS | 取消已经接纳的 Bin 工作成员；重复取消终态成员时不产生业务动作；禁止取消 DirectPick 或 Cell |
| `data.cancelled_bin_works[].bin_work_execution_id` | 条件 | string / WMS 原值 | 必须唯一命中当前任务执行中的既有成员，不能只按 `bin_id` 猜测 |

成功接纳返回 `202 / RECEIVED + data={}`；同一 `operation_id` 和同一 Payload 重放返回 `200 / DUPLICATE + data={}`。任何一项
引用未定义目标窗口、重复成员、未知取消成员或改写既有不可变字段时，整个 revision 原子拒绝，不允许部分接纳。

### 8.2 发布与接纳规则

- WMS 内部可以并行计算，同一 `task_id + execution_id` 的增量必须按 `plan_revision` 串行发布。
- `plan_revision` 从 1 开始，每次恰好加一；前一版本未取得稳定 ACK 前不得发布后一版本。
- `plan_revision=1` 必须定义且只定义一个初始目标窗口，可以同时新增来源成员；后续 revision 禁止新增目标窗口。
- WMS 内部资源计算是否结束不是 WES 执行事实，不进入计划增量 DTO。WMS 可以继续发布更高 revision，直到其权威任务状态完成。
- 初始目标窗口、直接取料成员和 Bin 工作成员的 ID 不得重复；已经接纳的来源、目标、锁代际或父子关系不得被后续版本改写。
- 新增来源成员必须引用初始计划或此前终局物料 `ACCEPT` 已经定义的有效 `target_assignment_id`。缺少有效目标窗口时不得先取盘、
  后补目标；计划增量只有在该窗口已经被一条 `outbound.material.movement_report@v1` 回显并由 WMS 确认为
  `RECORDED | DUPLICATE` 后，才允许引用由物料 `ACCEPT` 创建的窗口。
- 同一任务不得同时存在两个未终态、指向同一物理 `bin_id` 的 Bin 工作成员。
- 取消只接受已经接纳的 `bin_work_execution_id`；未知成员、取消 DirectPick 或取消 Cell 均返回 `409 / CONFLICT`。

WES 收到回调后按以下顺序处理：

1. 先把原始信封可靠保存为 `InboundEvidence`。
2. 在事务中校验 operation 幂等、执行实例、revision 连续性和所有引用。
3. 原子新增或标记取消业务成员，并更新 `last_applied_plan_revision`。
4. 提交后返回 `202 / RECEIVED`；之后才唤醒相关 TransportTask 或 DeviceCommand 编排。

### 8.3 首批开工

WES 接纳任一 revision 后，以下任一局部工作集完整时即可推进：

- 目标窗口完整：可以按可靠位置投影把对应转运货架运到该 WorkLine 的固定目标位。
- 直接取料成员的来源、来源锁和目标窗口引用完整：可以并行请求退料货架和目标转运货架到位。
- Bin 工作成员的来源、来源锁和目标窗口引用完整：可以请求五层货架与目标转运货架到位；CTU 搬运动作仍必须等待货架实际到位。

计划增量不包含运输起点推测、设备命令或 CTU 内部动作。WES 只从可靠 `PositionProjection` 读取运输起点，并使用 WorkLine 静态
拓扑中的不同货架类型目标位。

## 9. 货架、Bin 和 Cell 执行

### 9.1 并行货架运输

转运货架、退料货架和五层货架的 TransportTask 相互独立，可以在没有位置或设备冲突时并行。每次创建 TransportTask 前，
WES 出库业务模块必须把计划增量 `operation_id`、执行阶段、完整 Transport 输入和 `client_request_id` 原子持久化。崩溃恢复只能使用原
`client_request_id` 和原 Payload 重放。

Transport 的 ACK、结果、`UNKNOWN` 和恢复遵循 Transport 履约合同。计划增量 ACK 绝不表示货架已经到位；WES 也不能根据 WMS
业务计划伪造位置事实。

### 9.2 CTU 入站和退箱

#### 9.2.1 入站批次

五层货架可靠到达 CTU 作业位后，WES 从 `INGRESS_BUFFER` 原子预留 `1..4` 个具体位置，再请求：

```json
{
  "operation_id": "019f3405-2200-7b01-8b01-000000000001",
  "operation": "outbound.bin.inbound_batch@v1",
  "timestamp": 1786064700000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "rack_id": "RACK-5F-001",
    "rack_face": "A",
    "rack_transport_task_id": "TRANSPORT-RACK-ARRIVAL-001",
    "rack_outcome_version": 1,
    "reserved_ingress_positions": [
      {
        "type": "HANDOFF_POSITION",
        "location_code": "INGRESS_BUFFER_01"
      },
      {
        "type": "HANDOFF_POSITION",
        "location_code": "INGRESS_BUFFER_02"
      }
    ]
  }
}
```

`READY` 响应：

```json
{
  "operation_id": "019f3405-2200-7b01-8b01-000000000001",
  "code": "DECIDED",
  "timestamp": 1786064700100,
  "data": {
    "result": "READY",
    "moves": [
      {
        "bin_work_execution_id": "BIN-WORK-001",
        "bin_id": "BIN-001",
        "source": {
          "type": "RACK_BIN_SLOT",
          "rack_id": "RACK-5F-001",
          "rack_face": "A",
          "slot_id": "A-01"
        },
        "target": {
          "type": "HANDOFF_POSITION",
          "location_code": "INGRESS_BUFFER_01"
        }
      }
    ]
  }
}
```

`NO_BATCH` 响应示例：

```json
{
  "operation_id": "019f3405-2200-7b01-8b01-000000000002",
  "code": "DECIDED",
  "timestamp": 1786064700200,
  "data": {
    "result": "NO_BATCH",
    "reason_code": "MEMBER_NOT_READY",
    "retry_after_ms": 1000
  }
}
```

`FACE_DONE` 响应示例：

```json
{
  "operation_id": "019f3405-2200-7b01-8b01-000000000003",
  "code": "DECIDED",
  "timestamp": 1786064700300,
  "data": {
    "result": "FACE_DONE"
  }
}
```

请求字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 当前 PickingTask |
| `data.execution_id` | 是 | string / WES 原值 | 当前任务执行实例 |
| `data.rack_id` | 是 | string / 位置投影 | 已可靠到达 CTU 作业位的五层货架 |
| `data.rack_face` | 是 | code / Transport 结果 | 货架实际到达面，不能使用计划面推测 |
| `data.rack_transport_task_id` | 是 | string / Transport | 支撑货架到位事实的 TransportTask |
| `data.rack_outcome_version` | 是 | positive integer / Transport | 当前已接纳 `SUCCEEDED` 结果版本；ACK 或未知结果禁止请求批次 |
| `data.reserved_ingress_positions[]` | 是 | `HANDOFF_POSITION[1..4]` / WES | 本 operation 已原子预留且互不重复的入料位置；不能只发送数量 |
| `data.previous_operation_id` | 条件 | UUIDv7 / 前序请求 | 仅前次 `NO_BATCH` 后重新求值时必填，引用直接前序批次请求；首次请求禁止 |

`READY` 字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.result` | 是 | 固定 `READY` / WMS | WMS 已原子持久化完整 moves，并消费所选成员的一次性入站资格 |
| `data.moves[]` | 是 | array[1..4] / WMS | 一次 CTU 调用的原子成员闭集；WES 禁止拆分、合并或部分执行 |
| `data.moves[].bin_work_execution_id` | 是 | string / WMS 原值 | 当前有效且未取消的 Bin 工作成员 |
| `data.moves[].bin_id` | 是 | string / WMS 原值 | 必须匹配该工作成员冻结的物理 Bin |
| `data.moves[].source` | 是 | `RACK_BIN_SLOT` / WMS 原值 | 必须等于成员冻结来源，且属于请求的 `rack_id + rack_face` |
| `data.moves[].target` | 是 | `HANDOFF_POSITION` / WMS 引用 | 必须来自请求预留集合，且各 move 目标互不重复 |

不能形成 `READY` 时，WMS 返回以下闭集：

| `data.result` | 必填字段 | 禁止字段 | 语义 |
| --- | --- | --- | --- |
| `NO_BATCH` | `reason_code + retry_after_ms` | `moves` | 当前快照暂不能形成批次；原因是 `CTU_CAPACITY_UNAVAILABLE \| MEMBER_NOT_READY` |
| `FACE_DONE` | 无 | `moves`、`reason_code`、`retry_after_ms` | WMS 永久封口当前任务下的该 `rack_id + rack_face`，确认以后不会再追加该面的 Bin 工作成员 |

`FACE_DONE` 是单调业务事实，不是“当前快照暂时为空”。WES 接纳后记录该任务货架面的封口；后续计划增量再次新增同一
`rack_id + rack_face` 的 Bin 工作成员必须返回 `409 / CONFLICT + STATE_CONFLICT` 并进入对账。WMS 尚不能作出永久承诺时只能
返回 `NO_BATCH`。

`READY` 时保留被选位置、释放未选位置；`NO_BATCH | FACE_DONE` 释放本 operation 全部位置。收到 `NO_BATCH` 后，WES 在新事实
到达或 `retry_after_ms` 到期时使用新的 `operation_id` 重新求值，并以 `previous_operation_id` 引用直接前序批次请求；不得重放
已经得到终局决定的原请求。响应未知、`BUSY` 或 `UNAVAILABLE` 时保留全部预留并原身份重试；`CONFLICT` 时保留预留进入对账，
禁止按超时释放。

#### 9.2.2 退箱批次

正常 Bin 可靠到达 `RETURN_BUFFER` 后，WES 才把实际候选发送给 WMS：

```json
{
  "operation_id": "019f3406-2200-7b03-8b01-000000000003",
  "operation": "outbound.bin.return_batch@v1",
  "timestamp": 1786065050000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "rack_id": "RACK-5F-001",
    "rack_face": "A",
    "rack_transport_task_id": "TRANSPORT-RACK-ARRIVAL-001",
    "rack_outcome_version": 1,
    "return_candidates": [
      {
        "bin_work_execution_id": "BIN-WORK-001",
        "bin_execution_id": "BIN-EXEC-001",
        "bin_id": "BIN-001",
        "source": {
          "type": "HANDOFF_POSITION",
          "location_code": "RETURN_BUFFER_01"
        }
      }
    ]
  }
}
```

`READY` 响应：

```json
{
  "operation_id": "019f3406-2200-7b03-8b01-000000000003",
  "code": "DECIDED",
  "timestamp": 1786065050100,
  "data": {
    "result": "READY",
    "moves": [
      {
        "bin_work_execution_id": "BIN-WORK-001",
        "bin_execution_id": "BIN-EXEC-001",
        "bin_id": "BIN-001",
        "source": {
          "type": "HANDOFF_POSITION",
          "location_code": "RETURN_BUFFER_01"
        },
        "target": {
          "type": "RACK_BIN_SLOT",
          "rack_id": "RACK-5F-001",
          "rack_face": "A",
          "slot_id": "A-08"
        }
      }
    ]
  }
}
```

`NO_BATCH` 响应示例：

```json
{
  "operation_id": "019f3406-2200-7b03-8b01-000000000004",
  "code": "DECIDED",
  "timestamp": 1786065050200,
  "data": {
    "result": "NO_BATCH",
    "reason_code": "RETURN_TARGET_NOT_READY",
    "retry_after_ms": 1000
  }
}
```

当前五层货架换面响应示例：

```json
{
  "operation_id": "019f3406-2200-7b03-8b01-000000000005",
  "code": "DECIDED",
  "timestamp": 1786065050300,
  "data": {
    "result": "RACK_PREPARATION_REQUIRED",
    "source_rack_preparation": {
      "mode": "ROTATE",
      "next_rack_face": "B"
    }
  }
}
```

更换五层货架响应示例：

```json
{
  "operation_id": "019f3406-2200-7b03-8b01-000000000006",
  "code": "DECIDED",
  "timestamp": 1786065050400,
  "data": {
    "result": "RACK_PREPARATION_REQUIRED",
    "source_rack_preparation": {
      "mode": "REPLACE",
      "clearance_target_location": {
        "type": "RACK_POSITION",
        "location_code": "RACK_5F_RETURN_01"
      },
      "next_rack_id": "RACK-5F-002",
      "next_rack_face": "A"
    }
  }
}
```

字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.task_id`、`data.execution_id` | 是 | string / 原值 | 当前任务及执行实例 |
| `data.rack_id`、`data.rack_face` | 是 | string + code / 位置投影 | 当前可靠到达、准备承接退箱的五层货架和实际面 |
| `data.rack_transport_task_id`、`data.rack_outcome_version` | 是 | string + positive integer / Transport | 支撑当前货架到位的确定成功证据 |
| `data.return_candidates[]` | 是 | array[1..N] / WES | 当前可靠位于 `RETURN_BUFFER`、且未进入其他退箱决定的正常 Bin |
| `data.previous_operation_id` | 条件 | UUIDv7 / 前序请求 | 仅前次 `NO_BATCH` 后重新求值时必填，引用直接前序批次请求；首次请求禁止 |
| `data.return_candidates[].bin_work_execution_id` | 是 | string / WMS 原值 | 候选所属 Bin 工作成员 |
| `data.return_candidates[].bin_execution_id` | 是 | string / WES | 本次物理 Bin 入线执行实例 |
| `data.return_candidates[].bin_id` | 是 | string / WMS 原值 | 必须与工作成员及执行实例绑定一致 |
| `data.return_candidates[].source` | 是 | `HANDOFF_POSITION` / 位置投影 | 实际 `RETURN_BUFFER` 位置；在途、工作位、NG 或未知位置禁止发送 |
| `data.result` | 响应必填 | enum / WMS | `READY \| NO_BATCH \| RACK_PREPARATION_REQUIRED` |
| `data.moves[]` | `READY` 必填 | array[1..4] / WMS | 从候选中选择的原子子集；不得包含请求外成员 |
| `data.moves[].bin_work_execution_id`、`bin_execution_id`、`bin_id`、`source` | `READY` 必填 | 原请求值 / WMS 回显 | 必须与对应候选完全一致 |
| `data.moves[].target` | `READY` 必填 | `RACK_BIN_SLOT` / WMS | WMS 返回前已原子预留的当前货架面不同空储位 |

非 `READY` 响应联合：

| `data.result` | 必填字段 | 禁止字段 | 语义 |
| --- | --- | --- | --- |
| `NO_BATCH` | `reason_code + retry_after_ms` | `moves`、`source_rack_preparation` | `CTU_CAPACITY_UNAVAILABLE \| RETURN_TARGET_NOT_READY`；不授权任何退箱目标 |
| `RACK_PREPARATION_REQUIRED` | `source_rack_preparation` | `moves`、`reason_code` | 当前货架面不能承接，WMS 已形成可执行换面或换架方案 |

`source_rack_preparation` 是严格联合：

| `mode` | 必填字段 | 固定语义 |
| --- | --- | --- |
| `ROTATE` | `next_rack_face` | 当前 `rack_id` 换到指定面；Transport 成功后以新 operation 重新请求退箱批次 |
| `REPLACE` | `clearance_target_location + next_rack_id + next_rack_face` | 当前架清场后把新五层货架运入固定 CTU 作业位；到位后重新请求批次 |

`next_rack_face` 是 WMS 选择的非空货架面编码；`next_rack_id` 是 WMS 已建立任务级退箱承接占用的五层货架；
`clearance_target_location` 必须是 WMS 决定的 `RACK_POSITION`，且不得等于当前 CTU 作业位。新货架来源由 WES 从可靠位置投影
读取，不进入响应；精确退箱 SLOT 只允许在
新货架可靠到位后的下一次 `READY.moves[].target` 中返回。`READY` 后 WES 只冻结所选候选并调用一次 CTU Transport；未选候选继续
留在 `RETURN_BUFFER`。

退箱请求收到 `NO_BATCH` 后，WES 在新事实到达或 `retry_after_ms` 到期时使用新的 `operation_id` 重新求值，并以
`previous_operation_id` 引用直接前序批次请求。响应未知、`BUSY` 或 `UNAVAILABLE` 才使用原身份和原 Payload 重试。

### 9.3 Bin 工作计划

Bin 到达工作位并完成 SCAN2 后，WES 持久化身份和位置证据，再发送：

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e20",
  "operation": "outbound.bin.work_plan@v1",
  "timestamp": 1786064800000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "bin_work_execution_id": "BIN-WORK-001",
    "bin_execution_id": "BIN-EXEC-001",
    "bin_scan_evidence_id": "BIN-SCAN-001",
    "bin_id": "BIN-001",
    "source_lock_generation": 11,
    "scanned_at": 1786064799900
  }
}
```

`READY` 响应：

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e20",
  "code": "DECIDED",
  "timestamp": 1786064800100,
  "data": {
    "result": "READY",
    "cells": [
      {
        "cell_execution_id": "CELL-EXEC-021",
        "cell_id": "CELL-03",
        "target_assignment_id": "TARGET-WINDOW-001"
      }
    ]
  }
}
```

`NO_WORK` 响应示例：

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e21",
  "code": "DECIDED",
  "timestamp": 1786064800200,
  "data": {
    "result": "NO_WORK"
  }
}
```

`WAIT` 响应示例：

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e22",
  "code": "DECIDED",
  "timestamp": 1786064800300,
  "data": {
    "result": "WAIT",
    "reason_code": "WORK_PLAN_NOT_READY",
    "retry_after_ms": 1000
  }
}
```

请求字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.task_id`、`data.execution_id` | 是 | string / 原值 | 当前任务及执行实例 |
| `data.bin_work_execution_id` | 是 | string / WMS 原值 | 当前已接纳且未取消的 Bin 工作成员 |
| `data.bin_execution_id` | 是 | string / WES | 当前物理 Bin 本次进入 WorkLine 的执行实例 |
| `data.bin_scan_evidence_id` | 是 | string / WES | SCAN2 身份和到位证据；首次外发前与完整请求持久化 |
| `data.bin_id` | 是 | string / 扫码证据 | 必须与 Bin 工作成员和当前 BinExecution 的已绑定身份一致 |
| `data.source_lock_generation` | 是 | positive integer / WMS 原值 | 必须等于父 Bin 工作成员冻结的锁代际 |
| `data.scanned_at` | 是 | UTC Unix 毫秒 / 设备证据 | SCAN2 可靠读码发生时间，不使用 HTTP 发送时间代替 |
| `data.previous_operation_id` | 条件 | UUIDv7 / 前序请求 | 仅 `WAIT` 后重新求值时必填，并直接引用同一证据的前序请求；首次请求禁止 |

响应联合：

| `data.result` | 必填字段 | 禁止字段 | 语义 |
| --- | --- | --- | --- |
| `READY` | `cells[1..N]` | `reason_code`、`retry_after_ms` | 当前 Bin 的终局非空工作计划 |
| `NO_WORK` | 无 | `cells`、`reason_code`、`retry_after_ms` | 当前 Bin 不再需要取料，业务成员闭合，物理 Bin 继续退箱 |
| `WAIT` | `reason_code + retry_after_ms` | `cells` | 当前不能形成稳定计划；`reason_code=WORK_PLAN_NOT_READY` |

`READY.cells[]` 字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `cell_execution_id` | 是 | string / WMS | 当前 Bin 内唯一 Cell 工作成员；首次接纳后不可复用或改写 |
| `cell_id` | 是 | string / WMS | 当前 Bin 的精确工作料格 |
| `target_assignment_id` | 是 | string / WMS | 必须引用任务内已经接纳的有效目标窗口；工作计划不得内嵌新目标窗口 |

数组顺序不表达 Cell 业务优先级或依赖。CTU 投箱顺序也不构成业务顺序；`WORK_BUFFER` 是单向 FIFO，队首没有明确
`READY | NO_WORK | NG` 结果时，后续 Bin 不能绕行。同一 `bin_scan_evidence_id` 只能形成一个终局 `READY | NO_WORK`；`WAIT`
后使用新 `operation_id` 和 `previous_operation_id` 重新求值。

### 9.4 货架清场去向决定

初始进场目标来自 WorkLine 固定拓扑，不调用本 operation。只有货架真实清场条件已经满足、且先前决定未为同一原因给出清场
去向时，WES 才发送：

```json
{
  "operation_id": "019f3408-8300-7b05-8b01-000000000005",
  "operation": "outbound.rack.clearance_decide@v1",
  "timestamp": 1786065120000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "rack_id": "TRANSFER-RACK-01",
    "current_location": {
      "type": "RACK_POSITION",
      "location_code": "OUTBOUND_TARGET_WORK_01"
    },
    "current_face": "A",
    "clearance_reason": "TARGET_REPLACED"
  }
}
```

`READY` 响应：

```json
{
  "operation_id": "019f3408-8300-7b05-8b01-000000000005",
  "code": "DECIDED",
  "timestamp": 1786065120100,
  "data": {
    "result": "READY",
    "clearance_target_location": {
      "type": "RACK_POSITION",
      "location_code": "TRANSFER_RACK_COMPLETED_01"
    }
  }
}
```

`WAIT` 响应示例：

```json
{
  "operation_id": "019f3408-8300-7b05-8b01-000000000006",
  "code": "DECIDED",
  "timestamp": 1786065120200,
  "data": {
    "result": "WAIT",
    "reason_code": "CLEARANCE_TARGET_BUSY",
    "retry_after_ms": 1000
  }
}
```

字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.task_id`、`data.execution_id` | 是 | string / 原值 | 当前任务及执行实例 |
| `data.rack_id` | 是 | string / 已接纳计划 | 来自来源成员或目标窗口；WMS 根据任务快照识别货架角色，不接收 `rack_role` |
| `data.current_location` | 是 | `RACK_POSITION` / 位置投影 | 当前可靠物理位置，禁止使用计划目标代替 |
| `data.current_face` | 是 | code / 位置投影 | 当前可靠货架面 |
| `data.clearance_reason` | 是 | enum / WES | `TARGET_REPLACED \| SOURCE_DONE \| TASK_FINISHED`；只解释触发原因，不授权执行顺序 |
| `data.previous_operation_id` | 条件 | UUIDv7 / 前序请求 | 仅 `WAIT` 后重新求值时必填；首次请求禁止 |
| `data.result` | 响应必填 | enum / WMS | `READY \| WAIT` |
| `data.clearance_target_location` | `READY` 必填 | `RACK_POSITION` / WMS | 当前货架唯一业务去向；不得等于 `current_location` |
| `data.reason_code` | `WAIT` 必填 | enum / WMS | `CLEARANCE_NOT_READY \| CLEARANCE_TARGET_BUSY` |
| `data.retry_after_ms` | `WAIT` 必填 | positive integer / WMS | 无新事实时的兜底重试间隔 |

WES 只有在没有未决 PUT、未确认位置 Fact、关联设备动作和继续使用该货架的本地成员时才能请求。`READY` 后以当前决定
`operation_id` 派生一个稳定 `client_request_id` 并创建一项货架 TransportTask；不得拆分。同一 `target_preparation.mode=REPLACE`
或 `source_rack_preparation.mode=REPLACE` 已给出当前架去向时，禁止为同一清场原因重复调用。

## 10. 逐盘扫码、晚绑定和机械臂并发

### 10.1 物理前提

料盘从原 `RACK_SLOT` 或 `BIN_CELL` 取出后无法放回。实际尺寸和完整六合一码只有料盘到达扫码台后才能确认，因此“当前盘在
扫码台等待精确 SLOT、换面或换架”是正常的单盘晚绑定状态，不是异常或设计缺陷。

扫码台最多承载一盘未决物料。这个容量由现场机构和 ECS/PLC 硬件锁保证；WES 不为它建立资源对象、锁、租约或平台释放事件。

### 10.2 物料决定

扫码证据可靠入账后，WES 调用 `outbound.material.decide@v1`：

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea1",
  "operation": "outbound.material.decide@v1",
  "timestamp": 1786063000000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "source_execution_type": "CELL",
    "source_execution_id": "CELL-EXEC-021",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_id": "BIN-001",
      "cell_id": "CELL-03"
    },
    "source_lock_generation": 11,
    "target_assignment_id": "TARGET-WINDOW-001",
    "scan_evidence_id": "SCAN-EVIDENCE-001",
    "six_in_one": {
      "HHPN": "HHPN-001",
      "MfrPN": "MFR-001",
      "Qty": "100",
      "DateCode": "2610",
      "LotCode": "LOT-001",
      "PkgID": "PKG-001"
    },
    "scanned_at": 1786062999900
  }
}
```

请求字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.task_id`、`data.execution_id` | 是 | string / 原值 | 当前任务及执行实例 |
| `data.source_execution_type` | 是 | enum / WES | `DIRECT_PICK \| CELL`；决定来源引用和位置对象联合 |
| `data.source_execution_id` | 是 | string / WMS 原值 | 分别引用 `direct_pick_execution_id` 或 `cell_execution_id` |
| `data.source_locator` | 是 | `RACK_SLOT \| BIN_CELL` / WMS 原值 | 必须等于来源成员冻结位置；类型必须匹配 `source_execution_type` |
| `data.source_lock_generation` | 是 | positive integer / WMS 原值 | 必须等于来源成员冻结锁围栏 |
| `data.target_assignment_id` | 是 | string / WMS 原值 | 来源成员当前计划目标窗口；允许 WMS 因实际扫码结果在 `ACCEPT` 中返回新的有效目标 |
| `data.scan_evidence_id` | 是 | string / WES | 当前盘不可变扫码证据；与完整六合一码原子持久化 |
| `data.six_in_one.HHPN` | 是 | string[1..256] / 扫码设备 | 物料编码，WES 原样传递 |
| `data.six_in_one.MfrPN` | 是 | string[1..256] / 扫码设备 | 制造商料号，WES 不做同义转换 |
| `data.six_in_one.Qty` | 是 | string[1..256] / 扫码设备 | 当前包装数量原文；禁止改为 JSON number，数值合法性由 WMS 判断 |
| `data.six_in_one.DateCode` | 是 | string[1..256] / 扫码设备 | 日期码原文，WES 不解析日期 |
| `data.six_in_one.LotCode` | 是 | string[1..256] / 扫码设备 | 批次码原文 |
| `data.six_in_one.PkgID` | 是 | string[1..256] / 扫码设备 | 当前完整料盘包装身份；业务唯一性由 WMS 判断 |
| `data.scanned_at` | 是 | UTC Unix 毫秒 / 扫码证据 | 完整六合一码形成时间 |
| `data.previous_operation_id` | 条件 | UUIDv7 / 前序请求 | 仅 `WAIT` 后使用同一扫码证据重新求值时必填；首次请求禁止 |

`ACCEPT` 示例：

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea1",
  "code": "DECIDED",
  "timestamp": 1786063000100,
  "data": {
    "result": "ACCEPT",
    "target_assignment_id": "TARGET-WINDOW-001",
    "target_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "A",
      "slot_id": "A-05"
    },
    "face_window_generation": 1,
    "next_source_action": "CONTINUE"
  }
}
```

需要同架换面时仍在同一个终局 `ACCEPT` 中返回：

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea5",
  "code": "DECIDED",
  "timestamp": 1786063000100,
  "data": {
    "result": "ACCEPT",
    "target_assignment_id": "TARGET-WINDOW-002",
    "target_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "B",
      "slot_id": "B-01"
    },
    "face_window_generation": 2,
    "target_preparation": {
      "mode": "ROTATE"
    },
    "next_source_action": "CONTINUE"
  }
}
```

需要换架时仍在同一个终局 `ACCEPT` 中返回：

```json
{
  "operation_id": "019f3411-af77-71fd-9bde-0df75fcdeea2",
  "code": "DECIDED",
  "timestamp": 1786063000100,
  "data": {
    "result": "ACCEPT",
    "target_assignment_id": "TARGET-WINDOW-002",
    "target_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-02",
      "rack_face": "A",
      "slot_id": "A-01"
    },
    "face_window_generation": 2,
    "target_preparation": {
      "mode": "REPLACE",
      "clearance_target_location": {
        "type": "RACK_POSITION",
        "location_code": "TRANSFER_RACK_COMPLETED_01"
      }
    },
    "next_source_action": "CONTINUE"
  }
}
```

`MATERIAL REJECT` 示例：

```json
{
  "operation_id": "019f3412-af77-71fd-9bde-0df75fcdeea3",
  "code": "DECIDED",
  "timestamp": 1786063000100,
  "data": {
    "result": "REJECT",
    "business_exception_code": "MATERIAL_REJECTED",
    "ng_locator": {
      "type": "NG_ZONE",
      "zone_code": "MATERIAL_NG_01"
    },
    "source_disposition": "CONTINUE"
  }
}
```

`CELL REJECT` 示例（独立分支）：

```json
{
  "operation_id": "019f3412-af77-71fd-9bde-0df75fcdeea6",
  "code": "DECIDED",
  "timestamp": 1786063000200,
  "data": {
    "result": "REJECT",
    "business_exception_code": "SOURCE_CELL_MISMATCH",
    "ng_locator": {
      "type": "NG_ZONE",
      "zone_code": "CELL_NG_01"
    },
    "source_disposition": "CLOSE"
  }
}
```

`WAIT` 示例：

```json
{
  "operation_id": "019f3413-af77-71fd-9bde-0df75fcdeea4",
  "code": "DECIDED",
  "timestamp": 1786063000100,
  "data": {
    "result": "WAIT",
    "reason_code": "TARGET_DECISION_BUSY",
    "retry_after_ms": 1000
  }
}
```

响应联合：

| `data.result` | 必填字段 | 禁止字段 | 语义 |
| --- | --- | --- | --- |
| `ACCEPT` | `target_assignment_id + target_locator + face_window_generation + next_source_action`，可选 `target_preparation` | REJECT/WAIT 字段 | 物料资格和唯一 PUT 目标已形成终局授权 |
| `REJECT` | `business_exception_code + ng_locator + source_disposition` | 目标和 WAIT 字段 | WMS 已形成确定 MATERIAL/CELL 业务异常和隔离去向 |
| `WAIT` | `reason_code + retry_after_ms` | 目标、NG 和来源处置字段 | 当前不能形成终局资格或精确目标；不是 NG |

`ACCEPT` 字段约束：

| JSON Path | 类型/生成方 | 语义与约束 |
| --- | --- | --- |
| `data.target_assignment_id` | string / WMS | 可以引用既有窗口，也可以由本响应原子定义当前盘的新窗口；初始窗口之外只有本 operation 可以创建窗口，接纳后不可改写 |
| `data.target_locator` | `RACK_SLOT` / WMS | WMS 已预留的唯一目标 SLOT；严格遵守第 4.4 节位置对象，不内嵌窗口代际 |
| `data.face_window_generation` | positive integer / WMS | 目标工作位窗口代际；必须与 `target_assignment_id`、目标货架和货架面一致 |
| `data.next_source_action` | enum / WMS | `CONTINUE \| SOURCE_DONE`；DirectPick 固定 `SOURCE_DONE`；它是业务决定，不是硬件安全许可 |
| `data.target_preparation.mode` | 条件 enum / WMS | `ROTATE \| REPLACE`；目标无需物理准备时整个对象省略 |
| `data.target_preparation.clearance_target_location` | `REPLACE` 必填 | 当前目标架唯一清场 `RACK_POSITION`；`ROTATE` 分支禁止 |

`ROTATE` 的目标架和目标面来自 `target_locator`。`REPLACE` 的新架和目标面也来自 `target_locator`，新架当前来源由 WES 从可靠位置
投影读取。`REPLACE.clearance_target_location` 已完成当前目标架的业务去向决定，禁止再为同一原因调用清场 operation。

本响应定义新窗口时，`face_window_generation` 必须等于该 WorkLine 目标工作位当前已接纳代际 `+1`。引用既有窗口时，
`target_assignment_id + rack_id + rack_face + face_window_generation` 必须与已保存定义完全一致。物料决定按扫码台单盘串行求值；
同一 WorkLine 尚有未闭合物料决定时，WMS 不得为该线形成第二个新目标窗口。

`REJECT.business_exception_code` 是闭集：

| 值 | 允许来源 | NG 作用域 | `source_disposition` |
| --- | --- | --- | --- |
| `MATERIAL_REJECTED` | `DIRECT_PICK \| CELL` | `MATERIAL` | `CONTINUE \| CLOSE`；`CONTINUE` 只允许 CELL |
| `SOURCE_CELL_MISMATCH` | `CELL` | `CELL` | 固定 `CLOSE` |

`ng_locator` 必须是 WMS 批准的 `NG_ZONE`。`source_disposition` 只决定料盘可靠进入 NG 区后的来源业务状态，不是设备命令。
`CONTINUE` 允许当前 Cell 继续取下一盘，`CLOSE` 在当前盘 NG 事实确认后关闭当前来源。WMS 尚不能形成这两个终局结果时必须返回
`WAIT`，不能先返回 `REJECT` 再让 WES 等待未定义的恢复动作。
`WAIT.reason_code` 是 `TARGET_DECISION_BUSY | TARGET_RACK_DRAINING | TARGET_RACK_UNAVAILABLE`；相关可靠事实变化时立即以新
`operation_id` 重新求值，没有新事实时才等待 `retry_after_ms`。

`target_preparation` 只有三种 wire 形态：无动作时省略、同架换面时 `mode=ROTATE`、换架时 `mode=REPLACE`。
`REPLACE` 固定按“当前架清场 → 新架进场 → PUT”执行。WMS 必须在同一次 `ACCEPT` 中给出当前盘最终可执行的完整方案；WES
等待 Transport 可靠到位后直接 PUT，不重新请求物料资格。

`CONTINUE` 表示当前 Cell 业务需求允许继续取下一盘；`SOURCE_DONE` 表示当前盘闭合后关闭来源。DirectPick 只能返回
`SOURCE_DONE`。同一 `scan_evidence_id` 只能形成一个终局 `ACCEPT | REJECT`；`WAIT` 后请求必须引用直接前序 operation。

### 10.3 两个机械臂并发

- 来源机械臂和目标机械臂使用不同 `device_code`，各自最多一条已接纳未终态 DeviceCommand。
- WMS 对当前盘返回 `CONTINUE` 后，只要来源机械臂没有活动命令，WES 可以在目标机械臂 PUT 当前盘期间下发下一条来源命令。
- ECS 可以接纳命令并执行不改变料盘位置的准备动作。没有现场批准的安全暂存位时，硬件锁必须在料盘离开来源前确认扫码台交接
  路径可用；不能先取出下一盘，再持盘等待扫码台释放。
- 下一盘何时离开来源并进入扫码台、两个机械臂是否会同时进入干涉区以及如何防撞，由 ECS/PLC 硬件锁决定。
- WES 不等待“扫码台已释放”事件，不保存扫码台占用锁，不实现跨机械臂软件仲裁，也不要求 ECS 暴露长命令内部步骤。
- 物料移动 Fact 的确认继续门禁容量释放、成员完成和任务完成，但不作为另一机械臂命令开始的通用前置条件。

## 11. 执行中追加与取消

### 11.1 追加

WMS 通过更高 `plan_revision` 的 `added_bin_works[]` 追加 Bin，并创建新的 `bin_work_execution_id`。新增成员遵守第 8 节的
不可变和目标引用规则。WMS 不得修改已接纳成员；任务完成后，新增需求必须发布新的 PickingTask。

### 11.2 取消安全点

WMS 通过 `cancelled_bin_works[]` 取消指定 `bin_work_execution_id`。WES 接纳后按现场事实处理：

| 现场状态 | 处理 |
| --- | --- |
| Bin 尚未开始运输 | 立即标记 `CANCELLED`，不创建后续 Transport 或 DeviceCommand |
| Bin 已运输、已进入 FIFO 或已到工作位，但本 Bin 尚无已接纳取盘命令 | 停止创建新的取盘命令；让 Bin 按单向物流正常进入退箱路径，业务成员标记 `CANCELLED` |
| 本 Bin 的取盘命令已被 ECS 接纳，或当前盘已经离开来源 | 当前盘不可取消、不可放回；必须先闭合到 WMS 指定目标或 NG，并可靠提交位置事实，再取消剩余 Cell 和未来取盘 |
| Bin 工作成员已经终态 | 接纳重复取消，仍推进当前 `plan_revision`；不回退终态，不创建补偿动作 |

取消只停止未来业务动作，不撤销已接纳 TransportTask、DeviceCommand 或已形成的位置事实。WES 不向 PLC 下发“逆向放回”命令，
也不把当前盘长期滞留扫码台作为常规取消方案。

## 12. PUT、NG、空取和事实确认

NG 作用域是 `MATERIAL | CELL | BIN`。扫码不完整、设备失败、WMS `WAIT`、目标换架等待或 Transport/PUT 结果未知不属于 NG。
可靠空取通过 `outbound.source.empty_decide@v1` 返回 `RETRY | WAIT | SOURCE_DONE`；来源补充使用下一条 `plan_delta@v1`，空取
响应不得嵌入成员集合，计划增量也不直接关闭原来源。

### 12.1 正常 PUT 的三段闭环

正常 PUT 分三步闭合：

1. WMS 通过 `outbound.material.decide@v1` 返回 `ACCEPT`，授权当前盘的 `target_assignment_id` 和精确
   `RACK_SLOT`；需要换面或换架时，先完成同一决定中的 `target_preparation`。
2. 目标货架、货架面和窗口代际与本地可靠投影一致后，WES 为目标机械臂创建 DeviceCommand。只有匹配该命令的确定
   `SUCCEEDED` CALLBACK 才表示物理 PUT 已完成；ACK、设备忙、超时或结果未知都不能生成位置事实。
3. WES 持久化设备结果和本地位置投影后，调用 `outbound.material.movement_report@v1`；WMS 原子更新权威物料位置、库存和
   目标占用，再返回 `RECORDED | DUPLICATE`。

PickingTask 合同不定义目标机械臂的供应商 `task_type`。实际设备合同附录必须为该设备冻结一个 PUT `task_type`，其 `params`
至少能够唯一关联当前 `MaterialExecution`，并携带 WMS 已授权的 `target_assignment_id`、`rack_id`、`rack_face`、`slot_id` 和
`face_window_generation`。附录必须定义成功、失败、超时、结果未知及人工对账边界；不得包含库存、容量计算、替代目标、PLC
坐标、速度、安全锁或防撞字段。WES 保存 WMS 决定与 DeviceCommand 的关联，ECS/PLC 只执行逻辑目标和硬件安全控制。

### 12.2 空取决定 DTO

只有来源机械臂的确定设备终态证明指定 `RACK_SLOT` 或 `BIN_CELL` 无料时，WES 才发送：

```json
{
  "operation_id": "019f3420-01be-7e11-b265-10de42c881f0",
  "operation": "outbound.source.empty_decide@v1",
  "timestamp": 1786065100000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "source_execution_type": "CELL",
    "source_execution_id": "CELL-EXEC-021",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_id": "BIN-001",
      "cell_id": "CELL-03"
    },
    "source_lock_generation": 11,
    "source_observation_id": "EMPTY-EVIDENCE-001",
    "command_code": "CMD-SOURCE-ARM-EMPTY-001",
    "observed_at": 1786065099900
  }
}
```

响应示例：

```json
{
  "operation_id": "019f3420-01be-7e11-b265-10de42c881f0",
  "code": "DECIDED",
  "timestamp": 1786065100100,
  "data": {
    "result": "SOURCE_DONE"
  }
}
```

`RETRY` 响应示例：

```json
{
  "operation_id": "019f3420-01be-7e11-b265-10de42c881f1",
  "code": "DECIDED",
  "timestamp": 1786065100200,
  "data": {
    "result": "RETRY"
  }
}
```

`WAIT` 响应示例：

```json
{
  "operation_id": "019f3420-01be-7e11-b265-10de42c881f2",
  "code": "DECIDED",
  "timestamp": 1786065100300,
  "data": {
    "result": "WAIT",
    "reason_code": "PLAN_DELTA_PENDING",
    "retry_after_ms": 1000
  }
}
```

字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.task_id`、`data.execution_id` | 是 | string / 原值 | 当前任务及执行实例 |
| `data.source_execution_type` | 是 | enum / WES | `DIRECT_PICK \| CELL` |
| `data.source_execution_id` | 是 | string / WMS 原值 | 分别引用 DirectPickExecution 或 CellExecution |
| `data.source_locator` | 是 | `RACK_SLOT \| BIN_CELL` / WMS 原值 | 必须与来源类型和冻结位置一致 |
| `data.source_lock_generation` | 是 | positive integer / WMS 原值 | 必须等于来源成员冻结锁围栏 |
| `data.source_observation_id` | 是 | string / WES | 本次不可变空取证据；同一证据不能更换来源或设备结果 |
| `data.command_code` | 是 | string / WES DeviceCommand | 必须已有唯一确定的无料成功结果；设备结果未知时禁止调用 |
| `data.observed_at` | 是 | UTC Unix 毫秒 / 设备结果 | 确定无料终态发生时间 |
| `data.previous_operation_id` | 条件 | UUIDv7 / 前序请求 | 仅 `WAIT` 后同一观察重新求值时必填；首次请求禁止 |

响应联合：

| `data.result` | 必填字段 | 语义 |
| --- | --- | --- |
| `RETRY` | 无 | WMS 允许对同一来源创建新的取料尝试；不关闭来源成员 |
| `WAIT` | `reason_code + retry_after_ms` | 当前来源决定尚未闭合；`reason_code=SOURCE_DECISION_BUSY \| PLAN_DELTA_PENDING` |
| `SOURCE_DONE` | 无 | WMS 确认没有未决需求缺口和物料动作，允许关闭当前来源成员 |

同一 `source_observation_id` 只能形成一个终局 `RETRY | SOURCE_DONE`。需要补充来源时，WMS 返回
`WAIT / PLAN_DELTA_PENDING`，再通过普通 `plan_delta@v1` 追加替代成员；空取响应禁止嵌入来源集合。WES 接纳并应用任一后续计划增量后，
必须使用新的 `operation_id` 重新求值所有处于 `WAIT / PLAN_DELTA_PENDING` 的空取观察，并以 `previous_operation_id` 引用各观察的
直接前序请求。WMS 确认当前观察对应的需求缺口已由新增成员承接后返回 `SOURCE_DONE`，否则继续返回 `WAIT`。计划增量本身不得
关闭 `DirectPickExecution` 或 `CellExecution`，也不增加空取观察关联字段。

### 12.3 Bin NG 出口事实 DTO

`outbound.bin.ng_exit_report@v1` 只报告 Bin 已可靠到达 `NG_EXIT`。`cause_scope=CELL` 补充 CELL NG 后的 Bin 最终位置；
`cause_scope=BIN` 才表达 Bin 自身身份或方向异常。CELL 分支示例：

```json
{
  "operation_id": "019f3421-3a20-788d-b93d-d1a150a23b0d",
  "operation": "outbound.bin.ng_exit_report@v1",
  "timestamp": 1786065195000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "bin_work_execution_id": "BIN-WORK-001",
    "bin_execution_id": "BIN-EXEC-001",
    "bin_id": "BIN-001",
    "cell_execution_id": "CELL-EXEC-021",
    "cause_scope": "CELL",
    "cause_ng_evidence_id": "CELL-NG-EVIDENCE-001",
    "ng_evidence_id": "BIN-NG-EXIT-EVIDENCE-001",
    "command_code": "CMD-BIN-NG-EXIT-001",
    "ng_exit_code": "NG_EXIT_01",
    "occurred_at": 1786065194900
  }
}
```

BIN 分支示例：

```json
{
  "operation_id": "019f3421-3a20-788d-b93d-d1a150a23b0e",
  "operation": "outbound.bin.ng_exit_report@v1",
  "timestamp": 1786065200000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "cause_scope": "BIN",
    "bin_identity_kind": "KNOWN_CANDIDATE",
    "bin_work_execution_id": "BIN-WORK-001",
    "bin_execution_id": "BIN-EXEC-001",
    "bin_id": "BIN-001",
    "bin_observation_id": "BIN-OBSERVATION-001",
    "reason_code": "BIN_DIRECTION_INVALID",
    "ng_evidence_id": "BIN-NG-EVIDENCE-001",
    "command_code": "CMD-BIN-NG-001",
    "ng_exit_code": "NG_EXIT_01",
    "occurred_at": 1786065199900
  }
}
```

未匹配物理 Bin 分支示例：

```json
{
  "operation_id": "019f3421-3a20-788d-b93d-d1a150a23b0f",
  "operation": "outbound.bin.ng_exit_report@v1",
  "timestamp": 1786065201000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "cause_scope": "BIN",
    "bin_identity_kind": "UNMATCHED_PHYSICAL_BIN",
    "expected_bin_work_execution_id": "BIN-WORK-001",
    "bin_execution_id": "BIN-EXEC-UNMATCHED-001",
    "bin_observation_id": "BIN-OBSERVATION-002",
    "reason_code": "BIN_NOT_CANDIDATE",
    "observed_bin_code": "BIN-UNKNOWN-009",
    "ng_evidence_id": "BIN-NG-EVIDENCE-002",
    "command_code": "CMD-BIN-NG-002",
    "ng_exit_code": "NG_EXIT_01",
    "occurred_at": 1786065200900
  }
}
```

公共字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.task_id`、`data.execution_id` | 是 | string / 原值 | 当前任务及执行实例 |
| `data.bin_execution_id` | 是 | string / WES | 本次物理 Bin 入线执行实例 |
| `data.cause_scope` | 是 | enum / WES | `CELL \| BIN`，决定分支字段闭集 |
| `data.ng_evidence_id` | 是 | string / WES | 本次 Bin 出口可靠到位事实身份；每次到位独立生成 |
| `data.command_code` | 是 | string / WES DeviceCommand | 支撑 Bin 已到达 NG 出口的唯一确定成功命令 |
| `data.ng_exit_code` | 是 | code / WorkLine 静态拓扑 | 实际到达的批准 NG 出口，不得临时生成 |
| `data.occurred_at` | 是 | UTC Unix 毫秒 / 设备结果 | Bin 到达出口的物理发生时间 |

分支字段闭集：

| 分支 | 必填字段 | 条件字段 | 禁止字段 |
| --- | --- | --- | --- |
| `cause_scope=CELL` | `bin_work_execution_id + bin_id + cell_execution_id + cause_ng_evidence_id` | 无 | `bin_identity_kind`、`bin_observation_id`、`reason_code`、`observed_bin_code` |
| `cause_scope=BIN, bin_identity_kind=KNOWN_CANDIDATE` | `bin_work_execution_id + bin_id + bin_observation_id + reason_code` | 无 | `cell_execution_id`、`cause_ng_evidence_id`、`observed_bin_code` |
| `cause_scope=BIN, bin_identity_kind=UNMATCHED_PHYSICAL_BIN` | `expected_bin_work_execution_id + bin_observation_id + reason_code` | `observed_bin_code` | `bin_work_execution_id`、`bin_id`、`cell_execution_id`、`cause_ng_evidence_id` |

分支专属字段字典：

| JSON Path | 类型/生成方 | 语义与约束 |
| --- | --- | --- |
| `data.bin_identity_kind` | enum / WES | `KNOWN_CANDIDATE \| UNMATCHED_PHYSICAL_BIN`；仅 BIN 分支出现 |
| `data.bin_work_execution_id` | string / WMS 原值 | 已关联候选 Bin 的稳定工作成员 |
| `data.bin_id` | string / WMS 原值 | 已关联候选 Bin 身份；必须与工作成员和 BinExecution 一致 |
| `data.expected_bin_work_execution_id` | string / WMS 原值 | 未匹配物理 Bin 原本承接的计划成员；只表达入站搬运关联，不声称实际 Bin 身份匹配 |
| `data.cell_execution_id` | string / WMS 原值 | 触发前序 CELL NG 的当前 Cell |
| `data.cause_ng_evidence_id` | string / 前序 Fact | 已被 WMS 接纳的同一 Cell 料盘 NG 到位事实 |
| `data.bin_observation_id` | string / WES | 支撑 BIN NG 原因的不可变身份/方向观察 |
| `data.reason_code` | enum / WES 证据映射 | 下表闭集原因；不能由操作员自由输入 |
| `data.observed_bin_code` | string[1..100] / 扫码设备 | 未匹配物理 Bin 实际读码；无法形成合法读码时省略，禁止填猜测值 |

BIN `reason_code` 是闭集：`BIN_CODE_UNREADABLE | BIN_NOT_CANDIDATE | BIN_EXECUTION_IDENTITY_MISMATCH |
BIN_DIRECTION_INVALID`。前三项只允许 `UNMATCHED_PHYSICAL_BIN`，`BIN_DIRECTION_INVALID` 只允许 `KNOWN_CANDIDATE`。
`cause_ng_evidence_id` 必须引用同一任务、Bin 和 Cell 已被 WMS 接纳的前序 CELL NG 料盘移动 Fact。WMS 原子保存出口 Fact 并
更新 Bin 权威位置后返回 `200 / RECORDED + data={}`。`cause_scope=BIN` 且 `bin_identity_kind=KNOWN_CANDIDATE` 时，WMS 同时以
BIN NG 关闭对应 `BinWorkExecution`；是否补充来源由后续计划增量决定。`UNMATCHED_PHYSICAL_BIN` 不关闭任何候选成员，CELL
分支不得扩大原业务 NG 作用域。`expected_bin_work_execution_id` 必须来自把该物理 Bin 送入工作线的已接纳入站批次；WMS 用它
定位受影响的计划来源，再通过后续计划增量取消原成员或追加替代成员，不能把预期关联当成实际 Bin 身份。

### 12.4 逐盘位置事实 DTO

每盘正常 PUT 或 MATERIAL/CELL NG 放置形成确定设备终态后，WES 发送：

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f45e",
  "operation": "outbound.material.movement_report@v1",
  "timestamp": 1786065500000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "source_execution_type": "CELL",
    "source_execution_id": "CELL-EXEC-021",
    "scan_evidence_id": "SCAN-EVIDENCE-001",
    "decision_operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea1",
    "target_assignment_id": "TARGET-WINDOW-001",
    "from_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_id": "BIN-001",
      "cell_id": "CELL-03"
    },
    "to_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "A",
      "slot_id": "A-05"
    },
    "face_window_generation": 1,
    "command_code": "CMD-TARGET-ARM-000001",
    "occurred_at": 1786065499900
  }
}
```

MATERIAL NG 放置事实示例：

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f45f",
  "operation": "outbound.material.movement_report@v1",
  "timestamp": 1786065501000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "source_execution_type": "CELL",
    "source_execution_id": "CELL-EXEC-021",
    "scan_evidence_id": "SCAN-EVIDENCE-002",
    "decision_operation_id": "019f3412-af77-71fd-9bde-0df75fcdeea3",
    "from_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_id": "BIN-001",
      "cell_id": "CELL-03"
    },
    "to_locator": {
      "type": "NG_ZONE",
      "zone_code": "MATERIAL_NG_01"
    },
    "ng_evidence_id": "MATERIAL-NG-EVIDENCE-002",
    "command_code": "CMD-TARGET-ARM-NG-000001",
    "occurred_at": 1786065500900
  }
}
```

CELL NG 放置事实示例（与上面的 MATERIAL NG 示例是独立分支）：

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f460",
  "operation": "outbound.material.movement_report@v1",
  "timestamp": 1786065502000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "source_execution_type": "CELL",
    "source_execution_id": "CELL-EXEC-021",
    "scan_evidence_id": "SCAN-EVIDENCE-003",
    "decision_operation_id": "019f3412-af77-71fd-9bde-0df75fcdeea6",
    "from_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_id": "BIN-001",
      "cell_id": "CELL-03"
    },
    "to_locator": {
      "type": "NG_ZONE",
      "zone_code": "CELL_NG_01"
    },
    "ng_evidence_id": "CELL-NG-EVIDENCE-001",
    "command_code": "CMD-TARGET-ARM-NG-000002",
    "occurred_at": 1786065501900
  }
}
```

字段约束如下：

| JSON Path | 正常 PUT | NG 放置 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- | --- |
| `data.task_id`、`data.execution_id` | 必填 | 必填 | string / 原值 | 当前任务及执行实例 |
| `data.source_execution_type` | 必填 | 必填 | enum / WES | `DIRECT_PICK \| CELL` |
| `data.source_execution_id` | 必填 | 必填 | string / WMS 原值 | 必须匹配来源类型和前序物料决定 |
| `data.scan_evidence_id` | 必填 | 必填 | string / WES | 当前盘不可变扫码证据 |
| `data.decision_operation_id` | 必填 | 必填 | UUIDv7 / 前序物料决定请求的 WES 原值 | 正常 PUT 引用 `ACCEPT`；NG 引用 `REJECT` |
| `data.target_assignment_id` | 必填 | 禁止 | string / WMS | 正常 PUT 的有效目标窗口 |
| `data.from_locator` | 必填 | 必填 | `RACK_SLOT \| BIN_CELL` / WMS 原值 | 来源成员冻结位置，禁止根据当前主数据重构 |
| `data.to_locator` | `RACK_SLOT` | `NG_ZONE` | 位置对象 / WMS 决定 | 必须与前序决定的精确目标或 NG 去向一致 |
| `data.face_window_generation` | 必填 | 禁止 | positive integer / WMS | 正常 PUT 实际到位窗口代际；不得内嵌到 `to_locator` |
| `data.ng_evidence_id` | 禁止 | 必填 | string / WES | 当前盘可靠进入 NG 区后生成 |
| `data.command_code` | 必填 | 必填 | string / WES DeviceCommand | 执行本次放置且已有唯一确定 `SUCCEEDED` 结果的命令 |
| `data.occurred_at` | 必填 | 必填 | UTC Unix 毫秒 / 设备结果 | 实际放置完成时间 |

- `source_execution_type` 是 `DIRECT_PICK | CELL` 闭集；`source_execution_id` 必须分别引用当前任务中的
  `direct_pick_execution_id` 或 `cell_execution_id`。
- `scan_evidence_id` 唯一关联 WES 内部 `MaterialExecution` 和不可变六合一码快照，因此本 operation 不重复发送
  `material_execution_id` 或 `PkgID`。WMS 从 `decision_operation_id` 对应的已保存请求读取包装身份。
- 正常 PUT 的 `decision_operation_id` 必须引用同一任务、执行、来源和扫码证据的终局 `ACCEPT`；`target_assignment_id`、
  `to_locator` 和 `face_window_generation` 必须与该决定完全一致。换面或换架后，代际必须匹配实际到位窗口。
- `from_locator` 必须从来源成员冻结的 `source_locator` 原样复制；WES 不根据当前主数据或设备位置重新构造来源。
- `command_code` 引用执行本次放置的 DeviceCommand，且该命令必须已经具有统一设备接口接纳的唯一确定 `SUCCEEDED` 结果。
  设备结果的 `source_event_id` 由 WES 保存在 DeviceCommand 证据中，不复制到 WMS 业务 Payload，也不生成第二个结果 ID。
- MATERIAL/CELL NG 复用本 operation，但 `to_locator` 改为 `NG_ZONE(zone_code)` 并携带 `ng_evidence_id`；此时禁止携带
  `target_assignment_id`。WMS 根据 `decision_operation_id` 对应的 `REJECT` 和来源执行身份解释 NG 作用域，不接受 Payload
  自报第二份业务异常分类。
- 位置未知、放置失败或 DeviceCommand 结果未知时禁止发送已完成位置事实；WES 保留当前物理义务并进入恢复或对账。

WMS 正常接纳后返回：

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f45e",
  "code": "RECORDED",
  "timestamp": 1786065500100,
  "data": {}
}
```

WMS 必须在返回 `RECORDED` 前，在同一事务中保存 Fact，并更新权威物料位置、库存、来源占用和目标 SLOT 占用。相同
`operation_id`、相同 Payload 重试固定返回 `200 / DUPLICATE`，并复用首次响应的 `timestamp + data`；相同身份不同 Payload 返回
`409 / CONFLICT`。后续物料决定、
容量判断、货架清场和任务状态确认必须读取已经提交的位置事实。

WES 收到 `RECORDED | DUPLICATE` 前不得把依赖该位置事实的目标容量、来源成员或任务视为完成；但当 WMS 已对当前盘返回
`next_source_action=CONTINUE` 时，这个 Fact ACK 不是来源机械臂开始下一条命令的通用前置条件。两机械臂能否同时进入干涉区仍由
ECS/PLC 硬件锁裁决。

### 12.5 Fact 重试

响应未知、`BUSY` 或 `UNAVAILABLE` 时，WES 保留现场证据与相关资源，使用原 `operation_id` 和原 Payload 重试；不能创建第二份
Fact 身份或等待额外结果回调。

## 13. PickingTask 状态确认

### 13.1 WES 发起条件

```text
no_local_business_execution_obligation == true
AND no_unapplied_plan_delta == true
AND no_pending_cancellation == true
AND all_required_movement_facts_confirmed == true
```

WES 只检查本地业务义务是否闭合，不汇总历史成员结果。货架离位、Bin 退回、Transport 清场和下一任务准入不属于 PickingTask
状态确认条件。

准备请求已经取得 `PREPARE_ACCEPTED`、但超过双方配置的首批期限仍未收到任何计划增量时，WES 同样满足本节本地条件，并以
`last_applied_plan_revision=0` 请求状态确认。`0` 只表示尚无计划，不创建虚构的 plan revision。

### 13.2 确认请求与结果

```json
{
  "operation_id": "019f3420-e282-7672-82f5-322ed259f230",
  "operation": "outbound.picking_task.completion_confirm@v1",
  "timestamp": 1786066000000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "last_applied_plan_revision": 5
  }
}
```

WMS 返回同步决定：

```json
{
  "operation_id": "019f3420-e282-7672-82f5-322ed259f230",
  "code": "DECIDED",
  "timestamp": 1786066000100,
  "data": {
    "result": "COMPLETED",
    "current_plan_revision": 5
  }
}
```

`NOT_COMPLETED` 示例：

```json
{
  "operation_id": "019f3423-e282-7672-82f5-322ed259f231",
  "code": "DECIDED",
  "timestamp": 1786066001100,
  "data": {
    "result": "NOT_COMPLETED",
    "current_plan_revision": 6,
    "reason_code": "PLAN_REVISION_STALE"
  }
}
```

准备期尚无首批计划时的请求和响应示例：

```json
{
  "operation_id": "019f3423-e282-7672-82f5-322ed259f232",
  "operation": "outbound.picking_task.completion_confirm@v1",
  "timestamp": 1786066002000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "last_applied_plan_revision": 0
  }
}
```

```json
{
  "operation_id": "019f3423-e282-7672-82f5-322ed259f232",
  "code": "DECIDED",
  "timestamp": 1786066002100,
  "data": {
    "result": "NOT_COMPLETED",
    "current_plan_revision": 0,
    "reason_code": "BUSINESS_IN_PROGRESS",
    "retry_after_ms": 1000
  }
}
```

到期重新确认时必须使用新消息身份并引用直接前序请求：

```json
{
  "operation_id": "019f3423-e282-7672-82f5-322ed259f233",
  "operation": "outbound.picking_task.completion_confirm@v1",
  "timestamp": 1786066003100,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "last_applied_plan_revision": 0,
    "previous_operation_id": "019f3423-e282-7672-82f5-322ed259f232"
  }
}
```

如果 WMS 在第二次确认前已经闭合业务，允许直接以 revision 0 完成：

```json
{
  "operation_id": "019f3423-e282-7672-82f5-322ed259f233",
  "code": "DECIDED",
  "timestamp": 1786066003200,
  "data": {
    "result": "COMPLETED",
    "current_plan_revision": 0
  }
}
```

请求字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.task_id`、`data.execution_id` | 是 | string / 原值 | 当前任务及执行实例 |
| `data.last_applied_plan_revision` | 是 | non-negative integer / WES 引用 | WES 已经连续持久化并原子应用的最高计划版本；尚无计划时为 `0`，只作版本围栏 |
| `data.previous_operation_id` | 条件 | UUIDv7 / 前序请求 | 首次确认禁止；前次 `NOT_COMPLETED` 后重新确认时必须引用直接前序请求 |

响应字段字典：

| JSON Path | `COMPLETED` | `NOT_COMPLETED` | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- | --- |
| `data.result` | 必填 | 必填 | enum / WMS | `COMPLETED \| NOT_COMPLETED` |
| `data.current_plan_revision` | 必填 | 必填 | non-negative integer / WMS | WMS 当前权威计划版本；尚无计划时为 `0`，响应形成时原子读取 |
| `data.reason_code` | 禁止 | 必填 | enum / WMS | `PLAN_REVISION_STALE \| BUSINESS_IN_PROGRESS` |
| `data.retry_after_ms` | 禁止 | `BUSINESS_IN_PROGRESS` 必填 | positive integer / WMS | 无保证回调时的兜底重试间隔 |

`PLAN_REVISION_STALE` 时，WMS 必须重新投递缺失的连续计划增量，禁止只要求 WES 盲目重试确认；当双方版本均为 `0` 时禁止返回
该原因。`BUSINESS_IN_PROGRESS` 表示版本一致但 WMS 权威需求状态尚未完成；WES 等待新事实或 `retry_after_ms` 到期后使用新的
`operation_id` 重新确认。

`result` 是闭集：

- `COMPLETED`：`last_applied_plan_revision` 与 WMS 当前版本一致，且 WMS 根据此前逐盘确认、空取决定、NG 事实、取消记录和需求
  状态确认 PickingTask 已完成。双方版本可以同为 `0`，表示任务在形成首批计划前已经由 WMS 权威业务状态闭合。WMS 保证该任务
  不再发布计划增量；后续新增需求必须创建新 PickingTask。
- `NOT_COMPLETED`：WMS 当前权威状态尚未完成。响应携带 `current_plan_revision` 和稳定 `reason_code`；
  `BUSINESS_IN_PROGRESS` 还必须携带 `retry_after_ms`。`reason_code=PLAN_REVISION_STALE` 时 WMS 重新投递缺失增量；
  `BUSINESS_IN_PROGRESS` 时 WES 保持执行态，
  等待新事实或到期后重新确认。

完成状态完全由 WMS 已经持有的逐盘交互和业务状态聚合，不接收任何来源或 Bin 的历史结果数组。
`last_applied_plan_revision` 只承担版本围栏，不承担完成项对账。

`COMPLETED` 只关闭 PickingTask 业务计划和成员变更，不关闭已经在物流线上的 Bin 退箱或任务相关货架清场。WMS 必须保留完成时
冻结的任务执行快照和相关占用，并继续接受既有 Bin 的 `outbound.bin.return_batch@v1` 和任务相关货架的
`outbound.rack.clearance_decide@v1`，直到这些物理对象完成退箱或清场。WMS 不得仅因 PickingTask 已完成返回
`STATE_CONFLICT`；这些后续决定不得新增取料成员、重新打开任务或发布新的计划增量。

首次确认禁止携带 `previous_operation_id`。`NOT_COMPLETED` 后重新确认使用新的 `operation_id`，并以
`previous_operation_id` 引用同一任务执行实例的直接前序确认请求。

## 14. 失败、重试和对账

### 14.1 Transport 确定失败后的恢复决定

Transport `UNKNOWN` 只能通过同一 `transport_task_id` 的更高权威结果消歧，不接收替代方案。只有当前结果已经确定为
`REJECTED | FAILED`、所有涉及对象位置明确且 WMS 已形成可执行替代方案时，WMS 才发送：

```json
{
  "operation_id": "019f3424-75c0-7de0-a43e-41eff3f1d0a1",
  "operation": "outbound.picking_task.transport_recovery_decided@v1",
  "timestamp": 1786066100000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "transport_task_id": "TRANSPORT-FAILED-001",
    "transport_outcome": "FAILED",
    "replacement_transport_plan": {
      "plan_type": "BIN_RETURN",
      "moves": [
        {
          "bin_work_execution_id": "BIN-WORK-001",
          "bin_execution_id": "BIN-EXEC-001",
          "bin_id": "BIN-001",
          "source": {
            "type": "HANDOFF_POSITION",
            "location_code": "RETURN_BUFFER_01"
          },
          "target": {
            "type": "RACK_BIN_SLOT",
            "rack_id": "RACK-5F-001",
            "rack_face": "A",
            "slot_id": "A-09"
          }
        }
      ]
    }
  }
}
```

字段字典：

| JSON Path | 必填 | 类型/生成方 | 语义与约束 |
| --- | --- | --- | --- |
| `data.task_id`、`data.execution_id` | 是 | string / 原值 | 原 Transport 所属 PickingTask 和执行实例 |
| `data.transport_task_id` | 是 | string / Transport | 原确定失败 TransportTask；必须存在当前业务执行映射 |
| `data.transport_outcome` | 是 | enum / WMS 引用 | `REJECTED \| FAILED`，必须等于 WES 当前已接纳结果；禁止 `UNKNOWN \| SUCCEEDED` |
| `data.replacement_transport_plan` | 是 | 判别联合 / WMS | 一个完整可执行替代方案；不定义“无动作”分支 |
| `data.replacement_transport_plan.plan_type` | 是 | enum / WMS | `RACK_MOVE \| RACK_ROTATE \| BIN_INBOUND \| BIN_RETURN` |

替代方案分支：

| `plan_type` | 必填字段 | 语义与约束 |
| --- | --- | --- |
| `RACK_MOVE` | `rack_id + source + target` | `source/target` 均为可靠且不同的 `RACK_POSITION` |
| `RACK_ROTATE` | `rack_id + position + target_face` | `position` 为当前可靠 `RACK_POSITION`；目标面不得等于当前面 |
| `BIN_INBOUND` | `moves[1..4]` | 每项复用第 9.2.1 节 READY move DTO，只允许仍在冻结 `RACK_BIN_SLOT` 的未成功成员 |
| `BIN_RETURN` | `moves[1..4]` | 每项复用第 9.2.2 节 READY move DTO，只允许仍在批准 `RETURN_BUFFER` 的未成功成员 |

`RACK_MOVE` 替代方案示例：

```json
{
  "operation_id": "019f3424-75c0-7de0-a43e-41eff3f1d0a2",
  "operation": "outbound.picking_task.transport_recovery_decided@v1",
  "timestamp": 1786066101000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "transport_task_id": "TRANSPORT-FAILED-002",
    "transport_outcome": "FAILED",
    "replacement_transport_plan": {
      "plan_type": "RACK_MOVE",
      "rack_id": "TRANSFER-RACK-01",
      "source": {
        "type": "RACK_POSITION",
        "location_code": "OUTBOUND_TARGET_WORK_01"
      },
      "target": {
        "type": "RACK_POSITION",
        "location_code": "TRANSFER_RACK_COMPLETED_01"
      }
    }
  }
}
```

`RACK_ROTATE` 替代方案示例：

```json
{
  "operation_id": "019f3424-75c0-7de0-a43e-41eff3f1d0a3",
  "operation": "outbound.picking_task.transport_recovery_decided@v1",
  "timestamp": 1786066102000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "transport_task_id": "TRANSPORT-FAILED-003",
    "transport_outcome": "REJECTED",
    "replacement_transport_plan": {
      "plan_type": "RACK_ROTATE",
      "rack_id": "TRANSFER-RACK-01",
      "position": {
        "type": "RACK_POSITION",
        "location_code": "OUTBOUND_TARGET_WORK_01"
      },
      "target_face": "B"
    }
  }
}
```

`BIN_INBOUND` 替代方案示例：

```json
{
  "operation_id": "019f3424-75c0-7de0-a43e-41eff3f1d0a4",
  "operation": "outbound.picking_task.transport_recovery_decided@v1",
  "timestamp": 1786066103000,
  "data": {
    "task_id": "PICK-20260811-001",
    "execution_id": "EXEC-PICK-000001",
    "transport_task_id": "TRANSPORT-FAILED-004",
    "transport_outcome": "FAILED",
    "replacement_transport_plan": {
      "plan_type": "BIN_INBOUND",
      "moves": [
        {
          "bin_work_execution_id": "BIN-WORK-001",
          "bin_id": "BIN-001",
          "source": {
            "type": "RACK_BIN_SLOT",
            "rack_id": "RACK-5F-001",
            "rack_face": "A",
            "slot_id": "A-01"
          },
          "target": {
            "type": "HANDOFF_POSITION",
            "location_code": "INGRESS_BUFFER_01"
          }
        }
      ]
    }
  }
}
```

替代方案只能覆盖原 Transport 中尚未成功且位置明确的对象，禁止包含已成功、无关或位置未知对象。WES 首次接纳时把事件绑定到
当前内部 outcome version，并以恢复 `operation_id` 生成新的稳定 `client_request_id`；原子持久化后返回 `202 / RECEIVED +
data={}`。同一 Transport 确定结果只能接纳一个恢复决定；不同方案返回 `409 / CONFLICT + STATE_CONFLICT`。

### 14.2 通用失败与重试

- `BUSY`、`UNAVAILABLE` 或响应未知：使用原 `operation_id` 和原 Payload 重试。
- `REJECTED`：停止重试原 Payload；修正后使用新 `operation_id`。
- `CONFLICT`、版本跳号、不可变字段变化或无法关联：停止自动推进，保留现场资源并进入对账。
- 计划增量 ACK 未知：WMS 只重提原消息，不能越过该 revision 发布后一版本。
- 准备请求 ACK 未知：WES 只重提原请求，不能换线或创建第二个执行实例。
- `WAIT`、`NO_BATCH` 或 `NOT_COMPLETED` 后重新求值：新请求使用新的 `operation_id`，并以 `previous_operation_id` 引用同一
  证据或执行实例的直接前序请求。
- Transport `UNKNOWN`：保持位置和依赖步骤未决，按 Transport 合同用同一 `transport_task_id` 的更高权威结果消歧，不创建替代任务。
- DeviceCommand 结果未知：保留当前物理义务，不把未知解释为失败、NG、取消或完成。

`WmsClient` 每次只执行一次 HTTP/JSON 访问。Outbox、可靠重试、计划顺序、状态推进和对账由对应业务模块负责。

## 15. 联调验收清单

| 场景 | 预期结果 |
| --- | --- |
| Payload 含 `//` 注释或不是标准 JSON | 返回空 Body `400`；请求正文必须是标准 JSON |
| DTO 出现未知字段、`null`、空条件数组或错误字段类型 | 返回 `422 / REJECTED + INVALID_DATA`，不做部分接纳 |
| `six_in_one.Qty` 使用 JSON number | 返回 `422 / REJECTED + INVALID_DATA`；六个扫码值必须都是字符串 |
| 位置对象的判别类型与字段、来源类型或目标用途不一致 | 结构错误返回 `422`；与已接纳权威引用冲突返回 `409` |
| 响应联合缺少条件必填字段或混入另一分支字段 | 合同 fixture 必须拒绝，禁止客户端猜测结果分支 |
| WMS 发布 PickingTask | 只入队，不冻结 WorkLine、来源、目标或物理动作 |
| 准备请求已接纳 | 快速返回 `PREPARE_ACCEPTED`；没有计划增量前不创建 TransportTask 或 DeviceCommand |
| 准备 ACK 响应丢失 | WES 使用原 `operation_id` 和原正文重试；WMS 返回首次接纳时的完整 `PREPARE_ACCEPTED` 响应 |
| 首个计划增量 | `plan_revision=1` 必须且只能定义一个初始目标窗口，可以同时新增来源成员；不携带增量类型或 WMS 计算进度字段 |
| WMS 分批计算资源 | 每批 revision 连续；WES 先持久化再 ACK，首批完整成员立即开工 |
| 后续计划增量 | `plan_revision>=2`，禁止 `added_target_windows`；新窗口只由逐盘终局 `ACCEPT` 创建 |
| 首批增量只有初始目标窗口 | 可以提前运输目标架，但不能凭空创建来源取盘动作 |
| 增量追加多个货架类型 | 五层、退料和转运货架按各自固定目标位并行运输 |
| revision 跳号或同版本不同内容 | WES 停止自动推进并对账，不覆盖已接纳计划 |
| WMS 内部资源计算仍在继续 | 不向 WES 暴露计算完成字段；已接纳成员继续执行，状态确认返回 `NOT_COMPLETED` |
| 执行中追加 Bin | 新建 `bin_work_execution_id`；不修改旧成员，不直接追加 Cell |
| 取消尚未运输的 Bin | 立即取消，不创建设备或运输动作 |
| 取消已在 FIFO、尚未取盘的 Bin | 停止取料并正常退箱，不逆向搬运 |
| 取消时取盘命令已接纳 | 当前盘先闭合到目标或 NG；不得放回来源 |
| 取消到达时 Bin 已终态 | 接纳重复取消并推进 revision，不回退终态或阻断后续版本 |
| 扫码后才确认尺寸 | WMS 返回精确 SLOT 和可选换面/换架方案；当前盘允许在扫码台有界等待 |
| 当前盘 PUT 与下一盘准备重叠 | 两个 `device_code` 可各有一条命令；无安全暂存位时，ECS/PLC 在下一盘离开来源前取得扫码台交接许可 |
| 审查扫码台协调实现 | 不存在扫码台释放事件、WES 资源锁、租约或跨机械臂软件互锁 |
| 目标架不满足当前盘 | 同一 `ACCEPT` 返回完整 `target_preparation`；Transport 到位后直接 PUT，不重新验证物料 |
| 正常 PUT 命令 | 目标机械臂命令只使用已授权逻辑目标；确定 `SUCCEEDED` CALLBACK 前不生成逐盘位置事实 |
| 正常 PUT 位置确认 | `movement_report` 精确引用 WMS 决定、目标窗口、来源位置和设备结果；WMS 原子更新位置、库存及目标占用 |
| PUT 结果未知 | 不上报已完成位置事实、不替换目标、不释放依赖容量；保留现场义务并进入恢复或对账 |
| CTU 乱序投箱 | 未取消 Bin 按实际到达顺序请求工作计划，FIFO 队首不能被绕过 |
| 入站或退箱暂时无批次 | `NO_BATCH` 必须返回 `retry_after_ms`；WES 可被新事实提前唤醒，否则到期重试 |
| `NO_BATCH` 后重新求值 | 使用新 `operation_id` 并引用直接前序请求；只有响应未知、`BUSY` 或 `UNAVAILABLE` 才原身份重试 |
| 入站返回 `FACE_DONE` | 当前任务的该货架面永久封口；后续 revision 不得再追加该面 Bin |
| 未匹配物理 Bin 到达 NG 出口 | Fact 引用预期 `BinWorkExecution` 以定位受影响来源，但不把预期关联当作实际身份，也不自动关闭原成员 |
| Cell 物料绑定冲突 | WMS 返回 `REJECT + SOURCE_CELL_MISMATCH + CLOSE`；当前盘进入 NG，位置事实确认后关闭当前 Cell |
| 空取需要补充来源 | 先返回 `WAIT / PLAN_DELTA_PENDING`，下一普通计划增量追加新顶层成员；WES 应用增量后重新请求所有等待增量的空取观察，WMS 对已承接缺口返回 `SOURCE_DONE` |
| NG 需要补充来源 | 下一普通计划增量追加新顶层成员，不返回嵌套成员快照或来源恢复类型 |
| 状态确认与追加竞态 | WMS 以当前 `plan_revision` 原子裁决；版本落后时返回 `NOT_COMPLETED` 并补发增量 |
| 准备期尚无首批计划 | WES 使用 revision 0 确认；进行中响应必须带重试间隔，业务已闭合可直接 revision 0 完成 |
| PickingTask 状态确认 | 请求不携带成员结果全集；WMS 根据既有逐盘事实返回状态，不等待货架离位、Bin 退回或工作线清场 |
| PickingTask 完成后物理清理 | WMS 继续受理既有 Bin 退箱和任务相关货架清场决定，直到物流对象闭合；不得重新打开任务或追加成员 |

## 16. 实施与验收所有权

| 所有者 | 验证范围 |
| --- | --- |
| WMS Adapter 合同 | 固定 path、公共信封、operation、DTO、幂等、revision 和错误映射 |
| 自动出库插件 | PickingTask、计划增量应用、取消安全点、Cell 循环、双臂业务并发和状态确认触发 |
| Transport 核心 | 货架与 Bin 搬运、ACK、异步结果、unknown 和恢复 |
| DeviceCommand 核心 | 单设备命令接纳、ACK、终态、deadline 和幂等事实 |
| ECS/PLC 及供应商验收 | 扫码台硬件锁、机械臂防撞、长命令内部动作和设备实际行为 |

基础能力不得用自动出库业务场景证明；自动出库插件也不得复制 Transport、DeviceCommand 或 ECS/PLC 的基础状态机。

## 17. 正式实施前确认项

批准前必须完成以下联合评审和机械化确认：

- WMS 与 WES 开发组共同评审本文并把 `status` 从 `ReviewRequired` 更新为 `Approved`；有异议必须先修改本文，禁止在代码中
  创建事实上的另一份合同。
- 根据本文生成或手写一份严格 JSON Schema；Schema 只能机器化本文，必须设置未知字段拒绝，不得新增别名、默认值、`null`、
  扩展对象或兼容分支。
- 部署配置提供真实 `workline_code`、货架面、工作位、缓存位、NG 区和清场库位编码。编码值可以按现场变化，但字段结构、类型和
  权威边界不得变化；两组代码都不得硬编码本文示例值。
- 联调配置确认 WMS 分批计算首批期限、各业务 `WAIT` 的实际重试值和人工对账时限。这些是运行参数，不改变
  `retry_after_ms` 的字段合同。
- 目标机械臂实际设备合同附录冻结 PUT `task_type`、逻辑 `params`/结果 `data`、时限和错误；未获批前不得用占位附录、全局枚举
  或供应商私有字段开始设备实现。
- 双方基于本文相同 fixture 覆盖每个响应联合、条件字段、幂等重放、版本跳号、引用冲突、取消安全点和完成确认竞态。fixture
  可以机器化合同，但不得反向修改合同语义。
- ECS/PLC 单独确认两机械臂硬件锁、单设备单活动命令、扫码台单盘承载和防撞现场验收方案；这些硬件能力不进入 WMS/WES DTO。

不得从本文扩展出 JSONC、自由文本错误、通用资源锁、通用工作流引擎或兼容旧合同的双路径。
