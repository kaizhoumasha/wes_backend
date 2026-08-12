---
title: WES 出库操作顶层设计
status: Approved
created_at: 2026-08-06
updated_at: 2026-08-11
scope: SMT 自动出库 PickingTask、异步启动、事实驱动 CTU 批次、Bin 级晚绑定、退料优先取料、FIFO 缓存与并行搬运
system_stage: pre_release
migration_strategy: direct_replacement
related:
  - docs/architecture/SRS.md
  - docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
  - docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/contracts/wms-async-callback-envelope-contract.md
  - docs/contracts/transport-fulfillment-contract.md
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/architecture/authority-matrix.md
  - docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
  - docs/hardware/wms_rcs_interface_requirements.md
---

# WES 出库操作顶层设计

## 1. 文档定位

本文定义 SMT 自动出库的当前业务合同，说明 PickingTask 如何排队、启动、取得 WMS 资源决定并驱动物理执行。WMS 负责库存、
来源和目标分配；WES 负责工作线准入、可靠执行和现场证据。出库单、波次和库存策略不进入 WES。

权威关系如下：

- `docs/architecture/SRS.md` 记录产品需求和参与方职责。
- 最小执行架构 SPEC 定义可靠对象、位置投影和扩展边界。
- 本文定义自动出库对象、状态、不变量和验收场景。
- `docs/contracts/wms-outbound-picking-task-integration-requirements.md` 定义自动出库 operation、method、path、wire DTO、错误和
  幂等规则；WMS 北向通用合同只定义 `WmsClient` 的 HTTP/JSON 访问边界。
- Master Plan 和实施计划只规定实现顺序，不能改变本文裁决。
- `docs/hardware/` 保存厂商原始输入，不是当前业务或 wire 真源。

系统尚未发布。本文只描述当前目标设计，不提供别名、fallback、双读、双写或迁移兼容。

## 2. 设计裁决

- WMS 只向 WES 发布可排队的 `PickingTask`。任务发布不分配货架、Bin、Cell 或目标储位。
- WES 从自动出库任务池选择当前最高优先级的可执行任务和一条就绪分拣机工作线，请求 WMS 分配资源。WMS 先返回接收 ACK，
  再异步返回 `START_GRANTED | START_WAIT`。
- WMS 在 `START_GRANTED` 前完成来源运算和业务锁定，包括五层货架、候选 Bin、退料货架 SLOT、目标转运货架和目标窗口；
  此时不生成货架动作、清场去向或 CTU 入站/退箱批次。
- WMS 持有自动出库的全部业务事实。来源货架实际到位后，WES 提交到达面和已预留入料位置，WMS 才冻结一个不可拆分的
  CTU 入站 moves；正常 Bin 实际到达 `RETURN_BUFFER` 后，WMS 才按实际候选、背篓容量和当前货架面空储位冻结退箱 moves。
- WES 只因本地工作位、缓存或设备门禁延迟执行，不拆分、合并或改写 WMS 决定，也不自行选择退箱目标储位。
- 来源货架在任务授权期间由当前工作线独占。退料货架按货架独占，所选 SLOT 按执行事实逐项释放。
- PickingTask 的业务成员是 `DirectPickExecution` 和 `BinWorkExecution`。Cell 只有在实际 Bin 到达 SCAN2 后才创建。
- CTU 可以乱序投箱。WES 按实际到达的 Bin 请求 WMS 返回 Cell 工作计划，不依赖预期投箱顺序。
- `WORK_BUFFER` 单向 FIFO。进入后不能绕行，队首 Bin 必须得到明确的工作计划、`NO_WORK` 或 NG 结果。
- 退料货架中的物料只用于满足当前 PickingTask。退料直接取料优先调度，但不是料箱拣料的全局串行屏障。
- WMS 维护转运货架容量、规格兼容、目标货架和目标面分配。WES 不计算 7、13、15 寸容量，也不根据本地计数决定换面或换架。
- WMS 在允许来源执行前完成目标容量预留。WES 只验证 WMS 分配的目标货架和货架面是否可靠到位。
- 料盘扫码后，WMS 返回业务资格和精确目标 SLOT；需要换面或换架时，同一 `ACCEPT` 还返回完整目标准备方案。WES 不自行
  选择目标储位或推导货架动作。
- 同一工作线同一时刻只允许一张任务处于 `STARTING | EXECUTING`；不同就绪工作线可以按任务池优先序并行执行不同任务。
- WMS 手工操作通过 `queue_changed` 调整 `dispatch_sequence` 或 `not_before`。WES 不提供独立人工启动入口。
- PickingTask 完成不等待货架离位、Bin 退回、Transport 清场或工作线释放。

## 3. 范围与非目标

### 3.1 范围

- PickingTask 发布、队列更新、本地准入、异步启动决定和完成报告。
- 多个五层货架、候选 Bin、多个退料货架及货架面的来源执行。
- `DirectPickExecution`、`BinWorkExecution`、`CellExecution` 和逐盘 `MaterialExecution`。
- CTU 乱序投箱、三段缓存、单向 FIFO 和基于位置事实的背压。
- 退料优先取料、Bin 工作位 SCAN2、Cell 计划和逐盘晚绑定。
- 转运货架换面、换架、精确目标 SLOT 和可靠位置事实。
- 料盘、Cell、Bin 三类 NG、来源恢复、空取和未知结果。

### 3.2 非目标

