# 入库链路三场景验收步骤

> 日期：2026-06-24
> 状态：Draft
> 适用范围：粗分机入库、满箱交换前置分流、分拣机入库。

<!-- ownership: end-to-end-line-acceptance-steps -->

本文只拥有整线验收步骤，不拥有粗分机扫码到入料决策切片的分支判定、状态所有权、reason code 或 replay 规则。
这些语义以[粗分机扫码到准入决策窄闭环合同](./rough_sorter_scan_decision_contract.md)为唯一真源。下文拓扑分支只用于选择端到端验收路径；若与权威合同冲突，以权威合同为准，禁止据此实现业务判定。

本文用于验收粗分机到分拣机之间的目标态业务闭环。它只描述验收步骤、观测点和通过标准，不替代
`docs/architecture/workline-and-plugin-restructuring.md` 的架构设计，也不定义 WMS、AGV、CTU 或 ECS 的内部调度策略。

## 1. 验收目标

本次验收覆盖三个连续场景：

1. 粗分机正常入库：料盘从入口扫码决策后执行入料，成功结果携带有效测量，再经 WMS 准入后进入粗分机流水线，最终投入单层货架料箱料格。
2. 满箱交换前置分流：单层货架从粗分机移出后，先进入独立满箱交换区；如有满箱交换需求，先完成箱级入库交换，再进入分拣机 STATION 或排队区。
3. 分拣机入库：未通过满箱交换直接入库的剩余物料，由分拣机从单层货架逐件聚合到五层货架料箱料格。

验收必须证明：

- 对象级流水并发成立，设备完成当前对象步骤后可处理下一个对象。
- 现场物理事实先落 WES 本地投影，WMS 同步失败不得抹掉本地物理事实。
- 所有外部履约均通过 WMS fulfillment port 表达，WES 不直连 AGV/CTU/RCS SDK。
- 异常只 hold 对应对象、设备、资源或队列，不默认停止整条 WorkLine。
- 关键等待必须有 deadline、告警或解除条件，不能无限等待。

## 2. 验收拓扑

```text
操作员入口
  -> 粗分机入口扫码决策
       -> 按权威合同 RS-SD-001 至 RS-SD-013 记录 evidence 与 outcome
       -> 进入该 case 指定的后续设备、NG、Session Hold、no-op 或 evidence archive 路径

ADMIT / MOVE_FORWARD 正常主流程
  -> 粗分机流水线
  -> 出料机械臂
  -> 粗分机出料单层货架位
  -> 单层货架移出
  -> 满箱交换区
       ├─ 无满箱交换需求
       │    -> 分拣机 STATION A/B 或分拣机排队区
       └─ 有满箱交换需求
            -> CTU 当前货架面满箱交换
            -> 必要时 WMS/AGV 换面
            -> CTU 下一货架面满箱交换
            -> 剩余未满箱料箱进入分拣机 STATION A/B 或排队区
  -> 分拣机北向机械臂
  -> 扫码平台
  -> 分拣机南向机械臂
  -> 五层货架料箱料格
```

## 3. 统一验收前置条件

### 3.1 系统配置

- WorkLine manifest 已声明粗分机、满箱交换区、分拣机 STATION A/B、FIVE STATION、滚筒线、扫码平台和机械臂角色。
- 满箱交换区是独立 work position，不等同于分拣机 STATION A/B。
- WMS provider profile 已启用 fulfillment、inventory query、inventory transaction 和 event callback。
- ECS/device provider profile 已启用 command、result callback、event callback 和 device status 查询。
- 设备命令派发前必须校验 ECS 设备状态为 `IDLE` 或有效快照。
- WES 不推断扫码平台空闲或机械臂预取容量；平台与南北臂互锁由 PLC/机器人保证。
- WMS↔CTU 的内部执行与实时位置属于 WMS 内部实现；WES 只消费 WMS 批次终态。

### 3.2 主数据与资源

