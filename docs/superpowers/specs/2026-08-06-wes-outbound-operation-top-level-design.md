---
title: WES 出库操作顶层设计
status: Reviewed
created_at: 2026-08-06
updated_at: 2026-08-06
scope: SMT 自动出库 PickingTask 的接入、来源绑定、调度、执行、异常、补料与完成边界
system_stage: pre_release
migration_strategy: direct_replacement
related:
  - docs/architecture/SRS.md
  - docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
  - docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md
  - docs/superpowers/plans/2026-08-05-wes-wms-thin-access-convergence.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
  - docs/hardware/wms_rcs_interface_requirements.md
---

# WES 出库操作顶层设计

## 1. 文档定位

本文定义 SMT 自动出库的目标业务合同，回答以下问题：

1. WMS 以什么执行对象驱动 WES。
2. 来源为何在任务开始时才由 WMS 原子绑定。
3. WES 如何组织双面来源货架和双面目标生产转运货架。
4. NG、未知结果、缺料、恢复、取消和完成如何闭环。
5. wire、可靠生命周期、执行插件和 MOCK 数据分别由谁拥有。

权威关系：

- `docs/architecture/SRS.md` 记录原始产品需求和参与方职责。
- 最小执行架构 SPEC 定义核心对象、可靠性和扩展边界。
- 本文定义自动出库业务对象、状态和执行不变量。
- WMS 北向合同定义获批的 operation、method、path、wire DTO、错误和幂等语义。
- Master Plan 与分阶段计划定义实现顺序，不得反向改变本文业务决策。
- `docs/hardware/wms_rcs_interface_requirements.md` 是硬件厂商原始输入，不是当前架构或 wire 真源。

系统尚未发布。本设计直接替换旧目标，不保留旧字段、别名、fallback、双读、双写或迁移路径。

## 2. 核心裁决

- WES 不接收或拆解出库单、拣货单和波次单，只执行 WMS 下发的 `PickingTask`。
- `PickingTask` 排队时只描述目标项，不绑定来源、物料料盘或具体目标货架。
- WES 选中任务后进入 `STARTING`，请求 WMS 一次性返回覆盖全部任务项的原子 `SourceBinding`。
- WMS 负责库存资格、库存锁、料格冻结、跨任务分配和替代来源；WES 不以库存查询结果自行拼装来源方案。
- 完整来源绑定成功后，才允许并行调度目标空架和第一台来源货架。
- `PkgID` 是料盘唯一业务身份，不定义其他身份字段或兼容别名。
- 一台已绑定的目标生产转运货架由一张 `PickingTask` 独占，直至完成或取消处置闭环。
- 仅执行有任务项的目标面：A 单面不旋转，B 单面跳过 A 执行组后旋转，A/B 双面按 A 后 B。
- 来源货架进入工作位时由 RCS 保证 A 面朝向设备；WES 在当前目标面内按来源 A 后 B 执行。
- 只有成功且可关联的 `COMMAND_RESULT` 才能推进物理位置；ACK 不代表物理完成。
- 每个料盘 PICK+PUT 成功后产生独立位置变化事实；整张任务完成另行通知，二者不得合并或双写。

## 3. 范围与非目标

### 3.1 范围

- `PickingTask` 接入、排队、优先级更新和 WMS 调度结果的执行。
- `STARTING` 阶段完整来源绑定、无完整来源结果和原子替代来源批次。
- 来源为 `BIN_CELL` 或 `RACK_SLOT` 的完整料盘搬运。
- 双面来源货架、双面目标生产转运货架及工作位执行顺序。
- 逐项位置变化、任务完成、NG、未知结果、暂停、补料、恢复和取消。
- WMS 经 RCS 调架，WES 经 ECS Adapter 下发设备命令。

### 3.2 非目标

- WES 订单管理、波次计算、库存选择、库存锁、料格冻结或跨任务来源仲裁。
- WES 规划 RCS 路径、车辆交通或货架旋转机构动作。
- WES 处理 PDA、人工库存账务或生产交付后的 WMS 流程。
- 跨任务合并、全局来源优化器、通用补偿器或自动猜测未知现场状态。
- 为旧版本、未来货架类型或未批准协议提供兼容或扩展框架。

## 4. 权威边界

