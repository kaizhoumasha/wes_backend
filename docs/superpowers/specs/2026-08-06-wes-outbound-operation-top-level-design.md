---
title: WES 出库操作顶层设计
status: Draft
created_at: 2026-08-06
updated_at: 2026-08-07
scope: SMT 自动出库 PickingTask、多任务队列、多 Cell 晚绑定、分域 NG、追加来源、三段缓存与并行搬运
system_stage: pre_release
migration_strategy: direct_replacement
related:
  - docs/architecture/SRS.md
  - docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
  - docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/architecture/authority-matrix.md
  - docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
  - docs/hardware/wms_rcs_interface_requirements.md
---

# WES 出库操作顶层设计

## 1. 文档定位

本文定义 SMT 自动出库的目标业务合同，回答以下问题：

1. WMS 以什么执行对象驱动 WES，以及任务为何只聚合 Cell 执行。
2. 料盘为何在设备扫码后才绑定 `PkgID`、物料事实和目标储位。
3. AGV、CTU、滚筒线、机械臂和三段缓存如何并行推进并形成背压。
4. `PickingTask`、`CellExecution`、`MaterialExecution`、`BinExecution` 和 `TransportTask` 如何保持独立生命周期，
   以及工作线准入如何复用 `LineRunEpoch` 与设备/位置投影。
5. NG、未知结果、暂停、取消、完成、清场和可靠回写如何闭环。

权威关系：

- `docs/architecture/SRS.md` 记录产品需求和参与方职责。
- 最小执行架构 SPEC 定义核心对象、可靠性、位置投影和扩展边界。
- 本文定义自动出库业务对象、状态、不变量和验收场景。
- WMS 北向合同定义获批的 operation、method、path、wire DTO、错误与幂等语义。
- Master Plan 与分阶段计划只定义实现顺序，不得反向改变本文业务裁决。
- `docs/hardware/` 保存厂商原始输入，不是 WES 核心业务或北向 wire 真源。

系统尚未发布。本设计直接替换旧的“任务目标项 + 启动时逐盘 `SourceBinding`”方案，不保留旧字段、别名、fallback、
双读、双写或迁移兼容路径。

## 2. 核心裁决

- WES 不接收或拆解出库单、拣货单和波次单，只执行 WMS 下发的 `PickingTask`。
- `PickingTaskIssued` 冻结初始来源 Cell 集合，不预绑定 `PkgID`、SixInOne、料盘顺序、目标转运货架、目标储位或锁代际。
- WMS 在任务启动授权中原子锁定初始 Cell 集合；任务启动后不得静默修改成员，NG 恢复只允许引用现有未执行 Cell，或通过
  独立补充事件追加已锁定的新 Cell。
- `PickingTask` 只聚合已接纳的 `CellExecution`。全部初始与补充 Cell 完成且不存在未决 NG/来源恢复时，只形成 WES
  本地执行完成事实，不代表 WMS 业务终态；该条件不读取 Rack、Bin、AGV、CTU 或工作位状态。
- WMS 可以提前下发多张任务，并为同一工作线提供无歧义总序；WES 同线单任务执行、不同线并行执行，不自行排序或跳选。
- 设备从 Cell 顶部取盘并扫码后，WES 持久化扫码证据并请求 WMS 返回该盘的业务决定。
- WMS 是 `PkgID`、SixInOne、物料资格、目标储位、是否继续当前 Cell 及 Cell 完成的唯一权威。
- WES 不根据扫码内容自行判断物料是否正确，不自行选择目标储位或决定是否继续取下一盘。
- AGV 送架、CTU 批次投箱/退箱、目标架与可选退料架搬运分别由独立 `TransportTask` 承担。
- AGV 将指定五层货架送达 CTU 作业位并形成可靠到位证据后，对应 CTU 投箱批次才具备执行资格。
- 滚筒线划分入料缓存区、工作位缓存区和退料缓存区；工作位缓存容量包含机械臂实际工作位。
- 缓存占用数从逐位置投影聚合，不使用三个可直接增减的业务计数器作为位置真源。
- CTU、滚筒线、机械臂可以并行推进；共享机械臂、料盘扫码台和目标架工作面必须串行仲裁。
- 只有成功且可关联的物理结果才能推进位置；ACK、已受理、已派发和扫码路由决定都不是物理完成证据。
- 每盘成功 PUT 后形成独立位置变化事实；任务本地执行完成事实与逐盘事实分离并保持因果顺序。
- `PickingTask` 本地执行完成不等于 WMS 业务终态或现场资源已经释放；下一任务准入由当前 `LineRunEpoch` 下的
  设备/位置投影和活动对象共同判断。

