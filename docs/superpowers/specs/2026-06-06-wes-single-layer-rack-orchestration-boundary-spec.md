# WES 单层货架执行编排边界 SPEC

> 状态：后端已落地并验收 - 前端承接待独立计划完成
> 日期：2026-06-06
> 进度更新：2026-06-08
> 关联文档：
>
> - `docs/architecture/SRS.md`
> - `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`
> - `docs/superpowers/plans/2026-06-06-wes-single-layer-rack-orchestration-boundary-plan.md`
>
> 2026-06-08 功能性验收结论：后端合同、服务、OpenAPI、文档守护和目标测试已通过；前端 generated types、scene adapter、`/runtime/monitor` 浏览器视觉 QA 由 `wes_frontend` 独立计划承接，当前不作为本仓库完成项。

## 1. 背景与目标

当前 SRS 已覆盖 SMT 入库、生产发料、退料、机构件转运等多个业务场景，但“资源编排/货架调度”的表达仍容易被理解为 WES 管理所有货架、库位和运输设备。经过讨论，本阶段必须收敛为更清晰的边界：

- WES 不是第二套 WMS，不维护全局货架资源、库存主账或物理库位占用。
- WES 只对单层货架 active 执行快照和 WorkLine/Station 当前执行上下文负责；其他资源只保存执行投影、WMS 回调和对账证据。
- 五层货架、生产货架、退料货架、转运货架、库存、逻辑位置占用、AGV/CTU/RCS 任务均由 WMS 管理或转发。
- 业务需求驱动货架流转，不能让货架主动选择业务。

本 SPEC 的目标是将上述原则整理为统一业务规格，并已作为当前实施 PLAN 的输入落地。SRS 是需求文档，除原则性权责冲突外，不作为本 SPEC 的默认修改对象。

## 2. 适用范围

本 SPEC 适用于 WES v1 中所有涉及单层货架、WorkLine、Station、WMS 搬运/交换请求的执行编排场景，包括：

- SMT 粗分机装箱完成后的单层货架释放。
- 分拣机对单层货架的处理需求。
- 生产发料/出库波次对单层货架或发料缓存的需求。
- 退料流程中的 LCR、X-Ray、贴标、退料执行证据。
- 转运、补给、空架回流等需要 WMS/RCS/AGV/CTU 参与的运输需求。

本 SPEC 不定义新的数据库表、API 实现或迁移脚本。当前首轮落地范围由关联 PLAN 记录；后续新增代码、接口、测试或原则性 SRS 修订，应通过增量 PLAN 或 ADR 明确范围。

## 3. 核心架构决策

### 3.1 WES 只权威维护单层货架 active 执行快照

WES 本阶段只对 `SINGLE_LAYER` 单层货架的 active 执行快照拥有本地权威。该快照用于执行恢复、设备指令、对账和诊断，不作为库存或资源主账。

单层货架事实快照至少表达：

- 单层货架 ID、逻辑货位 `A/B/C/D`。
- 每个货位上料箱的 ID、来源、执行状态和最近一次可信证据。
- 料箱内部料格与物料执行快照，用于 ECS 指令和异常恢复。
- 当前绑定的 WorkLine、Station、Session、Dispatch Key。
- 最近一次 WMS 回调、ECS 执行证据、人工确认或对账证据。

WES 不维护以下资源主账：

- 五层货架库存、空箱位、A/B 面容量、冷热区真实占用。
- 生产货架库存、退料货架库存、转运货架库存。
- 逻辑区域真实占用、RCS 路径、交通管制、物理位置坐标。
- WMS 库存预留、库存扣减、账务状态、SAP 同步状态。

WES 仍可以保存非单层资源的执行证据和当前投影，例如 `ResourceStateEvent`、`RackPlacement`、`RackBinMount`、`RackMaterialMount`。这些投影只能回答“WES 最近收到过什么执行事实”，不能回答“库存是否可用”“位置是否真实占用”“五层空箱是否可授权”。

### 3.2 WMS 是非单层资源和库存权威

所有非单层货架及其资源能力均由 WMS 管理。WES 可以保存 WMS 返回的执行投影和证据，但不能把这些投影作为资源可用性判断的本地主账。

