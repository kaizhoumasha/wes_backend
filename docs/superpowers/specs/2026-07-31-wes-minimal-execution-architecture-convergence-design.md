---
title: WES 最小执行架构收敛设计
status: Approved
created_at: 2026-07-31
updated_at: 2026-08-05
scope: 单工厂 WES 产品的目标架构、业务边界、工作线扩展方式与现有系统收敛路径
implementation_baseline: develop@1a8bfcac
system_stage: pre_release
migration_strategy: direct_replacement
historical_reference: ee1f3b670c5ed33cfd5be1fd0370b53570790e73
supersedes:
  - docs/superpowers/README.md 中登记的全部项目外历史设计；这些资料仅供追溯，不是当前合同或实施入口
related:
  - docs/architecture/SRS.md
  - docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md
  - docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
  - docs/architecture/device-command-contract.md
  - docs/integration/callback_event_validation_principles.md
  - docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md
  - docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md
  - docs/contracts/wms-northbound-interaction-contract.md
---

# WES 最小执行架构收敛设计

## 1. 文档定位

`docs/architecture/SRS.md` 是产品范围、参与方职责和功能/非功能需求真源；本文在该需求边界内定义 WES
产品的最终目标架构。产品售卖给不同客户，每个工厂独立部署；各工厂使用相同北向 WMS
业务边界，差异主要来自南向设备、设备厂商 ECS 合同以及入库、出库工作线流程。

文档权威顺序为：SRS 定义“需要什么”，本文定义“目标架构如何满足”，Master Plan 定义“按什么顺序实施”。
如本文与 SRS 的业务范围或权责发生冲突，必须先显式修订其中一方，不得用实施计划静默覆盖需求。

本系统尚未发布，不存在必须兼容的生产旧版本或必须迁移的历史业务数据。开发和测试数据可以清空，
架构收敛采用直接替换：

- 当前经确认的 ECS、WMS、RCS 业务合同继续作为目标合同，不为旧 API、旧字段或旧 Payload 别名保留兼容入口。
- 幂等、持久化证据、ACK/CALLBACK 分离、设备状态和资源投影作为最终业务正确性保留，不继承旧 Runtime 抽象。
- 建立最小、具体、面向工作线对象推进的执行内核，用代码插件承载客户和工作线差异。
- WES 核心仓库的 `tests/` 只验证最小执行内核、通用 WorkLine 能力、外部合同和可靠性不变量；
  具体 WorkLine 执行插件以仓库根目录下的独立二次开发包交付，并由插件包自带测试和 fixture。
- 删除旧 Runtime、兼容 shim、re-export、双写、双读、旧路径 fallback 和仅服务迁移的配置。
- 最终模型稳定后清空开发/测试数据库，并由 Alembic generator 创建单一干净基线，不实现旧数据转换。
- 测试以本文为基线：通用 WES 行为改写到核心测试后保留；具体工作线/插件行为从核心 `tests/` 移出，
  随对应二次开发插件重新实现；只验证旧架构、旧迁移和兼容路径的测试直接删除。
- 基础能力、厂商合同与业务能力必须有独立测试所有者：核心测试不得以具体厂商、工作线或业务成功路径证明
  基础能力；Adapter 测试只验证厂商合同与标准化映射；WMS Adapter 测试验证业务结果合同；插件测试只验证业务结果到
  执行 Decision 的映射和对象推进。后三者都不得
  替代核心持久化、幂等、传输和可靠性不变量测试。

提交 `ee1f3b670c5ed33cfd5be1fd0370b53570790e73` 只作为平台化前的历史行为参照，不作为代码回退或新分支基线。
该提交已经存在 Manifest、RuntimeIntent、RuntimeHold、Reconciliation、CellReservation、
Service Locator 和插件模板，不是简洁内核。实现从最新 `develop` 开始，但历史实现、迁移链和测试资产
都不构成兼容约束；只有经本文确认的目标业务合同和可靠性不变量可以进入最终系统。

## 2. 核心架构结论

WES 只承担以下职责：

1. 工作线本地设备调度。
2. 工作线内对象、位置、队列、设备忙闲的实时投影。
3. ECS 设备命令、ACK、CALLBACK 的持久化证据和幂等处理。
4. 通过抽象搬运端口提交 AGV/CTU 业务搬运目标并跟踪任务级进度。
5. 消费 WMS 给出的封闭业务结果，并在其授权范围内决定设备发送、等待、暂停、隔离和对账等本地执行动作。
6. 向 WMS 同步物理执行结果；WMS 不可用时保存待确认事实并受控暂停。

WMS 继续负责业务单据、业务授权、库存、主数据、来源分配、人工业务和全局仓内位置权威。

目标执行闭环按外部义务分为三类，三者分别拥有状态和重试策略，不共享一个含混的 Callback 生命周期：

```text
DEVICE EVENT
→ 持久化并返回 ACK
→ 请求或匹配 WMS 封闭业务结果
→ 工作线插件校验关联并映射为执行 Decision
→ ECS COMMAND
→ ACK
→ CALLBACK
→ 回传物理结果并取得 WMS 后续业务结果
→ 下一条 COMMAND 或流程结束
```

```text
WMS 单据或同步业务输入
→ WMS 给出来源、目标、优先级、路线或处置等封闭业务结果
→ 工作线插件映射为执行 Decision
→ TransportTask 持久化
→ Transport Adapter 提交
→ 类型化 ACK
→ 主动查询状态；外部通知只能唤醒查询
→ 类型化终态
→ 结果判定并继续流程
```

```text
WMS 确认义务
→ WmsConfirmation 持久化
→ 同步发送
→ Success：确认义务完成
→ WmsBusinessReject：保存 WMS 封闭业务结果并按其语义执行，不进入依赖重试或本地改判
→ WmsDependencyFailure：仅逐 operation 合同批准安全重提且 retryable=true 时，由可靠对象保留内部 dispatch_key 重提；
  否则依赖暂停或人工对账
→ WmsContractFailure：记录合同告警并封闭失败，不进入依赖重试
```

WMS 同步 HTTP 调用不进入 ECS/RCS 异步 ACK 协议，也不需要“工作线执行引擎”进行实时投影。

## 3. 范围与非目标

### 3.1 范围

- 单工厂、独立部署的 WES 产品。
- 相同北向 WMS 业务能力。
- 不同客户的 ECS 设备、工作线拓扑和出入库执行插件。
- 粗分机、自动分拣线、人工分拣线、满箱交换及复杂出库来源执行。
- AGV/CTU 通过 Transport Port 调用；当前适配器由 WMS 转发。
- ECS、RCS 使用 HTTP/JSON 异步 ACK；WMS 使用同步 HTTP。

上述具体工作线流程是二次开发插件的执行验收范围，不进入 WES 核心测试套件。WES 核心只为插件提供稳定
SPI/SDK、封闭 Decision、注入端口和通用执行保障；每个具体插件在自己的独立包内验证 WMS 业务结果到物理执行的映射。