- WES 订单管理、波次计算、库存选择、库存锁策略或跨任务来源仲裁。
- WES 计算转运货架容量、尺寸兼容或替代目标货架。
- WES 规划 AGV、CTU 路线或 PLC 内部动作。
- 用 PickingTask 状态承载 Rack、Bin、Transport、设备命令或现场清场状态。
- 跨任务合并、全局优化器、通用补偿器或兼容旧 wire。

## 4. 权威边界

| 参与方 | 唯一权威 | 不承担 |
| --- | --- | --- |
| WMS | PickingTask、业务总序、库存、来源分配、来源锁、转运货架容量、目标分配、物料资格、原子搬运批次和需求状态 | 设备动作、滚筒线位置和本地安全互锁 |
| WES | 队列准入、执行对象、现场证据、资源仲裁、位置投影、NG 物理作用域和可靠外部义务 | 库存重算、来源替换、容量计算和目标储位选择 |
| RCS/AGV/CTU | 货架与 Bin 搬运、路径、排队和运输终态 | PickingTask、库存和物料资格 |
| ECS/PLC/设备 | 扫码、抓取、放置、输送、到离位、安全互锁和设备终态 | 任务顺序、库存和目标业务分配 |

业务操作由 WMS 发起或执行。WES 可以基于本地准入发起已批准的 WMS 资源请求、决定请求和事实报告，但不在本地创造业务
优先级、库存分配或人工业务操作。

## 5. 执行对象

| 对象 | 职责 | 不参与 |
| --- | --- | --- |
| `PickingTask` | 聚合已接纳的直接取料成员和 Bin 工作成员 | Rack、物理 Bin、Transport、设备和清场状态 |
| `DirectPickExecution` | 执行一个 WMS 指定的退料货架 SLOT，来源为 `RACK_SLOT` | 货架搬运生命周期和其他 SLOT 库存 |
| `BinWorkExecution` | 表达一个冻结候选 Bin 的业务工作，接纳 SCAN2 后的 Cell 计划并聚合 Cell | 滚筒线位置、退箱和 CTU 终态 |
| `CellExecution` | 管理一个 `BIN_CELL` 的逐盘循环和完成结果 | Bin 搬运和目标架运输 |
| `MaterialExecution` | 保存不可变扫码快照，跟踪单盘动作和位置事实 | 来源选择、任务聚合和容量计算 |
| `BinExecution` | 跟踪物理 Bin 在 SCAN1、FIFO、SCAN2、SCAN3 和 NG 路径中的位置 | PickingTask 业务完成 |
| `TransportTask` | 跟踪货架或 Bin 搬运、ACK、异步终态和未知结果 | PickingTask 和物料业务状态 |
| `DeviceCommand` | 跟踪一次设备命令、ACK、结果、deadline 和幂等事实 | WMS 业务决定 |
| `WmsConfirmation` | 可靠提交位置变化和任务完成事实 | 重放设备动作或修改执行对象 |
| `PositionProjection` | 表达具体物理位置的占用、预留、在途和 unknown | WMS 库存和转运货架容量主账 |

`BinWorkExecution` 与 `BinExecution` 通过稳定 `bin_id` 和各自执行 ID 关联。前者回答该 Bin 的业务取料是否完成，后者回答
物理 Bin 在哪里。两者不能共用状态机。

## 6. PickingTask 发布与队列

`PickingTaskIssued` 只携带任务身份和排队信息：

| 字段 | 含义 |
| --- | --- |
| `task_id` | 稳定且不可变的 PickingTask 身份 |
| `dispatch_sequence` | WMS 提供的自动出库任务池业务优先序 |
| `not_before` | 可选最早启动时间 |

发布阶段禁止携带：

- `workline_code`、`station_code` 或其他具体执行线字段。
- 来源货架、候选 Bin、Cell 或退料 SLOT。
- `PkgID`、六合一码、料盘数量或料盘顺序。
- 目标转运货架、目标面、目标 SLOT 或容量字段。
- TransportTask、DeviceCommand 或缓存状态。
- 来源锁和锁代际。

WES 接纳任务后进入 `QUEUED`。任务发布固定建立 `queue_revision=1`，后续队列更新必须连续递增。接收 ACK 只证明任务已可靠
入队，不代表资源已经分配。

同一任务的队列更新按 revision 串行发布。前一 revision 取得稳定接收 ACK 前，WMS 不得生成或发送下一 revision；响应未决时
只重提原消息。

所有 `QUEUED` 任务进入同一个自动出库任务池，`dispatch_sequence` 在池内唯一。WES 先过滤未到 `not_before`、已被领取或当前
没有任何就绪工作线可以准入的任务，再选择优先序最小的可执行任务；暂时不可执行的前序任务不阻塞后续可执行任务。

多条分拣机工作线具有相同物理结构和各自关联的 STATION。WES 从无活动任务且通过设备、工作位、缓存和 Transport 准入的
工作线中，按本地 `available_since ASC → workline_code ASC` 稳定选择。任务领取和候选工作线保留在一个事务中完成。WMS 手工
调整只通过 `queue_changed` 修改业务优先序或 `not_before`；本阶段不增加 WorkLineGroup、能力标签、评分引擎或人工启动入口。

## 7. 异步启动

### 7.1 启动条件

WES 原子领取当前最高优先级的可执行任务并保留候选工作线后，持久化新的 `operation_id`，把任务迁移为 `STARTING`，然后调用
`outbound.picking_task.start@v1`。启动 Payload 中的 `workline_code` 由 WES 提供，WMS 据此使用该线关联的 STATION、货架工作位
和交接位置计算资源。

