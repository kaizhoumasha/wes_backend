---
title: WES 出库操作顶层设计
status: ReviewRequired
created_at: 2026-08-06
updated_at: 2026-08-20
scope: SMT 自动出库 PickingTask 与人工分拣 Bin 流转；分批资源计划、CTU 批次、FIFO 缓存、业务完成与物理清场边界
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

## 1. 这份文档怎么读

本文说明自动出库 PickingTask 和人工分拣出库的业务与物理流程，以及 WMS、WES 和设备分别负责什么。WMS 负责库存、
来源和目标分配；WES 负责选择工作线、组织现场执行和保存设备结果。出库单、波次、人工子任务和库存策略仍由 WMS 管理。

负责 WMS C# 接口开发时，具体 URL、字段、枚举和错误码请以
[WMS / WES 自动出库 PickingTask 交互要求](../../contracts/wms-outbound-picking-task-integration-requirements.md)为准。
该合同只定义自动出库 wire；人工分拣的字段和 operation 尚未冻结。本文用于理解流程，不是第二份字段定义。

本文状态是 `ReviewRequired`，表示双方还需要共同确认，不表示功能已经完成。

几份相关文档各自负责以下内容：

- `docs/architecture/SRS.md` 记录产品需求和参与方职责。
- 最小执行架构 SPEC 说明 WES 如何保存任务、运输和设备结果。
- 本文第 2～19 节说明自动出库流程；第 20 节单独说明人工分拣的 Task 入站和 Bin 退料边界、状态限制与验收场景。
- `docs/contracts/wms-outbound-picking-task-integration-requirements.md` 定义自动出库 operation、method、path、请求与响应字段、错误和
  重复提交规则；WMS 对接通用合同只定义 `WmsClient` 的 HTTP/JSON 访问边界。
- Master Plan 和实施计划只规定实现顺序，不能改变接口字段和业务规则。
- `docs/hardware/` 保存厂商原始资料，不作为当前 WMS/WES 接口定义。

系统尚未发布，只实现当前文档中的接口，不同时保留旧接口或字段别名。

## 2. WMS 开发人员需要知道的流程

- WMS 先发布 `PickingTask`，此时不分配货架、Bin、Cell 或目标储位。WES 选好任务和工作线后发送准备请求。
- WMS 收到准备请求后先返回 `PREPARE_ACCEPTED`，再按连续的 `plan_revision` 分批发送计划。WMS 每算出一批就可以发送，
  不必等整张任务全部算完。
- `plan_revision=1` 必须带 `target_rack`。这个字段表示当前允许接料的转运货架和货架面，不是具体 SLOT。
  后续计划只新增来源。执行中换面或换架时，由逐盘 `ACCEPT` 返回新的精确目标和货架准备方案。
- 货架动作和离场去向不进入计划增量。五层货架到位后，WES 把本批最大可取数量发给 WMS，WMS 选择 Bin；正常 Bin 到达
  `RETURN_BUFFER` 后，WMS 根据 WES 提交的 FIFO 候选分配退箱目标。
- WES 内部把直接取料记录称为 `DirectPickExecution`，把 Bin 工作记录称为 `BinWorkExecution`。这些是 WES 内部对象，WMS 不传对应执行 ID。
  Cell 在 Bin 实际到达 SCAN2 后创建。CTU 可以乱序投箱，
  `WORK_BUFFER` 保持单向 FIFO。
- 来源货架由当前工作线独占。退料直接取料优先，但不阻塞无资源冲突的 Bin 流。
- WMS 负责转运货架容量、规格兼容和目标分配。逐盘扫码后，WMS 返回物料资格、精确 SLOT 和可选目标准备方案；WES 只验证并
  执行已授权目标。
- 两个机械臂按不同 `device_code` 推进。ECS/PLC 硬件锁负责扫码台交接、防撞和动作互锁，并在没有安全暂存位时守住料盘离开
  来源的不可逆动作；WES 不建立扫码台事件、资源锁或跨机械臂软件互锁，也不以当前 PUT 完成作为另一机械臂开始下一条命令的
  业务前提。
- WMS 可以使用更高 `plan_revision` 发送当前任务尚未发布的来源，但不能修改已经接收的明细，也不能替换空取、NG 或因 Transport
  确定失败而结束的任务明细。`inbound_batch` 返回 `READY` 后，WMS 不能撤销或改选其中的 Bin。Bin 到达 SCAN2 时如果已经没有
  取料需求，WMS 通过 `work_plan NO_WORK` 结束该 Bin 的业务工作。
- 同一工作线只允许一张任务处于 `PREPARING | EXECUTING`；不同就绪工作线可以并行执行。人工调整通过 `queue_changed` 进入
  正常队列，WES 不提供独立人工启动入口。
- WES 完成本地取料工作和位置上报后，携带 `last_applied_plan_revision` 请求 WMS 确认任务状态。WMS 返回
  `COMPLETED | PLAN_REVISION_STALE | BUSINESS_IN_PROGRESS`，不接收明细完成全集，也不等待货架、Bin、Transport 或工作线现场清理。
  `COMPLETED` 只表示当前任务明细都已处理完。没有满足的需求由 WMS 创建新的 PickingTask。

## 3. 范围与非目标

### 3.1 范围

- PickingTask 发布、队列更新、工作线选择、准备响应、分批计划、执行中追加来源、任务状态确认。
- 多个五层货架、候选 Bin、多个退料货架及货架面的来源执行。
- `DirectPickExecution`、`BinWorkExecution`、`CellExecution` 和逐盘 `MaterialExecution`。
- CTU 乱序投箱、三段缓存、单向 FIFO，以及缓存已满时的等待处理。
- 退料优先取料、Bin 工作位 SCAN2、Cell 计划，以及扫码后再决定每盘物料的目标。
- 转运货架换面、换架、精确目标 SLOT 和已确认位置结果。
- 料盘、Cell、Bin 三类 NG、正常计划增量、空取、Transport 确定失败和未知结果。

### 3.2 非目标

- WES 订单管理、波次计算、库存选择、库存锁策略或跨任务来源冲突处理。
- WES 计算转运货架容量、尺寸兼容或替代目标货架。
- WES 规划 AGV、CTU 路线或 PLC 内部动作。
- 用 PickingTask 状态承载 Rack、Bin、Transport、设备命令或现场清理状态。
- 跨任务合并、全局路径优化、自动改计划或兼容旧接口。

## 4. WMS、WES 和设备各自负责什么

| 系统 | 负责的数据和决定 | 不负责 |
| --- | --- | --- |
| WMS | PickingTask、业务顺序、库存、来源占用、入站 Bin 选择、转运货架容量、目标分配、物料是否合格、退箱目标和任务状态 | 设备动作、滚筒线位置、CTU 可用数量和本地安全互锁 |
| WES | 选择可执行任务和工作线、检查计划版本、保存现场位置和设备结果、组织运输和设备动作 | 重新计算库存、替换来源、计算容量或选择目标储位 |
| RCS/AGV/CTU | 货架与 Bin 搬运、路径、排队和运输最终结果 | PickingTask、库存和物料资格 |
| ECS/PLC/设备 | 扫码、抓取、放置、输送、到离位、安全互锁和设备最终结果 | 任务顺序、库存和目标业务分配 |

WES 可以按本文请求 WMS 给出来源、目标和任务状态，但不能自行产生业务优先级、库存分配或人工业务操作。

## 5. WES 内部对象，WMS 不需要实现