### 3.2 非目标

- 总部与分工厂的多级管理。
- WES 自建库存台账、货架主数据或 WMS 单据系统。
- AGV/CTU 车辆位置、路径规划、交通管制和设备级调度。
- WES 软件实现机械安全互锁、防撞或设备间防呆。
- MQTT、OPC UA、WebSocket 等未确认的南向协议。
- 由 WES 定义设备厂商必须实现的 `task_type`、`event_type` 或 ECS 内部步骤。
- Vendor Manifest、通用工作流 DSL、低代码流程引擎或运行时动态插件发现。
- 自动物理恢复、自动清线、推测式回放和自动补偿编排。
- 为未来客户预建可复用执行插件模板。
- 四条串联分拣线之上的 `SorterCorridor` 领域对象或跨线执行引擎。

## 4. 权威边界

### 4.1 WMS 权威数据

WMS 是以下数据和决策的唯一业务权威：

- GRN、入库单、出库单、退料单、转运单、波次、工单、盘点等业务单据。
- 库存台账、可用量、冻结量、预留、库存事务和最终入出库确认。
- 物料、料盘、料箱、料格、仓库区域、仓位等主数据。
- 单层货架、五层货架、退货货架、转运货架及其储位主数据和全局位置。
- 出库来源分配、目标货架或目标储位授权。
- 全部业务资格、优先级、路线、NG/等待/替代、取消、恢复和业务终态结果。
- AGV/CTU 搬运业务目标、货架分配和全局运输业务状态。
- 人工作业任务、PDA 扫码、人工入库目标格、人工出库来源和人工库存变更。
- SAP 等上游系统的业务同步。

WES 只可保存完成工作线执行所需的 WMS 结果引用和作业期投影，不得跨请求缓存原始事实用于业务判定，也不得把投影
升级为库存、主数据或业务决策权威。

### 4.2 WES 权威数据

WES 是以下本地执行事实的权威：

- WorkLine、设备角色绑定、位置节点、队列容量和活动流程模式。
- `LineRunEpoch` 及其固定插件版本、配置版本和工作线模式。
- 设备是否可以接收下一条命令的忙、闲和故障投影。
- 物料、料箱在当前工作线位置和队列中的瞬时执行投影。
- 每一条设备命令、`TransportTask`、WMS 确认义务和对应结果证据。
- 自动工作线对 WMS 已授权结果作出的设备等待、发送、暂停、隔离和对账等即时执行决定。
- NG 原因、来源工作线和物理流转证据。

### 4.3 ECS 权威边界

设备厂商 ECS 负责：

- 给出设备真实支持的事件类型、命令类型和 JSON Payload。
- 接收 WES 的长命令并同步返回 ACK。
- 命令执行完成后异步回调最终结果。
- 设备内部步骤、PLC 顺序、机械安全互锁、防撞和硬件防呆。
- 硬件故障、急停、安全门、光栅等物理事实。

WES 只能调用 ECS 已提供的命令，不能要求 ECS 把长命令拆成 WES 设计的内部步骤。

### 4.4 RCS、AGV 与 CTU 边界

WES 只提交 WMS 已给出的搬运目标并跟踪任务级事实：

- 搬运对象。
- 来源和目标业务位置。
- 提交时冻结的批次成员。
- 批次级类型化 ACK、状态和终态。
- 批次终态中可携带的成员最终事实。

WES 不关心该请求由 WMS 转发到 RCS，还是未来直接调用 RCS。两者通过同一个 Transport Port 隔离。
当前产品只实现 WMS 转发适配器。成员事实不是独立生命周期；WES 不要求逐件、逐箱或设备内部子阶段回调，
外部通知也不能直接成为任务终态权威。

## 5. 集成协议

### 5.1 ECS 事件

ECS 事件采用 ACK-before-processing：

1. 校验最小传输合同和幂等键。
2. 持久化原始 Payload、设备身份、事件类型和接收时间。
3. 同步返回 ACK。
4. 异步交给对应 WorkLine 插件处理。

重复事件相同 Payload 返回首次 ACK；同一幂等键、不同 Payload 必须拒绝并保存冲突证据。

### 5.2 ECS 命令

设备命令是厂商定义的长命令：

1. WES 在下发前只检查目标设备是否空闲。
2. 持久化 `DeviceCommand` 后调用 ECS。
3. ECS 返回 ACK，只表示接纳，不表示物理完成。
4. 最终 CALLBACK 才能推进物理位置和对象状态。

WES 不实现设备间并发互锁。不同设备可以同时处理不同对象；只有目标设备忙或目标位置被占用时等待。

### 5.3 WMS 调用

WMS HTTP 访问由 Phase 3 `WmsClient` 薄封装提供：

- Client 只统一 WMS origin、GET/POST、query、headers、JSON 编解码、传输事实和资源关闭。
- 具体业务模块在真实需求出现时定义自己的 method/path、request/response DTO 和结果解释。
- 当前 WMS outbound 合同无认证，不存在认证配置或预留 seam；真实合同明确要求时再由最窄 owner 实现。
- 工作线插件不直接访问 HTTP；它只消费对应业务模块提供的类型化业务结果。
- WMS 结果是业务事实，不伪装成 ECS ACK/CALLBACK。

WMS 四类普通业务事件通过 `/api/v1/callback/event` 接收，必须先持久化为 `InboundEvidence` 再 ACK，并且只触发对应
工作线对象判定。`WMS_EFFECT_STATUS_HINT` 的当前 successor 为 `NONE`；Phase 5 只删除旧 route、payload、OpenAPI 和测试，
不建立新 hint 路径。普通事件不得直接充当外部任务终态。

每项具体 WMS 业务 API 在对应业务开发中采用一个显式纵切片：业务 owner 定义具名方法、固定 path、request/response DTO、
业务结果解释和合同测试，并复用 `WmsClient.get/post`。Phase 3 不预建业务 Port、Gateway、normalizer、operation registry、
动态发现、配置驱动 API 或 WMS codegen。只有真实重复出现后才提取业务 helper。

`docs/hardware/wms_rcs_interface_requirements.md` 是 WMS 交互约定初稿，作为业务输入只读保留；具体 API 开发时再逐项确认。
任何具体业务 wire 未确认只阻断该业务 API，不阻断 Phase 3 共享 Client。

当前由 WMS 转发的 AGV/CTU 操作不属于 Phase 3。Phase 4 通过 Transport Port 和复用 `WmsClient` 的业务模块调用 WMS，
并由 `TransportTask` 统一拥有任务持久化、领取、状态查询、取消、恢复、批次状态和终态推进；成员只作为冻结请求事实和
终态结果事实存在。网络目标是 WMS 不改变其 Transport 语义所有权。