人工触发不会绕过这条路径。WMS 的人工操作只改变任务池业务优先序或最早启动时间。

### 7.2 ACK 与终局结果

WMS 收到启动请求后先可靠持久化，并快速返回：

```text
202 START_ACCEPTED
```

ACK 只表示 WMS 已接收请求。WES 保持 `STARTING`，不创建 TransportTask 或 DeviceCommand。

WMS 完成资源计算后，通过 WMS 事件入口回调 `outbound.picking_task.start_decided@v1`，并沿用原 `operation_id`：

- `START_GRANTED`：资源快照和业务锁完整生效。
- `START_WAIT`：本次不能形成完整业务快照，WMS 不保留可执行的部分结果。

`START_GRANTED` 使任务进入 `EXECUTING`，并把候选 WorkLine 冻结为正式绑定。`START_WAIT` 使任务回到 `QUEUED` 并释放候选
工作线；该线可以立即领取其他可执行任务，当前任务下次重新求值时也可以选择另一条线。WES 创建新的 `operation_id`，并在
请求中引用 `previous_operation_id`。

响应未知时，WES 使用原 `operation_id` 和原 Payload 重试。收到 ACK 后超过结果期限仍没有终局回调时，WES 不能假定失败、
释放候选工作线、换线或创建新启动尝试，只能重提原请求或进入对账。

### 7.3 WMS 资源快照

`START_GRANTED` 至少包含：

- `source_member_revision` 和来源锁代际。
- `direct_picks` 与 `bin_works` 分别允许 `0..N` 个成员，但两者合计必须包含 `1..N` 个来源成员。直接取料成员指定
  `rack_id + rack_face + slot_id`，Bin 工作成员指定五层货架、货架面、精确 `RACK_BIN_SLOT` 和候选 Bin。
- 一个或多个目标转运货架及不可变目标窗口。

WMS 在发出 `START_GRANTED` 前完成内部容量预留。wire 不向 WES 暴露总容量、剩余容量、尺寸兼容矩阵或本地可计算阈值。
`START_GRANTED` 不包含 CTU 入站或退箱批次；这两类批次必须等待第 9 节规定的现场事实形成后再同步请求。
初始 `START_GRANTED` 至少包含一个目标窗口；SCAN2、来源恢复和逐盘扫码只能因实际计划或物料身份增加替代、扩展窗口，不能
补齐一个原本没有目标资源的启动结果。

WES 可靠接纳 `START_GRANTED` 后，才依据货架当前可靠 `PositionProjection`、WorkLine 固定工作位和本地资源门禁组织初始
进场。货架离场去向不在启动阶段预生成；只有该货架全部来源成员、退箱目标和位置依赖闭合后，WES 才向 WMS 同步请求业务
清场去向并创建 TransportTask。

## 8. 来源锁与货架独占

WMS 是来源业务锁的唯一 owner：

- 每个已授权五层货架和退料货架在任务执行期间由当前工作线独占。
- 候选 Bin、直接取料 SLOT 和后续追加来源使用明确的锁代际。
- 同一退料货架即使只使用一个 SLOT，也不能同时分配给其他工作线。
- 单个直接取料完成并且位置事实已接收后，WMS 可以释放该 SLOT 的库存占用。
- 货架仍有未完成来源或尚未可靠离开工作位时，WMS 不能解除整架独占。
- 锁不按沉默时长自动释放。未知结果必须通过原操作重试、对账或人工处置闭合。
- 退箱换架可以复用任务内已有来源货架，也可以由 WMS 引入额外五层货架。复用时不建立第二份锁；额外货架建立任务级退箱
  承接占用，并持续到清场终态、目标 SLOT 预留、退箱决定和位置事实全部闭合。确定失败或未知期间不释放。

任务完成事实用于释放尚未逐项释放的来源业务锁，但不代替货架离位事实。

## 9. 并行运输与退料优先

收到 `START_GRANTED` 后，WES 先原子持久化完整业务快照并返回 ACK，再从当前可执行工作集创建独立 TransportTask。初始进场
来源取自可靠位置投影，目标取自 WorkLine 为货架角色配置的固定工作位；任一位置未知都禁止建任务。以下货架运输可以并行准备：

WES 业务 owner 在每次调用 Transport 前，以“WMS 业务决定 `operation_id + 执行阶段 + 确定成员和方向`”为唯一键，原子保存
完整 Transport 输入、新生成的 UUIDv7 `client_request_id` 和待补写的 `transport_task_id`。同一业务阶段最多创建一项任务；
调用后崩溃只能以原 `client_request_id` 和原 Payload 重放。WMS 不再为同一业务决定生成第二套搬运身份。

1. 当前目标转运货架到目标工作位。
2. 当前可执行退料货架到来源工作位。
3. 五层货架到 CTU 作业位。

CTU 批次在货架运输之后按现场事实晚绑定：

1. 五层货架可靠到达 CTU 作业位后，WES 核对实际货架和到达面，从 `INGRESS_BUFFER` 原子预留具体位置，再请求 WMS 计算
   入站批次。
2. WMS 从当前权威位置仍等于冻结来源、且尚未被任何入站决定消费的候选 Bin 中选择本次 `1..4` 个成员，并同时受 CTU 背篓
   可用量和 WES 预留位置约束；WES 按返回的原子 moves 创建一次 `move_bins()`。业务未关闭但已经投线的 Bin 不再进入批次规划。
3. 正常 Bin 可靠到达 `RETURN_BUFFER` 后才成为退箱候选。WES 提交实际候选、来源位置、当前工作货架和到达面，请求 WMS
   计算退箱批次。