| 参与方 | 唯一权威 | 不负责 |
| --- | --- | --- |
| WMS | 订单/波次、库存、SixInOne/PKG、来源分配与替代、库存锁和料格冻结、货架主数据及业务角色、任务总序与启动授权、目标面业务顺序、优先级、取消/恢复、生产交付 | 设备内部动作、WES 设备发送时机 |
| WES | `PickingTask` 本地执行状态、工作位执行、设备发送/等待/暂停、动作因果链、作业期投影、异常证据和可靠业务事实义务 | 任务选择、库存主账、来源/目标/路线选择、跨任务分配、RCS 路径、货架业务角色裁决 |
| RCS | 货架搬运、排队、路径、旋转和到位结果 | 库存、任务项和拣料顺序 |
| ECS/设备 | 扫码、抓取、放置、滚筒输送、安全互锁和最终物理结果 | 库存、任务优先级、替代来源 |

WES 可以校验 WMS 返回的任务、来源、目标和顺序是否完整、无歧义且物理可执行，但该校验不会使 WES 获得业务或库存
权威。结果不可执行时暂停并反馈 WMS，不得选择另一任务、来源、目标、面顺序或业务处置。

## 5. 两阶段业务合同

### 5.1 第一阶段：排队 `PickingTask`

WMS 完成订单、波次和目标储位规划后下发 `PickingTask`。任务只表达“哪些目标项需要完成”，使排队期间的入库、
库存变化和更高优先级任务不会被过早来源绑定阻塞。

```json
{
  "task_id": "PT-OUT-A001",
  "task_version": 1,
  "priority": 50,
  "issued_at": "2026-08-06T09:30:00+08:00",
  "workline_code": "SMT_OUTBOUND_01",
  "task_items": [
    {
      "item_id": "PT-OUT-A001-I01",
      "target_locator": {
        "target_face": "A",
        "target_slot_id": "A-01"
      }
    },
    {
      "item_id": "PT-OUT-A001-I02",
      "target_locator": {
        "target_face": "B",
        "target_slot_id": "B-01"
      }
    }
  ]
}
```

| 字段 | 含义 |
| --- | --- |
| `task_id` | WMS 生成的任务稳定标识。 |
| `task_version` | 同一任务业务内容的版本；幂等和冲突 wire 规则由北向合同冻结。 |
| `priority` | WMS 给出的业务优先级，数值越大越优先。 |
| `issued_at` | WMS 下发时间，用于同优先级排序。 |
| `workline_code` | 目标工作线标识。 |
| `task_items[]` | 任务必须完成的目标项集合。 |
| `item_id` | 任务内稳定且唯一的任务项标识。 |
| `target_locator.target_face` | 目标面，取值 `A` 或 `B`。 |
| `target_locator.target_slot_id` | 目标面内唯一储位；一个储位只放一个完整料盘。 |

目标货架类型 `DOUBLE_SIDED_REEL_RACK` 和入位初始朝向 A 是 WorkLine 静态合同，不重复进入任务 Payload。
排队任务不得出现 SixInOne、`PkgID`、来源位置或具体目标 `rack_id`。

### 5.2 第二阶段：`STARTING` 原子 `SourceBinding`

WES 选中任务后请求 WMS 生成完整来源方案。成功结果必须精确覆盖全部任务项；缺项、多项、重复项或部分成功均不生效。

```json
{
  "task_id": "PT-OUT-A001",
  "task_version": 1,
  "source_bindings": [
    {
      "item_id": "PT-OUT-A001-I01",
      "six_in_one": {
        "HHPN": "HHPN-001",
        "MfrPN": "MFR-001",
        "Qty": 1000,
        "DateCode": "202632",
        "LotCode": "LOT-001",
        "PkgID": "PKG-000101"
      },
      "source_locator": {
        "type": "BIN_CELL",
        "rack_id": "SRC-RACK-010",
        "rack_face": "A",
        "bin_id": "BIN-010-01",
        "cell_id": "CELL-01",
        "top_to_bottom_sequence": 1
      }
    },
    {
      "item_id": "PT-OUT-A001-I02",
      "six_in_one": {
        "HHPN": "HHPN-002",
        "MfrPN": "MFR-002",
        "Qty": 500,
        "DateCode": "202631",
        "LotCode": "LOT-002",
        "PkgID": "PKG-000201"
      },
      "source_locator": {
        "type": "RACK_SLOT",
        "rack_id": "RETURN-RACK-003",
        "rack_face": "B",
        "slot_id": "B-03"
      }
    }
  ]
}
```