- WMS 中存在可用于验收的 GRN、料盘、单层货架、五层货架、料箱、料格和储位。
- 粗分机出料位至少可调入一个带空料箱的单层货架。
- 满箱交换区可接收从粗分机移出的单层货架。
- 五层货架上有可用于满箱交换的空箱和可用空储位。
- 分拣机 STATION A/B 至少一个位置可接收单层货架。
- 分拣机 FIVE STATION 已有可用五层货架，或可通过 WMS/AGV 调度补入。

### 3.3 观测点

验收时必须能观测以下记录或等价读模型：

| 观测点 | 期望用途 |
| --- | --- |
| `ExecutionSession` | WorkLine 作业上下文，不作为整线串行锁 |
| `ExecutionWorkItem` | 料盘、物料、料箱、货架交换子项的对象级推进状态 |
| `RuntimeInbox` | WMS 普通事件/状态提示与 ECS device event/result 的入站记录 |
| `RuntimeIntentLog` | WES 下发设备命令、WMS fulfillment、WMS 库存事务的 effect ledger |
| `DeviceCommand` | 机械臂、流水线、扫码平台命令及 ACK/RESULT 状态 |
| operation-specific ACK/status/terminal result | 补架、移架、换面、满箱交换、CTU 投箱/退箱等外部履约状态 |
| `RuntimeLocationEvent` | 作业期物理位置事实 |
| `RackPlacement` / `RackBinMount` / `BinPlacement` | 货架、料箱和工作位投影 |
| `BinMaterialMount` / `BinCellOccupancy` | 物料与料格占用事实 |
| `MaterialUnit.location_summary` | 物料当前位置摘要 |
| `RuntimeHold` / `ReconciliationRecord` | scoped hold、冲突和人工恢复依据 |
| `PlaneSnapshot` | 操作台或验收大屏上的当前状态视图 |

## 4. 场景一：粗分机正常入库

### 4.1 前置条件

- 粗分机入口检测、扫码、测量、入料机械臂、流水线、出料机械臂均可用。
- 粗分机入口到出料缓冲路径容量未超过 manifest 上限。
- 启动或入口 admission 不要求出料货架位已有可用格位。
- 出料 step admission 才校验出料单层货架、料箱和料格可用性。

### 4.2 验收步骤

| 步骤 | 触发/操作 | 预期 WES 行为 | 通过标准 |
| --- | --- | --- | --- |
| R-01 | 操作员将料盘放入粗分机入口 | ECS 上报入口有料事件，WES 写入 `RuntimeInbox` | callback 只 ACK，不在响应体返回下一步动作 |
| R-02 | 入口扫码完成 | WES 按权威合同选择 `RS-SD-001` 至 `RS-SD-003` 并记录 evidence | 实际状态、Intent 与 outcome 和所选 case 一致 |
| R-03 | 所选 case 产生设备命令 | WES 按该 case 持久化 `DeviceCommand` | 命令先写 `RuntimeIntentLog`，再 dispatch |
| R-04 | 入料机械臂 ACK | WES 记录 ACK，不认为物理完成 | `DeviceCommand` 处于 accepted/running 等待结果态 |
| R-05a | 入料机械臂 Result callback | WES 按权威合同选择 `RS-SD-004` 至 `RS-SD-008` 或 `RS-SD-010`，并记录命令结果与 measurement evidence | 实际状态、Intent 与 outcome 和所选 case 一致 |
| R-05b | 等待入料命令结果超过 deadline | WES 通过 `TIMER_TIMEOUT` 触发 `RS-SD-009` | 超时 evidence、状态、Intent 与 outcome 和该 case 一致 |
| R-06 | 所选 case 要求 WMS QUERY | WES 执行并记录该 case 要求的 QUERY evidence | QUERY 身份、请求及响应或失败摘要可追踪 |
| R-07 | 窄闭环 outcome 已持久化 | WES 按所选 case 记录命令、Session Hold 或 evidence-only 结果 | 不由本验收文档重新判定分支 |
| R-08 | 所选 case 的后续流水线命令 SUCCESS callback | WES 写入料盘到达出料口事实 | work item 随后进入出料分配 step |
| R-09 | 出料 step admission | WES 查询单层货架、料箱和可用格位投影 | 缺少关键事实时进入 scoped `RuntimeHold` |
| R-10 | 出料位无可用格位 | WES 请求 WMS 补充空箱货架或换架 | 只 hold 当前出料 work item，入口侧按缓冲容量自然反压 |
| R-11 | WMS E08 status query 返回 typed terminal result | WES 校验 ACK/reference 后更新 `RackPlacement`、`RackBinMount` 和可用料格投影 | 可选 callback hint 只唤醒查询，不直接改 owner 状态 |
| R-12 | WES 计算目标料格 | 创建 `CellReservation` 或等价预约 | 唯一约束防止并发双占 |
| R-13 | WES 下发出料机械臂命令 | 指示将料盘投入指定料箱料格 | 命令包含业务对象、目标料箱、目标料格和幂等键 |
| R-14 | 出料机械臂 SUCCESS callback | WES 消费预约，写入投格物理事实 | `BinMaterialMount`、`BinCellOccupancy`、`MaterialUnit.location_summary` 更新 |
| R-15 | WES 通知 WMS PKG 绑定/库存事务 | 通过 `RuntimeIntentLog + WmsInventoryTransactionPort` 发起 | WMS 成功后 work item 业务闭环完成 |
| R-16 | 继续投入后续料盘 | 入料机械臂、流水线、出料机械臂按设备能力并行处理不同对象 | 料盘 N 未完成入格时，入料机械臂可处理料盘 N+1 |

