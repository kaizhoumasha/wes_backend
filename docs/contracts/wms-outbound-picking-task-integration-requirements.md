---
title: WMS / WES 自动出库 PickingTask 交互要求
status: ReviewRequired
created_at: 2026-08-07
updated_at: 2026-08-18
audience: WMS 与 WES 初级开发工程师、联调与测试人员
scope: WMS/WES API、任务队列、异步资源计划、计划增量、执行中增删、逐盘决定、结果确认和任务状态确认
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

## 1. 这份文档怎么读

本文给负责 WMS 对接的 C# 开发人员使用。你需要根据本文实现接口地址、DTO、Handler、重复提交处理和任务版本检查。
字段名、枚举值和 JSON 示例是接口约定，不能自行改名。

如果你只负责 WMS 端 C# 开发，先读第 2、4、5 节，再按实际业务场景阅读第 7 至 14 节。本文已经包含 WMS 对接所需的字段和处理规则，
不要求你先理解 WES 内部代码。需要了解完整设备流程时，再看
[WES 出库操作顶层设计](../superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md)。

本文状态是 `ReviewRequired`，表示 WMS 和 WES 还需要共同确认。它不表示功能已经开发完成。

系统尚未发布，因此只保留本文定义的接口。不提供旧接口、字段别名或新旧接口同时兼容。JSON Schema 必须与本文一致。

### 1.1 C# 类型对照

WMS 当前使用 .NET Framework 4.6。下面的建议按 C# 6 编写，不使用 `record`、nullable reference type 或新版本模式匹配。

| 接口写法 | C# 中建议使用 | 注意事项 |
| --- | --- | --- |
| string、code、enum | `string` 或固定枚举 | JSON 中仍按本文给出的字符串发送，大小写不能改变 |
| integer、UTC Unix 毫秒 | `long` | 时间值不是 `DateTime` 字符串 |
| object | 独立的 request/response DTO class | 每个 `operation` 使用自己的 `data` 类型 |
| array | `List<T>` 或只读集合 | 表中要求非空时，不能发送空数组 |
| 条件字段 | nullable value type 或独立响应 DTO | 条件不成立时不发送该字段，不要发送 `null` |

`operation_id` 是 UUIDv7，但接口中按字符串保存和传输。不要把所有 `data` 都放进一个带大量可空属性的通用 DTO，也不要使用
`Dictionary<string, object>` 跳过字段校验。收到请求后，先根据 `operation` 选择对应的 DTO 和 Handler。

## 2. 主流程

| 业务步骤 | 什么时候发生 | WMS 要做什么 | WES 接下来做什么 |
| --- | --- | --- | --- |
| 发布任务 | WMS 已生成可以进入自动出库队列的 PickingTask | 发送 `outbound.picking_task.issued@v1` | 保存任务并返回 `RECEIVED`，此时还不会搬货 |
| 准备任务 | WES 已选中任务和工作线 | 收到 `outbound.picking_task.prepare@v1` 后返回 `PREPARE_ACCEPTED`，再开始计算货架和来源 | 保留这条工作线，等待 WMS 分批下发结果 |
| 下发计划 | WMS 每算出一批可以执行的数据 | 按连续的 `plan_revision` 发送 `outbound.picking_task.plan_delta@v1` | 保存这一批数据并返回 `RECEIVED`，条件齐全的部分可以先执行 |
| 搬运货架和 Bin | WMS 已给出来源和去向 | 不直接创建运输任务 | WES 根据已确认的位置创建 TransportTask |
| 处理 Bin | 货架或 Bin 到达工作位置 | 根据 WES 请求返回本批 Bin、Cell 或退箱位置 | 按 WMS 返回结果执行 |
| 处理料盘 | 料盘到达扫码台并完成扫码 | 决定目标 SLOT 或 NG 去向；收到放置结果后更新库存和位置 | 执行放置，再上报实际结果 |
| 追加正常计划 | WMS 还有当前任务尚未发布的直接取料来源或五层来源货架面 | 发送更高 `plan_revision` 的计划 | WES 保存新增来源，已经接收的来源和 Bin 不被改写 |
| 确认完成 | 当前任务的每条任务明细都有处理结果，且 WES 已没有待执行工作 | 根据任务版本和已保存的结果返回任务状态 | 根据结果完成任务、接收尚未送达的计划或稍后重试 |

## 3. WMS、WES 各自负责什么

| 系统 | 负责的数据和决定 | 不负责 |
| --- | --- | --- |
| WMS | PickingTask、业务优先级、库存、来源和目标分配、来源占用、转运货架容量、物料是否合格、任务追加 | 工作线设备状态、物理防撞和设备命令结果 |
| WES | 选择可用工作线、检查计划版本、组织现场执行、保存位置和设备结果 | 重新计算库存、替 WMS 选择目标、计算货架容量 |
| RCS/AGV/CTU | 货架与 Bin 的路径、搬运和运输结果 | PickingTask 业务状态 |
| ECS/PLC/设备 | 扫码、取放、输送、防撞和设备动作结果 | 库存、来源或目标分配 |

WMS 只告诉 WES 这张任务可以使用哪些来源和目标。WES 根据已经确认的现场位置和 WorkLine 固定位置创建 TransportTask。
WMS 不创建 TransportTask，也不向设备发送 DeviceCommand。

## 4. 所有接口共用的 JSON 和 HTTP 规则

### 4.1 请求和响应的公共字段

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

顶层只允许示例中的四个字段。每个 `operation` 的 `data` 也只能使用对应字段表中列出的字段。
发送方第一次发送前，要在同一个事务中保存全局唯一的 UUIDv7 `operation_id` 和完整请求内容。网络超时后重试时，
`operation_id`、完整请求内容和顶层 `timestamp` 都不能改变。

请求公共字段说明：

| JSON Path | 必填 | 类型/格式 | 生成方 | 说明和校验规则 |
| --- | --- | --- | --- | --- |
| `operation_id` | 是 | UUIDv7 字符串 | 当前消息发起方 | 一次请求的唯一编号，用来识别重复提交；重试时必须使用原值 |
| `operation` | 是 | 本文第 5 节固定枚举 | 当前消息发起方 | 决定 `data` 使用哪个 C# 请求类；大小写敏感，不接受别名或旧版本 |
| `timestamp` | 是 | UTC Unix 毫秒整数 | 当前消息发起方 | 首次形成并持久化消息的时间；重试不得刷新 |
| `data` | 是 | object | 当前消息发起方 | 当前 operation 的业务数据；只能出现字段表中的字段，无内容时使用 `{}` |

响应公共字段说明：

| JSON Path | 必填 | 类型/格式 | 生成方 | 说明和校验规则 |
| --- | --- | --- | --- | --- |
| `operation_id` | 是 | UUIDv7 字符串 | 接收方原样返回 | 必须等于请求中的值，接收方不能另生成一个 ID |
| `code` | 是 | 第 4.3 节固定枚举 | 接收方 | 表示协议接收、业务决定、结果上报或失败类别 |
| `timestamp` | 是 | UTC Unix 毫秒整数 | 接收方 | 第一次保存完整响应的时间；同一请求重新发送时仍返回第一次的值 |
| `data` | 是 | code 对应的 object | 接收方 | 只能出现对应结果表中的字段；无内容时使用 `{}` |

业务请求不再增加 `event_id` 或 `request_id`。HTTP 日志可以使用 `X-Request-ID`，但它不能写进业务 JSON，也不能用来判断重复提交。

### 4.2 端点

| 发起方 | 接收方 | 方法和路径 | 模式 |
| --- | --- | --- | --- |
| WMS | WES | `POST {{WES_BASE_URL}}/api/v1/wms/events` | WMS 发送异步消息，WES 同步确认是否收到 |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/decisions` | WES 请求准备或业务决定，WMS 同步响应 |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/facts` | WES 同步上报结果，WMS 同步确认是否保存成功 |

三个接口的原始 Body 上限均为 `256 KiB`。非法 JSON、无法提取合法 `operation_id` 返回空响应体 `400`；解码前超限
返回空响应体 `413`。能够读取合法 `operation_id` 后，所有响应必须原样返回该值，并使用本节定义的公共响应格式。

### 4.3 HTTP 状态码和 `code`

| HTTP / `code` | 含义 |
| --- | --- |
| `200 / DECIDED` | 同步业务决定已经形成 |
| `200 / RECORDED` | WMS 已在一个事务中保存结果上报并更新相关业务数据 |
| `200 / DUPLICATE` | 相同的异步消息、回调或结果上报以前已经接收 |
| `202 / RECEIVED` | 异步消息或计划增量已经成功保存并按顺序接收 |
| `202 / PREPARE_ACCEPTED` | 耗时资源计算请求已经接收并保存，但尚不代表存在可执行计划 |
| `422 / REJECTED` | 公共字段、operation 或业务字段不合法 |
| `409 / CONFLICT` | 重复提交内容冲突、版本冲突或不可变约束冲突 |
| `503 / UNAVAILABLE` | 当前无法成功保存或处理 |

业务上不同意执行时，仍返回 `200 / DECIDED`，具体结果看 `data.result`。如果调用方因为响应丢失而使用原请求重试，
接收方必须再次返回第一次的完整业务结果，不能只返回 `DUPLICATE`。

本合同不使用 `429 / BUSY`。接口暂时无法保存请求时返回 `503 / UNAVAILABLE`；请求已经保存、但业务条件暂不满足时返回
`200 / DECIDED`，并在 `data.result` 中使用该 operation 定义的 `WAIT` 或 `NO_BATCH`。

通用失败响应的 `data` 按下表返回。某个 operation 自己定义的业务 `reason_code`，只能用在该 operation 的字段表或
`200 / DECIDED` 结果中，不能拿来替代下表中的错误码。

| HTTP / `code` | `data` 必填字段 | 枚举/约束 |
| --- | --- | --- |
| `422 / REJECTED` | `reason_code`，`INVALID_DATA` 时可带 `field_path` | `reason_code=INVALID_ENVELOPE \| UNSUPPORTED_OPERATION \| INVALID_DATA`；`field_path` 是长度 `1..256` 的 RFC 6901 JSON Pointer，例如 `/data/six_in_one/Qty` |
| `409 / CONFLICT` | `reason_code` | `IDEMPOTENCY_CONFLICT \| REVISION_CONFLICT \| STATE_CONFLICT \| REFERENCE_CONFLICT` |
| `503 / UNAVAILABLE` | 无 | `data={}`；调用方使用原 `operation_id` 和原请求内容重试 |

### 4.4 字段表和公共数据类型

字段表中的“条件”表示：条件成立时必须发送，条件不成立时不要发送。所有接口还要遵守以下规则：

- HTTP Body 使用 UTF-8 `application/json`。字段名和枚举大小写敏感；未知字段、重复 JSON key、错误类型和枚举外值返回
  `422 / REJECTED + INVALID_DATA`。
- 可选字段无值时必须省略。除非字段表明确允许，所有字段、对象元素和数组元素都禁止 `null`；空字符串、空对象和空数组也
  禁止代替省略。
- `operation_id` 使用 RFC 9562 UUIDv7 字符串。其他 ID、业务编码和位置编码必须匹配
  `[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}`；接收方按精确字符串比较，不根据前缀猜测类型。
- `six_in_one` 的六个设备值是长度 `1..256` 的非空 UTF-8 字符串；其他明确标注“扫码设备原文”的字段使用其字段表长度。所有
  UTF-8 字符串长度均按 Unicode code point 计数；WES 按设备规范化结果原样传递，不做数值、日期或主数据转换。