## 3. 范围与非目标

### 3.1 范围

- `PickingTask` 事件接入、WMS 总序驱动的同线队列、启动授权、Cell 成员代际、最早启动时间和本地执行完成。
- 来源为五层货架料箱 Cell，以及可选退料货架单料盘储位的自动取料。
- 扫码后 `PkgID` 晚绑定、WMS 逐盘决定、目标储位分配和 Cell 继续/完成判断。
- AGV 五层货架到位、CTU 冻结成员批次、滚筒线三段缓存和容量背压。
- 双面目标生产转运货架的目标面窗口与单调储位分配。
- 逐盘位置变化、料盘/Cell/Bin 三类 NG、追加式来源恢复、未知结果、设备资源仲裁和可靠 WMS 回写。
- `PickingTask` 业务完成与工作线物理清场、下一任务准入分离。

### 3.2 非目标

- WES 订单管理、波次计算、库存选择、库存锁策略或跨任务来源仲裁。
- WES 从 Cell 内容推断待取 `PkgID`、料盘数量、物料资格或替代来源。
- WES 规划 AGV/CTU 路线、车辆交通、货架搬运路径或 PLC 内部动作。
- 用 `PickingTask` 状态机承载 Rack、Bin、AGV、CTU、设备命令或工作位清场状态。
- 把缓存容量实现为脱离具体物理位置的可变整数，或让 WES 计数承担唯一安全互锁。
- 跨任务合并、全局优化器、通用补偿器、自动猜测未知现场或兼容旧方案。

## 4. 权威与对象边界

### 4.1 参与方权威

| 参与方 | 唯一权威 | WES 不得替代 |
| --- | --- | --- |
| WMS | 订单/波次、库存、Cell 锁、PKG/SixInOne、物料资格、逐盘去向、目标储位、继续/完成、任务优先级和业务取消 | 本地选料、目标分配、Cell 完成推断或库存账务 |
| WES | 任务与 Cell 本地执行、设备资源仲裁、命令发送/等待、作业期投影、异常证据和可靠义务 | WMS 业务决定、RCS 路径或设备内部控制 |
| RCS/AGV/CTU | 货架/料箱运输、路径、排队、旋转和终态物理结果 | 库存、PKG 资格、Cell 业务完成或目标储位业务分配 |
| ECS/PLC/设备 | 扫码、到位/离位、抓取、放置、滚筒输送、安全互锁和最终设备结果 | 库存、任务优先级、目标业务选择或任务终态 |

### 4.2 独立执行对象

| 对象 | 唯一职责 | 不参与 |
| --- | --- | --- |
| `PickingTask` | 聚合初始与显式追加的 `CellExecution` 成员及其完成状态 | Rack/Bin/Transport/设备/清场状态聚合 |
| `CellExecution` | 管理一个来源取料单元的逐盘循环和最终完成 | 料箱运输、目标架运输或全线清场 |
| `MaterialExecution` | 扫码后绑定一个 `PkgID`，跟踪该盘动作与位置事实 | Cell 是否继续、Bin 流转或任务聚合 |
| `BinExecution` | 跟踪一个料箱在三段缓存、SCAN1/2/3 和 NG 路径中的位置证据 | PickingTask 完成聚合或 CTU 批次终态 |
| `TransportTask` | 跟踪 AGV/CTU 搬运目标、冻结批次成员、ACK、状态和终态 | PickingTask、Cell 或设备命令状态 |
| `DeviceCommand` | 跟踪一次 ECS 命令的发送、ACK、结果、deadline 和幂等事实 | WMS 业务结果或任务状态 |
| `WmsConfirmation` | 可靠提交逐盘位置变化与任务完成事实 | 重放设备动作或修改执行状态 |
| `PositionProjection` | 表达具体位置的占用、预留、已知在途和未知状态 | 库存主账或运输路线规划 |
| `LineRunEpoch` | 固定一次活动流程的插件版本、配置版本和工作线模式 | 承载 PickingTask、Cell、Bin、Transport 或恢复状态 |

对象可通过 `picking_task_id`、`cell_execution_id`、`bin_execution_id` 等稳定引用建立因果关系，但不得因关联关系形成
级联状态机或把一个对象的终态伪装成另一个对象的终态。

## 5. PickingTask 与 Cell 合同

### 5.1 任务载荷

`PickingTask` 只携带执行 Cell，不携带具体料盘或目标储位。最低业务字段为：

