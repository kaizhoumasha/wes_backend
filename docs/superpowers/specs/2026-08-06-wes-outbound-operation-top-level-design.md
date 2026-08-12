---
title: WES 出库操作顶层设计
status: ReviewRequired
created_at: 2026-08-06
updated_at: 2026-08-12
scope: SMT 自动出库 PickingTask、分批资源计划、执行中增删、事实驱动 CTU 批次、Bin 级晚绑定、FIFO 缓存与并行搬运
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

本文定义 SMT 自动出库的目标业务合同评审基线，说明 PickingTask 如何排队、准备、取得 WMS 资源决定并驱动物理执行。WMS 负责库存、
来源和目标分配；WES 负责工作线准入、可靠执行和现场证据。出库单、波次和库存策略不进入 WES。

本文当前为 `ReviewRequired`，不代表相关业务能力已经实施，也不构成 Phase 9 实施授权。

权威关系如下：

- `docs/architecture/SRS.md` 记录产品需求和参与方职责。
- 最小执行架构 SPEC 定义可靠对象、位置投影和扩展边界。
- 本文定义自动出库对象、状态、不变量和验收场景。
- `docs/contracts/wms-outbound-picking-task-integration-requirements.md` 定义自动出库 operation、method、path、请求与响应字段、错误和
  幂等规则；WMS 北向通用合同只定义 `WmsClient` 的 HTTP/JSON 访问边界。
- Master Plan 和实施计划只规定实现顺序，不能改变本文裁决。
- `docs/hardware/` 保存厂商原始输入，不是当前业务或接口合同真源。

系统尚未发布。本文只描述当前目标设计，不提供别名、fallback、双读、双写或迁移兼容。

## 2. 设计裁决

- WMS 发布可排队的 `PickingTask`，不在发布阶段分配货架、Bin、Cell 或目标储位。WES 选择任务和就绪工作线后请求准备；WMS
  同步 ACK，再按连续 `plan_revision` 异步发布计划增量。
- `plan_revision=1` 只定义一个初始目标窗口，也可以同时新增已锁定的直接取料来源或候选 Bin。后续计划增量只新增来源或取消
  指定 Bin；新目标窗口只由逐盘 `ACCEPT` 创建。WES 接纳局部完整的资源后即可执行，不等待整单计算完成。
- 货架动作、清场去向和 CTU 批次不进入计划增量。来源货架或正常 Bin 到达相应现场位置后，WMS 才根据实际候选、预留位置、
  背篓容量和货架空储位形成原子搬运批次；WES 不改写批次或选择退箱储位。
- PickingTask 成员是 `DirectPickExecution` 和 `BinWorkExecution`。Cell 在 Bin 实际到达 SCAN2 后创建。CTU 可以乱序投箱，
  `WORK_BUFFER` 保持单向 FIFO。
- 来源货架由当前工作线独占。退料直接取料优先，但不阻塞无资源冲突的 Bin 流。
- WMS 负责转运货架容量、规格兼容和目标分配。逐盘扫码后，WMS 返回物料资格、精确 SLOT 和可选目标准备方案；WES 只验证并
  执行已授权目标。
- 两个机械臂按不同 `device_code` 推进。ECS/PLC 硬件锁负责扫码台交接、防撞和动作互锁，并在没有安全暂存位时守住料盘离开
  来源的不可逆动作；WES 不建立扫码台事件、资源锁或跨机械臂软件互锁，也不以当前 PUT 完成作为另一机械臂开始下一条命令的
  业务前提。
- WMS 可用更高 revision 追加或取消 `BinWorkExecution`，但不能改写已接纳成员。取消遵守料盘离开来源后不可放回的安全点。
- 同一工作线只允许一张任务处于 `PREPARING | EXECUTING`；不同就绪工作线可以并行执行。人工调整通过 `queue_changed` 进入
  正常队列，WES 不提供独立人工启动入口。
- 本地业务义务、逐盘事实和取消动作闭合后，WES 携带 `last_applied_plan_revision` 请求 WMS 确认任务状态。WMS 返回
  `COMPLETED | NOT_COMPLETED`，不接收成员完成全集，也不等待货架、Bin、Transport 或工作线清场。

## 3. 范围与非目标

### 3.1 范围

- PickingTask 发布、队列更新、本地准入、同步 ACK、分批计划增量、执行中增删和版本化状态确认。
- 多个五层货架、候选 Bin、多个退料货架及货架面的来源执行。
- `DirectPickExecution`、`BinWorkExecution`、`CellExecution` 和逐盘 `MaterialExecution`。
- CTU 乱序投箱、三段缓存、单向 FIFO 和基于位置事实的背压。
- 退料优先取料、Bin 工作位 SCAN2、Cell 计划和逐盘晚绑定。
- 转运货架换面、换架、精确目标 SLOT 和可靠位置事实。
- 料盘、Cell、Bin 三类 NG、计划增量补充来源、空取和未知结果。

### 3.2 非目标

- WES 订单管理、波次计算、库存选择、库存锁策略或跨任务来源仲裁。
- WES 计算转运货架容量、尺寸兼容或替代目标货架。
- WES 规划 AGV、CTU 路线或 PLC 内部动作。
- 用 PickingTask 状态承载 Rack、Bin、Transport、设备命令或现场清场状态。
- 跨任务合并、全局优化器、通用补偿器或兼容旧接口。

