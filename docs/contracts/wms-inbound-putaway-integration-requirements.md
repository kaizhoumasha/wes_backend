---
title: WMS / WES 满箱交换与自动上架交互要求
status: ReviewRequired
created_at: 2026-08-13
updated_at: 2026-08-20
audience: WMS 与 WES 初级开发工程师、联调与测试人员
scope: Phase 13 `automatic_putaway` 的满箱交换、自动上架、目标 Bin 投退料、NG、事实确认和人工对账
related:
  - docs/contracts/wms-rough-sorter-inbound-integration-requirements.md
  - docs/architecture/SRS.md
  - docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
  - docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/contracts/wms-async-callback-envelope-contract.md
  - docs/contracts/transport-fulfillment-contract.md
  - docs/architecture/authority-matrix.md
  - docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
---

# WMS / WES 满箱交换与自动上架交互要求

## 1. 文档定位

本文定义已完成粗分入库的料盘和单层货架进入满箱交换及自动分拣线上架后的 WMS/WES 交互要求。
本文是 `ReviewRequired` 的联合评审基线，拟冻结调用方向、operation、请求与响应字段、幂等、物理证据门禁、失败边界和联调验收口径；
只有第 20 节确认项全部闭合并转为 `Approved` 后，才构成正式实施授权。

Phase 8 粗分逐盘入库已经拆分到获批真源
[`wms-rough-sorter-inbound-integration-requirements.md`](wms-rough-sorter-inbound-integration-requirements.md)。本文只消费其已闭合的
placement、单层货架和释放快照事实，不复制其准入、目标晚绑定、设备链、换架 Transport 或五态生命周期。

本文只依赖重构后的 `MaterialExecution`、`BinExecution`、`PositionProjection`、`DeviceCommand`、`TransportTask`、
`WmsConfirmation`、`InboundEvidence` 和 `LineRunEpoch`。旧 `rough_sorter`、`smt_sorting_inbound`、旧通用
operation registry、旧 `confirm_inbound`、旧 `notify_pkg_binding`、通用 Runtime/Effect 和兼容路径均不是实现模板。

本文只定义 `FULL_BIN_EXCHANGE` / `AUTOMATIC_PUTAWAY`：满箱交换和自动分拣线把已经完成入库的料盘或 Bin 上架，
只迁移权威位置，不重复入库确认。

码头收货、IQC、特殊/MSD 物料、人工入库和生产退料不属于本文。系统尚未发布，不提供旧 operation、兼容字段、
双路径、历史数据迁移或通用动作接口。

## 2. 主流程

### 2.1 四段因果结构

自动上架按“执行任务驱动 → 机械臂执行 → 业务完成 → Bin/货架独立清场”四段闭合。这里的“任务”不是新增的
WMS `InboundTask`：WMS 以 `putaway_plan_id` 冻结必须处理的来源成员，WES 以 `putaway_execution_id` 标识该计划在当前
`LineRunEpoch` 的一次本地执行；二者共同构成自动上架的执行任务边界。

| 分段 | 开始条件 | 完成条件 | 不得混淆 |
| --- | --- | --- | --- |
| 执行任务驱动 | 粗分释放快照完整，WMS 返回不可变来源计划，WES 在自动线创建上架执行 | 来源成员、当前 WorkLine 和 Epoch 已冻结；目标资源仍按批次或逐盘晚绑定 | 不新增入库单、波次或兼容任务键，不把计划 `READY` 当作物理完成 |
| 机械臂执行 | 来源与目标 Bin 均通过物理准入，WMS 为当前料盘返回精确目标 | 北向机械臂完成取盘和复扫，南向机械臂完成 PUT，并形成确定设备结果和位置 Fact | ACK、设备忙闲或已下发命令都不是 PUT 完成；WES 不自选 Bin/Cell |
| 业务完成 | 正常 PUT、Material NG 和满箱交换已有 WMS 接纳的终局 Fact，来源缺失已有明确 `SOURCE_ABSENT` 决定 | WMS 根据不可变计划和自身持久化事实返回 `COMPLETED` | 不等待目标 Bin 退回、NG Bin 人工取走或来源货架搬离 |
| Bin/货架独立清场 | 业务成员闭合后仍存在目标 Bin、NG Bin、来源货架或 Transport 义务 | 各对象到达确定位置，外部义务闭合后才满足 WorkLine 释放门禁 | 业务 `COMPLETED` 不释放 WorkLine；清场未完成时不得切换插件或创建新 Epoch |

正常来源成员只有在机械臂可靠 PUT 且 `putaway.material.placement_report@v1` 被 WMS 返回
`RECORDED | DUPLICATE` 后才完成。`REJECT` 后的 NG 到位 Fact、可靠空取后的 `SOURCE_ABSENT` 等分支可以关闭来源成员，
但属于各自明确终态，不能冒充正常 PUT 完成。

满箱 `exchange_source` 是同一执行任务中的独立 Transport 分支：它在交换成员位置 Fact 全部闭合后结束，不进入机械臂逐盘执行段；
其未闭合成员仍会阻止上架业务 `COMPLETED`。四段结构表达因果边界，不要求每类来源都经过相同设备。

### 2.2 Operation 流程映射

| 阶段 | 触发条件 | WMS/WES 交互 | 成功后的处理 |
| --- | --- | --- | --- |
| 上架来源计划 | 释放快照完整且无未知位置 | WES 请求 `putaway.source_rack.plan_decide@v1` | WMS 返回不可变 `putaway_plan_id`，冻结全部来源成员和业务资格，不提前冻结交换目标 |
| 满箱交换 | 当前面来源待处理，且 WMS 当前批次决定为 `READY` | WES 为同面 1～2 对创建一个 `exchange_bins()` Transport | 搬运最终结果全成功且全部成员 Fact 被 WMS 记录后才重算下一批；否则人工恢复 |
| 自动线上架准入 | WorkLine 已清线，并在新 Epoch 激活 `automatic_putaway` | WES 运送来源单层货架，并按缓存需求请求目标 Bin | 同一 WorkLine 只激活一个 `putaway_execution_id` |
| 目标 Bin 投料 | 投料缓存有具体空位且 CTU 有背篓空间 | WES 请求 `putaway.target_bin.supply_batch@v1` | WMS 返回库存主账中至少有一个可分配空 Cell 的精确 Bin |
| SCAN1 路由 | 实际 Bin 到达 SCAN1 并已完成 `SUPPLY_PLACED` | WES 请求 `putaway.target_bin.route_decide@v1` | WMS 返回进入生产、正常无任务、标记 NG 或等待 |
| SCAN2 可用性 | SCAN1 返回进入生产 | WES 请求 `putaway.target_bin.work_admission_decide@v1` | WMS 判断实际 Bin 当前是否仍有可分配 Cell |
| 逐盘上架 | 来源盘到扫码平台并复扫完整六合一码 | WES 请求 `putaway.material.decide@v1` | WMS 返回精确目标 Bin/Cell；PUT 后只记录位置迁移 |
| SCAN3/SCAN4 分流 | 目标 Bin 完成生产或不进入生产 | WES 按已保存的 NORMAL/NG 结果做物理路由 | NG 跨线到统一出口；NORMAL 经 SCAN4 进入本线退料缓存 |
| 目标 Bin 退料 | Bin 可靠进入本次上架执行的 FIFO | WES 请求 `putaway.target_bin.return_batch@v1` | WMS 为连续队首分配当前五层货架面的精确空位 |
| 上架完成 | 不可变来源成员和逐盘业务 Fact 全部闭合 | WES 请求 `putaway.execution.completion_confirm@v1` | WMS 返回 `COMPLETED \| NOT_COMPLETED`；物理清线独立闭环 |

不定义 WMS 预发的 `InboundTask`、`ReceivingTask` 或 `WMS_GRN_RECEIVED` 设备启动语义。上架由获批粗分合同形成的冻结货架
快照驱动，目标 Bin 供给由 WorkLine 当前物理容量驱动。

## 3. 权威边界