- 时间字段均为 `0..9223372036854775807` 的 UTC Unix 毫秒整数，C# 使用 `long`。revision、sequence 和 outcome version 均为
  `1..9223372036854775807` 的整数；仅任务状态确认中的 `last_applied_plan_revision` 和 `current_plan_revision` 允许为 `0`，表示
  准备请求已经接收但尚无任何计划增量。`retry_after_ms` 是 `1..60000` 的整数。
- 条件数组出现时必须包含 `1..N` 项；最大可发送项数由 `256 KiB` 原始 Body 上限自然约束，不另设无法与 Body 上限独立验证的
  隐含数量。数组顺序只有在对应 operation 明确声明时才有业务含义。
- 字段表中的 `a.b` 表示对象字段，`items[]` 表示数组中的每一项，`x | y` 表示只允许这些值，不是任意字符串。

位置对象先看 `type`，再按下表读取其余字段。一个对象只能符合其中一种类型：

| `type` | 必填字段 | 语义 | 禁止字段 |
| --- | --- | --- | --- |
| `RACK_SLOT` | `rack_id + rack_face + slot_id` | 退料货架或转运货架上的单料盘储位 | `bin_id`、`cell_id`、`location_code`、`zone_code` |
| `RACK_BIN_SLOT` | `rack_id + rack_face + slot_id` | 五层货架上的单 Bin 储位 | `bin_id`、`cell_id`、`location_code`、`zone_code` |
| `BIN_CELL` | `rack_id + rack_face + bin_id + cell_id` | 当前五层货架来源 Bin 内的工作料格 | `slot_id`、`location_code`、`zone_code` |
| `HANDOFF_POSITION` | `location_code` | WorkLine 中经静态拓扑批准的 Bin 交接/缓存位置 | 货架、Bin、Cell 和 `zone_code` 字段 |
| `RACK_POSITION` | `location_code` | 经部署配置批准的货架工作位或业务库位 | 货架、Bin、Cell、SLOT 和 `zone_code` 字段 |
| `NG_ZONE` | `zone_code` | 单料盘确定 NG 放置区 | 货架、Bin、Cell、SLOT 和 `location_code` 字段 |

不同 operation 中出现同名位置对象时，都使用本表结构。后面的业务字段表只说明它引用哪条业务数据，以及位置从哪里取得。

### 4.5 接口中常见词的含义

| 词语 | WMS 开发人员可以这样理解 | 实现要求 |
| --- | --- | --- |
| `operation` | 接口动作名称，类似路由到不同的 C# Handler | 必须使用第 5 节完整字符串，不能缩写或自定义别名 |
| 完整请求内容 | 顶层四个公共字段加 `data` 的完整 JSON | 同一 `operation_id` 重试时必须完全相同 |
| 异步消息 | WMS 主动发送给 WES 的请求 | WES 保存成功后才返回接收确认 |
| 接收确认 | 接收方告诉发送方“这条消息已经收下” | 它不表示运输、设备动作或整张任务已经完成 |
| 业务决定 | WMS 对当前请求给出的处理结果 | 返回 `200 / DECIDED`，调用方根据 `data.result` 处理 |
| 结果上报 | WES 告诉 WMS 某个物理动作已经真实发生 | WMS 返回 `RECORDED | DUPLICATE` 后，本次上报才算完成 |
| 重复提交保护 | 网络超时后可以安全重发同一请求 | 必须复用 `operation_id`、完整请求内容和时间戳；同一 ID 不能换内容 |
| revision | 一张任务的计划版本号 | 从 1 连续增加，不能跳号、倒退或覆盖已接收版本 |
| 最终结果 | 已经确定、普通重试不会改变的结果 | `UNKNOWN` 不是失败结果，需要等待后续确定结果或人工核对 |
| 以谁为准 | 某类业务数据由哪个系统最终决定 | 库存和分配以 WMS 为准，设备动作结果以 ECS/PLC 为准 |
| 固定字段或固定值 | 只允许文档列出的字段或枚举值 | 多余字段、别名和扩展值都要拒绝 |
| 同一事务保存 | 多个字段或状态一起成功、一起失败 | 不能只保存一部分就返回成功 |
| WES 已确认位置 | WES 根据运输和设备结果保存的当前位置 | 只用于现场执行，不能替代 WMS 库存和全局位置数据 |
| 人工核对 | 系统无法确定是否可以继续时，由现场和开发人员检查 | 停止自动执行，并保留原请求和设备结果 |

## 5. WMS 需要实现或调用的接口

| operation | 谁调用谁 | 什么时候调用 | 第一次成功时返回 | 详见 |
| --- | --- | --- | --- | --- |
| `outbound.picking_task.issued@v1` | WMS 到 WES | WMS 发布新 PickingTask | `202 / RECEIVED` | §7.1 |
| `outbound.picking_task.queue_changed@v1` | WMS 到 WES | WMS 调整尚未准备任务的队列信息 | `202 / RECEIVED` | §7.1 |
| `outbound.picking_task.prepare@v1` | WES 到 WMS | WES 选中任务和候选 WorkLine | `202 / PREPARE_ACCEPTED`，随后接收计划增量 | §7.2 |
| `outbound.picking_task.plan_delta@v1` | WMS 到 WES | WMS 形成一批资源或追加来源 | `202 / RECEIVED` | §8 |
| `outbound.bin.inbound_batch@v1` | WES 到 WMS | 当前来源货架面到位、没有退箱候选，且 CTU 和入料缓存有容量 | `200 / DECIDED`：`READY \| NO_BATCH \| RACK_FACE_DONE` | §9.2.1 |
| `outbound.bin.return_batch@v1` | WES 到 WMS | `RETURN_BUFFER` 出现可退箱 Bin，且没有未结束 CTU 动作 | `200 / DECIDED`：`READY \| NO_BATCH` | §9.2.2 |
| `outbound.bin.work_plan@v1` | WES 到 WMS | Bin 到达工作位并完成扫码 | `200 / DECIDED`：`READY \| NO_WORK \| WAIT` | §9.3 |
| `outbound.rack.departure_decide@v1` | WES 到 WMS | 货架不再占用当前工作位，需要决定离场去向 | `200 / DECIDED`：`READY \| WAIT` | §9.4 |
| `outbound.material.decide@v1` | WES 到 WMS | 料盘形成完整扫码证据 | `200 / DECIDED`：`ACCEPT \| REJECT \| WAIT` | §10.2 |
| `outbound.source.empty_decide@v1` | WES 到 WMS | 设备形成已确认空取证据 | `200 / DECIDED`：`RETRY \| WAIT \| SOURCE_DONE` | §12.2 |
| `outbound.bin.ng_exit_report@v1` | WES 到 WMS | Bin 已确认到达 `NG_EXIT` | `200 / RECORDED` | §12.3 |
| `outbound.material.movement_report@v1` | WES 到 WMS | 正常 PUT 或单盘 NG 放置完成 | `200 / RECORDED` | §12.4 |
| `outbound.picking_task.completion_confirm@v1` | WES 到 WMS | WES 本地完成前提条件成立 | `200 / DECIDED`：`COMPLETED \| PLAN_REVISION_STALE \| BUSINESS_IN_PROGRESS` | §13 |

当前任务的正常计划数据通过 `outbound.picking_task.plan_delta@v1` 分批发送，共享同一条 `plan_revision` 顺序链。当前已开放货架面的
Bin 由 `inbound_batch` 分批选择。空取、NG 或 Transport 确定失败造成的需求缺口不再通过当前任务的后续计划补充，由 WMS 创建新的
PickingTask。

## 6. ID 和版本由谁生成

| 字段 | 生成方 | 规则 |
| --- | --- | --- |
| `task_id` | WMS | PickingTask 的唯一编号，发布后不可变 |
| `operation_id` | 当前消息发起方 | 一次请求的唯一编号，用于重复提交保护；每批计划使用新的 ID |
| `queue_revision` | WMS | 从 1 开始连续递增，只用于尚未准备任务的队列更新 |
| `plan_revision` | WMS | 同一 `task_id` 从 1 开始连续递增，统一排序初始资源和后续来源追加 |
| `last_applied_plan_revision` | WES 引用已接收版本 | WES 已连续应用到哪个计划版本；尚无计划时为 `0`，只用于版本比较，不表示完成数量 |
| `PkgID` | 扫码设备，WMS 判断业务唯一性 | 完整料盘的唯一业务编号；同一料盘重新请求时保持不变 |
| `transport_task_id` | WES Transport | 一个搬运任务的编号 |

WMS/WES 接口不传 WES 内部的 PickingTask、Bin、Material 或 DeviceCommand 执行 ID。WMS 按下面的组合查找业务明细：

- DirectPick：`task_id + source_locator`
- 五层来源货架面：`task_id + rack_id + rack_face`
- Bin：`task_id + bin_id`
- Cell：`task_id + bin_id + cell_id`
- 当前接料货架面：`task_id + rack_id + rack_face`
- 一盘物料：`task_id + source_locator + PkgID`

这些字段组合用来唯一识别一条业务明细。同一个组合在一张任务中用过后，不能删除再新建。新增需求要使用新的字段组合，或者新建 PickingTask。

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

字段说明：

| JSON Path | `issued` | `queue_changed` | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- | --- |
| `data.task_id` | 必填 | 必填 | string / WMS | PickingTask 的唯一编号；不要求等于上游拣料单号，WES 不得推导或重建 |
| `data.queue_revision` | 必填且固定为 `1` | 必填且为当前值 `+1` | positive integer / WMS | 只排序队列更新；不能代替 `plan_revision` |
| `data.dispatch_sequence` | 必填 | 条件 | positive integer / WMS | 自动出库任务池内唯一业务优先序，值越小优先级越高；更新至少改变本字段或 `not_before` |
| `data.not_before` | 可选 | 条件 | UTC Unix 毫秒 / WMS | 任务最早可领取时间；省略于发布表示立即具备时间条件，省略于更新表示保持原值 |

