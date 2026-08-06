---
title: WES 出库操作顶层设计
status: Reviewed
created_at: 2026-08-06
updated_at: 2026-08-06
scope: SMT 自动出库工作线的 PickingTask 接入、调度、执行、异常、补料与完成边界
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

本文定义自动出库场景的目标业务合同，是最小执行架构在该场景下的顶层设计。它回答四个问题：

1. WMS 以什么业务对象驱动 WES。
2. WES 如何组织双面来源货架和双面目标生产转运货架的执行。
3. NG、缺料、暂停、恢复、取消和完成如何处理。
4. Phase 1–3 已有能力与后续最小平台、Adapter、业务插件分别承担什么职责。

文档权威关系如下：

- `docs/architecture/SRS.md` 定义产品需求和参与方职责。
- 最小执行架构 SPEC 定义 WES 核心对象、可靠性和扩展边界。
- 本文定义自动出库业务对象、状态和执行不变量。
- WMS 北向合同定义 method、path、wire DTO、错误码和幂等承诺。
- Master Plan 和 Phase 3 计划定义实现顺序，不得反向改变本文业务决策。
- `docs/hardware/wms_rcs_interface_requirements.md` 只作为原始业务输入保留，不是当前架构或 wire 真源。

本系统尚未发布。本设计直接替换“WES 接收工单、查询出库单或波次并自行拆解”的旧目标，不保留旧接口、旧字段、
别名、fallback、双读、双写或数据迁移。

## 2. 核心结论

自动出库由 WMS 下发的执行级 `PickingTask` 驱动：

- WES 不接收 SAP 工单，不生成滚动波次，不读取或解释出库单、拣货单和波次单。
- WMS 负责订单、波次、库存、来源分配、目标储位规划和业务优先级。
- WES 负责 `PickingTask` 整单校验、排队、工作位选择、设备动作编排、局部执行顺序、证据和异常隔离。
- 一台目标生产转运货架在任务存续期间始终由一个 `PickingTask` 独占。
- 目标货架先完成 A 面，再旋转一次完成 B 面；来源货架每次进入工作位时必须 A 面朝向设备。
- 料箱料格中的料盘按物理可达顺序后进先出。WMS 必须提供精确 `reel_id`，并按顶部到下层排列。
- 目标货架单储位只放一个完整料盘，不堆叠。
- WMS 只能为未完成项追加替代来源，不得修改已开始任务的既有目标项。

## 3. 范围与非目标

### 3.1 范围

- 自动设备从来源货架取完整料盘并放入生产转运货架。
- 来源可以是“货架 → 料箱 → 料格 → 堆叠料盘”，也可以是“退料/转运货架 → 储位 → 单料盘”。
- 目标生产转运货架和来源货架均为双面货架。
- 一个目标工作位、一个当前来源工作位，以及最多一个下一来源货架在途或等待。
- 来源 NG、来源暂时不可用、库存不足、目标异常、补料、取消和恢复。
- WMS 经 RCS 调度货架搬运和旋转，WES 通过可靠搬运目标跟踪任务级事实。
- WES 经 ECS Adapter 下发厂商已支持的设备长命令，并以 `COMMAND_RESULT` 作为物理推进证据。

### 3.2 非目标

- WES 订单管理、波次计算、库存优化或替代库存选择。
- WES 规划 RCS 路径、车辆调度、交通管制或货架旋转机构动作。
- WES 处理 WMS 的 PDA 页面、人工库存账务或生产交付流程。
- 自动清线、自动猜测现场状态、通用恢复引擎或跨任务全局路径优化。
- 为未来货架类型、协议、安全机制或多工厂部署预留扩展框架。
- 将具体出库业务行为写入 WES 核心测试，或以该业务成功证明 Phase 2 HTTP 基础能力正确。

## 4. 权威边界

| 参与方 | 唯一权威 | 不负责 |
| --- | --- | --- |
| WMS | 订单/波次、库存、精确来源分配、货架主数据和业务角色、优先级、空架选择与锁定、补料、取消/恢复、生产交付 | 设备内部动作、WES 工作位执行顺序 |
| WES | `PickingTask` 接入与本地状态、队列选择、工作位和对象投影、动作因果链、NG/暂停证据 | 库存主账、替代来源选择、RCS 路径和生产交付 |
| RCS | AGV/CTU 路径、运输、到位姿态和旋转执行 | `PickingTask` 业务内容、料盘拣放 |
| ECS/设备 | 扫码、抓取、放置、滚筒输送、安全互锁和最终物理结果 | 库存、任务优先级、来源替代 |