## 4. 权威边界

| 参与方 | 唯一权威 | 不承担 |
| --- | --- | --- |
| WMS | PickingTask、业务总序、库存、来源分配、来源锁、转运货架容量、目标分配、物料资格、原子搬运批次和需求状态 | 设备动作、滚筒线位置和本地安全互锁 |
| WES | 队列准入、执行对象、计划版本接纳、现场证据、位置投影、NG 物理作用域和可靠外部义务 | 库存重算、来源替换、容量计算、目标储位选择和设备间安全互锁 |
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
| `WmsConfirmation` | 可靠提交位置变化事实和任务状态确认请求 | 重放设备动作或修改执行对象 |
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

## 7. 异步准备与分批计划

### 7.1 准备请求与 ACK

WES 原子领取当前最高优先级的可执行任务并保留候选工作线后，生成并持久化 `execution_id` 与 `operation_id`，把任务迁移为
`PREPARING`，然后调用 `outbound.picking_task.prepare@v1`。请求中的 `workline_code` 由 WES 提供，WMS 据此使用该线关联的
STATION、货架工作位和交接位置计算资源。人工操作不能绕过该路径。

WMS 可靠持久化请求后快速返回 `202 PREPARE_ACCEPTED`。ACK 只证明耗时资源运算已经被接纳，不代表存在可执行资源。请求响应
未知时，WES 使用原 `operation_id` 和原请求正文重试；WMS 返回首次接纳时的完整 `PREPARE_ACCEPTED` 响应，不改写时间戳或
改为 `DUPLICATE`。WES 不能释放候选工作线、换线或创建第二次准备请求。

### 7.2 计划增量

WMS 通过 `outbound.picking_task.plan_delta@v1` 分批回调。每个回调使用自己的 `operation_id`，并携带同一
`task_id + execution_id` 下严格连续的 `plan_revision`。WMS 内部可以并行计算，但对同一执行实例必须按 revision 串行发布；
前一版本没有取得稳定 ACK 前不得发布后一版本。

计划增量可以包含：

- `added_target_windows`：仅 `plan_revision=1` 携带固定一项，定义不可变初始目标货架窗口。
- `added_direct_picks`：新增直接取料成员，指定退料货架、货架面、SLOT、来源锁代际和目标窗口引用。
- `added_bin_works`：新增 Bin 工作成员，指定五层货架、货架面、精确 `RACK_BIN_SLOT`、候选 Bin、来源锁代际和目标窗口引用。
- `cancelled_bin_works`：取消指定 `bin_work_execution_id`；业务原因只保存在 WMS，不进入计划增量字段。

`plan_revision=1` 是唯一初始增量，必须且只能定义一个初始目标窗口。后续 revision 都是普通增量并禁止
`added_target_windows`；扫码后需要的新窗口只由同一盘终局 `ACCEPT` 原子定义。首个增量可以只包含初始目标窗口；后续增量
至少新增一个来源成员或取消一个 Bin 工作成员。新增成员和目标窗口不可变，已有成员不能通过高版本改来源、目标或锁代际。
WMS 内部计算进度不进入计划增量；业务补充、业务追加和来源缺口恢复统一使用同一条计划版本链。

### 7.3 接纳与局部开工

WES 先把完整回调保存为 `InboundEvidence`，再在一个事务中校验并应用计划增量，最后返回 ACK：

- 重复 `operation_id` 且请求正文相同，返回幂等 ACK。
- `plan_revision` 等于当前版本时，只允许内容完全相同的重放。
- `plan_revision` 必须等于当前版本加一；跳号、倒退、同版本不同内容或引用未定义目标窗口都失败关闭并停止自动推进。
- `plan_revision=1` 必须且只能携带一个初始目标窗口；后续新增来源只能引用已经接纳的有效目标窗口。由物料 `ACCEPT` 创建的
  窗口至少被一条逐盘位置 Fact 回显并由 WMS 确认后，计划增量才能引用，避免同步决定与异步增量乱序。

首批形成至少一个局部可执行工作集后，任务进入 `EXECUTING`，候选 WorkLine 冻结为正式绑定。局部可执行是指相关来源成员、
来源锁和目标窗口引用都已完整接纳；不要求其他货架或整单初始计算完成。WES 可立即依据可靠 `PositionProjection` 和 WorkLine
固定工作位组织这部分货架进场。目标窗口单独先到时也可以提前搬运目标架；没有完整来源成员时不得发起取料。

计划增量只表达业务资源，不携带 TransportTask、DeviceCommand 或 CTU 搬运批次。货架离场去向仍在现场依赖闭合后由 WES 请求
WMS 决定。WMS 若尚不能形成可执行资源，WES 通过第 15 节状态确认取得 `NOT_COMPLETED`，不观察 WMS 内部计算阶段。

## 8. 来源锁与货架独占

WMS 是来源业务锁的唯一权威：