| WES 对象 | 用来记录什么 | 不负责什么 |
| --- | --- | --- |
| `PickingTask` | 一张 WMS 任务以及已经接收的直接取料和 Bin 工作明细 | Rack、物理 Bin、Transport、设备和现场清理状态 |
| `DirectPickExecution` | 一个 WMS 指定的退料货架 SLOT | 货架搬运过程和其他 SLOT 库存 |
| `BinWorkExecution` | `inbound_batch` 已选中 Bin 的取料工作，以及 SCAN2 后返回的 Cell 列表 | 滚筒线位置、退箱和 CTU 结果 |
| `CellExecution` | 管理一个 `BIN_CELL` 的逐盘循环和完成结果 | Bin 搬运和目标架运输 |
| `MaterialExecution` | 一盘物料的扫码数据、设备动作和位置上报 | 来源选择、任务汇总和容量计算 |
| `BinExecution` | 跟踪已经可靠到达工作线的 Bin，经 SCAN1、FIFO、SCAN2、SCAN3，直到正常回库或 NGZone人工接管 | PickingTask 业务完成和入线前搬运 |
| `TransportTask` | 跟踪货架或 Bin 搬运、接收确认、异步最终结果和未知结果 | PickingTask 和物料业务状态 |
| `DeviceCommand` | 跟踪一次设备命令、接收确认、结果、超时时间和重复提交记录 | WMS 业务决定 |
| `WmsConfirmation` | 记录已经成功提交的位置变化和任务状态确认请求 | 重新执行设备动作或修改执行对象 |
| `PositionProjection` | WES 保存的作业期空闲、预留、占用、确定位置或位置未知；活动 TransportTask表达搬运中，不伪造在途位置 | WMS 库存和转运货架容量数据 |

`BinWorkExecution` 记录这个 Bin 的取料业务是否完成，`BinExecution` 记录物理 Bin 在哪里。两者都使用同一个 `bin_id` 关联，
WES 内部各自保存独立记录 ID 和状态。内部记录 ID 不进入 WMS/WES 接口。

当前 CTU/RCS 只能返回完整最终到位结果，不提供可靠取箱中间事实。WMS冻结精确供给 Bin 后，WES先创建 TransportTask；只有
`transport.task.resulted@v1` 最终结果确认 Bin成功到达 `HANDOFF_POSITION`，且现场扫码身份匹配后才创建 `BinExecution`。正常回库并由
WMS记录主账后关闭；NG Bin到达整线 `NGZone` 后等待人工扫码取走再关闭。该物理生命周期不因 PickingTask 完成、取消或失败而删除。

## 6. PickingTask 发布与队列

`PickingTaskIssued` 只携带任务编号和排队信息：

| 字段 | 含义 |
| --- | --- |
| `task_id` | PickingTask 的唯一编号，发布后不再改变 |
| `dispatch_sequence` | WMS 提供的自动出库任务池业务优先序 |
| `not_before` | 可选最早启动时间 |

发布阶段禁止携带：

- `workline_code`、`station_code` 或其他具体执行线字段。
- 来源货架、Bin、Cell 或退料 SLOT。
- `PkgID`、六合一码、料盘数量或料盘顺序。
- 目标转运货架、目标面、目标 SLOT 或容量字段。
- TransportTask、DeviceCommand 或缓存状态。
- 来源占用信息。

WES 接收任务后进入 `QUEUED`。任务发布固定建立 `queue_revision=1`，后续队列更新必须连续递增。WES 返回成功只表示任务已经保存，
不表示货架、Bin 或目标已经分配。

同一任务的队列更新按 revision 依次发送。前一个 revision 没有得到明确成功响应前，WMS 不能发送下一个 revision；响应不明确时
只重发原消息。

所有 `QUEUED` 任务进入同一个自动出库任务池，`dispatch_sequence` 在池内唯一。WES 先过滤未到 `not_before`、已被领取或当前
没有工作线满足启动条件的任务，再选择优先序最小的可执行任务；暂时不可执行的前序任务不阻塞后续可执行任务。

多条分拣机工作线具有相同物理结构和各自关联的 STATION。WES 从无活动任务，且设备、工作位、缓存和 Transport 状态都允许启动的
工作线中，按本地 `available_since ASC → workline_code ASC` 稳定选择。任务领取和候选工作线保留在一个事务中完成。WMS 手工
调整只通过 `queue_changed` 修改业务优先序或 `not_before`；本阶段不增加 WorkLineGroup、能力标签、评分引擎或人工启动入口。

## 7. 异步准备与分批计划

### 7.1 准备请求

WES 在同一事务中领取当前最高优先级的可执行任务并保留候选工作线后，生成并持久化准备请求的 `operation_id`，把任务迁移为
`PREPARING`，然后调用 `outbound.picking_task.prepare@v1`。请求中的 `workline_code` 由 WES 提供，WMS 据此使用该线关联的
STATION、货架工作位和交接位置计算资源。人工操作不能绕过该路径。

WMS 保存请求后立即返回 `202 PREPARE_ACCEPTED`。这个响应只表示 WMS 已经接收资源计算请求，不表示已经算出可执行数据。请求响应
未知时，WES 使用原 `operation_id` 和原请求正文重试；WMS 返回首次接收时的完整 `PREPARE_ACCEPTED` 响应，不改写时间戳或
改为 `DUPLICATE`。WES 不能释放候选工作线、换线或创建第二次准备请求。

### 7.2 计划增量

WMS 通过 `outbound.picking_task.plan_delta@v1` 分批回调。每个回调使用自己的 `operation_id`，并携带同一
`task_id` 下严格连续的 `plan_revision`。WMS 内部可以并行计算，但对同一任务必须按 revision 串行发布；
前一版本没有得到明确成功响应前，不得发布后一版本。

计划增量可以包含：

- `target_rack`：仅 `plan_revision=1` 携带，表示初始接料货架和货架面。
- `added_direct_picks`：用 `task_id + source_locator` 唯一识别一条直接取料明细，并指定退料货架、货架面和 SLOT。
- `added_bin_source_racks`：用 `task_id + rack_id + rack_face` 唯一识别一个五层来源货架面，不提前携带 Bin。

`added_bin_source_racks[]` 按货架面记录。同一货架的 A、B 面都有当前任务需要取出的 Bin 时，必须记录两项；只用于承接退箱的货架面
不属于来源计划。两个面可以在同一 `plan_revision` 中发布，也可以在计算完成后通过连续的更高版本追加。

`plan_revision=1` 是唯一初始增量，必须且只能定义一个初始接料货架面。后续 revision 都是普通增量并禁止
`target_rack`；扫码后需要的新接料货架和货架面，只能由同一盘最终 `ACCEPT` 在同一业务决定中定义。首个增量可以只包含初始接料货架；
后续增量至少新增一个来源明细。新增明细和初始接料货架不可变，已有明细不能通过高版本改来源或目标。
WMS 内部计算进度不进入计划增量。计划增量只追加当前任务尚未发布的正常计划数据；空取、NG 或 Transport 确定失败形成的需求缺口
由新的 PickingTask 承接，不能通过当前任务的更高版本补单。

### 7.3 WES 收到部分计划后何时可以执行

WES 先保存完整回调，再在一个事务中校验并应用计划，最后返回响应：

- `operation_id` 和请求正文都相同，返回 `DUPLICATE`。
- `plan_revision` 等于当前版本时，只允许内容完全相同的重放。
- `plan_revision` 必须等于当前版本加一；跳号、倒退、同版本不同内容或明细唯一字段组合冲突时，WES 停止自动执行并转人工核对。
- `plan_revision=1` 必须且只能携带一个初始接料货架面；后续新增来源时，任务必须已有有效接料货架面。由物料 `ACCEPT` 创建的新接料货架面
  至少被一条单盘位置上报确认后，才能供下一盘使用，避免同步决定和计划回调顺序冲突。

只要某一条来源明细和当前接料货架面都已收到，WES 就可以先执行这一部分，任务进入 `EXECUTING`。
WES 不需要等待其他货架或整张任务全部计算完成。WES 根据自己保存的已确认位置组织货架进场。只有接料货架面时可以先搬目标架，
但没有来源明细时不能开始取料。

计划增量只表达业务资源，不携带 TransportTask、DeviceCommand 或 CTU 搬运批次。货架离场去向仍在现场依赖完成后由 WES 请求
WMS 决定。WMS 若尚不能形成可执行资源，WES 通过第 15 节状态确认取得 `BUSINESS_IN_PROGRESS`，不观察 WMS 内部计算阶段。

## 8. 来源占用与货架独占

来源分配以 WMS 为准：

- 每个已授权五层货架和退料货架在任务执行期间由当前工作线独占。
- 五层来源货架面以 `task_id + rack_id + rack_face` 唯一识别，已经选入批次的 Bin 以 `task_id + bin_id` 唯一识别，直接取料来源使用
  `task_id + source_locator`。
