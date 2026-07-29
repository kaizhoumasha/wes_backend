# 软件需求规格说明书 (Software Requirements Specification)

> **项目名称**: 休斯顿P9 智能仓储执行系统 (Houston P9 Intelligent Warehouse Execution System - WES)
> **系统定位**: 独立部署的集成化控制中台 (Independent Integration & Control Middleware)
> **文档版本**: 2.0 (Architecture Refactoring)
> **日期**: 2025-12-13

## 1. 引言 (Introduction)

### 1.1 目的 (Purpose)

本文档定义了 **休斯顿 P9 智能仓储执行系统 (WES)** 的架构与功能需求。

本项目不再定位为传统的 WMS，而是一个 **独立于现有企业级 WMS/SAP 的控制中台**。它旨在作为一个高可用、低延迟的智能化中间层，向上承接 ERP/WMS 的业务单据，向下协调自动化执行：本版本直接接入 ECS/视觉/贴标/X-Ray/LCR 等作业设备；AGV/CTU/RCS 类搬运、交换、旋转任务统一提交现有 WMS 转发执行，WES 不直连 RCS。

系统具备 **独立部署 (Standalone Deployment)**、**API 驱动 (API-Driven)** 和 **业务逻辑可配置 (Configurable Logic)** 的特性，确保在外部网络或上层系统波动时，现场自动化作业仍能维持核心运行。

### 1.2 产品范围 (Scope)

本系统的核心职责是 **执行 (Execution)** 与 **协调 (Coordination)**：

* **业务解耦**: 将自动化执行策略、设备作业逻辑从企业级 SAP/WMS 中剥离，由本中台统一协调；库存、货架资源与 RCS 调度权仍由现有 WMS 持有。
* **多设备协同**: 直接协调 ECS/WCS/视觉/贴标/X-Ray/LCR 等作业设备；AGV/CTU/RCS 任务由 WES 生成业务需求并提交 WMS，由 WMS 转发执行。
* **标准化接口**: 提供标准 RESTful API 供上层系统调用，提供标准 Protocol Adapter 对接 WES 直连设备；搬运/交换/旋转类任务通过 WMS 接口转发。
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
    * **策略引擎 (Strategy Engine)**: 运行波次策略、路由算法、箱位分配算法。
    * **任务编排 (Orchestration)**: 将 L1 的 "单据" 拆解为 L3 的 "原子指令"。
    * **状态机 (State Machine)**: 跟踪每个料箱、每个任务的实时状态。
* **L3 - 执行层 (External - Hardware)**:
  * **职责**: 物理动作执行 (RCS, ECS)。其中 ECS 类作业设备由 WES 直接接入；RCS/AGV/CTU 类运输与交换设备由 WMS 统一调度并向 WES 回传结果。

### 2.2 关键架构特性 (Key Architectural Characteristics)

为满足 "集成化控制中台" 的定位，系统必须具备以下特性：

1. **独立部署能力 (Independent Deployment)**:
   * 系统包含完整的前后端、数据库及中间件 (Dockerized)。
   * **网络依赖性**: 系统作业强依赖于上游 WMS 的在线状态。若与 SAP/Legacy WMS 断网，系统将执行以下策略:
     * **立即暂停**: 所有涉及库存变动的业务任务 (收货、发料、装箱等)
     * **外部续行**: 已由 WMS 接收并转发的纯物理搬运任务可由外部系统自行完成；WES 暂停新的搬运/交换需求提交，并等待回调或对账恢复。
     * **自动恢复**: 网络恢复后，自动继续执行被暂停的任务，无需人工干预
2. **API 驱动架构 (API-Driven)**:
   * **API First**: 所有功能（包括前端 UI）均通过 RESTful/gRPC API 访问。
   * **标准化数据接口**: 定义通用的 `Order_Ingest` (单据接入) 和 `Task_Dispatch` (任务下发) 接口，屏蔽不同 ERP 或不同硬件厂商的差异。
3. **可配置业务逻辑 (Configurable Business Logic)**:
   * 支持通过脚本 (Python/Lua) 或 规则引擎 (Rule Engine) 动态调整上架策略、抽检比例等，无需重新编译代码。
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
> - **RCS/AGV/CTU 调度**: 本版本仍由现有 WMS 统一调度。WES 只生成搬运、交换、旋转等业务需求并提交给 WMS，由 WMS 转发给 RCS/AGV/CTU 并将结果/事件回传 WES；WES 不直接调用 RCS，不直接下发 AGV/CTU 任务。
> - **PDA 交互**: PDA 仅对接 WMS 应用；若 WES 需要感知 PDA 结果/事件，由 WMS 推送/同步给 WES。
> - **自动化设备**: 所有自动化设备（ECS/视觉/贴标/X-Ray/LCR/打印机等）只通过 WES 接入，WMS 不直连设备。
> - **标签打印**: WES 生成打印模板/ZPL。若为自动打印设备，则由 WES 下发；若为人工/非自动打印，则 WMS 获取模板后完成打印并回执结果。

### 3.1 硬件清单与基础配置 (Hardware & Configuration)

WES 中台负责协调以下核心硬件资源，并维护其基础数据配置：

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

