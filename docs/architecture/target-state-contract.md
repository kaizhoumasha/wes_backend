---
status: Phase 0 锁定合同
created_at: 2026-06-25
parent: docs/architecture/workline-and-plugin-restructuring.md
spec: docs/superpowers/specs/2026-06-25-workline-restructuring-phase-0-spec.md
audience: 后端实现 agent、reviewer、WMS/ECS 集成 lead
note: |
  本文件从顶层设计抽取可执行目标态合同，不复制完整顶层设计。
  目标态业务能力语义、域边界、状态所有权和外部 port 在此锁定；
  旧 API / 旧表名 / 旧插件形态不得反向约束本合同。
  字段级 schema、状态机转移表、HMAC canonical 等实施细节留对应 Phase SPEC。
---

# WorkLine 重构目标态合同（Phase 0 锁定）

> 父设计：`docs/architecture/workline-and-plugin-restructuring.md`（Draft v4）
> 本合同是 Phase 0 的可执行边界真源；任一条款与父设计冲突时以父设计为准，并在此同步修正。

## 1. 编写目的

本文档锁定 WORKLINE + PLUGIN 体系重构的**目标态业务能力、域边界、状态所有权、事实权威来源和破坏性删除范围**，使后续 Phase 1-5 实现者无需回读顶层设计即可判断边界。

回答四个问题（SPEC P0-001 验收要求 1）：

1. **WES 做什么** —— §2 P0 系统能力
2. **WES 不做什么** —— §7 不做清单
3. **谁是外部权威** —— §4 Authority Matrix
4. **内部各域拥有什么状态** —— §3 域边界与状态所有权

## 2. P0 系统能力

P0 必须支撑以下能力（每条都是验收项；来源主计划 §2.2）：

| # | 能力 | 目标态落点 |
| --- | --- | --- |
| 1 | **配置** | 配置一条工作线的物理与逻辑能力：滚筒线、队列、入口/出口、设备角色、工作位、资源边界和平面布局 |
| 2 | **会话** | 接收一次作业上下文，建立 `ExecutionSession`，按事件推进作业状态 |
| 3 | **设备** | 通过 ECS/设备上位机 API 下发业务命令，接收设备事件和执行结果 |
| 4 | **WMS 反腐** | 通过 `wms_integration` ACL 域查询或引用 WMS 主数据、单据、库存和履约能力 |
| 5 | **履约** | 通过外部履约 port 请求搬运、补给、移出、换面、投箱、取箱等现场动作 |
| 6 | **投影** | 维护 WES 作业期资源投影（工作位货架/料箱/格位/料盘占用、滚筒线队列 membership、外部搬运中对象） |
| 7 | **作业对象查询** | 回答某条 WorkLine 当前正在处理哪些料箱和料盘 |
| 8 | **物料位置查询** | 回答某个或某类物料在 WES 作业期内的位置 |
| 9 | **平面态势** | 为前端提供 WorkLine 平面态势图所需的 `PlaneSceneView + PlaneSnapshot` |
| 10 | **可恢复** | 为异常、冲突和外部拒绝提供 trace、evidence、hold、reconciliation 入口 |

### 2.1 行为不变量（Phase 0 锁定，Phase 1+ 实现遵循）

| 不变量 | 约束 |
| --- | --- |
| Start admission | 启动前必须校验 WorkLine manifest 有效、设备角色能力满足、必要 WMS/ECS port 可用、active projection 不存在阻塞冲突 |
| Pipeline concurrency | `ExecutionSession` 不能成为整条 WorkLine 的串行锁；料盘、物料、料箱和履约子项必须以 `ExecutionWorkItem` 或等价对象级令牌独立推进 |
| Handoff | 任何物料/料箱/货架交接必须以 External callback 或 `RuntimeIntentLog` evidence 推进，禁止 API 层直接改投影 |
| Resource projection | 同一 object 在同一 WorkLine 内只能有一个可解释的 active 归属；瞬态冲突必须带 `transient_until`，超时进入 `RECONCILING` |
| Device command | 每个物理动作必须有 `command_code + idempotency_key + request_hash + callback result` 闭环 |
| WMS fulfillment | 任何外部履约必须有 11 态机状态、timeout、callback/evidence 和失败恢复路径 |