- 同一退料货架即使只使用一个 SLOT，也不能同时分配给其他工作线。
- 单个直接取料完成并且位置结果已接收后，WMS 可以释放该 SLOT 的库存占用。
- 货架仍有未完成来源，或者还没有确认离开工作位时，WMS 不能解除整架占用。
- 锁不按沉默时长自动释放。未知结果必须通过原操作重试、人工核对或人工处置完成。
- 当前工作货架在实际到位期间由当前 WorkLine 独占。取出 Bin 后形成的空储位由 WMS 根据主账和业务资格分配给本 Epoch 的跨任务退箱 FIFO，不要求原任务或原货架面。

WMS 确认 PickingTask 完成后可以结束任务计划，但不能删除仍在 FIFO 或已冻结退箱搬运中的物理义务。CTU 不携带 Bin、没有未结束搬运或位置未知，且没有以当前面为已冻结目标的退箱时，
该货架面即可换面、换架或离场。

## 9. 并行运输与退料优先

WES 接收计划后，只为数据已经完整的来源创建 TransportTask。运输起点取自 WES 已确认的位置，目标取自 WorkLine
为货架角色配置的固定工作位；任一位置未知都禁止建任务。以下货架运输可以随对应增量分批、并行准备：

WES 出库业务模块在每次调用 Transport 前，在同一事务中保存当前业务步骤、完整 Transport 输入、新生成的 UUIDv7
`client_request_id` 和待补写的 `transport_task_id`。由 WMS 决定的搬运同时保存决定请求的 `operation_id`。同一业务步骤最多创建一项任务；
调用后崩溃只能以原 `client_request_id` 和原请求正文重新发送。

1. 当前目标转运货架到目标工作位。
2. 当前可执行退料货架到来源工作位。
3. 五层货架到 CTU 作业位。

每条 WorkLine 只有一台 CTU。入站和退箱共用一个串行通道，同一时刻最多有一个尚未结束的 WMS 批次请求或 CTU Transport。
自动出库业务模块使用一个“重新判断下一动作”的入口处理计划应用、缓存变化、重试到期和 Transport 结果。多个事件同时触发时，
业务模块在数据库事务中只允许一个事件声明下一动作。这不是缓存位预留、租约或 Transport 基础层锁。

计划可以一次提供多个五层来源货架面，但 WES 只选择一个当前来源面，不同时创建多条指向同一 CTU 工作位的货架任务。当前工作位上
尚未结束的计划来源面优先，其次是同一货架的另一计划面，最后按计划接收顺序选择其他货架面。同架换面使用 `RACK_ROTATE`；不同货架
必须先把旧架确定移出，再把新架移入。`RACK_MOVE` 已经带正确目标面时不再补一次换面。

每次重新判断时，先处理已经声明但尚未结束的动作。没有未结束动作且当前面能形成可执行批次时，`RETURN_BUFFER` 中的正常 Bin 具有最高 CTU 优先级。
WES 取 FIFO 队首的前 N 个，N 是 CTU 当前空闲背篓数和当前可取 Bin 数量的较小值。每个候选携带本次请求内从 1 连续递增的
`sequence_no`。WMS 为候选列表的连续前缀分配精确退箱 SLOT，并在响应中原样返回 `sequence_no + bin_id`，不能跳过队首。
所有目标 SLOT 必须位于当前工作位的 `rack_id + rack_face`，但不要求是原货架面或原储位。`return_batch` 不触发换面或换架。`NO_BATCH` 表示 WMS 本次不能为 FIFO 队首
分配当前面的合格空位；候选继续等待，但不阻止无资源冲突的入站需求推动换面或换架。只有身份、位置、预留或幂等性事实矛盾才返回 `409 / CONFLICT`。

没有可执行退箱批次时，WES 考虑入站。当前物理货架面必须是计划中尚未结束的来源面。WES 计算
`max_bin_count=min(CTU 当前空闲背篓数, INGRESS_BUFFER 当前空闲位置数)`，再调用 `outbound.bin.inbound_batch@v1`。WMS 返回
`bin_id + source_locator` 后，WES 补充本地入料目标并创建 `BIN_MOVE`。`NO_BATCH` 保持当前来源面开放；`RACK_FACE_DONE` 只关闭该面
后续选 Bin 的资格，不表示货架可以离场，也不表示先前选中的 Bin 已经完成。来源面结束后，WES 只能从计划已经包含的其他来源面中
选择下一面；但必须先确认 CTU 不携带 Bin、没有未结束搬运或未知位置，且没有以当前面为冻结目标的退箱决定。已可靠进入
`RETURN_BUFFER` 的 Bin 不阻塞切换。同架换面使用 `RACK_ROTATE`，不同货架先移出旧架、再移入新架。
`inbound_batch` 不返回新的来源货架方案。

WMS 返回 `READY` 后，这次业务决定不能再修改。只有对应 Transport 确定成功且完整最终位置已经保存，本批次才完成。入站或退箱完成后都重新判断
下一动作。CTU 仍携带 Bin、存在未完成 Bin 搬运、位置不明确，或存在以当前面为冻结目标的退箱决定时，禁止货架换面和换架。已可靠进入 `RETURN_BUFFER` 且尚未冻结目标的 Bin 可跨面等待，不再锁定原来源面。

正常运行时只有新入站需求驱动换面或换架。停止或切换已请求时，目标合同允许 WMS 为排空既有 FIFO 选择有合格空位的货架面；但候选 `workline.return_buffer.drain_rack_decide@v1` 的完整合同尚未获批，当前为 `ReviewRequired/BLOCKED`。获批前 WES 停止接纳新任务和新 Bin，Epoch 保持 `ACTIVE`，不创建货架切换或退箱 Transport。全部清场义务闭合后才关闭 Epoch。

多个退料货架或多个货架面不形成一条由 WMS 下发的机械队列。WMS 给出来源 SLOT，WES 根据退料优先规则、工作位、运输最终结果
和当前目标货架面选择业务执行顺序，并优先连续执行同一货架和货架面，减少换面和换架。

退料直接取料具有调度优先级，但不是全局屏障。目标货架、退料货架和五层货架满足各自条件后可以并行推进。两个机械臂按不同
`device_code` 各自维持至多一条已接收尚未得到最终结果命令；扫码台交接、防撞和动作互锁由 ECS/PLC 硬件锁完成，WES 不建立扫码台
资源锁、跨机械臂锁或平台释放事件。

Transport 返回 `UNKNOWN` 时，WES 暂停依赖这个结果的任务明细，并保留相关资源。WES 不自动重发搬运请求，也不创建替代
TransportTask。WMS/RCS 取得确定结果或完成人工核对后，通过 `transport.task.resulted@v1` 为同一 `transport_task_id` 发送更高版本的
结果。搬运完成且位置明确时返回 `SUCCEEDED`；搬运失败但位置明确时返回 `FAILED`；位置仍不明确时继续返回 `UNKNOWN`。

Transport 返回 `REJECTED | FAILED` 后，WES 根据本地保存的任务明细与 `transport_task_id` 对应关系，只结束确定失败的明细。已经成功和
不依赖该 Transport 的明细继续执行。WMS/RCS 产生并发送 Transport 结果，因此 WMS 已经知道哪些货架或 Bin 失败。WMS 统计没有满足的
需求并创建新的 PickingTask，不需要 WES 再上报 Transport 失败，也不需要返回恢复方案。当前任务的后续 `plan_delta` 不能替换这些失败
明细。

Transport 请求中不传 `task_id`。WES 在自动出库模块中保存对应关系；WMS 根据任务分配、批次结果以及 Transport 中的货架或 Bin 编号
找到受影响的任务明细。无法确定对应关系时，双方转人工核对，不能结束任务明细或任务。

PickingTask 完成后的退箱或货架离场属于现场清理。清理失败只影响对应物理流程，不会重新打开 PickingTask。

## 10. 三段缓存与 FIFO

滚筒线包含以下位置角色：

- `INGRESS_BUFFER`：CTU 投箱缓存。
- `WORK_BUFFER`：等待位置和料箱工作位，单向 FIFO，总容量包含实际工作位。
- `RETURN_BUFFER`：正常完成 Bin 的退箱缓存。
- `NG_EXIT`：NG Bin 专用出口，不计入退箱缓存。

WES 为每个物理位置记录 `FREE | RESERVED | OCCUPIED | IN_TRANSIT | UNKNOWN`。区域占用量根据这些位置记录计算，不能再维护一个
业务计数器替代。