4. WMS 根据 CTU 背篓可用量和当前货架面的权威空储位选择候选子集并预留目标。Bin 可以进入当前货架面的任意合法空储位，
   不要求回到原货架或原储位。

入站完成可以唤醒下一次入站或退箱规划，但退箱不是固定串在入站之后。两类批次都必须由新的现场事实触发；没有可执行批次时
WMS 返回当前快照的 `NO_BATCH | FACE_DONE`。当前货架面没有足够退箱空位且能够安全推进时，WMS 返回
`RACK_PREPARATION_REQUIRED` 及当前架去向、新架和目标面；WES 按固定顺序创建动作，不自行凑批或选择业务位置。

`FACE_DONE` 只表示当前面没有尚待投线的入站合格成员，不要求已经投线的 Bin 完成 Cell 工作。入料位置预留随请求结果闭合：
成功决定保留被选位置，`NO_BATCH | FACE_DONE` 和确认未接纳释放或原子转属，背压、响应未知和冲突保持预留并按原身份重试或
对账。

多个退料货架或多个货架面不形成一条由 WMS 下发的机械队列。WMS 给出来源 SLOT，WES 根据退料优先规则、
工作位、运输终态、共享机械臂和当前目标货架面选择实际顺序，并优先连续执行同一货架和货架面，减少换面和换架。

退料直接取料具有调度优先级，但不是全局屏障。目标货架、退料货架和五层货架满足各自条件后可以并行推进。共享机械臂、
扫码台或目标货架面发生冲突时，WES 才按优先级串行仲裁。

Transport `UNKNOWN` 不触发自动重放、替代任务或资源释放；它本身已经表达 Transport 核心 `RECONCILING` 和业务等待。
WMS 人工核对后仍通过
`transport.task.resulted@v1` 为同一 `transport_task_id` 发布新的权威结果 evidence：完整位置证明已完成时为 `SUCCEEDED`，
证明未完成但位置明确时为 `FAILED`，仍无法确认则保持 `UNKNOWN`。只有 `UNKNOWN/RECONCILING` 可以由新 evidence 形成更高的
内部 `outcome_version`，不增加同义的人工对账事件或业务状态。

确定的 `REJECTED | FAILED` 已经终结旧 TransportTask 并释放 Transport 核心绑定。WMS 只有形成位置明确且可执行的替代方案后，
才发送 Transport 恢复事件；尚无安全方案时不发送空决定，WES 保持相关业务步骤阻塞并告警。人工核对是 WMS 内部过程，不增加
没有可执行动作的 wire 状态。WES 不自行改写搬运对象、来源和目标。PickingTask 已完成后发生的退箱或清场失败不回退业务任务，
但自动出库业务 owner 继续阻止依赖步骤，直到新的可靠决定或现场事实闭合。

## 10. 三段缓存与 FIFO

滚筒线包含以下位置角色：

- `INGRESS_BUFFER`：CTU 投箱缓存。
- `WORK_BUFFER`：等待位置和料箱工作位，单向 FIFO，总容量包含实际工作位。
- `RETURN_BUFFER`：正常完成 Bin 的退箱缓存。
- `NG_EXIT`：NG Bin 专用出口，不计入退箱缓存。

每个物理位置维护 `FREE | RESERVED | OCCUPIED | IN_TRANSIT | UNKNOWN` 投影。区域占用量从位置事实聚合，不能用可直接增减的
业务计数器替代。

CTU 投箱必须先取得具体 `INGRESS_BUFFER` 位置预留，再由 WMS 基于实际货架面、候选 Bin 和 CTU 背篓可用量形成原子入站
moves。正常 Bin 只有可靠到达 `RETURN_BUFFER` 后才进入退箱候选；目标空储位由 WMS 从当前工作货架面分配。PLC 或 ECS
继续承担物理防撞和非空位置互锁，WES 投影不是唯一安全措施。结果未知时，相关位置保持保守阻塞。

`WORK_BUFFER` 不能重排或绕行。CTU 可以乱序送达候选 Bin，但进入工作段后的队首 Bin 必须先取得一种明确结果：

- WMS 返回 Cell 工作计划。
- WMS 返回 `NO_WORK`。
- WES 根据可靠设备证据把 Bin 路由到 NG。

队首 Bin 未闭合时，后续 Bin 保持 FIFO 等待。

## 11. Bin 工作计划

Bin 到达料箱工作位后执行 SCAN2。WES 先持久化 Bin 身份和位置证据，再调用 `outbound.bin.work_plan@v1`。

WMS 根据 PickingTask、实际 Bin、已完成退料直接取料和剩余需求返回：

- `READY`：返回该 Bin 当前需要执行的非空 `cells[1..N]`。
- `NO_WORK`：该 Bin 不再需要取料，`BinWorkExecution` 可以业务闭合。
- `WAIT`：当前不能形成稳定计划，Bin 保持工作位占用。

每个 Cell 成员至少包含稳定 `cell_execution_id`、`cell_id` 和 `target_assignment_id`。目标 ID 引用任务内唯一、不可变的
货架、货架面及代际定义。来源锁代际冻结在父 `BinWorkExecution`，Cell 继承但不重复保存。首版不定义 Cell 优先级或依赖图，
WES 根据 FIFO、设备和位置状态安排实际 Cell 顺序。

相同 `BinWorkExecution` 的计划是不可变快照。需要重新求值时，WES 创建新的 `operation_id` 并引用
`previous_operation_id`。终局 `READY | NO_WORK` 只能有一个。