| 字段 | 含义 |
| --- | --- |
| `task_id` / `task_version` | 稳定任务身份和业务版本 |
| `dispatch_sequence` / `issued_at` | WMS 提供的无歧义同线总序和发布时间；WES 不另排业务优先级 |
| `not_before` | 可选最早启动时间，不是准点执行承诺 |
| `workline_code` | 指定自动出库工作线 |
| `pick_cells[]` | 本任务启动时必须原子锁定的初始取料单元集合 |
| `cell_execution_id` | 任务内稳定且唯一的 Cell 执行身份 |
| `source_locator` | 物理来源联合类型，见下表 |

`source_locator` 是闭集联合类型：

| 类型 | 必填定位 | 执行语义 |
| --- | --- | --- |
| `BIN_CELL` | `rack_id + rack_face + bin_id + cell_id` | 可连续产生多盘，WMS 逐盘返回 `CONTINUE` 或 `CELL_DONE` |
| `RACK_SLOT` | `rack_id + rack_face + slot_id` | 可选退料货架来源；按单储位单料盘执行，仍以一个 `CellExecution` 参与任务聚合 |

这里的 `CellExecution` 是 PickingTask 的统一取料执行单元；`BIN_CELL` 与 `RACK_SLOT` 的物理差异只体现在 locator 和
逐盘循环规则中。不得把 Rack 或 Bin 生命周期塞回 PickingTask。

任务中禁止出现：

- `PkgID` 或 SixInOne。
- 料盘数量、顶部顺序或提前展开的逐盘 item。
- 具体目标转运货架 `rack_id`、目标面或目标储位。
- AGV/CTU 任务、设备命令或缓存状态。
- Cell 锁代际；任务发布阶段尚未锁定来源。
- 开放字段、兼容别名或未批准扩展。

### 5.2 接入与启动校验

任务接入时整单校验字段闭集、任务身份、Cell 身份唯一性、locator 联合类型完整性、幂等冲突和本地工作线队列准入。任一
Cell 结构非法时整单拒绝，不得部分接纳；WES 不重新校验 WMS 库存或来源业务资格。

启动授权必须同时确认：

- 当前任务版本有效，WMS 已原子锁定完整初始 `pick_cells[]` 并返回首个锁代际。
- 锁定的 `cell_execution_id` 与已接纳初始集合完全一致。
- WMS 已授权目标转运货架容量和目标面业务顺序。
- 所需五层货架、可选退料货架和目标空架的搬运目标已经明确。

任务启动后，WMS 不得静默增加、删除或替换 Cell。NG 恢复需要其他来源时，WMS 只能通过幂等补充事件：

- 引用当前任务内尚未执行的 Cell，不改变成员集合；或
- 追加已原子锁定的新 Cell，并为补充集合分配递增成员代际和独立锁代际。

既有 Cell 永不被改写或删除；每个 CellExecution 永久使用自己所属的锁代际。

## 6. 启动与并行运输

任务通过启动校验后，WES 创建相互独立但可关联的运输需求：

1. 目标空转运货架到目标工作位。
2. 可选退料货架到来源工作位。
3. 第一台五层货架由 AGV 运送至 CTU 作业位。
4. AGV 权威终态、货架身份和到位投影同时确认后，解锁该五层货架对应的 CTU 投箱批次。
5. CTU 按冻结成员批次把所需料箱投入本线入料口；只要入料缓存存在可用准入位置即可继续，不等待当前料箱完成。
6. 当前五层货架批次执行期间，是否预调下一货架只由已批准的物理等待位和 WMS/RCS 调度合同决定；本文不假设额外缓存位。

AGV/CTU 请求的 ACK、已受理或已派发均不构成货架/料箱到位。只有批准的运输终态和位置证据才能推进依赖它的执行。

CTU 批次拥有冻结成员、批次级 ACK/状态/终态和终态中的成员最终事实。WES 不要求 CTU 上报设备内部子阶段，也不把
单箱 `BinExecution` 状态写入批次状态机。

## 7. 三段缓存与容量背压

### 7.1 位置模型

滚筒线由以下位置角色构成：

- `INGRESS_BUFFER`：CTU 入料缓存区。
- `WORK_BUFFER`：工作位等待位置与机械臂实际工作位；总容量包含实际工作位。
- `RETURN_BUFFER`：正常完成料箱的 CTU 退料缓存区。
- `NG_EXIT`：NG 料箱专用出口，不属于退料缓存。