WMS 业务写操作和 Transport 请求都可在各自可靠对象内部保留 `dispatch_key`，但 wire 只发送对应外部合同批准的唯一
幂等/关联字段，不自动暴露内部字段名。WMS 原子幂等、回显和安全重提规则，以及 Transport submit/status/cancel 关联，
必须分别写入对应业务合同和 Phase 4 Transport 合同，不能由 WES 单方面推定。

Phase 3 Client 行为无状态，只复用 Phase 2 Transport 执行一次有界请求/响应并完成 JSON 编解码；不打开数据库事务，
不持久化 evidence，不拥有 breaker、retry、分页或业务终态。具体 API 的字段、items 和尺寸约束由该业务合同负责。

只有发生 `WmsDependencyFailure` 时才进入以下依赖暂停：

- 停止接纳新的 WMS 依赖对象。
- 已下发的设备命令继续等待并消费最终 CALLBACK。
- 已完成的物理事实写为待 WMS 确认义务。
- WMS 恢复后，只有逐 operation 合同明确批准安全重提且对象显式 `retryable=true` 时，可靠对象才保留内部原
  `dispatch_key`，映射为唯一获批 wire 字段后重提；否则保持暂停并进入人工对账。

这属于依赖暂停，不属于 NG，也不属于硬件故障。`WmsBusinessReject` 交由插件按 9.1 节处理；
`WmsContractFailure` 记录合同告警并封闭失败。两者都不得进入依赖重试。

## 6. 最小执行内核

### 6.1 核心对象

目标内核只保留具体执行对象：

| 对象 | 职责 |
| --- | --- |
| `WorkLine` | 工作线静态身份、设备和位置拓扑 |
| `LineRunEpoch` | 一次活动流程模式和固定版本边界 |
| `MaterialExecution` | 单个完整料盘或单个可执行物料单位的推进证据 |
| `BinExecution` | 单个料箱在滚筒线和工作位中的推进证据 |
| `PositionProjection` | 位置和队列的当前占用 |
| `DeviceRuntimeProjection` | 单设备忙、闲、故障和当前命令 |
| `DeviceCommand` | ECS 命令、ACK、CALLBACK 和幂等事实 |
| `TransportTask` | AGV/CTU 搬运请求、批次状态和终态中的成员最终事实 |
| `WmsConfirmation` | 待提交或已完成的 WMS 业务确认 |
| `InboundEvidence` | ECS 事件、WMS 输入和回调的持久化原始证据 |

不再使用一个通用 Session 同时承载工作线、物料、料箱、设备和恢复状态。`LineRunEpoch` 管版本边界，
`MaterialExecution` 和 `BinExecution` 管对象推进，具体命令和外部义务各自拥有生命周期。

### 6.2 最小执行路径

```text
ECS Event / WMS Input / External Result
                │
                ▼
        InboundEvidence
                │
                ▼
       WorkLine Plugin Handler
                │
                ▼
       封闭的执行 Decision
        │       │       │
        ▼       ▼       ▼
DeviceCommand  TransportTask  WmsConfirmation
 ACK/Callback   ACK/Query/Terminal   Sync Outcome
        └───────┴───────┘
                │
                ▼
    对象与位置投影更新并继续判定
```

不保留通用 `RuntimeIntent → Generic Effect → System Capability` 热路径。可靠投递仍然存在，但分别落在
`DeviceCommand`、`TransportTask` 和 `WmsConfirmation` 的具体状态与重试策略中。

### 6.3 Decision 边界

插件只能返回封闭 Decision，例如：

- 等待目标设备或位置。
- 下发一个 ECS 命令。
- 请求一次 AGV/CTU 搬运。
- 创建一次需要可靠提交的 WMS 确认义务。
- 按 WMS 封闭结果将对象执行到指定下一步或 NG 分支。
- 完成本次对象执行。

当前步骤所需的同步 WMS 业务决策或事实查询通过对应业务模块执行；业务模块内部复用 `WmsClient` 并返回类型化结果，
插件不接触 HTTP，也不得组合原始事实重算业务结果。
会改变 WMS 业务状态的确认不在插件内直接发送，而是由 Decision 创建 `WmsConfirmation`，以便持久化和重试。

插件不能直接写数据库、发 HTTP、调用 Repository 或自行启动后台任务。

## 7. WorkLine 与插件扩展

### 7.1 代码插件

不同客户、设备和工作线流程通过代码插件扩展，不使用声明式工作流 DSL。

插件职责：

- 解释已经映射到设备角色的厂商事件。
- 读取注入的只读工作线投影，并通过对应业务模块取得 WMS 封闭业务结果。
- 校验结果的关联、版本、时效和物理可执行性，不改变其业务语义。
- 将 WMS 业务结果映射为等待、发送、暂停、隔离或对账等封闭执行 Decision。

插件不负责：

- 事务、Repository、幂等、重试和 Outbox。
- 设备安全互锁。
- WMS 库存和单据业务。
- 来源、目标、优先级、路线、NG/等待/替代、取消、恢复或业务终态裁决。
- RCS 车辆调度。

具体插件不放入 WES 核心 `src/`。仓库内二次开发插件使用独立包结构：

```text
workline_plugins/<plugin_key>/
├── pyproject.toml
├── src/
├── tests/
└── fixtures/
```

设备厂商 Adapter 与执行插件使用并列而非嵌套的独立包结构：

```text
device_adapters/<adapter_key>/
├── pyproject.toml
├── src/
├── tests/
└── fixtures/
```

每个插件包只声明 WES SDK 和业务侧依赖；每个 Adapter 包独立声明厂商协议依赖。两类包分别维护测试入口，
代码、测试和 fixture 必须与各自所有者同包交付；核心 `pyproject.toml` 的默认 `testpaths = ["tests"]` 不收集
任何二次开发包测试。

根项目使用 uv workspace 管理核心与已交付的 Adapter/插件包，统一锁定共享依赖；每个包仍必须独立构建和运行
测试。客户镜像在构建期通过显式 package 列表安装所需 Adapter 与插件，并在 composition root 显式绑定；不建设
运行时动态发现、私有包 registry 或按字符串扫描目录。删除任一包时同时移除 workspace member、镜像安装项、
装配绑定及该包，核心无需保留 tombstone 或兼容入口。

### 7.2 装饰器与依赖注入

- 装饰器只表达静态 Handler 元数据，例如事件角色、事件类型和适用流程。
- 显式依赖注入提供具体业务能力、`ProjectionReader` 和 Decision Factory；插件不直接注入 `WmsClient`。
- 禁止 Service Locator、运行时全局容器查找和任意字符串动态 import。

### 7.3 设备厂商合同

厂商提供接口文档和 Payload 样例。实施团队根据文档编写：

- HTTP Adapter。
- 输入输出 DTO。
- 事件和命令映射。
- 厂商合同测试。