`BinWorkExecution` 在以下任一条件满足后完成：

- `READY` 计划中的全部 CellExecution 完成。
- WMS 返回 `NO_WORK`。
- Bin NG 的物理去向明确，且来源恢复决定已经闭合该业务成员。

物理 `BinExecution` 可以在此后继续退箱，不阻塞 PickingTask 业务完成。

## 12. 逐盘执行

`DirectPickExecution` 和 `CellExecution` 共用逐盘扫码与目标放置链路：

1. 来源机械臂从 WMS 指定的 `RACK_SLOT` 或 `BIN_CELL` 取出一盘物料。
2. 料盘进入扫码台，设备上报完整六合一码。
3. WES 持久化不可变扫码证据并调用 `outbound.material.decide@v1`。
4. WMS 返回 `ACCEPT | REJECT | WAIT`。`ACCEPT` 携带有效目标窗口和精确目标 `RACK_SLOT`；需要物理换面或换架时，附带固定
   `ROTATE` 或 `REPLACE` 目标准备联合。`REJECT` 携带稳定业务异常分类和闭集来源处置；`WAIT` 只表示业务决定尚未形成。
5. 需要目标准备时，WES 保持当前盘占用扫码台，按固定顺序执行 `ROTATE → PUT` 或
   `CURRENT_RACK_CLEARANCE → NEXT_RACK_STARTUP → PUT`。Transport 成功终态和位置、货架面、代际全部一致后，目标机械臂
   直接执行 PUT，不再次请求物料决定。
6. 放置终态明确后，WES 更新位置投影并同步提交移动事实；WMS 返回 `RECORDED | DUPLICATE` 后，WES 才推进依赖该事实的下一盘。

完整六合一码为 `HHPN`、`MfrPN`、`Qty`、`DateCode`、`LotCode`、`PkgID`。WES 不根据内容判断物料资格，也不复制第二套
物料身份。

一个 `BIN_CELL` 可以连续产生多盘。WMS 在每盘决定中返回 `demand_state=REMAINS | SATISFIED`。当前盘物理闭合后，
`REMAINS` 才允许继续取下一盘，`SATISFIED` 关闭当前 Cell。

`DirectPickExecution` 对应一个退料 SLOT 和一盘物料。该盘形成明确接受、拒绝或恢复结果，并且物理位置已确定后完成。

扫码台最多保存一盘未决物料。WMS 超时、响应无法关联或 PUT 结果未知时，WES 保持资源占用，不把未知解释为 NG、完成或可继续。
已经取得终局 `ACCEPT`、但目标尚未可靠就绪时，`MaterialExecution` 使用本地 `WAIT_TARGET_READY`，不能把物理等待降级为
WMS 业务 `WAIT`。

## 13. 转运货架换面与换架

WMS 维护转运货架容量和规格兼容，并在允许来源执行前完成内部预留。WES 不维护以下数据：

- 7、13、15 寸储位总量或剩余量。
- 不同规格之间的兼容矩阵。
- 基于百分比、低水位或预测需求的换架阈值。

WMS 为每个可执行来源成员给出 `target_assignment_id`。该 ID 在任务内唯一引用不可变的
`rack_id + rack_face + face_window_generation` 目标窗口，来源成员不复制第二份窗口值。
实际扫码结果需要新目标时，终局 `ACCEPT` 只为当前 `MaterialExecution` 绑定新的有效目标 ID，不改写其他未取料来源成员。

WES 只处理目标窗口变化：

1. 下一来源成员仍属于当前已到位货架、货架面和代际时继续执行。
2. `rack_id` 和 `rack_face` 相同但代际递增时，只替换目标窗口，不创建 TransportTask。
3. `rack_id` 相同且 `rack_face` 变化时，根据目标窗口和固定工作位创建 `ROTATE` TransportTask。
4. `rack_id` 变化时，先按 WMS 给出的业务去向清场当前目标架，再从可靠位置投影把新目标架运到固定工作位。
5. 目标窗口和容量预留已经接纳、扫码台为空且没有其他未决 MaterialExecution 时，允许有界预取并扫码一盘；新货架或货架面
   可靠到位前只禁止 PUT。

`face_window_generation` 是工作线目标工作位上的单调窗口围栏，不代表物理动作。应用新窗口时必须等于当前代际加一；旧代际
和跳号代际均视为合同冲突。只有目标面确实变化时才能创建 `ROTATE` TransportTask。

因此，7 寸储位仍有空位但大尺寸储位耗尽时，是否换面或换架由 WMS 的下一个目标窗口体现。WES 不从现场空位推导该决定。

精确目标 SLOT 在料盘扫码决定中返回。实际扫码物料需要换面或换架时，WMS 返回终局 `ACCEPT`、新的有效目标窗口、精确 SLOT
和完整 `target_preparation`；WES 保持当前料盘在扫码台并组织执行，目标就绪后直接 PUT。WMS 尚不能形成物料资格、精确目标，
或者当前架仍有正在闭合的短暂依赖时，返回带稳定原因和重试间隔的业务 `WAIT`。

对已在扫码台的物料返回换架方案前，WMS 必须确认当前目标架可以立即清场：没有未确认 PUT、未接收位置事实、关联中的目标
机械臂动作，或仍必须使用该架和当前面的其他未完成来源。当前硬件没有安全暂存位，因此不能下发不可执行的换架方案。只由
正在闭合的 PUT、位置事实或机械臂动作造成的短暂阻塞返回 `WAIT / TARGET_RACK_DRAINING`；相关事实可靠闭合时立即重新求值。
当前目标架无法安全清场且 WMS 尚无可执行替代方案时返回 `WAIT / TARGET_RACK_UNAVAILABLE`。`retry_after_ms` 只作没有新事实时
的兜底唤醒；两类资源等待都不能解释为物料质量 NG。WES 本地技术超时只能暂停、告警并进入对账，不能替 WMS 生成业务拒绝。