需要把已有 `not_before` 恢复为立即可执行时，`queue_changed` 必须发送不晚于当前时间的明确值；禁止发送 `null` 或清除标志。
同一任务只能被 `issued` 首次接收一次；换 `operation_id` 重复发布相同 `task_id` 返回 `409 / CONFLICT + STATE_CONFLICT`。
`queue_changed` 只作用于 `QUEUED`，不能抢占 `PREPARING | EXECUTING`。这两个异步消息成功时均返回 `202 / RECEIVED`，同一消息重复发送
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
    "workline_code": "SORTING-LINE-01"
  }
}
```

WMS 保存准备请求并登记后台资源计算工作后，返回 `202 / PREPARE_ACCEPTED`。WES 会保留当前候选 WorkLine，不能因为等待首批结果而换线
或发起第二次准备。响应未知或 `UNAVAILABLE` 时，WES 使用原 `operation_id` 和原请求内容重试。

相同 `operation_id` 和相同请求内容再次到达时，WMS 必须返回第一次的完整 `202 / PREPARE_ACCEPTED` 响应，包括原
`timestamp + data`，不能改为 `DUPLICATE`。相同 ID 但请求内容不同，返回 `409 / CONFLICT`。

字段说明：

| JSON Path | 必填 | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 必须引用当前仍为 `QUEUED`、且已被 WES 在同一事务中领取的 PickingTask |
| `data.workline_code` | 是 | code / WES 配置 | WES 根据本地可用状态选择的具体 WorkLine；WMS 只据此计算关联 STATION 资源，不得改派另一条线 |

成功响应的 `data={}`。同一 `task_id` 只能成功准备一次；换 `operation_id` 重复准备、改变 `workline_code`，或任务已进入后续
状态时，WMS 返回 `409 / CONFLICT + STATE_CONFLICT`。`PREPARE_ACCEPTED` 只表示 WMS 已经接收资源计算请求，不表示已经算出接料货架面、
来源明细、TransportTask 或 DeviceCommand。

## 8. 分批计划增量

### 8.1 数据格式

#### 8.1.1 首批接料货架面与直接取料明细

WMS 每算出一批可以执行的数据，就发送一个新的计划版本。已经发送并被 WES 接收的版本不能再修改：

```json
{
  "operation_id": "019f3401-4a10-7b1a-aab5-f2df785324d1",
  "operation": "outbound.picking_task.plan_delta@v1",
  "timestamp": 1786060815000,
  "data": {
    "task_id": "PICK-20260811-001",
    "plan_revision": 1,
    "target_rack": {
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "A"
    },
    "added_direct_picks": [
      {
        "source_locator": {
          "type": "RACK_SLOT",
          "rack_id": "RETURN-RACK-01",
          "rack_face": "A",
          "slot_id": "A-03"
        }
      },
      {
        "source_locator": {
          "type": "RACK_SLOT",
          "rack_id": "RETURN-RACK-01",
          "rack_face": "A",
          "slot_id": "A-04"
        }
      }
    ]
  }
}
```

`target_rack` 表示当前允许接料的转运货架和货架面，不是具体 SLOT。

上面的 `added_direct_picks` 表示同一个退料货架面有两个需要直接取料的储位。一个数组项只表示一个精确储位，不能把多个 `slot_id`
合并到一个数组项中。

如果多个退料货架上都有需要直接取料的储位，仍然按储位逐项列出。例如两个退料货架各有两个储位：

```json
{
  "added_direct_picks": [
    {
      "source_locator": {
        "type": "RACK_SLOT",
        "rack_id": "RETURN-RACK-01",
        "rack_face": "A",
        "slot_id": "A-03"
      }
    },
    {
      "source_locator": {
        "type": "RACK_SLOT",
        "rack_id": "RETURN-RACK-01",
        "rack_face": "A",
        "slot_id": "A-04"
      }
    },
    {
      "source_locator": {
        "type": "RACK_SLOT",
        "rack_id": "RETURN-RACK-02",
        "rack_face": "B",
        "slot_id": "B-01"
      }
    },
    {
      "source_locator": {
        "type": "RACK_SLOT",
        "rack_id": "RETURN-RACK-02",
        "rack_face": "B",
        "slot_id": "B-02"
      }
    }
  ]
}
```

这是 `data` 中与本例有关的字段片段，实际回调仍须携带完整的 `task_id`、`plan_revision` 等字段。同一 `task_id` 下，每个
`source_locator` 只能新增一次；WES 分别建立四条直接取料明细，不按货架合并。

`plan_revision=1` 必须带一个初始 `target_rack`，也可以同时新增来源明细。`plan_revision>=2` 不能再带 `target_rack`。
执行中需要换面或换架时，由 `outbound.material.decide@v1` 的 `ACCEPT` 直接返回新的精确目标和货架准备方案。后续计划至少要新增一条
来源明细，不能发送空计划。

#### 8.1.2 追加五层来源货架面

任务已有当前接料货架面后，后续增量可以追加一个或多个五层来源货架面。计划增量不提前选择 Bin：

```json
{
  "operation_id": "019f3402-5b21-7c2b-bbc6-03e0896435e2",
  "operation": "outbound.picking_task.plan_delta@v1",
  "timestamp": 1786060820000,
  "data": {
    "task_id": "PICK-20260811-001",
    "plan_revision": 2,
    "added_bin_source_racks": [
      {
        "rack_id": "RACK-5F-001",
        "rack_face": "A"
      },
      {
        "rack_id": "RACK-5F-001",
        "rack_face": "B"
      }
    ]
  }
}
```

`added_bin_source_racks[]` 的每一项表示 WMS 已为当前任务安排的一个可取料五层货架面。同一货架的 A、B 面都有当前任务需要取出的 Bin 时，
必须按两个来源货架面分别记录，即使 `rack_id` 相同。如果某一面只用于承接退箱，没有当前任务需要取出的 Bin，则该面不进入
`added_bin_source_racks[]`。两个面同时确定时可以放在同一 `plan_revision`；后确定的货架面使用更高版本追加。Bin 在货架到位后由
`outbound.bin.inbound_batch@v1` 分批选择；Cell 仍在 Bin 实际到达 SCAN2 后由 `outbound.bin.work_plan@v1` 返回。

`target_rack` 仅在 `plan_revision=1` 必填。`added_direct_picks` 和 `added_bin_source_racks` 均为条件可选；字段出现时必须包含
`1..N` 项，没有该类变化时省略。首批的接料货架面本身就是有效变化；`plan_revision>=2` 必须至少携带一个非空的来源新增数组。

字段说明：

| JSON Path | 必填 | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 必须等于准备请求引用的 PickingTask |
| `data.plan_revision` | 是 | positive integer / WMS | 同一 `task_id` 从 1 连续递增；每批增量唯一 |
| `data.target_rack` | revision 1 必填 | object / WMS | 当前允许接料的转运货架和货架面；revision 2 及以后禁止出现 |
| `data.target_rack.rack_id` | 条件 | string / WMS | WMS 已建立业务占用的转运货架 |
| `data.target_rack.rack_face` | 条件 | code / WMS | 当前允许 PUT 的货架面 |
| `data.added_direct_picks[]` | 条件 | array / WMS | 新增退料货架直接取料明细 |
| `data.added_direct_picks[].source_locator` | 条件 | `RACK_SLOT` / WMS | 精确退料货架、面和 SLOT；明细接收后不可变 |
| `data.added_bin_source_racks[]` | 条件 | array / WMS | 新增五层来源货架面；不携带 Bin 或 Cell |
| `data.added_bin_source_racks[].rack_id` | 条件 | string / WMS | WMS 已为当前任务安排的五层来源货架 |
| `data.added_bin_source_racks[].rack_face` | 条件 | code / WMS | 本次允许取 Bin 的货架面；同一 `task_id + rack_id + rack_face` 只能新增一次 |

成功接收后返回 `202 / RECEIVED + data={}`；同一 `operation_id` 和相同请求内容再次到达时返回 `200 / DUPLICATE + data={}`。缺少有效
接料货架面、重复的明细唯一字段组合或改写既有不可变字段时，整个 revision 整批拒绝，不允许部分接收。

### 8.2 发布与接收规则

- WMS 内部可以并行计算，同一 `task_id` 的增量必须按 `plan_revision` 串行发布。
- `plan_revision` 从 1 开始，每次加一；前一版本未收到明确成功响应前，不得发布后一版本。
- `plan_revision=1` 必须定义且只定义一个初始接料货架面，可以同时新增来源明细；后续 revision 禁止新增接料货架面。
- WMS 是否已经算完整张任务，不需要告诉 WES。WMS 可以继续发布更高版本，直到 WMS 中的任务状态完成。
- 已经接收的来源唯一字段组合、已经选入批次的 `bin_id` 或父子关系不得被后续版本改写；结束后也不得在同一任务内重新创建。
- 缺少有效接料货架面时不得先取盘、后补目标。WMS 收到上一盘的位置结果并返回 `RECORDED | DUPLICATE` 后，
  新货架面才能供下一盘使用。

WES 收到回调后按以下顺序处理：

1. WES 先保存收到的完整请求。
2. 在事务中校验 operation 重复提交、task 状态、revision 连续性和所有引用。
3. 在同一事务中新增来源明细，并更新 `last_applied_plan_revision`。
4. 提交后返回 `202 / RECEIVED`；之后才启动相关 TransportTask 或 DeviceCommand 流程。

### 8.3 首批开工

WES 接收任一 revision 后，只要下面任意一类数据完整，就可以开始对应工作：

- 接料货架面数据完整：WES 可以根据已确认的货架位置，把对应转运货架运到该 WorkLine 的固定目标位。
- 直接取料明细的来源和任务当前接料货架面完整：可以并行请求退料货架和目标转运货架到位。
- 五层来源货架面和任务当前接料货架面完整：可以请求五层货架与目标转运货架到位；`inbound_batch` 仍必须等待来源货架实际到位。

计划增量不包含运输起点、设备命令或 CTU 内部动作。WES 从自己保存的已确认位置读取运输起点，并使用 WorkLine 静态
拓扑中的不同货架类型目标位。多个五层来源货架面同时可用时，只选择一个当前来源面并创建必要的货架 Transport；禁止为多个货架同时
创建指向同一 CTU 工作位的 `RACK_MOVE`。

## 9. 货架、Bin 和 Cell 执行

### 9.1 并行货架运输

转运货架、退料货架和五层货架的 TransportTask 相互独立，可以在没有位置或设备冲突时并行。每次创建 TransportTask 前，
WES 出库业务模块必须把计划增量 `operation_id`、执行阶段、完整 Transport 输入和 `client_request_id` 在同一事务中保存。崩溃恢复只能使用原
`client_request_id` 和原请求内容重试。

Transport 的接收、结果和 `UNKNOWN/RECONCILING` 对账遵循 Transport 履约合同。计划增量返回成功只表示 WES 已保存计划，不表示货架已经到位；WES 也不能根据 WMS
业务计划伪造位置结果。

### 9.2 CTU 入站和退箱

每条 WorkLine 只有一台 CTU。入站和退箱共用一个串行通道，同一时刻最多有一个尚未结束的 WMS 批次请求或 CTU Transport。
这条规则由自动出库业务模块负责；Transport 只执行完整的货架或 Bin 搬运，不判断下一步应该入站、退箱、换面还是换架。

以下事件发生后，自动出库业务模块都要重新判断一次 CTU 下一动作：

- `plan_delta` 已经保存并应用；
- 货架 `RACK_MOVE` 或 `RACK_ROTATE` 得到确定结果；
- 入站或退箱 `BIN_MOVE` 得到确定结果；
- CTU 空闲背篓数或 `INGRESS_BUFFER`、`RETURN_BUFFER` 状态发生变化；
- `NO_BATCH.retry_after_ms` 到期；
- 原来为 `UNKNOWN` 的 Transport 得到新的确定结果。

判断前，WES 必须在一个数据库事务中确认当前没有未结束的 CTU 动作，并保存本次业务步骤、`operation_id` 或
`transport_task_id`。多个事件同时到达时，只允许一个事件成功创建下一动作。这里防止的是软件事件重复触发，不是为缓存位建立
预留、租约或长期锁。

下一动作按以下顺序判断：

1. 已有 WMS 请求、Transport 或未知物理结果尚未结束时，继续完成或等待原动作，不开始另一条业务分支。
2. `RETURN_BUFFER` 有正常可退 Bin 时，优先调用 `return_batch`。只要队首候选仍在，`NO_BATCH` 也不能转去执行新的
   `inbound_batch`。
3. 没有可退 Bin，且当前 CTU 工作位的货架面是计划中尚未结束的来源面时，按本地容量调用 `inbound_batch`。
4. 当前来源面已经返回 `RACK_FACE_DONE`，并且从该面选出的 Bin 都已有最终去向时，选择下一个计划来源面并执行必要的货架换面或换架。
5. 没有可执行来源面时，只能等待当前任务尚未送达的正常 `plan_delta`、退箱数据或任务清理条件。已经空取、NG 或因 Transport
   确定失败而结束的任务明细，不能再由当前任务的计划增量替换。

`plan_delta` 可以一次提供多个来源货架面，但 WES 不能同时创建多条指向同一 CTU 工作位的货架任务。来源面按下面的固定顺序选择：

1. 当前已经在 CTU 工作位、并且尚未结束的计划来源面；
2. 当前货架的另一个尚未结束的计划来源面；
3. 按 `plan_revision` 和数组顺序选择最早接收的其他来源面。

货架动作规则：

- 当前货架和工作面已经正确时，不创建 Transport；
- `rack_id` 相同但目标面不同，创建一个 `RACK_ROTATE`；
- `rack_id` 不同，必须先保存旧架去向、新架可靠来源和两个完整搬运输入，再完成旧架移出和新架移入；两个 `RACK_MOVE` 禁止并行；
- `RACK_MOVE` 已经携带正确 `target_face` 时，到位后不再补一个 `RACK_ROTATE`；
- CTU 仍携带 Bin、存在未完成 Bin 搬运、实际位置不明确，或者当前面仍有已选 Bin 没有最终去向时，禁止换面和换架。

WES 必须保留每个 `inbound_batch` Bin 与来源 `rack_id + rack_face` 的对应关系。只要从当前面选出的任一 Bin 仍在入料缓存、工作区、
退料缓存、CTU 或 Transport 中，或者位置结果未知，当前货架和货架面就必须留在 CTU 工作位。只有该 Bin 已确定退回当前面、到达
`NG_EXIT`，或进入合同允许的其他确定最终位置后，才不再阻止当前面换面、换架或离场。PickingTask 已完成也不能绕过这个条件。

这项限制不增加预留、租约或接口字段。当前来源货架在任务执行期间由当前 WorkLine 独占，WMS 不得把取 Bin 后形成的空储位分配给
其他任务。正常退箱只使用这些空储位。

本文中的“批次完成”不是收到 WMS `READY`。只有对应 Transport 得到确定 `SUCCEEDED`，并且所有成员的最终位置已经可靠保存，
本批次才完成。随后重新执行上述判断，并再次从退箱优先开始。

#### 9.2.1 入站批次

五层来源货架确认到达 CTU 作业位后，WES 计算本批最多可取数量：

```text
max_bin_count = min(CTU 当前空闲背篓数, INGRESS_BUFFER 当前空闲位置数)
```

结果为 `0` 时不调用 WMS。结果大于 `0` 时发送：

```json
{
  "operation_id": "019f3405-2200-7b01-8b01-000000000001",
  "operation": "outbound.bin.inbound_batch@v1",
  "timestamp": 1786064700000,
  "data": {
    "task_id": "PICK-20260811-001",
    "rack_id": "RACK-5F-001",
    "rack_face": "A",
    "max_bin_count": 2
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
    "bins": [
      {
        "bin_id": "BIN-001",
        "source_locator": {
          "type": "RACK_BIN_SLOT",
          "rack_id": "RACK-5F-001",
          "rack_face": "A",
          "slot_id": "A-01"
        }
      }
    ]
  }
}
```

暂时没有可取 Bin 时返回：

```json
{
  "operation_id": "019f3405-2200-7b01-8b01-000000000002",
  "code": "DECIDED",
  "timestamp": 1786064700200,
  "data": {
    "result": "NO_BATCH",
    "retry_after_ms": 1000
  }
}
```

当前货架面已经完成时返回：

```json
{
  "operation_id": "019f3405-2200-7b01-8b01-000000000003",
  "code": "DECIDED",
  "timestamp": 1786064700300,
  "data": {
    "result": "RACK_FACE_DONE"
  }
}
```

字段说明：

| JSON Path | 必填 | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 当前 PickingTask |
| `data.rack_id`、`data.rack_face` | 是 | string + code / WES 已确认位置 | 必须命中 `plan_delta.added_bin_source_racks[]` 中尚未结束的五层来源货架面 |
| `data.max_bin_count` | 是 | integer[1..4] / WES | CTU 当前空闲背篓数与 `INGRESS_BUFFER` 当前空闲位置数的较小值 |
| `data.result` | 响应必填 | enum / WMS | `READY \| NO_BATCH \| RACK_FACE_DONE` |
| `data.bins[]` | `READY` 必填 | array[1..max_bin_count] / WMS | 本次选中的完整 Bin 列表；不能返回超过请求数量的成员 |
| `data.bins[].bin_id` | `READY` 必填 | string / WMS | 当前任务首次选中的 Bin；同一任务不得再次返回 |
| `data.bins[].source_locator` | `READY` 必填 | `RACK_BIN_SLOT` / WMS | 必须位于请求的 `rack_id + rack_face`，并指向 WMS 当前确认的来源储位 |

各结果含义：

| `data.result` | 必填字段 | 禁止字段 | 含义 |
| --- | --- | --- | --- |
| `READY` | `bins` | `retry_after_ms` | WMS 已保存本次选择；这些 Bin 从此以 `task_id + bin_id` 唯一识别 |
| `NO_BATCH` | `retry_after_ms` | `bins` | 当前暂时没有可取 Bin；以后仍可能返回新 Bin |
| `RACK_FACE_DONE` | 无 | `bins`、`retry_after_ms` | 当前任务不会再从这个 `rack_id + rack_face` 返回 Bin |

WMS 返回 `READY` 前，必须在同一事务中保存完整响应，并保证这些 Bin 没有被其他任务或批次选中。相同 `operation_id` 和相同请求内容
再次到达时，返回第一次的完整响应。`NO_BATCH` 后使用新的 `operation_id` 重新请求；响应未知或 `UNAVAILABLE` 时使用原请求重试。

收到入站 `NO_BATCH` 后，本次请求已经结束。WES 保存该来源面的下次重试时间，在到期前不重复请求同一货架面；期间出现退箱候选时，
仍按退箱优先处理。`NO_BATCH` 不关闭来源面，也不授权换面或换架。

收到 `READY` 后，WES 按 WorkLine 配置的固定顺序选择当前空闲的 `HANDOFF_POSITION`，将 `bins[]` 逐项映射到不同位置，再生成一个
Transport `BIN_MOVE`。WMS 不需要在本接口中接收本地缓存位；完整来源和目标会进入 Transport 请求。

`READY` 返回后，本批 `bins[]` 已经选定。WMS 不能撤销或改选其中的 Bin；即使本地容量暂时发生变化，WES 也等待原 `bins[]` 具备
执行条件，并只处理其中尚未完成的成员。若某个 Bin 到达 SCAN2 时已无取料需求，WMS 在 `work_plan` 中返回 `NO_WORK`，让该 Bin 按正常
退箱路径离开，不再增加单独的取消接口。入站 `BIN_MOVE` 确定成功后重新判断 CTU 下一动作；此时先检查 `RETURN_BUFFER`，没有可退 Bin
才能再次请求入站。

收到 `RACK_FACE_DONE` 后，WES 关闭当前五层来源货架面。WMS 不能在后续 `plan_delta` 中重新添加同一
`task_id + rack_id + rack_face`。如果同一货架还有另一个面，需要把另一个面作为新的来源货架面加入计划。`RACK_FACE_DONE`
只表示不能再从当前面选择新 Bin，不表示当前货架可以立即离场。WES 必须等待从该面选出的 Bin 全部已有确定最终去向，才能换面、
换架或让当前货架离场。

来源换面或换架不由 `inbound_batch` 响应重新决定。WES 从已经接收的 `added_bin_source_racks[]` 中选择下一个来源面：同一货架的另一面
创建 `RACK_ROTATE`，不同货架按第 9.2 节的公共规则先移出旧架、再移入新架。新来源面确定到位后，WES 才使用新的 `operation_id`
调用 `inbound_batch`。

#### 9.2.2 退箱批次

当前没有其他 CTU 批次时，WES 从 `RETURN_BUFFER` 的可退队列头部取候选：

```text
candidate_count = min(CTU 当前空闲背篓数, RETURN_BUFFER 当前可取 Bin 数量)
```

结果为 `0` 时不调用 WMS。结果大于 `0` 时，WES 按 FIFO 顺序发送前 `candidate_count` 个实际 Bin：

```json
{
  "operation_id": "019f3406-2200-7b03-8b01-000000000003",
  "operation": "outbound.bin.return_batch@v1",
  "timestamp": 1786065050000,
  "data": {
    "task_id": "PICK-20260811-001",
    "rack_id": "RACK-5F-001",
    "rack_face": "A",
    "return_candidates": [
      {
        "sequence_no": 1,
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
        "sequence_no": 1,
        "bin_id": "BIN-001",
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
    "retry_after_ms": 1000
  }
}
```

字段说明：

| JSON Path | 必填 | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 当前 PickingTask |
| `data.rack_id`、`data.rack_face` | 是 | string + code / WES 已确认位置 | 当前已确认到达、准备承接退箱的五层货架和实际面 |
| `data.return_candidates[]` | 是 | array[1..4] / WES | 当前已确认位于 `RETURN_BUFFER` 的正常 Bin，按 FIFO 从队首开始排列；数量不能超过 CTU 当前空闲背篓数 |
| `data.return_candidates[].sequence_no` | 是 | integer[1..return_candidates.length] / WES | 本次请求内的 FIFO 顺序号，从 1 连续递增，不得重复或跳号 |
| `data.return_candidates[].bin_id` | 是 | string / WMS 原值 | 必须是当前任务 `inbound_batch` 已返回、且当前确认需要正常退箱的 Bin |
| `data.return_candidates[].source` | 是 | `HANDOFF_POSITION` / WES 已确认位置 | 实际 `RETURN_BUFFER` 位置；在途、工作位、NG 或未知位置禁止发送 |
| `data.result` | 响应必填 | enum / WMS | `READY \| NO_BATCH` |
| `data.moves[]` | `READY` 必填 | array[1..return_candidates.length] / WMS | 必须对应候选列表从第一项开始的连续前缀；不得跳过队首或包含请求外明细 |
| `data.moves[].sequence_no` | `READY` 必填 | 原请求值 / WMS 原样返回 | 必须与对应候选的 `sequence_no` 相同，并保持连续前缀顺序 |
| `data.moves[].bin_id` | `READY` 必填 | 原请求值 / WMS 原样返回 | 顺序必须与对应的 `return_candidates[]` 相同 |
| `data.moves[].target` | `READY` 必填 | `RACK_BIN_SLOT` / WMS | 必须位于请求的 `rack_id + rack_face`；WMS 返回前已在同一事务中分配不同空储位 |

`sequence_no` 只表示本次 `operation_id` 请求中的候选顺序，不是 Bin 的永久队列号。相同请求重试时，顺序号和完整请求都不能改变；
使用新 `operation_id` 重新请求时，WES 根据当时的 FIFO 队首候选重新从 1 编号。同一次请求中的 `sequence_no` 和 `bin_id` 都不得重复。
WMS 必须同时校验并原样返回二者，不能只根据数组位置猜测对应关系。

非 `READY` 各响应结果：

| `data.result` | 必填字段 | 禁止字段 | 语义 |
| --- | --- | --- | --- |
| `NO_BATCH` | `retry_after_ms` | `moves` | WMS 暂时不能完成目标储位分配；候选保持在队首，并阻止新的入站批次 |

`READY` 可以少于请求候选数。例如候选顺序号为 1 到 4，而 WMS 本次只处理前 2 个时，只能为顺序号 1、2 分配目标并原样返回。
其余 Bin 留在 `RETURN_BUFFER`，下一批仍从队首开始。WMS 不能跳过队首选择后面的 Bin。

`NO_BATCH` 只能表示等待后可能恢复的临时条件，例如 WMS 的目标分配事务尚未完成。WMS 已确定当前面没有可分配储位时，说明来源面
独占、库存位置或此前批次数据存在矛盾，必须返回 `409 / CONFLICT + STATE_CONFLICT`。WES 暂停当前 WorkLine 并告警，不能自动换面、
换架，也不能用 `NO_BATCH` 无限重试。

WMS 必须根据自己保存的 `inbound_batch` 结果确认每个候选都来自请求中的 `rack_id + rack_face`。候选来自其他货架面时返回
`409 / CONFLICT + REFERENCE_CONFLICT`。`READY` 后 WES 只处理所选候选，并调用一次 CTU Transport；未选候选继续留在
`RETURN_BUFFER`。

退箱 `BIN_MOVE` 确定成功后重新判断 CTU 下一动作。队首仍有候选时继续退箱；只有 `RETURN_BUFFER` 已经没有正常可退 Bin，才能进入
入站。当前货架面已经返回 `RACK_FACE_DONE` 时，还必须确认从该面选出的所有 Bin 都有确定最终去向，才能切换到下一来源面。

退箱请求收到 `NO_BATCH` 后，WES 在新业务数据到达或 `retry_after_ms` 到期时使用新的 `operation_id` 和当时的 FIFO 队首候选重新请求。
收到 `NO_BATCH` 后，本次请求已经结束，只保存下次重试时间；新的现场事件可以提前唤醒判断。响应未知或 `UNAVAILABLE` 时，才使用原
`operation_id` 和原请求内容重试。

### 9.3 Bin 工作计划

Bin 到达工作位并完成 SCAN2 后，WES 保存 Bin 编号和到位记录，再发送：

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e20",
  "operation": "outbound.bin.work_plan@v1",
  "timestamp": 1786064800000,
  "data": {
    "task_id": "PICK-20260811-001",
    "bin_id": "BIN-001",
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
    "cell_ids": ["CELL-03"]
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
    "retry_after_ms": 1000
  }
}
```

请求字段说明：

| JSON Path | 必填 | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 当前 PickingTask |
| `data.bin_id` | 是 | string / 扫码证据 | 必须命中当前任务某次 `inbound_batch` 已返回的 Bin；不匹配时进入第 12.3 节 NG 分支 |
| `data.scanned_at` | 是 | UTC Unix 毫秒 / 设备证据 | SCAN2 有效读码发生时间，不使用 HTTP 发送时间代替 |

各响应结果：

| `data.result` | 必填字段 | 禁止字段 | 语义 |
| --- | --- | --- | --- |
| `READY` | `cell_ids[1..N]` | `retry_after_ms` | 当前 Bin 的最终非空工作计划 |
| `NO_WORK` | 无 | `cell_ids`、`retry_after_ms` | 当前 Bin 不再需要取料，业务明细完成，物理 Bin 继续退箱 |
| `WAIT` | `retry_after_ms` | `cell_ids` | 当前不能形成稳定计划 |

`READY.cell_ids[]` 中每项是 WMS 返回的非空 `cell_id`。同一 `task_id + bin_id` 内不得重复；首次接收后即为该 Bin 的最终工作计划，
不能撤销、删减或改写，数组顺序不表达业务优先级或依赖。此后只能通过现有的逐 Cell、空取、NG 和结果确认流程处理，不能中途撤销
整个 Bin 或已经开始的料盘动作。

CTU 投箱顺序也不构成业务顺序；`WORK_BUFFER` 是单向 FIFO，队首没有明确
`READY | NO_WORK | NG` 结果时，后续 Bin 不能绕行。同一 `task_id + bin_id` 只能形成一个最终 `READY | NO_WORK`；`WAIT`
后使用新 `operation_id`，并携带同一 `task_id + bin_id` 重新判断。

### 9.4 货架离场去向决定

初始进场目标来自 WorkLine 固定配置，不调用本 operation。只有货架已经不再承担当前工作、可以离开工作位，并且先前决定没有给出离场
去向时，WES 才发送：

```json
{
  "operation_id": "019f3408-8300-7b05-8b01-000000000005",
  "operation": "outbound.rack.departure_decide@v1",
  "timestamp": 1786065120000,
  "data": {
    "task_id": "PICK-20260811-001",
    "rack_id": "TRANSFER-RACK-01",
    "current_location": {
      "type": "RACK_POSITION",
      "location_code": "OUTBOUND_TARGET_WORK_01"
    },
    "current_face": "A"
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
    "rack_destination": {
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
    "retry_after_ms": 1000
  }
}
```

字段说明：

| JSON Path | 必填 | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 当前 PickingTask |
| `data.rack_id` | 是 | string / 已接收计划 | 来自来源明细或接料货架面；WMS 根据任务已保存数据识别货架角色，不接收 `rack_role` |
| `data.current_location` | 是 | `RACK_POSITION` / WES 已确认位置 | 当前已确认物理位置，禁止使用计划目标代替 |
| `data.current_face` | 是 | code / WES 已确认位置 | 当前已确认货架面 |
| `data.result` | 响应必填 | enum / WMS | `READY \| WAIT` |
| `data.rack_destination` | `READY` 必填 | `RACK_POSITION` / WMS | 当前货架离开工作位后的唯一去向；不得等于 `current_location` |
| `data.retry_after_ms` | `WAIT` 必填 | positive integer / WMS | 无新业务数据时的兜底重试间隔 |

WES 只有在没有未完成 PUT、未确认的位置结果上报、相关设备动作和继续使用该货架的本地明细时，才能发送离场请求。五层来源货架还必须
满足：从当前面选出的所有 Bin 都有确定最终去向。`READY` 后以当前决定 `operation_id` 派生一个稳定 `client_request_id` 并创建一项
货架 TransportTask；不得拆分。同一 `target_preparation.mode=REPLACE` 已给出当前架去向时，禁止为同一货架重复调用。

## 10. 逐盘扫码后决定目标，以及两个机械臂并行工作

### 10.1 物理前提

料盘从原 `RACK_SLOT` 或 `BIN_CELL` 取出后无法放回。WMS 只有在料盘到达扫码台后，才能得到实际尺寸和完整六合一码，
再决定具体 SLOT、是否换面或是否换架。当前料盘在扫码台等待这个决定属于正常流程。

扫码台最多承载一盘未决物料。这个容量由现场机构和 ECS/PLC 硬件锁保证；WES 不为它建立资源对象、锁、租约或平台释放事件。

### 10.2 物料决定

完整扫码结果保存后，WES 调用 `outbound.material.decide@v1`：

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea1",
  "operation": "outbound.material.decide@v1",
  "timestamp": 1786063000000,
  "data": {
    "task_id": "PICK-20260811-001",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_id": "BIN-001",
      "cell_id": "CELL-03"
    },
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

请求字段说明：

| JSON Path | 必填 | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 当前 PickingTask |
| `data.source_locator` | 是 | `RACK_SLOT \| BIN_CELL` / WMS 原值 | 必须唯一命中当前任务锁定来源；位置类型分别表示 DirectPick 或 Cell |
| `data.six_in_one.HHPN` | 是 | string[1..256] / 扫码设备 | 物料编码，WES 原样传递 |
| `data.six_in_one.MfrPN` | 是 | string[1..256] / 扫码设备 | 制造商料号，WES 不做同义转换 |
| `data.six_in_one.Qty` | 是 | string[1..256] / 扫码设备 | 当前包装数量原文；禁止改为 JSON number，数值合法性由 WMS 判断 |
| `data.six_in_one.DateCode` | 是 | string[1..256] / 扫码设备 | 日期码原文，WES 不解析日期 |
| `data.six_in_one.LotCode` | 是 | string[1..256] / 扫码设备 | 批次码原文 |
| `data.six_in_one.PkgID` | 是 | string[1..256] / 扫码设备 | 当前料盘的完整包装编号；是否重复由 WMS 判断 |
| `data.scanned_at` | 是 | UTC Unix 毫秒 / 扫码证据 | 完整六合一码形成时间 |

`ACCEPT` 示例：

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea1",
  "code": "DECIDED",
  "timestamp": 1786063000100,
  "data": {
    "result": "ACCEPT",
    "target_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "A",
      "slot_id": "A-05"
    },
    "next_source_action": "CONTINUE"
  }
}
```

需要同架换面时仍在同一个最终 `ACCEPT` 中返回：

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea5",
  "code": "DECIDED",
  "timestamp": 1786063000100,
  "data": {
    "result": "ACCEPT",
    "target_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "B",
      "slot_id": "B-01"
    },
    "target_preparation": {
      "mode": "ROTATE"
    },
    "next_source_action": "CONTINUE"
  }
}
```