- 每个已授权五层货架和退料货架在任务执行期间由当前工作线独占。
- 候选 Bin、直接取料 SLOT 和后续追加来源使用明确的锁代际。
- 同一退料货架即使只使用一个 SLOT，也不能同时分配给其他工作线。
- 单个直接取料完成并且位置事实已接收后，WMS 可以释放该 SLOT 的库存占用。
- 货架仍有未完成来源或尚未可靠离开工作位时，WMS 不能解除整架独占。
- 锁不按沉默时长自动释放。未知结果必须通过原操作重试、对账或人工处置闭合。
- 退箱换架可以复用任务内已有来源货架，也可以由 WMS 引入额外五层货架。复用时不建立第二份锁；额外货架建立任务级退箱
  承接占用，并持续到清场终态、目标 SLOT 预留、退箱决定和位置事实全部闭合。确定失败或未知期间不释放。

WMS 确认 PickingTask 完成后可以释放尚未逐项释放的来源业务锁，但该状态不代替货架离位事实。

## 9. 并行运输与退料优先

WES 接纳计划增量后，从其中已经满足局部前提的工作集创建独立 TransportTask。运输来源取自可靠位置投影，目标取自 WorkLine
为货架角色配置的固定工作位；任一位置未知都禁止建任务。以下货架运输可以随对应增量分批、并行准备：

WES 出库业务模块在每次调用 Transport 前，以“WMS 业务决定 `operation_id + 执行阶段 + 确定成员和方向`”为唯一键，原子保存
完整 Transport 输入、新生成的 UUIDv7 `client_request_id` 和待补写的 `transport_task_id`。同一业务阶段最多创建一项任务；
调用后崩溃只能以原 `client_request_id` 和原请求正文重放。WMS 不再为同一业务决定生成第二套搬运身份。

1. 当前目标转运货架到目标工作位。
2. 当前可执行退料货架到来源工作位。
3. 五层货架到 CTU 作业位。

CTU 批次在货架运输之后按现场事实晚绑定：

1. 五层货架可靠到达 CTU 作业位后，WES 核对实际货架和到达面，从 `INGRESS_BUFFER` 原子预留具体位置，再请求 WMS 计算
   入站批次。
2. WMS 从当前权威位置仍等于冻结来源、且尚未被任何入站决定消费的候选 Bin 中选择本次 `1..4` 个成员，并同时受 CTU 背篓
   可用量和 WES 预留位置约束；WES 按返回的原子搬运批次创建一次 `move_bins()`。业务未关闭但已经投线的 Bin 不再进入批次规划。
3. 正常 Bin 可靠到达 `RETURN_BUFFER` 后才成为退箱候选。WES 提交实际候选、来源位置、当前工作货架和到达面，请求 WMS
   计算退箱批次。
4. WMS 根据 CTU 背篓可用量和当前货架面的权威空储位选择候选子集并预留目标。Bin 可以进入当前货架面的任意合法空储位，
   不要求回到原货架或原储位。

入站完成可以唤醒下一次入站或退箱规划，但退箱不是固定串在入站之后。两类批次都必须由新的现场事实触发；没有可执行批次时
WMS 返回带强制重试间隔的 `NO_BATCH`，或返回不可撤销的封口结果 `FACE_DONE`。当前货架面没有足够退箱空位且能够安全推进时，WMS 返回
`RACK_PREPARATION_REQUIRED` 及当前架去向、新架和目标面；WES 按固定顺序创建动作，不自行凑批或选择业务位置。

`FACE_DONE` 永久承诺当前任务不会再追加该 `rack_id + rack_face` 的 Bin 工作成员，但不要求已经投线的 Bin 完成 Cell 工作。
WMS 尚不能作出永久承诺时只能返回 `NO_BATCH`。入料位置预留随请求结果闭合：
成功决定保留被选位置，`NO_BATCH | FACE_DONE` 和确认未接纳释放或原子转属。`NO_BATCH` 已经形成确定业务结果：后续重新求值
必须使用新的 `operation_id`，并以 `previous_operation_id` 引用直接前序请求。只有响应未知、`BUSY`、`UNAVAILABLE` 或冲突
对账才保留原请求身份和对应预留。

多个退料货架或多个货架面不形成一条由 WMS 下发的机械队列。WMS 给出来源 SLOT，WES 根据退料优先规则、工作位、运输终态
和当前目标货架面选择业务执行顺序，并优先连续执行同一货架和货架面，减少换面和换架。

退料直接取料具有调度优先级，但不是全局屏障。目标货架、退料货架和五层货架满足各自条件后可以并行推进。两个机械臂按不同
`device_code` 各自维持至多一条已接纳未终态命令；扫码台交接、防撞和动作互锁由 ECS/PLC 硬件锁完成，WES 不建立扫码台
资源锁、跨机械臂锁或平台释放事件。

Transport `UNKNOWN` 不触发自动重放、替代任务或资源释放；它本身已经表达 Transport 核心 `RECONCILING` 和业务等待。
WMS 人工核对后仍通过
`transport.task.resulted@v1` 为同一 `transport_task_id` 发布新的权威结果证据：完整位置证明已完成时为 `SUCCEEDED`，
证明未完成但位置明确时为 `FAILED`，仍无法确认则保持 `UNKNOWN`。只有 `UNKNOWN/RECONCILING` 可以由新证据形成更高的
内部 `outcome_version`，不增加同义的人工对账事件或业务状态。