`docs/hardware/` 原样保留厂商提供的协议与联调资料，作为 Adapter 合同实现输入；即使资料较旧或与当前实现存在
差异，也不得按历史设计移出项目或反向改写。同目录人工转写和供应商联调说明属于可检索派生资料，必须与原始
PDF 明确区分，也不得作为当前 Adapter 合同或 WES 架构真源。差异必须在当前 Adapter 合同、映射与测试中显式处理。

只验证共享传输协议和基础传输错误分类的合同测试属于 WES 核心。厂商 DTO、真实合同要求的认证差异、事件/命令 Payload、
原始码映射和厂商合同测试只能由对应 Adapter 包拥有；插件只消费标准化角色事件、WMS 业务结果与逻辑动作，并在插件包内
验证工作线执行推进。一次客户交付可以同时包含 Adapter 包和插件包，但不得混淆两者所有权，二者测试均不进入核心
`tests/`。

运行配置只绑定工厂实际设备 ID、Endpoint、工作线角色和现场容量；当前不存在 outbound 凭据配置。系统不要求厂商维护 WES Manifest，
也不把厂商命令重新设计成 WES 自有指令集。

### 7.4 Convention over Configuration

按约定提供默认角色和行为：

- 标准位置角色：入口、缓存、SCAN1、SCAN2、SCAN3、退料缓存和 NG。
- 标准设备角色：扫码设备、滚筒线、来源机械臂、目标机械臂、扫码平台。
- 标准流程模式：入库、出库。
- 标准设备忙闲判定和命令证据字段。

只有现场无法推导的内容需要配置，例如设备实例、Endpoint、位置容量、角色绑定和实际物理拓扑。

### 7.5 不建设执行插件模板

平台只提供最小 SDK、脚手架、合同测试工具和一个最小示例。粗分机、自动分拣、人工分拣和满箱交换都是
真实执行插件，不作为“可复制执行模板”。

最小示例只证明 SPI/SDK 可用，不承载任何真实工作线规则。核心 `tests/` 可以使用最小 fake 验证插件接口、
依赖注入、封闭 Decision 和禁止数据库/HTTP 访问等边界，但不得包含粗分机、自动分拣、人工分拣、满箱交换
或其他具体客户流程的 handler、fixture、参数组合和期望结果。

具体插件测试由对应 `workline_plugins/<plugin_key>/tests/` 唯一拥有，通过插件包自己的 CI 或显式命令执行；
未通过自身测试的插件不得进入部署包。插件测试不进入 WES 核心默认回归、核心 HEAVY selector 或核心覆盖率。

相似逻辑在出现三次且语义稳定前允许局部重复。满足 Rule of Three 后，只抽取小型技术库，不抽取通用
工作流框架。

## 8. 工作线并发与版本

### 8.1 对象级流水并发

工作线上的设备独立推进不同对象：

```text
对象 A：出料设备
对象 B/C：输送设备或队列
对象 D：扫描设备
对象 E/F/G：等待入料
```

设备完成当前长命令后即可处理下一对象，不等待前一个对象走完整条工作线。

软件只校验：

- 目标设备是否空闲。
- 目标位置或队列是否有容量。
- 当前对象是否满足本步骤业务条件。

设备间物理互锁由 ECS/PLC 完成。

### 8.2 单线活动流程

同一个 WorkLine 可以同时具备入库和出库插件，但一个 `LineRunEpoch` 只激活一个流程。

切换要求：

- 工作分支、位置和本线缓存中没有对象。
- 本线设备全部空闲。
- 没有已经承诺给本线、尚未完成的对象。
- 没有待完成的人工或自动作业。

切换后创建新的 `LineRunEpoch`。活动 Epoch 固定插件版本、配置版本和流程模式。结构拓扑变更同样要求清线。

模式不匹配时：

- WMS 单据同步拒绝。
- ECS 事件先 ACK 并持久化证据。
- 不执行不匹配流程并产生告警；不得因模式不匹配自动启动另一流程。

## 9. 异常、NG 与恢复

### 9.1 业务 NG

以下属于由 WMS 给出的正常业务分支：

- WMS 判定条码或扫码业务资格不通过。
- 测量结果不通过。
- WMS 以稳定拒绝码明确给出的 NG 业务结果。
- 没有符合条件的目标料格。
- 人工扫码或物料校验失败。
- 来源或目标业务授权不满足。

具体业务模块返回的 WMS 拒绝结果必须携带封闭处置语义；插件只校验关联并映射为执行动作。插件不得根据
`reason_code` 自行选择 NG、等待、替代路径或人工处理；结果缺失、过期、矛盾或物理不可执行时 fail closed 并暂停当前对象。

业务 NG 不创建 RuntimeHold，也不冻结无关对象和设备。

### 9.2 硬件故障

只有 ECS 明确报告的设备、急停、安全或机械故障属于执行异常。默认隔离当前对象和相关设备；
现场可以按设备或工作线配置更严格的停止策略。WES 不自动解除硬件故障，也不推测物理状态。

### 9.3 重启恢复

采用“持久化证据 + 人工清线恢复”：

1. 重启后停止工作线新对象接纳。
2. 保留所有 InboundEvidence、DeviceCommand、ACK、CALLBACK 和位置投影。
3. 未明确终态的在途对象标记为需要现场清线。
4. 迟到 CALLBACK 继续保存，但不自动恢复物理编排。
5. 操作员完成清线并确认后创建新的 `LineRunEpoch`。

不实现自动恢复、自动重放物理命令或根据数据库状态猜测现场位置。

## 10. 资源与物料模型

### 10.1 物理承载关系

- 五层货架储位容量为 1，直接存放一个料箱。
- 单层货架储位容量为 1，直接存放一个料箱。
- 料箱料格可以存放多个完整料盘，按后进先出队列管理。
- 退货货架和转运货架的储位直接存放料盘。
- 退货货架和转运货架的单个储位容量为 1 个完整料盘。

WES 保存当前工作线需要的活动投影；货架、储位、料箱、料格和库存的全局权威仍在 WMS。

### 10.2 料盘与包装数量语义

ECS 每次物理搬运一个完整料盘。一个来源绑定项对应一个唯一 `PkgID` 和一个料盘级 `MaterialExecution`。
SixInOne 中的 `Qty` 表示该包装内的物料数量，不用于展开多个料盘执行；物料、厂商和批次等明细均来自该包装的
完整 6 合 1/PKG。

### 10.3 出库来源

`PkgID` 是完整料盘的唯一业务身份，不定义其他身份字段或兼容别名。排队任务只保存目标项；任务启动时，WMS 原子
绑定每个目标项的完整 6 合 1/PKG、`PkgID`、精确物理来源和 LIFO 顺序。五层货架料箱料格中的待拣料盘必须构成
当前顶部连续可达前缀；退货或转运货架以单储位、单料盘作为精确来源。WES 校验并建立作业期执行，不自行分配或
重排来源。