### 4.3 异常验收点

- 粗分机扫码到准入决策异常从权威合同选择对应 case；本验收只核对该 case 的 evidence、状态和 outcome，不重复定义判定条件。
- 入料机械臂、流水线、出料机械臂 `ERROR/OFFLINE/UNKNOWN` 时，不下发新命令，短退避耗尽后创建对应 device 或 work item scope 的 hold。
- 出料机械臂投放成功但 WMS PKG 绑定失败时，本地物理事实保持有效，状态进入 WMS 同步 hold 或 reconciliation。
- 重复 callback 使用 `source_event_id + provider_code + event_type` 幂等处理，同 key 不同 payload 返回 409 并写安全审计。

## 5. 场景二：满箱交换前置分流

### 5.1 场景定位

满箱交换发生在粗分机和分拣机之间，是粗分机移出单层货架后的独立前置分流能力。

```text
粗分机出料单层货架位
  -> WMS/AGV 移出单层货架
  -> 满箱交换区
  -> 满箱判断与交换
  -> 分拣机 STATION A/B 或排队区
```

满箱交换区不与分拣机 STATION A/B 共用。单层货架在满箱交换完成前，不允许进入分拣机
STATION A/B，也不允许被分拣机北向机械臂取料。

### 5.2 前置条件

- 粗分机已完成一个单层货架周期，单层货架上存在已完成初拣的料箱和料格占用投影。
- WES 可以基于 `BinCellOccupancy` 或等价 occupancy metrics 判断料箱是否达到满箱阈值。
- WMS/AGV 可将单层货架从粗分机移出到满箱交换区。
- 满箱交换区可读取单层货架当前可操作面。
- CTU 单次只能操作货架的单面。
- 若源满箱分布在货架两面，必须通过 WMS/AGV 执行 `CHANGE_RACK_FACE` 后再处理另一面。

### 5.3 验收步骤