| 参与方 | 唯一权威 | 不承担 |
| --- | --- | --- |
| WMS | GRN、`pkg_id`、库存主账、物料资格、目标 Bin/Cell、堆叠参数、满箱资格、交换配对、可用 Bin、空储位、业务路由、NG 业务分类和业务终态 | WorkLine 物理准入、设备安全、设备命令状态和车辆调度 |
| WES | WorkLine 模式与静态拓扑、`LineRunEpoch`、执行身份、设备与运输证据、位置投影、本地缓存准入、可靠外部义务和最小安全隔离域 | GRN 匹配、库存重算、Cell 容量算法、满箱阈值、替代 Bin/Cell 或 WMS 业务终态 |
| RCS/AGV/CTU | 车辆、路径、避让、内部重试、运输终态和成员位置证据 | GRN、库存、上架计划、Bin/Cell 业务资格 |
| ECS/PLC/设备 | 扫码与测量原始证据、机械动作、输送、硬件互锁、防撞、位置传感和设备终态 | GRN、业务资格、库存、目标分配和业务 NG 决定 |

WMS 返回业务对象和逻辑目标；WES 从当前 `LineRunEpoch` 的固定拓扑中选择粗分机位、满箱交换位、自动 WorkLine、
A/B 位和缓存位置；RCS 选择车辆与路线。三者不得相互替代。

## 4. 公共信封、端点和 HTTP 语义

### 4.1 公共信封

请求顶层固定为：

```json
{
  "operation_id": "019f3e20-96b7-77f2-9f10-2b46993ef401",
  "operation": "putaway.source_rack.plan_decide@v1",
  "timestamp": 1786579200000,
  "data": {
    "rack_release_id": "RR-20260813-001",
    "rack_id": "RACK-01"
  }
}
```

响应顶层固定为：

```json
{
  "operation_id": "019f3e20-96b7-77f2-9f10-2b46993ef401",
  "code": "DECIDED",
  "timestamp": 1786579200123,
  "data": {
    "result": "WAIT",
    "reason_code": "TARGET_RESOURCE_PENDING",
    "retry_after_ms": 2000
  }
}
```

顶层字段和 operation 专属 `data` 均为严格闭集。首次发送前，发起方必须原子持久化全局唯一 UUIDv7 `operation_id`、
完整 Payload 和首次 `timestamp`；重试保持三者不变。

业务 `WAIT` 或 `NO_BATCH` 后重新求值时使用新的 `operation_id`，并在新请求的 `data.previous_operation_id` 中引用直接前序
请求；首次请求禁止该字段。`previous_operation_id` 不是顶层信封字段，也不得用于技术重试。

| JSON Path | 必填 | 类型/格式 | 生成方 | 语义与约束 |
| --- | --- | --- | --- | --- |
| `operation_id` | 是 | UUIDv7 字符串 | 当前消息发起方 | 一次不可变跨系统交互身份；原请求重试必须复用 |
| `operation` | 是 | 本文第 5 节固定枚举 | 当前消息发起方 | 决定唯一 DTO；不接受别名、旧版本或通用动作名 |
| `timestamp` | 是 | UTC Unix 毫秒整数 | 当前消息发起方 | 首次持久化时间；重试不得刷新 |
| `data` | 是 | object | 当前消息发起方 | operation 专属严格闭集；无字段时使用 `{}` |
| 响应 `operation_id` | 是 | UUIDv7 字符串 | 接收方回显 | 原样回显已解析请求 |
| 响应 `code` | 是 | 第 4.3 节固定枚举 | 接收方 | 协议接纳、业务决定、事实提交或失败类别 |
| 响应 `timestamp` | 是 | UTC Unix 毫秒整数 | 接收方 | 首次形成并可靠保存完整响应的时间；重放首次值 |
| 响应 `data` | 是 | code 专属 object | 接收方 | 严格闭集；无字段时使用 `{}` |

业务 DTO 不定义 `request_id`、`event_id` 或 `dispatch_key`。HTTP 链路追踪可以使用 `X-Request-ID`，但不得进入业务幂等。

### 4.2 端点

| 发起方 | 接收方 | 方法和路径 | 模式 |
| --- | --- | --- | --- |
| WMS | WES | `POST {{WES_BASE_URL}}/api/v1/wms/events` | Event + 持久化 ACK |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/decisions` | 同步业务决定 |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/facts` | 同步事实确认 |

三个接口的原始 Body 上限均为 `256 KiB`。非法 JSON、无法提取合法 `operation_id`返回空响应体 `400`；解码前超限返回
空响应体 `413`。建立合法关联后，所有响应必须使用公共响应信封。

### 4.3 HTTP 与 code

| HTTP / `code` | 含义 |
| --- | --- |
| `200 / DECIDED` | 同步业务决定已形成；调用方读取 `data.result` |
| `200 / RECORDED` | Fact 及其要求的 WMS 业务状态已在一个事务中提交 |
| `200 / DUPLICATE` | 相同 Event 或 Fact 已接纳；不表示新的业务动作 |
| `202 / RECEIVED` | WMS Event 已由 WES 可靠持久化 |
| `422 / REJECTED` | 信封、operation 或专属 DTO 非法 |
| `409 / CONFLICT` | 幂等内容、状态、引用或不可变约束冲突 |
| `429 / BUSY` | 暂时没有接收容量；返回 `retry_after_ms` |
| `503 / UNAVAILABLE` | 当前无法可靠持久化或处理 |

业务 `WAIT`仍使用 `200 / DECIDED`，并由新 `operation_id`重新求值；`429 / BUSY`、`503 / UNAVAILABLE`、网络超时和
未得到确定响应时使用原身份重试。技术等待不得升级为业务拒绝、NG或完成。

通用失败响应：

| HTTP / `code` | `data` 必填字段 | 枚举/约束 |
| --- | --- | --- |
| `422 / REJECTED` | `reason_code`，`INVALID_DATA`时可带 `field_path` | `INVALID_ENVELOPE \| UNSUPPORTED_OPERATION \| INVALID_DATA` |
| `409 / CONFLICT` | `reason_code` | `IDEMPOTENCY_CONFLICT \| STATE_CONFLICT \| REFERENCE_CONFLICT \| POSITION_CONFLICT` |
| `429 / BUSY` | `retry_after_ms` | `1..60000` 毫秒；到期后仍使用原 Payload |
| `503 / UNAVAILABLE` | 无 | `data={}`；调用方使用原身份重试 |

### 4.4 公共数据类型

- JSON 使用 UTF-8。字段名、枚举和 ID 大小写敏感；未知字段、重复 JSON key、错误类型和枚举外值必须拒绝。
- 可选字段无值时省略；除非字段表明确允许，禁止 `null`、空字符串、空对象和空数组。
- 业务 ID、设备编码和位置编码匹配 `[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}`。
- 时间字段为 `0..9223372036854775807` 的 UTC Unix 毫秒整数。
- `retry_after_ms` 为 `1..60000` 的整数。
- 十进制测量值使用规范字符串 `0|[1-9][0-9]*(\.[0-9]{1,3})?`，单位由字段名固定为毫米，避免 JSON 浮点漂移。
- 数组出现时必须包含 `1..N`项；最大数量由 `256 KiB` Body 上限和 operation 专属上限共同约束。
- `six_in_one`固定包含六个长度 `1..256` 的设备原文字符串：`LotCode`、`DateCode`、`Qty`、`ProductNo`、
  `MfrPN`、`PONumber`。WES不解析、换算或拼接这些字段。

位置对象使用 `type`判别的严格联合：

| `type` | 必填字段 | 语义 | 禁止字段 |
| --- | --- | --- | --- |
| `ONE_LAYER_BIN_CELL` | `rack_id + rack_slot_code + bin_id + bin_cell_id` | 单层货架中一盘物料的逻辑位置 | `rack_face`、`location_code` |
| `BIN_CELL` | `bin_id + bin_cell_id` | 当前自动线上目标 Bin的逻辑料格 | `rack_id`、`rack_face`、`rack_slot_code`、`location_code` |
| `FIVE_LAYER_BIN_SLOT` | `rack_id + rack_face + slot_id` | 五层货架上的单 Bin 储位；映射 Transport `RACK_BIN_SLOT` 时完整保留三个字段 | `rack_slot_code`、`bin_cell_id`、`location_code` |
| `HANDOFF_POSITION` | `location_code` | 投料、SCAN、工作位或退料缓存等固定位置 | Rack、Bin、Cell字段 |
| `RACK_POSITION` | `location_code` | 货架工作位、等待位或业务位置 | Rack、Bin、Cell字段 |
| `NG_POSITION` | `location_code` | 当前 `LineRunEpoch`批准的料盘 NG交接区或 Bin NG出口 | Rack、Bin、Cell字段 |