* **容器绑定**: 建立物理箱号 (Bin ID) 与 货架 (Rack ID) 及 地码 (Location Code) 的关联。
* **基础策略**: 维护供应商物料的尺寸/厚度主数据 (Master Data)，作为装箱算法的计算基准。

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
     * WMS 生成搬运任务 `Transport_Task` 并转发调度 RCS。

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

为支撑后续章节中复杂的 SMT 智能装箱 (3.3.1)、混合入库 (3.3.2) 及 生产发料 (3.3.3) 业务，WES 必须首先构建一个 **标准化的核心能力平台 (Core Capability Platform)**。根据 *@[docs/integration/third_party_integration_whitepaper.md]* 定义的接入标准，该平台需具备以下软件基础设施：

**1. 控制系统集成 (Control System Integration)**

*   **架构约束**: WES 不直接控制底层 PLC、传感器或电机。所有物理设备的控制由硬件供应商提供的控制系统 (如装箱流水线控制系统、机构件流水线控制系统) 负责封装。
*   **通信协议**: 采用 HTTP/HTTPS 接口进行消息交互 (详见白皮书 2.1 节)。
*   **逻辑位置映射**: WES 仅下发逻辑位置ID (如 `STATION_A`, `RACK_01`)，由控制系统负责解析为物理坐标，实现业务逻辑与物理参数的解耦。

**2. 异步消息交互 (Async Message Interaction)**

*   **交互模式**: 采用 **"下发(Command) -> 应答(Ack) -> 回调(Callback)"** 的三段式机制。
    *   **WES**: 发送指令 -> 收到 `200 OK` (代表已接收) -> 继续处理其他任务。
    *   **控制系统**: 执行动作 -> 调用 `Report_Result` 回调接口 -> WES 更新任务状态。
*   **标准指令**: 支持白皮书定义的标准动作类型：`PICK` (抓取), `PUT` (放置), `SCAN` (扫码识别), `PROCESS` (加工/贴标)。

**3. 标准接口定义 (Standard Interface Definition)**

*   **下行接口** (WES 调用控制系统):
    *   `Receive_Command` (接收任务)
    *   `Cancel_Command` (取消任务)
    *   `Health_Check` (状态查询)
*   **上行接口** (控制系统调用 WES):
    *   `Report_Result` (任务结果回传: OK/NG)
    *   `Event_Push` (设备事件上报: 急停/离线/到位)
*   **幂等性保障**: 所有指令携带全局唯一的 `command_id`，控制系统必须缓存已处理指令，防止重复执行物理动作。

**4. 重试与异常处理 (Retry & Exception Handling)**

*   **超时与重试**: 接口调用超时 (10s) 后，按指数退避策略 (`1s, 2s, 4s`) 自动重试，最多 3 次 (详见白皮书 4.2 节)。
*   **异常处理**: 超过重试次数后触发报警，需要人工介入处理。
*   **状态监控**: WES 定期轮询控制系统 `Health_Check` 接口，维护设备状态 (`IDLE` / `RUNNING` / `ERROR` / `OFFLINE`)。

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

**6. 任务管理 (Task Management)**

*   **任务队列 (Task Queue)**:
    *   支持基于优先级的任务队列 (白皮书定义 `priority`: 1-10, 10最高)。
    *   任务按优先级和提交时间排序，高优先级任务优先执行。
    *   支持任务排队、调度、执行的完整流程。

*   **任务状态 (Task State)**:
    *   定义任务状态机:
        *   `PENDING` (待执行): 任务已创建，等待调度。
        *   `RUNNING` (执行中): 任务已下发给控制系统，等待执行完成。
        *   `COMPLETED` (已完成): 控制系统回传 `result=SUCCESS`。
        *   `FAILED` (已失败): 控制系统回传 `result=FAILED` 或超过重试次数。
        *   `CANCELLED` (已取消): 通过 `Cancel_Command` 接口取消。
    *   支持任务状态查询和追踪，记录状态变更历史。

*   **超时监控 (Timeout Monitoring)**:
    *   基于白皮书定义的 `timeout` 参数监控任务执行时间。
    *   任务下发后启动计时器，超时后触发重试机制 (详见第4章)。
    *   超过重试次数后，任务状态变更为 `FAILED`，触发报警。

*   **并发控制 (Concurrency Control)**:
    *   限制单个设备的并发任务数，防止设备过载。
    *   设备状态为 `RUNNING` 且达到并发上限时，新任务自动进入队列等待。
    *   设备完成任务后，自动从队列中取出下一个任务执行。

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
    * `ECS -> WES: Verify_Empty(List<BinID>)`.
    * WES 基于 ECS 证据确认当前执行快照无误后，允许本次装箱作业启动；该确认不代表 WES 接管全局空架库存或物理库位权威。

  **Step 2: 视觉识别与分箱校验 (Vision & Binning Validation)**

  * **动作**: 料盘沿流水线输送 -> 到位触发视觉系统扫描。
  * **交互**:
    * `ECS -> WES: Material_Scanned(PKG_Code, Dims, Thickness)` (上报扫描结果)。
  * **WES 处理逻辑**:
    1. **校验**: 验证 PKG 是否属于当前 GRN，校验 `Dims/Thickness` 偏差。
    2. **分配 (Binning Algorithm)**:
       * **同类合并**: 优先放入已存有相同 `Material + Vendor + DC` 的储位。
       * **料箱选择**: 料箱分为 **6 格箱** 与 **3 格箱** 两类；7 寸料盘优先选择 6 格箱，13/15 寸等大尺寸料盘只能选择 3 格箱的大尺寸格。
       * **深度计算**: 实时计算储位剩余深度，确保容量充足。
  * **指令下发**: `WES -> ECS: Put_Instruction(BinID, SlotID, Expected_Stack_Height)`.
    * `SlotID` 在 SMT 粗分机场景必须拆分为两级位置:
      * **料架货位**: `rack_id + rack_slot_code(A/B/C/D) + rack_slot_location_code`
      * **料箱货格**: `bin_id + bin_cell_location`
    * 机械手执行放盘时以上位给出的“箱位 + 储位信息”为准。
  * **执行**: ECS 接收指令后，驱动机械臂从流水线抓取料盘并执行放入动作。