需要换架时仍在同一个最终 `ACCEPT` 中返回：

```json
{
  "operation_id": "019f3411-af77-71fd-9bde-0df75fcdeea2",
  "code": "DECIDED",
  "timestamp": 1786063000100,
  "data": {
    "result": "ACCEPT",
    "target_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-02",
      "rack_face": "A",
      "slot_id": "A-01"
    },
    "target_preparation": {
      "mode": "REPLACE",
      "rack_destination": {
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
    "retry_after_ms": 1000
  }
}
```

各响应结果：

| `data.result` | 必填字段 | 禁止字段 | 语义 |
| --- | --- | --- | --- |
| `ACCEPT` | `target_locator + next_source_action`，可选 `target_preparation` | REJECT/WAIT 字段 | 物料资格和唯一 PUT 目标已形成最终授权 |
| `REJECT` | `business_exception_code + ng_locator + source_disposition` | 目标和 WAIT 字段 | WMS 已形成确定 MATERIAL/CELL 业务异常和隔离去向 |
| `WAIT` | `retry_after_ms` | 目标、NG 和来源处置字段 | 当前不能形成最终资格或精确目标；不是 NG |

`ACCEPT` 字段约束：

| JSON Path | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- |
| `data.target_locator` | `RACK_SLOT` / WMS | WMS 已预留的唯一目标 SLOT；其中已经包含目标货架、货架面和 SLOT |
| `data.next_source_action` | enum / WMS | `CONTINUE \| SOURCE_DONE`；`RACK_SLOT` 来源固定 `SOURCE_DONE`；它是业务决定，不是硬件安全许可 |
| `data.target_preparation.mode` | 条件 enum / WMS | `ROTATE \| REPLACE`；目标无需物理准备时整个对象省略 |
| `data.target_preparation.rack_destination` | `REPLACE` 必填 | 当前目标架离开工作位后的唯一 `RACK_POSITION`；`ROTATE` 分支禁止 |