| 字段 | 含义 |
| --- | --- |
| `source_bindings[]` | 与任务项集合精确相等并原子生效的来源绑定全集。 |
| `item_id` | 关联原任务项。 |
| `six_in_one` | 客户/WMS 约定的完整 6 合 1/PKG 物料事实。 |
| `HHPN` | 客户物料编码。 |
| `MfrPN` | 制造商物料编码。 |
| `Qty` | 该完整料盘的物料数量，不是待搬运料盘数。 |
| `DateCode` | 生产日期码。 |
| `LotCode` | 批次码。 |
| `PkgID` | 完整料盘的唯一业务身份。 |
| `source_locator.type` | `BIN_CELL` 或 `RACK_SLOT`。 |
| `rack_id` / `rack_face` | 来源货架及来源面。 |
| `bin_id` / `cell_id` | `BIN_CELL` 的料箱和料格。 |
| `top_to_bottom_sequence` | 同一料格内从顶部向下的实际可达顺序，从 1 连续递增。 |
| `slot_id` | `RACK_SLOT` 的单料盘储位。 |

`BIN_CELL` 中待拣绑定必须构成当前顶部的连续前缀。不同料格之间没有固定业务顺序；WES 可在当前目标面内按货架、
来源面和就绪状态调整，但不得改变同一料格内的后进先出顺序。同一料格从顶部向下关联的目标面只能是单面，或
`A* → B*`；`B → A`、`A → B → A` 等序列与目标架 A 后 B 的固定顺序冲突，整批来源绑定不得生效。

无完整来源方案是合法业务结果：

```json
{
  "task_id": "PT-OUT-A001",
  "task_version": 1,
  "binding_status": "NO_COMPLETE_BINDING"
}
```

此结果使任务进入启动前缺料等待：不申请目标架、不调来源架、不占工作位，并释放当前执行机会。

### 5.3 原子替代来源批次

替代来源只适用于满足资格的未完成项。批次中任一项不合格，整批不生效。

```json
{
  "task_id": "PT-OUT-A001",
  "task_version": 1,
  "replacement_source_bindings": [
    {
      "item_id": "PT-OUT-A001-I02",
      "six_in_one": {
        "HHPN": "HHPN-002",
        "MfrPN": "MFR-002",
        "Qty": 500,
        "DateCode": "202633",
        "LotCode": "LOT-009",
        "PkgID": "PKG-000909"
      },
      "source_locator": {
        "type": "BIN_CELL",
        "rack_id": "SRC-RACK-020",
        "rack_face": "A",
        "bin_id": "BIN-020-04",
        "cell_id": "CELL-02",
        "top_to_bottom_sequence": 1
      }
    }
  ]
}
```

替代资格同时要求：任务项未完成；原 `PkgID` 的当前物理位置已知且确认未被成功 PICK/PUT；没有在途 PICK/PUT；
没有未知物理结果。新绑定必须重新携带完整 SixInOne 和来源位置。旧绑定只保留为不可执行历史证据，不得与新绑定
并行有效，也不得改变目标项。

初始或替代来源结果只接受与当前有效请求及当前绑定基线相匹配的响应。取消、恢复或新替代批次产生后，迟到结果不得
生效；具体请求关联和绑定代际字段由北向 wire 合同冻结。

### 5.4 分阶段校验

`PickingTask` 接入时整单校验：字段闭集、任务身份、任务项唯一性、目标储位唯一性和目标面合法性。出现来源、物料、
具体目标架或未知字段时整单拒绝，由 WMS 修正后重发。

`SourceBinding` 返回时整批校验：任务项精确覆盖、SixInOne 完整、`PkgID` 唯一、来源联合类型完整、LIFO 连续前缀和
物理结构合法。结构或顺序非法属于合同拒绝，不得伪装成无库存；本次绑定不生效，任务保持 `STARTING` 并等待 WMS
修正或明确取消。

## 6. 状态与调度