> 完整行为不变量见主计划 §2.2；11 态机完整转移表留 Phase 3 `fulfillment-state-machine-spec.md`。

## 3. 域边界与状态所有权

### 3.1 域结构（来源主计划 §3.2）

| 域 | 路径 | 角色 | 状态所有权 |
| --- | --- | --- | --- |
| `workline`（配置域） | `src/app/workline/` | 滚筒线/队列/入口/出口/设备角色/资源边界/平面布局/启停配置生命周期 | **仅配置**（无运行状态） |
| `runtime/orchestration`（执行域） | `src/app/runtime/orchestration/`（新建） | ExecutionSession/Inbox/Timeline/Hold、RuntimeIntentLog effect ledger、EffectPort dispatcher | **唯一 session PK 拥有者** |
| `handling` | `src/app/handling/` | 搬运意图、请求生命周期、幂等、超时、重试 | 搬运请求状态 |
| `resource` | `src/app/resource/` | 作业期运行投影（不复制 WMS 主数据） | active projection 状态 |
| `material` | `src/app/material/`（新建；旧 `workline/models/material_unit.py` 迁出或删除） | 料盘/物料 WES 作业期根实体 | material_units 身份 + 作业期业务状态 |
| `device` | `src/app/device/` | ECS/设备上位机接入点、角色、EVENT/COMMAND/RESULT | 设备诊断状态 + command/result/event ledger |
| `wms_integration` | `src/app/wms_integration/`（已存在，扩展） | WMS 反腐层：能力面 ports + ACL | WMS 外部事实的 evidence，不复制主数据 |
| `reconciliation` | `src/app/reconciliation/`（新建） | RECONCILING 冲突登记、隔离、决议输出、审计 | 冲突 evidence + `resolution_decision` |

**命名约束**（主计划 §3.2）：`wms_integration` 作为模块名保留（已是 ACL，不重命名 `external/wms`）；条件触发的外部系统用镜像命名 `rcs_integration/`、`agv_integration/`、`ctu_integration/`，当前阶段不建包不占位。

### 3.2 状态所有权矩阵（来源主计划 §2.6 验收 + §3.3）

任一核心对象必须明确状态所有权（SPEC P0-001 验收要求 5）：

| 对象 | 状态 owner | owner 域 | 跨域引用方式 |
| --- | --- | --- | --- |
| WorkLine 配置 | WorkLine | `workline`（配置域） | workline_id 强 FK（配置域允许） |
| Runtime 执行状态 | ExecutionSession / ExecutionWorkItem | `runtime/orchestration` | `ExecutionCorrelation.correlation_id`（跨域不得持 `execution_session.id`） |
| Handling 业务意图 | HandlingOperation / HandlingMove | `handling` | `correlation_id`（无 session FK） |
| Resource 投影 | active projection（RackPlacement / BinPlacement / ConveyorQueueMembership） | `resource` | `correlation_id` + typed `ExternalReference` |
| Material 作业期实体 | material_units | `material`（WES 自有根实体） | `current_session_correlation_id` |
| Device 事件命令 | DeviceCommand / DeviceEvent / DeviceResult / DeviceRuntime | `device` | `correlation_id`（command_code 幂等） |
| WMS 外部事实 | evidence（不复制主数据） | `wms_integration` | typed `EvidenceEnvelope` + `source_event_id` |

**核心约束**（主计划 §3.3）：

- 跨域读写都通过 `ExecutionCorrelation.correlation_id`，不通过 `execution_session.id`
- runtime 域内才使用 `execution_session_id` 强 FK；其他域只持 `correlation_id` 引用
- `RuntimeIntentLog` 是 effect proposal / outbox log，**不是状态源**；下游状态仍归各 owner 域
- 跨域 session FK 收敛策略详见 `session-correlation-matrix.md`（P0-004）

### 3.3 ExecutionSession 与 RuntimeIntentLog 显式拆分（来源主计划 §3.3）