WMS业务目标只返回逻辑位置。设备坐标、供应商 `location_id`、机械参数和内部动作不进入本文 DTO，由插件依据设备合同附录映射。

### 4.5 实现术语

| 术语 | 本文含义 | 实现要求 |
| --- | --- | --- |
| 决定 | WMS 对当前证据形成的同步业务结果 | `200 / DECIDED`；原身份重放首次完整结果 |
| Event | WMS 主动发送给 WES 的业务输入 | WES先持久化，再返回 `RECEIVED` |
| Fact | WES 上报的已发生物理事实 | WMS返回 `RECORDED \| DUPLICATE`后才能关闭外部确认义务 |
| 证据 | ECS、Transport、WMS输入或人工结果的不可变原始输入 | 先保存为 `InboundEvidence`，不能只保存解析摘要 |
| 位置投影 | WES在活动 `TransportTask` 或 `BinExecution` 管辖期内根据可靠证据维护的确定位置或位置未知 | 只用于物理准入；执行关闭后的旧投影不代表全局当前位置，不替代 WMS库存主账 |
| 终局 | 普通重试不能改写的业务或物理结果 | `WAIT`和 `UNKNOWN`不是失败终局 |
| 对账 | 证据无法支持自动推进时的人工核对 | 保留身份、位置、原始消息和资源绑定 |
| 不可变来源计划 | `putaway_plan_id` 冻结的来源成员全集和业务资格 | 不支持 revision、来源成员增删、覆盖或普通取消；交换目标按面、按批次晚绑定 |

## 5. Operation 实现索引

### 5.1 满箱交换与自动上架

| operation | 方向 | 触发条件 | 首次成功响应 | 详见 |
| --- | --- | --- | --- | --- |
| `putaway.source_rack.plan_decide@v1` | WES 到 WMS | 单层货架释放快照完整 | `200 / DECIDED`：`READY \| REJECT \| WAIT` | §9.1 |
| （下一交换批次 operation 名称待联合评审） | WES 到 WMS | 当前面没有活动批次，且前批业务 Fact 已全部闭环 | 候选：`READY \| TARGET_RACK_REPLACEMENT_REQUIRED \| WAIT \| COMPLETED` | §10.1 |
| `putaway.bin_exchange.movement_report@v1` | WES 到 WMS | 交换成员可靠到达最终目标 | `200 / RECORDED` | §10.2 |
| `putaway.target_bin.supply_batch@v1` | WES 到 WMS | 投料缓存有预留空位且 CTU有空间 | `200 / DECIDED`：`READY \| NO_BATCH \| WAIT` | §12.1 |
| `putaway.target_bin.movement_report@v1` | WES 到 WMS | 实际 Bin供给到线或退回货架 | `200 / RECORDED` | §12.2 |
| `putaway.target_bin.route_decide@v1` | WES 到 WMS | 实际 Bin到达 SCAN1 | `200 / DECIDED`：四类路由结果 | §13.1 |
| `putaway.target_bin.work_admission_decide@v1` | WES 到 WMS | SCAN1决定进入生产且 Bin到达 SCAN2 | `200 / DECIDED`：`AVAILABLE \| PASS_THROUGH \| WAIT` | §13.2 |
| `putaway.material.decide@v1` | WES 到 WMS | 来源盘到扫码平台并复扫完整 | `200 / DECIDED`：`ACCEPT \| REJECT \| WAIT` | §14.1 |
| `putaway.material.target_recovery_decide@v1` | WES 到 WMS | 上架 PUT前发现目标不可执行 | `200 / DECIDED`：`REASSIGNED \| REJECT \| WAIT` | §14.2 |
| `putaway.material.placement_report@v1` | WES 到 WMS | 料盘可靠 PUT到目标 Bin/Cell | `200 / RECORDED` | §14.3 |
| `putaway.material.ng_placement_report@v1` | WES 到 WMS | 上架料盘可靠到达 NG交接区 | `200 / RECORDED` | §14.4 |
| `putaway.source.empty_decide@v1` | WES 到 WMS | 来源 Cell形成可靠空取证据 | `200 / DECIDED`：`RETRY \| SOURCE_ABSENT \| WAIT` | §14.5 |
| `putaway.target_bin.clearance_decide@v1` | WES 到 WMS | 每盘 Fact闭合或不可变来源全部闭合 | `200 / DECIDED`：`KEEP \| RETURN \| WAIT` | §15.1 |
| `putaway.target_bin.return_batch@v1` | WES 到 WMS | 退料缓存存在实际 Bin候选 | `200 / DECIDED`：`READY \| NO_BATCH \| WAIT` | §15.2 |
| `workline.return_buffer.drain_rack_decide@v1`（候选，未获批） | WES 到 WMS | 停止或切换已请求，当前面持续 `NO_BATCH` 且需要为当前 `putaway_execution_id` 的 FIFO 选择排空货架面 | 候选：`200 / DECIDED`：`READY \| WAIT` | 共同实施硬门禁 |
| `putaway.target_bin.ng_exit_report@v1` | WES 到 WMS | NG Bin可靠到达统一末端出口 | `200 / RECORDED` | §15.4 |
| `putaway.execution.completion_confirm@v1` | WES 到 WMS | 本地静态成员与逐盘义务全部闭合 | `200 / DECIDED`：`COMPLETED \| NOT_COMPLETED` | §16.1 |
| `putaway.source_rack.clearance_decide@v1` | WES 到 WMS | 单层货架已无业务成员 | `200 / DECIDED`：四类清场结果 | §16.2 |
| `putaway.execution.reconciliation_decided@v1` | WMS 到 WES | 上架多对象人工对账已形成权威结果 | `202 / RECEIVED` | §16.3 |

## 6. ID 和版本由谁生成

| 字段 | 生成方 | 规则 |
| --- | --- | --- |
| `operation_id` | 当前消息发起方 | 一次不可变消息身份；重试保持不变 |
| `previous_operation_id` | 引用前序请求 | 位于 operation 专属 `data`；`WAIT`或 `NO_BATCH`后新请求引用同一证据的直接前序请求；首次禁止 |
| `material_execution_id` | WES | 一盘实物本次进入自动上架执行的本地身份 |
| `pkg_id` | WMS | GRN准入事务返回的稳定料盘业务身份；WES不得拼接生成 |
| `target_assignment_id` | WMS | 一次不可变目标预留；目标恢复必须生成新身份 |
| `rack_release_id` | WES | 单层货架停止接纳后形成的冻结物理快照身份 |
| `putaway_plan_id` | WMS | 一份不可变上架来源计划；不定义 revision |
| `putaway_execution_id` | WES | `putaway_plan_id`在一条自动 WorkLine上的本地执行身份 |
| `line_run_epoch_id` | WES | 一次 WorkLine 插件激活的技术身份；约束本地执行与拓扑，不代替 `putaway_execution_id` 或 WMS 业务键 |
| `source_execution_id` | WMS | 一个不可变逐盘上架来源成员身份 |
| `exchange_execution_id` | WMS | 下一交换批次决定中返回的一个满 Bin/空 Bin 交换对身份；不属于来源计划静态成员 |
| `bin_execution_id` | WES | Bin可靠到达工作线交接位且扫码身份匹配后，为其线内推进、正常回库或 NGZone人工接管创建的物理执行身份 |
| `scan1_evidence_id` / `scan2_evidence_id` | WES | 当前实际 Bin在对应扫描点的不可变证据身份 |
| `source_observation_id` | WES | 一次可靠空取观察身份 |
| `ng_evidence_id` | WES | 一次可靠 NG到位事实身份 |
| `reconciliation_id` | WMS | 一次已完成业务主账核对的人工对账身份 |
| `command_code` | DeviceCommand | 一条设备命令稳定身份 |
| `client_request_id` | 调用 Transport的业务模块 | 一次不可变 Transport调用；失败后新任务使用新身份 |
| `transport_task_id` | Transport能力 | 一个可靠搬运执行对象身份 |