`ROTATE` 的目标架和目标面来自 `target_locator`。`REPLACE` 的新架和目标面也来自 `target_locator`，新架当前来源由 WES 从已确认位置
记录中读取。`REPLACE.rack_destination` 已完成当前目标架的离场去向决定，禁止再为同一原因调用货架离场 operation。

物料决定按扫码台单盘串行求值；
同一 WorkLine 尚有未完成物料决定时，WMS 不得为该线形成第二个新接料货架面。

`REJECT.business_exception_code` 只允许以下值：

| 值 | 允许来源 | NG 影响范围 | `source_disposition` |
| --- | --- | --- | --- |
| `MATERIAL_REJECTED` | `DIRECT_PICK \| CELL` | `MATERIAL` | `CONTINUE \| CLOSE`；`CONTINUE` 只允许 CELL |
| `SOURCE_CELL_MISMATCH` | `CELL` | `CELL` | 固定 `CLOSE` |

`ng_locator` 必须是 WMS 批准的 `NG_ZONE`。`source_disposition` 只决定料盘确认进入 NG 区后的来源业务状态，不是设备命令。
`CONTINUE` 允许当前 Cell 继续取下一盘，`CLOSE` 在当前盘 NG 结果确认后关闭当前来源。WMS 尚不能形成这两个最终结果时必须返回
`WAIT`，不能先返回 `REJECT` 再让 WES 等待未定义的恢复动作。
相关已确认结果变化时立即以新 `operation_id` 和同一 `task_id + source_locator + PkgID` 重新判断，没有新业务数据时才等待
`retry_after_ms`。

