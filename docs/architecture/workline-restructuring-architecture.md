> **拆分说明**: 本文档从 `workline-and-plugin-restructuring.md` v7 拆分（2026-07-10）。
> 完整设计目标与 frontmatter 仍以原文件为准, 详见同目录 index。
> 章节对应: architecture = 原文件 §3 体系结构设计。

---

## 3. 体系结构设计

### 3.1 设计原则

| 原则 | 应用 |
| --- | --- |
| **DRY** | Mixin 复用字段、ModelFactory 生成 Schema、基类复用 CRUD |
| **KISS** | 优先基类默认实现，不过度抽象 |
| **SOLID** | 域/Service/API 单一职责，Hook/Mixin 扩展功能 |
| **YAGNI** | 只实现当前需要，不预设计 |
| **TARGET CONTRACT FIRST** | 锁定目标态业务语义和域边界；旧 API / 旧表 / 旧插件形态不得反向约束新架构 |
| **CORRELATION OVER FK** | 跨域用 `ExecutionCorrelation.correlation_id`，不用 `execution_session.id` 强 FK |
| **TYPED OVER UNTYPE** | typed Pydantic 模型替代裸字符串/裸 JSON dict |
| **EXPLICIT OVER CLEVER** | ExecutionSession vs RuntimeIntentLog 显式拆分；HMAC canonical 显式定义 |

### 3.2 域结构

| 域 | 路径 | 角色 | 状态所有权 |
| --- | --- | --- | --- |
| `workline`（配置域） | `src/app/workline/` | 滚筒线/队列/入口/出口/设备角色/资源边界/平面布局/启停配置生命周期 | 仅配置（无运行状态） |
| `runtime/orchestration`（执行域） | `src/app/runtime/orchestration/`（新建） | ExecutionSession/Inbox/Timeline/Hold、RuntimeIntentLog effect ledger、EffectPort dispatcher | **唯一 session PK 拥有者** |
| `handling` | `src/app/handling/` | 搬运意图、请求生命周期、幂等、超时、重试 | 搬运请求状态 |
| `resource` | `src/app/resource/` | 作业期运行投影（不复制 WMS 主数据） | active projection 状态 |
| `material` | `src/app/material/`（新建；旧 `workline/models/material_unit.py` 迁出或删除） | 料盘/物料 WES 作业期根实体 | material_units 身份 + 作业期业务状态 + current_session correlation；位置摘要只读投影 |
| `device` | `src/app/device/` | ECS/设备上位机接入点、角色、EVENT/COMMAND/RESULT | 设备诊断状态 + command/result/event ledger |
| `wms_integration` | `src/app/wms_integration/`（**已存在，扩展**） | WMS 反腐层：能力面 ports + ACL | WMS 外部事实的 evidence，不复制主数据 |
| `reconciliation`（独立域） | `src/app/reconciliation/`（新建） | RECONCILING 冲突登记、隔离、决议输出、审计 | 冲突 evidence + `resolution_decision` + `owner_scope` + `allowed_next_effect_scope` |

#### 命名澄清：`wms_integration` 是 ACL 域，不是低层集成层

**选择**：`wms_integration` 作为模块名，**不**重命名为 `external/wms` 或 `wms_acl`。

**理由**：

1. **现有 ADR 已经确立** `wms_integration` 是 ACL：`2026-05-26-wms-integration-domain.md` 第二条："该域是 Anti-Corruption Layer，不是 WES 主数据域"。"integration" 命名的语义早就被升级为 ACL。
2. **语义已经足够清楚**：本轮允许破坏性重构，但 `wms_integration` 已被现有 ADR 定义为 ACL。重命名收益低，保留名称能减少无价值 churn。
3. **Python 习惯**：包名=具体名词（`wms_integration`），不是分类前缀（`external.wms`）。若 §10.5 的 RCS/AGV/CTU 直连触发条件成立，镜像命名（`rcs_integration/`、`agv_integration/`）比父目录命名更 Pythonic。
4. **现有结构已经是 ACL 模式**：`services/typed_ports.py`（609 行）+ `models/ports.py`（151 行）已经是 typed port 抽象；`circuit_breaker_service.py` + `callback_normalizer.py` + `evidence.py` 都是 ACL 概念。
5. **与同目录其他域对齐**：`src/app/wms_integration/` 与 `src/app/handling/`、`src/app/resource/`、`src/app/device/` 同级（都是"外部接口/执行域"），不需要 `external/` 父目录。