CTU 投箱先由 WES 计算可取数量，再由 WMS 返回具体 Bin。WES 补充当前空闲的 `INGRESS_BUFFER` 位置，形成一次完整入站搬运批次。
正常 Bin 只有已确认到达 `RETURN_BUFFER` 后才进入退箱候选；目标空储位由 WMS 从当前工作货架面分配。PLC 或 ECS
继续承担物理防撞和非空位置互锁，WES 保存的位置记录不是唯一安全措施。结果未知时，相关位置保持保守阻塞。

`WORK_BUFFER` 不能重排或绕行。CTU 可以乱序送达候选 Bin，但进入工作段后的队首 Bin 必须先取得一种明确结果：

- WMS 返回 Cell 工作计划。
- WMS 返回 `NO_WORK`。
- WES 根据已经确认的设备结果把 Bin 送往 NG 出口。

队首 Bin 未完成时，后续 Bin 保持 FIFO 等待。

## 11. Bin 工作计划

WES 接收 `outbound.bin.inbound_batch@v1` 的 `READY` 后，为每个新 `task_id + bin_id` 建立一条 `BinWorkExecution`，并保存 WMS 返回的
精确来源。Bin 实际到达料箱工作位后执行 SCAN2。

WES 先保存 Bin 编号和到位记录，再调用 `outbound.bin.work_plan@v1`。

WMS 根据 PickingTask、实际 Bin、已完成退料直接取料和剩余需求返回：

- `READY`：返回该 Bin 当前需要执行的非空 `cell_ids[1..N]`。
- `NO_WORK`：该 Bin 不再需要取料，`BinWorkExecution` 可以业务完成。
- `WAIT`：当前不能形成稳定计划，Bin 保持工作位占用。

一条 Cell 明细由 `task_id + bin_id + cell_id` 唯一识别。Cell 直接使用父 `BinWorkExecution` 已保存的来源，不重复保存。首版不定义
Cell 优先级或依赖图，WES 根据 FIFO、设备和位置状态安排实际 Cell 顺序。

相同 `task_id + bin_id` 的计划保存后不能修改。需要重新判断时，WES 创建新的 `operation_id` 并携带同一任务和 Bin。最终
`READY | NO_WORK` 只能有一个。

`BinWorkExecution` 在以下任一条件满足后完成：

- `READY` 计划中的全部 CellExecution 完成。
- WMS 返回 `NO_WORK`。
- 已关联候选 Bin 的 NG 出口到位结果被 WMS 接收，并关闭该业务明细。

物理 `BinExecution` 可以在此后继续退箱，不阻塞 PickingTask 业务完成。

## 12. 逐盘执行

`DirectPickExecution` 和 `CellExecution` 共用逐盘扫码与目标放置链路：

1. 来源机械臂从 WMS 指定的 `RACK_SLOT` 或 `BIN_CELL` 取出一盘物料。
2. 料盘进入扫码台，设备上报完整六合一码。
3. WES 持久化不可变扫码证据并调用 `outbound.material.decide@v1`。
4. WMS 返回 `ACCEPT | REJECT | WAIT`。`ACCEPT` 携带有效接料货架面和精确目标 `RACK_SLOT`；需要物理换面或换架时，附带
   `target_preparation`，其 `mode` 为 `ROTATE` 或 `REPLACE`。`REJECT` 携带明确的业务异常分类和 `source_disposition`；
   `WAIT` 只表示业务决定尚未形成。
5. 需要目标准备时，WES 保持当前盘占用扫码台，按固定顺序执行 `ROTATE → PUT` 或
   `CURRENT_RACK_DEPARTURE → NEXT_RACK_STARTUP → PUT`。Transport 成功结果和位置、货架面全部一致后，目标机械臂
   直接执行 PUT，不再次请求物料决定。
6. WES 把当前 `MaterialExecution` 和 WMS 已授权的接料货架面、精确 SLOT 写入目标机械臂 DeviceCommand 证据。只有匹配该命令的
   确定 `SUCCEEDED` CALLBACK 才表示 PUT 完成；接收确认、失败、超时和结果未知都不能形成位置结果。
7. 放置最终状态明确后，WES 更新本地已确认位置，并通过 `outbound.material.movement_report@v1` 同步提交来源位置、目标位置、前序 WMS
   决定和设备结果引用；WMS 返回 `RECORDED | DUPLICATE` 后，WES 才推进依赖该位置结果的容量释放、明细完成和任务完成。

逐盘 PUT 分为三步：“WMS 授权目标 → ECS 执行放置 → WMS 确认位置”。WMS 的 `ACCEPT` 不表示设备已经放置完成，
也不得把 ECS 的成功 CALLBACK 当作 WMS 库存已经更新。WMS/WES 接口字段以 PickingTask 合同为准；目标机械臂 `task_type`、逻辑
`params`、结果 `data`、时限和错误必须由该实际设备的获批合同附录定义，不能写入 WES 核心全局枚举。附录只承载已授权的逻辑
目标和设备证据，不承载库存、容量、替代目标、PLC 坐标或硬件互锁。

完整六合一码为 `HHPN`、`MfrPN`、`Qty`、`DateCode`、`LotCode`、`PkgID`。`PkgID` 是完整料盘的唯一业务编号；WES 不根据内容
判断物料资格，也不生成第二套料盘编号。

一个 `BIN_CELL` 可以连续产生多盘。WMS 在每盘扫码决定中返回 `next_source_action=CONTINUE | SOURCE_DONE`。`CONTINUE` 表示
业务需求允许继续从当前 Cell 取下一盘；`SOURCE_DONE` 表示当前盘完成后关闭来源，DirectPick 只能返回该值。它不是扫码台安全
许可：当来源机械臂没有活动命令时，WES 可以在当前盘 PUT 并行进行期间下发下一条来源命令。ECS 可以接收命令并执行不改变
料盘位置的准备动作；没有现场批准的安全暂存位时，硬件锁必须在料盘离开来源前确认扫码台交接路径可用，不能先取出下一盘再
持盘等待。WES 不等待扫码台释放事件，也不建立扫码台资源锁。

`DirectPickExecution` 对应一个退料 SLOT 和一盘物料。WMS 已经明确接受或拒绝，并且物理位置已经确定后，这条任务明细才算完成。

料盘一旦被来源机械臂取出就不可放回原储位或料格。扫码台最多承载一盘未决物料；WMS 超时、响应无法关联或 PUT 结果未知时，
当前盘继续占用扫码台，不把未知解释为 NG、完成或可继续；达到本地技术超时后暂停、告警并进入人工核对。
已经取得 `ACCEPT`，但目标还没有确认就绪时，`MaterialExecution` 使用本地 `WAIT_TARGET_READY`。这属于 WES 等待设备和货架，
不能改成 WMS 业务 `WAIT`，也不要求把当前盘放回来源。

## 13. 转运货架换面与换架

WMS 维护转运货架容量和规格兼容，并在允许来源执行前完成内部预留。WES 不维护以下数据：

- 7、13、15 寸储位总量或剩余量。
- 不同规格之间的兼容矩阵。
- 基于百分比、低水位或预测需求的换架阈值。

当前接料货架面由 `task_id + rack_id + rack_face` 识别。实际扫码结果需要新目标时，最终 `ACCEPT` 只为当前料盘返回精确
`rack_id + rack_face + slot_id` 和必要的货架准备方案，不改写其他未取料来源明细。

WES 只处理接料货架面变化：

1. 下一来源明细仍使用当前已到位货架和货架面时继续执行。
2. `rack_id` 相同且 `rack_face` 变化时，根据精确目标和固定工作位创建 `ROTATE` TransportTask。
3. `rack_id` 变化时，先按 WMS 给出的去向让当前目标架离场，再从 WES 已确认的位置把新目标架运到固定工作位。
4. 接料货架面和容量预留已经接收时允许预取并扫码一盘；新货架或货架面确认到位前不能 PUT。没有安全暂存位时，料盘能否
   离开来源并进入扫码台由 ECS/PLC 硬件锁决定，不由 WES 记录的扫码台状态或资源锁决定。

只有目标面确实变化时，WES 才创建 `ROTATE` TransportTask。

因此，7 寸储位仍有空位但大尺寸储位耗尽时，是否换面或换架由 WMS 的下一个接料货架面体现。WES 不从现场空位推导该决定。