**Step 3: 异常与满架 (Exception & Full)**

* **装不进**: 若 ECS 反馈 `Put_Fail` (物理无法放入)，WES 标记该 Slot 异常，重新分配。
* **初次无货架**: SMT 粗分机开工或当前 session 恢复时，如果粗分机工位没有 `active_bin_rack`，
  当前 T5 dispatcher 尚未实现，运行时必须以领域错误阻断，不得创建旧 transport Outbox。
* **当前货架无可用料格**: 当 `active_bin_rack` 存在，但 4 个料箱中没有同 DC/LC 兼容格位，也没有满足料盘尺寸的空格时，
  SMT 插件同时执行两件事:
  * 向满箱交换插件发内部设备事件 `SINGLE_LAYER_RACK_RELEASED`，事件携带
    `rack_release_id`、`single_layer_rack_id`、`source_classifier_line_code`、`source_task_batch_id`、
    `release_reason_code=NO_COMPATIBLE_OR_EMPTY_CELL`、`bin_snapshots`。
  * 向 WMS 发起 `SMT_RACK_SUPPLY` 新货架补充请求，并在当前 SMT session 的 `rack_supply` 上下文中记录
    `dispatch_key`、`reason_code`、`pkg_id` 和恢复所需的设备信息。
* **新货架到位恢复**: T5 只能在后续任务中通过 35-operation registry、权威 EFFECT status 与资源投影实现；
  本阶段不接受旧货架到位终态回调。只有出料机械臂成功把当前料盘放入料格后，当前 SMT session 才完成。

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
     * WES 生成交换外部请求 `FULL_BIN_EXCHANGE(Source_Single_Layer_Rack, RackReleaseSnapshot)` 并提交给 WMS，由 WMS 转发 RCS/CTU 执行原子动作。
     * **数据更新**: 交换完成后，库存属性、库存转移和账务确认由 WMS 完成；WES 只保存执行快照、权威 status、资源投影和回写证据。
     * **职责边界**: 满箱交换插件只消费 `SINGLE_LAYER_RACK_RELEASED` 事件，判断旧货架是否需要满箱交换并生成旧货架处理请求；
       它不恢复 SMT 粗分机当前料盘 session，也不决定新货架补充。SMT 当前 session 的恢复必须由后续
       T5 typed operation 权威终态与资源投影共同驱动。
  3. **流水线零散入库 (Pipeline Picking Execution)**:

     * **调度**: WES 生成 Target Bin (从五层货架) 到流水线的搬运需求并提交 WMS，由 WMS 转发 RCS 执行。
     * **拣选指令**:
       * ECS 扫描流水线上的 Target Bin。
       * `WES -> ECS: Pick_List(From_Rack, To_Bin, Qty)`.
     * **执行**: ECS 机械臂执行抓取放入。

#### 3.3.3 SMT 生产发料协调 (SMT Production Issue)

* **场景**: SAP 工单 -> 自动/人工线发料。
* **WES 核心算法 (Rolling Wave)**:

  1. **任务接入**: 接收 SAP 工单，根据 "开线时间 + 6小时补料间隔" 生成波次。
     * **驱动原则**: 生产发料/出库必须由工单、波次或产线需求驱动，不由货架就绪事实自行选择业务。
  2. **任务拆解**:
     * **电子料 (Auto)**: 生成 `Auto_Pick_Task`.
     * **特殊料 (Manual)**: 生成 `Rack_Move_Task` (MSD/PCB/异形)。
  3. **自动线执行 (Auto Line)**:
     * **预缓存**: WES 提前调度下一波次的货架到 Buffer 区。
     * **指令下发**: `WES -> ECS: Pick_Command(Source, Target)`.
     * **异常处理**: 若 ECS 反馈 `Pick_Fail`，WES 记录设备失败、诊断和 RuntimeHold；库存扣减、缺料确认和备选库存释放/补发必须由 WMS 确认或授权，WES 不自动扣减库存。

---

### 3.4 执行协同与调度策略 (Execution Coordination & Scheduling Strategy)

本模块是 WES 中台的 **核心协调引擎 (Core Coordination Engine)**，负责与现有 WMS 协同，追踪执行状态，支撑所有调度决策。

#### 3.4.1 执行状态追踪与库存协同 (Execution State Tracking & Inventory Coordination)