| 对象 | 权威系统 | WES 允许保存 |
| --- | --- | --- |
| 单层货架执行快照 | WES | 当前执行事实、关系投影、证据 |
| 五层货架 | WMS | WMS 授权结果、交换回调、对账证据 |
| 生产货架 | WMS | 发料执行证据、人工/ECS 回执 |
| 退料货架 | WMS | LCR/X-Ray/贴标结果、退料执行投影 |
| 转运货架 | WMS | 搬运需求、任务回调、异常证据 |
| 库存与账务 | WMS | 查询结果短缓存、预留引用、确认回执 |
| AGV/CTU/RCS | WMS 转发 | dispatch key、任务状态、回调证据 |

### 3.3 WES 本版本不直连 RCS/AGV/CTU

本版本 WES 不提供 RCS/AGV/CTU Driver Plugin，不直接调用 RCS，不直接下发 AGV 或 CTU 任务。

所有搬运、交换、旋转类需求统一由 WES 提交 WMS：

1. WES 生成业务需求和稳定业务键。
2. WES 调用 WMS 接口提交搬运、交换或旋转需求。
3. WMS 判断资源、位置、容量、路径和降级策略。
4. WMS 转发给 RCS/AGV/CTU。
5. WMS 将任务受理、执行中、完成、失败、取消等结果回传 WES。
6. WES 只根据可信回调推进执行投影或进入对账/RuntimeHold。

Runtime 合同上，`RuntimeIntent.external_request(...)` 是通用外部请求入口；`RuntimeIntent.rack_operation_request(...)` 是 rack operation 领域包装，用于表达 WES 单层货架搬运、交换、旋转或补给需求。rack operation 最终仍复用 `EXTERNAL_HTTP` outbox、Timeline、wait context、`WAITING_EXTERNAL` 和 WMS/RCS 回调恢复语义；不得绕过既有 runtime wait/context 链路。

### 3.4 业务驱动货架，而不是货架选择业务

所有货架流转必须由业务需求驱动。货架本身的状态变化只能产生资源事实，不能直接选择或启动业务。

正确模型：

```text
业务/WorkLine 产生需求
→ WES 判断 WorkLine 状态和 Station 业务 lease 是否可处理
→ WES 匹配单层货架 active 快照或等待 WMS 到位
→ WES 向 WMS 提交搬运/交换/补给需求
→ WMS 转发执行并回传结果
→ WES 对具体设备命令执行设备准入并推进作业
```

禁止模型：

```text
某货架 ready
→ 货架主动选择候选业务线
→ 货架决定进入某个 WorkLine
```

整体数据流：

```text
Business Demand / WorkLine Ready
        |
        v
WES Demand Coordinator
        |
        +-- checks WorkLine runtime status
        +-- checks Station lease / active binding
        +-- reads SINGLE_LAYER active snapshot
        |
        v
WMS Dispatch Request ---------------> WMS / RCS / AGV / CTU
        |                                      |
        |                                      v
        |                         WMS callback / authorization
        v                                      |
WES execution projection <---------------------+
        |
        +-- concrete device command admission
        v
ECS / vision / LCR / X-Ray / label devices
```

## 4. WorkLine 与逻辑 Endpoint

WorkLine 是业务处理能力，不是位置系统。一个 WorkLine 可以包含多个逻辑 endpoint，用于表达其处理、缓存、等待和目标资源需求。

关键定义：

- WorkLine：业务处理能力单元，例如某条分拣线、退料检测线或发料执行线。
- Endpoint：WorkLine 暴露给业务编排的逻辑端点，例如缓存区、输入口、输出口或工作位。
- Station：单层货架 dock / 业务端点，用于承载单层货架 active session；Station 不是设备。
- Station `IDLE`：表示 WES 侧没有 active rack binding、没有 active dispatch lease、没有 active session 占用该 Station。
- Device `IDLE`：只用于具体设备命令准入，例如 `SOURCE_ARM`、`TARGET_ARM`、LCR、X-Ray、贴标设备，不等同于 Station `IDLE`。

### 4.1 Endpoint 职责

Endpoint 只表达业务语义和逻辑名称，不表达物理坐标。物理位置、可达性、路径、占用和降级策略由 WMS/RCS 负责。

典型 endpoint：

- `SOURCE_STATION_A`
- `SOURCE_STATION_B`
- `SOURCE_CACHE_AREA`
- `TARGET_STATION`
- `RETURN_XRAY_INPUT`
- `RETURN_LABEL_OUTPUT`
- `PRODUCTION_BUFFER`

WES 对 endpoint 的职责是维护自身业务预约和执行绑定，避免同一个 WES 任务重复派发到互斥 endpoint。WES 不判断 endpoint 的物理占用是否真实可用。

### 4.2 分拣机 WorkLine