`target_preparation` 只有三种写法：无动作时省略、同架换面时 `mode=ROTATE`、换架时 `mode=REPLACE`。
`REPLACE` 固定按“当前架离场 → 新架进场 → PUT”执行。WMS 必须在同一次 `ACCEPT` 中给出当前盘最终可执行的完整方案；WES
等待 Transport 确认到位后直接 PUT，不重新请求物料资格。

`CONTINUE` 表示当前 Cell 业务需求允许继续取下一盘；`SOURCE_DONE` 表示当前盘完成后关闭来源。`RACK_SLOT` 来源只能返回
`SOURCE_DONE`。同一 `task_id + source_locator + PkgID` 只能形成一个最终 `ACCEPT | REJECT`；`WAIT` 后使用新 `operation_id` 携带
同一业务字段重新判断。

### 10.3 两个机械臂并发

- 来源机械臂和目标机械臂使用不同 `device_code`，各自最多一条已接收尚未得到最终结果 DeviceCommand。
- WMS 对当前盘返回 `CONTINUE` 后，只要来源机械臂没有活动命令，WES 可以在目标机械臂 PUT 当前盘期间下发下一条来源命令。
- ECS 可以接收命令并执行不改变料盘位置的准备动作。没有现场批准的安全暂存位时，硬件锁必须在料盘离开来源前确认扫码台交接
  路径可用；不能先取出下一盘，再持盘等待扫码台释放。
- 下一盘何时离开来源并进入扫码台、两个机械臂是否会同时进入干涉区以及如何防撞，由 ECS/PLC 硬件锁决定。
- WES 不等待“扫码台已释放”事件，不保存扫码台占用锁，不实现跨机械臂协调逻辑，也不要求 ECS 暴露长命令内部步骤。
- 物料移动结果得到 WMS 确认后，WES 才能释放相关容量并完成对应明细和任务。这个确认不是另一机械臂开始下一条命令的统一前提。

## 11. 执行中追加来源

WMS 通过更高 `plan_revision` 的 `added_direct_picks[]` 或 `added_bin_source_racks[]` 补充来源。已经开放且尚未返回
`RACK_FACE_DONE` 的五层来源货架面，可以通过后续 `inbound_batch` 返回新的 Bin，不需要为每个 Bin 发布计划增量。
这些数据只能是当前任务尚未发布的正常计划，不能用于替换已经空取、NG 或因 Transport 确定失败而无法完成的任务明细。WMS 不得修改、
删除后重建已接收来源；`RACK_FACE_DONE` 后不得重新开放同一货架面。上述异常形成的需求缺口，以及任务完成后的新增需求，都必须发布
新的 PickingTask。

## 12. PUT、NG、空取和结果确认

NG 影响范围是 `MATERIAL | CELL | BIN`。扫码不完整、设备失败、WMS `WAIT`、目标换架等待或 Transport/PUT 结果未知不属于 NG。
本文中的“空取”是指设备明确确认指定 `RACK_SLOT` 或 `BIN_CELL` 没有物料，不是取料失败、扫码失败或结果未知。已确认空取通过
`outbound.source.empty_decide@v1` 返回 `RETRY | WAIT | SOURCE_DONE`。空取响应不返回替代来源；当前任务关闭空取来源后，未满足的
需求由 WMS 创建新的 PickingTask。

### 12.1 正常 PUT 的三个步骤

正常 PUT 分三步完成：

1. WMS 通过 `outbound.material.decide@v1` 返回 `ACCEPT`，授权当前盘的精确 `RACK_SLOT`；需要换面或换架时，先完成同一决定中的
   `target_preparation`。
2. 目标货架和货架面与 WES 本地已确认的位置一致后，WES 为目标机械臂创建 DeviceCommand。只有匹配该命令的确定
   `SUCCEEDED` CALLBACK 才表示物理 PUT 已完成；接收确认、设备忙、超时或结果未知都不能生成位置结果。
3. WES 保存设备结果并更新本地已确认位置后，调用 `outbound.material.movement_report@v1`；WMS 在同一事务中更新物料位置、库存和
   目标占用，再返回 `RECORDED | DUPLICATE`。

PickingTask 合同不定义目标机械臂的供应商 `task_type`。实际设备合同附录必须为该设备锁定一个 PUT `task_type`，其 `params`
至少能够唯一关联当前 `MaterialExecution`，并携带 WMS 已授权的 `rack_id`、`rack_face` 和 `slot_id`。附录必须定义成功、失败、超时、
结果未知及人工核对边界；不得包含库存、容量计算、替代目标、PLC
坐标、速度、安全锁或防撞字段。WES 保存 WMS 决定与 DeviceCommand 的关联，ECS/PLC 只执行逻辑目标和硬件安全控制。

### 12.2 空取决定请求和响应

只有来源机械臂的确定的设备结果证明指定 `RACK_SLOT` 或 `BIN_CELL` 无料时，WES 才发送：

```json
{
  "operation_id": "019f3420-01be-7e11-b265-10de42c881f0",
  "operation": "outbound.source.empty_decide@v1",
  "timestamp": 1786065100000,
  "data": {
    "task_id": "PICK-20260811-001",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_id": "BIN-001",
      "cell_id": "CELL-03"
    },
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
    "retry_after_ms": 1000
  }
}
```

字段说明：

| JSON Path | 必填 | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 当前 PickingTask |
| `data.source_locator` | 是 | `RACK_SLOT \| BIN_CELL` / WMS 原值 | 必须唯一命中当前任务锁定来源；类型分别表示 DirectPick 或 Cell |
| `data.observed_at` | 是 | UTC Unix 毫秒 / 设备结果 | 确定无料最终状态发生时间 |

各响应结果：

| `data.result` | 必填字段 | 语义 |
| --- | --- | --- |
| `RETRY` | 无 | WMS 允许对同一 `source_locator` 再取一次；不更换来源，也不关闭来源明细 |
| `WAIT` | `retry_after_ms` | WMS 还不能确定如何处理这次空取；WES 到期后重新请求，不等待替代计划 |
| `SOURCE_DONE` | 无 | 结束当前任务中的这个来源明细；没有满足的需求留给新的 PickingTask |

同一 `task_id + source_locator` 在一次未完成空取处理中只能形成一个最终 `SOURCE_DONE`。`RETRY` 只允许重试原位置；再次空取时使用新的
`operation_id` 重新请求决定。`WAIT` 只表示当前决定尚未稳定，不能用来等待 WMS 为当前任务寻找替代来源。空取响应禁止嵌入来源集合，
后续 `plan_delta` 也不能为这个空取结果补上替代来源。WMS 返回 `SOURCE_DONE` 后，WES 结束对应的 `DirectPickExecution` 或
`CellExecution`；WMS 根据没有满足的需求创建新的 PickingTask。

### 12.3 Bin 到达 NG 出口后的上报

`outbound.bin.ng_exit_report@v1` 只报告 Bin 已确认到达 `NG_EXIT`。`reason_code=SOURCE_CELL_MISMATCH` 补充 CELL NG 后的
Bin 最终位置；其他原因表达 Bin 自身身份或方向异常。CELL 分支示例：

```json
{
  "operation_id": "019f3421-3a20-788d-b93d-d1a150a23b0d",
  "operation": "outbound.bin.ng_exit_report@v1",
  "timestamp": 1786065195000,
  "data": {
    "task_id": "PICK-20260811-001",
    "bin_id": "BIN-001",
    "reason_code": "SOURCE_CELL_MISMATCH",
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
    "bin_id": "BIN-001",
    "reason_code": "BIN_DIRECTION_INVALID",
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
    "expected_bin_id": "BIN-001",
    "reason_code": "BIN_NOT_CANDIDATE",
    "observed_bin_code": "BIN-UNKNOWN-009",
    "ng_exit_code": "NG_EXIT_01",
    "occurred_at": 1786065200900
  }
}
```

字段说明：

| JSON Path | 必填 | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 当前 PickingTask |
| `data.reason_code` | 是 | enum / WES 证据映射 | `SOURCE_CELL_MISMATCH \| BIN_CODE_UNREADABLE \| BIN_NOT_CANDIDATE \| BIN_DIRECTION_INVALID` |
| `data.bin_id` | 条件 | string / 扫码证据 | `SOURCE_CELL_MISMATCH` 或 `BIN_DIRECTION_INVALID` 必填；必须是当前任务已接收的 Bin |
| `data.expected_bin_id` | 条件 | string / WMS 原值 | `BIN_CODE_UNREADABLE` 或 `BIN_NOT_CANDIDATE` 必填；表示 `inbound_batch` 已选中并送入工作线的 Bin |
| `data.observed_bin_code` | 条件 | string[1..100] / 扫码设备 | `BIN_NOT_CANDIDATE` 必填；读码失败时禁止出现 |
| `data.ng_exit_code` | 是 | code / WorkLine 静态拓扑 | 实际到达的批准 NG 出口，不得临时生成 |
| `data.occurred_at` | 是 | UTC Unix 毫秒 / 设备结果 | Bin 到达出口的物理发生时间 |

WES 只有在 DeviceCommand 明确成功并保存本地证据后，才能发送这条结果上报。`command_code` 和 WES 内部证据 ID 不发给 WMS。
WMS 在同一事务中保存出口结果并更新 Bin 位置，然后返回 `200 / RECORDED + data={}`。`BIN_DIRECTION_INVALID` 关闭对应的
`task_id + bin_id` 明细；
`BIN_CODE_UNREADABLE | BIN_NOT_CANDIDATE` 不关闭 `task_id + expected_bin_id`。预期 Bin 后续实际到达 SCAN2 时，WMS 仍通过
`work_plan` 返回 `READY | NO_WORK | WAIT`；因 Bin NG 产生的库存需求缺口由 WMS 创建新的 PickingTask，不通过当前任务的后续计划补充；
`SOURCE_CELL_MISMATCH` 只补充 Bin 最终位置，不扩大前序 Cell NG 的业务影响范围。

### 12.4 单盘放置结果上报

每盘正常 PUT 或 MATERIAL/CELL NG 放置形成确定的设备结果后，WES 发送：

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f45e",
  "operation": "outbound.material.movement_report@v1",
  "timestamp": 1786065500000,
  "data": {
    "task_id": "PICK-20260811-001",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_id": "BIN-001",
      "cell_id": "CELL-03"
    },
    "PkgID": "PKG-001",
    "to_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "A",
      "slot_id": "A-05"
    },
    "occurred_at": 1786065499900
  }
}
```

MATERIAL NG 放置结果示例：

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f45f",
  "operation": "outbound.material.movement_report@v1",
  "timestamp": 1786065501000,
  "data": {
    "task_id": "PICK-20260811-001",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_id": "BIN-001",
      "cell_id": "CELL-04"
    },
    "PkgID": "PKG-NG-001",
    "to_locator": {
      "type": "NG_ZONE",
      "zone_code": "MATERIAL_NG_01"
    },
    "occurred_at": 1786065500900
  }
}
```