| 聚合 | 职责 | 不可被反查为 |
| --- | --- | --- |
| `ExecutionSession` | 状态所有权，session PK 拥有者（含 RuntimeInbox / RuntimeTimeline / RuntimeHold / ExecutionCorrelation） | —— |
| `RuntimeIntentLog` | effect proposal / outbox log，记录"Runtime 曾尝试发出什么意图"，经 EffectPort dispatch 到下游 | "意图对应的状态"——下游状态归各 owner |

## 4. Authority Matrix（事实权威来源）

WES 不是所有外部事实的唯一权威。**按事实类型拆分权威来源**——WES 只聚合 evidence 和冲突状态（来源主计划 §3.4）。

| 事实类型 | 权威系统 | WES 角色 | WES 写入 |
| --- | --- | --- | --- |
| 库存数量、批次、有效期 | WMS | 引用 + 作业期快照 | 只读 evidence + 短暂快照缓存（TTL 30s） |
| 入库单 / 出库单 / 批次单 / 波次 / 业务任务 | WMS | 外部引用 + 执行上下文 | 不复制为 WES 单据主档 |
| 设备到位信号（光电、接近开关、扫码） | ECS/device | 接收 + 转换 | evidence + transition events |
| 设备业务命令结果 | ECS/device runtime | 接收 + 诊断 | RESULT + 设备诊断状态 |
| 硬件防呆、安全回路、急停、复位、物理坐标/关节控制 | ECS/现场安全系统 | **只感知，不控制** | 只写 event/evidence/hold，不下发安全控制或坐标级指令 |
| 设备事件/任务结果回调 | ECS/device callback | normalize + dispatch | typed evidence + RuntimeInbox + device projection |
| AGV/CTU 履约状态与位置 evidence | WMS/RCS fulfillment callback | 引用履约回调 evidence | 触发 handling 派生状态，不复制实时位置或 SDK 状态 |
| 货架/料箱/库位主数据 | WMS | 引用 + 作业期投影 | 不复制主数据，只维护 active projection |
| WMS 回调事件（WMS 主动推送） | WMS callback | normalize + dispatch | typed evidence + correlation key |
| 冲突、对账、RECONCILING 决议 | WES ReconciliationManager | 冲突记录 + 决议权威 | RECONCILING evidence + `resolution_decision`；恢复动作由各 owner 按 evidence 执行 |
| WES 作业期料盘/物料根实体 | **WES material 域（WES 自有）** | 根实体拥有者 | material_units 身份 + 作业期业务状态；位置摘要只读投影只能由 `RuntimeLocationEvent` 更新 |

**不变量**（主计划 §3.4）：WES 内部域（workline / runtime / handling / resource / material / device）**不能**直接依赖 PLC/RCS/AGV-CTU SDK 或 WMS HTTP client。设备事实经 `device` 域，搬运事实经 `wms_integration` 端口；RCS/AGV/CTU 直连仅作条件触发扩展（主计划 §10.5），生产前默认不触发、不预留代码骨架。

## 5. Plane 读模型边界（来源主计划 §5.2 + §2.6 验收）

**锁定**（SPEC P0-001 验收要求 6）：前端最小平面态势图只消费后端 `PlaneSceneView + PlaneSnapshot`，不得直接拼接 resource/material/device/runtime 散表。

| 接口 | 用途 | Phase 0 范围 |
| --- | --- | --- |
| `GET /worklines/{id}/plane/scene` | manifest 派生场景读模型 | 锁定消费边界 |
| `GET /worklines/{id}/plane/snapshot` | active projection 派生运行态读模型 | 锁定消费边界 |
| `GET /worklines/{id}/plane/events` | SSE 增量事件流 | 后续，不强制 |

**安全门禁**（后续 Phase 落地，Phase 0 只锁定要求）：

- 鉴权拆 `biz:workline:view-plane-scene` / `biz:workline:view-plane-snapshot`
- 行级过滤 + 脱敏（`pkg_code` 后 4 位掩码、`bin_code` 前缀掩码）
- 每次 plane 读取写 `audit_logs`

> `PlaneSceneView` / `PlaneSnapshot` 完整 schema 留 Phase 3 `plane-read-model-spec.md`。