目标转运货架可以在 PickingTask 尚未完成时离场。只有以下条件都满足时，WES 才通过
`outbound.rack.clearance_decide@v1` 请求并执行该货架的清场去向决定：

- 没有未完成来源成员继续使用该货架和货架面。
- 已放入该货架的逐盘位置事实已被 WMS 接收。
- 目标机械臂和工作位没有与该货架关联的未决动作。

WMS 在清场 TransportResult 确认货架已离开工作位并到达决定位置后释放该货架业务占用。

## 14. NG、空取与来源恢复

NG 按物理影响对象分为三层，不能把技术等待、资源不足或结果未知统称为 NG：

| 作用域 | 唯一判定 | 处理 |
| --- | --- | --- |
| `MATERIAL` | 完整六合一码和来源绑定正确，但 WMS 返回 `MATERIAL_REJECTED` | 只把当前盘放入 NG 区，来源按 WMS 的 `CONTINUE \| CLOSE \| WAIT_RECOVERY` 处理 |
| `CELL` | 完整物料身份与 WMS 权威 Cell 绑定冲突，WMS 返回 `SOURCE_CELL_MISMATCH` | 当前盘进入 NG 区，当前 Cell 停止；同 Bin 其他 Cell 继续，Bin 最终进入 NG 出口 |
| `BIN` | Bin 条码不可读、不属于候选集合、SCAN1/SCAN2 身份不一致或可靠方向检测错误 | 整个 Bin 停止取料并进入 NG 出口 |

空取使用独立 `SOURCE_OBSERVATION`；六合一码未读全、设备失败、WMS `WAIT`/超时、目标换架等待和 Transport/PUT 结果未知都不是
NG。一次 Bin 读码不完整也不是 NG；只有按设备合同结束允许的读取重试并形成“无法建立合法身份”的可靠终态，才是
`BIN_CODE_UNREADABLE`。

MATERIAL/CELL NG 复用逐盘移动 Fact，作用域由原 WMS 决定和来源执行身份唯一解释。Bin 可靠到达 `NG_EXIT` 后统一使用
`outbound.bin.ng_exit_report@v1`：`cause_scope=CELL` 只补充 CELL NG 后的 Bin 最终位置，并通过 `cause_ng_evidence_id` 引用
前序料盘 NG 事实，不扩大业务作用域；`cause_scope=BIN` 才表达整箱 NG 并允许触发 BinWork 来源恢复。每次出口到位生成自己的
`ng_evidence_id`，路由结果未知时不得上报已完成事实。

SCAN1 发现未知或非法 Bin 时只隔离该物理 Bin 并告警，不得猜测 `BinWorkExecution` 或关闭候选成员。WMS 通过权威证据完成
业务关联后，才能以该 NG 证据触发来源恢复。

设备对退料货架 `RACK_SLOT` 或 `BIN_CELL` 返回可靠空取结果时，WES 持久化来源观察证据，再通过来源级判别联合请求 WMS 返回
`RETRY | WAIT | SOURCE_DONE`。只有 `SOURCE_DONE` 能在没有未决需求缺口和物料动作时关闭当前 DirectPickExecution 或
CellExecution；关闭 Cell 后由父 BinWorkExecution 重新聚合。

来源缺口由 WMS 通过 `outbound.picking_task.source_recovery_decided@v1` 处理：

- `NO_ADDITIONAL_SOURCES`：不增加任务成员。
- `ADD_SOURCE_MEMBERS`：追加新的 `DirectPickExecution` 或 `BinWorkExecution`，并给出锁代际；WES 按可靠位置和固定工作位组织
  新增货架进场。

来源恢复明确列出因本次原因关闭的 `DirectPickExecution`、`CellExecution` 或 `BinWorkExecution`。旧成员关闭和可选新成员
接纳必须原子生效；来源恢复不能直接追加尚未经过 SCAN2 的 Cell。Cell 仍由对应 Bin 工作计划创建，新增成员使用递增
`source_member_revision`。

## 15. 状态与完成

### 15.1 PickingTask 状态

| 状态 | 不变量 | 迁移 |
| --- | --- | --- |
| `QUEUED` | 已入队，尚无有效启动结果 | `STARTING` |
| `STARTING` | WMS 正在计算和锁定资源，不能创建设备或运输动作 | `QUEUED`、`EXECUTING` |
| `EXECUTING` | 来源成员不可改写，只能通过恢复事件追加 | `EXECUTION_COMPLETED` |
| `EXECUTION_COMPLETED` | 已接纳业务成员全部闭合，不代表现场已清场 | 无 |

同一 WorkLine 当前任务处于 `STARTING | EXECUTING` 时，不允许把该线后继任务迁移为 `STARTING`；其他就绪 WorkLine 仍可按
任务池优先序领取可执行任务。

### 15.2 完成条件

PickingTask 的本地完成条件是：

```text
ALL(DirectPickExecution.status == COMPLETED)
AND
ALL(BinWorkExecution.status == COMPLETED)
```

`BinWorkExecution` 负责聚合自己接纳的 CellExecution，避免尚未执行 SCAN2 时出现空 Cell 集合误判完成。

任务完成不读取：