投料和退料批次不新增业务批次 ID，直接由其不可变请求 `operation_id`标识。Transport `outcome_version`只属于 Transport结果，
不成为业务计划版本。

## 9. 上架不可变来源计划

### 9.1 `putaway.source_rack.plan_decide@v1`

WES 以冻结的 `rack_release_id` 请求一次不可变来源计划：

```json
{
  "operation_id": "0197f300-0000-7000-8000-000000000020",
  "operation": "putaway.source_rack.plan_decide@v1",
  "timestamp": 1786590000000,
  "data": {
    "rack_release_id": "RR-001",
    "rack_id": "SR-01",
    "rack_slot_code": "A",
    "occupied_cells": [
      {"rack_face": "A", "bin_id": "BIN-010", "bin_cell_id": "C03", "pkg_id": "PKG-9001"}
    ]
  }
}
```

`occupied_cells[]` 中每项必须携带该来源 Bin 的 `rack_face=A|B`。`rack_face` 是交换分批所需的物理面证据，不等同于
`rack_slot_code`，不得根据货位字母猜测。

`data.result`严格为：

| `result` | 必填字段 | 含义 |
| --- | --- | --- |
| `READY` | `putaway_plan_id`；按实际成员条件携带 `exchange_sources`、`source_executions` | WMS 已持久化覆盖快照全部来源成员的完整计划 |
| `REJECT` | `reason_code` | 快照身份或业务状态不允许上架 |
| `WAIT` | `reason_code`、`retry_after_ms` | WMS 尚不能形成完整来源计划 |

`exchange_sources` 和 `source_executions` 均为条件数组：没有该类成员时省略，出现时必须非空，两类成员总数必须大于零。
每个 `source_execution` 必须含 `source_execution_id`、`pkg_id`、`rack_id`、`rack_slot_code`、`bin_id`、`bin_cell_id` 和
可达性/作业顺序约束。每个 `exchange_source` 只冻结来源满 Bin 的稳定身份、来源 `rack_id + rack_face + slot_id`、其覆盖的
占用 Cell 和 WMS 已确认的交换资格；禁止提前携带目标五层货架、目标空 Bin、目标储位或 `exchange_execution_id`。

### 9.2 完整覆盖和不可变性

WMS 只有在一个事务内持久化完整 `READY` 响应后才可返回成功。来源计划必须满足：

- `occupied_cells` 中的每个物理占用 Cell 恰好由一个 `exchange_source` 或一个 `source_execution` 覆盖；
- 不得遗漏、重复，也不得把同一 Bin同时放入交换与逐盘来源；
- `exchange_sources` 可以覆盖同一来源货架 A/B 两面的全部合格满 Bin，不受单次 Transport 两对上限约束；
- 计划无 revision、来源追加、取消或成员覆盖；任何来源变化都必须人工对账并形成新的物理快照后重新开始；
- WES不得根据本地阈值把逐盘成员升级为满箱交换。

这里冻结的是来源事实，不是执行批次。目标五层货架和实际空 Bin 必须在每次准备交换时根据 WMS 当前主账重新选择，避免用早期
静态目标占用库存或在前一批完成后继续执行已经失效的配对。

## 10. 满箱交换

### 10.1 下一交换批次决定（operation 和 DTO 待联合评审）

当前没有活动交换批次、上一批搬运最终结果与全部业务 Fact 已闭环、相关货架位置均明确时，WES 才可请求 WMS 计算下一批。本文先冻结
结果语义，不冻结 operation 字面量和完整 DTO；正式批准前不得实现或自行命名：

| 候选 `result` | 最小语义 |
| --- | --- |
| `READY` | 返回当前面 `1..2` 个精确交换对；每对含稳定 `exchange_execution_id`、来源满 Bin、目标空 Bin 和双方最终位置 |
| `TARGET_RACK_REPLACEMENT_REQUIRED` | 当前目标五层货架无法完整满足当前面；返回经 WMS 主账批准的换架准备要求，不直接创建 Transport |
| `WAIT` | 当前不存在能够完整满足该面需求的合格空 Bin 或目标货架；来源计划保持不变，以新消息身份重新求值 |
| `COMPLETED` | 来源计划中的全部 `exchange_sources` 已取得 WMS 已记录的业务终局 |

“空 Bin”必须同时具有明确 `right_bin_id` 和 `right_location`；没有 Bin 的空储位不能参与交换。WMS 按以下最小顺序选择目标：

1. 先满足库存资格、冷热区、锁定状态、尺寸/容量兼容和 RCS 可达性等 WMS 业务硬条件；
2. 在合格候选中选择能够完整覆盖当前面剩余需求的五层货架；
3. 若多个候选都满足，优先选择还能覆盖其它剩余面的同一货架，减少后续换架；
4. 当前面需要两对而现有目标货架只有一个合格空 Bin 时，禁止自动缩成一对；优先返回换架要求，没有合格替换货架则 `WAIT`。

`READY` 的一批只允许一个来源面和一个目标面：所有 Left Bin 必须属于同一来源 `rack_id + rack_face`，所有 Right Bin 必须属于
同一目标 `rack_id + rack_face`；左右的 A/B 字面量可以不同。WES 对全部 1～2 对只创建一个 `exchange_bins()` TransportTask，
WMS/RCS 必须原子接纳或整批拒绝，禁止截断、拆成部分接纳或跨面混合。

满箱交换成员不创建 `BinExecution`；其执行身份、物理搬运和主账迁移分别由 `exchange_execution_id`、一个
`TransportTask(BIN_EXCHANGE)` 和 movement report闭合。当前 CTU/RCS没有逐容器中间位置事件；搬运任务被接纳或提交结果未知后，
全部成员继续由活动 Transport资源围栏保护且当前位置按未知处理，直到 `transport.task.resulted@v1` 最终结果收敛；不得继续把交换前
位置当作当前事实。

两面都需要交换时，固定顺序为：当前面交换闭环 → WMS 重新计算 → 对下一面仍需使用的每个货架分别创建 `RACK_ROTATE` →
所有换面搬运最终结果成功且 `arrival_face` 正确 → 再请求下一批。需要更换目标五层货架时，必须先完成旧货架搬离和新货架可靠到位，再
计算下一批。后续 TransportTask 只能在前一步成功后创建，不能提前形成搬运提交或 RCS 义务。

### 10.2 `putaway.bin_exchange.movement_report@v1`

每个 Bin 可靠到达当前批次决定的最终位置后分别上报：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `putaway_plan_id`、`exchange_execution_id` | 是 | 不可变来源计划和当前批次交换对身份 |
| `bin_id` | 是 | 实际复核后的 Bin身份 |
| `movement_role` | 是 | `FULL_BIN_TO_STORAGE \| EMPTY_BIN_TO_SOURCE_RACK` |
| `from_position`、`to_position` | 是 | 当前批次决定中的稳定起终点 |
| `transport_task_id`、`transport_outcome_version`、`placed_at` | 是 | 唯一 `exchange_bins()` TransportTask 及其确定结果版本；必须支撑本成员最终位置 |

WMS 返回 `RECORDED` 或 `DUPLICATE`。只有本批搬运最终结果全部成员 `SUCCEEDED` 且位置明确、每个 movement report 都取得
`RECORDED/DUPLICATE`，并且 WMS 主账已经提交全部位置迁移后，该批次才闭环并允许重新计算。任一成员失败、
身份不符或结果 `UNKNOWN`，立即停交换并把受影响交换对及其实际接触对象冻结为最小人工恢复范围；不得自动补偿、反向搬回，
也不得把“另一对成功”解释为整批成功。交换过程中不发送 `SOURCE_PICKED`业务事实。

## 11. WorkLine、货架搬运和并发边界

### 11.1 插件切换和 `LineRunEpoch`