## 6. WMS/RCS 集成边界（来源主计划 §3.5）

| 集成约束 | 目标态落点 |
| --- | --- |
| RCS 调度仍由 WMS 统一调度 | `WmsFulfillmentPort`——WES 生成搬运需求并提交 WMS；WMS 调 RCS，结果经 WMS 回传 |
| PDA 仅对接 WMS | `WmsEventPort` / `WmsDocumentPort`——WES 不做 PDA API |
| 自动化设备只通过 WES 接入 | `device` 域——WMS 不直连设备 |
| WES 不同步基础数据 | `WmsMasterDataPort` / `WmsInventoryQueryPort`——按需查询，可短 TTL 缓存 |
| WMS 是库存唯一真实源 | Authority Matrix——库存事务必须以 WMS 提交成功为准 |
| 外部输入统一进入 callback | callback → `RuntimeInbox`——只校验、落原始日志、ACK、写 inbox，不直接改 session |

**Runtime capability 注入边界**（主计划 §3.5）：

- `wms_integration` 只能作为 ACL provider 注册到端口注册表
- Runtime capability 只能依赖领域级 port contract，**不能**直接依赖 `wms_integration`/`device` 实现对象、HTTP client、DTO、异常、service locator
- QueryPort（只读）/ EffectPort（出站副作用，必须先写 `RuntimeIntentLog`）/ InboundEventPort（只写 `RuntimeInbox`）三类注入边界严格分离

**7 个目标 WMS port**（主计划 §5.1，字段留 Phase 1 `wms-integration-ports-spec.md`）：
`WmsMasterDataPort` / `WmsDocumentPort` / `WmsInventoryQueryPort` / `WmsInventoryTransactionPort` / `WmsFulfillmentPort` / `WmsEventPort` / `WmsReconciliationQueryPort`。

## 7. 不做清单（来源主计划 §2.3）

| # | 不做 | 原因 |
| --- | --- | --- |
| 1 | 复制 WMS 货架、料箱、库位、库存、单据和批次主数据 | Authority Matrix：WMS 是库存主数据权威 |
| 2 | 替代 WMS 做全局库存查询、库存规划、货架/箱位选择或波次规划 | WES 是执行中台，不做规划 |
| 3 | 直接把 AGV/CTU 作为唯一履约模型 | AGV/CTU 只是外部履约 provider 的可能实现 |
| 4 | 把 WorkLine 建成运行状态所有者 | WorkLine 只拥有配置 |
| 5 | 把完整数字孪生作为首版目标 | 首版只做平面态势图 |
| 6 | 沿用旧 WorkLine 插件执行体系作为新架构基础 | 旧 plugin 只作为业务事实样本 |
| 7 | 为旧 API、旧表名、旧插件形态做向后兼容 | 系统未发布，重构以清爽目标态为准 |
| 8 | 用本地 active projection 冒充 WMS 全局库存 | 影子 WMS 风险（Authority Matrix） |
| 9 | 本阶段直接调度 RCS / AGV / CTU | RCS 仍由 WMS 统一调度；直连仅作条件触发扩展，生产前默认不做 |
| 10 | 让 WMS 直连自动化设备 | 自动化设备只通过 WES 接入 |
| 11 | 让 WES 承接 PDA 应用交互 | PDA 仅对接 WMS |
| 12 | WES 直接控制 PLC、下发物理坐标、关节角度或安全回路指令 | WES 与 ECS/设备上位机通过 API 交互 |
| 13 | 在设备 Event_Push HTTP 响应中直接返回动作指令 | Event_Push 只 ACK；后续动作必须经 DeviceCommand 下发 |
| 14 | 在 WES 内建 NG 周转箱/库位主数据、返工工单主档或 PDA 离线原生流程 | 归 WMS/MES/PDA 体系；WES 只保留 RuntimeHold、evidence 和解除条件 |

## 8. 破坏性删除范围与 Legacy 处理原则

### 8.1 目标态契约优先（来源主计划 §3.7）