精确目标 SLOT 在料盘扫码决定中返回。实际扫码物料需要换面或换架时，WMS 返回最终 `ACCEPT`、新的有效接料货架面、精确 SLOT
和完整 `target_preparation`；WES 保持当前料盘在扫码台并组织执行，目标就绪后直接 PUT。WMS 尚不能形成物料资格、精确目标，
或者当前架仍有正在完成的短暂依赖时，返回带重试间隔的业务 `WAIT`。

对已在扫码台的物料返回换架方案前，WMS 必须确认当前目标架可以立即离场：没有未确认 PUT、未接收位置结果、关联中的目标
机械臂动作，或仍必须使用该架和当前面的其他未完成来源。当前硬件没有安全暂存位，料盘又不能放回来源，因此不能下发不可执行的换架方案。只由
正在完成的 PUT、位置结果或机械臂动作造成短暂阻塞，或者当前目标架无法安全离场且 WMS 尚无可执行替代方案时，均返回
`WAIT`；相关状态有明确变化时立即重新判断。`retry_after_ms` 只在没有新业务数据时作为定时重试；资源等待不能解释为物料质量 NG。
WES 本地技术超时只能暂停、告警并进入人工核对，不能替 WMS 生成业务拒绝。

目标转运货架可以在 PickingTask 尚未完成时离场。只有以下条件都满足时，WES 才通过
`outbound.rack.departure_decide@v1` 请求并执行该货架的离场去向决定：

- 没有未完成来源明细继续使用该货架和货架面。
- 已放入该货架的逐盘位置结果已被 WMS 接收。
- 目标机械臂和工作位没有与该货架关联的未决动作。

WMS 在离场 TransportResult 确认货架已离开工作位并到达决定位置后释放该货架业务占用。

## 14. NG、空取与计划变更

NG 按物理影响对象分为三层，不能把技术等待、资源不足或结果未知统称为 NG：

| 影响范围 | 唯一判定 | 处理 |
| --- | --- | --- |
| `MATERIAL` | 完整六合一码和来源绑定正确，但 WMS 返回 `MATERIAL_REJECTED` | 只把当前盘放入 NG 区，来源按 WMS 的 `CONTINUE \| CLOSE` 处理 |
| `CELL` | 完整物料身份与 WMS 保存的 Cell 绑定冲突，WMS 返回 `SOURCE_CELL_MISMATCH + CLOSE` | 当前盘进入 NG 区，位置确认后关闭当前 Cell；同 Bin 其他 Cell 继续，Bin 最终进入 NG 出口 |
| `BIN` | Bin 条码经允许重试后仍不可读、设备明确报告方向错误，或 WMS 已给出稳定业务 NG 决定 | 整个 Bin 停止取料并进入 NG 出口 |

空取是指设备按计划到来源位置取料，但可靠结果表明该位置没有料盘。六合一码未读全、设备失败、WMS `WAIT`/超时、目标换架等待和
Transport/PUT 结果未知都不是 NG。一次 Bin 读码不完整也不是 NG；只有按设备合同完成允许的读取重试，并明确确认“无法读出合法 Bin 编号”时，才是
`BIN_CODE_UNREADABLE`。

MATERIAL/CELL NG 继续使用单盘移动结果上报。WMS 根据前面的物料决定和 `source_locator` 判断影响范围。Bin 已确认到达 `NG_EXIT` 后统一使用
`outbound.bin.ng_exit_report@v1`：`reason_code=SOURCE_CELL_MISMATCH` 只补充 CELL NG 后的 Bin 最终位置，不扩大业务影响范围；
其他 Bin 原因才允许关闭或补充 BinWork。结果上报的 `operation_id` 用于重复提交保护，设备证据留在 WES；路由结果未知时不得上报。

SCAN1 或 SCAN2 发现无法识别或明确需要隔离的 Bin 时，只隔离该物理 Bin 并告警，不得把预期 Bin 当成实际扫码身份，也不得直接
关闭业务明细。WES 使用本次 `inbound_batch` 选中的 `expected_bin_id` 关联受影响来源；预期 Bin 后续实际到达 SCAN2 时，仍由
`work_plan` 返回 `READY | NO_WORK | WAIT`；因 Bin NG 产生的库存需求缺口由新的 PickingTask 承接。

若实际 Bin 可识别但不是本批预期 Bin，它不是 NG：WES 保存 `expected_bin_id + actual_bin_id` 和位置证据，不请求工作计划、不以实际 Bin 替代计划成员。
WES 将实际 Bin 冻结在当前安全位置，等待独立恢复 wire 获批；现有 `return_batch` 不能授权它进入 `RETURN_BUFFER` 或自动回库。

设备对退料货架 `RACK_SLOT` 或 `BIN_CELL` 返回已确认空取结果时，WES 保存来源观察结果，再调用空取决定接口，请求 WMS 返回
`RETRY | WAIT | SOURCE_DONE`。`SOURCE_DONE` 关闭当前任务中的 DirectPickExecution 或 CellExecution；即使因此仍有未满足需求，也由
新的 PickingTask 承接。关闭 Cell 后，由父 BinWorkExecution 重新计算自己的状态。

来源处理遵循以下规则：

- `RETRY` 只允许再次取原 `source_locator`；`WAIT` 只表示 WMS 对当前空取事实还不能形成稳定决定，不能用来等待替代来源。
- `SOURCE_DONE` 关闭当前任务中的 DirectPick 或 Cell。空取和 NG 造成的需求缺口由 WMS 创建新的 PickingTask，不通过当前任务的
  `plan_delta` 替换来源。
- 正常计划增量仍可追加当前任务尚未发布的直接取料来源或五层来源货架面；已开放五层货架面的新 Bin 由后续 `inbound_batch` 返回。
  计划增量不能直接追加尚未经过 SCAN2 的 Cell；Cell 仍由对应 Bin 工作计划创建。

`inbound_batch` 返回 `READY` 后，所选 `bin_id` 不再撤销或改选。Bin 到达 SCAN2 时，WMS 必须通过 `work_plan` 返回
`READY | NO_WORK | WAIT`。`READY.cell_ids[]` 首次接收后同样不可撤销、删减或改写；后续只能通过既有逐 Cell、空取、NG 和结果确认流程
处理完。计划增量只负责追加当前任务尚未发布的来源。

## 15. 状态与完成确认

### 15.1 PickingTask 状态

| 状态 | 不变量 | 迁移 |
| --- | --- | --- |
| `QUEUED` | 已入队，尚未发起准备请求 | `PREPARING` |
| `PREPARING` | WMS 已接收资源计算请求；没有任何一条来源数据完整时，不创建设备或运输动作 | `EXECUTING`、`EXECUTION_COMPLETED` |
| `EXECUTING` | 已接收至少一个计划版本；更高 `plan_revision` 只能追加来源，不能改写已接收明细 | `EXECUTION_COMPLETED` |
| `EXECUTION_COMPLETED` | WMS 已确认 PickingTask 完成，不代表现场物流对象都已处理完 | 无 |

同一 WorkLine 当前任务处于 `PREPARING | EXECUTING` 时，不允许把该线后继任务迁移为 `PREPARING`；其他就绪 WorkLine 仍可按
任务池优先序领取可执行任务。

### 15.2 状态确认条件

WES 只有在以下条件全部满足时才能发起状态确认：

- 当前任务所有已接收明细都有处理结果：成功、已确认 NG，或确定无法完成。
- WES 没有待执行的本地工作。`UNKNOWN/RECONCILING` 和设备结果未知表示任务明细还没有处理完。
- 所有必须上报的逐盘位置结果都已被 WMS 确认。

WES 只判断当前是否还有待执行的本地工作，不重新枚举历史 `DirectPickExecution`、`BinWorkExecution` 或
`CellExecution` 结果。WMS 已经通过逐盘位置确认、空取决定、NG 结果和需求状态持有业务完成依据。

`PREPARE_ACCEPTED` 后超过双方配置的首批期限仍没有计划增量时，WES 使用 `last_applied_plan_revision=0` 请求相同状态确认；WMS
可以返回同为 revision 0 的 `BUSINESS_IN_PROGRESS + retry_after_ms`，或在业务已经完成时返回 `COMPLETED`。这不创建虚构计划版本。

任务完成不读取：