WES 保存的货架、储位、料箱、料格和料盘均为作业期投影，不得升级为 WMS 主账。

## 5. `PickingTask` 业务合同

### 5.1 为什么使用执行任务而不是单据

订单、波次和库存分配是 WMS 业务决策。WES 若再次读取并拆解这些单据，会形成第二套业务规则和来源选择权。
`PickingTask` 因此必须是 WMS 已完成业务计算后的执行合同，而不是订单的镜像。

### 5.2 最小结构

```text
PickingTask
├── task_id
├── task_version
├── priority
├── issued_at
├── workline_code
├── target_requirement
│   ├── physical_type = DOUBLE_SIDED_REEL_RACK
│   └── initial_face = A
└── task_items[]
    ├── item_id
    ├── reel_id
    ├── material_fact
    ├── source_locator
    │   ├── rack_id + rack_face
    │   ├── BIN_CELL: bin_id + cell_id + top_to_bottom_sequence
    │   └── RACK_SLOT: slot_id
    └── target_locator
        └── target_face + target_slot_id
```

`target_requirement` 在任务排队时不包含具体目标 `rack_id`。WES 选中任务后才请求 WMS 分配并锁定空架。

`task_items[]` 是唯一执行明细。`SourceRacks/Bins/Cells` 是 WES 根据 `source_locator` 派生的调度分组，
不是另一套可独立修改的任务结构。这样可以用一个合同覆盖两类来源：

- `BIN_CELL`：料箱料格内堆叠料盘。
- `RACK_SLOT`：退料货架或转运货架单储位单料盘。

### 5.3 JSON 语义示例

示例覆盖：

- `BIN_CELL` 与 `RACK_SLOT` 两类来源。
- 同一料格内两个料盘按顶部到下层排列，并形成合法的目标 A 面到 B 面顺序。
- 目标货架 A、B 两面储位。
- 任务排队时只声明目标货架条件，不提前绑定具体目标 `rack_id`。

```json
{
  "task_id": "PT-OUT-A001",
  "task_version": 1,
  "priority": 50,
  "issued_at": "2026-08-06T09:30:00+08:00",
  "workline_code": "SMT_OUTBOUND_01",
  "target_requirement": {
    "physical_type": "DOUBLE_SIDED_REEL_RACK",
    "initial_face": "A"
  },
  "task_items": [
    {
      "item_id": "PT-OUT-A001-I01",
      "reel_id": "REEL-000101",
      "material_fact": {
        "material_id": "MAT-0001"
      },
      "source_locator": {
        "type": "BIN_CELL",
        "rack_id": "SRC-RACK-010",
        "rack_face": "A",
        "bin_id": "BIN-010-01",
        "cell_id": "CELL-01",
        "top_to_bottom_sequence": 1
      },
      "target_locator": {
        "target_face": "A",
        "target_slot_id": "A-01"
      }
    },
    {
      "item_id": "PT-OUT-A001-I02",
      "reel_id": "REEL-000102",
      "material_fact": {
        "material_id": "MAT-0001"
      },
      "source_locator": {
        "type": "BIN_CELL",
        "rack_id": "SRC-RACK-010",
        "rack_face": "A",
        "bin_id": "BIN-010-01",
        "cell_id": "CELL-01",
        "top_to_bottom_sequence": 2
      },
      "target_locator": {
        "target_face": "B",
        "target_slot_id": "B-01"
      }
    },
    {
      "item_id": "PT-OUT-A001-I03",
      "reel_id": "REEL-000201",
      "material_fact": {
        "material_id": "MAT-0002"
      },
      "source_locator": {
        "type": "RACK_SLOT",
        "rack_id": "RETURN-RACK-003",
        "rack_face": "B",
        "slot_id": "B-03"
      },
      "target_locator": {
        "target_face": "B",
        "target_slot_id": "B-02"
      }
    }
  ]
}
```