每个物理位置分别维护 `FREE | RESERVED | OCCUPIED | IN_TRANSIT | UNKNOWN` 投影。区域占用量由对应位置聚合得出：

```text
0 <= occupied(area) <= capacity(area)
```

不维护脱离位置身份的 `ingress_count`、`work_count` 或 `return_count` 作为业务真源。

### 7.2 投箱准入与硬互锁

CTU 投箱必须同时满足：

1. WES 根据可靠位置投影为具体入料位置建立一次性准入/预留，避免多个在途成员竞争同一空位。
2. PLC/ECS 在物理层阻止向非空位置继续投箱；WES 投影不得成为唯一安全互锁。

到位成功后，位置由 `RESERVED` 迁移为 `OCCUPIED`。明确失败时释放预留；结果未知时保持相关位置保守阻塞并进入对账，
不得猜测空闲后继续投箱。

### 7.3 三段流转

| 物理事件 | 入料缓存 | 工作位缓存 | 退料缓存 |
| --- | --- | --- | --- |
| CTU 成功投入料箱 | 对应位置 `+1` | 不变 | 不变 |
| SCAN1 判定正常且成功转入工作段 | 对应位置 `-1` | 对应位置 `+1` | 不变 |
| SCAN1 判定 NG 且成功移出 | 对应位置 `-1` | 不变 | 不变 |
| 正常完成料箱经 SCAN3 进入退料段 | 不变 | 对应位置 `-1` | 对应位置 `+1` |
| CTU 成功取走 N 个冻结成员 | 不变 | 不变 | 对应 N 个位置释放 |

表中的 `+1/-1` 仅用于描述位置事实的聚合结果，不授权直接修改计数器。跨段转移必须由同一个稳定位置变化事实原子更新
源位置与目标位置。扫码决定只确定路由，不能提前释放源位置或占用目标位置。

某段满载时只对其直接上游施加背压：

- 入料缓存满：CTU 暂停投箱，但已在滚筒线和工作位的料箱继续执行。
- 工作位缓存满：SCAN1 正常箱等待，不转为 NG；入料缓存因此自然形成上游背压。
- 退料缓存满：正常完成箱等待，不转为 NG；CTU 退箱批次可以并行释放容量。

## 8. Bin、Cell 与逐盘执行

### 8.1 料箱准入

1. CTU 成员到达入料缓存后创建或恢复对应 `BinExecution`。
2. 料箱运行到 SCAN1，设备上报料箱身份和位置证据。
3. WES 校验任务关联、WorkLine/Epoch 和位置容量；NG 箱进入专用出口，正常箱在工作段有准入位置时转入工作位缓存。
4. 料箱到达实际工作位并扫码后，WES 从该 Bin 的未完成 `CellExecution` 中选择一个物理可执行 Cell。
5. WES 的选择只考虑已冻结 Cell、设备/位置就绪和资源互斥，不改变 WMS 的库存或业务顺序。

非 NG 料箱必须同线进、同线作业、同线退，不得因其他线空闲而静默改绑。

### 8.2 逐盘晚绑定

一个 `BIN_CELL` 的循环为：

1. 机械臂从当前 Cell 顶部取出完整料盘，放入料盘扫码台。
2. 设备若可靠返回“无料”终态，WES 先持久化来源观察证据并请求 WMS 返回 `CELL_DONE | RETRY | WAIT`；不得自行推断
   Cell 已空或已完成。设备结果未知或无法关联时保持 Cell 未决，不调用空取业务决定。
3. 成功取盘后，扫码设备向 WES 上报可让 WMS 唯一确定 `PkgID` 的原始字段。
4. WES 先持久化扫码证据并快速完成设备侧接收，不让设备回调同步等待 WMS 业务处理。
5. WES 以任务、Cell、锁代际、扫码证据和请求幂等键调用 WMS 逐盘决定。
6. WMS 返回接受/拒绝/等待判别结果；仅接受结果携带实际 `PkgID`、完整 SixInOne、目标储位、目标面代际和 Cell 继续结果。
7. WES 校验响应与当前请求、Cell 锁代际和当前目标面窗口匹配；不得改写 WMS 业务含义。
8. 接受时创建 `MaterialExecution` 并下发 PUT；拒绝时按批准的 NG/异常去向执行；等待或结果未知时保持扫码台占用并暂停
   依赖该资源的新动作。
9. PUT 成功后更新位置投影并可靠建立逐盘位置变化事实。
10. WMS 返回 `CONTINUE` 时，当前盘闭合后才允许同一 Cell 取下一盘；返回 `CELL_DONE` 时停止创建下一盘动作。