分拣机可能有多条线，每条线是独立 WorkLine。每条分拣线可以有多个 source station，并可能同时需要目标五层货架资源。

分拣机关键约束：

- 分拣机只有两个机械臂：`SOURCE_ARM` 和 `TARGET_ARM`。
- 不存在 `NG_ARM`。
- NG 放置动作由 `TARGET_ARM` 完成。
- `WORKLINE_START_REQUESTED` 只表示工作线进入 READY/待机状态，可以开始接收业务需求；不表示已有货架到位，也不表示立即开始分拣。

当前 SMT 分拣入库插件的代码常量映射为：

- `SOURCE_ARM` 对应 `ROLE_SORTING_SOURCE_ARM`。
- `TARGET_ARM` 对应 `ROLE_SORTING_TARGET_ARM`。
- NG 放置命令对应 `COMMAND_NG_PLACE`，目标设备角色仍是 `ROLE_SORTING_TARGET_ARM`。

分拣启动条件：

- WorkLine 处于 `READY`。当前 WorkLine 运行状态模型不定义 `IDLE`，空闲语义由 Station 业务 lease 表达。
- 对应 Station 业务 lease 空闲，即没有 active rack binding、active dispatch lease 或 active session。
- WES 存在可处理的业务需求。
- WES 拥有可处理的单层货架 active 快照，或已提交 WMS 载入需求并等待 WMS 到位回调。
- 目标资源、五层货架、空箱、交换或补给条件由 WMS 授权、拒绝或等待。
- 具体设备命令下发前，再分别按 `SOURCE_ARM`、`TARGET_ARM` 等设备角色执行实时准入。

运行态 detail 合同：

- 后端运行态 detail / OpenAPI 必须提供结构化字段，供前端区分 WorkLine READY、Station lease、单层 active snapshot、WMS rack operation wait 和 resource evidence。
- 后端字段必须使用下表的 snake_case 名称并通过 OpenAPI / generated types 暴露；前端 scene adapter 负责转换为 camelCase `RuntimeSceneModel` 字段，不得用“等价字段”替代。
- 若后续执行阶段选择后端 Pydantic alias 输出 camelCase，必须补齐 response-by-alias、OpenAPI schema 和 generated type 回归测试，证明 snake_case 后端合同与前端消费合同没有分叉。
- 字段来源必须是后端结构化运行事实、plugin manifest/display 元数据或 runtime wait context，不得要求前端解析 `context_json`、`payload_json`、`event_payload` 或 raw resource badge 文本推断业务含义。

| 后端字段 | 前端 scene 字段 | 状态集合 | 来源与降级规则 |
| --- | --- | --- | --- |
| `workline_readiness` | `worklineReadiness` | `READY` / `NOT_READY` / `UNKNOWN` | 来源为 WorkLine runtime status；缺少状态或无法映射时为 `UNKNOWN`。 |
| `station_lease` | `stationLease` | `IDLE` / `ACTIVE_RACK_BOUND` / `ACTIVE_DISPATCH_LEASE` / `ACTIVE_SESSION_BOUND` / `UNKNOWN` | 来源为 Station lease 服务；不得由前端扫描 session raw JSON 推断。 |
| `single_layer_rack_snapshot` | `singleLayerRackSnapshot` | `ACTIVE` / `MISSING` / `INVALID` / `NON_SINGLE_LAYER_EVIDENCE` / `UNKNOWN` | 来源为单层 active snapshot、manifest boundary 和资源证据；非单层资源只能降级为 evidence。 |
| `rack_operation_wait` | `rackOperationWait` | `WAITING_WMS` / `WMS_CALLBACK_RECEIVED` / `TIMEOUT` / `FAILED` / `NONE` / `UNKNOWN` | 来源为 runtime wait context、rack operation task/outbox 和 WMS 回调；无等待时为 `NONE`。 |
| `resource_evidence_kind` | `resourceEvidenceKind` | `WES_ACTIVE_SNAPSHOT` / `WMS_CALLBACK_EVIDENCE` / `TRACE_RESOURCE_EVIDENCE` / `GENERIC_EVIDENCE` / `UNKNOWN` | 来源为结构化 evidence kind；缺少可信分类时降级为 `GENERIC_EVIDENCE` 或 `UNKNOWN`。 |

`UNKNOWN` 表示后端无法给出可信结构化结论；除明确允许为 `NONE` 的 `rack_operation_wait` 外，字段不得省略。字段缺失属于合同缺口，前端只能显示通用 evidence fallback，不能解析 raw JSON 自行补语义。