自动 WorkLine 可以配置 `automatic_putaway` 和 `automatic_picking`，但一个 `LineRunEpoch` 只能激活其中一个插件。只有该线无活动
`MaterialExecution`、`BinExecution`、`putaway_execution_id`、DeviceCommand、未闭合 TransportTask 和现场对象时才允许切换；
每次切换必须关闭旧 Epoch 并创建新的 `LineRunEpoch`。旧 Epoch 的证据、决定和回调不得驱动新 Epoch。

### 11.2 静态工位与 Transport

WES 只从活动 Epoch 已激活 `automatic_putaway`、设备健康且无活动上架执行的 WorkLine 中选择一条，并选择本地静态交换位、
来源工作位和缓存位；WMS 只
决定业务货架身份及最终库存位置。所有货架/CTU搬运必须使用 TransportTask，所有机械动作必须使用 DeviceCommand；业务 ACK
不得代替二者终局。

一条 WorkLine同一时刻只有一个活动 `putaway_execution_id`。来源单层货架可采用 A/B角色：一个 `ACTIVE`、一个 `STAGED`；
`STAGED`不得提前产生逐盘命令。货架从交换位到工作位的中间移动不新增 WMS业务 Fact。

## 12. 目标 Bin供给、退回和流量边界

### 12.1 `putaway.target_bin.supply_batch@v1`

WES只在投料缓存存在已预留空闲位、CTU背篓存在可用空间且当前 WorkLine允许接料时请求供给。请求给出物理上限，不指定
业务 Bin：

```json
{
  "operation_id": "0197f300-0000-7000-8000-000000000030",
  "operation": "putaway.target_bin.supply_batch@v1",
  "timestamp": 1786590600000,
  "data": {
    "putaway_execution_id": "PE-001",
    "workline_code": "AUTO-SORT-01",
    "line_run_epoch_id": "LRE-IN-010",
    "ingress_reserved_positions": ["INGRESS-01", "INGRESS-02"],
    "ctu_free_slots": 2,
    "max_bins": 2
  }
}
```

`max_bins`必须等于投料缓存已预留空闲位数与 CTU可用背篓位数的较小值；退料缓存已满或当前面已有可执行退回批次时，则必须为零且
禁止请求供给。仅有 FIFO 候选但当前面无合格空位时，可由新供给需求驱动换面或换架。

| `result` | 必填字段 | 含义 |
| --- | --- | --- |
| `READY` | `bins` | WMS返回不超过 `max_bins`的精确 Bin；每个 Bin至少有一个库存主账可分配空 Cell |
| `NO_BATCH` | `reason_code`、`retry_after_ms` | 当前快照无合格 Bin，不是系统故障 |
| `WAIT` | `reason_code`、`retry_after_ms` | WMS暂时不能可靠决定 |

每个 `bins[]`必须含 `bin_id`、`source_position`（五层货架 `rack_id+rack_face+slot_id`）、可用 Cell摘要以及业务约束。请求和响应
不包含 `target_bin_execution_id`或 `target_bin_batch_id`；批次身份就是请求 `operation_id`。WMS选择只依据其库存主账，不依据
WES缓存投影推断库存空闲。收到 `NO_BATCH` 后，新事实可提前唤醒；否则 WES等待 `retry_after_ms`到期，再以新的
`operation_id + previous_operation_id` 基于当前现场事实重新求值。

WES 持久化 `READY` 后，冻结每个精确 `bin_id`、WMS来源位置快照、当前 `putaway_execution_id`、`line_run_epoch_id` 和预留
`HANDOFF_POSITION`，再创建对应 `BIN_MOVE` TransportTask。提交、接纳、失败、位置未知和资源围栏均由 TransportTask负责；搬运最终
成功前不创建 `BinExecution`，也不得用来源快照声称当前位置确定。

`transport.task.resulted@v1` 最终结果确认 Bin成功到达冻结 `HANDOFF_POSITION`，且现场扫码身份与冻结 `bin_id` 一致后，WES才创建
唯一活动 `BinExecution`。缓存位、SCAN点和正常/NG路线由位置投影与证据表达。入线后不允许自动取消、返回原位或创建替代搬运，
物理执行只允许以正常回库或整线`NGZone`人工接管闭合。

### 12.2 `putaway.target_bin.movement_report@v1`

目标 Bin只在两个稳定端点上报 WMS业务位置事实。当前 CTU/RCS 只能返回包含全部成员最终位置的
`transport.task.resulted@v1`，不提供可靠的 `transport.task.member_position_changed@v1` 逐容器中间位置事件。WES不得等待、推断或
伪造 `SOURCE_PICKED/TARGET_PLACED`；Transport 被接纳到最终结果到达之间由 TransportTask保持资源围栏，且当前位置按未知处理：

| `movement_kind` | 上报时机 | 必填位置 | 后续门禁 |
| --- | --- | --- | --- |
| `SUPPLY_PLACED` | 搬运最终结果确认 Bin已到投料缓存，实际扫码身份与冻结 `bin_id` 匹配，WES已创建 `bin_execution_id` | 实际 `HANDOFF_POSITION`投料缓存位 | WMS记录后才允许进入 SCAN1 |
| `RETURN_PLACED` | Bin已由 CTU可靠放入 WMS指定五层货架槽位 | 最终 `FIVE_LAYER_BIN_SLOT` | WMS记录后关闭该 Bin外部确认义务 |

共同字段为 `putaway_execution_id`、`bin_execution_id`、`bin_id`、`movement_kind`、`from_position`、`to_position`、
`transport_task_id`和 `placed_at`。WMS返回 `RECORDED`或 `DUPLICATE`。Transport接纳、取走、在途和缓存间移动不产生
`SOURCE_PICKED`或其他 WMS业务 Fact，也不能把 ACK 当作位置证据。

若实际扫码 Bin与供给响应不一致，WES不得创建替代业务身份或继续 SCAN1；保留 `expected_bin_id + actual_bin_id` 和 Transport 证据，
将实际 Bin 冻结在当前安全位置，等待独立恢复 wire 获批。现有 `putaway.target_bin.return_batch@v1` 只处理已经进入正常退料 FIFO 的 Bin，
不能授权这次异常位置迁移；预期 Bin 保持未完成。

### 12.3 投料与退料批次算法

供给和退回是两个独立请求，不共享批次计数：

- 投料数量上限：`min(投料缓存已预留空闲位, CTU可用背篓位, WMS返回的可用五层货架 Bin数)`；
- 退料数量上限：`min(当前 putaway_execution_id 退料 FIFO 中已实际到位 Bin数, CTU可用背篓位, WMS返回的当前面合格空位数)`；
- “缓存可用”必须来自 WES位置预留或可靠到位事实，不能用 PLC瞬时计数直接覆盖本地投影；
- 一次请求的成员与目标响应持久化后不可在重试时增删。

### 12.4 背压

退料缓存满时停止把新 Bin 供给到线上；仍允许已接收的新供给需求驱动货架换面或换架，以及执行能够确定释放退料容量的已冻结搬运。当前面有可执行退回批次时优先退回。投料缓存无预留空闲位时也不得请求供给。当前合同不引入高低水位、自适应吞吐
或预测算法。现场需要更复杂节拍时，必须基于实测数据另行审批，不能由插件私自添加第二套规则。

## 13. SCAN1—SCAN4 路由

### 13.1 `putaway.target_bin.route_decide@v1`（SCAN1）

实际 Bin的 `SUPPLY_PLACED`已被 WMS记录并到达 SCAN1后，WES提交：`putaway_execution_id`、`bin_execution_id`、`bin_id`、
`scan1_evidence_id`、`workline_code`、`line_run_epoch_id`和当前本地位置。WMS返回严格联合：

| `result` | 必填字段 | WES动作 |
| --- | --- | --- |
| `ENTER_PRODUCTION` | `route_decision_id` | 进入当前 WorkLine的 SCAN2 |
| `NO_PRODUCTION_TASK` | `route_decision_id` | 不标记 NG，进入 SCAN3；由当前 WorkLine负责正常退回 |
| `MARK_NG` | `route_decision_id`、`ng_reason_code` | 持久化 NG处置，进入 SCAN3 |
| `WAIT` | `reason_code`、`retry_after_ms` | 停留 SCAN1，以新 operation重求值 |