#### PickingTask 字段定义

| 字段 | 含义与约束 |
| --- | --- |
| `task_id` | WMS 分配的 PickingTask 稳定业务标识。 |
| `task_version` | WMS 下发的整单版本；与 `task_id` 共同参与接入幂等和内容冲突校验。 |
| `priority` | WMS 给出的业务优先级；数值范围由 wire 合同冻结，WES 按值降序选择任务。 |
| `issued_at` | WMS 下发时间；相同优先级时按该时间升序排序。 |
| `workline_code` | 任务指定的自动出库工作线。 |
| `target_requirement` | 目标生产转运货架必须满足的条件；排队阶段不包含具体目标 `rack_id`。 |
| `target_requirement.physical_type` | 目标货架物理类型；本场景固定为双面料盘货架。 |
| `target_requirement.initial_face` | 目标架进入工作位时朝向设备的面；本场景固定为 `A`。 |
| `task_items[]` | 唯一执行明细集合；数组顺序不代表 WES 的全局设备动作顺序。 |
| `task_items[].item_id` | 单个任务项在 PickingTask 内的稳定标识，也是追加替代来源时的关联键。 |
| `task_items[].reel_id` | WMS 指定的精确料盘身份，不允许 WES 按物料或数量自行替代。 |
| `task_items[].material_fact` | WMS 提供的最小物料核对事实；只用于执行校验和证据关联，不把 WES 变成库存主账。 |
| `task_items[].material_fact.material_id` | 料盘对应的物料标识。 |
| `task_items[].source_locator` | WMS 分配的精确来源位置。 |
| `task_items[].source_locator.type` | 来源类型：`BIN_CELL` 表示料箱料格堆叠料盘，`RACK_SLOT` 表示货架单储位料盘。 |
| `task_items[].source_locator.rack_id` | 来源货架标识。 |
| `task_items[].source_locator.rack_face` | 来源位置所在货架面，取值为 `A` 或 `B`。 |
| `task_items[].source_locator.bin_id` | `BIN_CELL` 来源的料箱标识；`RACK_SLOT` 不提供。 |
| `task_items[].source_locator.cell_id` | `BIN_CELL` 来源的料格标识；`RACK_SLOT` 不提供。 |
| `task_items[].source_locator.top_to_bottom_sequence` | 同一料格内从顶部到底部的 1 起始连续序号；较小序号必须先完成。 |
| `task_items[].source_locator.slot_id` | `RACK_SLOT` 来源的储位标识；`BIN_CELL` 不提供。 |
| `task_items[].target_locator` | WMS 已规划的精确目标位置。 |
| `task_items[].target_locator.target_face` | 目标生产转运货架面，取值为 `A` 或 `B`。 |
| `task_items[].target_locator.target_slot_id` | 目标面内的唯一储位标识；一个储位只能对应一个任务项。 |

`task_items[]` 数组顺序不是 WES 的全局设备动作顺序；`top_to_bottom_sequence` 只表达同一料格内不可违反的
物理可达顺序。前两个任务项展示同一料格的合法跨面顺序：顶部料盘进入目标 A 面，下一层料盘进入目标 B 面。
WES 仍按“目标 A 面完成后再处理目标 B 面”派生来源架和来源面的执行分组。

当来源异常且任务已经开始时，WMS 只为未完成项追加替代来源。以下 JSON 表达追加的业务语义；原异常来源仍保留为
历史证据，不被本次增量覆盖：