**条件触发外部系统命名**：以下只是命名规则，不代表当前阶段建目录或预留代码骨架；只有 §10.5 触发条件满足并形成独立 SPEC 后才使用**镜像命名**：

- `src/app/rcs_integration/`（RCS 对接辅助域）
- `src/app/agv_integration/`（AGV provider adapter）
- `src/app/ctu_integration/`（CTU provider adapter）

### 3.3 状态所有权（ExecutionSession vs RuntimeIntentLog 显式拆分）

**问题**：原方案把 ExecutionSession 等运行状态与 RuntimeIntent 混在同一组"状态"中，导致意图和状态源边界不清。

**解决**：拆为两个独立聚合。

```text
runtime/orchestration
  +-- ExecutionSession  (状态所有权, session PK 拥有者)
  |     +-- RuntimeInbox
  |     +-- RuntimeTimeline
  |     +-- RuntimeHold
  |     +-- ExecutionCorrelation.correlation_id       (跨域唯一)
  |     +-- ExecutionCorrelation.execution_session_id (域内强 FK)
  +-- RuntimeIntentLog  (effect proposal / outbox log, **不是状态源**)
        +-- 只记录"Runtime 曾尝试发出什么意图"
        +-- 不可被下游反查为"意图对应的状态"
        +-- 通过 EffectPort dispatch 到下游域
```

**ExecutionCorrelation correlation key**：

| 字段组 | 用途 |
| --- | --- |
| `correlation_id` | 跨域唯一业务关联键 |
| `execution_session_id` | runtime 域内可选 FK；不得被其他域持有 |
| `trace_id` / `source_event_id` | 跨域 trace 与来源事件归因 |
| `business_owner_key` | 业务 owner 审计与查询 |

**约束**：

- 跨域读写都通过 `correlation_id`（correlation key），不通过 `execution_session.id`
- runtime 域内才使用 `execution_session_id` 强 FK；其他域只持 `correlation_id` 引用
- 现有 16+ 文件的 `session_id` FK 需在 ENG-001 中按 per-file 迁移矩阵（rebuild / drop-FK / 短生命周期数据搬迁脚本）逐步替换；不建立兼容期 dual-write

### 3.4 Authority Matrix（外部事实权威来源）

WES 不是所有外部事实的唯一权威。**按事实类型拆分权威来源**——WES 只聚合 evidence 和冲突状态。

| 事实类型 | 权威系统 | WES 角色 | WES 写入 |
| --- | --- | --- | --- |
| 库存数量、批次、有效期 | WMS | 引用 + 作业期快照 | 单次 execution 的只读 authority snapshot；不跨请求缓存 |
| 入库单 / 出库单 / 批次单 / 波次 / 业务任务 | WMS | 外部引用 + 执行上下文 | 不复制为 WES 单据主档 |
| 设备到位信号（光电、接近开关、扫码） | ECS/device | 接收 + 转换 | evidence + transition events |
| 设备业务命令结果（机械臂取放、滚筒线动作） | ECS/device runtime | 接收 + 诊断 | RESULT + 设备诊断状态 |
| 硬件防呆、安全回路、急停、复位、物理坐标/关节控制 | ECS/现场安全系统 | 只感知，不控制 | 只写 event/evidence/hold，不下发安全控制或坐标级指令 |
| 设备事件/任务结果回调 | ECS/device callback | normalize + dispatch | typed evidence + RuntimeInbox + device projection |
| AGV/CTU 履约状态与位置 evidence | WMS/RCS fulfillment callback（直连 provider adapter 仅作条件触发扩展） | 引用履约回调 evidence | 触发 handling 派生状态，不复制实时位置或 SDK 状态 |
| 货架/料箱/库位主数据 | WMS | 引用 + 作业期投影 | 不复制主数据，只维护 active projection |
| WMS 回调事件（WMS 主动推送） | WMS callback | normalize + dispatch | typed evidence + correlation key |
| 冲突、对账、RECONCILING 决议 | WES ReconciliationManager | 冲突记录 + 决议权威 | RECONCILING evidence + resolution_decision；恢复动作由各 owner 按 evidence 执行 |
| WES 作业期料盘/物料根实体 | WES material 域 | **WES 自有** | material_units 身份 + 作业期业务状态 + current_session correlation；位置只读摘要只能由 `RuntimeLocationEvent` 投影更新 |