### 10.4 自动线即时目标位置执行

自动设备投放前由 WMS 返回唯一目标料箱和料格；WES 不提前预留、不在授权范围内二次选址：

1. 目标机械臂准备执行 PUT 前读取最新本地投影。
2. 请求或消费 WMS 返回的唯一目标位置；WMS 返回等待或拒绝时，WES 不创建设备动作。
3. WES 只校验目标与当前设备、拓扑及本地物理投影是否可执行；冲突时暂停并重新请求 WMS，不自行替换目标。
4. 把 WMS 目标和本次执行关联写入 `DeviceCommand` 证据。
5. ECS 成功 CALLBACK 后更新本地投影并同步确认 WMS。
6. PUT 失败时保留旧投影，后续目标仍由 WMS 决定。

每条自动工作线同一目标投放段只绑定一台投放设备，由工作线拓扑和设备忙闲保证本地物理执行串行化。
人工分拣线的目标料箱和料格同样由 WMS 作业指引决定。

## 11. 业务流程

### 11.1 粗分机

1. 操作员将完整料盘放入入口。
2. 入料机械臂感应、扫码和测量，并通过 ECS EVENT 上报。
3. WES 向 WMS 提交当前料盘身份、扫码和测量证据，由 WMS 返回封闭准入结果；WES 只校验关联、时效与物理可执行性，
   不根据物料、GRN、Package 或本地规则重算准入。
4. 通过后下发厂商定义的入料机械臂长命令。
5. 成功 CALLBACK 后，料盘进入粗分机流水线；入料机械臂立即可以处理下一料盘。
6. 流水线长命令完成后，料盘到达出料侧。
7. WES 向 WMS 请求当前料盘的唯一目标料箱料格或等待/拒绝结果，不在本地计算或选择目标。
8. 无可用货架或料格时，通过 Transport Port 请求 WMS/AGV 补充空箱货架。
9. 出料机械臂成功投放后更新本地投影，并创建对应业务义务，提交 package binding 与 inbound confirmation。

每个设备独立推进，不等待同一料盘完成整个流程。

### 11.2 自动分拣线入库

自动分拣线包含：

- 单层货架位置 A、B。
- 五层货架位置。
- CTU 入料口、退料口和各自缓存段。
- 滚筒线及 SCAN1、SCAN2、SCAN3。
- 北向机械臂、物料扫码平台、南向机械臂。

并行子流程：

1. WMS/AGV 补充单层货架和五层货架。
2. CTU 按冻结成员的批次把五层货架上的料箱投入本线入口；WES 跟踪批次级类型化 ACK、状态和终态，终态可携带成员最终事实，但不要求逐箱回调。
3. SCAN1 判定料箱是否进入本线工作队列。
4. SCAN2 到位后启动料盘聚合流程。
5. 北向机械臂从单层货架指定来源取出完整料盘并放到扫码平台。
6. 扫码完成后，WMS 返回当前工作料箱的唯一目标料格，WES 校验并执行。
7. 南向机械臂把完整料盘投入目标料格。
8. 成功 CALLBACK 后更新料盘、料箱、料格投影并同步 WMS。
9. SCAN3 把正常完成料箱送入本线退料缓存；CTU 分批取走并放回 WMS 授权的五层货架储位。

北向机械臂的厂商 ECS 命令是一个长命令。WES 不要求 ECS 上报命令内部的抓取、移动和放置步骤。

### 11.3 自动分拣线出库

1. WMS 根据订单、波次、库存和产线需求形成执行级 `PickingTask`；WES 不读取或解释这些业务单据。
2. `PickingTask` 排队时只固定目标生产转运货架面/储位，不绑定来源、料盘或具体目标货架。
3. WES 选中任务后请求 WMS 原子绑定全部目标项的完整 6 合 1/PKG、唯一 `PkgID`、精确来源和 LIFO 顺序；无完整方案时整单等待库存，不创建或占用目标架。
4. 来源绑定成功后才请求 WMS 分配并锁定目标空架；目标架与第一来源架允许并行预调度。
5. 目标面只执行非空面：仅 A 不旋转，仅 B 跳过 A 后旋转，A/B 双面按 A 后 B；来源架进入工作位时 A 面朝向设备，并按当前目标面处理来源 A/B 面。
6. 料箱料格按 WMS 给定的顶部连续 LIFO 顺序执行；退料/转运货架按单储位单料盘执行。
7. NG 料箱隔离到专用出口，正常料箱继续；替代来源只由 WMS 为符合资格的未完成项原子提供。执行中无替代库存时，目标架移至缺料待补缓存并等待 WMS 明确恢复。
8. 每个料盘物理完成后立即形成独立位置变化事实并可靠提交 WMS；任一物理结果未知时整张任务暂停。
9. 取消后的目标架继续发料、转作他用或退回由 WMS 决定；全部目标项完成并闭合逐项事实后，WES 再通知 WMS“拣货完成”，等待目标架移出工作位后释放下一任务。

### 11.4 满箱交换

满箱交换只在粗分机出料货架整体移出粗分机工作位后判断：

1. 粗分机释放整台单层货架并冻结该时点快照。
2. 新空货架补入粗分机，与旧货架的后处理相互独立。
3. 旧货架通过 AGV 移至独立满箱交换位置。
4. WES 根据冻结快照判断达到阈值的料箱。
5. WMS 授权五层货架目标储位、空箱和 CTU 搬运目标。
6. CTU 按冻结成员的批次交换满箱和空箱，并以批次级类型化 ACK、状态和终态报告；终态可携带成员最终事实，但不要求逐箱状态回调。
7. 满箱完成箱级入库并同步 WMS。
8. 剩余未满箱料箱随单层货架进入自动分拣线 A/B，继续逐料盘聚合。

满箱交换不在粗分机持续装料期间并发判断，也不与自动分拣线货架位混用。

## 12. 四条串联分拣线

### 12.1 现场结构

现场有四条并排且滚筒线物理联通的分拣线：

- 自动线 1。
- 自动线 2。
- 人工线 1。
- 人工线 2。

每条 WorkLine 都有自己的：

- CTU 入料口和入料缓存。
- CTU 退料口和退料缓存。
- SCAN1、SCAN2、SCAN3 扫码设备。
- 工作分支段。

SCAN1 决定料箱是否进入本线工作队列；SCAN3 决定料箱进入本线退料缓存还是继续直行。

不建立 `SorterCorridor`。物理串联、滚筒线输送方向和段间互锁由 ECS/PLC 完成。WES 通过下一台
SCAN1 的 EVENT 自然接续料箱位置，不配置跨线执行引擎、父子 Session 或 `next_workline_id`。

### 12.2 NG 透传

料箱执行投影至少保存：

- `disposition: NORMAL | NG`。
- `ng_reason_code`。
- `ng_origin_workline_id`。
- `ng_source_event_id`。
- `ng_marked_at`。