- 来源货架或目标转运货架是否已经离位。
- 物理 Bin 是否已经回到五层货架。
- AGV、CTU、缓存或工作位是否已经清空。
- 下一任务是否已经满足本地启动条件。

每盘物料的位置结果必须先被 WMS 同步确认为 `RECORDED | DUPLICATE`。随后 WES 通过
`outbound.picking_task.completion_confirm@v1` 携带 `last_applied_plan_revision` 请求同步状态确认：

- `COMPLETED`：版本一致，而且当前任务所有已接收明细都已处理完；WES 迁移为 `EXECUTION_COMPLETED`，WMS 不再发布该任务的计划。
- `PLAN_REVISION_STALE`：WMS 当前版本更高，返回 `current_plan_revision` 并重新投递缺失增量。
- `BUSINESS_IN_PROGRESS`：版本一致，但 WMS 中的任务还没有完成；返回 `retry_after_ms`，WES 等待新业务数据或到期后重新确认。

请求不携带明细结果、完成数量或本地完成时间。`last_applied_plan_revision` 只用于比较计划版本，不用于逐条核对完成明细。请求由
`WmsConfirmation` 管理，任务状态只根据 WMS 同步决定迁移。

`COMPLETED` 只表示当前 PickingTask 已结束，不表示原订单或波次需求全部满足。PickingTask 不设置 `FAILED` 状态。空取、NG 和确定的
Transport 失败只结束受影响的任务明细，其他明细继续。WMS 根据自己已经保存的业务结果和 Transport 结果统计没有满足的需求，并使用
新的 `task_id` 创建 PickingTask。新任务的 `plan_revision` 从 `1` 开始。

### 15.3 工作线释放

下一任务能否启动，需要单独检查当前 `LineRunEpoch` 下的设备、工作位、缓存、WES 已确认位置和活动 Transport。PickingTask 完成不能绕过
现场资源门，也不需要增加承载所有动态状态的通用 Session。

`COMPLETED` 只关闭业务计划和明细变更。已经进入物流线的 Bin 仍可继续请求退箱，任务相关货架仍可继续请求离场；WMS 必须基于
任务完成时的数据和占用处理这些请求，不能仅因任务已完成返回状态冲突。离场请求不得重新打开任务或追加业务明细。

## 16. WMS 交互面

### 16.1 ID 所有权摘要

| ID 层级 | 生成方 | 代表什么 |
| --- | --- | --- |
| `operation_id` | 当前 WMS/WES 交互的发起方 | 一次请求、异步消息或结果上报的唯一 ID，用于重复提交保护 |
| `task_id`、`bin_id`、`cell_id`、来源位置、`rack_id` 和 `rack_face` | WMS | 唯一识别 PickingTask、业务明细、五层来源货架面和接料货架面 |
| `PkgID` | 扫码设备，WMS 判断业务唯一性 | 完整料盘的唯一业务编号 |
| `client_request_id` | WES 自动出库业务模块 | 调用 Transport 的稳定重复提交号；与 WMS 决定 `operation_id`、执行阶段和完整输入在同一事务中保存 |
| `transport_task_id` | WES Transport 服务 | 一个搬运任务 |