**不变量**：WES 内部域（workline / runtime / handling / resource / material / device）**不能**直接依赖 PLC/RCS/AGV-CTU SDK 或 WMS HTTP client。设备事实经 `device` 域，搬运事实经 `wms_integration` 端口；只有满足 §10.5 条件触发直连扩展时，才允许新增 `rcs_integration` / `agv_integration` / `ctu_integration` ACL 端口。WES 与固定式自动化设备只按 `third_party_integration_whitepaper.md` 通过 ECS/设备上位机 API 交互，不与 PLC 通讯，不控制坐标、关节、安全回路或硬件防呆。

### 3.5 WMS/RCS 集成约束映射

`docs/integration/wms_rcs_interface_requirements.md` 是本设计的集成边界输入。目标态不照搬其中旧 WorklineInbox / plugin 命名，但必须保留其外部系统边界和接口语义。

| 集成约束 | 来源章节 | 目标态落点 | 设计含义 |
| --- | --- | --- | --- |
| RCS 调度仍由 WMS 统一调度 | §1.2 | operation-specific WMS fulfillment contract | WES 生成搬运需求并提交 WMS；WMS 调 RCS，结果经 WMS 回传 |
| PDA 仅对接 WMS | §1.2 | `WmsEventPort` / `WmsDocumentPort` | WES 不做 PDA API；PDA 结果通过 WMS 事件或单据查询进入 |
| 自动化设备只通过 WES 接入 | §1.2 | `device` 域 | WMS 不直连设备；设备 EVENT/COMMAND/RESULT 归 WES |
| 标签打印按设备类型分流 | §1.2 | `device` 域 + `WmsDocumentPort` | 自动打印设备由 WES 下发；人工打印由 WMS 获取模板后回执 |
| WES 不同步基础数据 | §1.4 / §2 | `WmsMasterDataPort` / typed inventory operation | 物料、区域、地码、货架、料箱、GRN、库存均按需查询；库存 QUERY 禁止跨请求缓存 |
| WMS 是库存唯一真实源 | §1.4 / §10.1 | Authority Matrix | 库存事务必须以 WMS 提交成功为准，WES 只维护作业期投影 |
| 外部输入统一进入 callback | §1.3 / §3 / §5 | `callback` → `RuntimeInbox` | WMS/RCS/ECS/device 回调 API 只校验、落原始日志、ACK、写 inbox，不直接改 session |
| WMS 事件必须幂等 | §3.4 / §10.1 | `idempotency_keys` | `request_id` 映射为 `source_event_id` / `idempotency_key` |
| WMS 调用超时与资源预算 | §2.6 / §10.2 | `wms_integration` adapter | WMS 调用总 deadline、指数退避、wire/decoded/结构预算、熔断告警；inventory 禁止跨请求缓存 |

**接口口径调整**：旧文档中的 `WorklineInbox` 对应目标态 `RuntimeInbox`；旧 `WorklineOrchestrator` 对应目标态 `runtime/orchestration`；旧 plugin 决策对应目标态 runtime capability + EffectPort。

**条件触发直连 RCS/AGV/CTU**：当前阶段不实现，生产前默认不触发、不预留代码骨架。只有满足 §10.5 触发条件并形成独立 SPEC 后，才允许通过 `FulfillmentPort` provider adapter 替换 WMS 履约 provider；内部域仍不允许直接调用 SDK。

**条件触发 provider 差异化原则**：本段只定义触发后的隔离边界，不构成当前阶段建目录、建 API、建配置项或预留代码骨架的任务。

- `RCS` provider 代表调度系统能力，可能同时调度 AGV/CTU/其他搬运设备；WES 只提交履约意图和接收状态。
- `AGV` provider 代表货架搬运能力，核心对象是 rack/work_position/location，不承载料箱格位操作。
- `CTU` provider 代表料箱搬运能力，核心对象是 bin/rack_cell/conveyor_entry/exit，不承载货架移动。
- provider 差异只能在 `FulfillmentPort` adapter 层表达；handling/runtime 不得因 provider 类型直接 import SDK 或分叉业务状态机。

#### Runtime capability 注入边界

**结论**：`wms_integration` 应进入 Runtime capability 注入体系，但只能作为 **ACL provider** 注册到端口注册表；Runtime capability / plugin 只能依赖领域级 port contract，不能直接依赖 `wms_integration` 的实现对象、HTTP client、DTO、异常或 service locator。