* **架构定位**: WES 采用 **纯代理模式 (Pure Proxy Mode)**。WES **不维护库存主数据**，所有涉及库存的查询、预留、扣减操作均通过冻结 typed operation 访问现有 WMS。QUERY 不做跨请求缓存；单次 execution 只查询一次并复用同一 authority snapshot。
* **职责划分 (Responsibility Division)**:

  * **现有 WMS (Existing WMS)**:
    * **库存主数据 (Inventory Master)**: 唯一的库存真实源。
    * **决策中心**: 负责库存可用性判断、分配逻辑和账务更新。
  * **P9 WES (This System)**:
    * **执行管道 (Execution Pipeline)**: 专注于任务的物理执行（搬运、抓取）。
    * **状态中继**: 将设备的物理状态实时反馈给 WMS，由 WMS 决定下一步逻辑。
    * **异常熔断**: 一旦 WMS 接口超时或报错，WES 立即暂停相关作业。
* **执行状态数据结构 (Execution State Schema)**:

  ```
  Execution_State {
    TaskID: String,
    MaterialID: String,
    PKG_Code: String (六合一码),
    Qty: Integer,
    Current_Location: String (物理位置: AGV/流水线/货架),
    Status: Enum (InTransit, Processing, Completed),
    Source_Location: String (起点),
    Target_Location: String (终点),
    Start_Time: Timestamp,
    Expected_Completion: Timestamp
  }
  ```

  * **关键特征**: 这是 **瞬态数据 (Transient Data)**，任务完成后即清除，不长期保存。
* **与现有 WMS 的协同机制 (Coordination with Existing WMS)**:

  **1. 库存查询 (Inventory Query)**

  * **场景**: WES 需要决策时 (如: 分配发料任务)，查询现有 WMS 的库存。
  * **接口**: `GET /api/wms/inventory/query?material_code=R001&warehouse_code=SMT-A`
  * **缓存策略**: 不做跨请求缓存；同一 execution 仅复用该次查询返回的 authority snapshot。

  **2. 库存预留 (Inventory Reservation)**

  * **场景**: WES 生成发料任务前，向现有 WMS 申请预留库存。
  * **接口**: `POST /api/wms/inventory/reservations`
  * **请求**:
    ```json
    {
      "dispatch_key": "reserve-WO-12345",
      "material_code": "R001",
      "quantity": "100",
      "warehouse_code": "SMT-A"
    }
    ```
  * **响应**: 现有 WMS 返回 `ReservationID`，并在 WMS 侧锁定库存；WES 只保存预留引用和执行证据。
  * **释放机制**:
    * **释放请求**: 任务完成或取消时，WES 向 WMS 提交预留释放需求并保存释放引用、回执和执行证据；预留锁定与最终释放事实由 WMS 持有。
    * **自动过期**: WMS 在 `expire_time` 后自动释放预留，无需 WES 干预。
    * **异常恢复**: WES 重启后，可向 WMS 查询关联预留状态并提交对账/释放需求；不得在本地判定预留已释放。

  **3. 库存确认 (Inventory Confirmation)**

  * **场景**: 物理动作完成后 (如: 装箱完成、发料完成)，WES 通知现有 WMS 更新库存。
  * **入库确认**: `POST /api/wms/inventory/confirm-inbound`
    ```json
    {
      "dispatch_key": "inbound-GRN-001",
      "inbound_key": "GRN-001",
      "material_code": "R001",
      "quantity": "100",
      "pkg_id": "PKG-12345",
      "location_code": "SMT-A-01-03"
    }
    ```
  * **出库确认**: `POST /api/wms/inventory/confirm-outbound`
    ```json
    {
      "dispatch_key": "outbound-WO-12345",
      "outbound_key": "WO-12345",
      "material_code": "R001",
      "quantity": "100",
      "reservation_id": "RSV-001"
    }
    ```
  * **幂等性保障**: 所有 EFFECT 使用闭集 operation identity 与 `Idempotency-Key` 去重。

  **4. 异常处理 (Exception Handling)**

  * **场景**: 物理动作失败 (如: AGV 故障、装箱失败)。
  * **流程**:
    1. WES 标记任务失败: `Task_Status = Failed`。
    2. WES 向 WMS 提交预留释放或异常处置需求，等待 WMS 确认、拒绝或人工授权。
    3. WES 记录异常日志: `Exception_Log(TaskID, Reason, Timestamp)`。

#### 3.4.2 智能分配策略引擎 (Intelligent Allocation Engine)

* **核心算法**: 基于多维度规则的 **策略引擎 (Rule Engine)**，支持动态配置。
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

  **2. 发料优先级策略 (Issue Priority Strategy)**

  * **FEFO (First Expire First Out)**: 优先发 DC 日期最早的物料。
  * **退料优先**: 退料货架 > 正常库存。
  * **余料优先**: 栈板/货架容量 < 30% 的优先发出 (减少碎片化)。

  **3. 冷热区存储优化策略 (Hot/Cold Zone Optimization)**

  * **热区定义**: 近 7 天出库频次 > 10 次的物料 -> 向 WMS 提交靠近产线侧资源的策略建议或需求。
    * **五层货架**: A 面、B 面、冷热区和 CTU 路径由 WMS/RCS 判断，WES 只保存授权结果、typed status result 和对账证据。
  * **冷区定义**: 近 30 天无出库记录 -> 向 WMS 提交远端资源的策略建议或需求。
    * **五层货架**: WES 不以本地主账判断 A/B 面容量、空箱资源或真实占用。
  * **动态调整**: WES 可生成冷热度计算结果、策略建议或搬运需求并提交 WMS；是否执行、如何排队和降级由 WMS/RCS 决定。
  * **A/B 面负载平衡**: WES 可记录 WMS 返回的负载证据或对账结果，但不得本地触发五层货架资源授权。