```json
{
  "task_id": "PT-OUT-A001",
  "base_task_version": 1,
  "source_additions": [
    {
      "item_id": "PT-OUT-A001-I02",
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

#### 追加来源字段定义

| 字段 | 含义与约束 |
| --- | --- |
| `task_id` | 定位需要追加来源的原 PickingTask。 |
| `base_task_version` | 本次增量所基于的整单版本；最终并发和版本推进规则由 wire 合同冻结。 |
| `source_additions[]` | 本次追加的替代来源集合。 |
| `source_additions[].item_id` | 关联原任务中的未完成项，不创建新的任务项。 |
| `source_additions[].source_locator` | 为该未完成项追加的精确来源；嵌套字段含义与主示例字段表一致。 |

method、path、请求信封、字段类型与上限、枚举、幂等键、版本推进方式和拒绝码由 inbound wire 合同定义。

### 5.4 不变量

接入时必须整单验证：

- `task_id + task_version` 唯一，重复同版本且内容相同返回首次接入结果；同版本不同内容整单拒绝。
- `item_id` 和 `reel_id` 在任务内唯一。
- 每个目标 `face + slot_id` 只对应一个任务项。
- 每个任务项都有精确来源和精确目标，不接受“任意可用料盘”或仅数量来源。
- 同一 `rack_id + rack_face + bin_id + cell_id` 定义一个独立料格执行序列。
- `BIN_CELL` 中待拣 `reel_id` 必须构成当前顶部的连续可达前缀；`top_to_bottom_sequence` 必须从 `1` 开始、
  连续且唯一，较小序号完成前不得执行较大序号。
- 同一料格按 `top_to_bottom_sequence` 观察时，目标面只允许 `A...A → B...B`；`B → A` 或 `A → B → A`
  会同时违反目标架 A/B 面顺序和料格 LIFO，不引入中间缓存绕过，整单拒绝。
- `RACK_SLOT` 单储位只能对应一个料盘。
- 目标 A/B 面、目标储位和任务项身份在任务开始后不可修改。
- 已完成项不可覆盖、删除或重新分配。

任一结构错误、来源身份缺失、目标冲突、LIFO 前缀不合法或料格顺序与目标面顺序冲突，整张任务拒绝；WMS 修正后
以新版本重发。

### 5.5 开始后的唯一追加能力

开始后只允许 WMS 为未完成项追加替代 `source_locator`：

- `item_id`、业务需求、目标面和目标储位保持不变。
- 原异常来源保留为历史证据，不被覆盖。
- 新来源必须重新通过精确身份和物理可达校验。
- 追加来源不改变已开始任务优先级，也不创建新的 `PickingTask`。

## 6. 最小状态模型

| 状态 | 含义 | 允许的主要转移 |
| --- | --- | --- |
| `QUEUED` | 已整单接纳，未占用工作位 | `STARTING`、`CANCELLED` |
| `STARTING` | 已被选中，正在分配并调入目标空架 | `EXECUTING`、`PAUSED`、`CANCELLED` |
| `EXECUTING` | 正在执行 A/B 面任务 | `PAUSED`、`WAITING_STOCK`、`COMPLETED`、`CANCELLED` |
| `PAUSED` | 身份、目标或物理事实存在歧义，等待明确处理 | `EXECUTING`、`CANCELLED` |
| `WAITING_STOCK` | 部分完成但 WMS 无替代库存，目标架在缺料缓存区 | `EXECUTING`、`CANCELLED` |
| `COMPLETED` | WMS 已接受拣货完成，工作位已物理释放 | 终态 |
| `CANCELLED` | 任务取消且目标架已按规则处置 | 终态 |

整单接入失败是 `REJECTED` 接入结果，不建立活动任务。面向、当前来源架、当前来源面、工作位占用和暂停原因
属于执行投影，不扩张为更多业务状态。

## 7. 排队与任务选择

WMS 提供 `priority` 和 `issued_at`。WES 只在目标工作位空闲时选择下一任务：

```text
priority 降序 → issued_at 升序 → task_id 升序
```

- WMS 只能在 `QUEUED` 状态调整优先级。
- 从 `QUEUED` 进入 `STARTING` 后不可抢占；请求目标空架即视为开始占用执行机会。
- 同一优先级的排序稳定，不使用运行时随机值。
- `PAUSED` 或 `WAITING_STOCK` 的任务不自动与新任务争抢工作位；恢复必须由 WMS 明确指定任务。

## 8. 目标架与来源架调度

### 8.1 目标空架绑定

1. WES 选中 `PickingTask`。
2. WES 请求 WMS 分配目标空架。
3. WMS 原子选择并锁定具体 `rack_id`，将其业务角色设为生产转运货架，并向 RCS 下发搬运任务。
4. RCS 保证目标架以 A 面朝向设备到位。
5. WES 根据到位事件和设备扫描验证 `rack_id`、空架条件和面向。
6. 验证成功后，`PickingTask` 与目标 `rack_id` 建立独占绑定。

WMS 未返回具体空架前，WES 不自行选择或占用货架。

### 8.2 并行预调度与容量

目标空架和第一个来源货架允许并行预调度。为避免构建不必要的缓存调度器，工作区只要求：

- 一个目标货架工作位。
- 一个当前来源货架工作位。
- 最多一个下一来源货架在途或等待。

库区与工作区距离较近，不预建多货架队列、动态窗口或全局路径优化。

### 8.3 双面执行顺序

目标货架顺序固定为 A 面后 B 面，以减少目标架旋转次数。对于当前目标面：

1. 来源货架以 A 面朝向设备进入工作位。
2. 执行该来源架 A 面上属于当前目标面的全部可执行项。
3. 若来源架 B 面仍有当前目标面的需求，旋转来源架并执行 B 面。
4. 当前来源架对当前目标面无剩余需求后，通常退回库区，再处理下一来源架。
5. 目标 A 面完成后旋转目标架到 B 面。
6. 若当前来源架仍有目标 B 面需求，则保留在工作位：先处理当前朝向的一面；必要时再旋转一次处理另一面，然后退回。

来源架暂时不可用时，WES 可以在同一目标面内调整来源架顺序；不得为此提前切换目标面或重新选择库存。

不同料格之间不存在固定的业务执行顺序。WES 可以在当前目标面内按来源架、来源面和就绪状态调整料格顺序，但每个
料格内部始终受 `top_to_bottom_sequence` 的严格前置关系约束。

## 9. 料盘执行与证据

每个 `task_item` 的标准因果链是：

```mermaid
flowchart LR
    A[验证来源架、面、料箱/储位] --> B[扫描顶部 reel_id]
    B --> C[创建并下发 PICK/PUT DeviceCommand]
    C --> D[设备 ACK]
    D --> E[COMMAND_RESULT]
    E --> F[更新来源与目标投影]
    F --> G[记录 item 完成证据]