WMS独占“是否有生产任务”和“是否标记 NG”的判断。WES不得把 `NO_PRODUCTION_TASK`解释为 NG，也不得根据本地 Usage阈值改写
结果。已在上游 WorkLine持久化为 NG且路由到下游的 Bin，到达下游 SCAN1时直接按既有 NG处置通过，不再次请求 WMS。

### 13.2 `putaway.target_bin.work_admission_decide@v1`（SCAN2）

只有 SCAN1得到 `ENTER_PRODUCTION`的 Bin才调用。请求含 `bin_execution_id`、`bin_id`、`route_decision_id`、
`scan2_evidence_id`、当前 WorkLine和本地位置。

| `result` | 含义 | WES动作 |
| --- | --- | --- |
| `AVAILABLE` | 当前实际 Bin至少有一个 WMS授权的可分配 Cell | 停留为本线可用目标 Bin |
| `PASS_THROUGH` | 当前 Bin没有可用 Cell | 放行进入 SCAN3，不标记 NG |
| `WAIT` | WMS暂不能可靠判断 | 停留 SCAN2，以新 operation重求值 |

SCAN2只判断当前 Bin有无可用料格，不分配某盘物料的精确 Cell；精确 `bin_cell_id`只能由 §14.1逐盘决定晚绑定。

### 13.3 SCAN3和 SCAN4

SCAN3不调用 WMS，只按已持久化的处置分流：

- Bin已标记 NG：进入下一条 WorkLine的 SCAN1；如果当前已是最后一条线，则进入 `BIN_NG_EXIT`；
- `NO_PRODUCTION_TASK`、`PASS_THROUGH`或正常清退：进入当前 WorkLine的 SCAN4；
- 处置缺失或证据不确定：停线，禁止猜测路径。

SCAN4是当前 WorkLine正常退回的物理门禁，不调用 WMS。通过后进入当前 WorkLine退料缓存，等待 CTU退回五层货架。由此，
SCAN1负责生产准入/NG业务判断，SCAN2负责可用性，SCAN3负责 NG/正常物理分支，SCAN4负责正常退料缓存入口；四者职责不得合并。

## 14. 逐盘上架

### 14.1 `putaway.material.decide@v1`

来源料盘只有满足以下门禁才能取盘：

- 至少一个目标 Bin的 `SUPPLY_PLACED`已被 WMS记录；
- 该 Bin经 SCAN1 `ENTER_PRODUCTION`、SCAN2 `AVAILABLE`，且未进入 `RETURN`或 `WAIT`清退状态；
- 来源单层货架在可靠工作位，来源成员属于当前不可变来源计划；
- 机械臂、扫码平台和目标 Bin均无结果未知的命令。

料盘到扫码平台后必须复扫六合一码。WES提交：`putaway_plan_id`、`putaway_execution_id`、`source_execution_id`、
`material_execution_id`、计划 `pkg_id`、复扫 `scan_evidence_id`、实际编码、来源位置，以及当前所有可用目标 Bin及其实际
`bin_execution_id`。

| `result` | 必填字段 | 含义 |
| --- | --- | --- |
| `ACCEPT` | `target_assignment_id`、`target_position`、`placement_sequence`、`expected_height_mm` | WMS晚绑定精确 `bin_id+bin_cell_id` |
| `REJECT` | `reason_code`、`ng_destination` | 料盘进入上架 NG交接 |
| `WAIT` | `reason_code`、`retry_after_ms` | 料盘停留安全位，不得自行选 Cell |

复扫编码与计划 `pkg_id`不匹配不是自动 NG；WES必须冻结来源 Cell、实际料盘和扫码平台，进入人工对账。WMS不得返回未在请求
可用集合中的 Bin，也不得返回已被其他未闭合目标占用的 Cell。

### 14.2 `putaway.material.target_recovery_decide@v1`

PUT前发现已分配 Cell物理不可执行时，使用原 `source_execution_id`、`pkg_id`、失败 `target_assignment_id`、失败证据和当前仍可用
目标集合请求恢复。响应严格为 `REASSIGNED | REJECT | WAIT`：`REASSIGNED`必须返回新的 `target_assignment_id`、
`target_position`、`placement_sequence`和 `expected_height_mm`；`REJECT`必须返回 `reason_code + ng_destination`，`WAIT`
必须返回 `reason_code + retry_after_ms`。进入不可逆 PUT后禁止恢复改址，必须停机对账。

### 14.3 `putaway.material.placement_report@v1`

```json
{
  "operation_id": "0197f300-0000-7000-8000-000000000040",
  "operation": "putaway.material.placement_report@v1",
  "timestamp": 1786591800000,
  "data": {
    "putaway_plan_id": "PP-001",
    "putaway_execution_id": "PE-001",
    "source_execution_id": "SE-WMS-001",
    "material_execution_id": "ME-PUT-001",
    "pkg_id": "PKG-9001",
    "target_assignment_id": "TA-PUT-001",
    "from_position": {"type": "ONE_LAYER_BIN_CELL", "rack_id": "SR-01", "rack_slot_code": "A", "bin_id": "BIN-010", "bin_cell_id": "C03"},
    "to_position": {"type": "BIN_CELL", "bin_id": "BIN-200", "bin_cell_id": "C05"},
    "placement_sequence": 3,
    "command_code": "CMD-PUT-003",
    "placed_at": 1786591798000
  }
}
```

WMS必须在同一事务中验证来源成员、目标预留和序号，把 `pkg_id`从来源 Cell原子迁移到目标 Bin/Cell，再返回 `RECORDED`。
该 Fact是位置迁移，不是再次入库确认；GRN和获批粗分合同形成的原准入身份不得重建。`DUPLICATE`可关闭义务，冲突必须对账。

### 14.4 `putaway.material.ng_placement_report@v1`

WMS `REJECT`后，只有目标 NG区被 ECS/PLC确认 `READY`且料盘可靠到位才上报。字段含计划/执行/来源身份、`pkg_id`、
`ng_evidence_id`、`ng_position`、WMS原因和固定 `business_context=AUTOMATIC_PUTAWAY`。

WMS返回 `RECORDED`或 `DUPLICATE`后，该来源成员结束；后续人工处置由 WMS负责，不再通知 WES。若 PLC返回 `OCCUPIED`，
WES等待；返回 `UNKNOWN`则停机，不能把“已发命令”当作 NG到位。

### 14.5 `putaway.source.empty_decide@v1`

来源 Cell取盘动作得到可靠“无料”结果时，WES保存 `source_observation_id`并提交计划来源身份、预期 `pkg_id`、来源位置和设备证据。

| `result` | WES动作 |
| --- | --- |
| `RETRY` | 使用新的 DeviceCommand复检；不得复用已终局命令 |
| `SOURCE_ABSENT` | WMS原子处理来源缺失并关闭该静态成员，WES不再取盘 |
| `WAIT` | 保持来源冻结，以新 operation重求值 |

设备结果未知不属于“空取”，禁止调用本 operation；应按 §17进入 UNKNOWN等待或人工对账。

## 15. 目标 Bin清退、退回和 NG出口

### 15.1 `putaway.target_bin.clearance_decide@v1`

每个料盘 placement Fact被 WMS记录后，以及不可变来源全部闭合时，WES可针对当前 Bin提交 `bin_execution_id`、`bin_id`、
已记录的最后 `placement_sequence`、本地占用观察和触发原因。

| `result` | WES动作 |
| --- | --- |
| `KEEP` | Bin继续停留为当前线可用目标 |
| `RETURN` | 退出工作位，经 SCAN3/SCAN4进入当前线退料缓存；最终货架槽位尚未分配 |
| `WAIT` | 保持安全位置，以新 operation重求值 |

WMS根据库存主账、剩余可用 Cell和业务策略决定清退；WES不得按本地 Usage阈值替代决定。`RETURN`只授权离开生产位，不携带
五层货架目标。

### 15.2 `putaway.target_bin.return_batch@v1`

退料缓存已有实际到位 Bin且 CTU有可用背篓位时请求：