设备扫码事实与 WMS 决定必须分开持久化。WMS 超时、网络中断或响应无法关联时，不得解释为物料错误、Cell 完成或
允许继续。

`WAIT` 是非终局快照；身份冲突必须由 WMS 表达为 `WAIT` 且 `reason_code=IDENTITY_CONFLICT`。同一请求 ID 重复提交必须
返回原快照；恢复条件满足后，WES 业务模块使用新的请求 ID、
同一扫码证据和前序决定 ID 请求下一版本。一个扫码证据最多只能有一个不可变终局 `ACCEPT` 或 `REJECT`，并发分支、
版本倒退或多个终局结果一律失败关闭。空取 `WAIT` 对 `source_observation_id` 使用同一规则，`WmsClient` 不自动轮询。

### 8.3 Cell 完成

`CELL_DONE` 只表示“不再从该 Cell 产生下一盘”，不表示当前已扫码料盘已经完成物理放置。

`CellExecution` 进入 `COMPLETED` 必须满足：

- WMS 针对当前锁代际明确返回 `CELL_DONE`，或 `RACK_SLOT` 的单盘闭环已得到等价完成决定。
- 当前 Cell 没有尚未生成的下一盘动作。
- 空取结束 Cell 时，已有可关联的设备“无料”终态和来源观察证据，且没有未决的在途物料动作。
- 最后一盘若被接受，其 PUT 已有确定成功结果并已建立可靠位置变化事实。
- 最后一盘若被拒绝，其 NG/异常位置已有确定物理结果。
- 当前 Cell 不存在 `unknown` 或无法关联的扫码、设备命令和物料位置。

逐盘位置事实是否已经被远端接受由独立 `WmsConfirmation` 管理，不回填到 Cell 状态机；任务完成通知通过因果依赖保证
不会越过任何尚未接受的逐盘事实。

## 9. 共享资源与目标架执行

退料货架流与 Bin/Cell 流可以并行调架、排队和准备，但共享资源必须串行仲裁：

- 来源机械臂。
- 料盘扫码台。
- 目标机械臂或 PUT 动作资源。
- 当前目标转运货架工作面和目标储位。

同一时刻只有一个 `MaterialExecution` 可以持有同一设备或位置资源。并行的是运输、准备和等待，不是同一设备上的物理
动作。

`PkgID` 可以逐盘晚绑定，但目标架必须保持单调面窗口：

- WMS 在任务启动时冻结目标架容量、面顺序、初始开放面和 `face_window_generation`。
- WMS 每盘只能返回当前开放目标面和代际内的唯一空储位。
- WMS 只能通过递增的 `face_window_generation` 授权 A 面切换到 B 面；WES 校验旧面不存在未决物理动作后，只决定安全旋转时机。
- 切到 B 后不得再接受 A 面储位、旧代际或迟到决定。
- 目标储位没有可靠释放/占用事实时不得重复分配。

若目标架容量不足以容纳 WMS 仍要求继续的 Cell，WMS 必须返回明确等待/业务处置；WES 不自行更换目标架、切换面或
结束 Cell。

## 10. PickingTask 状态与完成

### 10.1 任务状态

| 状态 | 关键不变量 | 主要迁移 |
| --- | --- | --- |
| `QUEUED` | 任务已接纳，尚未取得有效启动锁 | `STARTING`、`CANCELLING` |
| `STARTING` | 请求 WMS 原子锁定初始 Cell；授权前不创建任务专属物理动作 | `EXECUTING`、`PAUSED`、`CANCELLING` |
| `EXECUTING` | 原始载荷不可变；成员只可由 WMS 补充事件追加；只聚合 Cell 状态 | `PAUSED`、`CANCELLING`、`EXECUTION_COMPLETED` |
| `PAUSED` | 保留已完成 Cell 和稳定事实；不猜测未知结果 | WMS 明确恢复后回到 `EXECUTING`，或 `CANCELLING` |
| `CANCELLING` | 停止创建新 Cell/料盘动作；等待任务取消业务决定闭合 | `CANCELLED` |
| `EXECUTION_COMPLETED` | WES 本地执行终态；只表达已授权物理执行全部闭合，不复制 WMS 业务终态 | 无 |
| `CANCELLED` | WMS 已给出取消业务决定，WES 已安全停止执行 | 无 |

队列参数变更通过独立带版本事件表达，不改写 PickingTask 原始载荷。同一工作线只执行一张任务，WES 只处理 WMS
`dispatch_sequence` 总序中的首个未开始任务；不同工作线可以并行。队首任务尚未达到 `not_before` 时保持等待，不得越过；
只有 WMS 返回显式 `TRY_NEXT` 授权时，WES 才可尝试总序中的下一任务。