```

- ACK 只表示设备接纳，不得更新料盘位置。
- 只有可关联的成功 `COMMAND_RESULT` 才能把料盘从来源位置推进到目标储位。
- 结果未知时保留原投影并暂停相关执行，不重复猜测物理动作。
- WES 不把目标储位重新计算成其他位置；目标位置来自 `PickingTask`。

## 10. 异常、NG 与补料

### 10.1 来源料箱 NG

出库以料箱为 NG 隔离单位：

- 一个料箱发生 NG，不影响同一来源货架上其他正常料箱继续执行。
- 自动滚筒线将 NG 料箱单独送往专用 NG/退料出口。
- WES 保存 NG 原因、来源架/箱/格、关联任务项和物理出口证据，并通知 WMS。
- 人工在 WMS/PDA 中确认并移走属于 WMS 流程；WES 只关注出口是否物理释放。
- WMS 为未完成项追加替代来源；WES 不自行查找替代库存。

### 10.2 顶部料盘身份或 LIFO 冲突

接入阶段发现序号不连续、顶部前缀不完整或同料格目标面顺序冲突时，按第 5.4 节整单拒绝，不建立活动任务。
任务执行期间若实际顶部 `reel_id` 与已接纳顺序不一致，说明来源库存身份无法可信解释。此时：

- 不跳过顶部料盘，不从下层抽取，不用同物料其他料盘替代。
- 暂停整张 `PickingTask`，保存扫码和位置冲突证据。
- 人工或 WMS 修正事实后，由 WMS 明确下达“恢复 `PickingTask`”。
- WES 重新验证目标架、当前来源和未完成项后继续原任务。

### 10.3 来源货架暂时不可用

- 仅在 WMS/RCS 明确返回可重试的暂时状态时，拥有该搬运义务的可靠对象才可做有界退避重试。
- WES 可以先执行当前目标面内其他已就绪来源架。
- NG、身份冲突、合同拒绝和永久不可用不得进入指数重试，必须等待 WMS 重新分配或人工处理。
- Adapter 每次调用仍只发送一次；重试属于可靠业务对象，不属于 Phase 2 Transport 或 Phase 3 Gateway。

### 10.4 WMS 无替代库存

当目标架缺少部分料盘且 WMS 明确没有可用替代库存时：

1. `PickingTask` 进入 `WAITING_STOCK`。
2. 目标架移出工作位，进入由 WMS/RCS 管理的“缺料待补货架缓存区”。
3. 原 `PickingTask`、目标 `rack_id`、已完成项和目标储位绑定保持不变。
4. 该货架不得进入生产交付区。
5. 后续任务可以继续使用工作位。
6. 库存可用后，WMS 指定具体 `PickingTask`、追加来源并明确下达恢复命令；WES 不因库存更新事件自动恢复。

缺料缓存区容量由 WMS/RCS 规划。缓存区满时，当前目标架可能占用工作位并阻塞后续任务；WES 只告警和保持现场事实，
不发明替代存放位置。

### 10.5 目标架异常

目标储位意外占用、实际料盘与目标不符、PUT 结果无法确定或目标架身份变化时，暂停整张任务。WMS 或人工完成处理后，
必须由 WMS 明确恢复；WES 重新验证后继续原任务，不创建通用补偿流程。

## 11. 取消与货架角色转换

货架物理类型固定为 `DOUBLE_SIDED_REEL_RACK`，业务角色由 WMS 管理：

```text
UNASSIGNED ↔ PRODUCTION_TRANSFER ↔ RETURN
```

取消规则：

- 尚未分配目标架：直接取消任务。
- 目标架已分配但仍为空：释放锁定并恢复为未分配空架。
- 已放入任一料盘：取消任务并把目标架业务角色改为退料货架；既有料盘保持原储位。

退料货架后续如何被其他任务优化消化由 WMS 计算。WES 仅在收到新的 `PickingTask` 时，把其储位视为
`RACK_SLOT` 精确来源；货架清空后是否恢复生产转运角色仍由 WMS 决定。

## 12. 完成与工作位释放

满足以下条件才可报告“拣货完成”：

- 所有任务项都有成功物理完成证据。
- 目标 A、B 面均完成。
- 目标 `rack_id`、面和储位投影与任务一致。
- 没有未决命令、未决 PUT 或未处理身份冲突。

完成流程：

1. WES 可靠通知 WMS `PickingTask` 拣货完成。
2. WMS 接受后向 RCS 下发搬运，将货架移至“拣货完成”货架区。
3. WES 只在得到完成接受事实和工作位物理清空证据后，把任务置为 `COMPLETED` 并选择下一任务。

完成区容量、后续生产交付、目标架进入生产时的朝向和人工异常处理均属于 WMS/RCS 范围。完成区满导致目标架无法移出时，
工作位保持占用并阻塞后续任务；WES 不绕过物理占用事实。

## 13. WMS 语义交互面

### 13.1 WMS → WES

目标业务命令只有：

- 创建 `PickingTask`。
- 更新 `QUEUED` 任务优先级。
- 为未完成项追加替代来源。
- 恢复指定 `PAUSED` 或 `WAITING_STOCK` 任务。
- 取消指定任务。

这些命令的 method、path、DTO、幂等和拒绝码必须在独立 inbound wire 合同中冻结。不得复用“查询出库单/波次”替代任务下发。

### 13.2 WES → WMS

出库场景最少需要：

- 请求目标空架、来源架搬运和货架换面。
- 报告来源 NG/身份异常。
- 通知 `PickingTask` 拣货完成。

Phase 3 WMS Adapter 只拥有这些调用的 DTO、固定 method/path 和单次结果翻译；`TransportTask`、`WmsConfirmation` 和
`PickingTask` 自身拥有可靠生命周期。旧候选 Q10–Q13（查询拣货单、出库单、波次和任务快照）不再进入目标 surface。

## 14. Phase 1–3 与后续阶段接缝

| 阶段 | 当前事实 | 对本文的支撑 | 明确不代表 |
| --- | --- | --- | --- |
| Phase 1 | 测试语义和重量治理已完成 | 约束核心、Adapter、插件分开验收 | 出库业务或最小执行对象已实现 |
| Phase 2 | `src/core/outbound_http/` 已提供 GET/POST、单次发送、有界响应和传输事实 | 供 WMS/RCS/ECS Adapter 复用 | WMS DTO、重试、任务状态或业务成功 |
| Phase 3 | 计划暗构建 `src/app/wms_adapter/`，当前仍被合同阻断，生产包尚不存在 | 冻结出库所需 WMS 调用的类型化边界 | `PickingTask`、可靠对象或生产接线 |
| Phase 4 | 尚未开始 | 最终 `InboundEvidence`、`TransportTask`、`WmsConfirmation`、执行投影 | 具体出库顺序 |
| Phase 8 | 尚未开始 | 自动出库插件实现本文 Decision 和对象推进 | HTTP、厂商 Payload 或库存算法 |

Phase 3 operation 清单已依据本文删除旧 Q10–Q13，并补入 NG 报告与拣货完成通知。Phase 3 Task 1 仍必须冻结
33 项完整 wire 字段矩阵和 `PickingTask` inbound 合同；不得为了维持旧数量恢复无消费者能力。

## 15. 测试所有权与验收边界

自动化验收按以下所有权划分：

- Phase 2 核心测试：只验证 HTTP 传输、资源限制、取消和传输事实。
- Phase 3 WMS Adapter 测试：只验证 WMS method/path/DTO/拒绝码、单次调用和结果映射。
- Phase 4 核心测试：只验证可靠对象、持久化、幂等、状态推进和投影不变量。
- 自动出库插件测试：验证任务校验、排序、A/B 面顺序、LIFO、NG、补料、缺料等待、取消和完成。
- 设备 Adapter 测试：验证厂商扫码、命令、Payload 和 `COMMAND_RESULT` 标准化。
- 真实 RCS/ECS/WMS 联调：验证到位姿态、工作位释放、NG 出口和搬运终态。

任何一层不得用另一层的 happy path 替代自身合同测试。

### 15.1 MOCK fixture 合同

inbound wire 合同冻结后，DTO 所属包至少维护以下 fixture 场景：

| 场景 | 预期 |
| --- | --- |
| 混合 `BIN_CELL` / `RACK_SLOT` 且目标包含 A/B 面 | 整单接纳 |
| 两个任务项占用同一目标面和目标储位 | 整单拒绝 |
| 同一料格的待拣料盘不是顶部连续前缀 | 整单拒绝 |
| 同一料格序号重复、从非 `1` 开始或存在跳号 | 整单拒绝 |
| 同一料格按顶部到底部出现目标面 `B → A` 或 `A → B → A` | 整单拒绝 |
| 不同料格在当前目标面内调整执行先后 | 允许调整，同时保持各料格内部顺序 |
| 为原任务未完成项追加精确替代来源 | 接纳增量，保留原来源证据 |

MOCK 自动化环境与合同测试共用同一组 `.json` fixture，并通过正式 DTO/Schema 校验。

## 16. 评审结论

### 16.1 已通过

- 权威边界与最小执行架构一致：WMS 管业务和库存，WES 管作业期执行，RCS 管运输，ECS 管物理动作。
- `PickingTask` 直接驱动消除了订单/波次的重复解释。
- 扁平任务项与派生调度分组同时满足两类来源，未引入多套任务模型。
- 状态、异常和恢复均有明确 owner，没有通用 Hold、恢复引擎或自动补偿。
- A/B 面顺序、工作位容量和预调度规则满足现场约束，未构建全局优化器。
- 取消后货架角色转换复用同一物理结构，不增加拆料任务模型。
- SRS、最小执行架构 SPEC、WMS operation 合同、Master Plan 和 Phase 3 计划已同步移除旧单据/波次驱动口径。

### 16.2 实施前阻断项

- WMS/WES inbound `PickingTask` wire method、path、字段上限、幂等与拒绝码尚未冻结。
- NG 报告、拣货完成通知和货架换面/搬运的 WMS wire 合同尚未全部冻结。
- Phase 3 的 33 项完整 method/path/DTO/拒绝码矩阵仍须 WMS/业务方批准。
- RCS 必须确认来源架和目标架进入工作位时 A 面朝向设备，以及换面任务的可观测终态。
- 现场必须确认缺料缓存区和拣货完成区容量；容量不足只形成阻塞事实，不改变本文流程。

这些阻断项只阻止代码实施，不改变本文已确认的业务流程。