#### 3.4.3 任务编排引擎 (Task Orchestration Engine)

* **职责**: 将上层 "单据" 拆解为下层 "原子任务"，并管理任务生命周期。
* **任务状态机 (Task State Machine)**:
  ```
  Pending (待执行) -> Dispatched (已下发) -> InProgress (执行中)
    -> Completed (完成) / Failed (失败) / Suspended (挂起)
  ```
* **核心功能**:
  1. **任务拆解 (Task Decomposition)**:
     * 示例: 1 个工单 -> 拆解为 50 个 `Pick_Task` + 10 个 `Transport_Task`。
  2. **依赖管理 (Dependency Management)**:
     * 示例: `Transport_Task(A)` 必须在 `Pick_Task(B)` 完成后才能执行。
  3. **并发控制 (Concurrency Control)**:
     * WES 可根据业务优先级、节拍和 WorkLine/Station 状态提交任务节流或派发节奏建议；真实区域容量、AGV 拥堵、运输并发、路径规划和避让由 WMS/RCS 判断。
  4. **断点续传 (Checkpoint & Resume)**:
     * 任务执行到 Step 3 时系统重启 -> WES 从 Step 3 继续执行，无需重头开始。

---

### 3.5 特殊物料处理流程 (Special Material Handling)

本模块处理 **高值物料 (High-Value)**、**MSD 物料 (Moisture Sensitive)**、**PCB 物料** 和 **机构件 (Mechanical Parts)** 的特殊流程。

#### 3.5.1 高值物料入库协调 (High-Value Material Inbound)

* **场景**: 码头 -> 高值区 -> IQC 取样 -> 入库。
* **WES 特殊处理**:
  1. **专属路由**: WES 识别物料属性 `IsHighValue = True` -> 生成 `Transport_Task(To HighValue_Area)`，不经过普通暂存区。
  2. **六合一绑定强制校验**:
     * WES 下发规则，WMS PDA 必须扫描 **六合一码 (PKG)** 才能完成入库，WMS 将结果同步给 WES。
     * 校验逻辑: `PKG.Material == GRN.Material && PKG.Vendor == GRN.Vendor`。
  3. **IQC 取样追溯**:
     * 记录 `Sample_Log(PalletID, PKG, IQC_Inspector, Sample_Time)`。
     * 归还时校验: `PKG -> 原 PalletID` 匹配。

#### 3.5.2 MSD 与非规则物料处理 (MSD & Irregular Material)

* **场景**: ... -> IQC -> **收货验收分流 (流向 B)** -> SMT 手工区绑定 -> MSD 存储区。
* **WES 特殊处理**:
  1. **MSD 标识**: WES 维护 `Material.IsMSD` 属性，在 3.2.3 环节被 PDA 识别并分流到 MSD 专区。
  2. **湿度敏感期管理**:
     * WES 记录 `MSD_Open_Time` (开封时间)。
     * 计算 `Remaining_Exposure_Time = Floor_Life - (Now - MSD_Open_Time)`。
     * 若 `Remaining_Exposure_Time < 0` -> WES 标记 `Status = Expired`，禁止发料。
  3. **非规则物料**: 无法通过自动线装箱 -> WES 生成 `Manual_Task`，指导人工处理。

#### 3.5.3 PCB 物料专线处理 (PCB Material Handling)

* **场景**: 码头 -> 收货暂存区 -> IQC -> PCB 存储区 -> 人工线发料。
* **WES 特殊处理**:
  1. **专属存储区**: WES 维护 `PCB_Storage_Zone`，与电子料物理隔离。
  2. **防静电追溯**: 记录 `ESD_Bag_ID` (防静电袋编号)，确保全程可追溯。
  3. **人工线发料**: PCB 不走自动线 -> WES 生成 `Rack_Move_Task(To Manual_Line)`。

#### 3.5.4 机构件物流协同 (Mechanical Parts Logistics)

