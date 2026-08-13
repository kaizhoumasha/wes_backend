# 软件需求规格说明书 (Software Requirements Specification)

> **项目名称**: 休斯顿P9 智能仓储执行系统 (Houston P9 Intelligent Warehouse Execution System - WES)
> **系统定位**: 独立部署的集成化控制中台 (Independent Integration & Control Middleware)
> **文档版本**: 3.0 (Architecture Convergence)
> **日期**: 2026-08-14
> **状态**: Current Requirements Baseline
>
> **文档层级**: 本文是产品范围、参与方职责和功能/非功能需求真源；
> `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
> 负责把这些需求收敛为当前目标架构；
> `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md` 只负责实施顺序。
> `docs/integration/third_party_integration_whitepaper.md` 是所有固定式设备供应商长期遵循的顶层统一接口（wire）真源。
> `docs/contracts/wms-inbound-putaway-integration-requirements.md` 是粗分逐盘入库、满箱交换和自动上架的业务合同评审真源；
> 当前状态为 `ReviewRequired`，不构成代码实施授权。
> SRS 不规定旧 Runtime、旧插件框架或兼容迁移路径；出现实现机制冲突时，以当前顶层 SPEC 为准，并同步修订本文需求表述。

## 1. 引言 (Introduction)

### 1.1 目的 (Purpose)

本文档定义 **休斯顿 P9 智能仓储执行系统 (WES)** 的产品范围、参与方职责以及功能与非功能需求。
架构机制、内部对象和收敛路径由当前顶层 SPEC 定义，实施计划不得反向修改本文的业务需求。

本项目不再定位为传统的 WMS，而是一个 **独立于现有企业级 WMS/SAP 的控制中台**。它旨在作为一个高可用、低延迟的智能化中间层，向上承接 ERP/WMS 的业务单据，向下协调自动化执行：当前交付只直接接入顶层 SPEC §3.1 已确认工作线所需的 ECS/WCS/视觉类作业设备；AGV/CTU/RCS 类搬运、交换、旋转任务统一提交现有 WMS 转发执行，WES 不直连 RCS。贴标、X-Ray、LCR 及机构件/SFC 协同属于本文保留的未来产品需求，不进入当前十二阶段实现与验收。

系统具备 **独立部署 (Standalone Deployment)**、**API 驱动 (API-Driven)** 和 **执行插件显式扩展 (Explicit Plugin Extension)** 的特性。外部网络或上层系统波动时，WES 必须保留已接收事实、限制影响范围并进入可诊断的等待或对账状态，不得推测外部结果后自动续行。

### 1.2 产品范围 (Scope)

本系统的核心职责是 **执行 (Execution)** 与 **协调 (Coordination)**：

* **业务解耦**: 将自动化执行策略、设备作业逻辑从企业级 SAP/WMS 中剥离，由本中台统一协调；库存、货架资源与 RCS 调度权仍由现有 WMS 持有。
* **多设备协同**: 当前直接协调已确认工作线所需的 ECS/WCS/视觉类作业设备；AGV/CTU/RCS 任务由 WES 生成业务需求并提交 WMS，由 WMS 转发执行。贴标、X-Ray、LCR 等设备仅保留需求边界，待对应工作线与厂商合同获批后交付。
* **边界化接口**: 提供版本化 RESTful API 供上层系统调用；所有固定式设备供应商适配 WES 统一接口（wire），搬运/交换/
  旋转类任务通过 WMS 接口转发。
* **核心域**:
  * **入库执行**: 码头收货、IQC 路由、上架策略执行。
  * **库存代理**: 采用 **按需动态查询 (On-Demand Query)** 模式，实时调用 WMS 接口获取库存数据，不维护本地库存副本。
  * **出库协同**: 接收 WMS 下发的 `PickingTask` 排队信息；WMS 接纳准备请求后按连续版本分批回调已分配并锁定的直接取料
    来源和候选 Bin，WES 接纳首批完整成员后即可执行。Cell 在实际 Bin 到达 SCAN2 后创建，逐盘身份在设备扫码后晚绑定。
    WES 只协调作业期执行，不计算波次或选择库存来源。

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
  * **交互**: 仅通过标准 API 向本中台提供已授权业务输入；自动出库下发执行级 `PickingTask`，不下发工单供 WES 生成波次。
* **L2 - 控制中台 (This System - P9 WES)**:
  * **职责**:
    * **独立数据库 (Independent DB)**: 拥有私有的 PostgreSQL/Redis 实例，不依赖 L1 数据库。
    * **执行插件 (Execution Plugin)**: 每条 WorkLine 通过显式注册的代码插件，把 WMS 封闭业务结果映射为本线设备等待、
      发送、暂停、隔离和对账动作；插件不拥有业务规则。
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
     * **证据驱动恢复**: 网络恢复后继续可靠接收未决 `TransportTask` 的异步终态；已超过结果截止时间或提交结果未知的任务
       保留原身份进入人工对账。未决 `WmsConfirmation` 保留内部原
       `dispatch_key`，仅当逐 operation 合同明确批准安全重提且对象显式 `retryable=true` 时，才映射为该 operation 唯一获批
       的 wire 幂等/关联字段后重提。取得终态证据后重新执行对象级准入；条件不满足、结果未知或证据冲突时保持暂停并
       进入人工对账。
2. **API 驱动架构 (API-Driven)**:
   * **API First**: 所有功能（包括前端 UI）均通过明确版本的 RESTful API 访问。
   * **边界合同**: 上层业务接口由 WMS 合同定义；所有设备供应商必须适配 WES 第三方设备统一接口（wire）。具体设备
     `task_type`、`event_type` 和 Payload 由获批设备合同附录拥有，不提升为核心全局枚举。
3. **显式执行扩展 (Explicit Execution Extension)**:
   * 不同工作线通过独立代码插件实现 WMS 业务结果到设备执行动作的映射并静态注册；不提供本地业务规则引擎、
     Python/Lua 动态脚本、声明式工作流 DSL、低代码规则引擎或运行时插件发现。
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
> - **RCS/AGV/CTU 调度**: 本版本仍由现有 WMS 统一调度。WES 只生成搬运、交换、旋转等业务需求并提交给 WMS，由 WMS 转发给 RCS/AGV/CTU；这类交互由 Phase 4 `Transport Port` 及其 WMS 转发适配器承接，Phase 3 Client 只提供 HTTP 访问。WES 不直接调用 RCS，不直接下发 AGV/CTU 任务。
> - **PDA 交互**: PDA 仅对接 WMS 应用；若 WES 需要感知 PDA 结果/事件，由 WMS 推送/同步给 WES。
> - **自动化设备**: 当前只交付顶层 SPEC §3.1 已确认工作线所需的 ECS/WCS/视觉类直连接入。贴标、X-Ray、LCR 和自动打印仍是未来需求；对应工作线获批后通过 WES 第三方设备统一接口及获批设备合同附录接入，WMS 不直连设备。
> - **标签打印**: 当前只保留合同边界。进入交付批次后，WES 生成打印模板/ZPL；自动打印设备由 WES 下发，人工/非自动打印由 WMS 获取模板并回执结果。

### 3.1 硬件清单与基础配置 (Hardware & Configuration)

下表是整体产品需求涉及的硬件清单，不等于当前交付清单。当前只实现顶层 SPEC §3.1 与十二阶段总控明确批准的工作线；
其余硬件只有在对应工作线获批后，才通过 WES 第三方设备统一接口、获批设备合同附录、明确的 endpoint/device 绑定和
供应商一致性验收接入；仅在需要业务执行映射时交付工作线插件：

| 区域 (Area)        | 关键硬件 (Hardware)      | WES 协调职责 (Coordination Role)                                               |
| :----------------- | :----------------------- | :----------------------------------------------------------------------------- |
| **码头区**   | PDA, 打印机              | 提供收货单据同步，生成栈板条码 (ZPL)，处理绑定数据。                           |
| **IQC 区**   | PDA                      | 提供抽检策略路由，接收 QMS 检验结果。                                          |
| **装箱区**   | 机械臂, 视觉相机, 输送线 | **关键**: 下发装箱策略 (Binning Strategy)，校验视觉识别数据 (PKG/Dims)。 |
| **SMT 区**   | CTU (料箱机器人), 机械臂 | **关键**: 调度混合入库 (Exchange/Picking)，执行 WMS 下发的发料任务。      |
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
  * 可承载 **4 个料箱 (Bins)**，A/B 两面各 2 个料箱位。
  * **货位编码**: 单层移动料架每架 4 个货位，按 Excel 初始化协议使用 `A/B/C/D`
    顺时针编码；货位条码示例为 `NHW-1CLJ-0096-1C-1`。`rack_face=A|B` 表示物理操作面，`rack_slot_code=A|B|C|D`
    表示架内货位，两者不是同一字段；每个货位属于哪一面以 WMS 货架主数据为准，不从字母名称推断。
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
  * **满箱交换**: 支持业务按 WMS 决定，通过一次 CTU 协调交换请求互换 1～2 对已确定料箱；一次请求对应一个
    `TransportTask`，不得拆成多个普通搬运请求。一个任务内所有 Left Bin 必须来自同一来源货架面，所有 Right Bin 必须来自
    同一目标货架面；左右物理面字母可以不同，但不得把同一角色的 A/B 两面混在一条请求中。
* **典型场景**：
  * 单层货架满箱 -> CTU 交换至五层货架空箱位。
  * 生产发料: CTU 从五层货架取料箱 -> 放至单层货架 -> AGV 运至产线。
* **调度策略**：
  * **冷热区管理**: 五层货架冷热区、A/B 面负载、空箱授权和 CTU 路径由 WMS/RCS 作为权威系统判断。
  * **A/B 面平衡**: 五层货架两侧真实负载和全局优化由 WMS/RCS 决定；WES 只按当前执行任务提交交换或补给需求，并保存授权结果和执行证据。

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
  * **料盘级执行证据**: WES 保存检测、贴标、人工确认，以及 WMS 提交 ACK 与类型化终态结果；料盘统一使用 `PkgID`
    关联，货架储位统一使用 `RACK_SLOT(rack_id, rack_face, slot_id)` 定位。真实储位归属、库存可用性和 A/B 面资源授权以
    WMS 为准。
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
     * WES 将 QMS 结果和栈板上 **所有 GRN** 的关联 evidence 提交 WMS，由 WMS 返回唯一业务去向：
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

本模块是 WES 最核心的 **执行协调 (Execution Coordination)** 场景；业务调度结果由 WMS 给出。

#### 3.3.0 WES 核心基础平台 (WES Core Foundation Platform)

为支撑后续章节中的 SMT 智能装箱 (3.3.1)、混合入库 (3.3.2) 和生产发料 (3.3.3)，WES 必须提供共享的设备命令可靠性基础能力。核心、
设备统一接口与 WorkLine 插件的所有权以 `docs/architecture/device-command-contract.md` 为准：核心只负责持久化、幂等、
ACK/CALLBACK 证据和通用诊断；供应商 ECS/网关直接实现统一接口，WMS 业务结果由对应业务合同拥有，Phase 3 只提供
WMS Client，工作线执行映射由插件拥有；不得互相替代测试。

**1. 控制系统集成 (Control System Integration)**

*   **架构约束**: WES 不直接控制底层 PLC、传感器或电机。所有物理设备的控制由硬件供应商提供的控制系统 (如装箱流水线控制系统、机构件流水线控制系统) 负责封装。
*   **通信协议**: 采用 `docs/integration/third_party_integration_whitepaper.md` 定义的 HTTP/JSON 统一接口（wire）；
    纯局域网目标协议不增加供应商私有认证分支。供应商原始文档只作为设备合同附录输入。
*   **逻辑位置映射**: WES 仅下发逻辑位置ID (如 `STATION_A`, `RACK_01`)，由控制系统负责解析为物理坐标，实现业务逻辑与物理参数的解耦。

**2. 异步消息交互 (Async Message Interaction)**

*   **交互模式**: 采用 **"下发(Command) -> 应答(Ack) -> 回调(Callback)"** 的三段式机制。
    *   **WES**: 在外部调用前持久化命令身份、目标设备、deadline 和关联对象；同步 ACK 只表示厂商控制系统已接纳请求。
    *   **控制系统**: 物理动作完成后按统一接口和获批设备合同附录返回最终结果。
*   **业务动作需求**: 系统需要支持抓取、放置、扫码、加工/贴标等业务动作；具体设备 `task_type` 和 Payload 不固化为
    WES 核心枚举，由对应设备合同附录显式定义。

**3. 逻辑端口要求 (Logical Port Requirements)**

*   **下行能力**: 统一提供 `POST /api/v1/device/command` 与
    `GET /api/v1/device/status?device_code={device_code}`；顶层协议不提供通用取消接口。
*   **上行能力**: 统一提供 `POST /api/v1/callback/result` 与 `POST /api/v1/callback/event`；供应商结果值不直接扩张核心状态枚举。
*   **幂等性保障**: 核心为每条 `DeviceCommand` 保持稳定身份并阻止重复调度；供应商必须按 `command_code` 保证重复请求不
    重复执行物理动作。同一稳定身份始终绑定同一规范化语义载荷，即使首次请求被明确拒绝也不得改载荷复用。命令可能已到达
    设备但结果未知时，不得仅凭相同命令身份自动重放。

**4. 重试与异常处理 (Retry & Exception Handling)**

*   **安全重提边界**: 能够证明请求尚未离开 WES，或设备明确返回统一接口定义的“未接纳”响应时，可靠对象才可保留原
    `command_code` 和原载荷重新提交；合同错误修正后载荷摘要不变才沿用原身份，摘要改变则在明确未接纳前提下创建新身份。
    请求可能已送达、已 ACK、返回幂等冲突或结果未知时保持暂停并查询、等待回调或人工对账，禁止换身份绕过。设备命令出站
    HTTP 基础层每次调用只执行一次发送，不拥有自动重试配置。
*   **未知结果处理**: 请求一旦可能被控制系统接收但未取得确定结果，命令进入 `TIMED_OUT`/远端结果未知状态，保留证据并暂停受影响对象，等待匹配的晚到 CALLBACK；无法闭合时进入人工对账，不自动重发。
*   **状态监控**: 所有供应商实现按 `device_code` 寻址的统一设备状态查询，并把运行模式映射为共享 `mode`、运行状态映射为
    共享 `status`；维护态属于 `mode=MAINTENANCE`，不得混入 `status`。状态响应同时返回实际运行的 `contract_key` 和
    `contract_version`，并禁止缓存；状态观察过期或与活动 `LineRunEpoch` 不匹配时新命令准入失败关闭。命令及两类回调也携带
    对应合同身份，用于识别延迟旧版本消息。核心据此维护通用 `DeviceRuntimeProjection`；状态查询只用于准入、诊断和对账，
    不替代最终 CALLBACK。

**5. 设备层次结构与基础数据 (Device Hierarchy & Master Data)**

*   **设备组织层次 (Device Organization Hierarchy)**:
    *   建立 **区域 (Zone) → 作业线 (WorkLine) → 设备 (Device)** 的三级组织结构，用于任务路由和设备管理。
        *   **区域 (Zone)**: 物理区域划分 (如 `SMT作业区`, `机构件作业区`, `料盘装箱区`)。
        *   **作业线 (WorkLine)**: 区域内的生产线或工作站 (如 `SMT自动线1`, `SMT自动线2`, `SMT人工线`)。
        *   **设备 (Device)**: 作业线上的具体设备实例 (如 `工业电脑`, `PDA`, `机械臂`, `打印机`)。

*   **设备基础数据 (Device Master Data)**:
    *   WES 维护设备清单，记录每个设备的基础信息:
        *   **标识信息**: `device_id` (唯一标识), `device_name`, `type` (PDA/工业电脑/打印机/电脑/LCR测试仪)。
        *   **层次归属**: `zone_code`, `work_line_id` (设备所属的区域和作业线；`work_line_id` 引用 WES WorkLine 主键)。
        *   **用途说明**: 设备的功能描述 (如 "用来点货，绑定栈板发运送任务")。

*   **WorkLine 启动与分拣机设备边界**:
    *   `WORKLINE_START_REQUESTED` 只表示工作线进入 READY/待机状态，可以开始接收业务需求；不表示已有货架到位，也不表示立即开始分拣。
    *   分拣机只有 `SOURCE_ARM` 和 `TARGET_ARM` 两个机械臂，不存在 NG 专用机械臂；NG 放置动作由 `TARGET_ARM` 完成，目标设备角色仍是 `ROLE_SORTING_TARGET_ARM`。
    *   分拣作业启动必须同时满足业务需求、WorkLine READY、Station 业务 lease 空闲、单层货架 active 执行快照或 WMS 到位/授权回调；具体设备命令下发前再按设备角色执行实时准入。

**6. 执行对象与设备命令管理 (Execution and Device Command Management)**

*   **所有权分离 (Ownership Separation)**:
    *   WMS 决定业务执行对象的优先级、来源、目标、业务路线、业务异常分类、替代来源和业务终态；WorkLine 插件校验
        封闭结果并决定设备等待、发送、暂停、NG 隔离或对账等执行 Decision。核心不得用某条工作线的业务状态证明通用设备可靠性。
    *   核心负责 `DeviceCommand` 的持久化、设备准入、幂等、关联、deadline、ACK/CALLBACK 证据和通用诊断。
    *   供应商 ECS/网关负责把内部协议收敛为统一接口；WES 统一接口层只校验公共包络和设备附录，不解释业务优先级或推进业务对象。

*   **设备命令生命周期 (Device Command Lifecycle)**:
    *   核心通用生命周期为 `PENDING / DISPATCHED / ACKNOWLEDGED / SUCCEEDED / FAILED / TIMED_OUT`。
    *   `ACKNOWLEDGED` 只代表请求被厂商接纳；只有匹配当前命令的最终 CALLBACK 才能进入 `SUCCEEDED`/`FAILED`。
    *   业务完成、失败、等待和取消语义来自 WMS 封闭结果；插件只维护本地执行状态，不与 `DeviceCommand` 状态合并。
    *   所有状态变化保留时间、来源、关联和诊断证据，支持查询与审计。

*   **超时监控 (Timeout Monitoring)**:
    *   每条可靠执行对象使用明确 deadline；超时不等同于厂商执行失败。
    *   命令可能已经到达设备但结果未确认时进入 `TIMED_OUT`，只暂停与该命令关联的业务对象并触发诊断或对账。
    *   晚到结果必须幂等追加为证据；只有满足当前对象关联和安全准入条件时才能推进业务状态。

*   **并发控制 (Concurrency Control)**:
    *   每个独立命令资源 `device_code` 最多存在一个已接纳且未终态的命令；只有 `mode=AUTO`、`status=IDLE` 且无活动命令时才能发送。
    *   ECS 以原子方式接纳命令；同一 `device_code` 竞争失败返回 `429 CAPACITY_EXCEEDED`。不同 `device_code` 可以并行执行。
    *   未通过设备准入的 Decision 保持等待，不直接发送；重新调度前必须再次读取当前投影并重新校验状态新鲜度。
    *   业务对象的总序及队列参数更新由 WMS 给出；设备空闲只触发执行准入，不代表核心或插件选择其他业务任务。

#### 3.3.1 SMT 智能装箱协调 (Smart Kitting Coordination)

粗分逐盘入库、满箱交换和自动上架的 operation、严格 DTO、幂等、物理门禁与失败边界由
`docs/contracts/wms-inbound-putaway-integration-requirements.md` 集中定义。本节与 §3.3.2 只保留产品级场景和职责边界，
不得复制或覆盖该合同；合同获批前不得据此开始 Phase 8/9 业务实现。

* **场景**: 工人把标准整盘物料放入粗分机入口，设备自动输送、扫码、测量并放入单层货架目标 Bin/Cell。
* **入库完成点**: WES 校验 ECS 身份与测量证据后请求 WMS 准入；WMS 原子绑定 GRN，返回稳定料盘身份及唯一目标。
  只有机械臂可靠 PUT 且 WMS 原子记录最终位置事实后，该盘才完成入库。
* **权威边界**: WMS 决定业务准入、GRN、料盘身份、目标 Cell、容量、兼容性和 NG；WES 只拥有本地执行、物理证据、
  位置投影以及 `DeviceCommand` / `TransportTask` 编排。粗分入库不建立顶层 `InboundTask`，WES 不在本地选择替代料格。
* **目标恢复**: PUT 不可逆点前发现目标不可执行时，WES 携带原始失败证据请求 WMS 返回新目标、NG 或等待；进入不可逆
  PUT 后只能停机对账，不能改址或重发等价物理动作。
* **货架释放**: 仅 WMS 可以作出释放决定。WES 接纳决定后停止新目标，但必须等待已接受料盘、所有已发出准入请求、
  设备命令、位置和外部 Fact 全部取得确定结果，才能冻结货架释放快照；顺序不明或迟到结果与释放冲突时必须停机对账。
* **NG**: 料盘可靠进入粗分 NG 交接区并由 WMS 记录业务专属 NG Fact 后，本盘执行结束；后续人工处置由 WMS 负责。

#### 3.3.2 混合入库策略 (Hybrid Inbound Strategy)

* **场景**: 已完成粗分入库的单层货架，先执行获批的满箱交换，再把剩余料盘迁移到五层货架目标 Bin/Cell。该阶段只
  迁移权威位置，不重复 GRN 绑定或入库确认。
* **计划边界**: WMS 根据冻结货架快照形成不可变来源计划，冻结全部来源成员及其满箱资格；目标五层货架、实际空 Bin 和
  精确交换对按货架面、按执行批次晚绑定。WES 不按本地 `Usage`、空格数或等待时长改判，也不得预建后续批次。
* **满箱交换**: WMS 每次决定同面 1～2 对精确成员和最终储位，WES 只通过 Transport Port 原子提交该批全部成员。当前面
  需要两对而目标货架只有一个合格空 Bin 时，禁止缩成一对；优先更换能完整覆盖当前面且尽量覆盖剩余面的五层货架，没有合格
  替换货架则等待。任一成员失败或结果未知时停止后续动作并等待人工恢复，不自动补偿或反向搬回。
* **跨面顺序**: 当前面只有在 T3 全部成员成功且位置明确、全部位置迁移 Fact 被 WMS 记录并完成主账更新后才闭环。若另一面
  仍需交换，WMS 重新计算后，WES 分别完成所需来源货架和目标货架换面，确认所有 `RACK_ROTATE` 成功且到达面正确，再创建下一面
  的新交换任务。需要换目标货架时，同样先完成旧架搬离和新架到位，再重新计算。
* **目标 Bin 供退**: WMS 只供给库存主账中存在可分配 Cell 的 Bin，并为实际退料候选分配五层货架储位；WES 只按现场
  缓存和 CTU 物理容量限流，不推断库存可用性。
* **扫描与逐盘上架**: SCAN1 承接 WMS 业务路由，SCAN2 只确认当前 Bin 是否可服务，SCAN3 按已持久化 NG 处置分流，
  SCAN4 把正常 Bin 送入本线退料缓存。来源盘复扫后由 WMS 晚绑定精确目标 Cell，身份不符或位置未知时冻结对账。
* **完成边界**: WMS 根据计划和已记录事实裁决业务完成；目标 Bin 退回、NG Bin 人工取走和来源货架搬离属于独立物理清理
  义务。全部物理义务闭合前，WorkLine 不得释放或切换模式。

#### 3.3.3 SMT 生产发料协调 (SMT Production Issue)

场景：SAP 工单进入自动或人工发料线。

1. WMS 根据 SAP 工单、出库单、波次、库存和产线需求形成 `PickingTask`。任务发布只携带身份和排队信息，不分配来源或目标
   资源。WES 不读取业务单据，也不生成波次。
2. WES 选择可执行任务和就绪工作线后请求 WMS 准备。WMS 返回 `PREPARE_ACCEPTED`，再按实际 WorkLine 及其 STATION，以连续
   `plan_revision` 分批发布已锁定的五层货架候选 Bin、退料货架 SLOT 和目标转运货架窗口。`plan_revision=1` 必须且只能定义一个
   初始目标窗口，可以同时新增来源成员；后续目标窗口只由逐盘 `ACCEPT` 创建。
   计划增量可以追加来源或取消 Bin，但不能改写已接纳成员，也不携带货架动作、清场去向、CTU 批次或 WMS 计算进度。WES
   持久化并 ACK 局部完整的增量后即可冻结 WorkLine 并开始相关搬运。
3. WMS 提供任务池优先序，人工调整通过队列更新完成。WES 根据设备、工作位、缓存、活动 Transport 和空闲时长选择 WorkLine。
   暂不可执行的前序任务不阻塞后续任务；同一工作线不提前启动后继任务，WES 不提供人工启动入口。WES 按退料优先、设备忙闲、
   Transport 终态和目标面安排节拍，RCS 负责车辆路径、拥堵和避让。
4. 五层货架到位后，WES 按实际货架面预留入料位置。WMS 从仍位于冻结来源且尚未消费入站资格的候选 Bin 中形成原子入站批次。
   Bin 到达 `RETURN_BUFFER` 后，WMS 从当前货架面的权威空储位中分配退箱目标，不要求原架原位。当前面容量不足时，WMS 返回
   等待或换面、换架方案；额外货架由 WMS 维护任务级退箱承接占用，WES 不自行选择其他目标。
5. CTU 可以乱序投箱。Bin 到达 SCAN2 后，WES 请求 Cell 工作计划。WMS 返回当前可执行成员，不下发 Cell 优先级或依赖图。
   WES 根据现场资源安排执行顺序。
6. 设备取盘并扫描完整六合一码。WMS 返回 `ACCEPT | REJECT | WAIT`；`ACCEPT` 包含精确 SLOT 和需要的换面或换架方案，`WAIT`
   包含原因和重试间隔。WES 从可靠位置投影取得新架来源，使用 WorkLine 固定工作位，并在相关事实变化后重新求值；本地技术
   超时只暂停、告警并进入对账，不得生成业务拒绝。当前盘在扫码台等待 Transport 到位。目标机械臂成功 PUT 后，WES 提交
   逐盘位置事实，由 WMS 更新物料位置、库存和目标占用。转运货架容量、规格兼容和目标决定属于 WMS。
   两个机械臂按不同 `device_code` 推进；ECS/PLC 硬件锁负责扫码台交接、防撞和动作互锁。没有安全暂存位时，硬件锁必须在
   下一盘离开来源前确认扫码台交接路径可用；WES 不建立扫码台事件或软件锁。
   NG 作用域固定为 MATERIAL、CELL 和 BIN：物料资格或质量不符是 MATERIAL NG，物料与权威 Cell 绑定冲突是 CELL NG，Bin
   无法识别、不是候选、身份冲突或方向错误是 BIN NG。空取、扫码不完整、设备失败、业务等待和未知结果不属于 NG。CELL NG
   当前盘位置事实确认后关闭当前 Cell，Bin 到达 NG 出口时只补充最终位置；只有 BIN NG 影响整个 BinWorkExecution。未匹配物理
   Bin 必须引用预期计划成员，但不能据此确认实际 Bin 身份或直接关闭原成员。
7. 目标架、退料架和五层货架可以并行搬运，退料优先但不阻塞无资源冲突的 CTU 和 Bin 流。货架去向不在准备或计划阶段预生成；
   WES 在清场门禁成立后请求 WMS 决定，再创建清场 TransportTask。WMS 可用更高 revision 追加或取消
   `BinWorkExecution`。料盘离开来源后不可放回；取消命中已接纳取盘命令时，必须先将当前盘闭合到目标或 NG。只有
   `UNKNOWN/RECONCILING` Transport 可以通过同一任务新的、位置完整的权威结果证据形成更高结果版本；确定终态的人工对账只保持
   业务步骤，不回退旧 TransportTask，也不恢复已释放的核心资源绑定。
8. 每盘位置事实可靠回传 WMS。本地业务义务、逐盘事实和取消动作闭合后，WES 携带 `last_applied_plan_revision` 请求 WMS 确认
   状态。尚无计划时 revision 为 `0`。WMS 返回 `COMPLETED | NOT_COMPLETED`，不接收成员结果全集；版本落后时补发增量，业务
   进行中时返回强制重试间隔。Bin、Rack、Transport 和工作线清场保持独立生命周期。

自动出库的对象、不变量和验收场景以
`docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md` 为业务设计真源；具体 HTTP 请求和响应 JSON
以 `docs/contracts/wms-outbound-picking-task-integration-requirements.md` 的评审基线为准。

---

### 3.4 执行协同与调度策略 (Execution Coordination & Scheduling Strategy)

本模块定义 WES 的 **执行协调能力 (Execution Coordination Capability)**：与现有 WMS 协同、保存执行证据，并为 WorkLine
插件提供受控的 WMS 业务结果和执行 Decision 边界。全部业务调度规则归 WMS；插件只映射和执行 WMS 结果。

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

  * **场景**: 经业务合同确认的非 `PickingTask` 消费者需要核对 WMS 权威库存事实。
  * **能力**: 使用对应业务合同批准的库存查询 operation 获取类型化 authority snapshot；具体 Method、Path、DTO 和预算
    由该业务合同定义，`docs/contracts/wms-northbound-interaction-contract.md` 只定义共享 Client 使用标准。
  * **缓存策略**: 不做跨请求缓存；同一 execution 仅复用该次查询返回的 authority snapshot。

  **2. 库存预留 (Inventory Reservation)**

  * **场景**: 经业务合同确认的非 `PickingTask` 消费者需要由 WMS 锁定库存。
  * **能力**: 使用对应业务合同批准的 operation 申请和释放预留；SRS 不重复定义 operation identity、Path 或 DTO。
  * **响应**: 现有 WMS 返回 `ReservationID`，并在 WMS 侧锁定库存；WES 只保存预留引用和执行证据。
  * **释放机制**:
    * **释放请求**: 任务完成或取消时，WES 向 WMS 提交预留释放需求并保存释放引用、回执和执行证据；预留锁定与最终释放事实由 WMS 持有。
    * **自动过期**: WMS 在 `expire_time` 后自动释放预留，无需 WES 干预。
    * **异常恢复**: WES 重启后，可向 WMS 查询关联预留状态并提交对账/释放需求；不得在本地判定预留已释放。

  **3. 库存确认 (Inventory Confirmation)**

  * **场景**: 物理动作完成后 (如: 装箱完成、发料完成)，WES 通知现有 WMS 更新库存。
  * **能力**: 入库物理完成、自动出库逐项位置变化和自动出库整单完成分别形成可靠提交义务；SRS 不重复定义
    operation identity 或 wire DTO。
  * **幂等性保障**: WES 内部以闭集 operation identity 与 `dispatch_key` 唯一标识可靠义务；`dispatch_key` 不自动成为
    WMS wire 字段。Adapter 只发送逐 operation 获批的一个幂等/关联字段；未获 WMS 批准前不得声称远端原子去重。

  **4. 异常处理 (Exception Handling)**

  * **场景**: 物理动作失败 (如: AGV 故障、装箱失败)。
  * **流程**:
    1. WES 根据权威失败证据更新对应 `MaterialExecution`、`BinExecution`、`DeviceCommand` 或 `TransportTask`；结果未知时保持对象级暂停，不伪造失败终态。
    2. WES 向 WMS 提交预留释放或异常处置需求，等待 WMS 确认、拒绝或人工授权。
    3. WES 保存异常原因、时间、关联对象和外部回执，供告警与对账追踪。

#### 3.4.2 WMS 业务决策与 WES 执行映射 (Business Decision and Execution Mapping)

* **唯一业务 owner**: WMS 负责装箱资格与目标料格、来源、优先级、业务路线、冷热区、A/B 面、交换/零散入库模式、
  业务异常分类、替代来源和业务终态。规则阈值与算法不在 WES 代码、配置或插件中复制；物理等待、NG 路由、暂停和对账
  由 WES 根据业务结果与执行证据决定。
* **显式合同**: 每个真实消费者通过对应业务模块的具名方法提交当前对象身份、已发生物理事实和必要执行证据；业务模块复用 Phase 3 `WmsClient`，
  WMS 返回封闭业务结果、稳定原因码、关联身份和版本/时效元数据；不得建设 generic `decide`、规则 DSL 或动态 registry。
* **WES 执行边界**:
  * 校验 WMS 结果的合同、关联、版本、时效和当前物理可执行性。
  * 按结果创建 `DeviceCommand`、`TransportTask` 或 `WmsConfirmation`，并根据单设备单活动命令准入、deadline、安全和终态证据
    决定等待、发送、暂停、隔离或对账。
  * 维护 `rack_slot_code`、`bin_cell_location` 和位置 `FREE | RESERVED | OCCUPIED | IN_TRANSIT | UNKNOWN` 等物理作业期投影，
    供执行校验和 evidence 回传；WES 不维护自动出库转运货架的 `Used_Depth`、`Remaining_Capacity`、规格兼容或换架阈值，
    也不能用物理投影产生或改写业务结果。
  * 结果缺失、过期、矛盾或物理不可执行时 fail closed 并反馈 WMS；不得本地选择另一来源、目标、路线或处置。
* **设备边界**: ECS/PLC 继续拥有坐标、机械互锁和安全；RCS 拥有运输路径与车辆调度。WMS 的业务结果不能替代设备终态
  evidence，WES 的设备执行校验也不能反向升级为业务裁决。

#### 3.4.3 对象级执行协调 (Object-Level Execution Coordination)

* **职责**: 上游 WMS 结果是业务权威；具体 WorkLine 插件校验其关联和物理可执行性后映射为封闭执行 Decision，核心分别推进 `MaterialExecution`、`BinExecution`、`DeviceCommand`、`TransportTask` 和 `WmsConfirmation`。
* **生命周期所有权**: 不建立统一 Task 状态机。每类执行对象只拥有自身状态、幂等键、终态证据和恢复规则，禁止用一个通用状态覆盖设备命令、搬运履约和 WMS 确认。
* **依赖表达**: 依赖通过明确的对象关联、权威终态证据和投影准入条件表达，不建设可配置 DAG、通用步骤表或跨工作线任务引擎。
* **并发边界**: 核心执行对象级准入，并强制每个独立命令资源 `device_code` 最多一个活动命令；不同 `device_code` 之间允许对象级流水并行。
  WMS 决定自动出库任务池的业务优先序，WES 可以跳过当前不可执行的前序任务，但同一 WorkLine 不得并行准入多张任务。
  WorkLine 插件只能在本线当前已准入任务的成员中，根据退料优先规则、当前工作位、共享设备、位置投影和可靠终态安排现场节拍
  及实际机械顺序。真实区域容量、AGV 拥堵、运输并发、车辆路径规划和避让由 WMS/RCS 判断，WES 不接管 RCS 调度。
* **通信恢复**: 未发生 WES 进程重启时，未决可靠对象取得权威终态证据、WMS 返回新的有效业务结果并重新通过对象级
  执行准入后，插件可以继续映射执行；不保存通用 Step checkpoint，也不从步骤号继续执行。
* **重启恢复**: WES 进程重启后只读取持久化证据用于诊断和人工清线，不在原 `LineRunEpoch` 重新判定或恢复
  物理编排；操作员完成清线并确认后创建新的 `LineRunEpoch`。

---

### 3.5 特殊物料处理流程 (Special Material Handling)

本模块处理 **高值物料 (High-Value)**、**MSD 物料 (Moisture Sensitive)**、**PCB 物料** 和 **机构件 (Mechanical Parts)** 的特殊流程。

> **交付范围：未来需求。** 本节保留产品需求，但不属于当前顶层 SPEC §3.1 和十二阶段架构收敛的实现或验收范围。
> 当前不得预建空插件、SFC Adapter 或通用特殊物料平台；进入实施前必须基于真实工作线和供应商资料修订顶层
> SPEC、总控计划，批准设备合同附录，并为对应执行插件单独批准实施计划。

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
    * ECS 按设备合同附录上报包含 PKG 与箱条码的统一扫描事件。
    * WES 校验: `PKG.Material == WorkOrder.Material`。
  * **件级追溯**:
    * ECS 扫描机构件条码后，按设备合同附录上报 PKG 与部件序列号关联证据。
    * WES 保存包含工单、PKG、部件序列号和时间戳的追溯证据。
    * WES 推送追溯数据至 SFC (Shop Floor Control)。

  **3. Magazine 调度与产线上料 (Magazine Dispatch)**

  * **装满触发**: ECS 按统一接口上报料架已满的规范证据后，WES 创建送往生产 Buffer 的 `TransportTask` 并提交 WMS。
  * **空 Magazine 回流**: RCS/WMS 自主调度空 Magazine 回自动线 (WES 不干预)。
  * **产线需求驱动**: WES 接收 SFC 的类型化料架需求后，创建 Buffer 到产线的搬运需求并提交 WMS。

  **4. 空栈板回收 (Empty Pallet Return)**

  * **触发条件**: ECS 扫描栈板，确认 `IsEmpty = True`。
  * **回收流程**:
    * ECS 按统一接口上报包含栈板身份的空栈板事实。
    * WES 创建送往空栈板区的 `TransportTask` 并提交 WMS。
    * WES 记录空栈板回收执行投影与 WMS 提交 ACK、类型化终态结果证据；真实位置和占用以 WMS/RCS 为准。
  * **统一边界**: 转运、补给、空架回流和空栈板回收均表达为 WMS 搬运需求；AGV/CTU/RCS 任务由 WMS 转发并闭环。

---

### 3.6 生产退料闭环 (Production Return Loop)

本模块处理产线退料的 **质量闭环 (Quality Loop)**，确保退料经过清点、测试后重新入库。

> **交付范围：未来需求。** 本节不属于当前十二阶段计划的最终验收范围。LCR、X-Ray、贴标和退料工作线的真实
> 设备合同明确后，必须先修订顶层 SPEC 和总控计划，再批准设备合同附录并由独立执行插件交付代码、fixture 和测试；
> 供应商 ECS/网关独立通过统一接口一致性验收，核心平台不得用本节业务场景证明基础能力。

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

#### 3.6.2 LCR 测试业务结果与执行 (LCR Test Result and Execution)

* **WMS 决策逻辑**: WMS 根据物料配置、退料数量和使用情况返回 `SKIP_LCR_TEST`、`REQUIRE_LCR_TEST` 或封闭拒绝结果；
  WES 不复制阈值或判断规则。
* **测试流程**:
  1. **PDA 扫描**: 作业员在 WMS PDA 扫描 PKG -> 调用 WMS 获取 `LCR_Required(True/False)` 并回显。
  2. **测试执行**: 若需测试，连接 LCR 测试仪；供应商 ECS/网关按设备合同附录上报通过/失败的规范检测证据。
  3. **结果处理**:
     * WES 将 LCR 物理检测 evidence 提交 WMS，按 WMS 返回的下一步路线或 NG 处置执行。
     * WES 不根据 Pass/Fail 自行产生业务状态或选择物理去向。

#### 3.6.3 X-Ray 智能清点与贴标 (X-Ray Counting & Relabeling)

* **WMS 决策、WES 执行逻辑**:

  **Step 1: 清点业务结果 (Counting Result)**

  * WES 提交退料数量、发料数量、使用情况和当前 PKG evidence；WMS 返回 `SKIP_XRAY_COUNT`、
    `REQUIRE_XRAY_COUNT` 或封闭拒绝结果。WES 不复制判断规则。

  **Step 2: X-Ray 清点执行 (X-Ray Execution)**

  * **流程**: ECS 扫描 PKG -> 流入 X-Ray 设备 -> 清点。
  * **数据流**: X-Ray 设备按设备合同附录上报原 PKG 与实际清点数量，供应商内部消息名和 DTO 不进入核心合同。

  **Step 3: 新标签执行 (New Label Execution)**

  * **WMS 标签结果**: WES 提交 X-Ray 清点 evidence，WMS 生成新的 PKG 业务身份和完整标签数据；WES 不生成序列号、批次、
    数量或业务日期。
  * **贴标校验**:
    * WES 创建包含 ZPL 数据的逻辑打印 `DeviceCommand`，按设备合同附录通过统一接口下发。
    * ECS 打印并贴标后扫描新标签，按统一接口上报新 PKG 证据。
    * WES 只校验扫描到的新 PKG 与 WMS 指定标签身份一致；物料和批次业务资格由 WMS 保证。
    * **贴标要求**: 新标签覆盖旧标签，但保留旧料号可见。

  **Step 4: 分类上架 (Classification & Putaway)**

  * **WMS 路由结果**:
    * WES 提交 X-Ray、贴标和物料 evidence；WMS 返回人工封包/干燥柜、退料上架、等待、拒绝或补空架等封闭结果。
    * WES 只执行指定路线，不根据 MSD 属性或其他本地规则改判。
  * **退料货架管理** (基于 3.1.2 定义的退货货架规格):
    * WES 维护退料执行投影或证据视图，料盘统一引用 `PkgID`，位置统一引用
      `RACK_SLOT(rack_id, rack_face, slot_id)`，WMS 确认义务统一引用 `WmsConfirmation`；不再为该视图定义另一套字段名。
      退料库存主账由 WMS 持有。
    * **料盘级执行证据**: 每个 WMS 授权储位可对应单个料盘，WES 只保存检测、贴标、人工确认，以及 WMS 提交 ACK 与类型化终态
      结果证据；真实储位归属、库存可用性和货架面/储位授权以 WMS 为准。
    * **A 面装满**: WES 根据执行证据提示现场或向 WMS 请求转向货架；是否可转向、目标面和目标货架由 WMS 授权。
    * **全部装满**: WES 提交退料货架转运或补空架需求给 WMS；是否搬运、目标区域和空架补给由 WMS 授权并转发执行。
    * **A/B 面隔离**: 不同产线或工单的退料隔离策略由 WES 提交建议或需求，WMS 返回授权、拒绝或实际执行结果。

#### 3.6.4 退料入库与库存更新 (Return Putaway & Inventory Update)

* **WES 数据处理**:
  1. **库存更新**: WES 提交退料检测、贴标和执行证据，由 WMS 完成库存调整并回传确认；库存增加在 WMS 确认后生效。
  2. **追溯记录**: 原/新 PkgID 谱系与数量清点追溯由 WMS 持有；WES 只保留不可变的当前六合一扫码快照、
     X-Ray/贴标/执行 evidence，以及执行所需的 WMS 确认。
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
     * **继续执行**: 未发生 WES 进程重启、证据闭合、取得 WMS 新的有效业务结果且对象级准入通过后，由对应
       WorkLine 插件映射下一步执行 Decision，不使用通用 Task Engine 从步骤号恢复。
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
  * **校验机制**: PDA 提交数据到 WMS，WMS 完成业务校验并返回允许/拒绝指令；若需确认现场可执行性，WES 只返回
    设备/位置 evidence 或执行可行性，不参与业务裁决。

#### 3.7.3 通信异常、未知结果与恢复 (Communication Failure, Unknown Result and Recovery)

* **场景**: ECS 网络抖动、设备无响应，或 WMS 转发的 RCS/AGV/CTU 任务超时。
* **WES 处理要求**:

  **1. 按发送边界分类 (Classify by Send Boundary)**

  * **确认未发送**: 请求尚未离开 WES 时，只有逐 operation 外部合同明确批准安全重提且可靠执行对象显式
    `retryable=true`，对象才可保留原命令身份或内部 `dispatch_key`，映射到获批的唯一 wire 字段后重新提交；否则保持暂停
    并进入人工对账。Adapter 每次调用只执行一次发送，不拥有 retry/backoff 配置；不得创建新的业务请求身份。
  * **可能已发送**: 已开始外部调用但未取得确定 ACK/终态时，必须标记为远端结果未知。设备命令进入
    `TIMED_OUT`。`TransportTask` 保留原身份等待可靠异步终态；提交结果未知或回调超期时进入人工对账。`WmsConfirmation` 保留原
    `dispatch_key`，仅在逐 operation 合同明确批准安全重提且对象显式 `retryable=true` 时，才映射为唯一获批 wire 字段后
    重提；否则保持暂停并进入人工对账，不自动重放物理动作。
  * **已取得终态**: `DeviceCommand` 只接受通过合同校验且关联匹配的最终 CALLBACK；
    `TransportTask` 只接受 WMS 可靠异步回传、且通过 Transport evidence 应用端口校验的类型化终态。标准化成员位置事实可在
    终态前更新位置投影，但不终结任务；重复、倒序或晚到事实只幂等追加证据，不得回退已确认位置。

  **2. 对象级暂停与告警 (Object-Scoped Hold and Alert)**

  * 通信异常只暂停与未决命令、搬运任务或确认义务关联的 `MaterialExecution`/`BinExecution`，不得无依据暂停整条工作线。
  * 自动出库任务中，物理结果未知只阻止依赖该结果的对象、设备和位置继续执行；它关联尚未完成的业务成员时，PickingTask
    保持执行中。PickingTask 已完成后的退箱或清场结果未知只保留相关资源绑定和告警，不回退任务，也不连带无资源依赖的对象、
    其他任务或工作线。
  * 设备离线、故障或未知状态写入 `DeviceRuntimeProjection`，并保存来源、时间和关联命令；不得由一次业务失败直接推断设备永久离线。
  * 超过对应合同的 deadline 后触发可操作告警，告警必须指向未决对象、证据和对账入口。

  **3. 证据驱动恢复 (Evidence-Driven Recovery)**

  * 心跳或网络恢复只说明通信重新可用，不证明先前物理动作未执行、已完成或可安全重放。
  * ECS 命令必须等待匹配的晚到 CALLBACK 闭合命令终态；人工对账只处理无法闭合的现场事实，不伪造设备命令 CALLBACK。
    RCS/AGV/CTU 任务必须以 WMS 可靠异步回传、且经合同校验的类型化终态结果为权威证据；WMS 完成人工位置核对后也必须通过
    同一个 Transport result operation 发布新的、位置完整的 `SUCCEEDED | FAILED` evidence，由 WES 形成更高内部结果版本，
    不能直接修改 WES 投影。
  * 证据闭合后重新执行当前对象和设备的准入检查；存在冲突、结果未知或位置不一致时继续保持对象级暂停。

  **4. 上游 WMS 断连处理 (WMS Disconnection Handling)**

  * WMS Client 和 WMS 转发 Transport Adapter 都只执行单次发送，不拥有 retry/backoff、可靠义务或业务终态。
    超时预算、远端幂等和安全重提资格必须由对应外部合同与可靠对象共同约束，不能由 Adapter 自行扩张。
  * 暂停新的库存查询依赖判定、搬运/交换需求和 WMS 确认提交；已被 WMS 接纳的外部任务可以继续执行。WES 继续可靠接收
    WMS 回传的 TransportResult；普通 WMS 业务事件不能终结 `TransportTask`。
  * WMS 恢复后继续以相同事件身份重传未获成功 ACK 的 TransportResult；WES 对回调超期或提交结果未知的任务保持人工对账。
    未决 `WmsConfirmation` 只有在逐 operation 合同明确批准
    安全重提且对象显式 `retryable=true` 时，才将内部原 `dispatch_key` 映射到唯一获批 wire 字段后重提。取得终态证据后
    逐对象恢复；条件不满足或结果仍未知时保持暂停，不得全局自动继续被暂停任务。

#### 3.7.4 优先级调整与急料插队 (Priority Adjustment & Urgent Material)

* **场景**: 产线急需某物料，需要调整尚未开始任务的执行顺序。
* **WMS 决策、WES 执行**:
  1. 优先级调整和急料插队只能由 WMS 通过新的无歧义总序发起，首版只调整尚未开始的任务。
  2. 当前正在执行的 PickingTask 继续完成；WES 不自行抢占、跳选或暂停任务。
  3. 已经发出的物理动作不得因优先级变化伪造取消或回滚；下一任务按 WMS 更新后的总序启动。

#### 3.7.5 数据校验与防错机制 (Data Validation & Error Proofing)

* **WMS 业务校验与 WES 执行校验**:
  1. **输入校验 (Input Validation)**:
     * PDA 扫描 PKG 由 WMS 完成格式、主数据和业务资格校验并返回封闭结果；WES 只校验 DTO 合同与关联。
  2. **逻辑校验 (Logic Validation)**:
     * 装箱目标和容量业务资格由 WMS 决定；WES 只核对指定目标与最新物理 evidence 是否矛盾。
  3. **物理校验 (Physical Validation)**:
     * ECS 反馈 "无法放入" -> WES 隔离当前执行并反馈 WMS，等待新的业务处置，不自行重新分配。
  4. **追溯校验 (Traceability Validation)**:
     * 出库业务追溯资格由 WMS 给出；WES 校验指定 PKG 与当前执行对象、WMS 结果版本和物理扫描 evidence 一致。

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
  * **执行映射**: 消费 WMS 的装箱目标、发料优先级、路线和业务处置结果，映射为设备等待、发送、暂停、隔离与对账。
  * **设备控制**: 直接调度 SMT 区、机构件区、退料区的 ECS/视觉/贴标/X-Ray/LCR 等作业设备；运输设备由 WMS 转发调度。
* **协同原则 (Coordination Principles)**:

  * **查询驱动 (Query-Driven)**: WES 需要库存数据时，实时查询现有 WMS。
  * **确认驱动 (Confirmation-Driven)**: WES 完成物理动作后，通知现有 WMS 更新库存。
  * **预留机制 (Reservation)**: WES 通过预留接口请求 WMS 锁定库存，避免超发；锁定事实和释放规则仍由 WMS 拥有。
  * **RCS 通道 (Phase Boundary)**: 本阶段 RCS/AGV/CTU 仍由现有 WMS 统一调度。WES 只通过 Phase 4 `Transport Port` 提交搬运、交换和旋转需求；Phase 3 Client 只提供 HTTP 访问，不拥有这些动作的提交、异步终态或对账合同。
  * **PDA 通道**: PDA 仅与 WMS 交互；WES 如需感知 PDA 作业结果，由 WMS 推送/同步。

#### 3.8.2 集成接口规范 (Integration Interface Specification)

* **现有 WMS 需提供的接口 (APIs Required from Existing WMS)**:

  | 需求能力 | Operation identity | 需求边界 |
  | --- | --- | --- |
  | 库存查询 | 由对应业务合同冻结 | 返回可追溯的库存 authority snapshot |
  | 库存预留/释放 | 由对应业务合同冻结 | 锁定、释放和过期事实由 WMS 持有 |
  | 入库确认 | 由对应业务合同冻结 | 接收已发生物理事实对应的可靠确认义务 |
  | 出库逐项位置与整单完成 | 由自动出库北向合同冻结 | 分别接收逐料盘位置变化事实与独立任务完成事实，不得合并或双写 |

  `docs/contracts/wms-northbound-interaction-contract.md` 只定义共享 WMS Client 使用标准。每项业务查询、确认和业务输入的
  operation、Method、Path、请求/响应 DTO 与错误合同由对应业务合同定义；WMS 转发的 RCS/AGV/CTU wire 由 Phase 4
  Transport 合同拥有。SRS 不维护第二份 wire 合同。
* **P9 WES 提供的接口能力 (API Capabilities Provided by P9 WES)**:

  **1. 执行状态查询 (Execution State Query)**

  * 按 `MaterialExecution`、`BinExecution`、`DeviceCommand`、`TransportTask` 或 WMS 业务关联键查询当前状态、位置、未决义务和诊断证据。
  * 查询合同必须使用明确版本的 DTO；不得把不同执行对象压缩为一个含糊的通用 `task_id/status` 结构。

  **2. 业务输入接口 (Business Input)**

  * WMS 通过已确认的类型化业务输入或事件合同触发对应 WorkLine 处理；WES 不提供可接受任意 `task_type` 的通用任务下发接口。
  * WorkLine 插件只能根据 WMS 封闭业务结果，通过执行 Decision 产生设备合同附录批准的逻辑动作和业务参数；核心据此
    持久化 `DeviceCommand` 并按统一接口下发。上层调用方不得绕过核心可靠性边界或直接构造设备命令。

#### 3.8.3 数据一致性保障 (Data Consistency Guarantee)

* **幂等性设计 (Idempotency)**:

  * WES 可靠对象内部基于 `operation_identity + dispatch_key` 唯一标识业务义务；同一义务恢复必须保留原
    `dispatch_key`，但该键不自动进入 wire。Adapter 只映射为逐 operation 获批的一个幂等/关联字段。
  * **内部幂等规则**:
    * **相同 operation identity、相同 dispatch_key、相同 Payload**: 复用同一义务及其 evidence，不创建第二个本地义务。
    * **相同 operation identity、相同 dispatch_key、不同 Payload**: 拒绝并记录本地幂等冲突证据。
    * **不同 dispatch_key**: 仅当它代表新的业务义务时才能创建新请求；不得用新键规避未知结果对账。
  * **远端幂等规则**: 只实现对应 WMS 业务合同或 Phase 4 Transport 合同明确批准的 wire 字段、回显、冲突和安全重提语义；不得由内部规则扩张。
  * 避免网络重试导致的重复入库/出库。
* **事务补偿 (Transaction Compensation)**:

  * 若 WES 物理动作失败 -> 向 WMS 提交预留释放或异常处置需求 -> 保存 WMS 回执并记录异常日志；预留最终释放事实由 WMS 持有。
  * 无论 WMS 确认请求可证明未发送还是可能已被接收，只有逐 operation 合同明确批准安全重提且 `WmsConfirmation`
    显式 `retryable=true` 时，才将内部原 `dispatch_key` 映射到唯一获批 wire 字段后重提。条件不满足或结果仍未知时进入
    人工对账，不查询不存在的确认状态端点。
* **对账机制 (Reconciliation)**:

  * 支持按计划和按需对比 WES 执行证据与 WMS 权威库存/任务结果，识别未决、冲突和位置差异。
  * 对账频率属于运维配置，不在产品需求中固化单一执行时间。
  * 发现差异后只暂停受影响对象并触发告警；人工只能确认现场清线/执行事实并留痕，涉及库存、来源、目标、路线或
    业务终态的恢复结果必须由 WMS 给出。

---

### 3.9 接口摘要 (Interface Summary)

(详细定义见章节 4)

* **SAP**: 收货单、工单、主数据（本阶段仅通过现有 WMS 中转，不直接对接）。
* **现有 WMS**: SAP 转发的业务事实、全部业务决策结果、出库 `PickingTask`、库存查询、库存预留、入库/出库确认——WES 的唯一业务/库存数据来源。
* **RCS/AGV/CTU**: 搬运 (Move)、交换 (Exchange)、旋转 (Rotate)，本版本仅通过现有 WMS 转发，不由 WES 直连。
* **ECS**: 校验 (Check)、指令 (Instruction)、结果 (Result)。
* **QMS**: 抽检请求、结果回传。

## 4. 接口需求 (Interface Requirements)

> **需求与合同边界：** Phase 3 只提供共享 WMS HTTP Client；具体业务查询、决策、确认和业务输入随对应业务逐项实现。WMS 转发的搬运、交换、
> 旋转提交及异步终态由 Phase 4 `Transport Port` 承接；首版不提供状态查询或取消。WES 不锁定五层空箱、不交换库存属性、不自动扣减库存，
> 只保存执行事实、资源投影、回写证据和对账证据。精确 wire 必须由对应合同批准，不能从本 SRS 或旧实现推定。

### 4.1 北向接口 (Northbound API - To Existing WMS)

* **定位**: 标准化的业务接入层，接收上游 SAP 通过现有 WMS 转发的单据和主数据。本阶段 WES 不直接调用 SAP。
* **协议**: 局域网 HTTP/JSON；具体 Base URL 由部署配置提供。
* **核心接口**:
  * 收货通知和物料主数据如需由现有 WMS 转发，必须先由各自业务合同批准 method、path 和 DTO；SRS 不预设通用端点。
  * 自动出库由 WMS 下发 `PickingTask`；任务发布、队列更新、准备请求、计划增量和状态确认的 method/path/DTO
    以独立自动出库合同为准，WES 不接收生产工单生成波次。
  * WES 调用现有 WMS 的业务决策、库存查询、预留/释放和入库/出库确认能力；
    `docs/contracts/wms-northbound-interaction-contract.md` 只定义 Phase 3 HTTP/JSON Client 使用标准。具体 operation identity、
    path、DTO 和业务错误必须由对应逐项业务合同批准；合同未批准时不得实现该业务 API。

### 4.2 南向接口 (Southbound API - To ECS)

* **定位**: WES 直连 ECS、视觉、贴标、X-Ray、LCR、打印机等固定式作业设备的统一接口层（uniform wire layer）。
* **协议**: 所有供应商必须实现 `docs/integration/third_party_integration_whitepaper.md` 定义的 HTTP/JSON 固定路径、公共包络、
  身份和异步 ACK/CALLBACK 语义。MQTT、OPC UA、WebSocket、Modbus 等现场协议只能存在于供应商 ECS/网关内部。
* **架构模式**: WES 核心零供应商私有代码。具体设备的 `task_type`、`event_type`、`params`、`data`、错误和时限由获批设备
  合同附录拥有；新供应商接入不得增加 Adapter、DTO 别名、私有路径或动态协议分支。
* **阶段边界**: 本版本 WES 不提供直连 RCS/AGV/CTU 的 Driver Plugin。所有搬运、交换、旋转需求通过 Phase 4
  `Transport Port` 的 WMS 转发适配器提交；`TransportTask` 只接受对应 Transport 合同批准的任务级或批次级结果，
  不接收 CTU 设备内部子阶段事件。

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