- 来源货架或目标转运货架是否已经离位。
- 物理 Bin 是否已经回到五层货架。
- AGV、CTU、缓存或工作位是否已经清场。
- 下一任务是否已经具备本地准入条件。

逐盘移动事实必须先被 WMS 同步确认为 `RECORDED | DUPLICATE`，WES 才能提交任务完成事实。确认响应表示事实及后续决定依赖的
WMS 业务状态已经提交，不存在第二个完成回调。可靠提交由 `WmsConfirmation` 管理，不回填到 PickingTask 状态机。

完成报告必须按最终 `source_member_revision` 精确覆盖所有曾接纳的 `DirectPickExecution` 和 `BinWorkExecution`；通过来源
恢复关闭的成员与 WMS 返回 `NO_WORK` 的成员使用不同结果，不能遗漏恢复新增成员。

### 15.3 工作线释放

下一任务准入独立检查当前 `LineRunEpoch` 下的设备、工作位、缓存、位置投影和活动 Transport。PickingTask 完成不能绕过
现场资源门，也不需要增加承载所有动态状态的通用 Session。

## 16. WMS 交互面

### 16.1 ID 所有权摘要

| ID 层级 | 生成方 | 代表什么 |
| --- | --- | --- |
| `operation_id` | 当前 WMS/WES 交互的发起方 | 一次请求、事件或 Fact 交互及其幂等身份 |
| `task_id`、来源成员 ID、`target_assignment_id` | WMS | PickingTask、WMS 分配的来源成员和目标窗口 |
| `execution_id`、物理执行 ID、证据 ID | WES | PickingTask 本地执行实例和可靠现场事实；正式 WorkLine 绑定在 `START_GRANTED` 时冻结 |
| `client_request_id` | WES 自动出库业务 owner | 调用 Transport 的稳定幂等号；与 WMS 决定 `operation_id`、执行阶段和完整输入原子持久化 |
| `transport_task_id` | WES Transport 服务 | 一个可靠搬运执行对象 |