确定的 `REJECTED | FAILED` 已经终结旧 TransportTask 并释放 Transport 核心绑定。WMS 只有形成位置明确且可执行的替代方案后，
才发送 Transport 恢复事件；尚无安全方案时不发送空决定，WES 保持相关业务步骤阻塞并告警。人工核对是 WMS 内部过程，不增加
没有可执行动作的接口状态。WES 不自行改写搬运对象、来源和目标。PickingTask 已完成后发生的退箱或清场失败不回退业务任务，
但自动出库业务模块继续阻止依赖步骤，直到新的可靠决定或现场事实闭合。

## 10. 三段缓存与 FIFO

滚筒线包含以下位置角色：

- `INGRESS_BUFFER`：CTU 投箱缓存。
- `WORK_BUFFER`：等待位置和料箱工作位，单向 FIFO，总容量包含实际工作位。
- `RETURN_BUFFER`：正常完成 Bin 的退箱缓存。
- `NG_EXIT`：NG Bin 专用出口，不计入退箱缓存。

每个物理位置维护 `FREE | RESERVED | OCCUPIED | IN_TRANSIT | UNKNOWN` 投影。区域占用量从位置事实聚合，不能用可直接增减的
业务计数器替代。

CTU 投箱必须先取得具体 `INGRESS_BUFFER` 位置预留，再由 WMS 基于实际货架面、候选 Bin 和 CTU 背篓可用量形成原子入站
搬运批次。正常 Bin 只有可靠到达 `RETURN_BUFFER` 后才进入退箱候选；目标空储位由 WMS 从当前工作货架面分配。PLC 或 ECS
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
- 已关联候选 Bin 的 BIN NG 出口事实被 WMS 接纳，并关闭该业务成员。

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
6. WES 把当前 `MaterialExecution` 和 WMS 已授权的目标窗口、精确 SLOT 写入目标机械臂 DeviceCommand 证据。只有匹配该命令的
   确定 `SUCCEEDED` CALLBACK 才表示 PUT 完成；ACK、失败、超时和结果未知都不能形成位置事实。
7. 放置终态明确后，WES 更新位置投影并通过 `outbound.material.movement_report@v1` 同步提交来源位置、目标位置、前序 WMS
   决定和设备结果引用；WMS 返回 `RECORDED | DUPLICATE` 后，WES 才推进依赖该位置事实的容量释放、成员完成和任务完成。

因此，逐盘 PUT 是“WMS 目标授权 → ECS 物理执行 → WMS 位置确认”三个独立权威边界，不得把 WMS 的 `ACCEPT` 当作物理完成，
也不得把 ECS 的成功 CALLBACK 当作 WMS 库存已经更新。WMS/WES 接口字段以 PickingTask 合同为准；目标机械臂 `task_type`、逻辑
`params`、结果 `data`、时限和错误必须由该实际设备的获批合同附录定义，不能写入 WES 核心全局枚举。附录只承载已授权的逻辑
目标和设备证据，不承载库存、容量、替代目标、PLC 坐标或硬件互锁。

完整六合一码为 `HHPN`、`MfrPN`、`Qty`、`DateCode`、`LotCode`、`PkgID`。WES 不根据内容判断物料资格，也不复制第二套
物料身份。

一个 `BIN_CELL` 可以连续产生多盘。WMS 在每盘扫码决定中返回 `next_source_action=CONTINUE | SOURCE_DONE`。`CONTINUE` 表示
业务需求允许继续从当前 Cell 取下一盘；`SOURCE_DONE` 表示当前盘闭合后关闭来源，DirectPick 只能返回该值。它不是扫码台安全
许可：当来源机械臂没有活动命令时，WES 可以在当前盘 PUT 并行进行期间下发下一条来源命令。ECS 可以接纳命令并执行不改变
料盘位置的准备动作；没有现场批准的安全暂存位时，硬件锁必须在料盘离开来源前确认扫码台交接路径可用，不能先取出下一盘再
持盘等待。WES 不等待扫码台释放事件，也不建立扫码台资源锁。

`DirectPickExecution` 对应一个退料 SLOT 和一盘物料。该盘形成明确接受、拒绝或恢复结果，并且物理位置已确定后完成。

料盘一旦被来源机械臂取出就不可放回原储位或料格。扫码台最多承载一盘未决物料；WMS 超时、响应无法关联或 PUT 结果未知时，
当前盘继续占用扫码台，不把未知解释为 NG、完成或可继续；达到本地技术超时后暂停、告警并进入对账。
已经取得终局 `ACCEPT`、但目标尚未可靠就绪时，`MaterialExecution` 使用本地 `WAIT_TARGET_READY`，不能把物理等待降级为
WMS 业务 `WAIT`。这是一种正常的单盘晚绑定状态，不要求把当前盘放回来源。

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
5. 目标窗口和容量预留已经接纳时允许预取并扫码一盘；新货架或货架面可靠到位前只禁止 PUT。没有安全暂存位时，料盘能否
   离开来源并进入扫码台由 ECS/PLC 硬件锁决定，不由 WES 扫码台投影或资源锁决定。

`face_window_generation` 是工作线目标工作位上的单调窗口围栏，不代表物理动作。应用新窗口时必须等于当前代际加一；旧代际
和跳号代际均视为合同冲突。只有目标面确实变化时才能创建 `ROTATE` TransportTask。