| 状态 | 关键不变量 | 主要迁移 |
| --- | --- | --- |
| `QUEUED` | 无来源绑定、无目标架、无工作位 | `STARTING`、`CANCELLING` |
| `STARTING` | 初次启动请求完整绑定；缺料恢复时校验 WMS 下发的替代批次和保留现场 | `EXECUTING`、`WAITING_STOCK`、`PAUSED`、`CANCELLING` |
| `EXECUTING` | 有有效来源绑定；调架成功后有独占目标架 | `PAUSED`、`WAITING_STOCK`、`CANCELLING`、`COMPLETING` |
| `WAITING_STOCK` | 保存缺料原因；可保留目标架绑定，但正常情况下不占工作位 | WMS 明确恢复并再次被选中后进入 `STARTING`，或 `CANCELLING` |
| `PAUSED` | 整单停止新动作；未知位置保持 `unknown` | WMS 明确恢复并再次被选中后进入 `STARTING`，或 `CANCELLING` |
| `CANCELLING` | 停止新动作并等待动作、处置和清位闭合 | `CANCELLED` |
| `COMPLETING` | 等待逐项事实、完成通知和清位闭合 | `COMPLETED` |
| `CANCELLED` / `COMPLETED` | 终态 | 无 |

`WAITING_STOCK` 有两种原因，不得混淆：

- 启动前无完整来源：没有目标架，释放执行机会。
- 执行中因 NG/来源失效且 WMS 明确返回“无完整替代方案”：无论目标架尚为空或已部分装载，均移入“缺料待补货架缓存区”，保留
  任务、目标架绑定和已完成目标项，不得交付生产。

目标架只有在成功移出工作位后才释放执行机会；缓存不可用、搬运失败或现场未清空时，保持真实占位并阻塞后续任务，
等待 RCS 重试或人工异常处理。

WMS 必须明确恢复具体 `PickingTask`。恢复只使任务重新具备候选资格，不抢占当前任务；任务再次被选中后进入
`STARTING`。启动前缺料任务重新请求完整来源；执行中缺料任务校验 WMS 下发的原子替代批次并调回原目标架。

WMS 必须为尚未开始的任务返回无歧义的总序或明确的下一任务启动授权。优先级更新只影响尚未开始的任务，不抢占执行中
任务；WES 不根据 `priority`、`issued_at` 或 `task_id` 自行选择另一任务。

## 7. 调架与双面执行

### 7.1 启动顺序

1. 工作位空闲后，WES 执行 WMS 当前有效的下一任务启动授权并进入 `STARTING`；授权任务暂不可执行时保持等待并反馈，
   不跳选其他任务。
2. WES 请求 WMS 生成覆盖全部任务项的原子来源绑定。
3. 无完整方案时进入启动前缺料等待，不请求任何货架。
4. 绑定成功后，WES 才请求 WMS 分配并锁定目标空架，同时预调第一台来源货架。
5. 两架到位并完成身份、面向和工作位校验后进入 `EXECUTING`。

工作区只要求一个目标工作位、一个当前来源工作位和最多一个下一来源货架在途或等待，不构建额外缓存调度器。

### 7.2 目标面顺序

| 有任务项的目标面 | 执行规则 |
| --- | --- |
| 仅 A | 执行 A，目标架不旋转到 B。 |
| 仅 B | 不生成 A 面执行组；目标架旋转到 B 后执行。 |
| A 和 B | 先完成 A，再旋转一次完成 B。 |

### 7.3 来源货架顺序

来源货架进入工作位时，RCS 必须保证 A 面朝向设备。对当前目标面：

1. 先执行当前来源架 A 面的全部可执行项。
2. 需要时旋转来源架，执行其 B 面的全部可执行项。
3. 当前来源架对当前目标面无剩余需求后，通常退回库区，再处理下一来源架。
4. 目标面切换后，若当前来源架仍有需求则可留在工作位；先处理当前朝向的一面，必要时再旋转处理另一面。

来源架暂不可用时，WES 可在同一目标面内调整来源架顺序并按受控指数退避重试；不得提前切换目标面或自行换库存。

## 8. 料盘执行与事实

单个任务项的物理推进链固定为：校验来源架/面/箱/格或储位 → 扫描并核对 `PkgID` 与 SixInOne → PICK+PUT →
成功 `COMMAND_RESULT` → 更新作业期投影 → 建立可靠位置变化事实。

- ACK、queued 或 dispatched 只证明接收/派发，不推进料盘位置。
- 成功 `COMMAND_RESULT` 是来源到目标位置变化的唯一物理证据。
- 超时、断连或结果不可判定时，当前位置设为 `unknown`，整张 `PickingTask` 进入 `PAUSED`。
- 未知结果未人工消歧前，不得重发动作、继续其他项、替换来源、取消终结或推断料盘仍在来源。
- 位置变化事实的可靠重提不得重放设备命令。