注入边界分三类：

| 类型 | 允许能力 | 约束 |
| --- | --- | --- |
| QueryPort | `WmsMasterDataPort` / `WmsDocumentPort` / typed inventory operation / `WmsReconciliationQueryPort` 只读查询 | 用于作业决策前的外部事实查询、WMS 权威事实拉取和 drift 检测；响应必须带 `scope/authority/source/evidence_at/source_version`；库存 QUERY 禁止跨请求缓存，不产生外部状态变更，不写 `RuntimeIntentLog` |
| EffectPort | `WmsInventoryTransactionPort` / operation-specific fulfillment contracts / `DeviceCommandPort` | 所有会改变外部状态、触发履约或确认事件的出站动作必须先写 `RuntimeIntentLog`，再经 EffectPort dispatch；必须有幂等、evidence、timeline 和 callback 闭环。RCS/AGV/CTU 直连若被 §10.5 触发，也只能隐藏在 fulfillment provider 实现内，不新增 capability 可见端口 |
| InboundEventPort | `WmsEventPort` / `DeviceEventPort` | 只负责外部 callback/event 的 normalizer、原始归档和 typed evidence 生成；必须写 `RuntimeInbox`，不得经 `RuntimeIntentLog` dispatch；不得注入 `RuntimeCapabilityContext` 给业务 capability 调用 |

禁止规则：

- `runtime capability` / `plugin` 直接 import `src.app.wms_integration.*` / `src.app.device.*` 的 DTO、异常、HTTP client、service locator、`WmsEventPort`、`DeviceEventPort` 或 `RuntimeInbox` consumer
- Runtime 直接调用 WMS HTTP client 绕过 QueryPort、`RuntimeIntentLog` 或 EffectPort
- `wms_integration` adapter 反向调用 Runtime domain service 修改 session/projection
- WMS/RCS/ECS/device callback 在 API 层直接修改 session/projection；callback 只能 normalize、校验、ACK、写 `RuntimeInbox`
- Runtime capability 直接调用 `WmsEventPort` / `DeviceEventPort`、自行消费 `RuntimeInbox` 或把入站 event 当作出站 effect 处理

**外部系统 ACL 命名约束（M7 回归）**：条件触发后新增的外部系统（`rcs_integration` / `agv_integration` / `ctu_integration` / provider-specific integration）一律以 `src/app/<system>_integration/` 镜像命名建立 ACL 域；禁止建立 `src/app/external/` 父目录，避免外部协议对象跨系统混放。本规则不代表当前阶段提前建包或占位。

#### 3.5.1 外部合同支撑（联调灵活性）

硬件、ECS 与 WMS 均处于并行开发期，WES 目标态把"外部系统会变"作为一等约束处理，但不退回旧 WorkLine/plugin 兼容模式。落地为 5 类能力（字段定义详见 §5.1 ExternalContractProfile 表）：

| 能力 | 设计约束 | 禁止事项 |
| --- | --- | --- |
| `ExternalContractProfile` | 按 `provider_code + contract_version` 描述 WMS/ECS/RCS provider 的能力、字段映射、超时、重试、fixture set 和不支持动作 | 在 runtime / handling / workline 中按 provider 写分支 |
| `RuntimeCapabilityProfile` | 描述 provider 支持且可注入 `RuntimeCapabilityContext` 的 query/effect 能力；Runtime 只通过 port contract 和 capability profile 做 admission | 让 capability 直接读取供应商 DTO、SDK、HTTP client、callback/event/result port 或 `RuntimeInbox` consumer |
| `InboundNormalizerProfile` | 描述 provider 支持的 callback/event/result normalizer 能力；只服务 callback API、normalizer 和 `RuntimeInbox` 写入 | 把入站 normalizer 能力暴露给业务 capability 或当作 effect dispatch |
| `IntegrationLab` | 提供 WMS/ECS simulator、scenario runner、sandbox provider profile 和合同测试集；用于硬件未到位、WMS 未稳定、现场联调前验证 | 把 simulator 写成生产 fallback 或影子 WMS |
| `ScenarioRecorder` / `ScenarioReplayRunner` | 从 RuntimeInbox、RuntimeIntentLog、DeviceCommand、WMS fulfillment、projection evidence 脱敏录制联调场景，并支持 deterministic replay | 用人工数据库改数替代事件回放 |
| 受控 toggle | 仅允许 typed、审计、短生命周期的 release/ops toggle，用于 provider version、adapter、新调度策略或场景流切换 | 通过裸配置动态改状态机、跳过幂等/HMAC/evidence 或绕过 IDLE 准入 |