| 步骤 | 触发/操作 | 预期 WES 行为 | 通过标准 |
| --- | --- | --- | --- |
| F-01 | 粗分机判定单层货架需要移出 | WES 创建 `REMOVE_LOADED_SINGLE_LAYER_RACK` fulfillment | 目标是满箱交换区或交换决策点，不直接进入分拣机 STATION |
| F-02 | WMS E09 status query 返回货架到达 typed terminal result | WES 校验 ACK/reference 后写入 `RuntimeLocationEvent` 并更新 `RackPlacement` | `RackPlacement.work_position_code` 表示满箱交换区 |
| F-03 | WES 扫描该单层货架料箱占用 | 计算达到阈值的满箱集合和未满箱集合 | 判断基于本地投影和 WMS 查询 evidence |
| F-04 | 无满箱交换需求 | WES 创建进入分拣机 STATION A/B 或排队区的履约请求 | 货架未进入满箱交换 work item |
| F-05 | 有满箱交换需求 | WES 创建箱级 `ExecutionWorkItem` 和 `FULL_BOX_EXCHANGE` operation | 每个满箱有独立 correlation，可追踪子 evidence |
| F-06 | WES 按 `rack_code + rack_side` 分组 | 只为当前可操作货架面创建 E11 批次 | 跨货架面满箱不得混入同一 E11 请求 |
| F-07 | 当前面交换 admission | 校验满箱冻结事实与 WMS 可选空箱/目标储位约束 | 具体 CTU 容量和逐箱调度由 WMS 内部收敛 |
| F-08 | WES 请求满箱交换履约 | 提交 `wms.fulfillment.full_box_exchange@v1` | WES 不直连 CTU SDK |
| F-09 | WMS 内部执行满箱取放和空箱补位 | WES 不接收逐箱阶段事件，也不回写推测位置 | E11 在批次终态前保持在途 |
| F-10 | WMS 返回 E11 typed terminal result | WES 校验 ACK、status、terminal result 的同一 provider reference | result 完整返回最终 rack-bin-slot 关系 |
| F-11 | E11 terminal result 校验通过 | WES 按最终关系更新本地物理事实和挂载投影 | 原满箱和换入空箱位置均可解释 |
| F-12 | 当前面批次收敛 | Runtime 校验 terminal result、active projection 和资源收敛 | 只使用批次级权威结果推进请求 |
| F-13 | 另一面仍有满箱 | WES 创建 `CHANGE_RACK_FACE` fulfillment | 换面是独立 WMS/AGV 履约，不是 CTU 子步骤 |
| F-14 | WMS E10 status query 返回换面 typed terminal result | WES 校验 ACK/reference 后更新货架当前可操作面投影 | 下一面 exchange work items 被释放执行 |
| F-15 | 另一面满箱交换 | 重复 F-07 至 F-12 | 两面 E11 均完成或进入 scoped hold |
| F-16 | 满箱交换完成 | WES 将满箱内物料标记为箱级入库物理完成，随后通知 WMS 库存事务 | WMS 失败进入同步 hold/reconciliation，不抹掉本地事实 |
| F-17 | 剩余未满箱料箱处理 | WES 创建进入分拣机 STATION A/B 或排队区的履约请求 | 已满箱交换的物料不再进入分拣机逐件流程 |

### 5.4 资源锁与并发

- 单层货架位于满箱交换区期间，默认锁定整座单层货架，不允许分拣机取料。
- 若现场支持更细粒度并行，必须由 manifest 显式声明安全边界和 ECS 防呆 evidence。
- 换面期间锁定该货架及其 exchange work items，不影响粗分机继续处理其他货架。
- 满箱交换失败只 hold 对应货架、货架面、料箱或 exchange work item，不默认停止分拣机已在处理的其他货架。

### 5.5 异常验收点

- E11 terminal final relations 缺失、重复或与冻结 occupancy 冲突时，父 `FULL_BOX_EXCHANGE` 不得本地完成，必须进入 `RECONCILING` 或 scoped hold。
- `CHANGE_RACK_FACE` 失败、超时或回调货架面与本地投影冲突时，只 hold 该货架和未执行的对应面满箱交换。
- 五层货架空箱不足时，WES 请求 WMS 补充或等待，不得把满箱送入未知储位。
- 单层货架在满箱交换区无满箱需求时，应可直接进入分拣机 STATION A/B 或排队区。