CELL NG 放置结果示例（与上面的 MATERIAL NG 示例是独立分支）：

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f460",
  "operation": "outbound.material.movement_report@v1",
  "timestamp": 1786065502000,
  "data": {
    "task_id": "PICK-20260811-001",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_id": "BIN-001",
      "cell_id": "CELL-05"
    },
    "PkgID": "PKG-NG-002",
    "to_locator": {
      "type": "NG_ZONE",
      "zone_code": "CELL_NG_01"
    },
    "occurred_at": 1786065501900
  }
}
```

字段约束如下：

| JSON Path | 必填 | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 当前 PickingTask |
| `data.source_locator` | 是 | `RACK_SLOT \| BIN_CELL` / WMS 原值 | 必须与前序物料决定中的来源完全一致 |
| `data.PkgID` | 是 | string[1..256] / 扫码设备原文 | 必须与前序物料决定中的 `six_in_one.PkgID` 完全一致 |
| `data.to_locator` | 是 | `RACK_SLOT \| NG_ZONE` / WMS 决定 | 必须与前序决定的精确目标或 NG 去向一致 |
| `data.occurred_at` | 是 | UTC Unix 毫秒 / 设备结果 | 实际放置完成时间 |

- WMS 根据 `task_id + source_locator + PkgID` 找到前面的最终物料决定。位置上报不重复发送六合一码。
- 正常 PUT 的相同业务字段必须已经得到最终 `ACCEPT`，并使用其 `RACK_SLOT`；MATERIAL/CELL NG 必须已经得到最终 `REJECT`，
  并使用其 `NG_ZONE`。
  WMS 根据前面的业务决定判断 NG 影响范围，不接受本次上报再传一套业务异常分类。
- WES 仅在执行本次放置的 DeviceCommand 已取得确定 `SUCCEEDED` 并保存本地证据后发送；`command_code` 不进入 WMS 数据格式。
- 位置未知、放置失败或 DeviceCommand 结果未知时禁止发送已完成位置结果；WES 暂停受影响的任务明细，等待确定的设备结果或人工核对。

WMS 正常接收后返回：

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f45e",
  "code": "RECORDED",
  "timestamp": 1786065500100,
  "data": {}
}
```

WMS 必须先在同一事务中保存结果上报，并更新物料位置、库存、来源占用和目标 SLOT 占用，再返回 `RECORDED`。相同
`operation_id` 和相同请求内容再次到达时，返回 `200 / DUPLICATE`，并复用第一次响应的 `timestamp + data`；相同 ID、不同请求内容返回
`409 / CONFLICT`。后续物料决定、
容量判断、货架离场和任务状态确认必须读取已经提交的位置结果。

WES 收到 `RECORDED | DUPLICATE` 前不得把依赖该位置结果的目标容量、来源明细或任务视为完成；但当 WMS 已对当前盘返回
`next_source_action=CONTINUE` 时，WMS 对这次上报的确认不是来源机械臂开始下一条命令的统一前提。两机械臂能否同时进入干涉区仍由
ECS/PLC 硬件锁裁决。

### 12.5 结果上报失败后的重试

响应未知或 `UNAVAILABLE` 时，WES 保留现场记录与相关资源，使用原 `operation_id` 和原请求内容重试；不能创建第二份
结果上报 ID，也不能等待额外结果回调。

## 13. PickingTask 状态确认

### 13.1 WES 发起条件

```text
no_local_business_execution_obligation == true
AND no_unapplied_plan_delta == true
AND all_required_movement_facts_confirmed == true
```

每条已接收的任务明细都必须有处理结果：成功、已确认 NG，或者确定无法完成。以下情况还没有处理结果：Transport 为
`UNKNOWN/RECONCILING`、设备结果未知、仍在等待 WMS 决定。存在这些情况时，WES 不能请求完成任务。

WES 只检查当前任务是否还有待执行工作，不重新汇总所有历史结果。货架离位、Bin 退回、Transport 离场和下一任务能否启动，不是
PickingTask 状态确认的条件。