因此，7 寸储位仍有空位但大尺寸储位耗尽时，是否换面或换架由 WMS 的下一个目标窗口体现。WES 不从现场空位推导该决定。

精确目标 SLOT 在料盘扫码决定中返回。实际扫码物料需要换面或换架时，WMS 返回终局 `ACCEPT`、新的有效目标窗口、精确 SLOT
和完整 `target_preparation`；WES 保持当前料盘在扫码台并组织执行，目标就绪后直接 PUT。WMS 尚不能形成物料资格、精确目标，
或者当前架仍有正在闭合的短暂依赖时，返回带稳定原因和重试间隔的业务 `WAIT`。

对已在扫码台的物料返回换架方案前，WMS 必须确认当前目标架可以立即清场：没有未确认 PUT、未接收位置事实、关联中的目标
机械臂动作，或仍必须使用该架和当前面的其他未完成来源。当前硬件没有安全暂存位，料盘又不能放回来源，因此不能下发不可执行的换架方案。只由
正在闭合的 PUT、位置事实或机械臂动作造成的短暂阻塞返回 `WAIT / TARGET_RACK_DRAINING`；相关事实可靠闭合时立即重新求值。
当前目标架无法安全清场且 WMS 尚无可执行替代方案时返回 `WAIT / TARGET_RACK_UNAVAILABLE`。`retry_after_ms` 只作没有新事实时
的兜底唤醒；两类资源等待都不能解释为物料质量 NG。WES 本地技术超时只能暂停、告警并进入对账，不能替 WMS 生成业务拒绝。

目标转运货架可以在 PickingTask 尚未完成时离场。只有以下条件都满足时，WES 才通过
`outbound.rack.clearance_decide@v1` 请求并执行该货架的清场去向决定：

- 没有未完成来源成员继续使用该货架和货架面。
- 已放入该货架的逐盘位置事实已被 WMS 接收。
- 目标机械臂和工作位没有与该货架关联的未决动作。

WMS 在清场 TransportResult 确认货架已离开工作位并到达决定位置后释放该货架业务占用。

## 14. NG、空取与计划变更

NG 按物理影响对象分为三层，不能把技术等待、资源不足或结果未知统称为 NG：

| 作用域 | 唯一判定 | 处理 |
| --- | --- | --- |
| `MATERIAL` | 完整六合一码和来源绑定正确，但 WMS 返回 `MATERIAL_REJECTED` | 只把当前盘放入 NG 区，来源按 WMS 的 `CONTINUE \| CLOSE` 处理 |
| `CELL` | 完整物料身份与 WMS 权威 Cell 绑定冲突，WMS 返回 `SOURCE_CELL_MISMATCH + CLOSE` | 当前盘进入 NG 区，位置事实确认后关闭当前 Cell；同 Bin 其他 Cell 继续，Bin 最终进入 NG 出口 |
| `BIN` | Bin 条码不可读、不属于候选集合、SCAN1/SCAN2 身份不一致或可靠方向检测错误 | 整个 Bin 停止取料并进入 NG 出口 |

空取使用独立 `SOURCE_OBSERVATION`；六合一码未读全、设备失败、WMS `WAIT`/超时、目标换架等待和 Transport/PUT 结果未知都不是
NG。一次 Bin 读码不完整也不是 NG；只有按设备合同结束允许的读取重试并形成“无法建立合法身份”的可靠终态，才是
`BIN_CODE_UNREADABLE`。

MATERIAL/CELL NG 复用逐盘移动 Fact，作用域由原 WMS 决定和来源执行身份唯一解释。Bin 可靠到达 `NG_EXIT` 后统一使用
`outbound.bin.ng_exit_report@v1`：`cause_scope=CELL` 只补充 CELL NG 后的 Bin 最终位置，并通过 `cause_ng_evidence_id` 引用
前序料盘 NG 事实，不扩大业务作用域；`cause_scope=BIN` 才表达整箱 NG 并允许触发 BinWork 补充。每次出口到位生成自己的
`ng_evidence_id`，路由结果未知时不得上报已完成事实。

SCAN1 或 SCAN2 发现未知或非法 Bin 时只隔离该物理 Bin 并告警，不得把预期计划身份当成实际扫码身份，也不得直接关闭候选成员。
WES 必须上报把该物理 Bin 送入工作线的预期 `BinWorkExecution`；WMS 据此定位受影响的计划来源，再通过后续计划增量取消原成员
或追加替代成员。

设备对退料货架 `RACK_SLOT` 或 `BIN_CELL` 返回可靠空取结果时，WES 持久化来源观察证据，再通过来源级判别联合请求 WMS 返回
`RETRY | WAIT | SOURCE_DONE`。只有 `SOURCE_DONE` 能在没有未决需求缺口和物料动作时关闭当前 DirectPickExecution 或
CellExecution；关闭 Cell 后由父 BinWorkExecution 重新聚合。

来源缺口、业务追加和指定 Bin 取消都通过第 7 节同一条 `plan_revision` 链处理，不再建立独立来源恢复 operation 或版本：