## 6. 场景三：分拣机入库

### 6.1 前置条件

- 单层货架已完成粗分机流程和满箱交换前置分流。
- 进入分拣机的单层货架只包含仍需逐件分拣的剩余物料。
- 分拣机 STATION A/B 至少一个可用，或存在可进入排队区的调度路径。
- FIVE STATION 有可解释五层货架投影，或已创建 WMS 补架 fulfillment。
- 滚筒线入口线、SCAN1、SCAN2、SCAN3、工作位和退料线容量可由 manifest 与 active projection 计算。

### 6.2 CTU 投箱与退箱验收步骤

| 步骤 | 触发/操作 | 预期 WES 行为 | 通过标准 |
| --- | --- | --- | --- |
| S-01 | 分拣机开工 | WES 检测 STATION A/B、FIVE STATION、滚筒线容量 | 缺五层货架时请求 WMS/AGV 补入 |
| S-02 | FIVE STATION 有可用料箱 | WES 创建 CTU 投箱批次 | 批次数量 = `min(入口线空位, CTU 背篓容量, 五层货架可用料箱数)` |
| S-03 | WMS 内部调度 CTU 执行 E12 | WES 只消费批次级 status 与 typed terminal result | WES 保持 ACK 冻结批次在途 |
| S-04 | WMS 提示 E12 状态变化 | WES 只触发 typed status query | 状态 reference 必须与 ACK 一致 |
| S-05 | WMS 返回 E12 typed terminal result | WES 校验冻结成员和逐成员最终事实 | 未扫码前不凭 WMS 内部阶段绑定新物理事实 |
| S-06 | E12 批次收敛 | WES 校验 terminal items 和入口线本地 projection | 父请求查询视图展示批次终态 |
| S-07 | 退料线存在需回架料箱 | WES 按 FIFO 提交 E13 有界候选窗口 | WMS按目标容量与 CTU 容量接纳有序前缀 |
| S-08 | WMS 返回 E13 typed terminal result | WES 按 ACK 冻结成员更新最终位置和挂载关系 | 缺项、乱序、reference 漂移进入 reconciliation |

### 6.3 滚筒线扫码与路由验收步骤

| 步骤 | 触发/操作 | 预期 WES 行为 | 通过标准 |
| --- | --- | --- | --- |
| S-09 | SCAN1 感应到料箱到达 | ECS 上报到位和扫码事件 | callback 写 `RuntimeInbox`，worker 异步解析 |
| S-10 | SCAN1 扫码成功 | WES 将扫码结果写入 `actual_scanned_bin_ids` | 只有命中 `expected_authorized_bin_ids` 才允许 placeholder resolve |
| S-11 | 授权命中 | WES 下发滚筒线进入工作位队列命令 | `ConveyorQueueMembership` 进入工作位队列 |
| S-12 | 授权未命中、重复、缺失或码制冲突 | WES 下发 NG 或 hold/reconciliation 决策 | 未授权料箱不得静默进入工作位 |
| S-13 | SCAN2 感应到料箱到达工作位 | WES 记录工作位到位事件 | 目标料箱可与物料 work item join |
| S-14 | SCAN3 感应到料箱到达 | WES 判断 NG 或退料线 | 非 NG 料箱进入退料线，NG 料箱进入 NG 路径 |

### 6.4 机械臂分拣验收步骤