### 10.2 本地执行完成条件

`PickingTask` 的本地执行完成条件只有：

```text
ALL(已接纳 CellExecution.status == COMPLETED)
AND 不存在未决 NG / 来源恢复决定
```

“已接纳 Cell”包括启动时锁定的初始成员和后续补充事件追加的成员。NG Cell 仍以 `COMPLETED + outcome` 闭合，不能通过
删除成员绕过任务完成条件。

满足后，WES 将任务执行投影置为 `EXECUTION_COMPLETED` 并建立独立执行完成事实；该状态不代表 WMS 业务终态。
执行完成义务依赖全部逐盘位置确认义务先被 WMS 接受；可靠派发层负责因果排序，不把远端接受状态重新塞回
`PickingTask` 执行投影。

任务完成不要求读取或聚合：

- 五层货架、退料货架或目标转运货架状态。
- Bin 是否已经被 CTU 放回五层货架。
- CTU/AGV 批次是否已经全部清场。
- 工作位是否已允许下一任务进入。

上述资源仍由各自 owner 继续闭环。失败只产生对应 Transport/Bin/WorkLine 异常，不得反向修改已完成的本地执行投影。

### 10.3 工作线释放

自动出库插件在当前 `LineRunEpoch` 上下文中，根据独立设备/位置投影和活动执行对象判断下一任务准入，至少考虑：

- 共享机械臂和扫码台没有当前在制品或 unknown 动作。
- 下一任务所需目标工作位已经释放。
- 三段缓存具备下一任务准入所需容量，且不存在位置冲突。
- 未闭合的运输/清场义务不会与下一任务争用相同物理资源。

`PickingTask COMPLETED` 与下一任务准入可以异步发生。任务完成不能绕过工作线资源门直接启动下一任务，也不得为此
新建承载全部动态状态的通用 Session。

## 11. 异常、暂停与取消

| 异常 | 隔离与处理 |
| --- | --- |
| 扫码无法唯一确定 `PkgID` | 当前料盘保持扫码台占用，关联 Cell 暂停并上报 WMS，不猜测身份 |
| WMS 决定超时或无法关联 | 保留扫码证据和未决请求；阻止依赖扫码台的动作，不解释为 NG 或 `CELL_DONE` |
| 设备可靠返回空取 | 持久化来源观察证据并请求 WMS 决定；仅 `CELL_DONE` 结束 Cell，`RETRY/WAIT` 保持未完成 |
| 空取结果未知或无法关联 | 保持 Cell 和来源资源未决，不把未知结果解释为 Cell 已空 |
| PICK/PUT 结果未知 | 对应物料位置设为 unknown，阻止相关设备/位置复用并进入人工消歧 |
| 入料缓存满 | CTU 投箱背压；已上线料箱继续执行 |
| 工作位缓存满 | SCAN1 正常箱等待，不转 NG |
| 退料缓存满 | 正常完成箱等待；CTU 退箱批次并行释放容量 |
| AGV 五层货架未到位 | 对应 CTU 批次保持不可执行；不影响无资源冲突的其他运输或设备动作 |
| CTU 批次部分失败 | 由 TransportTask 保存成员最终事实并对账；不得伪造 Bin 或 Cell 终态 |
| 目标容量不足 | 当前 Cell 等待 WMS 处置；WES 不自行换架或提前完成 |

取消不触发通用补偿：

1. `PickingTask` 进入 `CANCELLING`，停止创建新的 Cell/料盘动作。
2. 已在途或 unknown 的物料动作由对应 `MaterialExecution` 和 `DeviceCommand` 继续闭环或人工消歧。
3. WES 向 WMS 报告稳定的 Cell、料盘和任务事实；WMS 决定业务取消是否接受。
4. WMS 接受任务取消后，`PickingTask` 可进入 `CANCELLED`。
5. Rack、Bin、Transport 和 WorkLine 清场继续由各自 owner 执行，不成为 PickingTask 取消终态条件。

## 12. WMS 语义交互面

WMS Business Event 与 Device Event 是两类合同。PickingTask Event 必须进入独立 WMS 业务 ingress，禁止携带
`device_code`、复用 `/api/v1/callback/event`、查询 `DeviceContext`，或为 WMS 创建虚拟设备。`workline_code` 只用于业务
路由和工作线队列准入，不是设备身份。WMS Event 被接纳后，插件才可为真实设备创建独立 `DeviceCommand`。