- 缺口需要补充时，增量追加新的 `DirectPickExecution` 或 `BinWorkExecution`，并给出锁代际；WES 按可靠位置和固定工作位组织
  新增货架进场。计划增量不直接关闭原 DirectPick 或 Cell；WES 应用增量后，使用新的 operation 重新求值所有等待计划增量的
  空取观察。WMS 对需求缺口已经被承接的观察返回 `SOURCE_DONE`，关闭原来源；其他观察继续 `WAIT`。
- 不再补充时，WMS 通过既有空取或 NG 决定闭合当前来源，不发送空的计划增量。
- 业务取消必须引用稳定 `bin_work_execution_id`，不能只按 `bin_id` 猜测当前执行实例。
- 计划增量不能直接追加尚未经过 SCAN2 的 Cell；Cell 仍由对应 Bin 工作计划创建。

### 14.1 Bin 取消安全点

WES 接纳 `cancelled_bin_works` 后，按不可逆物理事实处理：

| 接纳时现场状态 | 处理 |
| --- | --- |
| Bin 尚未开始运输 | 立即取消业务成员，不创建后续 Transport 或 DeviceCommand |
| Bin 已运输、已入 FIFO 或已到工作位，但尚未接纳本 Bin 的取盘命令 | 停止创建新的取盘命令；让 Bin 按既有单向物流正常到达退箱路径，业务成员记为取消 |
| 取盘命令已被 ECS 接纳，或料盘已离开来源 | 当前盘不可取消、不可放回；必须先放入 WMS 指定目标或 NG 并可靠提交位置事实，再取消该 Bin 剩余工作 |
| Bin 工作成员已终态 | 接纳重复取消并推进 `plan_revision`；不回退终态，也不生成补偿动作 |

取消只关闭未来业务义务，不撤销已经接纳的 TransportTask、DeviceCommand 或已形成的位置事实。WMS 如果需要在取消后补充其他
Bin，必须在同一或后续计划增量中创建新的 `bin_work_execution_id`；不能修改旧成员。

## 15. 状态与完成确认

### 15.1 PickingTask 状态

| 状态 | 不变量 | 迁移 |
| --- | --- | --- |
| `QUEUED` | 已入队，尚未发起准备请求 | `PREPARING` |
| `PREPARING` | WMS 已接纳耗时资源运算；没有局部可执行计划时不创建设备或运输动作 | `EXECUTING`、`EXECUTION_COMPLETED` |
| `EXECUTING` | 已接纳至少一个计划版本；成员只能由更高 `plan_revision` 追加或取消，不能改写 | `EXECUTION_COMPLETED` |
| `EXECUTION_COMPLETED` | WMS 已确认 PickingTask 权威状态为完成，不代表现场已清场 | 无 |

同一 WorkLine 当前任务处于 `PREPARING | EXECUTING` 时，不允许把该线后继任务迁移为 `PREPARING`；其他就绪 WorkLine 仍可按
任务池优先序领取可执行任务。

### 15.2 状态确认条件

WES 只有在以下条件全部满足时才能发起状态确认：

- 当前没有未闭合的本地业务执行义务。
- 当前没有待处理的取消动作。
- 所有必须上报的逐盘位置事实都已被 WMS 确认。

WES 只判断当前是否仍有可执行或未闭合的本地义务，不重新枚举历史 `DirectPickExecution`、`BinWorkExecution` 或
`CellExecution` 结果。WMS 已经通过逐盘位置确认、空取决定、NG 事实、取消记录和需求状态持有业务完成依据。

`PREPARE_ACCEPTED` 后超过双方配置的首批期限仍没有计划增量时，WES 使用 `last_applied_plan_revision=0` 请求相同状态确认；WMS
可以返回同为 revision 0 的 `BUSINESS_IN_PROGRESS + retry_after_ms`，或在业务已经闭合时返回 `COMPLETED`。这不创建虚构计划版本。

任务完成不读取：

- 来源货架或目标转运货架是否已经离位。
- 物理 Bin 是否已经回到五层货架。
- AGV、CTU、缓存或工作位是否已经清场。
- 下一任务是否已经具备本地准入条件。

逐盘移动事实必须先被 WMS 同步确认为 `RECORDED | DUPLICATE`。随后 WES 通过
`outbound.picking_task.completion_confirm@v1` 携带 `last_applied_plan_revision` 请求同步状态确认：

- `COMPLETED`：版本一致且 WMS 权威业务状态已完成；WES 迁移为 `EXECUTION_COMPLETED`，WMS 不再发布该任务的计划增量。
- `NOT_COMPLETED`：WMS 权威状态尚未完成；WES 保持当前状态。版本落后时 WMS 重新投递缺失增量；业务仍在进行时 WMS 必须
  返回 `retry_after_ms`，WES 等待新事实或到期后重新确认。

请求不携带成员结果、完成数量或本地完成时间。`last_applied_plan_revision` 只承担版本围栏，不承担完成项对账。可靠请求由
`WmsConfirmation` 管理，任务状态只根据 WMS 同步决定迁移。

### 15.3 工作线释放

下一任务准入独立检查当前 `LineRunEpoch` 下的设备、工作位、缓存、位置投影和活动 Transport。PickingTask 完成不能绕过
现场资源门，也不需要增加承载所有动态状态的通用 Session。