**联调不变量**：

- simulator 与 sandbox provider 只能通过正式 port contract 进入系统；不得引入测试专用 domain service。
- contract version 必须写入 evidence、trace attributes 和 callback envelope；同一 execution session 固定 provider profile，不热切。
- 场景回放必须验证 active projection diff、RuntimeTimeline 顺序、outbox/effect 幂等和 ReconciliationRecord 结果。
- toggle 默认关闭，必须有 owner、expiry、影响范围、回滚方式和测试矩阵；过期 toggle 必须在同一 Phase 清理。

### 3.6 总体架构图

```text
                                ┌─────────────────────────────────────────────┐
                                │            wms_integration (ACL)               │
                                │  ┌──────────────────────────────────────┐  │
                                │  │  WmsMasterDataPort    WmsDocumentPort │  │
                                │  │  InventoryQueryOperation WmsInventoryTxPort │
                                │  │  Fulfillment operations  WmsReconciliationQueryPort │
                                │  └──────────────────────────────────────┘  │
                                │  InboundEventPort: WmsEventPort -> Inbox     │
                                │  reuse/clean src/app/wms_integration/        │
                                │  + 7 WMS port target-state ACL               │
                                │  + HMAC body 签名 / nonce TTL / typed envelope│
                                └───────────────┬─────────────┬───────────────┘
                                                │             │
                               query/effect port│             │inbound callback
                                                ▼             ▼
┌────────────────────┐   ┌──────────────────────────────────────┐
│ frontend           │   │  runtime/orchestration (执行域)      │
│                    │   │  ┌────────────────────────────────┐   │
│  GET /worklines/   │   │  │ ExecutionSession              │   │
│   {id}/plane/scene │◄──┼──┤  (session aggregate / owner)   │   │
│  GET /worklines/   │   │  │  - RuntimeInbox / Timeline /   │   │
│   {id}/plane/      │   │  │    RuntimeHold                 │   │
│   snapshot         │   │  │  - ExecutionCorrelation        │   │
│  + 行级 + 脱敏 +   │   │  │    correlation_id (跨域唯一)   │   │
│  audit log         │   │  └───────────────▲────────────────┘   │
└────────────────────┘   │  ┌────────────────────────────────┐   │
                         │  │ RuntimeIntentLog              │   │
                         │  │  (effect proposal, NOT state) │   │
                         │  └────────────────┬───────────────┘   │
                         └────────────────────┼──────────────────┘
                                              │ effect port
                ┌─────────────────────────────┼──────────────────────────────────┐
                ▼                             ▼                                  ▼
   ┌────────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────────┐
   │  handling              │  │  resource                │  │  material                │
   │  - HandlingOperation   │  │  - active projection     │  │  - material_units        │
   │  - HandlingMove       │  │  - typed ExternalRef     │  │  - WES-owned root        │
   │  - correlation_id     │  │  - typed evidence env    │  │  - current_session_      │
   │  (no session FK)      │  │  (无 rack_code FK)       │  │    correlation_id        │
   └────────────────────────┘  └──────────────────────────┘  └──────────────────────────┘
                │                             │                                  │
                └─────────────────────────────┴──────────────────────────────────┘
                                              │
                                              ▼
                          ┌──────────────────────────────────┐
                          │  device (设备接入)              │
                          │  - EVENT/COMMAND/RESULT          │
                          │  - 设备诊断状态                  │
                          │  - 接收 ECS/设备事件              │
                          └──────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────┐
   │  reconciliation (对账域)                                  │
   │  - ReconciliationManager                                  │
   │  - 触发矩阵: 投影冲突 / External callback / 设备状态 / drift│
   │  - 强制动作: evidence + RuntimeHold + 通知               │
   │  - 决议输出: resolution_decision + owner evidence         │
   │  - 状态转移: 各 owner 按 evidence 自行转移                │
   └──────────────────────────────────────────────────────────┘
```

### 3.7 目标态契约与 Legacy 处理