## 5. 业务流规格

### 5.1 粗分机到分拣机

粗分机不直接调用分拣机，也不选择分拣线。

流程：

1. 粗分机完成单层货架装箱。
2. WES 记录或更新单层货架事实快照。
3. 粗分机释放当前单层货架，产生 `SINGLE_LAYER_RACK_RELEASED` 资源事实。
4. 若单层货架中存在满箱或优先交换条件，WES 向 WMS 提交满箱交换或补给需求。
5. WMS 负责五层货架资源、空箱、交换区、AGV/CTU/RCS 动作闭环。
6. 分拣 WorkLine READY 且 Station 业务 lease 空闲时，由分拣业务需求检测可用单层货架 active 快照。
7. WES 通过事务型 Station dispatch claim 锁定 station scope `(workline_id, position_code)` 后创建 WMS 载入需求；同一 WorkLine + Station 并发请求只能一个 claim 成功。
8. WMS 回传到位后，WES 驱动 ECS/分拣插件开始处理。

Station dispatch lease 约束：

- `ACTIVE_RACK_BOUND` 表示 WES 已有 active rack binding 占用该 Station。
- `ACTIVE_DISPATCH_LEASE` 表示 WES 已创建仍未闭环的 WMS dispatch/outbox；其中 `SENT` 且 `finished_at is None` 仍等待 WMS 回调，必须继续占用 Station。
- 只有 dispatch/outbox 已有 `finished_at`，或处于 `FAILED` / `CANCELLED` 且不再等待 WMS 回调时，才可释放该 dispatch lease。
- `ACTIVE_SESSION_BOUND` 表示仍有 open session 绑定或等待该 Station。
- Station lease 状态是可观测结果，不是并发互斥本身；创建 WMS dispatch 必须通过同事务 station-scope claim 完成互斥，业务 `dispatch_key` 只负责派发幂等和回调恢复，不得作为 Station scope lock。

### 5.2 出库/生产发料

出库发料由工单、波次、产线请求驱动，不由货架状态驱动。

流程：

1. WES 接收 WMS/SAP 转发的工单或发料需求。
2. WES 生成波次或补料需求。
3. WES 向 WMS 查询库存、申请预留或请求目标资源。
4. WMS 返回库存、预留引用、资源授权或拒绝原因。
5. WES 基于 WorkLine/Station 状态生成执行需求。
6. 搬运、交换、旋转由 WES 提交 WMS，WMS 转发执行。
7. ECS 执行拣选、校验、上料等 WES 直连动作。
8. 出库确认、库存扣减和账务由 WMS 确认后生效。

WES 不允许在 Pick Fail、缺料或异常场景下自动扣减库存或释放备选库存。必须进入诊断、RuntimeHold 或等待 WMS 授权。

### 5.3 退料

退料流程中，WES 负责自动化执行和证据，不负责退料库存主账。

流程：

1. WMS/PDA 产生退料处理需求。
2. WES 根据物料属性和 WMS 返回信息决定是否需要 LCR、X-Ray、贴标。
3. WES 直连 LCR、X-Ray、贴标设备，记录执行结果和证据。
4. 若需要退料货架、转向货架、补空架或搬运，WES 向 WMS 提交需求。
5. WMS 负责退料货架、储位、库存归属和搬运动作。
6. WES 将检测、贴标和执行证据提交给 WMS。
7. WMS 完成库存调整、入库确认和 SAP 同步。

SRS 中 `Return_Rack_Inventory` 应改为退料执行投影或证据视图，不能作为 WES 库存主账。

### 5.4 转运、补给、空架回流

转运、补给和空架回流统一表达为 WMS 搬运需求。

WES 可以发起：

- 单层空架补给需求。
- 单层货架从缓存区到 Station 的载入需求。
- 已处理单层货架的移出需求。
- 退料/生产/转运相关的搬运需求。

WMS 负责：

- 判断目标逻辑位置是否可用。
- 判断区域容量和拥堵。
- 选择运输设备。
- 执行路径规划、排队、避让和降级策略。
- 回传执行结果。

## 6. 状态、事件与证据

### 6.1 WES 应保存的事实

WES 应保存 append-only 的执行事实和当前投影：

- 单层货架到达、离开、绑定、释放。
- 料箱挂载、卸载、异常、满箱交换请求。
- ECS 扫描、放置、抓取、校验、失败。
- WMS 受理、执行中、完成、失败、取消回调；若 WMS 转发的回调携带原始 `source_system=RCS`，WES 可将其作为执行证据来源保存，但入口、授权和任务闭环仍以 WMS 转发合同为准。
- 人工确认、RuntimeHold、对账结果。