Transport 提交与其他 WMS/WES 消息一样使用顶层 `operation_id`：WES Transport 在首次形成不可变提交时生成并持久化，安全
重提保持原值；WMS 的每条 Transport 结果回调使用自己的新 `operation_id`，通过 `transport_task_id` 关联任务。业务 JSON 不使用
`event_id` 或 `request_id`，前序引用字段只引用已有 ID，不再生成另一套同义 ID。完整字段级生成方、用途、
不可变和重试规则以
[WMS / WES 自动出库 PickingTask 交互要求](../../contracts/wms-outbound-picking-task-integration-requirements.md#6-id-和版本由谁生成)为准；
Transport 内部 ID 以 [Transport 履约合同](../../contracts/transport-fulfillment-contract.md#411-transport-id-所有权)为准。

### 16.2 API 端点

端点固定为：

| 发起方 | 接收方 | 路径 | 用途 |
| --- | --- | --- | --- |
| WMS | WES | `POST {{WES_BASE_URL}}/api/v1/wms/events` | 发布任务、更新队列和分批发布正常计划增量 |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/decisions` | 准备请求先同步返回 `PREPARE_ACCEPTED`，计划随后异步发送；其他业务请求在 HTTP 响应中直接返回决定 |
| WES | WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/facts` | 同步提交 Bin NG 出口到位和逐盘位置结果，不使用结果回调 |

所有消息使用顶层 `operation_id`。同步响应原样返回请求 ID；每条异步消息都生成自己的 ID。同一不可变消息的重试保持 ID 和请求正文
不变；每批计划增量使用自己的 `operation_id`，并由
`task_id + plan_revision` 建立顺序。业务对象直接使用 `task_id`、`bin_id`、`cell_id`、位置和 `PkgID` 表达，不增加同义消息 ID。

Transport 请求和结果继续遵循独立 Transport 合同。PickingTask operation 只提供业务决定，不能直接修改 TransportTask
状态或伪造货架、Bin 到位结果。

## 17. 阶段与验收所有权

| 所有者 | 验证范围 |
| --- | --- |
| HTTP 基础能力 | 请求发送、超时、重复提交、异步发送记录和结果未知处理，不使用出库业务证明基础能力 |
| WMS 对接合同 | 固定 operation、数据格式、错误、重复提交和 WMS 业务结果解释 |
| 自动出库模块 | PickingTask、直接取料、单 CTU 批次串行与数量计算、Bin 工作计划、Cell 循环、FIFO、冲突处理和完成条件 |
| Transport 合同 | 执行自动出库模块提交的完整货架或 Bin 搬运、换面、换架，并返回异步最终结果；不理解 PickingTask、FIFO 或批次数量 |
| 设备统一接口 | 公共设备请求格式、命令、接收确认、回调和结果 |
| 供应商验收 | 具体扫码、机械臂、滚筒线、PLC 和设备行为 |

基础能力测试不能用业务插件通过来代替，业务插件测试也不能反向证明基础设施正确。

## 18. 最低验收场景

| 场景 | 通过标准 |
| --- | --- |
| WMS 发布 PickingTask | 请求正文不含 `workline_code`；WES 只加入自动出库任务池，不锁定执行线、Rack、Bin、Cell 或目标储位 |
| 多条分拣机工作线同时就绪 | WES 按任务池优先序领取不同任务，并以 `available_since + workline_code` 稳定选择执行线 |
| 前序任务暂不可执行 | 后续可执行任务可以由其他就绪工作线领取；`dispatch_sequence` 不构成完成依赖 |
| 准备请求已接收 | `PREPARE_ACCEPTED` 后任务和候选工作线保持 `PREPARING`，没有可执行计划增量前不创建 TransportTask 或 DeviceCommand |
| 准备响应丢失 | WES 使用原 `operation_id` 和原正文重试；WMS 返回第一次保存的完整 `PREPARE_ACCEPTED` 响应 |
| 首个计划增量 | `plan_revision=1` 必须且只能定义一个初始接料货架面，可以同时新增来源明细；不携带增量类型或 WMS 计算进度字段 |
| WMS 分批完成资源计算 | 每批使用连续 `plan_revision`；WES 保存成功后再响应，并立即执行本批数据完整的来源或目标运输 |
| 后续计划增量 | 禁止 `target_rack`；新的精确目标只由逐盘最终 `ACCEPT` 返回 |
| 计划版本跳号或冲突 | WES 停止自动推进并人工核对，不跨版本猜测、合并或覆盖已接收明细 |
| 首批只有部分来源 | 已完整接收的明细可以执行；WMS 内部计算进度不进入 WES 状态或执行前提条件 |
| 首批期限到期仍无计划 | WES 以 `last_applied_plan_revision=0` 请求状态确认；WMS 返回带重试间隔的进行中状态或 revision 0 完成 |
| 新来源引用未定义接料货架面 | 拒绝该计划增量，不允许先取盘后补目标资源 |
| WMS 手工调整任务 | 通过 `queue_changed` 改变任务池业务优先序，不调用 WES 人工启动接口 |
| 多个退料货架或货架面 | 货架独占，SLOT 逐项执行；WES 按物理条件安排顺序 |
| 退料与五层货架同时就绪 | 退料直接取料优先，但不阻塞没有资源冲突的 CTU 和 Bin 流 |
| 计划同时包含多个五层来源货架面 | 每个 `rack_id + rack_face` 单独记录；同一货架的 A、B 面都有来源时记录两项；WES 只选择一个当前来源面，不得同时创建多条指向同一 CTU 工作位的货架任务 |
| 五层货架确认到位 | WES 发送 CTU 空闲背篓数和入料缓存空闲数的较小值；WMS 返回不超过该数量的 Bin 和精确来源 |
| 同一 WorkLine 的 CTU 批次 | 入站和退箱串行；同一时刻最多一个批次处于 WMS 请求或 Transport 执行中，不建立缓存位预留、租约或锁 |
| 多个事件同时触发 CTU 判断 | 自动出库业务模块在事务中只声明一个下一动作；其他触发发现已有未结束动作后退出 |
| 本地入站资源不足 | CTU 没有空闲背篓或入料缓存没有空位时不调用 WMS，等待现场状态变化 |
| 入站 `READY` 已返回 | 只表示 WMS 已选定本批 Bin，之后不能撤销或改选；对应 `BIN_MOVE` 最终成功、实扫身份匹配并创建 `BinExecution` 前，不得开始下一 CTU 动作 |
| 入站搬运完成且实扫匹配 | 创建唯一活动 `BinExecution` 后重新判断下一动作；当前面有可执行退箱批次时先退箱，否则继续入站或切换来源货架 |
| 当前五层来源货架面暂时无 Bin | WMS 返回 `NO_BATCH` 和重试间隔；到期前不重复请求、不据此换面，期间仍允许退箱优先 |
| 当前五层来源货架面结束 | WMS 返回 `RACK_FACE_DONE`；CTU 不携带 Bin、没有未结束搬运或未知位置、没有以当前面为冻结目标的退箱决定后才能选择下一来源面并执行换面或换架；已可靠进入 `RETURN_BUFFER` 且尚未冻结目标的 Bin 不阻塞切换 |
| 退箱暂时无批次 | `NO_BATCH` 必须返回重试间隔；候选留在 FIFO，无资源冲突的入站需求可驱动换面或换架 |
| 出料口出现正常 Bin | WES 按 Epoch 级 FIFO 提交队首候选并设置 `sequence_no`；WMS 在当前 `rack_id + rack_face` 分配任意合格精确空位，并原样返回 `sequence_no + bin_id` |
| 当前面暂时不能完成退箱目标分配 | WMS 返回 `NO_BATCH + retry_after_ms`；退箱自身不触发换面或换架，下一入站需求可以触发 |
| 当前面确定没有合格退箱储位 | 作为正常 `NO_BATCH` 等待，不转 NG、不返回 `STATE_CONFLICT`；下一入站需求到位后重新评估 FIFO |
| Bin 已可靠进入退料缓存 | Bin 可跨任务、跨面继续等待；不锁定原货架面，也不重新打开已完成 PickingTask |
| CTU 乱序投箱 | 任一 `inbound_batch` 已返回的 Bin 可按实际到达顺序在 SCAN2 请求工作计划 |
| FIFO 队首无工作 | WMS 返回 `NO_WORK` 后该 Bin 业务完成并继续向后流转 |
| Bin 计划尚未产生 Cell | PickingTask 不因 Cell 集合为空而完成 |
| 7 寸有空位但大尺寸无容量 | WMS 给出新的接料货架或货架面，WES 不计算容量 |
| 接料货架面切换 | 允许扫码台单盘预取；新货架或货架面确认到位前禁止 PUT |
| 精确 SLOT 分配 | WMS 在料盘扫码后返回目标 SLOT，WES 只校验并执行 |
| 扫码后需要换面或换架 | `ACCEPT` 一次返回精确 SLOT 和完整目标准备方案；WES 创建 TransportTask，到位后直接 PUT，不重复物料决定 |
| PUT 三步处理 | WMS `ACCEPT` 只授权目标，ECS `SUCCEEDED` 只表示物理放置成功，WMS `RECORDED` 后才更新位置、库存和目标占用 |
| PUT 设备记录 | DeviceCommand 结果保留在 WES；只有确定成功后才能上报位置，失败、超时或结果未知时禁止上报 |
| 两个机械臂并行 | 两个 `device_code` 可各有一条已接收尚未得到最终结果命令；ECS/PLC 硬件锁负责扫码台交接与防撞，WES 不等待平台释放事件或建立资源锁 |
| PUT 尚未完成但 WMS 返回 `CONTINUE` | 来源机械臂可接收下一条命令；无安全暂存位时，ECS/PLC 必须在料盘离开来源前取得扫码台交接许可 |
| 换架时当前架存在短暂依赖或当前无安全目标 | 返回 `WAIT + retry_after_ms`；相关状态变化后立即重新判断，WES 本地超时只暂停和告警 |
| Material NG | 完整扫码和来源绑定正确，WMS 返回 `MATERIAL_REJECTED`；只隔离当前盘 |
| Cell NG | WMS 返回 `SOURCE_CELL_MISMATCH + CLOSE`；当前盘 NG 结果确认后关闭当前 Cell，同 Bin 其他 Cell 继续；Bin 到达 `NG_EXIT` 后以相同原因只补充位置结果 |
| Bin NG | Bin 编号无法识别、设备明确报告方向异常或 WMS 明确业务 NG；整箱已确认到达 `NG_EXIT` 后上报原因 |
| 可识别但非预期 Bin | 保存 `expected_bin_id + actual_bin_id` 与位置证据并冻结；不请求工作计划、不送 NG、不替代预期成员，等待获批恢复合同 |
| 空取、扫码不完整、设备失败或未知 | 不得升级为 NG，分别进入空取、重试、暂停或人工核对流程 |
| 执行中补充正常计划 | 更高 `plan_revision` 增加当前任务尚未发布的直接取料来源或五层来源货架面；不能用于替换空取、NG 或 Transport 确定失败的明细 |
| 状态确认与增量竞态 | 状态确认携带 `last_applied_plan_revision`；WMS 发现更高版本时返回 `PLAN_REVISION_STALE` 并补发增量 |
| Transport 确定失败 | WES 结束本地对应的失败明细，其他明细继续；WMS 已知失败结果并创建新的 PickingTask，双方不交换失败上报或恢复方案 |
| Transport `UNKNOWN/RECONCILING` | 受影响明细保持未完成，等待同一 `transport_task_id` 的更高版本结果或人工核对；不释放资源、不创建替代 TransportTask |
| 任务业务状态完成 | `COMPLETED` 只表示当前任务明细都已处理完，不表示上游需求全部满足；不等待货架、Bin 或工作线现场清理，未满足需求由新任务处理 |

## 19. 当前评审状态

本文保持 `ReviewRequired`，直到 PickingTask 字段合同和相关公共合同共同批准。具体字段、枚举、错误、超时和 JSON 测试用例以
`docs/contracts/wms-outbound-picking-task-integration-requirements.md` 为准。

## 20. 人工分拣的 Bin 流转

### 20.1 工作线与插件

现场两条自动线有机械臂，两条人工线无机械臂；其他扫码、输送、缓存、CTU 和货架工作位的物理结构一致。该分拣机工作线的目标工作插件共三个：

| 工作线静态类型 | 允许激活的插件 |
| --- | --- |
| 自动线 | `automatic_putaway`、`automatic_picking` |
| 人工线 | `manual_bin_processing` |

自动线不通过缺少机械臂角色或可空字段降级为人工线；人工线也不伪造机械臂角色运行自动插件。当前部署不使用 `HYBRID`。

自动线在上架和拣货之间切换时，必须清线并创建新 `LineRunEpoch`。人工上架和人工拣货对 WES 都是相同的 Bin 流转，不切换插件；
操作员通过 PDA 在 WMS 中完成具体物料业务。

### 20.2 身份与所有权

`manual_bin_processing` 只处理货架、货架面、精确储位、Bin、本线缓存、CTU 和 Transport。它不创建机械臂 `DeviceCommand`，不接收 Cell、
料盘、物料数量或人工子任务，也不判断当前人工动作是上架还是拣货。

WMS 不为人工线增加 `manual_work_id` 或其他业务键。最小关联为：

- `task_id`：WMS 已有业务任务身份；人工上架和拣货任务在同一命名空间中全局唯一。
- `task_id + bin_id`：唯一标识该 Bin 在该任务中的一次人工进站与释放；同一组合不重复进站。
- `operation_id`：消息幂等身份，不是人工业务键。
- `transport_task_id`：独立 Transport 生命周期身份，不进入 WMS 人工任务模型。

WMS 拥有人工子任务、PDA 扫码、物料校验、库存事务、货架储位分配和业务完成裁决。WES 只保存可靠的物理位置、缓存占用和搬运结果。

### 20.3 三段流程

人工分拣不在工作位处简单切成“进”和“出”，而是以 WMS 持久化的 Bin 释放决定为因果分界。

#### 20.3.1 Task 驱动入站

1. WMS 使用已有 `task_id`、业务优先序和时间条件发布人工任务，不携带人工子任务 ID。
2. WES 只从已激活 `manual_bin_processing` 的就绪人工 WorkLine 中选线。
3. WMS 按任务提供可用来源货架面；WES 只在 CTU 和入站缓存有容量时请求下一 Bin 批次。
4. WMS 为本批返回确定 `bin_id[]`。WES 接收后不撤销、不替换、不用另一 Bin 补位。
5. 新入站 Bin 所在的货架面决定 CTU 工作位当前货架和当前面。只有入站需求可在正常运行中触发换面或换架。
6. Transport 确定成功并保存完整位置后，Bin 才进入本线缓存。SCAN 证据确认实际 `bin_id`，不使用计划值冒充实际身份。

Task 驱动段到 Bin 到达人工工作位并形成可靠位置事实为止。任务取消只能停止未发生的后续入站；已离开货架的 Bin 必须继续收口到确定位置。

#### 20.3.2 WMS/PDA 人工业务

1. `MANUAL_WORK_STATION` 同时最多一个 Bin；上游缓存可在现场容量内预取，但操作员不能通过 PDA 跳选后续 Bin。
2. WES 以设备扫码和位置证据向 WMS 报告 Bin 已到人工工作位。PDA 扫码不替代这个物理到位事实。
3. 操作员在 WMS PDA 中完成物料放入 Bin 或从 Bin 拣出。WMS 校验并持久化物料子任务和库存结果；WES 只保持 `WAITING_EXTERNAL`。
4. 当该 Bin 本轮全部人工子任务有确定结果后，WMS 只向 WES 发送一次 Bin 级释放决定：允许进入正常退料流，或明确送往 NG。

WMS 持久化的 Bin 释放决定是唯一分界事件。业务任务完成不能代替该决定；未收到决定时，WES 不得因超时自动退箱或送 NG。

#### 20.3.3 Bin 状态驱动退料

1. 收到正常释放决定后，Bin 进入 `LineRunEpoch` 级的 `RETURN_BUFFER` FIFO，不再受原任务的完成、取消或优先级驱动。
2. FIFO 可同时包含多个 `task_id` 的 Bin。新任务不能插队自己的退料 Bin。
3. 退料 Bin 不要求返回原货架、原货架面或原储位。WES 提交当前工作位 `rack_id + rack_face` 和 FIFO 候选；WMS 为连续前缀原子预留互不重复的精确 `slot_id`。
4. 当前面已有空位时使用 `BIN_MOVE`。当前面没有合格空位时，退料 Bin 继续等待；现有 `BIN_EXCHANGE` 不支持 `HANDOFF_POSITION` 参与交换，不能用于人工退箱。
5. 下一入站 Bin 位于其他面或其他货架时，由该入站需求触发 `RACK_ROTATE/RACK_MOVE`；退料 Bin 在 FIFO 中跨任务等待，不为自己触发换面或换架。
6. 停线或切换时，Epoch 保持 `ACTIVE` 并进入排空阶段，停止新任务和新 Bin；目标合同允许 WMS 选择有空位的货架面，但共同排空货架面决定 wire 尚未获批，当前为 `ReviewRequired/BLOCKED`，不得创建换面、换架或退箱 Transport。全部清场义务闭合后再关闭 Epoch。

退料、入站和换架共用一个 CTU 动作仲裁：已声明动作先收口；当前面已有空位时先消耗 FIFO；无空位时才由下一入站需求推进换面或换架。
多个事件同时触发时，只能在事务中声明一个下一动作。

### 20.4 容量和完成边界

下一 Bin 进入人工工作位前，WES 必须确认至少一个条件成立：

- `RETURN_BUFFER` 仍有可用位置。
- WMS 已在当前货架面预留精确空位。

缓存已满、位置未知或相关 Transport 为 `UNKNOWN/RECONCILING` 时，停止向人工工作位供箱；仍允许执行能够确定释放退料容量的已冻结搬运。

业务完成和物理清理分开：

| 对象 | 完成条件 |
| --- | --- |
| WMS 物料子任务 | 物料已正确放入 Bin 或已正确从 Bin 拣出，由 PDA/WMS 确认 |
| WMS 业务任务 | 全部应完成物料子任务均已正确执行，且 WMS 确认不再追加；取消或失败由 WMS 裁决为独立终态 |
| WES Bin 流转 | Bin 已进入确定货架储位或 NG 位置 |
| WorkLine/Epoch 清场 | 工作位、缓存、货架动作和 Transport 全部满足停线或切换条件 |

业务任务可在相关 Bin 仍位于人工工作位、`RETURN_BUFFER` 或回库 Transport 时完成。后续新任务可在物理容量和准入条件满足时继续入站，但不得丢弃旧任务的退料尾巴。

### 20.5 身份不匹配、NG 和外部等待

实际 Bin 可识别但不是本批预期 Bin 时：

- 保存 `expected_bin_id + actual_bin_id` 和扫码/位置证据，并由 WMS 可靠接收。
- 实际 Bin 不进入人工业务，也不自动进入 NG；WES 将其冻结在当前安全位置，等待独立恢复 wire 获批。
- 预期 Bin 仍保持未完成；WES 不以实际 Bin 替换计划成员。

只有 Bin 无法识别、物理路由明确要求隔离，或 WMS 给出稳定业务 NG 决定时，Bin 才进入 NG 路径。缓存满、人工处理慢、WMS 不可用、不是本批预期 Bin 都不是 NG。

WMS 不可用时不新增 `PAUSED_WAITING_WMS` 或 WorkLine 停线状态：

- `LineRunEpoch` 保持 `ACTIVE`，当前 Session 使用现有 `WAITING_EXTERNAL`，查询层可派生显示 `WAITING_WMS`。
- 相关 Outbox 保留原 `operation_id` 和冻结请求重试。插件不选新 Bin、不创建依赖 WMS 新决定的 Transport，不自选空位。
- 已被设备或 Transport 接收的动作不取消、不替换；新到的确定事实继续持久化。WMS 恢复并明确确认后重新判断下一动作。

WES 进程重启不等于 WMS 暂时不可用。进程重启仍遵循全局安全基线：不在原 `LineRunEpoch` 自动恢复物理编排，保留证据，现场清线后创建新 Epoch。

### 20.6 实施前门禁

本节只冻结人工分拣流程和所有权，不授权生产实施。实施前还必须单独冻结：

- 人工任务发布、准备、货架面计划、Bin 批次、工作位到位、Bin 释放、退料储位分配和任务完成的 operation 与严格 DTO。
- 人工线设备角色、位置角色、缓存容量、两面货架与统一 NG 位置编码。
- 四条工作线的静态类型和三个插件的 activation 配置；每个插件在激活时校验自己的闭集角色，不建设通用 capability registry。
- WMS/WES 共享幂等、版本、身份不匹配、跨任务 FIFO、容量背压、WMS 不可用和 Epoch 保持 `ACTIVE` 的排空用例。
- 共同排空货架面决定 operation 的字面量、严格 DTO、插件执行身份、旧架离场去向、新架可靠来源/工作位/到达面、目标 rack/face 原子绑定与非空 FIFO 前缀容量保留、`WAIT` 与幂等 fixture；获批前自动上架、自动出库和人工流的停线排空均为 `ReviewRequired/BLOCKED`。

人工流的严格 wire 合同获批前，不得为了表达人工流而复用或扩展现有自动出库 operation，不以可空机械臂、Cell、料盘或目标货架字段
表达人工流，也不创建兼容分支。