* **场景**: 上盖/下盖栈板 -> 自动拆包线 -> Magazine 上料。
* **WES 协调职责**:

  **1. AB 栈板成对协同 (Paired Pallet Coordination)**

  * **配对逻辑**: WES 根据工单识别 `Top_Cover_Material` 和 `Bottom_Cover_Material`。
  * **同步调度**:
    * WES 生成成对搬运需求 `Transport_Task_Pair(Pallet_A, Pallet_B)` 并提交 WMS。
    * 确保两个栈板 **同时到达** 拆包区 (时间差 < 5 分钟)。
  * **缺料处理**: 若只有 A 无 B -> WES 挂起任务，触发 `Shortage_Alert`。

  **2. 拆包与追溯 (Unpacking & Traceability)**

  * **围膜拆除**:
    * WES 生成 `Transport_Task(To Unwrap_Zone)` 搬运需求并提交 WMS。
    * 人工拆围膜后，WMS PDA 呼叫（WMS 记录作业结果）-> WES 生成 `Transport_Task(To Unpack_Line)` 搬运需求，由 WMS 转发 RCS 执行。
  * **箱级校验**:
    * `ECS -> WES: Validate_Box(PKG, Box_Barcode)`。
    * WES 校验: `PKG.Material == WorkOrder.Material`。
  * **件级追溯**:
    * ECS 扫描机构件条码 (Part_SN) -> `ECS -> WES: Bind_Part(PKG, Part_SN)`。
    * WES 记录 `Traceability_Log(WorkOrder, PKG, Part_SN, Timestamp)`。
    * WES 推送追溯数据至 SFC (Shop Floor Control)。

  **3. Magazine 调度与产线上料 (Magazine Dispatch)**

  * **装满触发**: ECS 上报 `Magazine_Full` -> WES 生成 `Transport_Task(To Production_Buffer)` 搬运需求并提交 WMS。
  * **空 Magazine 回流**: RCS/WMS 自主调度空 Magazine 回自动线 (WES 不干预)。
  * **产线需求驱动**: WES 接收 SFC 的 `Magazine_Request` -> 生成 Buffer 到产线的搬运需求并提交 WMS。

  **4. 空栈板回收 (Empty Pallet Return)**

  * **触发条件**: ECS 扫描栈板，确认 `IsEmpty = True`。
  * **回收流程**:
    * `ECS -> WES: Pallet_Empty(PalletID)`。
    * WES 生成 `Transport_Task(To Empty_Pallet_Zone)` 搬运需求并提交 WMS。
    * WES 记录空栈板回收执行投影与 WMS ACK/status/typed terminal result evidence；真实位置和占用以 WMS/RCS 为准。
  * **统一边界**: 转运、补给、空架回流和空栈板回收均表达为 WMS 搬运需求；AGV/CTU/RCS 任务由 WMS 转发并闭环。

---

### 3.6 生产退料闭环 (Production Return Loop)

本模块处理产线退料的 **质量闭环 (Quality Loop)**，确保退料经过清点、测试后重新入库。

#### 3.6.1 退料接收与分类 (Return Receiving & Classification)

* **场景**: 产线下线 -> 料盘拆飞达 -> 放入标准胶框 -> 单层货架 -> SMT 退料作业区。
* **货架说明**: 使用 **退货货架 (Return Rack)** (详见 3.1.2) 存放退料料盘，货架式结构支持料盘级追踪，具有 A/B 面区分。
* **WES 处理流程**:
  1. **人工呼叫**: 仓库人员通过 WMS PDA 呼叫搬运 -> WES 生成 `Transport_Task(To Return_Area)` 搬运需求并提交 WMS，由 WMS 转发 RCS 执行。
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
  2. **测试执行**: 若需测试 -> 连接 LCR 测试仪 -> WES 接收测试结果 `LCR_Result(Pass/Fail)`。
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
  * **数据流**: `ECS -> WES: XRay_Result(Original_PKG, Actual_Count)`。

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
    * WES 生成 ZPL 数据 -> `WES -> ECS: Print_Label(ZPL_Data)`。
    * ECS 打印并贴标 -> 扫描新标签 -> `ECS -> WES: Verify_Label(New_PKG)`。
    * WES 校验: `New_PKG.Material == Original_PKG.Material`。
    * **贴标要求**: 新标签覆盖旧标签，但保留旧料号可见。

  **Step 4: 分类上架 (Classification & Putaway)**

  * **WES 路由算法**:
    ```
    IF (Material.IsMSD == True) THEN
      Route_To = Manual_Box  // 人工封包 -> 干燥柜
    ELSE
      Submit_Return_Putaway_Request_To_WMS(
        Evidence: XRay_Result + Label_Verification,
        Need: Return_Rack_or_Empty_Rack
      )
      Wait_WMS_Authorization(RackID, Side, SlotID)  // WMS 可授权、拒绝或要求补空架
    END IF
    ```
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

#### 3.7.1 任务中断与断点恢复 (Task Interruption & Checkpoint Resume)

* **场景**: 系统重启、设备故障、紧急停机。
* **WES 机制**:
  1. **断点记录 (Checkpoint Logging)**:
     * 每个任务执行到关键节点时，WES 记录 `Task_Checkpoint(TaskID, Step, State, Timestamp)`。
     * 示例: 装箱任务执行到 "已扫描 PKG，未分配箱号" -> 记录 `Step = 2, State = Pending_Allocation`。
  2. **恢复策略 (Resume Strategy)**:
     * **幂等性设计**: WES 直连的 ECS 指令、以及提交给 WMS 的搬运/交换需求均支持重复执行或幂等去重。
     * **状态校验**: 恢复前，WES 查询 ECS 当前状态，并通过 WMS 查询 RCS/AGV/CTU 任务状态，确认物理世界与数据一致。
     * **从断点继续**: `WES -> Task_Engine: Resume_From(TaskID, Last_Checkpoint)`。
  3. **人工确认机制**:
     * 若断点状态不明确 (如: AGV 位置未知) -> WES 触发 `Manual_Confirm_Required`，等待人工确认后继续。