| 请求字段 | 约束 |
| --- | --- |
| `workline_code`、`line_run_epoch_id` | 当前 WorkLine 和活动 Epoch；不新增 WMS 业务任务键 |
| `putaway_execution_id` | 当前上架执行；退料 FIFO 不跨执行共享 |
| `rack_id`、`rack_face` | 已确认到达、当前准备承接退料 Bin 的五层货架和实际面 |
| `return_buffer_bins` | 当前 `putaway_execution_id` 退料 FIFO 的连续队首，仅含已实际到位的 `bin_execution_id+bin_id+buffer_position` |
| `ctu_free_slots`、`max_bins` | `max_bins=min(实际候选数, CTU空位数)` |

| `result` | 必填字段 | 含义 |
| --- | --- | --- |
| `READY` | `returns` | 每个成员含精确 `bin_id`和请求当前面的目标 `rack_id+rack_face+slot_id` |
| `NO_BATCH` | `reason_code`、`retry_after_ms` | WMS 本次不能为 FIFO 队首分配当前面合格空位 |
| `WAIT` | `reason_code`、`retry_after_ms` | 暂不能可靠分配 |

`READY`成员必须是请求 FIFO 列表的连续前缀，不得超过请求候选或包含重复成员；每个目标必须位于请求的 `rack_id + rack_face`，但不要求是 Bin 的原货架、原面或原储位。响应一经持久化不可换成员或换目标。到位后逐 Bin调用 §12.2的
`RETURN_PLACED`。

正常 Bin 只有在 `transport.task.resulted@v1` 最终结果确认成员位置等于冻结目标时才证明可靠回库；WMS 对 `RETURN_PLACED` 返回
`RECORDED | DUPLICATE` 后，WES才关闭该 `BinExecution`并释放管辖权。最终结果 `FAILED` 且位置明确时，保留活动
`BinExecution`并按该位置进入人工恢复；结果超时或成员位置未知时由 TransportTask保持 `RECONCILING`。两种情况都不得自动回原位、
改址或关闭执行。

当前面没有合格空位属于正常 `NO_BATCH`，不是 NG 或 `STATE_CONFLICT`。正常运行时退料 Bin 留在 FIFO，不为自己触发换面或换架；新目标 Bin 供给需求可以驱动货架切换，新面到位后重新评估 FIFO。停止或切换已请求时，目标合同允许 WMS 为排空当前 `putaway_execution_id` 的既有 FIFO 选择货架面；但候选 `workline.return_buffer.drain_rack_decide@v1` 尚未获批。在其严格 DTO、完整货架切换目标和幂等 fixture 冻结前，该路径保持 `ReviewRequired/BLOCKED`：WES 停止新任务和新 Bin，Epoch 保持 `ACTIVE`，不创建货架切换或退料 Transport。
收到 `NO_BATCH` 后，新货架面到位或新事实可提前唤醒；否则 WES等待 `retry_after_ms`到期，再以新的 `operation_id + previous_operation_id` 基于仍在退料缓存的当前候选重新求值。

### 15.3 NG Bin跨线规则

`MARK_NG`的 Bin沿 SCAN3进入下一条 WorkLine SCAN1，既有 NG处置跨线保持不变，不再次请求业务判断；下游只负责物理放行。
正常 `NO_PRODUCTION_TASK`或 `PASS_THROUGH`不得跨线冒充 NG，而应进入当前线 SCAN4和退料缓存。

### 15.4 `putaway.target_bin.ng_exit_report@v1`

最后一条 WorkLine的 NG Bin可靠到达整线 `NGZone`的 `BIN_NG_EXIT`后，上报 `bin_execution_id`、`bin_id`、原 `route_decision_id`、
`ng_evidence_id`、`business_context=AUTOMATIC_PUTAWAY`、NG原因和出口位置。WMS返回 `RECORDED`或 `DUPLICATE`。

此后 Bin仍保留在 WES位置投影，直到操作员在整线 `NGZone`扫码并实际取出；该本地事件即可关闭 `BinExecution`，无需等待 WMS
对账结果。下次该 Bin重新进入系统时，必须从 WMS获取最新主账信息，禁止复用旧执行快照。

## 16. 上架完成、来源货架清场和对账

### 16.1 `putaway.execution.completion_confirm@v1`

WES 只有在来源计划每个 `exchange_source` 都由已闭环交换批次覆盖、每个 `source_execution_id` 均有已记录终局、所有目标分配
Fact 已闭合，且不存在业务位置冲突时请求完成。请求只提交 `putaway_plan_id`、`putaway_execution_id` 和本地门禁摘要，不提交
可被篡改的成员清单。

| `result` | 必填字段 | 处理 |
| --- | --- | --- |
| `COMPLETED` | `completed_at` | WMS已根据权威事实完成业务计划 |
| `NOT_COMPLETED` | `reason_code`、`open_obligations` | WES保留活动执行并按明确义务处理 |

WMS 必须依据其持久化的不可变来源计划、逐批决定和事实裁决，不能信任 WES 汇总。业务 `COMPLETED` 不等待目标 Bin 退回、NG Bin 人工取出或空来源
货架搬走；这些属于物理清理门禁。

### 16.2 `putaway.source_rack.clearance_decide@v1`

来源单层货架已无业务成员、位置可确认且无活动设备命令时，WES提交货架身份、当前工作位、计划身份和本地空架观察。

| `result` | 必填字段 | 含义 |
| --- | --- | --- |
| `CLEAR_TO_DESTINATION` | `destination` | WMS指定最终货架位置，WES创建 TransportTask |
| `HOLD` | `reason_code` | 保持现位，不创建搬运 |
| `REJECT` | `reason_code` | 状态冲突，进入人工处理 |
| `WAIT` | `reason_code`、`retry_after_ms` | WMS暂不能决定 |

只有 Transport确定成功且所有本地清理对象闭合，WorkLine才满足释放门禁并允许切模式或接纳下一执行。排空阶段不接纳下一执行；全部清场义务闭合后才关闭 Epoch。

### 16.3 `putaway.execution.reconciliation_decided@v1`

上架多对象出现身份、位置或不可逆动作冲突时，由 WMS人工核对后发送 `reconciliation_id`、`putaway_plan_id`、受影响执行身份、
每个 `pkg_id/bin_id/rack_id`的权威位置、`decision=CONTINUE|ABORT`和原因。

WES只修正后续业务投影和准入门禁，不改写 DeviceCommand或 TransportTask历史终局。`ABORT`停止未来动作，但不删除已从原位取出的
料盘或现场 Bin；这些实物必须保持冻结，直至有明确目标、NG或人工取出证据。

## 17. 失败、重试和对账

### 17.1 通用重试表

| 场景 | 身份规则 | WES动作 |
| --- | --- | --- |
| 网络超时、`429`、`503` | 原 `operation_id`、原 payload、原 `timestamp` | 退避重试；不得生成新业务求值 |
| `200 / DECIDED` 且 `data.result=WAIT` | 新 `operation_id`，设置 `previous_operation_id` | 等待 `retry_after_ms`后基于同一现场证据重求值 |
| `200 / DECIDED` 且 `data.result=NO_BATCH` | 新 `operation_id`，设置 `previous_operation_id` | 新事实可提前唤醒；否则等待 `retry_after_ms`后基于当前现场事实重求值 |
| `409 / CONFLICT` | 禁止改字段重试 | 冻结最小受影响范围并对账 |
| DeviceCommand结果未知 | 保持原 `command_code`等待匹配回调或进入人工核验 | 禁止重发等价动作、假定失败或自创版本查询语义 |
| Transport `UNKNOWN` | 保持原 `transport_task_id`，等待或消费后续权威证据发布的更高 `outcome_version` | 禁止创建重复搬运 |
| Transport `FAILED/REJECTED` | 原任务保持终局；恢复若获人工批准使用新 `client_request_id` | 进入人工恢复，不定义自动替代 operation |
| Fact `RECORDED/DUPLICATE` | 原 Fact身份已闭合 | 不再发送新身份的同义事实 |

### 17.2 不可逆动作边界

料盘或 Bin从原位置被取出后，不得把“停止未来动作”理解为可以回滚数据库或自动放回原位。WES必须把已经取出的实物可靠闭合到
批准目标、NG交接区或人工处理区。任何无法确认是否已取出、是否已放入、或实际身份不明的情况，都属于物理 `UNKNOWN`，必须
停机保留原始证据。