WMS → WES：

- 以 Event 发布包含 `pick_cells[]` 的 PickingTask，并用独立事件更新未开始任务的队列参数。
- 在 WES 请求启动时原子锁定初始 Cell，并同步返回锁代际、初始目标面、目标面代际和任务启动授权。
- 对 NG 来源恢复发布决定事件：引用现有未执行 Cell、追加已锁定的新 Cell，或不补充而关闭缺口。
- 对每次扫码返回业务接受/拒绝/等待判别结果；仅接受结果携带 `PkgID`、SixInOne、目标储位、目标面代际和
  `CONTINUE | CELL_DONE`。
- 对可靠空取证据返回 `CELL_DONE | RETRY | WAIT`，并以递增目标面代际明确授权换面。
- 提供批准的人工消歧或异常处置结果。

WES → WMS：

- 请求/确认任务启动与 Cell 锁。
- 提交逐盘扫码证据并请求业务决定。
- 提交可靠空取证据并请求 Cell 后续决定。
- 报告 Bin NG 和受影响 Cell。
- 提交逐盘位置变化事实。
- 提交 PickingTask 本地执行完成事实或 WMS 已决定的取消事实。
- 报告身份冲突、设备未知结果、目标容量不足和稳定现场证据。

AGV/CTU/RCS 搬运不进入上述 PickingTask 业务 API；它们由 Phase 4 Transport 合同拥有，并通过 WMS 转发 Adapter 调用。
具体 operation 名、method、path、DTO、同步/异步结果、幂等键、SLA 和错误码必须由对应北向合同批准。

面向 WMS 开发团队的最小能力、字段语义和批准清单见
`docs/contracts/wms-outbound-picking-task-integration-requirements.md`；该要求文档同样不替代正式获批 wire。

## 13. 阶段与所有权

| 所有者 | 本场景职责 |
| --- | --- |
| Phase 1/2 | 提供已验收的测试治理与无业务语义 HTTP 传输事实，不以出库行为反向验收基础能力 |
| Phase 3 `WmsClient` | 只提供 HTTP/JSON 访问；不拥有 PickingTask、扫码决定、运输或缓存业务语义 |
| Phase 4 最小平台 | 拥有 `TransportTask`、可靠义务、幂等、设备动作、unknown、位置投影和持久化证据 |
| 后续 WMS 出库业务模块 | 拥有任务启动、逐盘扫码决定、位置变化和任务完成的获批 wire DTO 与结果解释 |
| 自动出库插件 | 拥有 `CellExecution`、三段缓存执行、资源仲裁、目标面窗口和本文业务流程 |
| WMS/RCS/ECS Adapter | 只翻译各自批准 wire，不持有 PickingTask、Cell 或 WorkLine 生命周期 |

Phase 3 共享 `WmsClient` 已独立实施并验收；当前实施合同为 `docs/contracts/wms-northbound-interaction-contract.md`，完成计划已移出项目归档。
本文的 PickingTask、逐盘决定和完成 API 仍须取得 WMS 正式批准，不得因本 Draft 存在而解释为业务或 wire 已批准。

## 14. Fixture 与验收所有权

| 资产 | 唯一所有者 | 验证范围 |
| --- | --- | --- |
| WMS wire DTO/fixture | WMS ACL 合同测试 | 获批字段闭集、编码、幂等和错误映射 |
| 出库业务场景/fixture | 自动出库插件 | Cell 循环、晚绑定、三段缓存、仲裁、任务完成与清场分离 |
| AGV/CTU 批次 fixture | Transport 合同/Adapter | 冻结成员、批次 ACK/状态/终态和成员最终事实 |
| 厂商命令/结果 fixture | 对应 ECS Adapter | 扫码、到位、命令、ACK、结果和厂商原始码 |
| MOCK 场景数据 | MOCK 自动化环境 | 跨系统验收，不作为 wire、插件或 Adapter 真源 |

各层可使用正式 schema 校验自己的数据，但不得共用同一 fixture 文件作为多层真源。

最低验收场景：