当前系统未发布，本重构不做向后兼容。旧 v0.6-v0.8 能力只用于提炼业务语义、构造 characterization tests 和识别数据风险，**不得**锁定旧 API、旧表名、旧 enum 或旧 plugin 框架。

| 旧能力/旧实现 | 可提炼的目标态业务语义 | 目标态处理 |
| --- | --- | --- |
| Start admission | 工作线启动前需要检查设备能力、资源边界、外部履约可用性 | 在 `runtime/orchestration` 中重建 admission flow；旧 service 可删除 |
| Runtime monitor command status | 运行态需要可查询命令状态、trace 和最近事件 | 由 RuntimeTimeline / DeviceResult / EffectDispatchResult 聚合生成 |
| SMT inbound handoff | 入料交接需要 manifest 驱动、事件驱动、可追溯 | 作为 runtime capability 重建，不保留旧插件接口 |
| C0 resource projection | 作业期资源投影必须可查询、可追溯、可对账 | 保留业务语义；允许破坏性调整 schema/API |
| Manifest topology YAML | WorkLine manifest 是配置源 | 保留 manifest 概念，字段按目标态 WorkLine manifest 重整 |
| WMS typed port (Inventory + Confirm) | WMS ACL 必须以 typed port 隔离外部协议 | 可复用现有代码，但允许破坏性清理 import/API |
| 旧 BinTransitMembership 8 队列 | 料箱在滚筒线队列中的 active membership | 以 manifest 动态队列重建为 `ConveyorQueueMembership` |
| External callback normalize + circuit breaker | 外部回调需要规范化、签名、幂等和熔断 | 提升为 callback ACL 标准能力，并按 `WmsEventPort` / `DeviceEventPort` 分流 |

**测试门禁**：建立行为契约测试，而不是追求旧 service 100% 行覆盖。测试覆盖“业务语义不丢”，不保护旧代码形态。

**破坏性变更原则**：删除旧入口、重命名表、重建 enum、迁移包路径均允许；每个 PR 只需说明目标态替代路径、数据清理方式和回滚数据库快照策略。

**队列模型最终决策（WMS_INTEGRATION_BOUNDARY 回归）**：

- 采用破坏性方案 B：物理表重建/重命名为 `conveyor_queue_memberships`，不保留 `bin_transit_memberships` 作为长期表或兼容视图。
- `BinTransitQueue` enum 删除，替换为 manifest 驱动的 `queue_code VARCHAR`；`queue_role` 仅作为进入队列时的 role 快照，用于展示、审计和迁移追溯，不参与硬编码流程。
- 旧 `BinTransitMembership` 只允许在一次性 migration 中抽取 active evidence；迁移完成后旧模型、旧 enum、旧 import 路径全部删除。
- ADR 0001 中关于保留 `BinTransitMembership` 的 taste 决策被本文目标态覆盖；ADR 必须在 Phase 0 更新为 `Superseded by workline-and-plugin-restructuring v4`。

### 3.8 4 方案决策表

| 方案 | 范围 | 风险 | 回归保护 | 迁移成本 | 决定 |
| --- | --- | --- | --- | --- | --- |
| **A：workline 单体 + 端口化 WMS** | 仅抽粗粒度 fulfillment family port | 低 | 中 | 小（1-2 周） | 不选 |
| **B（本设计）：目标态重写** | 拆 workline / runtime-orchestration / wms_integration ACL，清理 plugin 体系 | 中-高 | 行为契约测试 + 分阶段验证 | 大（XL，2-3 月） | **选择** |
| **C：增量 ACL only** | 只增强 wms_integration 内部（补 5 套缺失 port） | 低 | 高 | 中（2-3 周） | 不单独选择；可复用其 ACL 工作 |
| **D：port-only refactor** | 保持 runtime 在 workline 内 | 中 | 中 | 中（1-2 月） | 备选 |

**B 方案启动条件**（go/no-go 指标）：

- 目标态域边界、状态所有权、WMS ACL、队列动态配置模型已锁定
- legacy characterization tests 覆盖关键业务语义
- 破坏性清理清单发布：delete / rebuild / move / keep
- ENG-001 ExecutionCorrelation migration matrix 发布
- ENG-007 plane 接口 RBAC + 脱敏 + 审计已上线

> **状态所有权图**：详见 §13.8 域间引用关系 ASCII 图（单一真源）；§3.3 已给出 runtime/orchestration 内部聚合视图。

---