### 17.3 对账最小充分证据

对账包至少包含 operation及首次响应、InboundEvidence原文、DeviceCommand所有版本、TransportTask所有 outcome版本、当前
`LineRunEpoch`、位置投影、人工扫码记录以及受影响来源计划和交换批次成员。不得只发送错误摘要，也不得用对账结果删除历史证据。

## 18. 联调验收清单

| 场景 | 预期结果 |
| --- | --- |
| 上架来源计划存在遗漏或重复成员 | WES拒绝 `READY`计划，不创建动作 |
| 当前面需要两对但目标货架只有一个合格空 Bin | 不缩成一对；优先返回换架要求，没有合格替换货架则 `WAIT` |
| A/B 两面都存在合格满 Bin | 当前面批次全部业务闭环后才换面并重新求值；不得预建下一面 TransportTask |
| 两对交换混入不同 Left 面或不同 Right 面 | 整条搬运提交确定拒绝，不创建部分任务 |
| 两对满箱交换仅一对成功 | 整批不成功，停止交换并冻结最小安全范围等待人工恢复 |
| 投料缓存只有一空位、CTU有四空位 | `max_bins=1`，不得超发 |
| WMS返回精确供给 Bin | WES冻结供给与交接位并创建 BIN_MOVE TransportTask；不提前创建 BinExecution |
| BIN_MOVE已接纳但尚无最终结果 | 由 TransportTask保持资源围栏且位置未知；不得继续显示来源货架位置或创建替代搬运 |
| BIN_MOVE最终成功且实扫身份匹配 | WES创建唯一活动 BinExecution，再报告 `SUPPLY_PLACED`并进入工作线 |
| 当前面有可执行退回批次 | 优先消耗当前 `putaway_execution_id` 的 FIFO；不启用未定义水位算法 |
| 当前面无合格退料空位 | WMS返回 `NO_BATCH`，Bin留在 FIFO；不转 NG、不返回 `STATE_CONFLICT`，新供给需求可驱动换面或换架 |
| 退料缓存满 | 停止新 Bin 供给；仍允许能够释放 FIFO 容量的货架切换或已冻结搬运 |
| 供给响应 Bin与投料口实扫不一致 | 不创建替代业务身份，不进 SCAN1，冻结对账 |
| SCAN1无生产任务 | 返回 `NO_PRODUCTION_TASK`，不标 NG，经 SCAN3/SCAN4进入本线退料缓存 |
| SCAN1标记 NG | 下游 SCAN1不再次问 WMS；最终进入 `BIN_NG_EXIT` |
| SCAN2无可用 Cell | `PASS_THROUGH`，不标 NG，不在 SCAN2分配虚假 Cell |
| 来源盘复扫不一致 | 冻结来源、料盘和平台；不得自动送 NG |
| 逐盘 placement成功 | WMS原子迁移 `pkg_id`位置，不再次做 GRN入库确认 |
| 料盘 NG区占用/未知 | `OCCUPIED`等待，`UNKNOWN`停机；不得提前上报 NG Fact |
| Material NG Fact被记录 | 该成员结束，不等待 WMS后续人工处理回调 |
| NG Bin到整线NGZone但未人工取走 | WMS NG Fact可闭合，WES仍保留本地位置；人工扫码取走后关闭 BinExecution |
| Fact响应丢失 | 原 operation重试，WMS返回 `DUPLICATE`，不得生成同义新 Fact |
| DeviceCommand或 Transport未知 | 等待或消费同一对象后续发布的更高权威版本，不重发等价物理动作 |
| WMS业务完成但仍有物理清理 | 计划可 `COMPLETED`，但 WorkLine 尚未满足释放门禁 |
| 切换自动上架/自动拣货插件时仍有活动对象 | 切换被拒绝，不关闭旧 Epoch，也不生成新 `LineRunEpoch` |

## 19. 实施与验收所有权

| 层次 | 实现所有者 | 验收边界 |
| --- | --- | --- |
| 本文 WMS ACL DTO与路由 | `src/app/wms_adapter/` | 当前已具名的 23 个 operation，以及批准后补齐的下一交换批次 operation 的严格 schema、幂等、冲突和失败响应 |
| 可靠执行基础 | WES核心 | MaterialExecution、BinExecution、PositionProjection、InboundEvidence、LineRunEpoch的不变量；不验证具体工作线业务 |
| 满箱交换与自动上架编排 | 独立自动上架插件 | §9—§16的计划、交换、SCAN和逐盘上架；测试随插件交付 |
| TransportTask | Transport基础能力 | 可靠提交、结果版本和 UNKNOWN；不证明库存业务正确 |
| DeviceCommand和统一设备 wire | DeviceCommand基础与供应商 ECS | 命令幂等、回调版本、物理安全和原始码映射；供应商验收不进入 WES核心测试 |
| WMS库存事务 | WMS | GRN绑定、目标预留、位置原子迁移、Bin/Cell可用性、完成裁决和人工对账 |
| 现场整线验收 | WMS、WES、RCS、ECS联合 | 实物身份、缓存容量、CTU节拍、机械臂动作、四扫描点和人工恢复 |

架构基础能力验收不得替代业务插件验收；业务插件也不得通过复制 Transport或 DeviceCommand内部逻辑来证明自身闭环。

## 20. 正式实施前确认项

下表是实施授权记录，不是完成情况说明。责任方必须提供可追溯的审批人、版本和证据位置；不得由 WES 单方把 `PENDING` 改为
`APPROVED`，也不得用代码或测试结果替代跨系统业务授权。

| ID | 确认项 | 主要责任方 | 状态 | 冻结版本/审批证据 |
| --- | --- | --- | --- | --- |
| C1 | WMS、WES、RCS 和 ECS 联合审批本文，状态由 `ReviewRequired` 变为 `Approved` | 联合 | PENDING | 未提供 |
| C2 | 冻结下一交换批次 operation 字面量和完整 DTO；联合确认全部 operation 的严格 JSON Schema、共享正反 fixture 和字段大小限制 | WMS、WES | PENDING | 未提供 |
| C3 | 确认本文逐盘上架只迁移获批粗分入库事实的位置，不重复 GRN 绑定或入库确认 | WMS | PENDING | 未提供 |
| C4 | 确认 Cell 容量、兼容性、Usage、空箱资格、生产准入、NG 和清退规则均以 WMS 主账决定为唯一权威 | WMS | PENDING | 未提供 |
| C5 | 确认自动上架机械臂不可逆点、NG 区状态和 SCAN1—SCAN4 物理含义，并形成各设备获批合同附录 | ECS、WES | PENDING | 已确认 Bin离架后不可回退、只有正常回库或整线NGZone人工处理、CTU/RCS只返回最终到位结果；机械臂与SCAN物理合同仍未提供 |
| C6 | 确认 CTU 背篓容量、投料/退料缓存可观测状态和货架交换位 Transport 边界 | RCS、WMS | PENDING | 未提供 |
| C7 | 联合冻结超时、退避、`retry_after_ms` 上下限、人工停线恢复 SOP 和对账证据导出格式 | 联合 | PENDING | 未提供 |
| C8 | 在业务插件包内完成 §18 场景验收；WES 核心仓库只验收共享合同和可靠性不变量 | WES、交付 | BLOCKED | 依赖 C1—C7 获批后实施 |
| C9 | 现场验证两对交换的“非同批全成功即停线”策略不会把人员或机械臂置于危险状态 | ECS、RCS、现场 | PENDING | 未提供 |
| C10 | 审批 SRS、最小执行架构和主计划当前真源同步，清除库存阈值或三点 SCAN 等过期表述 | 联合 | PENDING | 未提供 |
| C11 | 冻结 `workline.return_buffer.drain_rack_decide@v1` 的 operation 字面量、严格 DTO、当前 `putaway_execution_id`、旧架离场去向、新架可靠来源/工作位/到达面、目标 rack/face 原子绑定与非空 FIFO 前缀容量保留、`WAIT` 和幂等 fixture | WMS、WES、RCS | BLOCKED | 未提供 |

只有 C1—C11 全部为 `APPROVED`，本文才构成代码实施授权。
