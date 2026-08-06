# 软件需求规格说明书 (Software Requirements Specification)

> **项目名称**: 休斯顿P9 智能仓储执行系统 (Houston P9 Intelligent Warehouse Execution System - WES)
> **系统定位**: 独立部署的集成化控制中台 (Independent Integration & Control Middleware)
> **文档版本**: 3.0 (Architecture Convergence)
> **日期**: 2026-08-03
> **状态**: Current Requirements Baseline
>
> **文档层级**: 本文是产品范围、参与方职责和功能/非功能需求真源；
> `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
> 负责把这些需求收敛为当前目标架构；
> `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md` 只负责实施顺序。
> SRS 不规定旧 Runtime、旧插件框架或兼容迁移路径；出现实现机制冲突时，以当前顶层 SPEC 为准，并同步修订本文需求表述。

## 1. 引言 (Introduction)

### 1.1 目的 (Purpose)

本文档定义 **休斯顿 P9 智能仓储执行系统 (WES)** 的产品范围、参与方职责以及功能与非功能需求。
架构机制、内部对象和收敛路径由当前顶层 SPEC 定义，实施计划不得反向修改本文的业务需求。

本项目不再定位为传统的 WMS，而是一个 **独立于现有企业级 WMS/SAP 的控制中台**。它旨在作为一个高可用、低延迟的智能化中间层，向上承接 ERP/WMS 的业务单据，向下协调自动化执行：当前交付只直接接入顶层 SPEC §3.1 已确认工作线所需的 ECS/WCS/视觉类作业设备；AGV/CTU/RCS 类搬运、交换、旋转任务统一提交现有 WMS 转发执行，WES 不直连 RCS。贴标、X-Ray、LCR 及机构件/SFC 协同属于本文保留的未来产品需求，不进入当前十一阶段实现与验收。

系统具备 **独立部署 (Standalone Deployment)**、**API 驱动 (API-Driven)** 和 **业务插件显式扩展 (Explicit Plugin Extension)** 的特性。外部网络或上层系统波动时，WES 必须保留已接收事实、限制影响范围并进入可诊断的等待或对账状态，不得推测外部结果后自动续行。

### 1.2 产品范围 (Scope)

本系统的核心职责是 **执行 (Execution)** 与 **协调 (Coordination)**：

* **业务解耦**: 将自动化执行策略、设备作业逻辑从企业级 SAP/WMS 中剥离，由本中台统一协调；库存、货架资源与 RCS 调度权仍由现有 WMS 持有。
* **多设备协同**: 当前直接协调已确认工作线所需的 ECS/WCS/视觉类作业设备；AGV/CTU/RCS 任务由 WES 生成业务需求并提交 WMS，由 WMS 转发执行。贴标、X-Ray、LCR 等设备仅保留需求边界，待对应工作线与厂商合同获批后交付。
* **边界化接口**: 提供版本化 RESTful API 供上层系统调用，通过厂商专属 Protocol Adapter 对接 WES 直连设备；搬运/交换/旋转类任务通过 WMS 接口转发。
* **核心域**:
  * **入库执行**: 码头收货、IQC 路由、上架策略执行。
  * **库存代理**: 采用 **按需动态查询 (On-Demand Query)** 模式，实时调用 WMS 接口获取库存数据，不维护本地库存副本。
  * **出库协同**: SMT 滚筒波次计算、自动线发料协调。

### 1.3 定义、首字母缩写和缩写 (Definitions)

| 缩写                 | 全称                       | 定义                                                                    |
| :------------------- | :------------------------- | :---------------------------------------------------------------------- |
| **WES**        | Warehouse Execution System | **仓储执行系统**（本项目）。位于 WMS 与 WCS 之间的智能控制中台。  |
| **Upstream**   | Upstream System            | **上游系统**。指 SAP 或 企业级 Legacy WMS，负责主数据与财务账务。 |
| **Downstream** | Downstream System          | **下游系统**。指 RCS, ECS, WCS 等硬件控制层。                     |
| **Middleware** | -                          | **中台/中间件**。本系统的架构定位，强调连接与协调。               |
| **ECS**        | Equipment Control System   | 设备控制系统，负责具体硬件动作执行。                                    |

---

## 2. 总体描述 (Overall Description)

### 2.1 系统架构前景 (System Architecture Perspective)

本系统采用 **三层架构 (Three-Tier Architecture)**，作为独立节点运行：

* **L1 - 决策层 (External - SAP + Legacy WMS)**:
  * **SAP 职责**: 财务核算、采购计划、主数据管理 (Master Data)。
  * **Legacy WMS 职责**: 全公司库存账务、非 P9 区域的仓储管理。
  * **交互**: 仅通过标准 API 向本中台下发 "单据 (Orders)" (如: 收货通知单、工单)。
* **L2 - 控制中台 (This System - P9 WES)**:
  * **职责**:
    * **独立数据库 (Independent DB)**: 拥有私有的 PostgreSQL/Redis 实例，不依赖 L1 数据库。
    * **业务插件 (Business Plugin)**: 每条 WorkLine 通过显式注册的代码插件实现本线波次、路由和箱位分配规则。
    * **执行内核 (Execution Kernel)**: 接收插件返回的封闭 Decision，可靠创建设备命令、搬运任务或 WMS 确认义务。
    * **对象投影 (Object Projection)**: 分别跟踪物料、料箱、位置、设备命令和外部义务，不使用一个通用任务状态机承载全部职责。
* **L3 - 执行层 (External - Hardware)**:
  * **职责**: 物理动作执行 (RCS, ECS)。其中 ECS 类作业设备由 WES 直接接入；RCS/AGV/CTU 类运输与交换设备由 WMS 统一调度并向 WES 回传结果。

### 2.2 关键架构特性 (Key Architectural Characteristics)

为满足 "集成化控制中台" 的定位，系统必须具备以下特性：

1. **独立部署能力 (Independent Deployment)**:
   * 系统包含完整的前后端、数据库及中间件 (Dockerized)。
   * **网络依赖性**: 系统作业强依赖于上游 WMS 的在线状态。若与 SAP/Legacy WMS 断网，系统将执行以下策略:
     * **立即暂停**: 所有涉及库存变动的业务任务 (收货、发料、装箱等)
     * **外部续行**: 已由 WMS 接收并转发的纯物理搬运任务可由外部系统自行完成；WES 暂停新的搬运/交换需求提交，并等待回调或对账恢复。
     * **证据驱动恢复**: 网络恢复后先查询未决 `TransportTask`；未决 `WmsConfirmation` 保留内部原
       `dispatch_key`，仅当逐 operation 合同明确批准安全重提且对象显式 `retryable=true` 时，才映射为该 operation 唯一获批
       的 wire 幂等/关联字段后重提。取得终态证据后重新执行对象级准入；条件不满足、结果未知或证据冲突时保持暂停并
       进入人工对账。
2. **API 驱动架构 (API-Driven)**:
   * **API First**: 所有功能（包括前端 UI）均通过明确版本的 RESTful API 访问。
   * **边界合同**: 上层业务接口由 WMS 合同定义；设备接口由厂商 Adapter 映射。核心不定义要求所有 ERP 或硬件厂商适配的通用任务协议。
3. **显式业务扩展 (Explicit Business Extension)**:
   * 不同工作线通过独立代码插件实现业务规则并静态注册；不提供 Python/Lua 动态脚本、声明式工作流 DSL、低代码规则引擎或运行时插件发现。
4. **独立的数据库实例 (Independent Database Instance)**:
   * 业务数据 (Business Data) 与 硬件日志 (IoT Logs) 分离存储。
   * 确保高并发下的读写性能。

### 2.3 运行环境 (Operating Environment)

* **部署方式**: 容器化部署 (Docker)，支持私有云或边缘服务器 (Edge Server)。
* **数据库**: PostgreSQL 17+ (业务数据) + TimescaleDB（时序插件）, Redis (缓存与队列).

---

# 3. 具体需求 (Specific Requirements)

本章节详细定义了 P9 WES 中台的核心业务流程、控制逻辑及与外围系统的交互规范。

> **本阶段集成边界 (Phase Boundary)**
>
> - **RCS/AGV/CTU 调度**: 本版本仍由现有 WMS 统一调度。WES 只生成搬运、交换、旋转等业务需求并提交给 WMS，由 WMS 转发给 RCS/AGV/CTU；E08–E14 只通过冻结 ACK、status query 与 typed terminal result 收敛，WES 不直接调用 RCS，不直接下发 AGV/CTU 任务。
> - **PDA 交互**: PDA 仅对接 WMS 应用；若 WES 需要感知 PDA 结果/事件，由 WMS 推送/同步给 WES。
> - **自动化设备**: 当前只交付顶层 SPEC §3.1 已确认工作线所需的 ECS/WCS/视觉类直连接入。贴标、X-Ray、LCR 和自动打印仍是未来需求；对应工作线获批后仍遵循“作业设备经 WES Adapter 接入、WMS 不直连设备”的边界。
> - **标签打印**: 当前只保留合同边界。进入交付批次后，WES 生成打印模板/ZPL；自动打印设备由 WES 下发，人工/非自动打印由 WMS 获取模板并回执结果。

### 3.1 硬件清单与基础配置 (Hardware & Configuration)

下表是整体产品需求涉及的硬件清单，不等于当前交付清单。当前只实现顶层 SPEC §3.1 与十一阶段总控明确批准的工作线；其余硬件在对应工作线获批后再由独立 Adapter 和业务插件承接：

| 区域 (Area)        | 关键硬件 (Hardware)      | WES 协调职责 (Coordination Role)                                               |
| :----------------- | :----------------------- | :----------------------------------------------------------------------------- |
| **码头区**   | PDA, 打印机              | 提供收货单据同步，生成栈板条码 (ZPL)，处理绑定数据。                           |
| **IQC 区**   | PDA                      | 提供抽检策略路由，接收 QMS 检验结果。                                          |
| **装箱区**   | 机械臂, 视觉相机, 输送线 | **关键**: 下发装箱策略 (Binning Strategy)，校验视觉识别数据 (PKG/Dims)。 |
| **SMT 区**   | CTU (料箱机器人), 机械臂 | **关键**: 调度混合入库 (Exchange/Picking)，计算滚筒波次发料。            |
| **机构件区** | 自动拆包线, E-AGV        | 协调 A/B 栈板同步进线，采集追溯数据。                                          |
| **退料区**   | X-Ray, LCR, 贴标机       | **关键**: 执行清点/测试逻辑判定，生成新标签数据。                        |

#### 3.1.1 数据初始化 (Data Initialization)

WES 需在上线前完成以下 "物理-数字" 映射的初始化：

* **执行映射**: 从 WMS 获取其权威的物理箱号 (Bin ID)、货架 (Rack ID) 与地码 (Location Code) 绑定事实，
  只投影当前 WorkLine 执行所需的关系；WES 不建立全局货架或容器主数据。
* **基础策略输入**: 从 WMS 获取其权威的供应商物料尺寸/厚度事实，保存工作线执行所需的最小版本化投影并绑定到
  `LineRunEpoch`，作为装箱算法输入；WES 不成为该主数据的权威所有者。

#### 3.1.2 货架类型定义 (Rack Type Definitions)

P9 智能仓库使用三种货架类型，各有不同的物理结构和业务用途：

**1. 单层货架 (Single-Layer Rack)**

* **物理结构**：
  * 单层平面结构，可承载 **4 个料箱 (Bins)**。
  * 无 A/B 面区分，所有储位在同一平面。
  * **货位编码**: 单层移动料架每架 4 个货位，按 Excel 初始化协议使用 `A/B/C/D`
    顺时针编码；货位条码示例为 `NHW-1CLJ-0096-1C-1`。
* **业务用途**：
  * **中转缓存 (Transit Buffer)**: 用于收货装箱、发料准备等临时作业场景。
  * **快速流转**: 适合高频次的装卸操作，AGV 可快速搬运整架。
* **典型场景**：
  * 码头拆箱 -> 装箱作业 -> 单层货架 -> 运至五层货架存储区。
  * 五层货架 -> 单层货架 -> 流水线发料。

**2. 五层货架 (Five-Layer Rack)**

* **物理结构**：
  * 垂直五层结构，每层可承载 **4 个料箱 (Bins)**，总容量 **20 个料箱**。
  * 具有 **A 面 / B 面 (Side A / Side B)** 区分，CTU (Container Transfer Unit) 可从两侧操作。
* **业务用途**：
  * **高密度存储 (High-Density Storage)**: SMT 存储区的主要存储设备。
  * **满箱交换**: 支持 CTU 执行原子化的料箱交换操作 (Exchange)。
* **典型场景**：
  * 单层货架满箱 -> CTU 交换至五层货架空箱位。
  * 生产发料: CTU 从五层货架取料箱 -> 放至单层货架 -> AGV 运至产线。
* **调度策略**：
  * **冷热区管理**: 五层货架冷热区、A/B 面负载、空箱授权和 CTU 路径由 WMS/RCS 作为权威系统判断。
  * **A/B 面平衡**: WES 可基于工单、波次或产线需求生成策略建议、交换需求或补给需求并提交 WMS；WES 只保存 WMS 授权结果、回调、执行投影和对账证据，不动态平衡五层货架两侧真实负载。

**3. 生产货架 / 退货货架 (Production Rack / Return Rack)**

* **物理结构**：
  * **货架式结构 (Shelf Structure)**: 多个独立储位 (Slots)，每个储位可存放 **单个料盘 (Individual Tray)**。
  * 具有 **A 面 / B 面 (Side A / Side B)** 区分，人工可从两侧操作。
  * 储位数量根据货架高度和料盘尺寸动态计算 (例如: 7 寸料盘可堆叠更多层)。
* **业务用途**：
  * **生产货架**: 放置产线正在使用的料盘，供 SMT 机台直接上料。
  * **退货货架**: 放置产线下线的剩余料盘，等待拆飞达后退库。
* **典型场景**：
  * 生产发料: 料箱 -> 人工拆箱 -> 料盘放至生产货架 -> 产线上料。
  * 生产退料: 产线下料 -> 料盘放至退货货架 -> 拆飞达 -> 装入料箱 -> 退库。
* **管理特点**：
  * **料盘级执行证据**: WES 保存检测、贴标、人工确认，以及 WMS ACK/status/typed terminal result 形成的 PKG、Rack_ID、Side、Slot_ID 执行投影或证据；真实储位归属、库存可用性和 A/B 面资源授权以 WMS 为准。
  * **A/B 面证据投影**: WES 可展示不同产线或工单的执行证据分布，但不作为生产货架或退货货架 A/B 面真实占用主账。

---

### 3.2 收货入库执行 (Inbound Execution)

本模块处理从码头卸货到上架任务生成的全过程。当前阶段 **码头到暂存区由 WMS 完整主导**，WES 不参与打印与绑定环节；后续进入自动化交接/上架环节时再切入 WES 编排。

#### 3.2.1 码头接收与绑定 (Dock Receiving & Binding)

* **流程概述**: 物流录入 -> 导入到货清单 -> 打印栈板码 -> 物理扫码绑定 (支持混托) -> 通过 WMS 转发 RCS 分流。
* **关键变更**: **不再依赖供应商 ASN (Advance Shipping Notice)**，采用到货后导入清单或现场录入的方式。
* **WMS 核心功能**:
  1. **到货清单管理 (Arrival List Management)**:
     * **导入/录入**: 接收 SAP 采购订单 (PO) 数据，现场导入或录入到货明细 (GRN, Material, Qty)。若无 GRN，支持生成临时收货号。
     * **箱号补录**: 针对无法解析供应商箱号的场景，提供 "箱号补录" 功能，允许人工补充 `Material + Vendor + DC + Qty` 信息。
  2. **栈板条码 (Pallet Label)**:
     * 支持提前批量打印栈板条码 (ZPL)，张贴于空栈板。
  3. **多对多绑定 (M:N Binding)**:
     * **核心逻辑**: 完全支持 **混托 (Mixed Pallet)**。
     * 1 个栈板可绑定多个 GRN；1 个 GRN 可拆分到多个栈板。
     * **操作流程**: 扫描栈板号 -> 逐箱扫描 (解析或补录) -> 校验 `Sum(Qty) <= GRN.Remaining` -> 提交绑定。
  4. **码头分流决策 (Dock Diversion)**:
     * **决策逻辑**: 绑定完成后，根据物料属性自动分配流向：
       * **贵重料 (Precious)**: -> **贵重品仓** (人工直送，IQC 在仓内取样)。
       * **PCB/机构件**: -> **专属暂存区** (不与电子料混放)。
       * **普通电子料 (SMT/MSD/异形)**: -> **收货暂存区** (进入 IQC 流程)。
     * WMS 生成搬运任务并转发调度 RCS；WES 只通过 `TransportTask` 保存请求身份、状态证据和关联关系。

#### 3.2.2 IQC 动态路由与复判 (IQC Routing & Review)

* **流程概述**: 待检列表 (Wait List) -> WMS 转发 RCS 呼叫 -> IQC 取样 -> 结果录入 -> 路由/复判。
* **设计理念**: 引入 **待检列表 (Wait List)** 与 **QMS 动态抽检**，缓解 IQC 物理区域压力。
* **WMS 核心功能**:
  1. **待检列表与优先级 (Wait List & Priority)**:
     * 栈板进入暂存区后，自动加入 "IQC 待检列表" (PC Web)。
     * **排序规则**: 默认按到达时间 FIFO，IQC 主管可手动调整 GRN 优先级 (Priority High/Low)。
     * **容量控制**: WMS 监控 IQC 区域栈板数量，超过阈值时限制呼叫，避免拥堵。
  2. **智能抽检策略 (Smart Sampling)**:
     * **触发时机**: 栈板入库或进入待检列表时，请求 QMS。
     * **接口参数**: `(Material, Vendor, DC, Qty)` 或 `GRN`。
     * **免检逻辑**: 若 QMS 返回 `Sample_Qty = 0`，直接标记为 **免检 (Pass)**，无需进入 IQC 区域。
  3. **IQC 执行 (IQC Execution)**:
     * **WMS 转发 RCS 呼叫**: IQC 人员在 Web 端点击 "呼叫"，WMS 转发 RCS 将栈板搬运至 IQC 工作台。
       * *自动模式*: 优先推荐包含多个待检 GRN 的栈板。
       * *手动模式*: 允许指定呼叫特定栈板。
     * **取样操作**: 扫描栈板号 -> 显示待检 GRN -> 逐盘扫描 PKG (直至达到抽检量) -> 取走样品。
     * **检测完成**: 样品归还后，扫描栈板号 + PKG (确认归还) -> 提交检测完成。
  4. **结果判定与路由 (Decision & Routing)**:
     * WES/WMS 轮询或接收 QMS 结果，根据栈板上 **所有 GRN** 的状态决定去向：
     * **ANY NG (任一 GRN 不合格)**:
       * **标记**: 相关 GRN 标记 `需复判 (Review_Needed)`。
       * **锁定**: **所有** 包含该 GRN 的栈板 (含暂存区) 均被锁定，等待复判。
       * **路由**: 当前栈板 -> **IQC 复判区**。
     * **ALL OK (全部合格)**:
       * **标记**: 相关 GRN 标记 `通过 (Passed)`。
       * **路由**: 当前栈板 -> **栈板暂存区** (等待收货验收)。
  5. **复判与拆板 (Review & Split)**:
     * **复判 OK**: 更新 QMS 结果 -> 栈板移回 `栈板暂存区`。
     * **复判 NG**:
       * **拆板作业**: 必须将 NG 盘从原栈板拆解 (Split) 到新栈板 (或不良品车)。
       * **验证**: 确认原栈板仅剩 OK 物料 -> 移回 `栈板暂存区`。
       * **关联处理**: 系统提示并指导人员处理暂存区中其他包含该 NG GRN 的栈板。

---

### 3.2.3 收货验收与分流 (Receipt Acceptance & Diversion) (New)

本环节位于 **IQC 完成后** 与 **正式上架前**。目的是将“标准盘自动化物料”与“特殊物料”进行物理分离，确保自动化立库的高效运行。

* **流程概述**: `待收列表` -> WMS 转发 RCS 呼叫 -> 预拆板/分流判断 -> 执行分流。
* **WMS 核心功能**:
  1. **待收列表 (Pending Acceptance List)**:
     * 显示所有 **IQC Pass (含免检)** 且在暂存区的栈板。
     * 支持按 `栈板到达时间` 或 `优先级` 排序。
  2. **预分流检查 (Pre-Sort Check)**:
     * 栈板到达 `收货验收区` 后，PDA 自动提示该栈板包含的物料类型：
       * **标准类**: 7/13/15 寸标准料盘 (适合自动线)。
       * **特殊类**: MSD (湿敏)、非标准尺寸、异形料。
  3. **拆板分流 (Split & Divert)**:
     * **若混放**: 必须执行 **拆板作业**，将标准类与特殊类物料分置于不同栈板。
     * **分流执行**:
       * **流向 A (标准盘)**: 地牛/AGV -> **粗分机拆箱工作位** (进入 Section 3.3.1 智能装箱)。
       * **流向 B (特殊/MSD)**: AGV -> **SMT 手工注册/作业区** (人工开箱、绑定、入库 MSD 专区)。

---

### 3.3 内部物流与生产供料 (Internal Logistics)

本模块是 WES 最核心的 **智能调度 (Intelligent Scheduling)** 场景。

#### 3.3.0 WES 核心基础平台 (WES Core Foundation Platform)

为支撑后续章节中的 SMT 智能装箱 (3.3.1)、混合入库 (3.3.2) 和生产发料 (3.3.3)，WES 必须提供共享的设备命令可靠性基础能力。核心、厂商 Adapter 与 WorkLine 插件的所有权以
`docs/architecture/device-command-contract.md` 为准：核心只负责持久化、幂等、ACK/CALLBACK 证据和通用诊断；
具体厂商命令、Payload 与工作线业务判定分别由 Adapter 和插件拥有，不得互相替代测试。

**1. 控制系统集成 (Control System Integration)**

*   **架构约束**: WES 不直接控制底层 PLC、传感器或电机。所有物理设备的控制由硬件供应商提供的控制系统 (如装箱流水线控制系统、机构件流水线控制系统) 负责封装。
*   **通信协议**: 采用 HTTP/HTTPS 接口进行消息交互；具体认证、DTO 和 wire contract 以厂商原始文档及对应 Adapter 合同为准。
*   **逻辑位置映射**: WES 仅下发逻辑位置ID (如 `STATION_A`, `RACK_01`)，由控制系统负责解析为物理坐标，实现业务逻辑与物理参数的解耦。

**2. 异步消息交互 (Async Message Interaction)**

*   **交互模式**: 采用 **"下发(Command) -> 应答(Ack) -> 回调(Callback)"** 的三段式机制。
    *   **WES**: 在外部调用前持久化命令身份、目标设备、deadline 和关联对象；同步 ACK 只表示厂商控制系统已接纳请求。
    *   **控制系统**: 物理动作完成后按厂商合同返回最终结果；Adapter 将 ACK、结果和事件映射为核心可识别的证据。
*   **业务动作需求**: 系统需要支持抓取、放置、扫码、加工/贴标等业务动作；具体厂商命令名和 Payload 不固化为 WES 核心枚举，由对应 Adapter 显式映射。

**3. 逻辑端口要求 (Logical Port Requirements)**

*   **下行能力**: Adapter 根据实际厂商合同提供命令提交；核心不规定 Endpoint、方法名或 DTO。本版本不要求设备命令状态查询或取消能力。
*   **上行能力**: Adapter 接收厂商 ACK、最终结果和设备事件并映射为规范证据；厂商结果值不直接进入核心状态枚举。
*   **幂等性保障**: 核心为每条 `DeviceCommand` 保持稳定身份并阻止重复调度；Adapter 显式声明厂商侧幂等能力。命令可能已到达设备但结果未知时，不得仅凭相同命令身份自动重放物理动作。

**4. 重试与异常处理 (Retry & Exception Handling)**

*   **安全重提边界**: 只有逐 operation 厂商合同明确保证该调用可安全重复，且 `DeviceCommand` 等可靠对象显式
    `retryable=true` 时，可靠对象才可保留原身份重新提交；否则保持暂停并进入人工对账。Adapter 每次调用只执行一次发送，
    不拥有 retry/backoff 配置。
*   **未知结果处理**: 请求一旦可能被控制系统接收但未取得确定结果，命令进入 `TIMED_OUT`/远端结果未知状态，保留证据并暂停受影响对象，等待匹配的晚到 CALLBACK；无法闭合时进入人工对账，不自动重发。
*   **状态监控**: Adapter 根据厂商合同提供心跳或状态证据，核心据此维护通用 `DeviceRuntimeProjection`；不得要求所有厂商实现 WES 命名的健康检查接口。

**5. 设备层次结构与基础数据 (Device Hierarchy & Master Data)**

*   **设备组织层次 (Device Organization Hierarchy)**:
    *   建立 **区域 (Zone) → 作业线 (WorkLine) → 设备 (Device)** 的三级组织结构，用于任务路由和设备管理。
        *   **区域 (Zone)**: 物理区域划分 (如 `SMT作业区`, `机构件作业区`, `料盘装箱区`)。
        *   **作业线 (WorkLine)**: 区域内的生产线或工作站 (如 `SMT自动线1`, `SMT自动线2`, `SMT人工线`)。
        *   **设备 (Device)**: 作业线上的具体设备实例 (如 `工业电脑`, `PDA`, `机械臂`, `打印机`)。

*   **设备基础数据 (Device Master Data)**:
    *   WES 维护设备清单，记录每个设备的基础信息:
        *   **标识信息**: `device_id` (唯一标识), `device_name`, `type` (PDA/工业电脑/打印机/电脑/LCR测试仪)。
        *   **层次归属**: `zone_code`, `work_line_code` (设备所属的区域和作业线)。
        *   **用途说明**: 设备的功能描述 (如 "用来点货，绑定栈板发运送任务")。

*   **WorkLine 启动与分拣机设备边界**:
    *   `WORKLINE_START_REQUESTED` 只表示工作线进入 READY/待机状态，可以开始接收业务需求；不表示已有货架到位，也不表示立即开始分拣。
    *   分拣机只有 `SOURCE_ARM` 和 `TARGET_ARM` 两个机械臂，不存在 NG 专用机械臂；NG 放置动作由 `TARGET_ARM` 完成，目标设备角色仍是 `ROLE_SORTING_TARGET_ARM`。
    *   分拣作业启动必须同时满足业务需求、WorkLine READY、Station 业务 lease 空闲、单层货架 active 执行快照或 WMS 到位/授权回调；具体设备命令下发前再按设备角色执行实时准入。

**6. 执行对象与设备命令管理 (Execution and Device Command Management)**

*   **所有权分离 (Ownership Separation)**:
    *   WorkLine 插件决定业务执行对象的优先级、路由和下一步 Decision；核心不得用某条工作线的业务状态证明通用设备可靠性。
    *   核心负责 `DeviceCommand` 的持久化、设备准入、幂等、关联、deadline、ACK/CALLBACK 证据和通用诊断。
    *   Adapter 只负责厂商协议映射和合同校验，不解释业务优先级或推进业务对象。

*   **设备命令生命周期 (Device Command Lifecycle)**:
    *   核心通用生命周期为 `PENDING / DISPATCHED / ACKNOWLEDGED / SUCCEEDED / FAILED / TIMED_OUT`。
    *   `ACKNOWLEDGED` 只代表请求被厂商接纳；只有匹配当前命令的最终 CALLBACK 才能进入 `SUCCEEDED`/`FAILED`。
    *   业务执行对象的完成、失败、等待和取消语义由对应插件定义，不与 `DeviceCommand` 状态合并。
    *   所有状态变化保留时间、来源、关联和诊断证据，支持查询与审计。

*   **超时监控 (Timeout Monitoring)**:
    *   每条可靠执行对象使用明确 deadline；超时不等同于厂商执行失败。
    *   命令可能已经到达设备但结果未确认时进入 `TIMED_OUT`，只暂停与该命令关联的业务对象并触发诊断或对账。
    *   晚到结果必须幂等追加为证据；只有满足当前对象关联和安全准入条件时才能推进业务状态。

*   **并发控制 (Concurrency Control)**:
    *   核心根据设备运行投影和配置限制单设备可接纳的命令数，防止设备过载。
    *   未通过设备准入的 Decision 保持等待，不直接发送；重新调度前必须再次读取当前投影。
    *   业务对象的队列顺序由插件决定，设备空闲只触发重新判定，不代表核心自动选择具体业务任务。

#### 3.3.1 SMT 智能装箱协调 (Smart Kitting Coordination)

* **场景**: 从 **收货验收区 (流向 A)** -> 粗分机拆箱 -> 放入 **单层货架 (Single-Layer Rack)** (详见 3.1.2)。
* **货架规格**: 单层货架可承载 **4 个料箱**，用于中转缓存，适合高频装卸操作。
* **参与者**: WES (大脑), ECS (机械臂/视觉), WMS 转发的 RCS/AGV 搬运能力, 人工 (拆箱)。
* **WES 控制逻辑**:

  **Step 1: 空架补给 (Empty Rack Supply)**

  * **监控**: WES 基于单层货架 active 执行快照、WMS 授权结果和 typed status evidence 判断装箱区是否已有可执行空单层货架 (4 个料箱位)；空架资源主账和物理占用仍由 WMS/RCS 持有。
  * **决策**: 若无，WES 生成搬运需求从 "单层货架存放区" 补给空架并提交 WMS，由 WMS 转发 RCS 执行。
  * **ECS 握手**:
    * ECS 机械臂扫描空架所有箱号。
    * ECS Adapter 将厂商扫描事件转换为包含料箱身份列表的入站证据；厂商事件名和 DTO 由对应 Adapter 合同定义。
    * WES 基于 ECS 证据确认当前执行快照无误后，允许本次装箱作业启动；该确认不代表 WES 接管全局空架库存或物理库位权威。

  **Step 2: 视觉识别与分箱校验 (Vision & Binning Validation)**

  * **动作**: 料盘沿流水线输送 -> 到位触发视觉系统扫描。
  * **交互**:
    * ECS Adapter 将厂商扫描事件转换为包含 PKG、尺寸和厚度的入站证据；核心不识别厂商事件名。
  * **WES 处理逻辑**:
    1. **校验**: 验证 PKG 是否属于当前 GRN，校验 `Dims/Thickness` 偏差。
    2. **分配 (Binning Algorithm)**:
       * **同类合并**: 优先放入已存有相同 `Material + Vendor + DC` 的储位。
       * **料箱选择**: 料箱分为 **6 格箱** 与 **3 格箱** 两类；7 寸料盘优先选择 6 格箱，13/15 寸等大尺寸料盘只能选择 3 格箱的大尺寸格。
       * **深度计算**: 实时计算储位剩余深度，确保容量充足。
  * **逻辑动作**: WES 创建包含目标料箱、目标货格和预期堆叠高度的 `DeviceCommand`，由 ECS Adapter 映射为厂商命令和 Payload。
    * `SlotID` 在 SMT 粗分机场景必须拆分为两级位置:
      * **料架货位**: `rack_id + rack_slot_code(A/B/C/D) + rack_slot_location_code`
      * **料箱货格**: `bin_id + bin_cell_location`
    * 机械手执行放盘时以上位给出的“箱位 + 储位信息”为准。
  * **执行**: ECS 接收指令后，驱动机械臂从流水线抓取料盘并执行放入动作。

**Step 3: 异常与满架 (Exception & Full)**

* **装不进**: 若 ECS Adapter 返回“物理无法放入”的规范终态证据，WES 标记该 Slot 异常并由插件重新分配。
* **初次无货架**: SMT 粗分机开工或当前物料执行恢复时，如果粗分机工位没有可用单层货架，插件必须返回“等待货架”Decision，
  核心只暂停当前 `MaterialExecution`，并通过 `TransportTask` 向 WMS 提交新货架补给需求。
* **当前货架无可用料格**: 当 `active_bin_rack` 存在，但 4 个料箱中没有同 DC/LC 兼容格位，也没有满足料盘尺寸的空格时，
  当前 SMT 插件必须返回两项相互独立、可追踪的业务 Decision:
* 为旧货架保存释放快照并请求后续处置；核心据此创建对应外部义务，释放证据至少携带
  `rack_release_id`、`single_layer_rack_id`、`source_classifier_line_code`、`source_task_batch_id`、
  `release_reason_code=NO_COMPATIBLE_OR_EMPTY_CELL`、`bin_snapshots`。
* 为当前 `MaterialExecution` 请求新货架补给；核心据此创建 `TransportTask`，保存稳定 `dispatch_key`、
  `reason_code`、`pkg_id` 和恢复判定所需的工作线/位置关联。
* **新货架到位恢复**: 只有 WMS ACK/status/typed terminal result、对应 InboundEvidence 和 PositionProjection
  共同确认新货架已到达目标位置后，插件才能重新判定当前 `MaterialExecution`；旧货架处置完成不得替代新货架到位证据。
  只有出料机械臂成功把当前料盘放入料格后，当前物料执行才完成。

#### 3.3.2 混合入库策略 (Hybrid Inbound Strategy)

* **场景**: 单层货架 (装满料) -> SMT 存储区 (**五层货架 (Five-Layer Rack)**, 详见 3.1.2)。
* **货架规格**: 五层货架每层可承载 **4 个料箱**，总容量 **20 个料箱**，具有 **A/B 面** 区分，支持 CTU 双侧操作。
* **WES 决策逻辑**:

  1. **模式判断 (Mode Decision)**:

     * **满箱交换 (Full Exchange)**: 若料箱 `Usage >= 80%` 且 WMS 授权存在可用空箱资源 -> WES 生成 **满箱交换外部请求**。
     * **优先交换 (Priority Exchange)**: 若 `50% <= Usage < 80%`，WES 可向 WMS 请求优先交换；若 WMS 返回无资源或拒绝，则执行拣选或进入人工对账。
     * **零散入库 (Pipeline Picking)**: 若料箱 `Usage < 50%`，或 WMS 未授权空箱资源 -> WES 生成 **拣选任务**。
     * **混合模式**: 先交换，后拣选。
  2. **满箱交换执行 (Full Exchange Execution)**:

     * WMS 负责判断五层货架空箱资源、交换区空位、排队和 CTU/AGV 动作闭环；WES 不本地锁定 `Empty_Bin`。
     * WES 根据源单层货架和释放快照创建满箱交换 `TransportTask` 并提交给 WMS，由 WMS 转发 RCS/CTU 执行原子动作；具体 WMS operation 名称由北向合同定义。
     * **数据更新**: 交换完成后，库存属性、库存转移和账务确认由 WMS 完成；WES 只保存执行快照、权威 status、资源投影和回写证据。
     * **职责边界**: 旧货架处置只决定该货架的后续搬运或交换需求，不恢复 SMT 粗分机当前物料执行，也不替代新货架补充。
       当前 `MaterialExecution` 的恢复必须由其关联 `TransportTask` 的权威终态与位置投影共同驱动。
  3. **流水线零散入库 (Pipeline Picking Execution)**:

     * **调度**: WES 生成 Target Bin (从五层货架) 到流水线的搬运需求并提交 WMS，由 WMS 转发 RCS 执行。
     * **拣选动作**:
       * ECS 扫描流水线上的 Target Bin。
       * WES 创建包含来源货架、目标料箱和数量的逻辑 `DeviceCommand`，由 ECS Adapter 映射为厂商 Payload。
     * **执行**: ECS 机械臂执行抓取放入。

#### 3.3.3 SMT 生产发料协调 (SMT Production Issue)

* **场景**: SAP 工单 -> 自动/人工线发料。
* **WES 核心算法 (Rolling Wave)**:

  1. **任务接入**: 接收 SAP 工单，根据 "开线时间 + 6小时补料间隔" 生成波次。
     * **驱动原则**: 生产发料/出库必须由工单、波次或产线需求驱动，不由货架就绪事实自行选择业务。
  2. **业务拆解**:
     * **电子料 (Auto)**: 对应工作线插件生成自动拣选 Decision。
     * **特殊料 (Manual)**: 对应工作线插件生成 MSD/PCB/异形物料的人工处理或搬运 Decision。
  3. **自动线执行 (Auto Line)**:
     * **预缓存**: WES 提前调度下一波次的货架到 Buffer 区。
     * **逻辑动作**: WES 创建包含来源和目标的 `DeviceCommand`，厂商命令名和 Payload 由 ECS Adapter 决定。
     * **异常处理**: 若 ECS Adapter 返回拣选失败的规范终态证据，WES 记录硬件故障与诊断，并只暂停受影响对象；库存扣减、缺料确认和备选库存释放/补发必须由 WMS 确认或授权，WES 不自动扣减库存。

---

### 3.4 执行协同与调度策略 (Execution Coordination & Scheduling Strategy)

本模块定义 WES 的 **执行协调能力 (Execution Coordination Capability)**：与现有 WMS 协同、保存执行证据，并为 WorkLine 插件提供受控的查询和 Decision 执行边界。业务调度规则仍归对应插件所有。

#### 3.4.1 执行状态追踪与库存协同 (Execution State Tracking & Inventory Coordination)

* **架构定位**: WES 采用 **纯代理模式 (Pure Proxy Mode)**。WES **不维护库存主数据**，所有涉及库存的查询、预留、扣减操作均通过冻结 typed operation 访问现有 WMS。QUERY 不做跨请求缓存；单次 execution 只查询一次并复用同一 authority snapshot。
* **职责划分 (Responsibility Division)**:

  * **现有 WMS (Existing WMS)**:
    * **库存主数据 (Inventory Master)**: 唯一的库存真实源。
    * **决策中心**: 负责库存可用性判断、分配逻辑和账务更新。
  * **P9 WES (This System)**:
    * **执行协调**: 通过 WorkLine 插件的封闭 Decision 协调物料、料箱、设备命令和外部搬运。
    * **证据中继**: 将设备事实、物理结果和 WMS 回执保存为可关联证据，并向授权调用方提供查询。
    * **异常隔离**: WMS 接口超时或报错时只暂停依赖该结果的对象，保留未决义务并进入诊断或对账。
* **执行对象要求 (Execution Object Requirements)**:

  | 对象 | 需求职责 |
  | --- | --- |
  | `MaterialExecution` | 保存单个完整料盘或可执行物料单位的业务推进证据 |
  | `BinExecution` | 保存单个料箱在滚筒线和工作位中的推进证据 |
  | `DeviceCommand` | 保存 ECS 命令、ACK、CALLBACK、deadline 和幂等事实 |
  | `TransportTask` | 保存 WMS 转发搬运请求、批次状态和终态中的成员最终事实 |
  | `WmsConfirmation` | 保存需要可靠提交的 WMS 业务确认义务 |
  | `PositionProjection` | 提供位置、队列和占用的当前投影 |
  | `InboundEvidence` | 保存外部事件、输入和回调的原始证据 |

  当前投影可以更新，但命令、外部义务、终态和对账证据必须按审计要求保留；不得在执行结束时整体删除或压缩成一个通用任务记录。
* **与现有 WMS 的协同机制 (Coordination with Existing WMS)**:

  **1. 库存查询 (Inventory Query)**

  * **场景**: WES 需要决策时 (如: 分配发料任务)，查询现有 WMS 的库存。
  * **能力**: 使用 `wms.inventory.query_inventory@v1` 获取类型化 authority snapshot；具体 Method、Path、DTO
    和预算以 `docs/contracts/wms-northbound-interaction-contract.md` 为唯一 wire 真源。
  * **缓存策略**: 不做跨请求缓存；同一 execution 仅复用该次查询返回的 authority snapshot。

  **2. 库存预留 (Inventory Reservation)**

  * **场景**: WES 生成发料任务前，向现有 WMS 申请预留库存。
  * **能力**: 使用 `wms.inventory.reserve_inventory@v1` 申请预留，使用
    `wms.inventory.release_reservation@v1` 释放；SRS 不重复定义 Path 或 DTO。
  * **响应**: 现有 WMS 返回 `ReservationID`，并在 WMS 侧锁定库存；WES 只保存预留引用和执行证据。
  * **释放机制**:
    * **释放请求**: 任务完成或取消时，WES 向 WMS 提交预留释放需求并保存释放引用、回执和执行证据；预留锁定与最终释放事实由 WMS 持有。
    * **自动过期**: WMS 在 `expire_time` 后自动释放预留，无需 WES 干预。
    * **异常恢复**: WES 重启后，可向 WMS 查询关联预留状态并提交对账/释放需求；不得在本地判定预留已释放。

  **3. 库存确认 (Inventory Confirmation)**

  * **场景**: 物理动作完成后 (如: 装箱完成、发料完成)，WES 通知现有 WMS 更新库存。
  * **能力**: 入库、出库分别使用 `wms.inventory.confirm_inbound@v1` 和
    `wms.inventory.confirm_outbound@v1`；由 `WmsConfirmation` 保存可靠提交义务，SRS 不重复定义 wire DTO。
  * **幂等性保障**: WES 内部以闭集 operation identity 与 `dispatch_key` 唯一标识可靠义务；`dispatch_key` 不自动成为
    WMS wire 字段。Adapter 只发送逐 operation 获批的一个幂等/关联字段；未获 WMS 批准前不得声称远端原子去重。

  **4. 异常处理 (Exception Handling)**

  * **场景**: 物理动作失败 (如: AGV 故障、装箱失败)。
  * **流程**:
    1. WES 根据权威失败证据更新对应 `MaterialExecution`、`BinExecution`、`DeviceCommand` 或 `TransportTask`；结果未知时保持对象级暂停，不伪造失败终态。
    2. WES 向 WMS 提交预留释放或异常处置需求，等待 WMS 确认、拒绝或人工授权。
    3. WES 保存异常原因、时间、关联对象和外部回执，供告警与对账追踪。

#### 3.4.2 智能分配业务策略 (Intelligent Allocation Business Strategy)

* **实现边界**: 分配规则由对应 WorkLine 业务插件实现，并只读取经过校验、绑定到 `LineRunEpoch` 的版本化业务配置；核心不提供通用规则引擎或动态脚本运行时。
* **关键策略**:

  **1. 装箱分配策略 (Binning Strategy)**

  本策略仅用于当前单层货架 active 执行快照内的装箱执行计算和 ECS/机械臂指令生成。`rack_slot_code`、`bin_cell_location`、`Used_Depth`、`Remaining_Capacity` 等字段是执行投影或过程证据，不作为 WES 的库存可用性、储位归属、非单层资源授权或物理占用主账。

  * **料箱规格定义**:

    * **6 格箱**: 6 个 7 寸料盘可用格，每个格位仅可存放 7 寸料盘。
    * **3 格箱**: 2 个 7 寸料盘可用格 + 1 个大尺寸料盘可用格；大尺寸格用于 13/15 寸等大尺寸料盘。
    * **料格编号规则**:
      * 6 格箱: `料箱条码-1` ~ `料箱条码-6`，均为 7 寸料盘可用格。
      * 3 格箱: `料箱条码-1`、`料箱条码-2` 为 7 寸料盘可用格；`料箱条码-7` 为大尺寸料盘可用格。
      * 料箱外侧方位码使用 `料箱条码-A/B/C/D`；放入移动料架时 A 方位朝货架外侧、C 方位朝货位内侧。
    * **完整物理定位**:
      * `rack_slot_code`: 单层料架 4 个货位，取值 `A/B/C/D`。
      * `rack_slot_location_code`: 料架货位条码，例如 `NHW-1CLJ-0096-1C-1`。
      * `bin_cell_location`: 料箱货格条码，例如 `NHW000001-7`。
      * SMT 出料指令必须同时携带料架货位和料箱货格，不能只下发料箱内部货格。
    * **储位堆叠**: 每个储位可堆叠多个料盘，堆叠数量 = `储位可用深度 / 料盘厚度`。
  * **分配算法 (Allocation Algorithm)**:

    ```
    IF (料盘尺寸 == 7寸) THEN
      优先查找: 当前单层货架 active 执行快照中，已有 6 格箱或 3 格箱内存有相同 Material+Vendor+DC 的 7 寸格位
      IF (找到 AND 储位未满) THEN
        生成本次装箱执行目标储位
      ELSE
        选择空料箱: 优先 6 格箱 (`-1` ~ `-6`) > 3 格箱 (`-1`、`-2`)
        生成第一个空 7 寸格位作为本次装箱执行目标
      END IF

    ELSE IF (料盘尺寸 > 7寸) THEN
      优先查找: 当前单层货架 active 执行快照中，已有 3 格箱的大尺寸格存有相同 Material+Vendor+DC 的料盘
      IF (找到 AND 储位未满) THEN
        生成本次装箱执行目标储位
      ELSE
        选择空 3 格箱
        生成大尺寸格 (`-7`) 作为本次装箱执行目标
      END IF
    END IF
    ```
  * **容量计算 (Capacity Calculation)**:

    * **储位剩余容量投影** = `储位深度 - Sum(当前执行快照内已存料盘厚度)`
    * **本次执行可放入判断** = `剩余容量投影 >= 当前料盘厚度`
    * **实时更新**: 每次放入后，WES 更新当前执行快照中的 `Used_Depth` 和 `Remaining_Capacity` 过程证据；该更新不代表 WES 接管库存主账或非单层储位容量授权。
  * **防错机制 (Error Prevention)**:

    * **尺寸校验**: 7 寸料盘不得放入 3 格箱的大尺寸格；13/15 寸等大尺寸料盘不得放入 7 寸格位，也不得放入 6 格箱。
    * **混料禁止**: 同一储位内，`Material + Vendor + DC` 必须一致。

  **2. WMS 来源分配偏好 (WMS Source Allocation Preferences)**

  * **FEFO 偏好**: WES 可向 WMS 提交优先使用 DC 日期较早物料的分配建议或请求条件。
  * **退料优先偏好**: WES 可向 WMS 表达退料货架优先于正常库存的业务偏好。
  * **余料优先偏好**: WES 可向 WMS 表达优先使用低余量栈板或货架的碎片治理偏好。
  * **权威边界**: WMS 决定并返回具体来源库存、货架或储位；WES 只消费该授权结果并安排本线执行顺序，
    不在本地重新执行 FEFO、退料优先或余料优先分配。

  **3. 冷热区存储优化策略 (Hot/Cold Zone Optimization)**

  * **热区定义**: 近 7 天出库频次 > 10 次的物料 -> 向 WMS 提交靠近产线侧资源的策略建议或需求。
    * **五层货架**: A 面、B 面、冷热区和 CTU 路径由 WMS/RCS 判断，WES 只保存授权结果、typed status result 和对账证据。
  * **冷区定义**: 近 30 天无出库记录 -> 向 WMS 提交远端资源的策略建议或需求。
    * **五层货架**: WES 不以本地主账判断 A/B 面容量、空箱资源或真实占用。
  * **动态调整**: WES 可生成冷热度计算结果、策略建议或搬运需求并提交 WMS；是否执行、如何排队和降级由 WMS/RCS 决定。
  * **A/B 面负载平衡**: WES 可记录 WMS 返回的负载证据或对账结果，但不得本地触发五层货架资源授权。

#### 3.4.3 对象级执行协调 (Object-Level Execution Coordination)

* **职责**: 上游单据只作为业务输入和权威引用；具体 WorkLine 插件基于当前证据与投影产生封闭 Decision，核心分别推进 `MaterialExecution`、`BinExecution`、`DeviceCommand`、`TransportTask` 和 `WmsConfirmation`。
* **生命周期所有权**: 不建立统一 Task 状态机。每类执行对象只拥有自身状态、幂等键、终态证据和恢复规则，禁止用一个通用状态覆盖设备命令、搬运履约和 WMS 确认。
* **依赖表达**: 依赖通过明确的对象关联、权威终态证据和投影准入条件表达，不建设可配置 DAG、通用步骤表或跨工作线任务引擎。
* **并发边界**: 核心只执行对象级和设备级准入；业务优先级、节拍与队列顺序由插件决定，真实区域容量、AGV 拥堵、运输并发、路径规划和避让由 WMS/RCS 判断。
* **通信恢复**: 未发生 WES 进程重启时，未决可靠对象取得权威终态证据并重新通过对象级准入后，插件可以基于
  当前投影重新判定；不保存通用 Step checkpoint，也不从步骤号继续执行。
* **重启恢复**: WES 进程重启后只读取持久化证据用于诊断和人工清线，不在原 `LineRunEpoch` 重新判定或恢复
  物理编排；操作员完成清线并确认后创建新的 `LineRunEpoch`。

---

### 3.5 特殊物料处理流程 (Special Material Handling)

本模块处理 **高值物料 (High-Value)**、**MSD 物料 (Moisture Sensitive)**、**PCB 物料** 和 **机构件 (Mechanical Parts)** 的特殊流程。

> **交付范围：未来需求。** 本节保留产品需求，但不属于当前顶层 SPEC §3.1 和十一阶段架构收敛的实现或验收范围。
> 当前不得预建空插件、SFC Adapter 或通用特殊物料平台；进入实施前必须基于真实工作线与厂商合同修订顶层
> SPEC、总控计划，并为对应业务插件和 Adapter 单独批准实施计划。

#### 3.5.1 高值物料入库协调 (High-Value Material Inbound)

* **场景**: 码头 -> 高值区 -> IQC 取样 -> 入库。
* **WES 特殊处理**:
  1. **专属路由**: WES 识别高值物料权威属性后创建送往高值区的 `TransportTask`，不经过普通暂存区。
  2. **六合一绑定强制校验**:
     * WES 下发规则，WMS PDA 必须扫描 **六合一码 (PKG)** 才能完成入库，WMS 将结果同步给 WES。
     * 校验逻辑: `PKG.Material == GRN.Material && PKG.Vendor == GRN.Vendor`。
  3. **IQC 取样追溯**:
     * 保存包含栈板、PKG、检验员和取样时间的抽样追溯证据。
     * 归还时校验: `PKG -> 原 PalletID` 匹配。

#### 3.5.2 MSD 与非规则物料处理 (MSD & Irregular Material)

* **场景**: ... -> IQC -> **收货验收分流 (流向 B)** -> SMT 手工区绑定 -> MSD 存储区。
* **WES 特殊处理**:
  1. **MSD 标识**: WES 从 WMS 获取版本化 `Material.IsMSD` 事实并绑定到当前执行快照，在 3.2.3 环节由
     WMS PDA 识别并分流到 MSD 专区；WES 不成为物料属性主数据 owner。
  2. **湿度敏感期管理**:
     * WES 记录 `MSD_Open_Time` (开封时间)。
     * 计算 `Remaining_Exposure_Time = Floor_Life - (Now - MSD_Open_Time)`。
     * 若 `Remaining_Exposure_Time < 0` -> WES 标记 `Status = Expired`，禁止发料。
  3. **非规则物料**: 无法通过自动线装箱时，对应插件生成明确的人工处理 Decision 和审计证据。

#### 3.5.3 PCB 物料专线处理 (PCB Material Handling)

* **场景**: 码头 -> 收货暂存区 -> IQC -> PCB 存储区 -> 人工线发料。
* **WES 特殊处理**:
  1. **专属存储区**: WES 消费 WMS 授权的 `PCB_Storage_Zone` 与当前工作线位置配置，协调 PCB 与电子料的
     物理隔离；区域主数据、容量和授权仍由 WMS 持有。
  2. **防静电追溯**: 记录 `ESD_Bag_ID` (防静电袋编号)，确保全程可追溯。
  3. **人工线发料**: PCB 不走自动线，WES 创建送往人工线的 `TransportTask`。

#### 3.5.4 机构件物流协同 (Mechanical Parts Logistics)

* **场景**: 上盖/下盖栈板 -> 自动拆包线 -> Magazine 上料。
* **WES 协调职责**:

  **1. AB 栈板成对协同 (Paired Pallet Coordination)**

  * **配对逻辑**: WES 根据工单识别 `Top_Cover_Material` 和 `Bottom_Cover_Material`。
  * **同步调度**:
    * WES 为两个栈板创建具有同一业务关联的成对 `TransportTask` 并提交 WMS。
    * 确保两个栈板 **同时到达** 拆包区 (时间差 < 5 分钟)。
  * **缺料处理**: 若只有 A 无 B，WES 暂停对应执行对象并记录缺料诊断，等待 WMS 补齐或人工裁决。

  **2. 拆包与追溯 (Unpacking & Traceability)**

  * **围膜拆除**:
    * WES 创建送往拆膜区的 `TransportTask` 并提交 WMS。
    * 人工拆围膜后，WMS PDA 记录作业结果；WES 再创建送往拆包线的 `TransportTask`，由 WMS 转发 RCS 执行。
  * **箱级校验**:
    * ECS Adapter 将包含 PKG 与箱条码的扫描结果转换为入站证据，厂商事件名由 Adapter 合同定义。
    * WES 校验: `PKG.Material == WorkOrder.Material`。
  * **件级追溯**:
    * ECS 扫描机构件条码后，Adapter 将 PKG 与部件序列号关联转换为入站证据。
    * WES 保存包含工单、PKG、部件序列号和时间戳的追溯证据。
    * WES 推送追溯数据至 SFC (Shop Floor Control)。

  **3. Magazine 调度与产线上料 (Magazine Dispatch)**

  * **装满触发**: ECS Adapter 上报料架已满的规范证据后，WES 创建送往生产 Buffer 的 `TransportTask` 并提交 WMS。
  * **空 Magazine 回流**: RCS/WMS 自主调度空 Magazine 回自动线 (WES 不干预)。
  * **产线需求驱动**: WES 接收 SFC 的类型化料架需求后，创建 Buffer 到产线的搬运需求并提交 WMS。

  **4. 空栈板回收 (Empty Pallet Return)**

  * **触发条件**: ECS 扫描栈板，确认 `IsEmpty = True`。
  * **回收流程**:
    * ECS Adapter 将包含栈板身份的空栈板事实转换为入站证据。
    * WES 创建送往空栈板区的 `TransportTask` 并提交 WMS。
    * WES 记录空栈板回收执行投影与 WMS ACK/status/typed terminal result evidence；真实位置和占用以 WMS/RCS 为准。
  * **统一边界**: 转运、补给、空架回流和空栈板回收均表达为 WMS 搬运需求；AGV/CTU/RCS 任务由 WMS 转发并闭环。

---

### 3.6 生产退料闭环 (Production Return Loop)

本模块处理产线退料的 **质量闭环 (Quality Loop)**，确保退料经过清点、测试后重新入库。

> **交付范围：未来需求。** 本节不属于当前十一阶段计划的最终验收范围。LCR、X-Ray、贴标和退料工作线的真实
> 设备合同明确后，必须先修订顶层 SPEC 和总控计划，再由独立业务插件与厂商 Adapter 同包交付代码、fixture
> 和测试；核心平台不得用本节业务场景证明基础能力。

#### 3.6.1 退料接收与分类 (Return Receiving & Classification)

* **场景**: 产线下线 -> 料盘拆飞达 -> 放入标准胶框 -> 单层货架 -> SMT 退料作业区。
* **货架说明**: 使用 **退货货架 (Return Rack)** (详见 3.1.2) 存放退料料盘，货架式结构支持料盘级追踪，具有 A/B 面区分。
* **WES 处理流程**:
  1. **人工呼叫**: 仓库人员通过 WMS PDA 呼叫搬运，WES 创建送往退料区的 `TransportTask` 并提交 WMS，由 WMS 转发 RCS 执行。
  2. **物料分类**: WMS PDA 界面指导人工区分（分类结果同步给 WES）:
     * **电子料 (Electronic)**: 进入 X-Ray/LCR 流程。
     * **MSD 物料**: 标记 `RequiresDrying = True`。
     * **高值物料**: 标记 `IsHighValue = True`，专人处理。
     * **非规则物料**: 标记 `IsIrregular = True`，人工入库。

#### 3.6.2 LCR 测试决策引擎 (LCR Test Decision Engine)

* **WES 决策逻辑**:
  ```
  IF (Material.HasLCR_Config == False) THEN
    Skip_LCR_Test
  ELSE IF (Qty_Returned == Qty_Issued AND Usage == 0) THEN
    Skip_LCR_Test  // 满盘未使用
  ELSE
    Require_LCR_Test
  END IF
  ```
* **测试流程**:
  1. **PDA 扫描**: 作业员在 WMS PDA 扫描 PKG -> 调用 WMS 获取 `LCR_Required(True/False)` 并回显。
  2. **测试执行**: 若需测试，连接 LCR 测试仪；对应 Adapter 将厂商结果转换为通过/失败的规范检测证据。
  3. **结果处理**:
     * **Pass**: 放入流水线，进入 X-Ray 清点。
     * **Fail**: 放入不良品框，WES 标记 `Status = Defective`。

#### 3.6.3 X-Ray 智能清点与贴标 (X-Ray Counting & Relabeling)

* **WES 控制逻辑**:

  **Step 1: 清点决策 (Counting Decision)**

  ```
  IF (Qty_Returned == Qty_Issued AND Usage == 0) THEN
    Skip_XRay_Count  // 满盘
    New_PKG = Original_PKG
  ELSE
    Require_XRay_Count
  END IF
  ```

  **Step 2: X-Ray 清点执行 (X-Ray Execution)**

  * **流程**: ECS 扫描 PKG -> 流入 X-Ray 设备 -> 清点。
  * **数据流**: X-Ray Adapter 将原 PKG 与实际清点数量转换为规范检测证据，厂商消息名和 DTO 不进入核心合同。

  **Step 3: 新标签生成 (New Label Generation)**

  * **WES 算法**:
    ```
    New_PKG = Generate_PKG(
      Material: Original_PKG.Material,
      Vendor: Original_PKG.Vendor,
      Qty: Actual_Count,
      LC: Original_PKG.LC,  // 继承批次
      DC: Today,            // 更新日期
      NewSerial: Auto_Increment
    )
    ```
  * **贴标校验**:
    * WES 创建包含 ZPL 数据的逻辑打印 `DeviceCommand`，由打印 Adapter 映射为厂商请求。
    * ECS 打印并贴标后扫描新标签，Adapter 将新 PKG 转换为规范入站证据。
    * WES 校验: `New_PKG.Material == Original_PKG.Material`。
    * **贴标要求**: 新标签覆盖旧标签，但保留旧料号可见。

  **Step 4: 分类上架 (Classification & Putaway)**

  * **WES 路由规则**:
    * MSD 物料进入人工封包和干燥柜流程。
    * 其他物料以 X-Ray 清点证据和标签校验证据向 WMS 提交退料上架需求，等待 WMS 授权、拒绝或要求补空架。
  * **退料货架管理** (基于 3.1.2 定义的退货货架规格):
    * WES 维护退料执行投影或证据视图，例如 `Return_Rack_Execution_View(RackID, Side, SlotID, PKG, Qty, WMS_Confirmation)`；退料库存主账由 WMS 持有。
    * **料盘级执行证据**: 每个 WMS 授权储位可对应单个料盘，WES 只保存检测、贴标、人工确认，以及 WMS ACK/status/typed terminal result evidence；真实储位归属、库存可用性和 Side/Slot 授权以 WMS 为准。
    * **A 面装满**: WES 根据执行证据提示现场或向 WMS 请求转向货架；是否可转向、目标面和目标货架由 WMS 授权。
    * **全部装满**: WES 提交退料货架转运或补空架需求给 WMS；是否搬运、目标区域和空架补给由 WMS 授权并转发执行。
    * **A/B 面隔离**: 不同产线或工单的退料隔离策略由 WES 提交建议或需求，WMS 返回授权、拒绝或实际执行结果。

#### 3.6.4 退料入库与库存更新 (Return Putaway & Inventory Update)

* **WES 数据处理**:
  1. **库存更新**: WES 提交退料检测、贴标和执行证据，由 WMS 完成库存调整并回传确认；库存增加在 WMS 确认后生效。
  2. **追溯记录**: `Return_Log(Original_PKG, New_PKG, Actual_Count, Return_Date)`。
  3. **SAP 同步**: 由 WMS 负责向 SAP 推送退料数据（WES 不越级上报）。

---

### 3.7 异常处理与人工干预 (Exception & Manual Intervention)

为确保中台的 **高可用性 (High Availability)** 和 **业务连续性 (Business Continuity)**，WES 必须具备完善的异常处理机制。

#### 3.7.1 对象中断、通信恢复与重启清线 (Object Interruption, Communication Recovery and Restart Clearance)

* **场景**: 未发生 WES 进程重启的通信中断，以及进程重启、设备故障或紧急停机。
* **WES 机制**:
  1. **事实记录 (Evidence Persistence)**:
     * 每个关键边界分别保存 `MaterialExecution`/`BinExecution` 状态、位置投影、设备命令、外部义务和原始输入证据。
     * 示例: “已扫描 PKG、尚未形成箱位 Decision”由物料执行证据和位置投影表达，不创建通用 Task checkpoint。
  2. **通信恢复策略 (Communication Recovery Strategy)**:
     * **禁止推测重放**: WES 直连 ECS 命令和提交给 WMS 的搬运/交换需求均复用原可靠对象身份；结果未知时不得自动重复执行。
     * **状态校验**: 未发生 WES 进程重启时，查询 ECS 当前状态，并通过 WMS 查询 RCS/AGV/CTU 任务状态，
       确认物理世界与持久化证据一致。
     * **重新判定**: 未发生 WES 进程重启、证据闭合且对象级准入通过后，由对应 WorkLine 插件基于当前投影
       重新产生下一步 Decision，不使用通用 Task Engine 从步骤号恢复。
  3. **进程重启清线 (Process Restart Clearance)**:
     * WES 进程重启后停止工作线新对象接纳，保留全部证据，并把未明确终态的在途对象标记为需要现场清线。
     * 迟到 CALLBACK 继续保存，但不得在原 `LineRunEpoch` 自动恢复物理编排、重新下发命令或猜测现场位置。
     * 操作员完成清线并确认后创建新的 `LineRunEpoch`，新对象只能从该 epoch 开始执行。
  4. **人工确认机制**:
     * 通信恢复时若对象状态不明确（如 AGV 位置未知），创建带关联证据的人工对账事项；人工决议审计落库后
       才能重新判定。进程重启场景始终执行现场清线和新 epoch，不以状态查询替代清线。

#### 3.7.2 人工降级模式 (Manual Fallback Mode)

* **触发条件**: 自动线故障、ECS 离线、紧急情况。
* **WES 降级策略**:

  **1. 自动线 -> PDA 模式 (Auto to Manual)**

  * **切换流程**:
    * 管理员在 WES 界面点击 "切换人工模式"。
    * WES 只暂停目标 WorkLine 上尚未闭合的自动执行对象，保留设备命令和外部任务证据；其他工作线不得被连带暂停。
    * WES 只向 WMS 发布原执行对象关联、人工处置原因和工作线本地工位事实；WMS 创建人工作业并由 PDA 展示。
  * **PDA 指导**:
    * 人工指令中的来源物料、来源储位、目标料箱和目标料格由 WMS 授权并生成；WES 不选择或改写这些业务目标。
    * 人工扫描确认在 WMS PDA 完成，WMS 同步结果给 WES，逻辑与自动线完全一致。

  **2. 数据一致性保障 (Data Consistency)**

  * **关键原则**: 无论自动/人工，所有操作必须实时通过 WMS 接口校验。
  * **校验机制**: PDA 提交数据到 WMS，WMS 调用 WES（如需编排/策略校验）并最终由 WMS 返回允许/拒绝指令。WES 不做本地逻辑校验。

#### 3.7.3 通信异常、未知结果与恢复 (Communication Failure, Unknown Result and Recovery)

* **场景**: ECS 网络抖动、设备无响应，或 WMS 转发的 RCS/AGV/CTU 任务超时。
* **WES 处理要求**:

  **1. 按发送边界分类 (Classify by Send Boundary)**

  * **确认未发送**: 请求尚未离开 WES 时，只有逐 operation 外部合同明确批准安全重提且可靠执行对象显式
    `retryable=true`，对象才可保留原命令身份或内部 `dispatch_key`，映射到获批的唯一 wire 字段后重新提交；否则保持暂停
    并进入人工对账。Adapter 每次调用只执行一次发送，不拥有 retry/backoff 配置；不得创建新的业务请求身份。
  * **可能已发送**: 已开始外部调用但未取得确定 ACK/终态时，必须标记为远端结果未知。设备命令进入
    `TIMED_OUT`。`TransportTask` 保留原 `dispatch_key` 等待获批的 status query；`WmsConfirmation` 保留原
    `dispatch_key`，仅在逐 operation 合同明确批准安全重提且对象显式 `retryable=true` 时，才映射为唯一获批 wire 字段后
    重提；否则保持暂停并进入人工对账，不自动重放物理动作。
  * **已取得终态**: `DeviceCommand` 只接受通过合同校验且关联匹配的最终 CALLBACK；
    `TransportTask` 只接受 WMS status 返回的 typed terminal result。重复或晚到结果只幂等追加证据。

  **2. 对象级暂停与告警 (Object-Scoped Hold and Alert)**

  * 通信异常只暂停与未决命令、搬运任务或确认义务关联的 `MaterialExecution`/`BinExecution`，不得无依据暂停整条工作线。
  * 设备离线、故障或未知状态写入 `DeviceRuntimeProjection`，并保存来源、时间和关联命令；不得由一次业务失败直接推断设备永久离线。
  * 超过对应合同的 deadline 后触发可操作告警，告警必须指向未决对象、证据和对账入口。

  **3. 证据驱动恢复 (Evidence-Driven Recovery)**

  * 心跳或网络恢复只说明通信重新可用，不证明先前物理动作未执行、已完成或可安全重放。
  * ECS 命令必须等待匹配的晚到 CALLBACK 闭合命令终态；人工对账只处理无法闭合的现场事实，不伪造命令 CALLBACK。
    RCS/AGV/CTU 任务必须以 WMS status 和 typed terminal result 为权威证据。
  * 证据闭合后重新执行当前对象和设备的准入检查；存在冲突、结果未知或位置不一致时继续保持对象级暂停。

  **4. 上游 WMS 断连处理 (WMS Disconnection Handling)**

  * `timeout_seconds` 由 `WmsAdapterConfig` 配置；当前 breaker 参数由 Phase 3 计划固定为连续失败 3 次、OPEN 60 秒、
    HALF_OPEN 单 probe，不暴露配置。WMS Adapter 始终单次发送且不提供 retry/backoff 配置；未来真实合同只能在先修订
    合同、SPEC 和计划后改变可靠对象的安全重提资格，不得把重试所有权下放给 Adapter。
  * 暂停新的库存查询依赖判定、搬运/交换需求和 WMS 确认提交；已被 WMS 接纳的外部任务可以继续执行。WES 始终以
    主动 status query 取得 `TransportTask` 终态，关联 callback 只负责唤醒查询。
  * WMS 恢复后先按已批准合同查询所有未决 `TransportTask`；未决 `WmsConfirmation` 只有在逐 operation 合同明确批准
    安全重提且对象显式 `retryable=true` 时，才将内部原 `dispatch_key` 映射到唯一获批 wire 字段后重提。取得终态证据后
    逐对象恢复；条件不满足或结果仍未知时保持暂停，不得全局自动继续被暂停任务。

#### 3.7.4 优先级调整与急料插队 (Priority Adjustment & Urgent Material)

* **场景**: 产线急需某物料，需打断当前作业。
* **WES 功能**:
  1. **手动调整优先级**:
     * 管理员在 WES 界面选择任务 -> 点击 "提升优先级"。
     * 对应业务插件按优先级降序、创建时间升序重新判定其业务队列；核心不提供通用任务队列排序器。
  2. **急料插队 (Urgent Insert)**:
     * WES 暂停当前低优先级任务 (若可中断)。
     * 立即执行急料任务 -> 完成后恢复原任务。
  3. **冲突检测**:
     * 若急料与当前任务使用同一设备 -> WES 提示 "无法插队，设备占用中"。

#### 3.7.5 数据校验与防错机制 (Data Validation & Error Proofing)

* **WES 多层校验**:
  1. **输入校验 (Input Validation)**:
     * PDA 扫描 PKG (通过 WMS) -> WMS 调用 WES 校验: `PKG.Format == "6-in-1" && PKG.Material IN Master_Data`。
  2. **逻辑校验 (Logic Validation)**:
     * 装箱前 -> WES 校验: `Bin.Remaining_Space >= Material.Dims`。
  3. **物理校验 (Physical Validation)**:
     * ECS 反馈 "无法放入" -> WES 标记异常，重新分配。
  4. **追溯校验 (Traceability Validation)**:
     * 出库前 -> WES 校验: `PKG.WorkOrder == Current_WorkOrder`，防止错料上线。

---

### 3.8 与现有 WMS 的集成架构 (Integration with Existing WMS)

本章节定义 P9 WES 作为 **自动化增强层 (Automation Enhancement Layer)** 与现有 WMS 的集成模式。

#### 3.8.1 系统定位与职责划分 (System Positioning & Responsibility)

* **现有 WMS (Existing WMS - 主系统)**:

  * **库存主数据**: 唯一的库存真实源 (Single Source of Truth)，管理 P9 工厂所有库存。
  * **业务单据**: 收货单、出库单、调拨单、盘点单等业务凭证。
  * **账务管理**: 库存账务、成本核算、财务对接。
  * **全局视图**: 提供全工厂的库存查询和报表。
* **P9 WES (This System - 增强层)**:

  * **执行协调**: 协调自动化区域的 ECS 作业设备，并向 WMS 提交 RCS/AGV/CTU 搬运、交换、旋转需求。
  * **状态追踪**: 追踪物料在自动化设备中的实时位置和状态 (瞬态数据)。
  * **策略引擎**: 运行装箱策略、发料优先级、路由算法等智能决策。
  * **设备控制**: 直接调度 SMT 区、机构件区、退料区的 ECS/视觉/贴标/X-Ray/LCR 等作业设备；运输设备由 WMS 转发调度。
* **协同原则 (Coordination Principles)**:

  * **查询驱动 (Query-Driven)**: WES 需要库存数据时，实时查询现有 WMS。
  * **确认驱动 (Confirmation-Driven)**: WES 完成物理动作后，通知现有 WMS 更新库存。
  * **预留机制 (Reservation)**: WES 通过预留接口请求 WMS 锁定库存，避免超发；锁定事实和释放规则仍由 WMS 拥有。
  * **RCS 通道 (Phase Boundary)**: 本阶段 RCS/AGV/CTU 仍由现有 WMS 统一调度。WES 只提交搬运/交换/旋转需求给 WMS；E08–E14 统一以冻结 ACK、status query 和 typed terminal result 收敛，callback 仅可提示查询。
  * **PDA 通道**: PDA 仅与 WMS 交互；WES 如需感知 PDA 作业结果，由 WMS 推送/同步。

#### 3.8.2 集成接口规范 (Integration Interface Specification)

* **现有 WMS 需提供的接口 (APIs Required from Existing WMS)**:

  | 需求能力 | Operation identity | 需求边界 |
  | --- | --- | --- |
  | 库存查询 | `wms.inventory.query_inventory@v1` | 返回可追溯的库存 authority snapshot |
  | 库存预留/释放 | `wms.inventory.reserve_inventory@v1` / `wms.inventory.release_reservation@v1` | 锁定、释放和过期事实由 WMS 持有 |
  | 入库确认 | `wms.inventory.confirm_inbound@v1` | 接收已发生物理事实对应的可靠确认义务 |
  | 出库确认 | `wms.inventory.confirm_outbound@v1` | 接收已发生物理事实对应的可靠确认义务 |

  Method、Path、请求/响应 DTO、同步/异步 completion 和错误合同只在
  `docs/contracts/wms-northbound-interaction-contract.md` 定义；SRS 不维护第二份 wire 合同。
* **P9 WES 提供的接口能力 (API Capabilities Provided by P9 WES)**:

  **1. 执行状态查询 (Execution State Query)**

  * 按 `MaterialExecution`、`BinExecution`、`DeviceCommand`、`TransportTask` 或 WMS 业务关联键查询当前状态、位置、未决义务和诊断证据。
  * 查询合同必须使用明确版本的 DTO；不得把不同执行对象压缩为一个含糊的通用 `task_id/status` 结构。

  **2. 业务输入接口 (Business Input)**

  * WMS 通过已确认的类型化业务输入或事件合同触发对应 WorkLine 处理；WES 不提供可接受任意 `task_type` 的通用任务下发接口。
  * WorkLine 插件只能通过封闭 Decision 产生逻辑设备动作和业务参数；核心据此持久化 `DeviceCommand`，Adapter
    再映射为厂商命令和 wire Payload。上层调用方不得绕过核心可靠性边界或直接构造厂商设备命令。

#### 3.8.3 数据一致性保障 (Data Consistency Guarantee)

* **幂等性设计 (Idempotency)**:

  * WES 可靠对象内部基于 `operation_identity + dispatch_key` 唯一标识业务义务；同一义务恢复必须保留原
    `dispatch_key`，但该键不自动进入 wire。Adapter 只映射为逐 operation 获批的一个幂等/关联字段。
  * **内部幂等规则**:
    * **相同 operation identity、相同 dispatch_key、相同 Payload**: 复用同一义务及其 evidence，不创建第二个本地义务。
    * **相同 operation identity、相同 dispatch_key、不同 Payload**: 拒绝并记录本地幂等冲突证据。
    * **不同 dispatch_key**: 仅当它代表新的业务义务时才能创建新请求；不得用新键规避未知结果对账。
  * **远端幂等规则**: 只有 WMS 逐 operation 合同明确批准 wire 字段、回显、冲突和安全重提语义后才能实现；当前
    E08–E14 status method/path、状态闭集、`request_id`/`task_id` 关联及幂等承诺仍待批准，不得由内部规则推定。
  * 避免网络重试导致的重复入库/出库。
* **事务补偿 (Transaction Compensation)**:

  * 若 WES 物理动作失败 -> 向 WMS 提交预留释放或异常处置需求 -> 保存 WMS 回执并记录异常日志；预留最终释放事实由 WMS 持有。
  * 无论 WMS 确认请求可证明未发送还是可能已被接收，只有逐 operation 合同明确批准安全重提且 `WmsConfirmation`
    显式 `retryable=true` 时，才将内部原 `dispatch_key` 映射到唯一获批 wire 字段后重提。条件不满足或结果仍未知时进入
    人工对账，不查询不存在的确认状态端点。
* **对账机制 (Reconciliation)**:

  * 支持按计划和按需对比 WES 执行证据与 WMS 权威库存/任务结果，识别未决、冲突和位置差异。
  * 对账频率属于运维配置，不在产品需求中固化单一执行时间。
  * 发现差异后只暂停受影响对象并触发告警；人工决议必须留痕后才能恢复。

---

### 3.9 接口摘要 (Interface Summary)

(详细定义见章节 4)

* **SAP**: 收货单、工单、主数据（本阶段仅通过现有 WMS 中转，不直接对接）。
* **现有 WMS**: SAP 转发的单据、库存查询、库存预留、入库/出库确认——WES 的唯一业务/库存数据来源。
* **RCS/AGV/CTU**: 搬运 (Move)、交换 (Exchange)、旋转 (Rotate)，本版本仅通过现有 WMS 转发，不由 WES 直连。
* **ECS**: 校验 (Check)、指令 (Instruction)、结果 (Result)。
* **QMS**: 抽检请求、结果回传。

## 4. 接口需求 (Interface Requirements)

> **运行时资源边界 ADR（2026-05-13）**:
> SMT 运行时资源、满箱交换、WMS E08–E14 typed fulfillment 和库存权责边界以
> `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`、
> `docs/contracts/wms-northbound-interaction-contract.md` 与
> `docs/business/wms_rcs_interface_requirements.md` 为准。
> WES 不锁定五层空箱、不交换库存属性、不自动扣减库存；E08–E14 目标上由 ACK/status/typed terminal result 收敛。
> 可选 `WMS_EFFECT_STATUS_HINT` 只有在 inbound 合同完整批准 payload、关联、事件幂等、重试和冲突语义，且 Phase 4
> 唯一 hint successor 已验收后才可接入，并且只唤醒查询；否则 successor 为 `NONE`，Phase 5 只删除旧 route、payload、
> OpenAPI 和测试，不建立新 hint 路径。status method/path、状态闭集、关联和幂等同样仍须由 WMS 合同批准。WES 只保存
> 执行事实、资源投影、回写证据和对账证据。

### 4.1 北向接口 (Northbound API - To Existing WMS)

* **定位**: 标准化的业务接入层，接收上游 SAP 通过现有 WMS 转发的单据和主数据。本阶段 WES 不直接调用 SAP。
* **协议**: RESTful API (HTTPS).
* **核心接口**:
  * `POST /api/v1/orders/inbound`: 接收收货通知 (由现有 WMS 转发 SAP 单据)。
  * `POST /api/v1/orders/production`: 接收生产工单 (由现有 WMS 转发 SAP 单据)。
  * `POST /api/v1/master-data`: 接收物料主数据同步 (由现有 WMS 转发 SAP 主数据)。
  * WES 调用现有 WMS 的库存查询、预留/释放和入库/出库确认能力；operation identity 与 wire 合同以
    `docs/contracts/wms-northbound-interaction-contract.md` 为唯一真源。

### 4.2 南向接口 (Southbound API - To ECS)

* **定位**: 适配 WES 直连作业设备的插件层 (Adapter Layer)，包括 ECS、视觉、贴标、X-Ray、LCR、打印机等。
* **协议**: 当前产品只确认 HTTP/JSON 异步 ACK/CALLBACK；MQTT、OPC UA、WebSocket、Modbus 等未确认协议
  不进入本版本，真实新增需求必须先更新 SPEC，再由对应 Adapter 独立封装。
* **架构模式**: **插件化 (Plugin-Based)**。针对不同品牌的机械臂、视觉、检测、贴标等设备开发独立
  Adapter，将厂商命令、ACK、结果和事件映射到 `DeviceCommand` 逻辑端口与规范证据；WES 不定义跨厂商的
  通用动作枚举或标准化设备指令集。
* **阶段边界**: 本版本 WES 不提供直连 RCS/AGV/CTU 的 Driver Plugin。所有搬运、交换、旋转需求均通过 4.1 的 WMS 接口提交，由 WMS 转发给 RCS/AGV/CTU；E08–E14 只消费 status query 返回的 typed terminal result，E12/E13 只接受批次级结果，不接收 CTU 子阶段事件。

---

## 5. 非功能需求 (Non-functional Requirements)

1. **启动独立性 (Startup Independence)**: WES 启动不应阻塞于 WMS 连接失败（可启动进入待命状态），但**业务执行**强依赖 WMS。
2. **数据一致性 (Data Consistency)**: WES 不持有库存主账或库存变动主账；WES 可以持有执行事实、单层货架
   active 执行快照、运行投影、WMS 回调和对账证据。带权威设备结果/evidence 的物理动作一旦发生即形成不可回滚
   的物理终态；对应库存业务只有在 WMS 事务提交成功后才形成业务确认终态。WMS 暂时失败时，WES 保留物理事实
   和 `WmsConfirmation` 待确认义务，不得把已发生动作改写为未完成或自动重放未知结果。
3. **可观测性 (Observability)**: 提供独立的 Prometheus/Grafana 监控接口，重点监控 **WMS 接口延迟**、设备在线率及任务积压。
4. **故障恢复 (Failure Recovery)**: 不要求 WES 本地持久化库存主账缓存。系统重启后，WES 使用自身保存的
   执行事实、单层货架 active 执行快照、运行投影和回调证据进行诊断与现场清线；查询 WMS 和现场设备状态只用于
   对账和闭合证据，不恢复原执行上下文。操作员确认清线后创建新的 `LineRunEpoch`。