`COMPLETED` 只关闭业务计划和成员变更。已经进入物流线的 Bin 仍可继续请求退箱，任务相关货架仍可继续请求清场；WMS 必须基于
完成时冻结的执行快照和占用处理这些请求，不能仅因任务已完成返回状态冲突。清场请求不得重新打开任务或追加业务成员。

## 16. WMS 交互面

### 16.1 ID 所有权摘要

| ID 层级 | 生成方 | 代表什么 |
| --- | --- | --- |
| `operation_id` | 当前 WMS/WES 交互的发起方 | 一次请求、事件或 Fact 交互及其幂等身份 |
| `task_id`、来源成员 ID、`target_assignment_id` | WMS | PickingTask、WMS 分配的来源成员和目标窗口 |
| `execution_id`、物理执行 ID、证据 ID | WES | PickingTask 本地执行实例和可靠现场事实；正式 WorkLine 在首批局部可执行计划接纳时冻结 |
| `client_request_id` | WES 自动出库业务模块 | 调用 Transport 的稳定幂等号；与 WMS 决定 `operation_id`、执行阶段和完整输入原子持久化 |
| `transport_task_id` | WES Transport 服务 | 一个可靠搬运执行对象 |

Transport 提交与其他 WMS/WES 消息一样使用顶层 `operation_id`：WES Transport 在首次形成不可变提交时生成并持久化，安全
重提保持原值；WMS 的每条 Transport 结果回调使用自己的新 `operation_id`，通过 `transport_task_id` 关联任务。业务 JSON 不使用
`event_id` 或 `request_id`，因果引用字段只引用既有 ID，不产生第二套身份。完整字段级生成方、用途、
不可变和重试规则以
[WMS / WES 自动出库 PickingTask 交互要求](../../contracts/wms-outbound-picking-task-integration-requirements.md#6-id-和版本由谁生成)为准；
Transport 内部 ID 以 [Transport 履约合同](../../contracts/transport-fulfillment-contract.md#51-入口和-id)为准。

### 16.2 API 端点

端点固定为：

| 发起方 | 接收方 | 路径 | 用途 |
| --- | --- | --- | --- |
| WMS | WES | `POST {{WES_BASE_URL}}/api/v1/wms/events` | 发布任务、更新队列、分批发布计划增量和 Transport 恢复决定 |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/decisions` | 准备请求使用同步 ACK+异步计划增量；CTU 批次、货架清场去向、Bin 计划、逐盘、空取和任务状态确认在响应内同步决定 |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/facts` | 同步提交 Bin NG 出口到位和逐盘位置事实，不使用结果回调 |

所有消息使用顶层 `operation_id`。同步响应回显请求 ID；每条异步消息都生成自己的 ID。同一不可变消息的重试保持 ID 和请求正文
不变；每批计划增量使用自己的 `operation_id`，并由
`task_id + execution_id + plan_revision` 建立顺序。`task_id`、`queue_revision` 和证据 ID 继续表达各自业务对象，不增加同义消息 ID。

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
| WMS 发布 PickingTask | 请求正文不含 `workline_code`；WES 只加入自动出库任务池，不冻结执行线、Rack、Bin、Cell 或目标储位 |
| 多条分拣机工作线同时就绪 | WES 按任务池优先序领取不同任务，并以 `available_since + workline_code` 稳定选择执行线 |
| 前序任务暂不可执行 | 后续可执行任务可以由其他就绪工作线领取；`dispatch_sequence` 不构成完成依赖 |
| 准备请求已接纳 | `PREPARE_ACCEPTED` 后任务和候选工作线保持 `PREPARING`，没有可执行计划增量前不创建 TransportTask 或 DeviceCommand |
| 准备 ACK 响应丢失 | WES 以原身份和原正文重试；WMS 返回首次接纳时的完整 `PREPARE_ACCEPTED` 响应 |
| 首个计划增量 | `plan_revision=1` 必须且只能定义一个初始目标窗口，可以同时新增来源成员；不携带增量类型或 WMS 计算进度字段 |
| WMS 分批完成资源运算 | 每批使用连续 `plan_revision`；WES 先持久化再 ACK，并立即执行本批已经满足前提的来源或目标运输 |
| 后续计划增量 | 禁止 `added_target_windows`；新窗口只由逐盘终局 `ACCEPT` 创建 |
| 计划版本跳号或冲突 | WES 停止自动推进并对账，不跨版本猜测、合并或覆盖已接纳成员 |
| 首批只有部分来源 | 已完整接纳的成员可以执行；WMS 内部计算进度不进入 WES 状态或执行门禁 |
| 首批期限到期仍无计划 | WES 以 `last_applied_plan_revision=0` 请求状态确认；WMS 返回带重试间隔的进行中状态或 revision 0 完成 |
| 新来源引用未定义目标窗口 | 拒绝该计划增量，不允许先取盘后补目标资源 |
| WMS 手工调整任务 | 通过 `queue_changed` 改变任务池业务优先序，不调用 WES 人工启动接口 |
| 多个退料货架或货架面 | 货架独占，SLOT 逐项执行；WES 按物理条件安排顺序 |
| 退料与五层货架同时就绪 | 退料直接取料优先，但不阻塞没有资源冲突的 CTU 和 Bin 流 |
| 五层货架可靠到位 | WES 按实际到达面预留具体入料位置，WMS 只从当前仍在冻结来源、且入站资格未消费的成员中返回原子批次 |
| 入站批次请求未被接纳或结果未知 | 确认未接纳时释放或原子转属位置；背压、未知和冲突时保持原 operation 预留并重试或对账 |
| 入站或退箱暂时无批次 | `NO_BATCH` 必须返回重试间隔；新事实可提前唤醒，否则以新 `operation_id + previous_operation_id` 重新请求 |
| 入站返回 `FACE_DONE` | 当前任务的该货架面永久封口；后续计划不得再追加该面 Bin |
| 出料口出现正常 Bin | WES 只提交 `RETURN_BUFFER` 实际候选；WMS 从当前货架面分配任意合法空储位形成退箱批次 |
| 当前货架面没有退箱空位 | WMS 返回 `NO_BATCH` 等待，或以 `RACK_PREPARATION_REQUIRED` 给出当前架去向、新架和目标面；WES 不自行寻找其他储位 |
| 退箱换架引入额外五层货架 | WMS 建立任务级退箱承接占用，并在清场终态、目标预留、退箱决定和位置事实全部闭合后释放 |
| CTU 乱序投箱 | 任一计划增量新增且未取消的候选 Bin 可按实际到达顺序在 SCAN2 请求工作计划 |
| FIFO 队首无工作 | WMS 返回 `NO_WORK` 后该 Bin 业务闭合并继续向后流转 |
| Bin 计划尚未产生 Cell | PickingTask 不因 Cell 集合为空而完成 |
| 7 寸有空位但大尺寸无容量 | WMS 给出新的目标货架或货架面窗口，WES 不计算容量 |
| 目标窗口切换 | 允许扫码台单盘预取；新货架或货架面可靠到位前禁止 PUT |
| 精确 SLOT 分配 | WMS 在料盘扫码后返回目标 SLOT，WES 只校验并执行 |
| 扫码后需要换面或换架 | `ACCEPT` 一次返回精确 SLOT 和完整目标准备方案；WES 创建 TransportTask，到位后直接 PUT，不重复物料决定 |
| PUT 三段闭环 | WMS `ACCEPT` 只授权目标，ECS `SUCCEEDED` 只证明物理放置，WMS `RECORDED` 才提交权威位置、库存和目标占用 |
| PUT 设备证据 | 位置 Fact 精确引用 DeviceCommand 和其确定结果；失败、超时或结果未知时不得上报已完成位置 |
| 两个机械臂并行 | 两个 `device_code` 可各有一条已接纳未终态命令；ECS/PLC 硬件锁负责扫码台交接与防撞，WES 不等待平台释放事件或建立资源锁 |
| PUT 尚未完成但 WMS 返回 `CONTINUE` | 来源机械臂可接纳下一条命令；无安全暂存位时，ECS/PLC 必须在料盘离开来源前取得扫码台交接许可 |
| 换架时当前架存在短暂依赖或当前无安全目标 | 分别返回 `WAIT / TARGET_RACK_DRAINING` 或 `WAIT / TARGET_RACK_UNAVAILABLE`；事实变化后立即重新求值，WES 本地超时只暂停和告警 |
| Material NG | 完整扫码和来源绑定正确，WMS 返回 `MATERIAL_REJECTED`；只隔离当前盘 |
| Cell NG | WMS 返回 `SOURCE_CELL_MISMATCH + CLOSE`；当前盘 NG 事实确认后关闭当前 Cell，同 Bin 其他 Cell 继续；Bin 到达 `NG_EXIT` 后以 `cause_scope=CELL` 只补充位置事实 |
| Bin NG | Bin 身份或可靠方向异常；整箱可靠到达 `NG_EXIT` 后以 `cause_scope=BIN` 形成 NG Fact；未匹配物理 Bin 同时引用预期计划成员，但不自动关闭它 |
| 空取、扫码不完整、设备失败或未知 | 不得升级为 NG，分别进入空取、重试、暂停或对账流程 |
| 执行中追加 Bin | 更高 `plan_revision` 创建新的 `bin_work_execution_id`，不改写既有成员，也不直接追加未扫码 Bin 的 Cell |
| 取消尚未取盘的 Bin | 停止新取盘动作，让已进入物流线的 Bin 正常退回；业务成员以 `CANCELLED` 闭合 |
| 取消时当前盘已取出 | 当前盘不可放回，先闭合到目标或 NG 并提交位置事实，再取消该 Bin 剩余工作 |
| 取消到达时 Bin 已终态 | 接纳重复取消并推进 revision，不回退终态或生成补偿 |
| 状态确认与增量竞态 | 状态确认携带 `last_applied_plan_revision`；WMS 发现更高版本时返回 `NOT_COMPLETED` 并补发增量 |
| 任务业务状态完成 | WMS 根据既有逐盘交互返回 `COMPLETED`，不要求 WES 对账成员全集，也不等待货架、Bin 或工作线清场；既有退箱和清场请求仍可闭合 |

## 19. 当前评审状态

本文保持 `ReviewRequired`，直到 PickingTask 字段合同和相关公共合同共同批准。具体 DTO、枚举、错误、超时和 fixture 以
`docs/contracts/wms-outbound-picking-task-integration-requirements.md` 为准。