逐项位置变化业务示例：

```json
{
  "fact_id": "MOVE-PT-OUT-A001-I01-001",
  "task_id": "PT-OUT-A001",
  "task_version": 1,
  "item_id": "PT-OUT-A001-I01",
  "PkgID": "PKG-000101",
  "from_locator": {
    "type": "BIN_CELL",
    "rack_id": "SRC-RACK-010",
    "rack_face": "A",
    "bin_id": "BIN-010-01",
    "cell_id": "CELL-01"
  },
  "to_locator": {
    "rack_id": "TARGET-RACK-001",
    "target_face": "A",
    "target_slot_id": "A-01"
  },
  "command_result_id": "RESULT-001",
  "occurred_at": "2026-08-06T09:42:00+08:00"
}
```

`fact_id` 标识一次已发生的位置变化；其 wire 命名、信封和远端幂等规则由北向合同批准。每个任务项单独产生事实，
不得用整单完成通知代替。

## 9. 异常、NG 与补料

| 来源异常 | 隔离单位 | 可继续范围 | 后续 |
| --- | --- | --- | --- |
| `BIN_CELL` 料箱异常 | 整个 `bin_id` | 同架其他正常 bin | NG 料箱经滚筒线送专用出口，WMS/PDA 人工确认移走；WMS 规划替代来源。 |
| `RACK_SLOT` 料盘异常 | `slot_id + PkgID` | 同架其他正常 slot | WES 报告事实，WMS 规划替代来源。 |
| 整架不可用 | `rack_id` | 其他来源架 | 该架全部未执行绑定失效，WES 调整其他来源顺序，WMS 重新规划。 |

NG 事实不自动授权替代。只有满足 §5.3 资格且 WMS 返回原子替代来源批次后，WES 才能继续未完成项。只有 WMS
针对当前有效替代请求明确返回“无完整替代方案”，才能进入执行中缺料等待；尚未收到响应、消息延迟或处理超时均
不能被解释为无库存。

目标架或目标储位异常、扫码冲突、LIFO 冲突以及无法确定的 PICK/PUT 结果均停止整张任务的新动作。明确的结构/身份
冲突报告 WMS 后等待修正；未知物理结果必须人工消歧。恢复原任务只能由 WMS 明确下达。

## 10. 取消

取消不触发通用补偿，也不授权 WES改变目标架业务角色：

1. 接收取消后进入 `CANCELLING`，停止创建新动作。
2. 已下发动作等待成功/失败 `COMMAND_RESULT`；未知结果先人工消歧。
3. WES 依据稳定物理事实向 WMS 报告任务、料盘和目标架现状。
4. WMS 决定目标架继续发料、转为退料货架、转作新任务或退回，并规划 RCS 目的地。
5. WMS 接受处置且工作位取得物理清空证据后，任务才进入 `CANCELLED`。

尚无目标架且无在途动作的排队或启动前缺料任务，可在 WMS 接受取消后直接终结；已有目标架时不得跳过处置与清位。

## 11. 完成

任务完成条件：

- 所有计划目标项完成，空目标面没有执行义务。
- 每项 PICK+PUT 均有成功 `COMMAND_RESULT`，不存在在途或 `unknown` 动作。
- 每项位置变化事实已由 WMS 接受；不得让整单完成通知越过任何未接受的逐项事实。
- 独立的 PickingTask 完成通知已由 WMS 接受。
- 目标架已移入“拣货完成”货架区，工作位有物理清空证据。

任务完成业务示例：

```json
{
  "fact_id": "COMPLETE-PT-OUT-A001-001",
  "task_id": "PT-OUT-A001",
  "task_version": 1,
  "target_rack_id": "TARGET-RACK-001",
  "completed_at": "2026-08-06T10:05:00+08:00"
}
```

完成通知不携带、不聚合也不替代逐项位置变化事实。生产交付及后续异常属于 WMS 流程，WES 不关注。

## 12. WMS 语义交互面

WMS → WES：创建目标项任务、更新排队优先级、下发原子替代来源批次、恢复指定任务、取消指定任务，以及获批的人工结果。