#### 3.7.2 人工降级模式 (Manual Fallback Mode)

* **触发条件**: 自动线故障、ECS 离线、紧急情况。
* **WES 降级策略**:

  **1. 自动线 -> PDA 模式 (Auto to Manual)**

  * **切换流程**:
    * 管理员在 WES 界面点击 "切换人工模式"。
    * WES 挂起所有自动任务 -> 生成 `Manual_Task_List`。
  * **PDA 指导**:
    * WES 生成人工指令，由 WMS PDA 展示: "请从货架 A-01 取料盘 PKG-12345，放入料箱 B-03"。
    * 人工扫描确认在 WMS PDA 完成，WMS 同步结果给 WES，逻辑与自动线完全一致。

  **2. 数据一致性保障 (Data Consistency)**

  * **关键原则**: 无论自动/人工，所有操作必须实时通过 WMS 接口校验。
  * **校验机制**: PDA 提交数据到 WMS，WMS 调用 WES（如需编排/策略校验）并最终由 WMS 返回允许/拒绝指令。WES 不做本地逻辑校验。

#### 3.7.3 通信异常与重试机制 (Communication Exception & Retry)

* **场景**: ECS 网络抖动、设备无响应，或 WMS 转发的 RCS/AGV/CTU 任务超时。
* **WES 处理流程**:

  **1. 自动重试 (Auto Retry)**

  ```
  FOR i = 1 TO 3 DO
    Send_Command(ECS, Command)
    Wait_Response(Timeout = 10s)
    IF Response_Received THEN
      Break
    ELSE
      Log_Warning("Retry " + i + "/3")
    END IF
  END FOR
  ```

  **2. 失败处理 (Failure Handling)**

  * **3 次重试失败** -> WES 执行:
    1. 标记设备状态: `Device_Status = Offline`。
    2. 挂起相关任务: `Task_Status = Suspended`。
    3. 触发告警: `Alert(DeviceID, "Communication Lost", Severity = High)`。
    4. 通知运维: 发送邮件/短信/钉钉消息。

  **3. 设备恢复 (Device Recovery)**

  * ECS 恢复在线 -> WES 自动检测 (心跳机制)。
  * RCS/AGV/CTU 任务恢复或完成 -> WMS status query 返回任务状态与 typed terminal result，WES 根据校验后的结果或对账决议恢复挂起任务。
  * WES 执行 `Health_Check(DeviceID)` 或 WMS 任务状态查询 -> 若通过，自动恢复挂起任务。

  **4. 上游 WMS 断连处理 (WMS Disconnection Handling)**

  * **熔断机制**: 若连续 3 次调用 WMS 接口超时或返回 5xx 错误，触发断网保护。
  * **暂停策略**:
    * **立即暂停**: 所有涉及库存变动的业务任务 (收货、发料、装箱、入库确认、出库确认等)
    * **外部续行**: 已由 WMS 接收并转发的纯物理搬运任务可由外部系统自行完成；WES 暂停新的搬运/交换需求提交，并等待回调或对账恢复。
    * **告警通知**: 触发 `System_Pause` 告警，通知 IT 介入
  * **自动恢复**: WMS 接口恢复正常后，自动继续执行被暂停的任务，无需人工干预。

#### 3.7.4 优先级调整与急料插队 (Priority Adjustment & Urgent Material)

* **场景**: 产线急需某物料，需打断当前作业。
* **WES 功能**:
  1. **手动调整优先级**:
     * 管理员在 WES 界面选择任务 -> 点击 "提升优先级"。
     * WES 重新排序任务队列: `Task_Queue.Sort_By(Priority DESC, Create_Time ASC)`。
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
  * **RCS 通道 (Phase Boundary)**: 本阶段 RCS/AGV/CTU 仍由现有 WMS 统一调度。WES 只提交搬运/交换/旋转需求给 WMS，由 WMS 转发给 RCS/AGV/CTU 并回传结果/事件。
  * **PDA 通道**: PDA 仅与 WMS 交互；WES 如需感知 PDA 作业结果，由 WMS 推送/同步。

#### 3.8.2 集成接口规范 (Integration Interface Specification)

* **现有 WMS 需提供的接口 (APIs Required from Existing WMS)**:

  **1. 库存查询接口 (Inventory Query)**

  ```
  GET /api/wms/inventory/query
  Query Params: material_id, location, zone
  Response: { material_id, location, qty_available, qty_reserved, ... }
  ```

  **2. 库存预留接口 (Inventory Reservation)**

  ```
  POST /api/wms/inventory/reservations
  Body: { dispatch_key, material_code, quantity, warehouse_code, owner_code?, lot_no? }
  Response: { dispatch_key, material_code, reservation_id, reserved_quantity, expires_at }

  POST /api/wms/inventory/reservations/release
  Body: { dispatch_key, reservation_id, release_reason }
  Response: { dispatch_key, reservation_id, release_reference, reservation_status }
  ```

  **3. 入库确认接口 (Putaway Confirmation)**

  ```
  POST /api/wms/inventory/confirm-inbound
  Body: { dispatch_key, inbound_key, material_code, quantity, pkg_id, location_code }
  Response: { dispatch_key, inbound_key, wms_document_no, inventory_source_version }
  ```

  **4. 出库确认接口 (Issue Confirmation)**

  ```
  POST /api/wms/inventory/confirm-outbound
  Body: { dispatch_key, outbound_key, material_code, quantity, pkg_id?, reservation_id? }
  Response: { dispatch_key, outbound_key, issue_reference, inventory_source_version }
  ```