下游 SCAN1 首先检查 NG：

- 已带 NG 标识：直行，不进入 SCAN2，不重新绑定 WorkLine。
- 非 NG：执行本线准入规则；本线忙时等待。

下游 SCAN3 同样检查 NG：

- 已带 NG 标识：继续直行。
- 非 NG 且本线作业完成：进入本线退料缓存。
- 本线退料缓存满：等待，不改为 NG。

硬件故障不得把料箱标记为 NG。

### 12.3 非 NG 同线进出不变量

非 NG 料箱必须同线进、同线作业、同线出：

```text
disposition != NG
⇒ ingress_workline_id = work_workline_id = return_workline_id
```

首次 CTU 单箱投料成功时绑定 `owner_workline_id` 和 `line_run_epoch_id`；没有可靠单箱投料状态证据时，由首次
SCAN1 事件以原子方式绑定。本次执行期间不得修改。

校验点：

1. SCAN1：非 NG 的 `owner_workline_id` 必须等于当前 WorkLine。
2. SCAN3：非 NG 只允许进入当前 WorkLine 的退料缓存。
3. CTU 退料：实际退料口所属 WorkLine 必须等于 `owner_workline_id`。

正常料箱即使本线设备或退料缓存忙，也只能等待，不能借用其他工作线出口。

非 NG 料箱出现在其他 WorkLine 时：

- 禁止静默重新绑定。
- 禁止进入该线工作分支。
- 禁止从该线正常退料。
- 保存位置冲突证据。
- 根据实际来源判定为错误投料业务 NG 或 ECS 物理路由故障。

### 12.4 人工分拣线

人工线没有北向机械臂、南向机械臂和物料扫码平台。人工不是虚拟设备，PDA 不进入 ECS 命令模型。

WES 负责：

- 料箱在 SCAN1、SCAN2、SCAN3 和缓存中的位置。
- 滚筒线 ECS 命令和结果。
- SCAN2 人工工作位占用。
- WMS 人工作业关联和完成接纳。

WMS 负责：

- 人工入库、出库任务。
- 操作员扫码和业务校验。
- 入库目标料箱和料格。
- 出库来源物料和料格。
- 库存事务和人工任务完成。

人工入库：

```text
CTU 投箱
→ SCAN1 准入
→ SCAN2 到位
→ WES 同步通知 WMS 人工工作位就绪
→ 操作员通过 WMS 扫码并放入目标料箱/料格
→ WMS 同步通知 WES 人工作业完成
→ SCAN3
→ 本线退料缓存
```

人工出库使用同一物理路径，由 WMS 出库单、来源分配和人工取料完成事实驱动。

等待人工完成是正常对象状态，不是设备忙、RuntimeHold 或硬件故障。WMS 不可用时料箱停留在 SCAN2，
本线进入依赖暂停。

## 13. 当前系统收敛范围

### 13.1 保留

- 当前经确认的 HTTP/JSON EVENT、COMMAND、ACK、CALLBACK 目标合同。
- 入站先持久化再 ACK。
- 幂等键、Payload Hash 和冲突证据。
- DeviceCommand 的接纳与最终结果区分。
- WMS 类型化 DTO、HTTP Adapter 和同步调用证据；当前不保留 outbound 认证能力或配置 seam。
- RCS/AGV/CTU Transport Port。
- 位置、队列、货架、料箱和料格活动投影。
- 可观测性、安全和测试治理规则。
- 能直接证明通用 WES 合同、执行对象和可靠性不变量的断言；测试代码按最终对象重写，不保留旧实现结构。
- 插件 SPI/SDK、封闭执行 Decision、依赖注入以及插件不能直接访问数据库或 HTTP 的边界。
- 具体工作线执行映射断言只作为插件二次开发包的测试资产保留，不属于核心 `tests/`。

### 13.2 简化

| 当前概念 | 目标概念 |
| --- | --- |
| `ExecutionSession` | `LineRunEpoch` + 具体对象 Execution |
| 通用 `RuntimeInbox` | 有限类型 `InboundEvidence` |
| `RuntimeIntent + Effect + Outbox` | `DeviceCommand`、`TransportTask`、`WmsConfirmation` |
| Binding/Profile Snapshot | Epoch 固定插件和配置版本 |
| `RuntimeHold/Reconciliation` | 业务 NG、硬件故障、依赖暂停、人工清线 |
| 投影 God Service | 按物料、料箱、位置和设备拆分的窄服务 |
| 动态 Provider/Catalog | 部署时显式 Adapter 绑定 |

### 13.3 删除

- 通用 System Capability 平台和生成索引。
- WorkLine Manifest、Vendor Manifest 和动态能力 Catalog。
- Generic Intent/Effect 业务热路径。
- 业务 NG 的 RuntimeHold 和 Reconciliation。
- 自动物理恢复、自动 replay 和推测式状态修复。
- 提前目标料格预约及其 TTL 恢复。
- AGV/CTU 车辆实时位置和路径投影。
- 可复用执行插件模板。
- `SorterCorridor` 或四线跨线调度引擎。
- 未由当前 HTTP 需求支持的协议扩展点。

## 14. 收敛实施策略

### 14.1 未发布系统直接替换

从最新 `develop` 创建独立收敛分支，保留 Git 历史，但不保留应用层或数据库层的向后兼容。工作包只是
开发和验证边界，不是两套运行时共存方案；只有全部收敛门禁通过后，最终结果才合并回 `develop`。

禁止：

- 将 `develop` reset 到历史提交或从旧提交重新建设产品。
- 兼容 shim、旧名称 alias、re-export、deprecated wrapper、双写、双读或旧路径 fallback。
- 让旧 Runtime 继续服务部分 WorkLine，或按 WorkLine 保留两套活动执行路径。
- 为现有开发/测试数据编写转换、回填、桥接表或旧 schema downgrade。
- 为通过测试而保留旧类、旧字段、旧状态、旧配置、旧 fixture 或旧迁移断言。

### 14.2 依赖顺序

实施顺序：

1. 总控基线冻结与测试治理确认：接受本文、冻结最新 `develop` 实施基线，并确认测试所有权、重量和延后承接边界。
2. Outbound HTTP 传输基础能力收敛：全新增量交付框架无关 request/result、每外部系统由各运行时/事件循环 owner 持有
   一个 Client、单次发送、
   有界响应读取、传输事实分类和显式生命周期；不切换生产消费者，不建设 outbound 认证、重试、业务解释、registry 或 fake。
3. WMS HTTP Client 薄封装：消费 Phase 2 Transport，在独立应用包 `src/app/wms_adapter/` 中只交付
   `request/get/post/aclose`、统一 JSON 编解码、最小 factory 和开发示例；当前 outbound 无认证。本阶段不包含任何具体
   WMS 业务 API、业务 Port、数据库、evidence、breaker 或可靠生命周期，不接入生产、不修改旧实现和旧测试。