WES → WMS：请求 STARTING 完整来源绑定、报告来源 NG/身份异常、提交逐项位置变化事实、提交独立任务完成事实、
报告取消稳定现场并请求货架处置。具体 operation 名、method、path、DTO、同步/异步结果和幂等规则只由北向合同冻结。

来源绑定、逐项位置变化和任务完成是三个不同业务语义，不得复用一个任务快照接口。库存查询、预留或释放不得被 WES
组合成 PickingTask 来源方案。

## 13. 阶段与所有权

| 所有者 | 本场景职责 |
| --- | --- |
| Phase 1/2 | 提供已验收的测试治理与无业务语义 HTTP 传输事实，不以出库行为反向验收基础能力。 |
| Phase 3 WMS ACL | 经 WMS 批准后拥有来源绑定、逐项位置事实、任务完成、NG、取消/恢复等 wire DTO 与单次调用结果。 |
| Phase 4 最小平台 | 拥有可靠派发、幂等义务、动作在途/结果、unknown 投影、重提资格和持久化证据。 |
| Phase 8 自动出库插件 | 拥有本文状态、队列、双面顺序、NG 隔离、替代资格、空面跳过及恢复规则。 |
| WMS/RCS/ECS Adapter | 只翻译各自获批 wire，不持有上述业务生命周期。 |

Phase 3 当前仍阻断在消费者矩阵和 WMS wire 批准，本文不得被解释为 method/path 已获批或可以进入实施。

## 14. Fixture 与验收所有权

| 资产 | 唯一所有者 | 验证范围 |
| --- | --- | --- |
| WMS wire DTO/fixture | WMS ACL 合同测试 | 获批 wire、闭集字段、编码和错误映射。 |
| 出库业务场景/fixture | 自动出库插件 | 状态、顺序、NG、补料、恢复和取消。 |
| 厂商命令/结果 fixture | 对应设备 Adapter | 厂商 Payload、ACK、结果和原始码映射。 |
| MOCK 场景数据 | MOCK 自动化环境 | 跨系统业务验收，不作为 wire、插件或 Adapter 真源。 |

各层可用正式 schema 校验自己的数据，但不得共用同一 fixture 文件作为多层真源。

最低验收场景：

| 场景 | 通过标准 |
| --- | --- |
| 排队任务只含目标项 | 接纳；出现来源、SixInOne、具体目标架或未知字段时整单拒绝。 |
| 来源绑定精确覆盖全部项 | 原子生效后才并行调目标架和首个来源架。 |
| 来源绑定缺项/重复/字段缺失/PkgID 重复/LIFO 非连续 | 整批不生效；不得部分执行。 |
| 启动前无完整来源 | 无目标架、无来源架、不占工作位，后续任务可继续。 |
| WMS 明确返回无完整替代方案 | 执行中的目标架进入待补缓存，不交付生产；成功清位后才释放工作位。 |
| WMS 恢复等待任务 | 重新竞争下一工作位机会，不抢占当前任务。 |
| 未知 PICK/PUT 结果 | 当前位置 `unknown`，整单暂停且不重发。 |
| 每项成功与整单完成 | 逐项位置事实与任务完成事实分别可靠建立。 |
| 替代项不合格或批内一项非法 | 整批替代不生效。 |
| BIN_CELL / RACK_SLOT / 整架异常 | 分别按 bin、slot+PkgID、rack 隔离。 |
| 目标仅 A / 仅 B / A+B | 分别为不旋转、跳过 A 后旋转、A 后 B。 |
| 取消存在在途或未知动作 | 等待终态/人工消歧；WMS 接受处置且清位后才取消。 |
| 完成时工作位未清空 | 不得进入 `COMPLETED`。 |

## 15. 评审结论

本轮业务裁决修订已经独立复审，以下一致性检查均已通过：

- `PickingTask` 与 `SourceBinding` 两阶段边界是否无重复来源权威。
- 启动前缺料与执行中缺料是否正确区分目标架处置。
- 未知结果、替代来源、取消和完成的终结条件是否闭合。
- WMS 合同、最小架构 SPEC、Phase 3 计划和 Master Plan 是否同步。
- 所有 JSON 均可解析，fixture 保持单一所有者。

本文通过的是业务设计评审，不表示 WMS wire 已批准，也不改变 Phase 3 `BLOCKED_AT_TASK_1` 状态。