`all_required_movement_facts_confirmed` 表示所有必须保存的移动结果都有确定值，不表示每次移动都成功。搬运失败时，只有对象位置已经
确定、WES 已保存结果并结束对应任务明细，这个条件才为 `true`。

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
    "result": "COMPLETED"
  }
}
```

WES 计划版本落后示例：

```json
{
  "operation_id": "019f3423-e282-7672-82f5-322ed259f231",
  "code": "DECIDED",
  "timestamp": 1786066001100,
  "data": {
    "result": "PLAN_REVISION_STALE",
    "current_plan_revision": 6
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
    "result": "BUSINESS_IN_PROGRESS",
    "retry_after_ms": 1000
  }
}
```

到期重新确认时使用新的 `operation_id` 和当前版本：

```json
{
  "operation_id": "019f3423-e282-7672-82f5-322ed259f233",
  "operation": "outbound.picking_task.completion_confirm@v1",
  "timestamp": 1786066003100,
  "data": {
    "task_id": "PICK-20260811-001",
    "last_applied_plan_revision": 0
  }
}
```

如果 WMS 在第二次确认前已经完成业务，允许直接以 revision 0 完成：

```json
{
  "operation_id": "019f3423-e282-7672-82f5-322ed259f233",
  "code": "DECIDED",
  "timestamp": 1786066003200,
  "data": {
    "result": "COMPLETED"
  }
}
```

请求字段说明：

| JSON Path | 必填 | 类型/生成方 | 说明和校验规则 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | string / WMS 原值 | 当前 PickingTask |
| `data.last_applied_plan_revision` | 是 | non-negative integer / WES 引用 | WES 已经连续保存并应用的最高计划版本；尚无计划时为 `0`，只用于和 WMS 当前版本比较 |

响应字段说明：

| `data.result` | 必填字段 | 禁止字段 | 说明和校验规则 |
| --- | --- | --- | --- |
| `COMPLETED` | 无 | `current_plan_revision`、`retry_after_ms` | 当前任务的全部已接收明细都已处理完，而且双方计划版本一致 |
| `PLAN_REVISION_STALE` | `current_plan_revision` | `retry_after_ms` | WMS 当前版本更高，并重新投递缺失增量 |
| `BUSINESS_IN_PROGRESS` | `retry_after_ms` | `current_plan_revision` | 双方版本一致，但 WMS 中的 PickingTask 还没有完成 |

`PLAN_REVISION_STALE` 时，WMS 必须重新投递缺失的连续计划增量，禁止只要求 WES 盲目重试确认；当双方版本均为 `0` 时禁止返回
该结果。`BUSINESS_IN_PROGRESS` 表示双方版本一致，但 WMS 中的任务还没有完成。WES 等待新业务数据，或在 `retry_after_ms` 到期后使用新的
`operation_id` 重新确认。

`result` 只允许以下值：

- `COMPLETED`：`last_applied_plan_revision` 与 WMS 当前版本一致。WMS 根据已经保存的逐盘结果、空取决定、NG 结果和 Transport
  结果，确认当前任务的全部已接收明细都已处理完。双方版本可以同为 `0`，表示 WMS 在首批计划形成前已经结束任务。WMS 不再为该任务
  发布计划增量。没有满足的需求由 WMS 创建新的 PickingTask。
- `PLAN_REVISION_STALE`：WMS 当前版本更高，携带 `current_plan_revision` 并重新投递缺失增量。
- `BUSINESS_IN_PROGRESS`：版本一致，但 WMS 中的任务还没有完成；返回 `retry_after_ms`，WES 等待新业务数据或到期后重新确认。

WMS 根据自己已经保存的逐盘结果和任务状态判断是否完成。WES 不需要再发送来源或 Bin 的历史结果数组。
`last_applied_plan_revision` 只用于检查双方的计划版本是否一致，不用于核对每条任务明细。

`COMPLETED` 只表示当前 PickingTask 已经结束，不表示原订单或波次的需求全部满足。PickingTask 不设置 `FAILED` 状态。空取、NG 或
确定的 Transport 失败只结束受影响的任务明细，其他任务明细继续执行。WMS 汇总没有满足的需求，使用新的 `task_id` 创建 PickingTask。
新任务的 `plan_revision` 从 `1` 开始。

`COMPLETED` 只结束 PickingTask 的业务处理和计划变更，不会自动完成仍在物流线上的 Bin 退箱或货架离场。WMS 必须保留任务完成时的
数据和相关占用，并继续接受既有 Bin 的 `outbound.bin.return_batch@v1` 和任务相关货架的
`outbound.rack.departure_decide@v1`，直到这些物理对象完成退箱或离场。WMS 不得仅因 PickingTask 已完成返回
`STATE_CONFLICT`；这些后续决定不得新增取料明细、重新打开任务或发布新的计划增量。

`PLAN_REVISION_STALE | BUSINESS_IN_PROGRESS` 后重新确认使用新的 `operation_id` 和当前 `last_applied_plan_revision`。

## 14. 失败、重试和人工核对

### 14.1 Transport 失败后怎么处理

Transport 是独立的搬运能力，它不知道 PickingTask。Transport 请求中不传 `task_id`，WMS 和 WES 分别使用自己已经保存的数据找到
受影响的任务明细。

WES 在创建 TransportTask 时，保存任务明细与 `transport_task_id` 的对应关系。WMS 已经保存 PickingTask 的来源分配、
`inbound_batch` 和 `return_batch` 结果，可以根据这些数据以及 Transport 结果中的 `rack_id`、`container_id` 找到对应任务明细。

WMS/RCS 本来就是 Transport 结果的产生方，所以 WES 不需要再向 WMS 上报一次 Transport 失败。WMS 也不需要向 WES 返回恢复方案。
双方按下表处理：

| Transport 结果 | WES 怎么处理 | WMS 怎么处理 |
| --- | --- | --- |
| `SUCCEEDED` | 保存实际位置，继续当前任务 | 保存搬运结果，不需要额外操作 |
| `REJECTED \| FAILED` | 根据每个货架或 Bin 的结果，结束确定失败的任务明细；已经成功和不受影响的明细继续执行 | 根据相同的搬运结果统计没有满足的需求，创建新的 PickingTask；不修改当前任务，也不通过当前任务的 `plan_delta` 补单 |
| `UNKNOWN/RECONCILING` | 暂停受影响的任务明细和后续物理动作，保留相关资源 | 等待 RCS 后续确定结果或完成人工核对，再为同一 `transport_task_id` 发送更高版本的结果 |

`UNKNOWN/RECONCILING` 不是成功，也不是失败。WES 不能结束任务明细、释放资源或创建替代 TransportTask。

WMS 必须保证同一个正在搬运的货架或 Bin 不会同时分配给两个未结束的 PickingTask 或批次。否则，WMS 无法只根据现有任务数据和
Transport 结果判断影响了哪张任务。这个规则属于 WMS 现有的库存与分配处理，不需要增加接口字段或跨系统锁。

一个 Transport 失败不会让整张 PickingTask 失败。当前任务的全部已接收明细都有处理结果后，WES 只调用
`outbound.picking_task.completion_confirm@v1`。任务完成后的退箱或货架离场仍按各自流程处理；清理失败不会重新打开 PickingTask。

### 14.2 通用失败与重试

- `UNAVAILABLE` 或响应未知：使用原 `operation_id` 和原请求内容重试。
- `REJECTED`：停止重试原请求；修正内容后使用新 `operation_id`。
- `CONFLICT`、版本跳号、已保存字段被修改或无法找到对应任务：停止自动处理，保留现场资源并进入人工核对。
- 计划增量没有得到明确响应：WMS 只重发原消息，不能跳过该 revision 发布后一版本。
- 准备请求没有得到明确响应：WES 只重发原请求，不能换线或发起第二次准备。
- `WAIT`、`NO_BATCH`、`PLAN_REVISION_STALE` 或 `BUSINESS_IN_PROGRESS` 后重新判断：新请求使用新的 `operation_id`，并携带原来的
  业务字段、记录 ID 或当时的资源数据；不增加请求链字段。
- Transport `UNKNOWN`：位置和后续步骤继续等待。按照 Transport 合同，等待同一 `transport_task_id` 后续更高版本的确定结果，不创建替代
  TransportTask。
- Transport `REJECTED | FAILED`：只结束本地对应的任务明细。WMS 根据自己形成并发送的 Transport 结果创建后续 PickingTask，不增加
  失败上报或恢复接口。
- DeviceCommand 结果未知：保留当前待完成的物理工作，不把未知解释为失败、NG 或完成。

`WmsClient` 每次只执行一次 HTTP/JSON 访问。Outbox、自动重试、计划顺序、状态推进和人工核对由对应业务模块负责。

## 15. 联调验收清单

| 场景 | 预期结果 |
| --- | --- |
| 请求内容含 `//` 注释或不是标准 JSON | 返回空 Body `400`；请求正文必须是标准 JSON |
| 请求或响应出现未知字段、`null`、空条件数组或错误字段类型 | 返回 `422 / REJECTED + INVALID_DATA`，不做部分接收 |
| `six_in_one.Qty` 使用 JSON number | 返回 `422 / REJECTED + INVALID_DATA`；六个扫码值必须都是字符串 |
| 位置对象的 `type` 与字段、来源类型或目标用途不一致 | JSON 结构错误返回 `422`；与 WMS 已保存的业务数据冲突返回 `409` |
| 某种响应缺少必填字段，或混入另一种响应的字段 | JSON 测试用例必须拒绝，客户端不能猜测应该按哪种结果处理 |
| WMS 发布 PickingTask | 只入队，不锁定 WorkLine、来源、目标或物理动作 |
| 准备请求已接收 | 快速返回 `PREPARE_ACCEPTED`；没有计划增量前不创建 TransportTask 或 DeviceCommand |
| 准备响应丢失 | WES 使用原 `operation_id` 和原正文重试；WMS 返回第一次保存的完整 `PREPARE_ACCEPTED` 响应 |
| 首个计划增量 | `plan_revision=1` 必须且只能定义一个初始 `target_rack`，可以同时新增来源明细；不携带增量类型或 WMS 计算进度字段 |
| WMS 分批计算资源 | 每批 revision 连续；WES 保存成功后再响应，数据完整的首批明细可以立即执行 |
| 后续计划增量 | `plan_revision>=2`，禁止 `target_rack`；新的精确目标只由逐盘最终 `ACCEPT` 返回 |
| 首批增量只有初始接料货架面 | 可以提前运输目标架，但不能凭空创建来源取盘动作 |
| 增量追加多个货架类型 | 五层、退料和转运货架按各自固定目标位并行运输 |
| revision 跳号或同版本不同内容 | WES 停止自动推进并人工核对，不覆盖已接收计划 |
| WMS 内部资源计算仍在继续 | 不向 WES 暴露计算完成字段；已接收明细继续执行，状态确认返回 `BUSINESS_IN_PROGRESS` |
| 执行中补充正常计划 | 更高 `plan_revision` 可以增加当前任务尚未发布的直接取料来源或五层来源货架面；不能用来替换空取、NG 或 Transport 确定失败的明细 |
| 扫码后才确认尺寸 | WMS 返回精确 SLOT 和可选换面/换架方案；当前盘允许在扫码台有界等待 |
| 当前盘 PUT 与下一盘准备重叠 | 两个 `device_code` 可各有一条命令；无安全暂存位时，ECS/PLC 在下一盘离开来源前取得扫码台交接许可 |
| 审查扫码台协调实现 | 不存在扫码台释放事件、WES 资源锁、租约或跨机械臂软件互锁 |
| 目标架不满足当前盘 | 同一 `ACCEPT` 返回完整 `target_preparation`；Transport 到位后直接 PUT，不重新验证物料 |
| 正常 PUT 命令 | 目标机械臂命令只使用已授权逻辑目标；确定 `SUCCEEDED` CALLBACK 前不生成逐盘位置结果 |
| 正常 PUT 位置确认 | `movement_report` 引用前序 WMS 决定并原样返回精确去向；设备证据留在 WES，WMS 在同一事务中更新位置、库存及目标占用 |
| PUT 结果未知 | 不上报完成位置、不替换目标、不释放相关容量；保留现场状态，等待确定的设备结果或人工核对 |
| CTU 乱序投箱 | `inbound_batch` 已返回的 Bin 按实际到达顺序请求工作计划，FIFO 队首不能被绕过 |
| 计划同时包含多个五层来源货架面 | 每个 `rack_id + rack_face` 单独记录；同一货架的 A、B 面都有来源时记录两项；WES 只选择一个当前来源面 |
| 五层货架入站分批 | WES 发送 `max_bin_count`；WMS 返回不超过该数量的 `bins[]`；WES 再补充本地入料目标并生成 Transport `BIN_MOVE` |
| 同一 WorkLine 的 CTU 批次 | 入站和退箱串行，同一时刻最多一个批次处于 WMS 请求或 Transport 执行中；不建立缓存位预留、租约或锁 |
| 多个事件同时触发 CTU 判断 | 自动出库业务模块在事务中只声明一个下一动作；其他触发发现已有未结束动作后退出 |
| 入站 `READY` 已返回但 Transport 未完成 | 继续等待原批次，不调用 `return_batch`、不再次调用 `inbound_batch`、不改选其他 Bin |
| 入站 Transport 确定成功 | 重新判断 CTU 下一动作；`RETURN_BUFFER` 有候选时先退箱，没有候选时才继续入站 |
| 当前五层来源货架面暂时无 Bin | WMS 返回 `NO_BATCH + retry_after_ms`；到期前不重复请求、不据此换面，期间出现退箱候选仍优先退箱 |
| 当前五层来源货架面结束 | WMS 返回 `RACK_FACE_DONE`；从该面选出的 Bin 都有确定最终去向后，WES 才从已接收的来源面计划中选择下一面并执行必要的换面或换架；`inbound_batch` 不返回新来源方案 |
| Transport `UNKNOWN/RECONCILING` | 只暂停受影响的任务明细，等待同一 `transport_task_id` 的更高版本结果或人工核对；不结束明细、不释放资源、不创建替代 TransportTask |
| Transport 确定失败 | WES 结束本地对应的失败明细，其他明细继续；WMS 已知 Transport 结果并用新的 PickingTask 补足需求，双方不交换失败上报或恢复方案 |
| 退箱候选请求 | WES 按本次请求从 1 连续设置 `sequence_no`；WMS 原样返回连续前缀的 `sequence_no + bin_id`，不得重复、跳号或跳过队首 |
| WMS 本次只处理部分退箱候选 | 只能返回 FIFO 候选列表的连续前缀；未返回的 Bin 留在 `RETURN_BUFFER` 等下一批，新请求重新从 1 编号 |
| 退箱暂时无批次 | `NO_BATCH` 必须返回 `retry_after_ms`；候选仍在时禁止新入站；WES 可被新业务数据提前唤醒，否则到期重试 |
| 退箱目标储位 | `moves[].target` 只能位于请求的当前 `rack_id + rack_face`；`return_batch` 不触发换面或换架 |
| 当前面确定没有可用退箱储位 | WMS 返回 `409 / CONFLICT + STATE_CONFLICT`；WES 暂停并告警，不自动换面、换架，也不用 `NO_BATCH` 无限重试 |
| 已选 Bin 仍可能进入退料缓存 | 当前货架面保持在 CTU 工作位；所有已选 Bin 确定退回当前面、到达 NG 出口或进入其他确定最终位置后，才允许换面、换架或离场 |
| 未匹配物理 Bin 到达 NG 出口 | 上报时使用 `expected_bin_id` 找到受影响来源，但不把预期 Bin 当作实际扫码结果，也不自动关闭原明细 |
| Cell 物料绑定冲突 | WMS 返回 `REJECT + SOURCE_CELL_MISMATCH + CLOSE`；当前盘进入 NG，位置结果确认后关闭当前 Cell |
| 空取形成需求缺口 | `RETRY` 只重试原来源；`WAIT` 只表示决定未稳定；`SOURCE_DONE` 关闭当前来源，缺口由新的 PickingTask 承接 |
| NG 形成需求缺口 | 当前任务结束已确认 NG 的明细并继续其他明细；WMS 用新的 PickingTask 处理未满足需求，不通过当前任务的计划增量替换来源 |
| 状态确认与追加竞态 | WMS 以当前 `plan_revision` 在同一事务中判断；版本落后时返回 `PLAN_REVISION_STALE` 并补发增量 |
| 准备期尚无首批计划 | WES 使用 revision 0 确认；进行中响应必须带重试间隔，业务已完成可直接 revision 0 完成 |
| PickingTask 状态确认 | 所有已接收明细必须已有成功、NG 或确定无法完成的结果；请求不重复发送所有历史明细，WMS 根据自己已保存的数据返回状态 |
| PickingTask `COMPLETED` | 只表示当前任务明细都已处理完且不再追加计划，不表示订单需求全部满足；任务没有 `FAILED` 状态，未满足需求由新 `task_id` 处理 |
| PickingTask 完成后物理处理 | WMS 继续受理既有 Bin 退箱和任务相关货架离场决定，直到物流对象完成；不得重新打开任务或追加明细 |

## 16. 实施与验收所有权

| 所有者 | 验证范围 |
| --- | --- |
| WMS Adapter 合同 | 固定 path、公共消息格式、operation、数据格式、重复提交、revision 和错误映射 |
| 自动出库插件 | PickingTask、计划增量应用、CTU 串行批次和数量计算、Bin/Cell 接收后不可修改规则、Cell 循环、任务明细处理、双臂业务并发和状态确认触发 |
| Transport 核心 | 货架与 Bin 搬运、接收确认、异步结果、`UNKNOWN/RECONCILING` 和资源绑定 |
| DeviceCommand 核心 | 单设备命令接收、接收确认、最终状态、超时时间和重复提交记录 |
| ECS/PLC 及供应商验收 | 扫码台硬件锁、机械臂防撞、长命令内部动作和设备实际行为 |

基础能力不得用自动出库业务场景证明；自动出库插件也不得复制 Transport、DeviceCommand 或 ECS/PLC 的基础状态机。

## 17. 正式实施前确认项

批准前必须完成以下联合评审和机械化确认：

- WMS 与 WES 开发组共同评审本文并把 `status` 从 `ReviewRequired` 更新为 `Approved`；有异议必须先修改本文，禁止在代码中
  形成另一份实际生效的合同。
- 根据本文生成或手写一份严格 JSON Schema；Schema 只能机器化本文，必须设置未知字段拒绝，不得新增别名、默认值、`null`、
  扩展对象或兼容分支。
- 部署配置提供真实 `workline_code`、货架面、工作位、缓存位、NG 区和货架离场库位编码。编码值可以按现场变化，但字段结构、类型和
  WMS、WES 的职责划分不得变化；两边代码都不能硬编码本文示例值。
- 联调配置确认 WMS 分批计算首批期限、各业务 `WAIT` 的实际重试值和人工核对时限。这些是运行参数，不改变
  `retry_after_ms` 的字段合同。
- 目标机械臂实际设备合同附录锁定 PUT `task_type`、逻辑 `params`/结果 `data`、时限和错误；未获批前不得用占位附录、全局枚举
  或供应商私有字段开始设备实现。
- 双方使用相同的 JSON 测试用例，覆盖每种响应结果、条件字段、重复提交、版本跳号、引用冲突、Bin/Cell 接收后不可修改规则和完成确认并发。测试用例
  可以机器化合同，但不得反向修改合同语义。
- ECS/PLC 单独确认两机械臂硬件锁、单设备单活动命令、扫码台单盘承载和防撞现场验收方案；这些硬件能力不进入 WMS/WES 数据格式。

不得从本文扩展出 JSONC、自由文本错误、通用资源锁、通用工作流引擎或兼容旧合同的双路径。