### 6.2 WES 不应保存为主账的事实

以下数据即使在 WES 中出现，也只能作为证据或短期投影：

- WMS 库存数量。
- WMS 预留锁定结果。
- 五层货架空箱位。
- 退料货架储位。
- 生产货架储位。
- RCS 当前坐标。
- 区域真实占用。

### 6.3 异常处理

WES 遇到以下情况必须进入 RuntimeHold、诊断或对账，不得静默推进：

- WMS 回调缺少稳定业务键或来源事件 ID。
- WMS 回调版本落后于当前投影。
- 物理完成但缺少可信 post-action relation。
- ECS 状态与 WMS 回调冲突。
- WMS 断连、超时或返回拒绝。
- 同一单层货架被多个 active session 绑定。
- 同一 endpoint 被多个 WES dispatch lease 绑定。

## 7. 原则性冲突与维护 PLAN 输入

本 SPEC 已完成首轮落地，维护阶段不默认要求立即改写 `docs/architecture/SRS.md`。只有当 SRS、ADR 或后续 PLAN 中存在以下原则性冲突时，才需要同步修订对应文档或先提出新的 ADR：

- 将 WES 描述为全局资源主账、库存主账或位置占用权威。
- 将 WES 描述为直接调用 RCS、直接下发 AGV/CTU 任务的系统。
- 将 `Return_Rack_Inventory` 设计为 WES 退料库存主账，而不是退料执行投影或证据视图。
- 将五层货架冷热区、A/B 面负载、空箱资源授权设计为 WES 本地主账。
- 将 `WORKLINE_START_REQUESTED` 解释为货架到位、设备启动或作业开始事件。
- 将分拣机描述为存在 `NG_ARM`，而不是由 `TARGET_ARM` 执行 NG 放置动作。

后续维护或新增 PLAN 应以本 SPEC 作为边界输入，逐项判断是否需要 SRS、ADR、接口、模型、服务、任务流或测试调整。SRS 的修订范围应限于消除权责冲突和需求歧义，不应把实现细节提前写入需求文档。

## 8. 验收标准

文档评审通过时，必须满足：

- 能解释粗分机到分拣机链路，且粗分机不直接依赖分拣机。
- 能解释出库/生产发料，且由工单、波次、产线需求驱动。
- 能解释退料，且 WES 不维护退料库存主账。
- 能解释转运、补给、空架回流，且所有 AGV/CTU/RCS 动作由 WMS 转发。
- 能解释 WorkLine、Endpoint、Station、Device 的关系，且不把 Station 当作设备。
- 能解释 Station 业务 lease 与 Device 实时准入的差异。
- 能解释 Station dispatch lease 的释放规则：`SENT` 且 `finished_at is None` 仍占用 Station；`finished_at` 存在，或 `FAILED` / `CANCELLED` 且不再等待 WMS 回调后释放；释放后同一 `(workline_id, position_code)` 的新业务可再次 station-scope claim。
- 能解释单层货架 active 执行快照的用途，且不扩展为全局货架管理。
- 能解释非单层资源执行投影/证据与 WMS 主账的边界。
- 能指出 SRS、ADR 或后续 PLAN 中哪些旧口径属于原则性冲突，而不是默认要求大范围回写 SRS。

## 9. 后续维护边界

本 SPEC 已完成首轮实现落地。后续维护或扩展建议按以下顺序处理：

1. 生成增量 PLAN，明确本轮只做文档修订、模型调整、接口调整、服务实现还是测试补强。
2. 由增量 PLAN 判断是否存在原则性 SRS/ADR 冲突；仅在必要时修订对应文档。
3. 持续检查 resource/workline/rack 模型中是否出现“全局货架管理”口径回退，并同步更新命名和注释。
4. 维护单层货架 active 快照服务与 WorkLine endpoint / Station lease 的最小数据合同。
5. 保持分拣机触发条件为业务需求 + WorkLine READY + Station lease 空闲 + WMS 到位/授权回调，不回退到 `WORKLINE_START_REQUESTED` 直接触发作业。
6. 保持退料货架库存表达为 WMS 主账 + WES 执行证据。

任何实现计划如果需要 WES 管理五层货架库存、退料货架库存、物理库位占用、AGV/CTU/RCS 直连调度，必须先提出新的 ADR。