| 步骤 | 触发/操作 | 预期 WES 行为 | 通过标准 |
| --- | --- | --- | --- |
| S-15 | STATION A/B 上有单层货架 | WES 根据粗分机初拣信息选择待处理物料 | 已满箱交换入库的物料不再被选取 |
| S-16 | WES 下发北向机械臂取料命令 | 从指定单层货架料箱料格取出物料，放到扫码平台 | 首件由 Session 启动，后续只由上一件南向 PICK ACK 解锁；平台互锁由 PLC/机器人负责 |
| S-17 | 北向机械臂 SUCCESS callback | WES 写入物料离开源料格/到达扫码平台事实 | 同步释放或更新源料格占用 |
| S-18 | WES 下发扫码平台扫码命令 | 扫码平台执行物料扫码 | 默认不允许北向机械臂立即取下一件 |
| S-19 | 扫码平台 SUCCESS callback | WES 根据扫码结果计算目标料箱和目标料格 | 目标料箱必须处于滚筒线工作位 |
| S-20 | 目标料格可预约 | WES 创建 `CellReservation` | 满足目标料箱工作位、目标格位可预约、相关等待有 deadline |
| S-21 | 目标料格不可用 | WES 下发换箱、等待下一个料箱或 scoped hold | 等待不能无限期存在 |
| S-22 | WES 下发南向机械臂投料命令 | 将物料投入指定料箱料格 | 命令关联物料 work item 和料箱 work item |
| S-23 | 南向机械臂 SUCCESS callback | WES 消费预约，写入本地物理事实 | `BinMaterialMount`、`BinCellOccupancy`、`MaterialUnit.location_summary` 更新 |
| S-24 | WES 通知 WMS PKG 绑定/库存事务 | WMS 成功后物料上架状态完成 | WMS 失败进入同步 hold/reconciliation |
| S-25 | 南向 PICK ACK | WES 触发下一次北向取料 | 未收到南向 ACK 时不得由 WES 主动触发下一次北向取料 |

### 6.5 异常验收点

- SCAN1 未授权料箱进入 NG 或 scoped hold，不能污染 active projection。
- SCAN2 到位但目标料箱与 expected work item 不匹配时，进入 reconciliation。
- SCAN3 非 NG 料箱必须可进入退料线；退料线满时应停投箱或触发 CTU 退箱，不得继续无限投箱。
- 北向机械臂取料成功后，物料扫码失败时进入 NG 或人工处理路径，源格位事实不得回滚。
- 南向机械臂投料成功但 WMS 同步失败时，本地投格事实保持有效。

## 7. 跨场景通过标准

验收通过必须同时满足：

- 三个场景的成功流全部通过，且每个关键物理动作都有 callback/evidence。
- `PlaneSnapshot` 能展示粗分机、满箱交换区、分拣机 STATION、滚筒线、料箱、物料和 hold 状态。
- 任一对象异常不会默认停止整条 WorkLine，除非安全门、急停或共享设备不可用明确要求整线 hold。
- WMS EFFECT status hint 与 ECS/device/AGV callback 重复、乱序、超时均能进入幂等处理、dead letter、hold 或 reconciliation；hint 不直接写 E08–E14 终态。
- 已满箱交换入库的物料不会再次进入分拣机逐件流程。
- 剩余未满箱料箱的物料能继续进入分拣机 STATION A/B 或排队区。
- WMS 同步失败时，本地物理事实、审计 evidence 和人工恢复路径可观测。

## 8. 不通过判定

出现以下任一情况，验收不通过：

- callback API 同步返回下一步动作，绕过 `RuntimeInbox` 异步处理。
- WES 直接调用 AGV/CTU/RCS SDK，绕过 WMS fulfillment port。
- 单个料盘、料箱或格位异常导致整条 WorkLine 无理由停线。
- 满箱交换区与分拣机 STATION A/B 混用，导致分拣机提前取料。
- CTU 单批次混合处理同一货架两面的源料箱。
- `CHANGE_RACK_FACE` 未作为独立 WMS/AGV 履约建模。
- 出料机械臂或南向机械臂物理投放成功后，因 WMS 同步失败回滚本地位置事实。
- 已满箱交换完成的物料被重复纳入分拣机逐件入库。

## 9. 关联文档

- `docs/architecture/workline-and-plugin-restructuring.md`
- `docs/business/rough_sorter_runtime_flow.md`
- `docs/business/smt_sorter_inbound_workflow_guide.md`
- `docs/integration/wms_rcs_interface_requirements.md`
- `docs/integration/third_party_integration_whitepaper.md`