4. WES 最小平台与 Transport 能力建设：暗构建最终执行对象、三类可靠记录、通用 WorkLine、投影、最小 SPI/SDK、
   `Transport Port` 及 WMS 转发 RCS/AGV/CTU Transport Adapter；该 Adapter 复用 Phase 3 Client。普通 WMS 业务 API
   由独立业务实施计划按需实现；测试 fake 由 Phase 4 核心测试树自行定义；本阶段不修改当前生产 Composition Root、
   旧表、旧实现和旧测试。
5. 新旧能力原子切换与旧所有者删除：迁移生产消费者和装配，清理开发/测试数据，原子删除 Provider Profile、QUERY
   System Capability、旧 WMS Transport/认证、Effect/status/Outbox 可靠闭包及被最终对象替代的核心旧 owner；不迁移旧代码或旧数据。
6. 核心测试承接与平台基线验收：把剩余通用可靠性测试收敛到最终对象，完成核心/插件测试分界和平台独立验收。
7. 粗分机参考插件优化：以独立二次开发包交付第一个真实执行插件，验证平台扩展边界。
8. 分拣执行插件优化：按实际工作线、WMS 业务结果和厂家指令分别交付自动分拣、人工分拣、满箱交换和复杂出库能力。
9. 旧平台代码最终闭环清理：扫描并删除跨阶段残留，证明最终生产运行态只有一套最小执行架构。
10. 旧数据模型与迁移链清理：最终模型稳定后删除历史 schema/revision，生成单一干净 Alembic 基线。
11. 最终基线与系统验收：从空库分别验证核心、Adapter、插件、质量、部署装配和旧架构缺席门禁。

任何可靠性不变量都必须先在最终具体对象上有实现和测试，才能删除旧实现；这只是同一收敛分支内的
依赖顺序，不允许通过兼容层、双路径或旧数据迁移完成过渡。

测试治理贯穿阶段 1–8 和 11；阶段 3/4 只建立新测试 owner，生产消费者迁移和直接旧 owner 删除统一在阶段 5，阶段 9
只做跨阶段残留闭环；目标数据模型在阶段 4 建立，历史 migration 只在阶段 10 一次性重建。十一阶段的详细入口、交付物和退出门禁由
`docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md` 统一控制。

### 14.3 实施范围分解

本文是全局架构约束，不把多个独立子系统展开成一个巨型实施脚本。十一个总控阶段分别形成经批准的详细实施
计划和测试范围；同一阶段内仍可按最终对象、Adapter 或真实插件拆成可独立审查的任务，但不得改变 §14.2 的
依赖顺序和退出门禁。