Transport submit 与其他 WMS/WES wire 一样使用顶层 `operation_id`：WES Transport 在首次形成不可变提交时生成并持久化，安全
重提保持原值；WMS 的每条 Transport evidence 使用自己的新 `operation_id`，通过 `transport_task_id` 关联任务。业务 JSON 不使用
`event_id` 或 `request_id`，因果引用字段只引用既有 ID，不产生第二套身份。完整字段级生成方、用途、
不可变和重试规则以
[WMS / WES 自动出库 PickingTask 交互要求](../../contracts/wms-outbound-picking-task-integration-requirements.md#31-id-分类生成方与用途)为准；
Transport 内部 ID 以 [Transport 履约合同](../../contracts/transport-fulfillment-contract.md#411-transport-id-所有权)为准。

### 16.2 API 端点

推荐端点：

| 发起方 | 接收方 | 路径 | 用途 |
| --- | --- | --- | --- |
| WMS | WES | `POST {{WES_BASE_URL}}/api/v1/wms/events` | 发布任务、更新队列、返回启动结果、发布来源或 Transport 恢复决定 |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/decisions` | 启动使用接纳 ACK+异步终局；CTU 批次、货架清场去向、Bin 计划、逐盘和空取在响应内同步决定 |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/facts` | 同步提交 Bin NG 出口到位、逐盘位置和任务完成事实，不使用结果回调 |

所有消息使用顶层 `operation_id`。同一业务交互的重试保持 ID 和 Payload 不变；异步终局结果沿用发起请求的
`operation_id`。`task_id`、`queue_revision` 和证据 ID 继续表达各自的业务对象，不再增加同义消息 ID。

Transport 请求和结果继续遵循独立 Transport 合同。PickingTask operation 只提供业务决定，不能直接修改 TransportTask
状态或伪造货架、Bin 到位事实。

## 17. 阶段与验收所有权

| 所有者 | 验证范围 |
| --- | --- |
| 基础 HTTP 和可靠对象 | 传输结果、超时、幂等、Outbox 和 unknown，不使用出库业务证明基础能力 |
| WMS ACL 合同 | 固定 operation、DTO、错误、幂等和 WMS 业务结果解释 |
| 自动出库插件 | PickingTask、直接取料、Bin 工作计划、Cell 循环、FIFO、仲裁和完成边界 |
| Transport 合同 | 货架与 Bin 搬运、换面、换架、成员位置和异步终态 |
| 设备统一接口 | 公共设备信封、命令、ACK、回调和结果 |
| 供应商验收 | 具体扫码、机械臂、滚筒线、PLC 和设备行为 |

基础能力测试不能用业务插件通过来代替，业务插件测试也不能反向证明基础设施正确。

## 18. 最低验收场景

| 场景 | 通过标准 |
| --- | --- |
| WMS 发布 PickingTask | Payload 不含 `workline_code`；WES 只加入自动出库任务池，不冻结执行线、Rack、Bin、Cell 或目标储位 |
| 多条分拣机工作线同时就绪 | WES 按任务池优先序领取不同任务，并以 `available_since + workline_code` 稳定选择执行线 |
| 前序任务暂不可执行 | 后续可执行任务可以由其他就绪工作线领取；`dispatch_sequence` 不构成完成依赖 |
| 启动请求已接纳 | `START_ACCEPTED` 后任务和候选工作线仍保持 `STARTING` 保留，不创建 TransportTask 或 DeviceCommand |
| 异步 `START_GRANTED` | 完整来源成员、来源锁和目标窗口一次接纳，不包含货架动作或 CTU 批次；WES 持久化并 ACK 后组织初始进场 |
| 启动暂时不可用 | `START_WAIT` 使任务回到任务池并释放候选工作线，没有部分结果残留；下一次可以重新选线 |
| 启动结果未知 | 保持原任务领取和候选工作线，使用原 `operation_id` 对账，禁止换线或重复领取 |
| 启动结果没有初始目标窗口 | 拒绝启动，不允许先搬来源、再依赖 SCAN2 或逐盘决定补齐目标资源 |
| WMS 手工调整任务 | 通过 `queue_changed` 改变任务池业务优先序，不调用 WES 人工启动接口 |
| 多个退料货架或货架面 | 货架独占，SLOT 逐项执行；WES 按物理条件安排顺序 |
| 退料与五层货架同时就绪 | 退料直接取料优先，但不阻塞没有资源冲突的 CTU 和 Bin 流 |
| 五层货架可靠到位 | WES 按实际到达面预留具体入料位置，WMS 只从当前仍在冻结来源、且入站资格未消费的成员中返回原子批次 |
| 入站批次请求未被接纳或结果未知 | 确认未接纳时释放或原子转属位置；背压、未知和冲突时保持原 operation 预留并重试或对账 |
| 出料口出现正常 Bin | WES 只提交 `RETURN_BUFFER` 实际候选；WMS 从当前货架面分配任意合法空储位形成退箱批次 |
| 当前货架面没有退箱空位 | WMS 返回 `NO_BATCH` 等待，或以 `RACK_PREPARATION_REQUIRED` 给出当前架去向、新架和目标面；WES 不自行寻找其他储位 |
| 退箱换架引入额外五层货架 | WMS 建立任务级退箱承接占用，并在清场终态、目标预留、退箱决定和位置事实全部闭合后释放 |
| CTU 乱序投箱 | 初始或来源恢复新增的任一未关闭候选 Bin 可按实际到达顺序在 SCAN2 请求工作计划 |
| FIFO 队首无工作 | WMS 返回 `NO_WORK` 后该 Bin 业务闭合并继续向后流转 |
| Bin 计划尚未产生 Cell | PickingTask 不因 Cell 集合为空而完成 |
| 7 寸有空位但大尺寸无容量 | WMS 给出新的目标货架或货架面窗口，WES 不计算容量 |
| 目标窗口切换 | 允许扫码台单盘预取；新货架或货架面可靠到位前禁止 PUT |
| 精确 SLOT 分配 | WMS 在料盘扫码后返回目标 SLOT，WES 只校验并执行 |
| 扫码后需要换面或换架 | `ACCEPT` 一次返回精确 SLOT 和完整目标准备方案；WES 创建 TransportTask，到位后直接 PUT，不重复物料决定 |
| 换架时当前架存在短暂依赖或当前无安全目标 | 分别返回 `WAIT / TARGET_RACK_DRAINING` 或 `WAIT / TARGET_RACK_UNAVAILABLE`；事实变化后立即重新求值，WES 本地超时只暂停和告警 |
| Material NG | 完整扫码和来源绑定正确，WMS 返回 `MATERIAL_REJECTED`；只隔离当前盘 |
| Cell NG | WMS 返回 `SOURCE_CELL_MISMATCH`；当前 Cell 停止，同 Bin 其他 Cell 继续；Bin 到达 `NG_EXIT` 后以 `cause_scope=CELL` 只补充位置事实 |
| Bin NG | Bin 身份或可靠方向异常；整箱可靠到达 `NG_EXIT` 后以 `cause_scope=BIN` 形成 NG Fact |
| 空取、扫码不完整、设备失败或未知 | 不得升级为 NG，分别进入空取、重试、暂停或对账流程 |
| 来源恢复 | 只追加 DirectPickExecution 或 BinWorkExecution，不直接追加未扫码 Bin 的 Cell |
| 任务业务执行完成 | 全部直接取料和 Bin 工作成员完成，不等待货架、Bin 或工作线清场 |

## 19. 当前批准状态

本文已经确认以下设计：

- PickingTask 发布只负责排队，WMS 在异步启动阶段执行耗时资源计算和锁定。
- 多条同构分拣机工作线属于同一自动出库任务池；WMS 给出业务优先序，WES 根据本地就绪事实选择具体 WorkLine。
- 任务以直接退料来源和候选 Bin 为初始业务成员，Cell 在 SCAN2 后晚绑定。
- 多个退料货架和货架面可以参与同一任务，货架独占，WES 安排物理执行顺序。
- `START_GRANTED` 只包含完整来源、目标窗口和业务锁；WES 接纳后自主组织初始货架进场。CTU 入站在来源货架实际到位后规划，
  退箱在 `RETURN_BUFFER` 出现实物后规划。
- WMS 按当前权威位置、一次性入站资格、CTU 背篓容量、WES 位置预留和权威货架空储位形成每个原子批次；退箱不要求原架
  原位，额外退箱承接货架由 WMS 维护任务级占用。
- CTU 乱序投箱不改变任务语义，单向 WORK_BUFFER 保持 FIFO。
- WMS 独占转运货架容量、业务去向和换面换架决定，WES 组织执行并验证现场到位。
- 同一工作线不提前启动后继任务；不同工作线可以并行领取可执行任务，WMS 手工操作通过队列更新进入正常启动流程。
- `operation_id` 是唯一消息交互身份。

具体 DTO、枚举、错误、超时和 fixture 以
`docs/contracts/wms-outbound-picking-task-integration-requirements.md` 为实施合同。