当前系统未发布，本重构**不做向后兼容**。旧 v0.6-v0.8 能力只用于提炼业务语义、构造 characterization tests 和识别数据风险，**不得**锁定旧 API、旧表名、旧 enum 或旧 plugin 框架（SPEC P0-001 验收要求 2/3）。

### 8.2 允许破坏性删除的范围

| 旧形态 | 目标态处理 |
| --- | --- |
| 旧 API 路径 | 删除或破坏性替换，不保留兼容入口 |
| 旧表名 / 旧 enum | 重命名或重建，不做 rename 兼容层 |
| 旧 plugin 框架 | 不迁移插件框架；能力以目标态 port/capability 重建 |
| 旧 `BinTransitMembership` 8 队列 enum | 删除，替换为 manifest 驱动的 `ConveyorQueueMembership`（`queue_code VARCHAR`） |
| 旧跨域 `session_id` FK | 收敛为 `ExecutionCorrelation.correlation_id`（见 P0-004 矩阵） |

### 8.3 Legacy 仅作为业务事实样本

| 旧资产 | Phase 0 用途 | 明确禁止 |
| --- | --- | --- |
| `src/app/workline/`、`src/workline_runtime/`、`src/workline_plugins/` | legacy inventory、业务事实提取、characterization 输入 | 作为目标态 runtime/plugin 架构基础继续继承 |
| `tests/workline_runtime/`、`tests/workline_plugins/` | 提取业务语义、生成 contract fixture | 用旧测试覆盖率替代目标态 contract test |
| `src/app/workline/models/inbox.py`（旧 `WorklineInbox`） | 旧 inbox 行为的 characterization 来源 | 反向决定目标态 `RuntimeInbox` 状态命名 |

> 逐入口清理策略见 `legacy-cleanup-matrix.md`（P0-002）。

## 9. 4 方案决策（来源主计划 §3.8）

| 方案 | 决定 |
| --- | --- |
| A：workline 单体 + 端口化 WMS | 不选 |
| **B：目标态重写（本设计）** | **选择** |
| C：增量 ACL only | 不单独选择；可复用其 ACL 工作 |
| D：port-only refactor | 备选 |

**B 方案启动条件**（go/no-go）：目标态域边界、状态所有权、WMS ACL、队列动态配置模型已锁定；legacy characterization tests 覆盖关键业务语义；破坏性清理清单发布；ExecutionCorrelation migration matrix 发布；plane 接口 RBAC + 脱敏 + 审计已上线。

## 10. 后续 Phase SPEC 触发清单（来源主计划 §13.1）

本合同只锁定目标态边界，实施细节留对应 Phase SPEC：

| Phase | 触发 SPEC | 本合同锁定项 |
| --- | --- | --- |
| Phase 1 | `wms-integration-ports-spec.md`、`runtime-orchestration-spec.md` | 7 WMS port、ExecutionCorrelation、域边界 |
| Phase 3 | `fulfillment-state-machine-spec.md`、`plane-read-model-spec.md`、`external-callback-auth-spec.md`、`reconciliation-manager-spec.md` | 11 态机、Plane schema、HMAC、RECONCILING |
| Phase 4 | `material-location-query-spec.md`、`sorter-inbound-capability-spec.md` | 作业对象查询、分拣机入库语义 |
| Phase 5 | `legacy-cleanup-execution-plan.md` | 破坏性删除范围 |

## 11. 验收

本合同满足 SPEC P0-001 验收要求：

1. ✅ 文档独立回答 "WES 做什么（§2）、不做什么（§7）、谁是外部权威（§4）、内部各域拥有什么状态（§3）"
2. ✅ 不出现 "兼容旧 API/旧表/旧插件" 作为目标（§8.1 明确不做向后兼容）
3. ✅ 旧代码仅作为业务事实样本和 characterization 输入（§8.3）
4. ✅ Authority Matrix 覆盖 WMS 主数据/库存/单据、ECS/device 事件命令、WES 作业期投影与 runtime 状态的权威来源（§4）
5. ✅ 状态所有权矩阵覆盖 7 类核心对象（§3.2）
6. ✅ Plane 边界锁定为 `PlaneSceneView + PlaneSnapshot` 消费边界，不展开 Phase 3 schema（§5）