现有
`docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md`
是阶段 1 的权威计划，
其中直接绑定旧生产 owner 的测试随阶段 5 原子切换完成 successor/NONE，Task 4 的混合资产与 Task 5 的剩余最终对象承接
在阶段 6 收尾；Task 7 按所有权分段完成：阶段 6 承接核心平台测试，阶段 7/8 重建具体插件测试，阶段 10/11 完成迁移链和
最终收集验收。Phase 2 已完成，详细实施计划已归档到项目外
`../archive_docs/wes_backend/docs/superpowers/plans/2026-08-04-wes-outbound-http-transport-convergence.md`，不再是当前实施入口；其余阶段的计划路径、
入口条件、交付物和
验收归属由
`docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
固定。

插件阶段不得把具体业务测试重新写入核心 `tests/`；后续阶段不得反向扩张最小执行内核。最终合并态不得
包含任何仅为旧版本、旧数据或迁移过程存在的代码。

### 14.4 测试与数据库基线

测试以本文目标行为为唯一基线：

- 当前测试若直接证明通用 WES 合同、执行对象或可靠性不变量，改写到最终对象后保留在核心 `tests/`。
- 当前测试若证明粗分机、自动分拣、人工分拣、满箱交换、复杂出库或其他具体插件业务行为，从核心
  `tests/` 移出；不把旧 Runtime/Manifest 测试原样搬入新插件包。对应插件二次开发时，按最终插件代码、
  业务验收和标准化 Adapter 输入重新建立测试。
- 当前测试若证明具体厂商 DTO、命令、事件、Payload、原始码或映射，从核心 `tests/` 移出；对应 Adapter
  二次开发时，按厂商原始合同在 `device_adapters/<adapter_key>/tests/` 重新建立测试，不得寄存在插件包。
- `workline_plugins/<plugin_key>/tests/` 与插件代码、fixture 同步交付，由插件包自己的测试入口和 CI 负责，
  不进入核心默认 pytest、核心 HEAVY selector 或核心覆盖率。
- `device_adapters/<adapter_key>/tests/` 与 Adapter 代码、fixture 同步交付，由 Adapter 包自己的测试入口和 CI
  负责，同样不进入核心默认 pytest、核心 HEAVY selector 或核心覆盖率。
- 只验证旧 Runtime、Manifest、Capability、Intent/Effect、Hold、Recovery、Reservation 或兼容入口的测试删除。
- 不保留旧行为 characterization、旧 schema upgrade/downgrade、旧 revision chain 或数据回填测试。
- 新增机器缺席门禁，禁止生产代码、测试和机器可读配置重新引入旧架构 import、配置键、别名和 fallback；
  人类阅读文档通过引用审查、原路径缺席和外部归档检查收敛，不进入 pytest 或质量门禁的正文解析。
- 新增核心测试所有权门禁：核心 `tests/` 不得包含或导入具体工作线插件；通用 WorkLine 身份、拓扑、
  `LineRunEpoch`、设备/位置投影和可靠性测试不受此限制。
- 测试删除按语义判断，不能按 `replay`、`reconciliation` 等关键词批量处理；每个旧测试必须记录
  `REWRITE`、`DELETE → successor` 或 `DELETE → NONE + 理由`，且 successor 先通过、旧测试后删除。合同样例
  回放和可靠确认重试若属于最终行为，必须使用最终领域名称继续覆盖。

数据库以最终 SQLModel metadata 为真源。所有目标模型稳定后，删除未发布 revision，使用 Alembic generator
创建新的随机 revision ID，再验证空库建库、约束、索引、schema、TimescaleDB 扩展对象及 metadata 一致性。

## 15. 验收标准

### 15.1 架构

- API → Service → Repository → Database 分层不变。
- 插件无 Repository、数据库 Session、HTTP Client 或 Service Locator。
- 生产热路径中不存在通用 System Capability 和 WorkLine Manifest 依赖。
- 新厂商接入只增加 Adapter、DTO、映射、插件或配置，不修改最小执行内核。
- 新增、优化或删除 WMS 能力只修改单个垂直能力模块、显式端口/Gateway 方法和同名测试；生产运行时无
  capability registry、动态发现或 codegen。
- WES 核心 `tests/` 不包含或导入任何具体工作线插件；只保留 SPI/SDK 边界和通用 WorkLine 能力测试。
- 每个已交付插件位于 `workline_plugins/<plugin_key>/`，代码、测试和 fixture 同包交付且独立通过测试。
- 每个已交付厂商 Adapter 位于 `device_adapters/<adapter_key>/`，代码、测试和 fixture 同包交付且独立通过测试。
- uv workspace、客户镜像安装清单和 composition root 对已选择 Adapter/插件保持一致；二者由构建期显式装配。

### 15.2 协议

- ECS EVENT 持久化后 ACK，重复事件幂等。
- COMMAND ACK 与最终 CALLBACK 明确分离。
- WMS 为同步业务能力，不伪装成 ACK/CALLBACK。
- Transport Port 可在不修改插件的情况下替换 WMS 转发适配器。

### 15.3 并发

- 同一工作线上至少覆盖多个对象分别位于入口、扫描、输送和出料设备的并行场景。
- 单设备忙只阻止该设备的新命令。
- 目标位置满只阻止相关对象。
- 不存在 WES 设备间软件互锁或工作线级全局锁。

### 15.4 资源

本节与 §15.5 中绑定具体工作线业务的验收项由对应二次开发插件包及其独立测试证明；WES 核心只验证位置
容量、对象占用、投影更新和 Decision 执行等通用机制，不在核心 `tests/` 重复具体业务场景。

- 出库按 WMS 在任务启动时原子绑定的料格顶部连续 LIFO 顺序执行，`PkgID` 无身份别名。
- 启动时来源绑定必须完整且原子；无完整来源时不得创建或占用目标架。
- 单面目标不执行空面动作；每个料盘位置事实与整单完成分别提交，未知物理结果暂停整张任务。
- 退货、转运货架单储位只能有一个完整料盘。
- 一个来源绑定项只关联一个唯一 `PkgID` 和一个完整料盘执行；`Qty` 只表示包装内物料数量。
- 自动线目标格在 PUT 前即时计算，失败后重新计算。
- 人工线目标格始终由 WMS 决定。

### 15.5 四线

- 两条自动线使用同一自动插件的两个配置实例。
- 两条人工线使用同一人工插件的两个配置实例。
- NG 料箱在后续 SCAN1/SCAN3 只直行并最终到达统一 NG 区。
- 非 NG 料箱只能从其 `owner_workline_id` 对应退料口离开。
- 本线忙或退料缓存满时正常料箱等待，不转投其他线。
- 非 NG 错线时禁止重新绑定和错误出料，并保存冲突证据。

### 15.6 故障与恢复

- WMS 判定为 NG 的业务结果进入 NG；WMS 给出的容量不足等待结果保持当前对象等待，不创建 RuntimeHold。
- 硬件故障只隔离配置范围内的对象、设备或工作线。
- WMS 不可用停止新接纳但不丢失已完成物理事实。
- 进程重启后不自动下发物理恢复命令。
- 人工清线后使用新 Epoch 恢复。

### 15.7 零兼容与干净基线

- 生产代码中不存在兼容 shim、旧名称 alias、re-export、deprecated wrapper、双写、双读或旧路径 fallback。
- 测试套件中不存在只验证旧架构、旧数据迁移或旧 revision chain 的测试。
- 核心 `tests/` 中不存在具体工作线/插件行为测试；插件测试只存在于对应二次开发包。
- 当前态文档和 active TODO 不再把 Runtime、Manifest、System Capability、Hold、Recovery 或 CellReservation
  作为未来目标依赖。
- 收敛完成时 `migrations/versions/` 只包含最终模型的干净基线及其后真实新增的 revision。
- 清空开发/测试数据库后，`alembic upgrade head` 可以从空库建立完整最终 schema。
- 全仓架构扫描、默认快速回归、受影响重测试和质量门禁全部通过。

## 16. 风险与控制

| 风险 | 控制 |
| --- | --- |
| 删除通用 Runtime 时丢失可靠投递 | 在同一收敛分支先把可靠性落入三个具体执行记录并通过测试，再删除旧实现；不建立兼容层 |
| 过早重建数据库基线导致反复漂移 | 最终模型和 metadata 稳定后只生成一次干净基线，并从空库验收 |
| 按关键词删除测试误伤目标合同 | 使用一次性逐文件处置矩阵：核心不变量改写保留，插件行为移交所有权，旧实现断言明确 successor 或 NONE 后删除 |
| WMS evidence 失败导致重复写操作 | 发送前证据失败则不发送；发送后证据失败标记远端结果未知，并由可靠对象使用同一 `dispatch_key` 恢复 |
| 旧通用实现把未经厂商合同证明的分页带入新 Adapter | 列表能力固定一次有界请求/响应且无 cursor；真实合同出现后再整体修订，不预留 seam |
| 具体插件测试继续污染核心套件 | 核心测试所有权门禁禁止具体插件路径和 import；插件包自带 tests 并独立运行 |
| 先删插件测试后永久失去执行验收依据 | 删除提交标记插件所有权；插件代码、测试、fixture 必须同工作包重新交付，未通过测试不得进入部署包 |
| WMS 权威与 WES 投影漂移 | 每个物理成功即时写本地证据并提交 WMS 确认义务 |
| 长命令重试造成重复物理动作 | 命令 ID 幂等、ACK 与 CALLBACK 分离，不自动重放未知物理结果 |
| 设备厂商 Payload 差异污染核心 | 厂商 Adapter 和合同测试隔离，插件只接收角色化输入 |
| 四线串联被过度抽象 | 不建 Corridor；依靠 NG 标识和下一 SCAN1 EVENT 接续 |
| 正常料箱错误跨线 | 不可变 `owner_workline_id`，SCAN1、SCAN3、CTU 退料三重校验 |
| 插件复制演变成平台 | Rule of Three 后只抽小型技术库，不建设 DSL 或通用执行引擎 |
| 当前文档目标态相互矛盾 | 显式同步修订当前 SRS、ADR、插件指南和合同引用；仅将被取代的历史 SPEC/PLAN 移出项目归档 |

## 17. 最终设计原则

1. WMS 管业务和库存，WES 管工作线本地执行，ECS 管设备物理动作。
2. 具体执行记录优先于通用 Intent、Effect 和 Capability 平台。
3. 代码插件优先于声明式工作流。
4. 显式依赖注入优先于 Service Locator。
5. 约定优先于配置，现场事实必须配置。
6. 业务 NG 是正常流，硬件故障才是异常。
7. 持久化证据和人工清线优先于自动恢复猜测。
8. 对象级并发优先于工作线级串行。
9. 即时目标格计算优先于提前预留。
10. 当前真实需求优先于推测性的通用平台。
11. 最终单一路径优先于迁移兼容；未发布系统不保留旧版本或旧数据负担。