| 场景 | 通过标准 |
| --- | --- |
| PickingTask 含多个 `BIN_CELL` | Cell 集合原子接纳；无 `PkgID`、SixInOne、目标 rack/slot 或运输状态 |
| WMS Event 携带设备身份或投向设备 Event 入口 | 拒绝接纳；不查询设备上下文，不创建虚拟设备或 PickingTask |
| WMS 提前下发同线多任务 | 全部可靠排队，同线只启动一张；不同线任务可并行 |
| 同线队首任务尚未到 `not_before` | 保持等待；没有 WMS 显式 `TRY_NEXT` 授权时不得跳选后续任务 |
| 可选 `RACK_SLOT` 来源 | 作为单盘 `CellExecution` 参与任务聚合，不把退料货架生命周期写入 PickingTask |
| Cell 集合缺项、重复或 locator 非法 | 整单拒绝，不部分接纳 |
| 启动锁代际不匹配 | 不创建执行动作或运输义务，等待 WMS 修正 |
| AGV 尚未把五层架送达 | 对应 CTU 批次不可执行 |
| 入料缓存有可用准入位置 | CTU 可继续投入批次下一箱，不等待当前工作箱完成 |
| 三段任一区域满载 | 只向直接上游背压，不把正常箱改为 NG |
| 跨段位置变化 | 同一事实原子迁移源/目标位置；聚合容量不漂移 |
| 退料架流与 Bin/Cell 流同时就绪 | 可并行准备；共享机械臂/扫码台同一时刻只执行一个 MaterialExecution |
| 扫码结果可唯一确定 `PkgID` | WMS 返回实际 PKG、目标储位与继续结果后才允许 PUT |
| WMS 返回 `CONTINUE` | 当前盘闭合后才能从同一 Cell 取下一盘 |
| WMS 返回 `CELL_DONE` | 当前最后一盘闭合后 Cell 才进入 `COMPLETED` |
| 料盘 NG | 当前盘进入 NG 区；WMS 决定当前 Cell 继续、结束或等待 |
| Cell NG | 当前 Cell 闭合；同 Bin 其他 Cell 继续；Bin 最终进入 NG 出口 |
| Bin NG | 全箱 Cell 停止执行并进入 NG 出口；WES 报告全部受影响 Cell |
| NG 使用任务内未执行 Cell | 只引用既有成员，不重复追加 |
| NG 需要新来源 | 追加新 Cell 成员代际和独立锁代际，不改写旧 Cell |
| 设备可靠返回空取 | 提交稳定来源观察证据；仅 WMS 返回 `CELL_DONE` 才结束 Cell，`RETRY/WAIT` 保持未完成 |
| `WAIT`（包括身份冲突）解除 | 新请求关联同一证据和前序决定并取得下一版本；原请求结果不被改写 |
| 同一证据出现多个终局决定 | 失败关闭并上报合同冲突，不选择任一结果推进 |
| WMS 超时或结果无法关联 | 当前扫码台保持占用，不解释为 NG、继续或 Cell 完成 |
| WMS 授权目标面从 A 切到 B | 目标面代际递增且旧面无未决动作后执行旋转；切换后拒绝 A 面目标和旧代际 |
| 全部初始与补充 Cell 完成且无未决恢复 | PickingTask 进入 `COMPLETED`，不读取 Rack/Bin/Transport/WorkLine 状态 |
| 逐盘事实尚未被 WMS 接受 | 任务完成通知义务保持依赖等待，不越过逐盘确认 |
| PickingTask 已完成但 CTU 尚在退箱 | PickingTask 不回退；当前 Epoch 下的设备/位置投影和活动对象独立决定下一任务准入 |
| Transport 或清场失败 | 产生对应对象异常，不篡改已完成 PickingTask |

## 15. 当前评审状态

本次修订已经完成架构讨论并收敛以下裁决：

- PickingTask 从“目标项 + 启动时逐盘来源绑定”改为“冻结 Cell 集合 + 扫码后逐盘晚绑定”。
- PickingTask 只聚合 `CellExecution`，不聚合 Rack、Bin、Transport 或 WorkLine 状态。
- AGV 到位是 CTU 批次执行的前置门；CTU 投箱按入料缓存容量持续并行推进。
- 滚筒线按入料、工作位、退料三段逐位置投影和背压运行，工作位容量包含实际工作位。
- 共享设备动作串行仲裁，目标架采用单调目标面窗口。
- Cell、任务、可靠回写和现场清场保持独立生命周期。
- 多任务按 WMS 给出的同线无歧义总序排队，同线单任务执行、不同线并行；WES 不自行排序或跳选。
- NG 按料盘、Cell、Bin 分域处置，恢复来源采用“引用既有 Cell 或追加新 Cell”的显式事件。

本文仍为待书面复审草案，不表示任何 PickingTask、逐盘决定或 Transport wire 已获 WMS/RCS 批准。书面复审通过后再更新
状态并进入实施计划；在此之前不得据此开始业务 API 或插件编码。