* **P9 WES 提供的接口 (APIs Provided by P9 WES)**:

  **1. 执行状态查询 (Execution State Query)**

  ```
  GET /api/wes/execution/status
  Query Params: task_id, material_id, work_order
  Response: { task_id, status, current_location, progress, ... }
  ```

  **2. 任务下发接口 (Task Dispatch)**

  ```
  POST /api/wes/tasks/dispatch
  Body: { task_type, material_id, qty, source, target, priority }
  Response: { task_id, status }
  ```

#### 3.8.3 数据一致性保障 (Data Consistency Guarantee)

* **幂等性设计 (Idempotency)**:

  * 所有确认接口 (putaway/issue) 基于 `TaskID` 去重，支持重复调用。
  * **幂等性规则**:
    * **相同 TaskID + 相同数据**: 返回成功，不重复执行，返回首次执行结果。
    * **相同 TaskID + 不同数据**: 返回错误 (409 Conflict)，拒绝执行，提示数据不一致。
    * **不同 TaskID**: 正常执行新任务。
  * 避免网络重试导致的重复入库/出库。
* **事务补偿 (Transaction Compensation)**:

  * 若 WES 物理动作失败 -> 向 WMS 提交预留释放或异常处置需求 -> 保存 WMS 回执并记录异常日志；预留最终释放事实由 WMS 持有。
  * 若 WMS 确认接口失败 -> WES 重试 3 次 -> 失败后触发人工介入。
* **对账机制 (Reconciliation)**:

  * 每日凌晨 2:00，WES 与现有 WMS 进行库存对账。
  * 对比 WES 的执行记录与 WMS 的库存变动，识别差异。
  * 差异超过阈值 -> 触发告警，人工核查。

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
> `docs/architecture/target-state-contract.md` 与 `docs/business/wms_rcs_interface_requirements.md` 为准。
> WES 不锁定五层空箱、不交换库存属性、不自动扣减库存；E08–E14 由 ACK/status/typed terminal result
> 收敛，可选 `WMS_EFFECT_STATUS_HINT` 只唤醒查询。WES 只保存执行事实、资源投影、回写证据和对账证据。

### 4.1 北向接口 (Northbound API - To Existing WMS)

* **定位**: 标准化的业务接入层，接收上游 SAP 通过现有 WMS 转发的单据和主数据。本阶段 WES 不直接调用 SAP。
* **协议**: RESTful API (HTTPS).
* **核心接口**:
  * `POST /api/v1/orders/inbound`: 接收收货通知 (由现有 WMS 转发 SAP 单据)。
  * `POST /api/v1/orders/production`: 接收生产工单 (由现有 WMS 转发 SAP 单据)。
  * `POST /api/v1/master-data`: 接收物料主数据同步 (由现有 WMS 转发 SAP 主数据)。
  * `GET /api/v1/wms/inventory/query`: 调用现有 WMS 的库存查询接口。
  * `POST /api/v1/wms/inventory/reserve`: 调用现有 WMS 的库存预留接口。
  * `POST /api/v1/wms/inventory/confirm`: 向现有 WMS 推送入库/出库确认。

### 4.2 南向接口 (Southbound API - To ECS)

* **定位**: 适配 WES 直连作业设备的插件层 (Adapter Layer)，包括 ECS、视觉、贴标、X-Ray、LCR、打印机等。
* **协议**: TCP/Modbus/HTTP/MQTT (视硬件而定)。
* **架构模式**: **插件化 (Plugin-Based)**。针对不同品牌的机械臂、视觉、检测、贴标等设备，开发独立的 Driver Plugin，转换为 WES 内部的标准化指令。
* **阶段边界**: 本版本 WES 不提供直连 RCS/AGV/CTU 的 Driver Plugin。所有搬运、交换、旋转需求均通过 4.1 的 WMS 接口提交，由 WMS 转发给 RCS/AGV/CTU，并由 WMS 将执行结果/事件回传 WES。

---

## 5. 非功能需求 (Non-functional Requirements)

1. **启动独立性 (Startup Independence)**: WES 启动不应阻塞于 WMS 连接失败（可启动进入待命状态），但**业务执行**强依赖 WMS。
2. **数据一致性 (Data Consistency)**: 采用 **强一致性 (Strong Consistency)** 模型。WES 不持有库存主账或库存变动主账；WES 可以持有执行事实、单层货架 active 执行快照、运行投影、WMS 回调和对账证据。所有库存变动必须在 WMS 端事务提交成功后，物理动作方可视为完成。
3. **可观测性 (Observability)**: 提供独立的 Prometheus/Grafana 监控接口，重点监控 **WMS 接口延迟**、设备在线率及任务积压。
4. **故障恢复 (Failure Recovery)**: 不要求 WES 本地持久化库存主账缓存。系统重启后，WES 可使用自身保存的执行事实、单层货架 active 执行快照、运行投影和回调证据恢复执行上下文，并通过查询 WMS 和现场设备状态完成对账。
