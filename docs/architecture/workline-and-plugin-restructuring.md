---
status: Draft v4 — 概要/详细设计（GB/T 8567 风格）
created_at: 2026-06-23
updated_at: 2026-06-24
parent_goal: 对当前 WORKLINE + PLUGIN 体系进行全面重构/重做
document_type: 概要设计说明书 + 详细设计（Outline Design + Detailed Design）
audience: eng/arch lead, WES owner, WMS 集成 lead, code reviewer
related_specs:
  - docs/superpowers/specs/2026-06-19-workline-multi-object-state-machine-design.md  (子设计)
  - docs/superpowers/specs/2026-06-23-workline-c0-resource-projection-foundation.md  (C0 子基础)
detail_docs:
  - 关键决策（ADR）：docs/architecture/adr/workline-restructuring/
  - 评审存档：docs/architecture/reviews/
note: |
  本设计采用 GB/T 8567 概要设计说明书 + 详细设计的标准结构。
  实施细节（字段定义、状态机转移表、HMAC 合同等）不在本文展开为独立 SPEC；
  将在对应 Phase 启动前或启动时按需生成 SPEC。
review_summary: |
  autoplan 评审已存档到 docs/architecture/reviews/。
  28 auto-decision 已记录到 docs/architecture/reviews/decision-audit-trail.md。
  本轮修订明确：当前系统未发布，本重构不做向后兼容；旧 WorkLine/plugin 体系只作为业务事实和测试样本输入，不作为目标态约束。
  Critical path: 目标态边界锁定 → WMS ACL → Runtime/Orchestration 骨架 → plane 最小闭环 → legacy 清理。
---

# WORKLINE + PLUGIN 体系全面重构顶层设计

> 概要设计说明书（GB/T 8567 风格）+ 详细设计
> 版本：Draft v4（2026-06-24）
> 父目标：对当前 WORKLINE + PLUGIN 体系进行全面重构/重做

---

## 1. 引言

### 1.1 编写目的

本文档是 WORKLINE + PLUGIN 体系全面重构的**顶层设计说明书**，目的是：

- 明确重构的**父目标、P0 系统目标、明确不做**（§2）
- 描述重构的**总体体系结构**（域拆分、状态所有权、Authority Matrix、目标态契约、4 方案决策）（§3）
- 给出**数据设计**（核心实体、ExecutionCorrelation、typed ExternalReference/EvidenceEnvelope、数据迁移）（§4）
- 给出**接口设计**（wms_integration 能力面、plane 接口、External callback、idempotency 规范、域内 API 边界）（§5）
- 描述**状态与恢复设计**（11 态机 + 4 timeout + BLOCKED_BY_CB、RECONCILING 冲突决议模型、3 路 UNION 冲突 policy）（§6）
- 给出**安全设计**（威胁模型、plane RBAC、External callback HMAC、idempotency 跨域审计、关键不变量）（§7）
- 给出**非功能性设计**（性能、容量、可靠性、可观测性、可维护性）（§8）
- 给出**模块设计**（workline 配置域、runtime/orchestration 执行域、handling、resource、material、device、wms_integration、reconciliation 8 个模块的详细设计）（§9）
- 给出**实施计划**（5 Phase 路线图 + critical path + 总 Effort 估算）（§10）
- 给出**执行规范**（TDD 纪律、破坏性迁移规范、评审制度、命名规范、Legacy 清理规范、工具与命令规范）（§11）
- 列出**风险与对策**（4 CRITICAL、1 TASTE、2 事实修正、3 跨阶段主题）（§12）
- **附录**：实施细节 SPEC 触发清单、ADR 索引、现有相关文档、评审存档（§13）

### 1.2 范围

**本文档范围**：

- WORKLINE + PLUGIN 体系全面重构的**顶层设计**（含概要 + 详细）
- 8 个域的边界与责任：workline（配置域）/ runtime/orchestration（执行域）/ handling（搬运意图）/ resource（运行投影）/ material（WES 根实体）/ device（设备接入）/ wms_integration（WMS ACL）/ reconciliation（对账）
- 7 个目标 WMS port（MasterData / Document / InventoryQuery / InventoryTransaction / Fulfillment / Event / Reconciliation）
- 5 Phase 实施路线图

**本文档不包含**（实施 SPEC 阶段展开）：

- 各 port 详细字段定义（Phase 1 启动时写 `wms-integration-ports-spec.md`）
- 11 态机完整转移表（Phase 3 启动时写 `fulfillment-state-machine-spec.md`）
- HMAC canonical 字符串（Phase 3 启动时写 `external-callback-auth-spec.md`）
- PlaneSceneView/PlaneSnapshot 完整 schema（Phase 3 启动时写 `plane-read-model-spec.md`）
- ReconciliationManager 触发矩阵（Phase 3 启动时写 `reconciliation-manager-spec.md`）
- Runtime IntentLog/Session 拆分（Phase 1 启动时写 `runtime-orchestration-spec.md`）

### 1.3 术语与缩略语

| 术语 | 定义 |
| --- | --- |
| WES | Warehouse Execution System，本系统，仓储现场自动化执行中台 |
| WMS | Warehouse Management System，外部权威系统，持有库存/单据/库位主数据 |
| RCS | Robot Control System，当前阶段由 WMS 统一调度；WES 只消费 WMS/RCS 履约回调 evidence，未来直连时才通过 provider adapter 引入状态权威 |
| AGV / CTU | Automated Guided Vehicle / Container Transfer Unit，外部搬运设备 |
| PLC | Programmable Logic Controller，ECS 内部控制组件；WES 不与 PLC 通讯 |
| WorkLine | 工作线配置域的根实体；只拥有配置，不拥有运行状态 |
| Runtime | 工作线执行域（重构后）；拥有 ExecutionSession 等状态 |
| ExecutionSession | runtime/orchestration 的会话聚合根；一次作业的执行会话 |
| RuntimeIntentLog | 运行时输出的下一步意图记录（effect proposal / outbox log，**不是**状态源） |
| ExecutionCorrelation | 跨域 correlation key，替代 `execution_session.id` 跨域 FK |
| EffectPort | Runtime 向 handling/device/resource/material/wms_integration 分发副作用的稳定接口 |
| ConveyorQueueMembership | 料箱在滚筒线队列中的 runtime active 投影；队列由 WorkLine manifest 动态定义 |
| WmsFulfillmentPort | WMS 履约能力 port（11 态机） |
| PlaneSceneView | WorkLine 平面态势场景读模型（manifest 派生） |
| PlaneSnapshot | WorkLine 平面态势运行态读模型（active projection） |
| RECONCILING | 投影/回调/现场状态冲突的"待对账"状态 |
| ReconciliationManager | 登记 RECONCILING 冲突、执行隔离、产出 resolution_decision 并审计；不直接写跨域 owner 状态 |
| idempotency_key | 跨域幂等键（复合主键 `(provider_code, operation_kind, idempotency_key)`） |
| HMAC | Hash-based Message Authentication Code，External callback body 签名 |
| nonce | 一次性随机数，External callback 5 分钟 TTL 去重 |
| ECS | Equipment Control System，第三方设备上位机/中间件，负责设备侧物理控制与坐标映射 |
| DeviceCommand | WES 下发给 ECS 的业务命令，不是 PLC、坐标或关节级控制指令 |
| DeviceDispatchPolicy | DeviceCommand 的目标态调度策略：能力匹配、优先级、deadline、串行、取消和限流 |
| RuntimeInbox | 外部 callback / 内部事件进入 Runtime 的持久 inbox；ACK-before-processing 的恢复边界 |
| RuntimeLocationEvent | WES 作业期对象位置变化事实；按 what/where/when/why/source 记录 evidence，并投影为当前平面态势 |
| trace_id | 跨域追踪标识 |
| ACL | Anti-Corruption Layer，反腐层（隔离外部协议） |
| Port | 端口，稳定的依赖倒置接口 |
| target-state contract | 目标态契约；只锁定业务能力语义和新架构边界，不锁定旧 API / 旧表名 / 旧插件形态 |
| ADL | Acceptance-Driven Development，autoplan CEO 用的"答案驱动"风格 |
| BLOCKED_BY_CB | 11 态机中因 circuit breaker open 而阻塞的状态 |
| ActiveObjectRegistry | 跨投影唯一 active 归属读模型视图 |
| PlaneSceneView / PlaneSnapshot | WorkLine 平面态势前后端读模型 |

### 1.4 参考资料

| # | 文档 | 说明 |
| --- | --- | --- |
| 1 | `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md` | 现有 ADR：WES/WMS/RCS 运行时资源边界 |
| 2 | `docs/architecture/adr/2026-05-26-wms-integration-domain.md` | 现有 ADR：WMS 对接辅助域 |
| 3 | `docs/superpowers/specs/2026-06-19-workline-multi-object-state-machine-design.md` | 状态机子设计 |
| 4 | `docs/superpowers/specs/2026-06-23-workline-c0-resource-projection-foundation.md` | C0 资源投影子基础 |
| 5 | `docs/architecture/ARCHITECTURE_EVOLUTION_ROADMAP.md` | 季度级演进路线图 |
| 6 | `docs/architecture/REPOSITORY_GUIDE.md` | 通用 Repository 使用指南 |
| 7 | `docs/architecture/SRS.md` | 软件需求规格说明书 |
| 8 | `docs/integration/wms_rcs_interface_requirements.md` | WES 对 WMS/RCS 接口需求分析：本阶段集成边界、基础数据、标准回调、WES→WMS 指令 |
| 9 | `docs/integration/third_party_integration_whitepaper.md` | 第三方设备接入白皮书：WES 与 ECS/设备上位机的 API 边界、Command-Ack-Callback、逻辑位置约束 |
| 10 | `docs/architecture/reviews/autoplan-workline-restructuring-2026-06-23.md` | autoplan CEO/Design/Eng 评审全文 |
| 11 | `docs/architecture/reviews/decision-audit-trail.md` | 28 auto-decision 审计 |
| 12 | `docs/architecture/adr/workline-restructuring/0001-0008` | 8 个关键决策 ADR |

### 1.5 缩略语

| 缩略语 | 全称 |
| --- | --- |
| ACL | Anti-Corruption Layer |
| ADL | Acceptance-Driven Development |
| AGV | Automated Guided Vehicle |
| API | Application Programming Interface |
| CB | Circuit Breaker |
| CTU | Container Transfer Unit |
| FK | Foreign Key |
| HMAC | Hash-based Message Authentication Code |
| LOC | Lines of Code |
| PLC | Programmable Logic Controller |
| RCS | Robot Control System |
| RBAC | Role-Based Access Control |
| SHA | Secure Hash Algorithm |
| TTL | Time To Live |
| WES | Warehouse Execution System |
| WMS | Warehouse Management System |

---

## 2. 系统概述

### 2.1 父目标

**对当前 WORKLINE + PLUGIN 体系进行全面的重构/重做**。

WES 目标是成为**仓储现场自动化执行中台**：承接 WMS 下发或查询得到的业务上下文，编排工作线、设备和外部履约能力，维护 WES 作业期内可信的运行投影、请求生命周期和追溯证据，让现场作业**可执行、可观测、可对账、可恢复**。

### 2.2 P0 系统目标

P0 必须支撑以下能力（每条都是验收项）：

1. **配置**：配置一条工作线的物理与逻辑能力（滚筒线、队列、入口/出口、设备角色、工作位、资源边界和平面布局）
2. **会话**：接收一次作业上下文，建立 `ExecutionSession`，按事件推进作业状态
3. **设备**：通过 ECS/设备上位机 API 下发业务命令，接收设备事件和执行结果
4. **WMS 反腐**：通过 `wms_integration` ACL 域查询或引用 WMS 主数据、单据、库存和履约能力
5. **履约**：通过外部履约 port 请求搬运、补给、移出、换面、投箱、取箱等现场动作
6. **投影**：维护 WES 作业期资源投影（工作位货架/料箱/格位/料盘占用、滚筒线队列 membership、外部搬运中对象）
7. **作业对象查询**：回答某条 WorkLine 当前正在处理哪些料箱和料盘
8. **物料位置查询**：回答某个或某类物料在 WES 作业期内的位置
9. **平面态势**：为前端提供 WorkLine 平面态势图所需的 scene + snapshot
10. **可恢复**：为异常、冲突和外部拒绝提供 trace、evidence、hold、reconciliation 入口

**P0 行为不变量清单**：

- Start admission：启动前必须校验 WorkLine manifest 有效、设备角色能力满足、必要 WMS/ECS port 可用、active projection 不存在阻塞冲突。
- Runtime monitor：任一 active session 必须可查询当前 state、最近 timeline、未处理 inbox、active hold、pending intent 和关联 correlation。
- Handoff：任何物料/料箱/货架交接必须以 External callback 或 RuntimeIntentLog evidence 推进，禁止 API 层直接改投影。
- Resource projection：同一 object 在同一 WorkLine 内只能有一个可解释的 active 归属；瞬态冲突必须带 `transient_until`，超时进入 `RECONCILING`。
- Device command：每个物理动作必须有 `command_code + idempotency_key + request_hash + callback result` 闭环。
- WMS fulfillment：任何外部履约必须有 11 态机状态、timeout、callback/evidence 和失败恢复路径。
- Inbound flow baseline：分拣机/粗分机入库链路的业务语义必须被行为契约测试覆盖，包括扫码、测量或识别、WMS 校验、箱格分配、满箱/换架、NG、投箱和完成；旧插件接口、旧 context 字段和旧 fake allocator 不进入目标态合同。
- Full-box exchange：满箱、满货架、换空箱、换货架属于外部履约 + 对账闭环，必须走 callback + reconciliation 完成语义，不能按普通 `CALLBACK_TRUSTED` 搬运完成处理。

### 2.3 明确不做

| # | 不做 | 原因 |
| --- | --- | --- |
| 1 | 复制 WMS 货架、料箱、库位、库存、单据和批次主数据 | Authority Matrix 原则：WMS 是库存主数据权威 |
| 2 | 替代 WMS 做全局库存查询、库存规划、货架选择、箱位选择或波次规划 | WES 是执行中台，不做规划 |
| 3 | 直接把 AGV/CTU 作为唯一履约模型 | AGV/CTU 只是外部履约 provider 的可能实现 |
| 4 | 把 WorkLine 建成运行状态所有者 | WorkLine 只拥有配置 |
| 5 | 把完整数字孪生作为首版目标 | 首版只做平面态势图（PlaneSceneView/PlaneSnapshot） |
| 6 | 沿用旧 WorkLine 插件执行体系作为新架构基础 | 旧 plugin 只作为业务事实样本，新能力在 runtime 域以 port/capability 重建 |
| 7 | 为旧 API、旧表名、旧插件形态做向后兼容 | 系统未发布，重构以清爽目标态为准；旧入口允许删除或破坏性替换 |
| 8 | 用本地 active projection 冒充 WMS 全局库存 | 影子 WMS 风险（Authority Matrix） |
| 9 | 本阶段直接调度 RCS / AGV / CTU | `wms_rcs_interface_requirements.md` 明确 RCS 仍由 WMS 统一调度；直连能力只作为未来 provider adapter |
| 10 | 让 WMS 直连自动化设备 | 自动化设备只通过 WES 接入，WMS 不直连设备 |
| 11 | 让 WES 承接 PDA 应用交互 | PDA 仅对接 WMS；WES 如需感知 PDA 结果，由 WMS 事件推送或同步 |
| 12 | WES 直接控制 PLC、下发物理坐标、关节角度或安全回路指令 | `third_party_integration_whitepaper.md` 明确 WES 与 ECS/设备上位机通过 API 交互；ECS 自主完成物理控制、坐标映射和硬件防呆 |
| 13 | 在设备 Event_Push HTTP 响应中直接返回动作指令 | 白皮书要求 Event_Push 只 ACK；后续动作必须通过 Receive Command 下发，保证指令可追踪、可幂等、可审计 |
| 14 | 在 WES 内建 NG 周转箱/库位主数据、返工工单主档或 PDA 离线原生流程 | 这些能力归 WMS/MES/PDA 体系；WES 只保留 RuntimeHold、ExternalReference、evidence 和解除条件 |

### 2.4 当前体系规模（实测 2026-06-23）

| 范围 | 实测 LOC | 角色 | 在重构中的位置 |
| --- | --- | --- | --- |
| `src/app/workline/` | 32,979 | 混合"配置 + 执行 + 插件" | 保留配置 CRUD 思路；执行与插件能力破坏性迁出或删除 |
| `src/app/handling/` | 2,229 | 搬运意图 + 旧 BinTransitMembership | 只保留搬运意图语义；队列投影按目标态重建 |
| `src/app/wms_integration/` | 2,649 | typed port 760 行 + circuit breaker + callback normalize + cache + evidence | 复用可验证 ACL 能力，允许破坏性整理 import/API |
| `src/app/resource/` | C0 已落 | 资源投影基座 | 保留 + 对接 `PlaneSnapshot` |
| `src/workline_runtime/` | 10,241（legacy） | 旧 runtime，独立于 `src/app/` 根 | 业务事实提取后删除或重建 |
| `src/workline_plugins/` | 3,085（legacy） | 旧 plugin 体系 | 不迁移插件框架；能力以目标态 port/capability 重建 |
| **合计** | **~51,000** | 跨 5 个 module | — |

### 2.5 6 个子目标（重构的支撑机制）

| # | 子目标 | 关键产出 |
| --- | --- | --- |
| 1 | WES 顶层领域边界 | 拆 workline（配置域）+ runtime/orchestration（执行域）+ handling + resource + material + device + wms_integration |
| 2 | WMS 反腐层 (wms_integration ACL) | 7 个目标 port：MasterData / Document / InventoryQuery / InventoryTransaction / Fulfillment / Event / Reconciliation |
| 3 | Authority Matrix | 外部事实按事实类型拆分权威来源；WES material 域是唯一自有根实体 |
| 4 | 目标态契约 | 锁定目标业务能力、域边界、状态所有权和外部 port；不锁定旧 API/旧表 |
| 5 | 4 方案决策表 | A（workline 单体）/ B（本设计）/ C（增量 ACL）/ D（port-only）；选择 B，C 的可复用部分并入 B |
| 6 | implementation task | 5 阶段实施 roadmap（详见 §10） |

### 2.6 P0 验收标准

- [ ] 顶层边界文档能清楚回答"WES 做什么、不做什么、谁是外部权威、内部各域拥有什么状态"
- [ ] 任一核心对象都能明确状态所有权：WorkLine 配置、Runtime 执行、Handling 业务意图、Resource 投影、Material 作业期实体、Device 事件命令、WMS 外部事实
- [ ] 任一外部 WMS 能力都必须经 `wms_integration` port 进入系统，不能从内部领域直接依赖 WMS DTO/client
- [ ] 前端最小平面态势图只消费后端 `PlaneSceneView + PlaneSnapshot`，不直接拼散表
- [ ] Legacy 清理策略能指导从旧 WorkLine/plugin/runtime 体系提取业务事实、删除旧形态，而不是反向约束目标态
- [ ] 目标态契约落地，明确哪些业务能力必须保留，哪些旧 API/旧表/旧插件允许破坏性删除
- [ ] Authority Matrix 落地，外部事实的权威来源按事实类型显式拆分
- [ ] 行为契约测试基线建立：覆盖关键业务语义，不以旧 service 行覆盖率作为目标

**P0 验收分层**：

| 层级 | 目的 | 必须完成 | 不要求完成 |
| --- | --- | --- | --- |
| P0 文档验收 | 锁定目标态边界，防止旧体系反向约束重构 | 本节全部检查项、行为契约测试清单、Legacy 清理矩阵、ECS 设备边界合同 | 新 runtime 全量代码落地 |
| P0 最小可运行闭环（Phase 3 完成） | 证明新边界能跑通一条受控作业链路 | WorkLine manifest、ExecutionSession、RuntimeInbox、RuntimeIntentLog、DeviceCommand、WMS fulfillment、PlaneSnapshot、RECONCILING 最小闭环 | 分拣机/粗分机完整生产能力 |
| 完整业务能力 | 覆盖现场可交付业务闭环 | 分拣机/粗分机入库、SMT/NG/WMS 对账、MaterialLocationQuery、WorklineActiveObjects | 任何旧 plugin/API/表兼容承诺 |

后续实现阶段验收（不作为 P0 文档验收，但必须在对应 Phase 门禁中落地）：

- [ ] plane 接口安全门禁：`biz:workline:view-plane-scene` / `biz:workline:view-plane-snapshot` + 行级过滤 + 脱敏 + 审计
- [ ] External callback HMAC body 签名 + idempotency 复合主键 + typed `ExternalReference` 全部就绪
- [ ] RuntimeInbox 支持 ACK-before-processing 后的重试、死信、人工重放和幂等审计
- [ ] DeviceCommand 调度策略支持设备能力选择、优先级、deadline、串行/限流、取消和状态快照 TTL

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
| `runtime/orchestration`（执行域） | `src/app/runtime/orchestration/`（新建） | ExecutionSession/Inbox/Timeline/Hold、EffectPort、RuntimeIntentLog effect ledger | **唯一 session PK 拥有者** |
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
3. **Python 习惯**：包名=具体名词（`wms_integration`），不是分类前缀（`external.wms`）。镜像命名（未来加 `rcs_integration/`、`agv_integration/`）比父目录命名更 Pythonic。
4. **现有结构已经是 ACL 模式**：`services/typed_ports.py`（609 行）+ `models/ports.py`（151 行）已经是 typed port 抽象；`circuit_breaker_service.py` + `callback_normalizer.py` + `evidence.py` 都是 ACL 概念。
5. **与同目录其他域对齐**：`src/app/wms_integration/` 与 `src/app/handling/`、`src/app/resource/`、`src/app/device/` 同级（都是"外部接口/执行域"），不需要 `external/` 父目录。

**未来外部系统命名**：如 RCS / AGV / CTU provider 出现，使用**镜像命名**：

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
| 库存数量、批次、有效期 | WMS | 引用 + 作业期快照 | 只读 evidence + 短暂快照缓存（TTL 30s） |
| 入库单 / 出库单 / 批次单 / 波次 / 业务任务 | WMS | 外部引用 + 执行上下文 | 不复制为 WES 单据主档 |
| 设备到位信号（光电、接近开关、扫码） | ECS/device | 接收 + 转换 | evidence + transition events |
| 设备业务命令结果（机械臂取放、滚筒线动作） | ECS/device runtime | 接收 + 诊断 | RESULT + 设备诊断状态 |
| 硬件防呆、安全回路、急停、复位、物理坐标/关节控制 | ECS/现场安全系统 | 只感知，不控制 | 只写 event/evidence/hold，不下发安全控制或坐标级指令 |
| 设备事件/任务结果回调 | ECS/device callback | normalize + dispatch | typed evidence + RuntimeInbox + device projection |
| AGV/CTU 履约状态与位置 evidence | WMS/RCS fulfillment callback（未来 direct provider adapter） | 引用履约回调 evidence | 触发 handling 派生状态，不复制实时位置或 SDK 状态 |
| 货架/料箱/库位主数据 | WMS | 引用 + 作业期投影 | 不复制主数据，只维护 active projection |
| WMS 回调事件（WMS 主动推送） | WMS callback | normalize + dispatch | typed evidence + correlation key |
| 冲突、对账、RECONCILING 决议 | WES ReconciliationManager | 冲突记录 + 决议权威 | RECONCILING evidence + resolution_decision；恢复动作由各 owner 按 evidence 执行 |
| WES 作业期料盘/物料根实体 | WES material 域 | **WES 自有** | material_units 身份 + 作业期业务状态 + current_session correlation；位置只读摘要只能由 `RuntimeLocationEvent` 投影更新 |

**不变量**：WES 内部域（workline / runtime / handling / resource / material / device）**不能**直接依赖 PLC/RCS/AGV-CTU SDK 或 WMS HTTP client。设备事实经 `device` 域，搬运事实经 `wms_integration` 端口或未来 `rcs_integration` / `agv_integration` / `ctu_integration` 端口。WES 与固定式自动化设备只按 `third_party_integration_whitepaper.md` 通过 ECS/设备上位机 API 交互，不与 PLC 通讯，不控制坐标、关节、安全回路或硬件防呆。

### 3.5 WMS/RCS 集成约束映射

`docs/integration/wms_rcs_interface_requirements.md` 是本设计的集成边界输入。目标态不照搬其中旧 WorklineInbox / plugin 命名，但必须保留其外部系统边界和接口语义。

| 集成约束 | 来源章节 | 目标态落点 | 设计含义 |
| --- | --- | --- | --- |
| RCS 调度仍由 WMS 统一调度 | §1.2 | `WmsFulfillmentPort` | WES 生成搬运需求并提交 WMS；WMS 调 RCS，结果经 WMS 回传 |
| PDA 仅对接 WMS | §1.2 | `WmsEventPort` / `WmsDocumentPort` | WES 不做 PDA API；PDA 结果通过 WMS 事件或单据查询进入 |
| 自动化设备只通过 WES 接入 | §1.2 | `device` 域 | WMS 不直连设备；设备 EVENT/COMMAND/RESULT 归 WES |
| 标签打印按设备类型分流 | §1.2 | `device` 域 + `WmsDocumentPort` | 自动打印设备由 WES 下发；人工打印由 WMS 获取模板后回执 |
| WES 不同步基础数据 | §1.4 / §2 | `WmsMasterDataPort` / `WmsInventoryQueryPort` | 物料、区域、地码、货架、料箱、GRN、库存均按需查询，可短 TTL 缓存 |
| WMS 是库存唯一真实源 | §1.4 / §10.1 | Authority Matrix | 库存事务必须以 WMS 提交成功为准，WES 只维护作业期投影 |
| 外部输入统一进入 callback | §1.3 / §3 / §5 | `callback` → `RuntimeInbox` | WMS/RCS/ECS/device 回调 API 只校验、落原始日志、ACK、写 inbox，不直接改 session |
| WMS 事件必须幂等 | §3.4 / §10.1 | `idempotency_keys` | `request_id` 映射为 `source_event_id` / `idempotency_key` |
| WMS 调用超时与缓存策略 | §2.6 / §10.2 | `wms_integration` adapter | WMS 调用 10s 超时、指数退避、短 TTL 缓存、熔断告警 |

**接口口径调整**：旧文档中的 `WorklineInbox` 对应目标态 `RuntimeInbox`；旧 `WorklineOrchestrator` 对应目标态 `runtime/orchestration`；旧 plugin 决策对应目标态 runtime capability + EffectPort。

**未来直连 RCS/AGV/CTU**：当前阶段不实现。若后续绕过 WMS 直接对接 RCS/AGV/CTU，只能通过 `FulfillmentPort` provider adapter 替换，不允许内部域直接调用 SDK。

**未来 provider 差异化原则**：

- `RCS` provider 代表调度系统能力，可能同时调度 AGV/CTU/其他搬运设备；WES 只提交履约意图和接收状态。
- `AGV` provider 代表货架搬运能力，核心对象是 rack/work_position/location，不承载料箱格位操作。
- `CTU` provider 代表料箱搬运能力，核心对象是 bin/rack_cell/conveyor_entry/exit，不承载货架移动。
- provider 差异只能在 `FulfillmentPort` adapter 层表达；handling/runtime 不得因 provider 类型直接 import SDK 或分叉业务状态机。

#### Runtime capability 注入边界

**结论**：`wms_integration` 应进入 Runtime capability 注入体系，但只能作为 **ACL provider** 注册到端口注册表；Runtime capability / plugin 只能依赖领域级 port contract，不能直接依赖 `wms_integration` 的实现对象、HTTP client、DTO、异常或 service locator。

注入边界分两类：

| 类型 | 允许能力 | 约束 |
| --- | --- | --- |
| QueryPort | `WmsMasterDataPort` / `WmsDocumentPort` / `WmsInventoryQueryPort` 只读查询 | 用于作业决策前的外部事实查询；响应必须带 `scope/authority/source/evidence_at`；允许短 TTL 缓存，不产生外部状态变更 |
| EffectPort | `WmsInventoryTransactionPort` / `WmsFulfillmentPort` / `WmsEventPort` / `DeviceCommandPort` / `DeviceEventPort` / `WmsReconciliationPort` 及未来 provider adapter | 所有会改变外部状态、触发履约或确认事件的动作必须先写 `RuntimeIntentLog`，再经 EffectPort dispatch；必须有幂等、evidence、timeline 和 callback 闭环 |

禁止规则：

- `runtime capability` / `plugin` 直接 import `src.app.wms_integration.*` 的 DTO、异常、HTTP client 或 `service_locator`
- Runtime 直接调用 WMS HTTP client 绕过 `RuntimeIntentLog` / EffectPort
- `wms_integration` adapter 反向调用 Runtime domain service 修改 session/projection
- WMS/RCS/ECS/device callback 在 API 层直接修改 session/projection；callback 只能 normalize、校验、ACK、写 `RuntimeInbox`

**外部系统 ACL 命名约束（M7 回归）**：未来任何外部系统（`rcs_integration` / `agv_integration` / `ctu_integration` / provider-specific integration）一律以 `src/app/<system>_integration/` 镜像命名建立 ACL 域；禁止建立 `src/app/external/` 父目录，避免外部协议对象跨系统混放。

### 3.6 总体架构图

```text
                                ┌─────────────────────────────────────────────┐
                                │            wms_integration (ACL)               │
                                │  ┌──────────────────────────────────────┐  │
                                │  │  WmsMasterDataPort    WmsDocumentPort │  │
                                │  │  WmsInventoryQueryPort WmsInventoryTxPort │
                                │  │  WmsFulfillmentPort WmsEventPort WmsRecon │
                                │  └──────────────────────────────────────┘  │
                                │  reuse/clean src/app/wms_integration/        │
                                │  + 7 WMS port target-state ACL               │
                                │  + HMAC body 签名 / nonce TTL / typed envelope│
                                └─────────────────┬───────────────────────────┘
                                                  │ capability/query + effect port
                                                  ▼
┌────────────────────┐   ┌──────────────────────────────────────┐
│ frontend           │   │  runtime/orchestration (执行域)      │
│                    │   │  ┌────────────────────────────────┐   │
│  GET /worklines/   │   │  │ ExecutionSession              │   │
│   {id}/plane/scene │◄──┼──┤  (session aggregate / owner)   │   │
│  GET /worklines/   │   │  │  - RuntimeInbox / Timeline /   │   │
│   {id}/plane/      │   │  │    RuntimeHold                 │   │
│   snapshot         │   │  │  - ExecutionCorrelation        │   │
│  + 行级 + 脱敏 +   │   │  │    correlation_id (跨域唯一)   │   │
│  audit log         │   │  └────────────────────────────────┘   │
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

**队列模型最终决策（C1 回归）**：

- 采用破坏性方案 B：物理表重建/重命名为 `conveyor_queue_memberships`，不保留 `bin_transit_memberships` 作为长期表或兼容视图。
- `BinTransitQueue` enum 删除，替换为 manifest 驱动的 `queue_code VARCHAR`；`queue_role` 仅作为进入队列时的 role 快照，用于展示、审计和迁移追溯，不参与硬编码流程。
- 旧 `BinTransitMembership` 只允许在一次性 migration 中抽取 active evidence；迁移完成后旧模型、旧 enum、旧 import 路径全部删除。
- ADR 0001 中关于保留 `BinTransitMembership` 的 taste 决策被本文目标态覆盖；ADR 必须在 Phase 0 更新为 `Superseded by workline-and-plugin-restructuring v4`。

### 3.8 4 方案决策表

| 方案 | 范围 | 风险 | 回归保护 | 迁移成本 | 决定 |
| --- | --- | --- | --- | --- | --- |
| **A：workline 单体 + 端口化 WMS** | 仅抽 `WmsFulfillmentPort` | 低 | 中 | 小（1-2 周） | 不选 |
| **B（本设计）：目标态重写** | 拆 workline / runtime-orchestration / wms_integration ACL，清理 plugin 体系 | 中-高 | 行为契约测试 + 分阶段验证 | 大（XL，2-3 月） | **选择** |
| **C：增量 ACL only** | 只增强 wms_integration 内部（补 5 套缺失 port） | 低 | 高 | 中（2-3 周） | 不单独选择；可复用其 ACL 工作 |
| **D：port-only refactor** | 保持 runtime 在 workline 内 | 中 | 中 | 中（1-2 月） | 备选 |

**B 方案启动条件**（go/no-go 指标）：

- 目标态域边界、状态所有权、WMS ACL、队列动态配置模型已锁定
- legacy characterization tests 覆盖关键业务语义
- 破坏性清理清单发布：delete / rebuild / move / keep
- ENG-001 ExecutionCorrelation migration matrix 发布
- ENG-007 plane 接口 RBAC + 脱敏 + 审计已上线

### 3.9 状态所有权图（域间引用关系）

```text
WES 域间状态所有权图（按 ExecutionCorrelation 修复后）

runtime/orchestration
  +-- ExecutionSession (session aggregate / 状态 owner / 域内强 FK 根)
  |     +-- RuntimeInbox / RuntimeTimeline / RuntimeHold
  |     +-- ExecutionCorrelation.correlation_id (跨域唯一)
  |     +-- ExecutionCorrelation.execution_session_id (域内强 FK)
  +-- RuntimeIntentLog   (effect proposal / outbox log, NOT state)
        |
        v  EffectPort fan-out
        +--> wms_integration / WmsFulfillmentPort
        +--> device / DeviceCommandPort
        +--> handling / resource / material owner service

wms_integration  (760 行 typed port + 5 套新 port)
  +-- WmsMasterDataPort / WmsDocumentPort (新)
  +-- WmsInventoryQueryPort / WmsInventoryTransactionPort (由现有 Inventory port 拆分)
  +-- WmsFulfillmentPort / WmsEventPort / WmsReconciliationPort (新)
  +-- WmsFulfillmentRequest (外部履约 11 态机 owner)

handling
  +-- HandlingOperation / HandlingMove (业务意图状态 owner)
  |     correlation_id -> ExecutionCorrelation  (无 execution_session.id FK)
  |     通过 WmsFulfillmentRequest evidence 派生粗粒度状态，不双写 11 态机

resource  (active projection only)
  +-- correlation_id -> ExecutionCorrelation
  +-- typed ExternalReference (rack_code / bin_code / location_code, no FK)
  +-- typed evidence envelope (schema_version/source_system/...)

material  (WES-owned root entity)
  +-- material_units.current_session_correlation_id (correlation key, NOT FK)
  +-- 不允许 material 域作为 session 域的强 FK 来源

reconciliation  (RECONCILING 冲突决议模型)
  +-- ReconciliationManager
        +-- 触发矩阵: 投影冲突 / External callback 不一致 / 设备状态矛盾 / drift
        +-- 强制动作: evidence + RuntimeHold + 通知
        +-- 决议输出: resolution_decision + owner_scope + allowed_next_effect_scope
        +-- owner 自行转移: fulfillment / handling / session / projection
```

---

## 4. 数据设计

### 4.1 域核心实体

runtime 域内表可使用 `execution_session_id` 作为 `ExecutionSession` FK；若实现层沿用字段名 `session_id`，必须标注为“仅 runtime 域内 FK”。跨域实体只允许持有 `ExecutionCorrelation.correlation_id`，不得重新扩散 `execution_session.id` 强 FK。

| 域 | 核心实体 | 关键字段 |
| --- | --- | --- |
| workline | WorkLine | id, line_code, manifest_yaml, status, config_version |
| workline | ConveyorLine | id, workline_id, code, label, layout |
| workline | PipelineQueue | id, workline_id, conveyor_code, code, role, capacity, order_policy |
| workline | EntryPoint / ExitPoint | id, workline_id, code, conveyor_code, queue_code, external_handler |
| workline | Device (配置) | id, workline_id, role, code, capabilities |
| runtime | ExecutionSession | id, workline_id, state, started_at, ended_at |
| runtime | RuntimeInbox | id, execution_session_id（仅 runtime 域内 FK，可为空）, correlation_resolution_status, source, provider_code, event_type, source_event_id, payload_hash, status, attempt_count, next_retry_at, dead_letter_at |
| runtime | RuntimeTimeline | id, execution_session_id（仅 runtime 域内 FK）, event_type, trace_id, occurred_at |
| runtime | RuntimeHold | id, execution_session_id（仅 runtime 域内 FK）, reason, hold_type, created_at, resolved_at |
| runtime | RuntimeIntentLog | id, execution_session_id（仅 runtime 域内 FK）, correlation_id, target_domain, target_action, request_hash, provider_code, idempotency_key, dispatch_status, attempt_count |
| runtime | ExecutionCorrelation | correlation_id, execution_session_id, trace_id, source_event_id, business_owner_key, created_at |
| runtime | EffectPort (log table) | id, correlation_id, target_domain, target_action, payload, status |
| handling | HandlingOperation | id, workline_id, kind, coarse_business_status, source, target |
| handling | HandlingMove | id, handling_operation_id, from_location, to_location, kind, status |
| runtime | ConveyorQueueMembership | id, bin_code/placeholder_key, workline_id, conveyor_code, queue_code, status, entered_at, left_at |
| resource | RackPlacement / RackBinMount / BinPlacement | id, workline_id, rack_code, bin_code, status, correlation_id, evidence |
| resource | BinMaterialMount / BinCellOccupancy | id, cell_code, pkg_code, material_identity, status, correlation_id |
| resource | ResourceStateEvent | id, workline_id, event_type, source_event_id, payload, occurred_at |
| resource | RuntimeLocationEvent | id, workline_id, object_type, object_key, location_scope, location_code, business_step, source, evidence_json, occurred_at |
| material | material_units | id, pkg_code, material_identity_key, status, location_summary, current_session_correlation_id |
| device | DeviceRuntime | id, device_code, role, last_event_at, last_result_at, diagnostic_state |
| wms_integration | WmsFulfillmentRequest | id, fulfillment_kind, source, target, status, request_hash, idempotency_key, correlation_id |
| wms_integration | WmsCallbackEnvelope | id, callback_type, source_event_id, source_version, signature, timestamp, raw_body_hash, normalized_evidence_json |
| reconciliation | ReconciliationRecord | id, conflict_type, detected_at, resolution_decision, owner_scope, allowed_next_effect_scope, resolved_at, evidence |

### 4.2 ExecutionCorrelation correlation key

**问题**：实测 16+ 模型文件包含 `session_id` / `execution_session_id` / `current_session_id` 跨域 FK（`workline/models/runtime.py` 13 处、timeline.py 7 处、inbox.py 4 处、smt_inbound_handoff.py 3 处、operation.py 3 处、`handling/models/bin_transit_membership.py` 2 处、object_transition_event.py 2 处、material_unit.py 2 处、bin_cell_reservation.py 2 处、`resource/models/resource.py` 2 处 等）。**runtime 之外的域不能把 `execution_session.id` 作为强 FK 扩散**。

**解决**：引入 `ExecutionCorrelation` correlation key：

| 字段组 | 用途 |
| --- | --- |
| `correlation_id` | 跨域唯一业务关联键，作为主键 |
| `execution_session_id` | runtime 域内回放用 FK；其他域不得引用 |
| `trace_id` / `source_event_id` | trace 时间线与外部事件归因 |
| `business_owner_key` | 业务 owner 审计、查询和冲突定位 |

**索引**：

- `correlation_id` PRIMARY KEY
- `(execution_session_id, created_at)` 用于 runtime 域内回放
- `(trace_id, created_at)` 用于跨域 trace 时间线
- `(business_owner_key, created_at)` 用于 12 审计

**破坏性迁移策略**（ENG-001）：

- 现有 16+ 文件的 `session_id` FK 改造（rename / rebuild / drop-FK）
- 输出 `docs/architecture/session-correlation-matrix.md` 列出 per-file 迁移路径
- start_admission / runtime query / handoff 等旧流程只保留行为契约测试，代码允许重建
- 不保留旧 string `session_id` 兼容入口（C0 已决定破坏性切换）

### 4.3 typed `ExternalReference` 与 `EvidenceEnvelope`

**问题**：resource 域的 `rack_code / bin_code / location_code` 等外部引用当前是裸字符串，无 schema 无版本无对账标记；`evidence_json` 字段是裸 JSON dict，跨域写入方自由结构。

**解决**：typed Pydantic 模型，详细 schema 在 Phase SPEC 展开。

| 模型 | 字段组 | 用途 |
| --- | --- | --- |
| `ExternalReference` | `system`, `object_type`, `code`, `schema_version`, `validated_at`, `source_version` | 标识外部系统对象和最近对账版本，替代裸字符串 |
| `EvidenceEnvelope` | `schema_version`, `source_system`, `source_event_id`, `source_version`, `validated_at`, `request_hash`, payload | 统一 evidence 来源、版本、幂等和审计字段，替代裸 JSON |

**索引**：GIN 索引支持 `ExternalReference.code` + `EvidenceEnvelope.source_event_id` 等结构化字段查询。

**evidence schema 变更日志**：`docs/contracts/evidence-catalog.md` 维护每次 schema 升级的 source/target 映射。

### 4.4 conveyor queue membership 数据模型

**目标**：滚筒线队列是 WorkLine manifest 的动态配置，不是系统级 enum。队列 membership 是 runtime/orchestration 拥有的 current-state projection，只记录“某个料箱/占位符当前位于某条滚筒线的哪个 manifest queue”，不把具体队列名称写死到系统模型。Handling 只负责搬运意图和履约请求生命周期，不拥有滚筒线队列状态。

| 字段组 | 用途 |
| --- | --- |
| object identity | `bin_code` 或 `placeholder_key`，支持扫码前占位 |
| manifest scope | `workline_id`, `workline_code`, `conveyor_code`, `queue_code`, `queue_role` |
| state | `membership_status = ACTIVE / LEFT / RECONCILING`, `entered_at`, `left_at` |
| evidence | `handling_operation_id`, `handling_move_id`, `trace_id`, `execution_correlation_id`, `evidence_json` |

**约束**：

- `queue_code` 必须来自当前 WorkLine manifest 的 `pipeline_queues.code`
- `queue_role` 是写入时的 manifest role 快照，用于审计，不作为 enum 约束
- active 唯一约束保留业务语义：同一 `bin_code` 或 `placeholder_key` 在同一 WorkLine 下最多一个 active membership
- 旧 `BinTransitMembership` / `BinTransitQueue` 允许删除、重命名或迁移到一次性迁移脚本，不进入目标态模型
- 不定义系统级“7 队列”或“8 队列”常量；入口缓冲、扫码点、工位、出口路由、回收等待、NG 等队列只作为 manifest `pipeline_queues[]` 的配置实例存在。
- 多扫码点、Gate3/Gate4、出口路由扫码、回收扫码和多料箱并发写入由 `ConveyorQueueMembershipWriter` 按 pinned manifest 解析；writer 只依赖 runtime event、device event、WMS/CTU callback 和 `ExecutionCorrelation`。
- 并发写入必须使用 PostgreSQL 行级锁、savepoint/upsert 或等价 CAS；唯一冲突只允许两种结果：幂等重读成功，或写入 `RECONCILING` evidence。禁止让唯一冲突回滚主 callback ACK。
- placeholder resolve、terminal leave、queue switch 必须在同一事务内保证 active membership 收敛；跨事务重复事件依靠 `idempotency_key + request_hash` 识别。
- 投影失败分三类记录：预期并发冲突、外部合同缺字段、内部编程错误。生产默认 best-effort + diagnostic；测试/预发可启用严格模式，对非预期异常 re-raise。

### 4.5 数据迁移策略

**Alembic migration 规范**：

- 可逆 schema migration 必须可 upgrade + downgrade
- 新增表/字段必须包含完整 downgrade
- 迁移顺序：先 schema、后数据（如果有）、最后 application 验证
- 破坏性迁移不保留旧兼容入口
- 不做长期 dual-write；若数据搬迁需要过渡脚本，必须在同一 Phase 给出清理 PR

**破坏性迁移分级**：

| 类型 | 允许操作 | 回滚方式 | 门禁 |
| --- | --- | --- | --- |
| Reversible schema | 新表、新字段、新索引、非破坏性 rename 前置 | Alembic downgrade | upgrade/downgrade + 结构断言 |
| Data reshape | evidence 搬迁、字段重算、旧 payload 归档 | 数据库快照 + 幂等重跑脚本 | dry-run 报告 + 行数校验 + 抽样校验 |
| Destructive cleanup | drop 旧表、旧 enum、旧字段、旧 API 路径 | 数据库快照回滚，不伪造数据 downgrade | 用户确认 + 快照点 + 清理矩阵逐项勾选 |

不可逆清理不能写“假 downgrade”来重建已删除数据。系统未发布，允许破坏性优化，但必须把回滚真实边界写清楚：可逆靠 Alembic，不可逆靠快照和清理矩阵。

**Legacy 数据处理**：

- 旧 evidence 可迁入新 `EvidenceEnvelope`，无法结构化的旧 payload 进入 `legacy_payload`
- 旧表/旧 enum 不作为目标态约束；迁移后可 drop
- 新写入只走目标态 schema

**schema 选择准则**：系统未发布，默认采用 drop/recreate 或新表重建，避免为旧字段形态设计兼容层。只有需要保留 evidence、审计链、外部 request id 或人工对账依据时，才执行 data reshape；普通配置、enum、旧 API 路径和旧插件状态不做 rename 兼容。

---

## 5. 接口设计

### 5.1 wms_integration 能力面 Port 详细

`wms_integration` 是 WMS 能力面 port 的统一入口。可复用现有 `wms_integration` 中已验证的 ACL 能力，但允许破坏性整理包结构、import 路径和 API。现有 `WmsInventoryPort` 必须破坏性拆分为只读 `WmsInventoryQueryPort` 和事务型 `WmsInventoryTransactionPort`。

| Port | 职责 | 现状 | 关键方法 |
| --- | --- | --- | --- |
| `WmsMasterDataPort` | 查询/校验货架、料箱、库位、物料、区域、地码等元数据 | **新增** | `get_material` / `list_materials` / `get_zone` / `list_locations` / `get_rack` / `get_bin` / `validate_rack_bin` |
| `WmsDocumentPort` | 查询/接收 GRN、入库单、出库单、批次单、波次、业务任务 | **新增** | `get_grn` / `list_grn_packages` / `get_inbound_order` / `get_outbound_order` / `get_batch_order` / `subscribe_task_changes` |
| `WmsInventoryQueryPort` | 查询库存、箱位、货架占用、可用容器等外部事实 | **由现有 `WmsInventoryPort` 只读能力迁出** | `query_inventory` / `query_empty_bins` |
| `WmsInventoryTransactionPort` | 库存预留、释放、转移确认等会改变 WMS 事务状态的能力 | **由现有 `WmsInventoryPort` mutation 能力迁出** | `reserve_inventory` / `release_reservation` / `confirm_transfer` |
| `WmsFulfillmentPort` | 请求外部系统执行搬运、补给、移出、换面、投箱、取箱 | **新增** | `request_rack_supply` / `request_rack_transport` / `notify_pkg_binding` / `move_bin_to_conveyor_entry` / `move_bin_from_conveyor_exit` |
| `WmsEventPort` | 接收 WMS 状态变化、RCS 结果、任务结果、异常通知 | **新增**（部分实现于 callback_normalizer） | `WMS_GRN_RECEIVED` / `WMS_PALLET_ARRIVED` / `WMS_RACK_ARRIVED` / `WMS_TRANSPORT_COMPLETED` / `WMS_EXCHANGE_COMPLETED` |
| `WmsReconciliationPort` | 对账 WES 作业期投影与 WMS 权威事实 | **新增** | `reconcile_bin` / `reconcile_rack` / `reconcile_workline` / `reconcile_full` |

**`wms_rcs_interface_requirements.md` 到 port 的映射**：

| 来源接口/事件 | 目标 port | 目标态说明 |
| --- | --- | --- |
| `GET /api/wms/materials/{id}` / `GET /api/wms/materials?ids=...` | `WmsMasterDataPort` | 物料主数据按需查询；结果可 30s TTL 缓存 |
| `GET /api/wms/zones` / `GET /api/wms/locations?zone=...` | `WmsMasterDataPort` | 区域/地码用于设备归属、资源边界和履约目标校验 |
| `GET /api/wms/racks/{id}` / `GET /api/wms/bins/{id}` / `GET /api/wms/racks?type=...` | `WmsMasterDataPort` | 货架/料箱主数据与状态按需引用，不复制为 WES 主数据 |
| `GET /api/wms/grn/{id}` / `GET /api/wms/grn/{id}/packages` | `WmsDocumentPort` | GRN 与料盘归属用于作业上下文和 PKG 校验 |
| `GET /api/wms/inventory/query` | `WmsInventoryQueryPort` | 库存查询实时透传 WMS；WES 只可短 TTL 缓存 |
| `POST /api/wes/rack-supply-request` / `POST /api/wes/transport-request` | `WmsFulfillmentPort` | WES 生成搬运需求；WMS 统一调度 RCS |
| `POST /api/wms/kitting/pkg-binding` | `WmsFulfillmentPort` 或 `WmsDocumentPort` | 按最终 WMS 合同归类；目标语义是 WES 作业结果通知 WMS |
| `POST /api/wms/inventory/reserve` / `DELETE /api/wms/inventory/reserve/{id}` / `POST /api/wms/inventory/transfer` | `WmsInventoryTransactionPort` | 库存预留、释放、转移确认必须以 WMS 事务结果为准；必须走 `RuntimeIntentLog` + EffectPort，不允许作为查询能力直调 |
| `POST /api/v1/callback/event` / `POST /api/v1/callback/result` | `WmsEventPort` / `DeviceEventPort` → `RuntimeInbox` | 统一回调入口；按 source 路由到 WMS/RCS/ECS/device normalizer；ACK 后写 inbox，不直接改 session |

**标准履约意图**：

| 意图 | 当前执行方式 | 说明 |
| --- | --- | --- |
| `SUPPLY_EMPTY_SINGLE_LAYER_RACK` | 请求 WMS 履约接口 | 为粗分机补充带空料箱的单层货架；对应 `/api/wes/rack-supply-request` |
| `REMOVE_LOADED_SINGLE_LAYER_RACK` | 请求 WMS 履约接口 | 将粗分机上已载有料盘/物料的单层货架移出；对应 `/api/wes/transport-request` |
| `POSITION_FIVE_LAYER_RACK` | 请求 WMS 履约接口 | 将五层货架从原料仓移动到分拣机工作位，或从工作位移出 |
| `CHANGE_RACK_FACE` | 请求 WMS 履约接口 | 请求货架原地换面 |
| `MOVE_BIN_TO_CONVEYOR_ENTRY` | 请求 WMS 履约接口 | 从工作位货架取指定料箱，送入分拣机入口 |
| `MOVE_BIN_FROM_CONVEYOR_EXIT` | 请求 WMS 履约接口 | 从分拣机出口取料箱，送回工作位货架指定位置 |
| `NOTIFY_PKG_BINDING` | 通知 WMS 作业结果 | 将 PKG 与料箱/料格/货架绑定结果通知 WMS；对应 `/api/wms/kitting/pkg-binding` |

**WES 只定义本系统侧的履约意图、请求字段、幂等证据、回调接收和状态处理**。外部 WMS 如何选择货架、计算箱位、规划库位或调度 AGV/CTU，不在本系统规划内。

### 5.2 plane 接口（运营敏感数据入口）

**适用前端范围**：P0 plane 接口只面向操作员终端、工程调试台和只读可视化大屏；不面向公开报表、客户门户或 WMS 全局库存查询。前端不得绕过 `PlaneSceneView + PlaneSnapshot + plane/events` 直接拼接 resource/material/device/runtime 散表。

**接口设计**（首版**禁止**聚合接口）：

```text
GET /worklines/{id}/plane/scene
  -> PlaneSceneView
  鉴权: biz:workline:view-plane-scene
  频率: 1 Hz 轮询足够（manifest 派生，变化慢）

GET /worklines/{id}/plane/snapshot
  -> PlaneSnapshot
  鉴权: biz:workline:view-plane-snapshot
  频率: 实时刷新（SSE/WebSocket 或 250ms 轮询）

GET /worklines/{id}/plane/events  (后续, 不强制)
  -> SSE/WebSocket, 基于 object_transition_events + device event/result + handling request status changes
```

**实时性分级决策（M1 回归）**：

- `plane/scene`：manifest 派生，首版只支持 1 Hz 轮询；不提供 SSE/WebSocket。
- `plane/snapshot`：active projection 派生，首选 SSE 单向流；断线或客户端不支持 SSE 时 fallback 到 250ms 轮询。
- `plane/events`：增量事件流，只使用 SSE；不引入 WebSocket，避免双向通道把前端动作混入读模型。
- 首版不实现完整数字孪生，只保证平面展示可按上述接口得到稳定 scene、当前 snapshot 和后续增量事件。

**安全门禁**：

| 维度 | 要求 |
| --- | --- |
| 鉴权 | 拆 `biz:workline:view-plane-scene` / `biz:workline:view-plane-snapshot` 两套权限 |
| 行级过滤 | 默认用户只能读自己 WorkLine 域内的 WorkLine；跨域读需 `wes.observer` 角色 |
| 脱敏 | `evidence_json` 默认脱敏（`pkg_code` 后 4 位掩码、`bin_code` 前缀掩码），仅 `wes.engineer` 角色可见全量 |
| Audit log | 每次 plane 读取写 `audit_logs`：`viewer_user_id, viewer_ip, snapshot_version, snapshot_status, result_size, read_at` |

**PlaneSceneView schema**（实施细节在 Phase 3 SPEC 展开）：

```text
PlaneSceneView
  schema_version          (string, 当前 "1.0")
  generated_at            (ISO timestamp)
  workline_id
  workline_code
  nodes[]
    node_id
    node_type = CONVEYOR | QUEUE | ENTRY_POINT | EXIT_POINT | DEVICE | RACK_POSITION
    ref_code               (稳定 identifier)
    label                  (i18n 字符串)
    role
    layout
    capacity
    order_policy
  edges[]
    from_node_id
    to_node_id
    edge_type = MATERIAL_FLOW | OPERATION | QUEUE_FLOW | EXTERNAL_TRANSFER
  warnings[]
    code
    message
    evidence
```

**PlaneSnapshot schema**（实施细节在 Phase 3 SPEC 展开）：

```text
PlaneSnapshot
  workline_id
  schema_version          (string, 当前 "1.0")
  generated_at            (ISO timestamp)
  stale_threshold_seconds (int, 默认 30)
  snapshot_status         (enum: OK | EMPTY | CONFLICTS_ONLY | STALE | RECONCILING)
  active_material_units[] (限 active 30 天内; 超限 -> truncated=true)
  active_bins[]           (presence_type 目标态枚举)
  queue_memberships[]     (上限 200, top by entered_at desc)
  devices[]               (上限 50, by last_event_at desc)
  resource_projections[]  (上限 200)
  in_transfer[]           (限 active 30 天内, 限 100 条)
  conflicts[]             (top 50 by detected_at desc)
  warnings[]
  truncated               (bool)
  total_counts            (Map<list_name, int>)
```

**目标态枚举值**：

- `presence_type ∈ {ON_CONVEYOR, AT_WORK_POSITION, IN_TRANSFER, UNKNOWN}`
- `queue_role ∈ {BUFFER, INFEED, SCAN, WORKSTATION, EXIT, NG_REJECT, RETURN}`
- `snapshot_status ∈ {OK, EMPTY, CONFLICTS_ONLY, STALE, RECONCILING}`

### 5.3 External callback 鉴权

**入口**：统一 `src/app/callback/v1/callback.py:91`（不引入新 callback 路径）

**目标态约束**：

- `docs/integration/wms_rcs_interface_requirements.md` 的 WMS/RCS 回调与 `docs/integration/third_party_integration_whitepaper.md` 的 ECS/device 回调共用入口，但必须按 `source_system + provider_code + callback_type` 路由到不同 normalizer。
- 联调白皮书中的 Bearer Token 可选口径只作为历史输入；目标态中任何外部 callback 都必须通过 HMAC body 签名、timestamp、nonce、source allow-list 和幂等校验。
- Callback API 不承载业务决策；只做鉴权、schema normalize、原始日志、幂等校验、ACK、写 `RuntimeInbox`。
- `DeviceEventPort` 处理 ECS/device 的 `/api/v1/callback/event` 与 `/api/v1/callback/result`；`WmsEventPort` 处理 WMS/RCS 事件；二者不得互相复用 DTO 或 provider exception。

**Raw body 与 EvidenceEnvelope 分层**：

| 层 | 输入/输出 | 规则 |
| --- | --- | --- |
| Raw callback body | 外部系统原始 JSON | 保持供应商合同原样，用于 HMAC、原始日志和重放；不要求外部系统提交内部 `EvidenceEnvelope` |
| Callback auth envelope | method/path/header/timestamp/nonce/body_hash/app_id | 只做身份、签名、防重放、allow-list 和幂等预检 |
| Provider normalizer | raw body -> normalized callback DTO | WMS/RCS/ECS/device 各自 normalizer 负责字段映射、缺字段诊断和 provider error code 转换 |
| EvidenceEnvelope | normalized DTO -> 内部 evidence | 只在 WES 内部生成；写入 RuntimeInbox、diagnostic、timeline 或 projection evidence |

外部 body 签名覆盖的是 raw body；`EvidenceEnvelope` 的 schema 校验发生在 normalizer 之后。供应商 DTO、HTTP client、provider exception 仍只能存在于对应 ACL/normalizer 层。

**签名 canonical string**：

```text
canonical = method + "\n" + path + "\n" + timestamp + "\n" + nonce + "\n" + sha256(body) + "\n" + app_id
signature = HMAC-SHA256(secret, canonical)
```

**鉴权矩阵**：

| 字段 | 要求 |
| --- | --- |
| `provider_code` | 必填，`WMS` / `RCS` / `ECS` / `AGV` / `CTU` / provider-specific code |
| `source_system` | 必填，必须命中启用状态的外部系统 allow-list |
| `app_id` | 必填，绑定 secret、IP allow-list、provider_code 和 callback_type allow-list |
| `signature` | 必填，HMAC-SHA256 覆盖 method/path/timestamp/nonce/body/app_id |
| `timestamp` | 必填，与 WES 时钟偏差 > 30s 拒绝 |
| `nonce` | 必填，按 `app_id` 做 5 分钟 TTL 去重 |
| `callback_type` | 必填，必须匹配 provider + 未终结 request/command/session allow-list |
| `source_event_id` | 必填；ECS/device event 使用 `data.event_id`，result 使用 `command_code`，WMS/RCS 使用 `request_id` 或业务事件 id |
| `body` | 必填，外部原始 JSON；通过 normalizer 后必须生成合法 typed `EvidenceEnvelope` |

**Body 完整性**：signature 校验失败立即返回 401，**不触发**业务处理；防重放窗口 5 分钟。

**callback_type allow-list**（实施细节在 Phase 3 SPEC 展开）：

- WMS/RCS：`WMS_GRN_RECEIVED` / `WMS_PALLET_ARRIVED` / `WMS_RACK_ARRIVED` / `WMS_TRANSPORT_COMPLETED` / `WMS_EXCHANGE_COMPLETED` / `WMS_INVENTORY_UPDATED` / `WMS_TASK_CHANGE` / `WMS_REJECTED` / `WMS_FAILED`
- ECS/device：`DEVICE_RESULT` / `DEVICE_EVENT` / `DEVICE_STATUS_CHANGED` / `MATERIAL_ARRIVED` / `SCAN_COMPLETED` / `ESTOP_PRESSED` / `DEVICE_ERROR` / `DEVICE_ONLINE` / `DEVICE_OFFLINE`

**统一入口约束**：WMS/RCS 事件沿用 `docs/integration/wms_rcs_interface_requirements.md` 的统一 callback 语义；ECS/device 事件沿用 `docs/integration/third_party_integration_whitepaper.md` 的 Command-Ack-Callback 语义。目标态中 callback API 只做签名校验、幂等校验、原始日志、normalizer 调用、ACK、写 `RuntimeInbox`，不得直接修改 `ExecutionSession`、`DeviceRuntime`、`MaterialUnit` 或投影表。

### 5.4 idempotency_key 规范

**复合主键**：

```text
idempotency_keys:
  PRIMARY KEY (provider_code, operation_kind, idempotency_key)
  request_hash          (immutable)
  execution_correlation_id  (correlation key)
  business_owner_key
  created_at            (TTL 30 天)
```

**provider_code**：`"WES" / "WMS" / "ECS" / "RCS" / "AGV" / "CTU"` 及 provider-specific code，跨域跨 provider 隔离。

**operation_kind**：`"fulfillment" / "callback" / "device_command" / "device_event" / "reconciliation"`。

**idempotency_key**：调用方提供的业务键，跨域跨 provider 唯一。

**WES 内部 key 命名空间（C3 回归）**：

- WES 内部生成的 key 不得使用裸 `callback-timeout:{id}` / `dispatch-ack-exhausted:{id}` / `safety-estop:{id}` 格式。
- 内部 key 统一格式：`WES-{OPERATION_KIND}-{DETERMINISTIC_HASH(source_id, source_event_id, correlation_id)}`。
- 内部 `operation_kind` 必须细分为 `wes-callback-timeout` / `wes-dispatch-ack-exhausted` / `wes-safety-estop` / `wes-resource-reconciliation` / `wes-manual-replay` 等，不得全部塞入通用 `reconciliation`。
- 外部 provider 传入的 `idempotency_key` 保持原样存储，但必须与 `provider_code + operation_kind` 共同组成命名空间；WES 生成 key 必须使用 `provider_code=WES` 且 operation_kind 细分。
- 同一 `provider_code + operation_kind + idempotency_key` 下不同 `request_hash` 必须 409；不同 provider 或不同 operation_kind 下同名 key 不视为冲突。

**行为**：

- 同 key 不同 `request_hash` → `409 Conflict` + 安全审计事件（**不静默**返回旧 record）
- 同 key 同 `request_hash` → 直接返回旧 record（不重新走状态机）
- 30 天 TTL 后允许同 key 不同 hash 覆盖

**现有实现迁移**：`runtime_hold` 的 `UniqueConstraint("source_idempotency_key")` 需迁移到复合主键。

### 5.5 域内 API 边界

**域间 API 调用规则**：

- 域间通过 port 接口调用（`WmsFulfillmentPort` / `EffectPort` 等）
- 域间不直接 import 对方模型类（避免强耦合）
- 域间返回值通过 typed Pydantic 模型
- 域间不直接访问对方数据库（必须通过对方 repository）

**域内 API 规则**：

- 域内 Service 通过 repository 访问数据库
- 域内 repository 不跨域访问
- 域内 Service 负责业务逻辑、跨 repository 协调
- 域内 Model 由 ModelFactory 派生 Schema

---

## 6. 状态与恢复设计

### 6.1 11 态机（外部履约）

```text
REQUESTED -> SENT -> ACCEPTED -> RUNNING -> SUCCEEDED
                         │          │
                         │          ├── FAILED
                         │          └── TIMEOUT
                         ├── REJECTED
                         └── BLOCKED_BY_CB  (新增: circuit breaker open)

任意非终态 -> CANCELLED
不可信/证据冲突 -> RECONCILING evidence + RuntimeHold  (ReconciliationManager 登记/隔离/决议)
```

**状态含义**：

| 状态 | 含义 | 终态 |
| --- | --- | --- |
| `REQUESTED` | WES 已生成搬运意图，尚未成功发出 | 否 |
| `SENT` | 已调用 Adapter，下游响应未定 | 否 |
| `ACCEPTED` | 下游接受请求 | 否 |
| `RUNNING` | 下游已开始执行 | 否 |
| `SUCCEEDED` | 下游确认完成 | ✓ |
| `REJECTED` | 下游业务拒绝（无可用货架、无空箱位、目标不合法） | ✓ |
| `FAILED` | 执行失败或技术错误 | ✓ |
| `TIMEOUT` | 执行超时（**新增**：与 FAILED 区分） | ✓ |
| `CANCELLED` | WES 或下游取消 | ✓ |
| `BLOCKED_BY_CB` | circuit breaker open 阻塞期间（**新增**：不混进 RECONCILING） | 否 |
| `RECONCILING` | WES evidence / WMS 回调 / 现场投影冲突 | 否（需产生恢复决议） |

### 6.2 4 条 timeout 转移规则

| 源态 | 触发 | 目标态 | 默认时长 |
| --- | --- | --- | --- |
| `REQUESTED` | Adapter 宕机 / CB open 持续 > 30s / 进程崩溃 | `FAILED` | 30s |
| `SENT` | 收到 `ACCEPTED/REJECTED` 之前超时 | `TIMEOUT` | 60s |
| `ACCEPTED` | 长时间无 `RUNNING` 进展 | `RECONCILING` | 5 min |
| `RUNNING` | 长时间无 `SUCCEEDED` | `RECONCILING` | 30 min |

**注**：时长按 WorkLine 配置可覆盖（不同 WorkLine 业务节奏不同）。

### 6.3 Circuit breaker 集成

`WmsFulfillmentAdapter` 持 `circuit_breaker` 状态（open / half-open / close），现有 `src/app/wms_integration/services/circuit_breaker_service.py` 实现。

| CB 状态 | 新请求行为 | 已有请求行为 |
| --- | --- | --- |
| `open` | 进 `BLOCKED_BY_CB`，不消耗 `SENT` 配额 | 继续等原状态机超时 |
| `half-open` | 限速（默认 1 req / 10s）尝试 | 继续 |
| `close` | 正常进 `REQUESTED` | 正常 |

**CB `open → half-open` 转移时**：所有 `BLOCKED_BY_CB` 请求自动恢复为 `REQUESTED` 重试（保留 `idempotency_key`）。如重试期间 `idempotency_key` hash 一致，直接返回旧 record。

**BLOCKED_BY_CB 语义（M2 回归）**：

- `BLOCKED_BY_CB` 是 circuit breaker open 期间的系统侧延迟状态，不代表 WMS/RCS/ECS 已接收或拒绝业务请求。
- 业务查询 API 默认不把 `BLOCKED_BY_CB` 计入 in-flight fulfillment 列表；运维视图可单独展示 CB 阻塞队列。
- `BLOCKED_BY_CB` 不计入履约 P95 指标；但必须计入可观测性指标 `effect_blocked_by_cb_total` 与告警。

### 6.4 RECONCILING 冲突决议模型（ReconciliationManager）

**触发矩阵**：

| 触发类型 | 检测源 | 默认处理 |
| --- | --- | --- |
| 投影冲突 | 同 object 在 2+ 投影源 | `RECONCILING` + `RuntimeHold` |
| External callback 与本地 projection 不一致 | callback normalize + drift detector | `RECONCILING` + audit log |
| device 事件与 handling 业务意图状态不一致 | runtime event monitor | `RECONCILING` + `RuntimeHold` |
| CB 半开期间收到旧 callback | circuit breaker state | `BLOCKED_BY_CB` |
| WMS master-data drift | `WmsReconciliationPort` | `RECONCILING` 分类处理（详见 §6.5） |
| `RuntimeHold` 关联的现场异常 | runtime intent effects | 创建 `RECONCILING` evidence |
| 传感器抖动 | ECS/device event 去抖窗口 | N 秒内同 sensor 同 object 合并 evidence；超阈值 `RuntimeHold` |
| 通信丢包或 callback 延迟 | deadline + provider query | 超过 TTL 后主动 query WMS/ECS；不可确认时 `RECONCILING` |
| 重复上报 | idempotency + payload_hash | 同 key 同 hash 合并 evidence；同 key 不同 hash 409 + 安全审计 |

**强制动作**：

1. 创建 `RECONCILING` evidence（`detected_at` + `detected_by` + `reason`）
2. 创建/关联 `RuntimeHold`
3. 对相关 `correlation_id` / object / workline scope 加 effect 禁发闸门，不再创建新的 DeviceCommand / WMS transaction effect
4. 冻结相关 active projection 写入；只允许追加 evidence、event、diagnostic，不允许“猜测式修正”
5. 通知操作员 dashboard（事件总线 `reconciliation.conflict.detected`）
6. 写 audit log

**现场隔离语义**：

- WES 的隔离动作是软件层禁发、hold、告警和证据冻结，不直接控制 PLC 或安全回路。
- 如需停止物理动作，WES 只能按 ECS 支持的 Cancel Command 请求取消仍在排队/执行的业务命令；是否能安全停止由 ECS/现场安全系统决定。
- `ESTOP_PRESSED`、安全门、光栅等事件一律进入 `RuntimeHold + RECONCILING`，恢复条件必须来自 ECS 状态回传或人工 reconcile。
- 人工 reconcile 必须记录操作者、恢复依据、允许恢复的 object scope 和下一步 effect 范围。
- 物理现场 RECONCILING 采用 push + pull 双通道：ECS/device push 事件优先，超过 TTL 未恢复时由 WES 主动 query ECS/WMS 状态；两者不一致时保留 evidence 并继续 hold。

**恢复决议**：

`ReconciliationManager` 不直接写入 `WmsFulfillmentRequest`、`HandlingOperation`、`ExecutionSession` 或 active projection 的业务状态。它只产出 `ReconciliationRecord.resolution_decision`、追加 evidence、解除/维持 `RuntimeHold`，再由各状态 owner 按 evidence 自己转移。

| 路径 | 触发 | 决议输出 |
| --- | --- | --- |
| WMS 重发回调 | callback normalize 命中 correlation key | `FULFILLMENT_EVIDENCE_ACCEPTED`，由 `WmsFulfillmentRequest` 决定转回 `RUNNING` 或到 `SUCCEEDED` |
| device 事件恢复 | runtime 检测到一致状态 | `DEVICE_EVIDENCE_ACCEPTED`，由 `DeviceCommand` / `ExecutionSession` owner 决定恢复或继续 hold |
| 人工 reconcile | 操作员确认后 close | `MANUAL_RESOLUTION_ACCEPTED`，指定允许恢复的 object scope 和下一步 effect 范围 |
| 超时升级 | `RECONCILING > 5 分钟` 告警；`> 30 分钟` 升级 P1 | `RuntimeHold` 升级 |

**告警分级**：

- `info`：瞬态冲突、同 hash 重复上报、自动恢复的 callback 延迟。
- `warn`：进入 `RuntimeHold`、设备 `UNKNOWN` 持续超过 TTL、单 WorkLine inbox 积压接近阈值。
- `critical`：`ESTOP_PRESSED`、同 object 多归属超过 `transient_until`、WMS/ECS 长时间不可用、`RECONCILING > 30 分钟`。
- 告警目标首版只写 `audit_logs` + dashboard event；外部钉钉/PagerDuty 等通知作为后续 provider adapter，不进入 P0。

**owner 转移约束**：

`RECONCILING` 不是跨域全局状态写入口。各 owner 只能根据 reconciliation evidence 自行转移：

| Owner | 允许根据 reconciliation evidence 转移到 | 禁止 |
| --- | --- | --- |
| `WmsFulfillmentRequest` | `RUNNING` / `SUCCEEDED` / `FAILED` / `CANCELLED` / `BLOCKED_BY_CB` | 直接由 `ReconciliationManager` 写 `ACCEPTED/REJECTED/TIMEOUT` |
| `HandlingOperation` | `IN_PROGRESS` / `COMPLETED` / `FAILED` / `CANCELLED` / `RECONCILING` | 写入外部履约细态 |
| `ExecutionSession` | `RUNNING` / `HOLD` / `CLOSED` | 绕过 owner 直接改投影 |
| active projection | 解除冻结后由 projection writer 重放 evidence | 人工猜测式覆盖当前归属 |

### 6.5 WMS master-data drift 分类

`WmsReconciliationPort.reconcile_*` 定期对账：

| drift 类型 | WES 处理 |
| --- | --- |
| `MISSING_IN_WMS` | 标 `RECONCILING`，等待 WMS 确认；5 分钟升级告警 |
| `RENAMED_IN_WMS` | 写 `source_version` 升级 evidence，projection 用新 code；旧 code 留 evidence |
| `METADATA_DRIFT` | 写 conflict evidence，触发人工 reconcile；30 分钟升级 P1 |

启动时跑一次 full reconcile（性能预算：1 条 WorkLine < 5 分钟）；运行期按 5 分钟周期跑增量对账。

**drift SLA 与 WMS 可用性（M10 回归）**：

- drift 恢复 SLA 与 WMS 可用性 SLA 分离；WMS 不可用期间不得把每次 reconcile 失败都升级为新的业务 drift。
- WMS 长时间不可用时进入 `DRIFT_WAITING_FOR_WMS` 降级模式：保留首个 P0/P1 告警、停止重复告警风暴、继续追加 evidence。
- WMS 恢复后，ReconciliationManager 必须从最后一次成功 `source_version` 继续增量对账，再决定是否解除 `RuntimeHold`。

### 6.6 3 路 UNION 冲突 policy

3 路 UNION（`ON_CONVEYOR` + `AT_WORK_POSITION` + `IN_TRANSFER`）现唯一约束只在各自表内生效；同一 `bin_code` 同时出现在多个来源时，**没有跨投影唯一 active 归属**。引入 `ActiveObjectRegistry` 跨投影仲裁读模型。

| 组合 | 处理 | 说明 |
| --- | --- | --- |
| `(IN_TRANSFER, ON_CONVEYOR)` 在 `handling_request.created_at + N 秒` 内 | 合法 | 物理瞬态：CTU 送入瞬间料箱已 conveyor 接住但请求未终结 |
| `(IN_TRANSFER, ON_CONVEYOR)` 超过 `N` 秒 | 进 RECONCILING | CTU 卡死或请求未终结异常 |
| `(ON_CONVEYOR, AT_WORK_POSITION)` 任何时候 | 进 RECONCILING | 料箱不能在两处 |
| `(AT_WORK_POSITION, IN_TRANSFER)` 任何时候 | 进 RECONCILING | 料箱必须先离开工作位才能搬运 |

`N` 暂取 30 秒。evidence 中通过 `transient_until` 字段区分"瞬态合法"与"真冲突"。

**非料箱冲突扩展（M5 回归）**：

- 货架维度：`RackPlacement.status=IN_TRANSIT` 后，新 placement 写入必须基于 WMS/RCS/ECS evidence；同一 `rack_code` 同时处于 2 个 `work_position_code` 直接进 `RECONCILING`。
- 命令维度：同一 `correlation_id` 下，不允许同时存在 `DeviceCommand.status=RUNNING` 与 `WmsFulfillmentRequest.status=SUCCEEDED` 且 evidence 时间线无法解释的组合；发现后进入 `RECONCILING`。
- active 归属仲裁不只面向 `bin_code`，还必须支持 `rack_code`、`pkg_code`、`command_code` 四类 object key。

---

## 7. 安全设计

### 7.1 威胁模型

| 威胁 | 攻击向量 | 影响 | 防线 |
| --- | --- | --- | --- |
| 运营数据泄露 | plane 接口无 RBAC，全员可读 `pkg_code/bin_code/dispatch_request_id/source/target/evidence` | 物料追踪 + 仓库地理边界 + 业务拒绝原因泄露 | §5.2 plane RBAC + 行级 + 脱敏 + 审计 |
| External callback 重放 | 合法签名后重放同一 callback payload | 重复触发状态机或冲突 evidence | §5.3 HMAC body 签名 + nonce 5 分钟 TTL |
| External callback body 篡改 | 修改 payload 但保持 signature 头 | 业务逻辑走错路径 | §5.3 HMAC body hash 包含 sha256(body) |
| 跨 session 幂等键复用 | 同 `idempotency_key` 被 2 个不同 WES session submit | 业务证据混淆 + 攻击信号 | §5.4 复合主键 + `request_hash` 校验，409 + 审计 |
| 时钟偏差攻击 | 篡改 timestamp 绕过 nonce TTL | replay 攻击窗口扩大 | §5.3 时钟偏差 > 30s 拒绝 |
| 内部域直接 import WMS DTO | 跨层强耦合，WMS schema 变化穿透 | WES 主数据污染风险 | §2.5 不变量 1：WMS DTO 只能存在于 `wms_integration` |
| 影子 WMS | 本地 active projection 冒充 WMS 全局库存 | 业务决策错误 + WMS 不可信 | §3.4 Authority Matrix + §2.5 不变量 5 |
| 设备到位信号伪造 | 攻击 ECS 事件或 device callback 接口 | 现场状态误判 | §3.4 Authority Matrix：设备到位归 ECS/device，WES 只接收 |
| 设备命令越界 | WES 下发 PLC/坐标/关节/安全回路指令，绕过 ECS | 现场安全边界被软件业务层污染 | §9.6：WES 不与 PLC 通讯，只通过 ECS API 下发业务命令 |
| 事件响应体偷渡动作 | 在 Event_Push ACK 中返回下一步动作 | 指令不可追踪、无法幂等、绕过 effect ledger | §9.6：Event_Push 只 ACK，动作必须走 DeviceCommand |
| 设备事件乱序/缺失 ID | ECS 事件重放、延迟、缺 `event_id` 或 `sequence_no` | Session 被旧事件推进 | §9.6：缺 ID/乱序事件只落 evidence + diagnostic |
| 绕过 WMS 直连 RCS | WES 内部域直接调用 RCS/AGV/CTU SDK | 调度权威分裂、WMS 账务/任务状态失真 | §3.5：当前阶段 RCS 调度由 WMS 统一调度 |
| 跨域 FK 误用 | `execution_session.id` 跨域强 FK | 域耦合、未来重构困难 | §2.2 跨域 correlation key |
| Event_Push 响应体偷渡检测缺失 | callback handler 返回非 ACK body 或携带 command-like 字段 | 供应商绕过 DeviceCommand 审计链 | callback 响应 schema 固定为 ACK；响应拦截器检测非 ACK 字段并告警 |

### 7.2 plane 接口 RBAC

详见 §5.2。

### 7.3 External callback HMAC

详见 §5.3。

### 7.4 idempotency 跨域审计

详见 §5.4。

### 7.5 关键不变量（17 条）

| # | 不变量 | 出处 |
| --- | --- | --- |
| 1 | WMS DTO / client / 状态码 / 供应商字段只能存在于 `wms_integration` adapter/ACL 层——内部域直接 import WMS 类型立即拒绝 | §2.5 |
| 2 | 目标态契约优先：旧 API / 旧表 / 旧插件形态不得反向约束新架构 | §3.7 |
| 3 | B 方案以目标态边界 + 行为契约测试 + 破坏性清理清单为前置 | §3.8 |
| 4 | 跨域 session FK 收敛为 `ExecutionCorrelation` correlation key | §3.3 |
| 5 | 查询响应强制带 `scope/authority/source/evidence_at`——不允许本地 active projection 冒充 WMS 全局库存 | §3.4 |
| 6 | plane 接口不允许全员可读全量运营数据 | §5.2 |
| 7 | WMS/RCS/ECS/device External callback 必须 body HMAC + nonce TTL + path canonical | §5.3 |
| 8 | idempotency_key 复合主键 `(provider_code, operation_kind, idempotency_key)`——跨 session 同 key 不同 hash 返回 409 + 安全审计 | §5.4 |
| 9 | 当前阶段 RCS/AGV/CTU 调度只能经 WMS 履约 port；未来直连必须通过 provider adapter 替换，不允许内部域直连 SDK | §3.5 |
| 10 | Runtime capability 只能通过注入的 port contract 使用 WMS 能力；不能注入 `wms_integration` 实现对象、HTTP client、DTO、provider exception 或 service locator | §3.5 |
| 11 | WES 不与 PLC 通讯，不下发坐标/关节/安全回路指令；设备控制只能经 ECS/设备上位机标准 API | §9.6 |
| 12 | 设备 Event_Push 只能 ACK；任何后续动作必须经 RuntimeIntentLog + DeviceCommand 下发 | §9.6 |
| 13 | WorkLine manifest 在 ExecutionSession 创建时 pin 版本；运行中 session 不热切 manifest | §9.1 |
| 14 | DeviceCommand dispatch 前必须确认 ECS 设备状态为 IDLE；RUNNING 有界等待，ERROR/OFFLINE/查询超时短退避后 RuntimeHold | §9.6 |
| 15 | RuntimeInbox 必须支持 `RECEIVED -> PROCESSING -> PROCESSED/FAILED/DEAD_LETTER`，callback ACK 后处理失败必须可重试、死信和人工重放 | §9.2 |
| 16 | 作业期位置只能通过 evidence/RuntimeLocationEvent 投影，不允许裸写 location summary 覆盖冲突事实 | §9.4 |
| 17 | Event_Push HTTP 响应 schema 固定为 ACK；任何 command-like 字段都必须由响应拦截器拒绝并告警 | §7.1 / §9.6 |

---

## 8. 非功能性设计

### 8.1 性能设计

| 指标 | 目标 |
| --- | --- |
| Plane 接口 P95 | < 500ms（无 10x load）；< 1.5s（10x load） |
| 启动时 full reconcile | 1 条 WorkLine < 5 分钟 |
| 增量 reconcile | 5 分钟周期 |
| 关键业务语义测试 | characterization + contract tests 覆盖 |
| ConveyorQueueMembership active membership 写入 | 同 WorkLine 下 active 唯一约束 |
| DeviceCommand dispatch | 下发前校验 ECS 设备状态为 IDLE；RUNNING 有界等待；ERROR/OFFLINE/查询超时指数退避（1s/2s/4s，最多 3 次）；按 DeviceDispatchPolicy 串行/限流/取消 |
| RuntimeInbox 积压 | 超过 workline 阈值进入降级：停止新 effect、保留 ACK + evidence、告警 |
| DeviceRuntime 状态快照 | 默认 TTL 1000ms；过期必须重新查询 ECS；查询失败按短退避处理 |

### 8.2 容量设计

| 资源 | 上限 | 处置 |
| --- | --- | --- |
| `PlaneSnapshot.conflicts[]` | ≤ 50 | 超出打 `truncated=true` + `total_counts` 字段 |
| `PlaneSnapshot.in_transfer[]` | ≤ 100，限 active 30 天内 | 同上 |
| `PlaneSnapshot.active_material_units[]` | ≤ 200，限 active 30 天内 | 同上 |
| `PlaneSnapshot.devices[]` | ≤ 50，by last_event_at desc | 同上 |
| `PlaneSnapshot.queue_memberships[]` | ≤ 200，top by entered_at desc | 同上 |
| 1 条 WorkLine 同时活跃 session | ≤ 100（首版限 50） | 监控告警 |
| `idempotency_keys` TTL | 30 天 | 超时允许同 key 不同 hash 覆盖 |
| `material_units` per pkg_code | 唯一索引 | 1 个 active per pkg_code |
| `ConveyorQueueMembership` per bin_code | 同 WorkLine active 唯一约束 | 1 个 ACTIVE per bin_code |
| `DeviceCommand` in-flight per device | 默认 1，允许 manifest 按设备能力覆盖 | 超限或设备 RUNNING 时等待到 IDLE 或 deadline；ERROR/OFFLINE/查询超时短退避耗尽后 RuntimeHold，不排队无限增长 |
| `RuntimeInbox` unprocessed per workline | 默认 1,000 | 超限停止新 effect，优先处理安全/错误/结果事件 |

### 8.3 可靠性设计

| 维度 | 设计 |
| --- | --- |
| **Failure 隔离** | External callback 异步处理失败不影响 API ACK；circuit breaker 隔离下游故障；RuntimeInbox 死信进入人工审计 |
| **Graceful degradation** | plane 接口超载自动降级（精简 `devices[]` 和 `in_transfer[]` 字段）；RuntimeInbox 积压时停止新 effect 并保留 callback ACK/evidence |
| **Recovery decision** | RECONCILING 决议模型：owner-scoped `resolution_decision` + evidence；5/30 分钟两阶段超时升级 |
| **Idempotency** | 跨域 `idempotency_key` 复合主键；30 天 TTL 抗重试风暴 |
| **Backup & restore** | WMS 主数据不在 WES（WMS 是权威）；WES active projection 可从 WMS 事件、查询和本地 evidence 重放恢复 |
| **WMS evidence retention** | WMS evidence 必须支持 retention/archive 策略；保护 active trace、人工对账中 evidence 和安全审计，不允许无界增长 |
| **Benchmark gate** | Runtime worker、RuntimeInbox claim、ConveyorQueueMembership writer、ECS status GET + command POST 必须有基准场景；优化不得绕过 ECS 实时 IDLE 准入事实 |

**Benchmark gate 最小验收**：

| 场景 | 基线规模 | 验收口径 | 失败处理 |
| --- | --- | --- | --- |
| Plane snapshot | 1 条 WorkLine、10 队列、50 设备、100 active sessions、200 active objects | P95 < 500ms；10x load P95 < 1.5s；返回 `truncated/total_counts` | 阻塞对应 Phase 完成 |
| RuntimeInbox claim | 1,000 unprocessed inbox、4 worker 并发 claim | 不重复 claim；dead-letter 可重放；claim P95 需记录基线 | 阻塞 runtime/orchestration 完成 |
| ConveyorQueueMembership writer | 同 WorkLine 200 active memberships、同 bin/placeholder 并发写入 | 唯一冲突只幂等重读或 RECONCILING；主 callback 不回滚 | 阻塞动态队列模型完成 |
| ECS status + command POST | status GET + command POST 串行/限流/timeout/mock failure | dispatch 前必须验证实时 IDLE 或有效快照；ERROR/OFFLINE/UNKNOWN 短退避耗尽进入 RuntimeHold | 阻塞 DeviceCommand contract 完成 |

基准命令必须随对应任务固化到 `tests/load/` 或等价脚本：RuntimeInbox claim 随 CEO-007，ConveyorQueueMembership writer 随 CEO-008/ENG-016，ECS status + command POST 随 CEO-010，Plane snapshot 随 Phase 3 `plane-read-model-spec.md`。任何性能优化不得删除 ECS 实时 IDLE 准入、idempotency、HMAC 或 evidence 写入。

### 8.4 可观测性设计

| 维度 | 设计 |
| --- | --- |
| **Trace** | `ExecutionCorrelation.trace_id` 跨域追踪；`evidence_json.trace_id` 单条 evidence 追踪 |
| **Metrics** | `audit_logs`（plane 读取 + 安全事件 + 破坏性迁移审计 + WMS 漂移告警）；RuntimeInbox backlog；`correlation_resolution_failed_total`；Outbox blocked；DeviceCommand ACK age；WMS breaker/evidence |
| **Logging** | 结构化日志；统一 `payload` schema |
| **Alerting** | 5/30 分钟 RECONCILING 超时升级；10x load 触发降级告警；HMAC 失败 401 告警；idempotency 409 告警；WMS breaker OPEN/HALF_OPEN；evidence 写入失败；DeviceCommand ACK 前假死 |
| **Dashboard** | 平面态势（scene + snapshot）；冲突视图（conflicts[]）；恢复决议视图（resolution decisions） |

**观测口径**：

- 必须区分 RuntimeInbox `RESOURCE_WAIT` 与 Outbox `BLOCKED_RESOURCE`，避免把入口阻塞、外部履约等待和设备 dispatch busy 混成一个指标。
- 必须区分本地 `DeviceRuntime.diagnostic_state=IDLE` 与 ECS 实时 status probe `IDLE`；后者才是 dispatch admission 的放行事实。
- WMS timeout、5xx、business reject、breaker open、evidence 写入失败按 operation/provider 聚合，阈值在现场数据稳定后配置，不在首版硬编码。

### 8.5 可维护性设计

| 维度 | 设计 |
| --- | --- |
| **Modularity** | 8 个域独立演进；域间通过 port 接口；域内 repository 隔离 |
| **Testability** | 关键业务语义 characterization tests；每个新 capability unit + integration + regression 三层覆盖 |
| **Documentation** | 顶层设计（本文件）+ 8 个 ADR（关键决策）+ 2 个 review 存档（autoplan 评审）；详细 SPEC 不在本文展开，Phase 启动前或启动时按需生成 |
| **Migration discipline** | 可逆 schema 走 Alembic upgrade + downgrade；数据重塑和破坏性清理必须说明 dry-run、快照回滚和清理矩阵 |
| **Naming discipline** | 目标态命名优先；跨域 correlation key 替代 session FK；typed Pydantic 模型替代裸字符串/JSON |

---

## 9. 模块设计

### 9.1 workline 配置域

**职责**：滚筒线/队列/入口/出口/设备角色/资源边界/平面布局/启停配置生命周期

**核心实体**：

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `WorkLine` | `line_code`, `manifest_yaml`, `status`, `config_version` | 配置聚合根；只管理 manifest 生命周期，不保存运行状态 |
| `ConveyorLine` | `workline_id`, `code`, `layout` | 滚筒线配置来自 manifest；运行态队列 membership 不写回配置 |
| `PipelineQueue` | `conveyor_code`, `code`, `role`, `capacity`, `order_policy` | `code` 仅在当前 WorkLine manifest 内唯一，不提升为系统级 enum |
| `EntryPoint` / `ExitPoint` | `conveyor_code`, `queue_code`, `external_handler` | 表达 CTU/AGV/WMS 等外部交接点，不直接调度外部设备 |
| `Device`（配置） | `role`, `code`, `capabilities` | 只声明设备角色和能力；运行时状态归 device 域 |
| `SafetyZone` | `affected_workline_codes`, `affected_device_codes`, `affected_conveyor_codes`, `recovery_policy` | 定义故障影响范围；不包含 PLC/坐标/安全回路控制字段 |

**API**（v1 router，`src/app/workline/v1/`）：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/worklines/` | GET | 列出 WorkLine |
| `/worklines/{id}` | GET | WorkLine 详情（含 manifest） |
| `/worklines/{id}/manifest` | GET | manifest YAML |
| `/worklines/{id}/queue-memberships` | GET | 队列 active memberships（经 `ConveyorQueueMembership` 查询服务） |
| `/worklines/{id}/plane/scene` | GET | PlaneSceneView（详见 §5.2） |
| `/worklines/{id}/plane/snapshot` | GET | PlaneSnapshot（详见 §5.2） |

**Boot-time manifest validator**：

WorkLine 激活时**必须**运行：

1. 拉 `ConveyorQueueMembership.ACTIVE` 集合的 `queue_code`
2. 与 manifest `pipeline_queues.code` 集合做集合差
3. 不匹配行打 `warnings[].code=STALE_QUEUE_CODE`，**不**删除
4. 未知队列写入尝试直接拒绝并生成 RuntimeHold
5. 校验 required device role、capability、SafetyZone 归属和 shared-device 影响范围；缺失或冲突时拒绝激活

**SafetyZone / shared-device 拓扑**：

- WorkLine manifest 必须能描述设备、滚筒线、缓存位、机械臂、共享输送线与 `SafetyZone` 的归属关系。
- 同一设备或输送线被多个 WorkLine 共享时，运行时调度必须按 `SafetyZone` 计算影响范围；设备进入 `ERROR` / `ESTOP_PRESSED` / `MAINTENANCE` 时，只冻结受影响的 session/effect，不用整库停摆。
- `ESTOP_PRESSED`、安全门、光栅等物理安全事实只能由 ECS/device event 进入 WES；WES 不复位 PLC，不解除硬件急停，只记录 evidence、停止新 effect 并等待 ECS 状态恢复或人工 reconcile。
- `SafetyZone` 不替代 `DeviceDispatchPolicy`。前者定义影响范围，后者定义 dispatch 排队、限流、deadline 和取消。

**Manifest 版本冻结**：

- `ExecutionSession` 创建时必须记录 `manifest_version = WorkLine.config_version`。
- session 生命周期内的队列、入口、出口、设备角色、容量、平面布局解析都以 pin 住的 `manifest_version` 为准。
- WorkLine manifest 更新只影响新 session；已有 RUNNING/HOLD session 不热切到新配置。
- 需要现场热切换时，必须走 `DRAINING -> HOLD -> VALIDATE -> ACTIVATE` 流程：停止为旧版本创建新 effect，等待在途命令完成或人工 hold，验证新 manifest 后再创建新 session。
- manifest validator 必须同时支持 boot-time 与 activation-time；activation-time 失败不得污染 active projection。

**DRAINING / HOLD / VALIDATING 边界（M3 回归）**：

- `DRAINING` 是 WorkLine 配置域状态：停止为旧 manifest 创建新 session 或新 effect，等待旧 session 自然结束或被人工 hold。
- `HOLD` 是 ExecutionSession 状态：暂停某个 session 的新 effect，不代表整条 WorkLine 不可用。
- `VALIDATING` 是 WorkLine manifest 激活前临时状态：只运行 manifest validator、资源边界校验和 active projection 污染检测。
- 热切换流程固定为 `WorkLine.ACTIVE -> DRAINING -> session.HOLD(per active session) -> VALIDATING -> ACTIVE`；失败则回到 `MAINTENANCE` 并保留旧 manifest_version 的 evidence。

### 9.2 runtime/orchestration 执行域

**职责**：ExecutionSession/Inbox/Timeline/Hold/EffectPort；RuntimeIntentLog；ExecutionCorrelation

**核心实体**：

runtime 域内表使用 `execution_session_id` 作为 `ExecutionSession` FK；跨域实体只允许持有 `ExecutionCorrelation.correlation_id`。

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `ExecutionSession` | `workline_id`, `manifest_version`, `state`, lifecycle timestamps | 唯一 session 聚合根；创建时 pin WorkLine manifest version |
| `RuntimeInbox` | `execution_session_id?`, `correlation_id?`, `provider_code`, `event_type`, `source_event_id`, `payload_hash`, `status`, retry fields | ACK-before-processing 边界；未解析入站事件允许暂时无 session/correlation |
| `RuntimeTimeline` | `execution_session_id`, `trace_id`, `correlation_id?`, `event_type`, `occurred_at` | append-only 执行轨迹；不作为 owner 状态源 |
| `RuntimeHold` | `execution_session_id`, `correlation_id?`, `reason`, `hold_type`, `resolved_at` | 暂停新 effect 的运行时闸门；解除必须有 evidence |
| `RuntimeIntentLog` | `execution_session_id`, `correlation_id`, `provider_code`, `target_domain`, `target_action`, `request_hash`, `idempotency_key`, `dispatch_status`, retry fields | outbox/effect ledger；不是下游状态源 |
| `ExecutionCorrelation` | `correlation_id`, `execution_session_id?`, `trace_id`, `source_event_id`, `business_owner_key` | 跨域唯一 correlation key；`execution_session_id` 仅 runtime 域内强 FK |

**RuntimeInbox 处理契约**：

- Callback API 在鉴权、schema normalize 和幂等检查通过后立即写入 `RuntimeInbox(status=RECEIVED)` 并 ACK；不得同步推进 session、projection 或 device runtime。
- 外部 callback 入站时允许 `session_id=None` / `correlation_id=None`；worker 通过 `source_event_id`、`command_code`、`request_id`、`idempotency_key` 或 normalized evidence 解析 correlation。解析成功写 `RESOLVED`，解析失败写 `FAILED` + diagnostic，不回滚原始 ACK。
- 异步 worker 以 `RECEIVED -> PROCESSING -> PROCESSED` 为唯一成功路径；处理异常写 `FAILED`、`attempt_count + 1`、`last_error_*` 和 `next_retry_at`。
- `FAILED` 超过重试上限或超过业务 deadline 后转 `DEAD_LETTER`，创建 `RuntimeHold` 并进入人工审计队列。
- 人工重放只能从 `DEAD_LETTER` 复制生成新的 inbox 记录，保留原 `payload_hash/source_event_id/idempotency_key` 作为审计链；不得原地改写历史 payload。
- `source_event_id + provider_code + event_type` 必须唯一；同 key 同 hash 直接返回既有 ACK，同 key 不同 hash 返回 409 并写安全审计。
- RuntimeInbox 积压超过阈值时停止创建新 effect，但 callback 仍可在鉴权通过后 ACK + 持久化 evidence，防止外部系统重试风暴。

**API**（v1 router，`src/app/runtime/orchestration/v1/`）：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/sessions/` | POST | 创建 ExecutionSession |
| `/sessions/{id}` | GET | ExecutionSession 详情 |
| `/sessions/{id}/inbox` | GET | RuntimeInbox（分页） |
| `/sessions/{id}/timeline` | GET | RuntimeTimeline（分页） |
| `/sessions/{id}/holds` | GET | RuntimeHold（分页） |
| `/sessions/{id}/intents` | GET | RuntimeIntentLog（分页，只读） |
| `/correlations/` | POST | 创建 ExecutionCorrelation |
| `/correlations/{id}` | GET | ExecutionCorrelation 详情 |

**Intent 写入口约束**：生产业务 API 不提供公开 `POST /intents`。`RuntimeIntentLog` 只能由 runtime/orchestration worker、runtime capability 或受控内部任务在通过 admission、幂等和状态门禁后创建；人工调试入口如确需保留，只能放在 internal/admin 路由，默认关闭并写审计。API 层不得绕过 Runtime capability 直接创建 effect。

**EffectPort 接口**：

| 契约项 | 要求 |
| --- | --- |
| 输入 | 已持久化的 `RuntimeIntentLog` |
| 输出 | effect id、dispatch 结果、`correlation_id`、deadline、拒绝原因 |
| 状态 | `ACCEPTED` / `REJECTED` / `BLOCKED_BY_CB` / `FAILED` |
| 不变量 | dispatch 前必须完成 admission、幂等和状态门禁；不得由 API 层直接调用 provider client |

**RuntimeCapabilityContext 接口**：

| 能力 | 用途 |
| --- | --- |
| `readonly_facts` | 只读事实查询 port，可注入 WMS 主数据/单据/库存查询或测试 fake provider |
| `effects` | 唯一允许触发 WMS/RCS/设备副作用的出口 |
| `clock` | 统一时间来源 |
| `idempotency` | 跨域幂等检查与审计 |

`readonly_facts` 可注入 WMS 主数据/单据/库存查询端口，也可在测试中替换为 fake provider；`effects` 是唯一允许触发 WMS/RCS/设备副作用的出口。Capability 不允许持有 `wms_integration` service locator、HTTP client、供应商 DTO 或 provider exception。

**Effect ledger 约束**：

- `RuntimeIntentLog` 是 outbox/effect ledger，不是下游状态源；下游状态仍归 handling/device/resource/material/wms_integration 各自拥有。
- 每条 effect 必须带 `correlation_id`、`provider_code`、`idempotency_key`、`request_hash`，用于崩溃恢复、幂等复查和乱序回调归因。
- dispatch worker 只能从 `PENDING` 抢占到 `DISPATCHING`，成功写 `DISPATCHED/ACKED`；失败写 `FAILED` 并保留 `attempt_count/last_error_*`。
- 进程崩溃恢复时只重放 `PENDING` 或过期 `DISPATCHING` 且 `request_hash` 一致的记录；不允许重新构造 payload 发起新 effect。

### 9.3 handling 搬运意图域

**职责**：搬运意图、请求生命周期、幂等、超时、重试

**核心实体**：

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `HandlingOperation` | `workline_id`, `kind`, `coarse_business_status`, `source`, `target`, `correlation_id`, `idempotency_key` | 只表达 WES 业务搬运意图；不持有外部履约 11 态细节 |
| `HandlingMove` | `handling_operation_id`, from/to location, `kind`, `status`, `evidence_json` | 记录业务意图下的局部动作；事实依据必须来自 typed evidence |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/handling/operations/` | POST | 创建 HandlingOperation |
| `/handling/operations/{id}` | GET | HandlingOperation 详情 |
| `/handling/operations/{id}/moves` | GET | HandlingMove 列表 |

滚筒线队列查询不挂在 Handling API 下。队列当前态由 runtime/orchestration 写入，WorkLine/plane 侧提供只读查询和展示入口。

**WmsFulfillmentPort 集成**：

Handling 只表达 WES 业务搬运意图和本地完成语义；外部履约 11 态机的事实源归 `WmsFulfillmentRequest` / fulfillment adapter。Handling 可通过 `correlation_id`、`RuntimeIntentLog` 和 fulfillment evidence 派生粗粒度状态，但不得直接持有或双写 `SENT/ACCEPTED/RUNNING/BLOCKED_BY_CB/TIMEOUT` 等外部履约细态。

状态归属固定如下：

| 状态层 | Owner | 允许写入方 | 说明 |
| --- | --- | --- | --- |
| 业务搬运意图 | `HandlingOperation` | handling service / runtime capability | 表达 WES 需要完成的业务动作及最终本地语义 |
| 外部履约请求 11 态机 | `WmsFulfillmentRequest` | `WmsFulfillmentPort` adapter + callback worker | 表达 WMS/RCS/provider 是否接收、执行、拒绝、超时或被 CB 阻塞 |
| 作业位置事实 | `RuntimeLocationEvent` / active projection writer | runtime worker / reconciliation | 表达对象在 WES 作业期位置，不由 Handling 或 WMS ACL 直接改写 |

`HandlingOperation.coarse_business_status` 只能由本地业务语义和 evidence 汇总推进：`PLANNED -> WAITING_FULFILLMENT -> IN_PROGRESS -> COMPLETED`，或进入 `REJECTED/FAILED/CANCELLED/RECONCILING`。若需要展示外部履约细态，查询层通过 `correlation_id` 联合 `WmsFulfillmentRequest.status` 返回派生视图，不落双份状态。

**满箱/换箱/换架完成语义**：

- `FULL_BOX_EXCHANGE` / `RACK_BIN_EXCHANGE` / 满货架换架不按普通单步 `BIN_MOVE` 处理。
- 这类 operation 必须显式进入 callback + reconciliation 完成语义：WMS/RCS/ECS callback 只能证明外部动作结果，WES 还必须校验 active projection、queue membership 和目标箱/货架 evidence 后才能关闭。
- 若外部 callback 成功但本地投影冲突，operation 进入 `RECONCILING`，不得覆盖 active projection 或直接标记完成。
- 目标态不要求保留旧 `completion_policy` 字段名；可以用枚举、状态机或 policy object 表达，但语义必须可查询、可测试、可审计。

### 9.4 resource 运行投影域

**职责**：作业期运行投影（不复制 WMS 主数据）

**核心实体**：

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `RackPlacement` | `workline_id`, rack `ExternalReference`, `work_position_code`, `status`, `correlation_id`, `evidence_json` | 表达货架在 WES 工作位的作业期投影 |
| `RackBinMount` | `rack_placement_id`, bin `ExternalReference`, `cell_code`, `status`, `correlation_id`, `evidence_json` | 表达料箱与货架格位的作业期挂载关系 |
| `BinPlacement` | `work_position_code`, bin `ExternalReference`, `status`, `correlation_id`, `evidence_json` | 表达料箱在工作线/工作位的作业期投影 |
| `BinMaterialMount` / `BinCellOccupancy` | `pkg_code`, `material_identity_key`, `cell_code`, occupancy metrics, `correlation_id` | 表达 WES 作业期物料与箱格占用，不复制库存主数据 |
| `ResourceStateEvent` | `workline_id`, `event_type`, `source_event_id`, payload, `occurred_at` | 资源投影事件记录；按 `source_event_id` 幂等 |
| `RuntimeLocationEvent` | `object_type`, `object_key`, `location_scope`, `location_code`, `business_step`, `source`, `evidence_json`, `correlation_id` | append-only 位置事实；active projection 和查询视图均由它派生 |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/resource/rack-placements/` | GET | 列出 rack placement |
| `/resource/rack-bin-mounts/` | GET | 列出 rack-bin mount |
| `/resource/bin-material-mounts/` | GET | 列出 bin-material mount |
| `/resource/state-events/` | GET | 列出 state event |

**FK 策略**：

- WES 自有实体（RackPlacement / RackBinMount / BinPlacement / BinMaterialMount）补 SQL FK
- 外部对象用 typed `ExternalReference`（无 FK）
- 跨域关联用 `ExecutionCorrelation.correlation_id`（无 `execution_session.id` FK）

**work_position_code 归属（M9 回归）**：

- `work_position_code` 是 WES 内部工作位编号，例如 `WP-KITTING-01`，用于运行投影、plane 展示和设备调度。
- WMS `location_code` 是外部库位/区域编码；二者通过 WorkLine manifest 或 WorkLine 配置中的映射关系关联，不允许混用。
- 映射关系来源于 `WmsMasterDataPort.list_locations()` / `list_zones()` 的只读结果，经配置发布流程写入 WorkLine manifest；运行时只引用 pin 住的 `manifest_version`。
- WES 内部查询默认暴露 `work_position_code`；需要回传 WMS 时，通过映射表转换为 WMS `location_code` 并写入 evidence。

**位置事实契约**：

- `RuntimeLocationEvent` 是 append-only 事实表，表达作业期对象在 WES 视角下的 `what/where/when/why/source`。
- `RackPlacement`、`BinPlacement`、`ConveyorQueueMembership`、`MaterialUnit.location_summary` 和 `PlaneSnapshot` 均由 `RuntimeLocationEvent` 或同等 evidence 投影得到。
- WMS 仍是货架、料箱、物料、库存和单据主数据权威；WES 只拥有作业期位置事实与 active projection，不创建 WMS 主数据副本。
- 查询“某个料箱/料盘/物料在哪里”必须返回 `location_scope/location_code/source/evidence_at/correlation_id`，不能只返回裸位置字符串。
- 位置事实冲突时不覆盖旧值，必须写冲突 evidence 并进入 `RECONCILING` 或 `RuntimeHold`。

### 9.5 material 物料根实体域

**职责**：WES 作业期料盘/物料处理单元身份；当前位置只保留投影摘要，不拥有位置事实源

**核心实体**：

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `MaterialUnit` | `pkg_code`, `material_identity_key`, material/vendor/lot/date 派生身份字段, `status`, `location_summary`, `current_session_correlation_id` | WES 作业期唯一自有根实体；`location_summary` 只读投影摘要，不能被 material service 直接写位置事实 |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/material/material-units/` | GET | 列表 |
| `/material/material-units/{pkg_code}` | GET | 详情 |
| `/material/material-units/location-query` | GET | `MaterialLocationQuery`（按 pkg_code / material_identity / workline / status 查） |

**Authority**：WES material 域是**唯一自有根实体域**（作业期料盘/物料处理单元身份）——其他域不能直接修改身份字段。物料主数据、批次、库存数量、货主和单据仍归 WMS；`material_identity_key` 只用于 WES 作业期归因、查询和投影。`location_summary` 不是事实源，只能由 RuntimeLocationEvent projection writer 更新，material service 不得直接写位置。

### 9.6 device 设备接入域

**职责**：ECS/设备上位机 API 接入、设备角色、EVENT/COMMAND/RESULT、设备诊断。

**边界**：

- WES 只按 `third_party_integration_whitepaper.md` 调用 ECS/设备上位机 HTTP API。
- WES 下发的是 `task_type + params` 业务命令，只包含逻辑位置和业务参数。
- WES 不与 PLC 通讯，不下发 PLC 点位、物理坐标、关节角度、速度曲线、安全回路或急停复位指令。
- 硬件防呆由 ECS 自主完成；WES 只根据 ECS 暴露的设备状态、Ack、Result、Event 做业务编排。
- `ESTOP_PRESSED`、安全门、光栅等安全事件只能由 ECS 转换为 WES event/evidence/RuntimeHold；恢复必须来自 ECS 状态回传或人工 reconcile。

**核心实体**：

| 实体 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `DeviceRuntime` | `device_code`, `role`, `diagnostic_state`, heartbeat/result/event timestamps, status snapshot TTL, current command | 设备运行诊断状态来自 ECS/device，不包含 PLC 级控制状态 |
| `DeviceDispatchPolicy` | `device_role`, `capability_code`, priority/concurrency/deadline/order policy, status snapshot TTL | 定义设备选择、限流、有界等待和取消策略 |
| `DeviceEvent` | `device_code`, `event_type`, `event_id`, `sequence_no`, payload, `source_event_id` | 缺 `event_id` 或乱序事件只落 evidence + diagnostic，不推进业务 |
| `DeviceCommand` | `device_code`, `command_code`, `task_type`, payload, `correlation_id`, deadline/ack deadline, `idempotency_key`, lease, ack status | WES 下发给 ECS 的业务命令；不包含 PLC/坐标/关节/安全回路字段 |
| `DeviceResult` | `command_code`, result status, payload, `evidence_json`, `occurred_at` | 动作完成事实只能由 ECS callback/result 推进 |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/device/devices/` | GET | 设备列表 |
| `/device/devices/{code}` | GET | 设备详情 |
| `/device/commands/` | POST | 发送命令 |
| `/device/commands/{id}/result` | GET | 查询结果 |
| `/device/events/` | GET | 事件流 |

**Command-Ack-Callback 约束**：

- DeviceCommand dispatch 前必须先调用 ECS `GET /api/v1/device/status` 或读取 `now <= DeviceRuntime.status_valid_until` 的快照，确认目标设备 `status=IDLE`。
- `status_snapshot_ttl_ms` 由 manifest 或 `DeviceDispatchPolicy` 定义；默认 1000ms。快照过期必须重新查询 ECS，查询失败按状态查询超时处理。
- 若设备状态为 `RUNNING`，Runtime 不下发命令，进入有界等待：按 `wait_poll_interval_ms` 轮询或订阅 ECS 状态变化，直到设备变为 `IDLE` 或到达 `dispatch_deadline_at`。
- 若等待到 `dispatch_deadline_at` 仍未变为 `IDLE`，写 `DeviceCommand.ack_status=TIMEOUT`，创建 RuntimeHold；不进入无限排队。
- 若设备状态为 `ERROR` / `OFFLINE` / `UNKNOWN` / `MAINTENANCE`，或状态查询超时，Runtime 不下发命令，按指数退避重试（默认 1s / 2s / 4s，最多 3 次）。
- 故障/查询超时退避耗尽后写 `DeviceCommand.ack_status=TIMEOUT` 或 `REJECTED`，创建 RuntimeHold。
- DeviceCommand 下发后只以 ECS HTTP `200 Accepted` 表示“收到并接受”，不代表动作完成。
- 动作完成必须由 `/api/v1/callback/result` 回传 `command_code` 后推进。
- ACK 前等待必须有 `ack_deadline_at` 或等价 lease；设备未 ACK 的命令不得无限停留在等待态。
- ACK deadline 到期后 Runtime 必须扫描并写入 diagnostic/RuntimeHold；是否取消或人工恢复由 `DeviceDispatchPolicy` 和 ReconciliationManager 决定。
- `/api/v1/callback/event` 只 ACK，不允许在响应体中返回下一步动作；Runtime 后续通过 DeviceCommand 下发动作。
- 同一 `command_code` 重试不得触发重复物理动作；WES 侧保留 `request_hash` 和 `idempotency_key`，ECS 侧按白皮书缓存最近 1 小时 command_code。
- 缺 `event_id` 或乱序 `sequence_no` 的事件只落 evidence + diagnostic，不直接推进 session 或 projection。

**DeviceState 扩展语义（M6 回归）**：

- `UNKNOWN`：WES 未拿到有效 ECS 状态，或状态快照过期且查询失败；禁止下发命令，只能重查 ECS 或等待状态事件。
- `MAINTENANCE`：设备本地或 ECS 标记维护中；Runtime 选设备时跳过该设备，直到收到 `MAINTENANCE_LEFT` / `DEVICE_ONLINE` 且状态重新变为 `IDLE`。
- `OFFLINE` 表示 ECS 明确回传离线；`UNKNOWN` 表示 WES 无法确认。二者都不能派发，但告警和排障路径不同。

**DeviceDispatchPolicy 调度契约**：

- Runtime 先按 `device_role + capability_code + manifest_version` 选择候选设备，再按 `priority + deadline + order_policy` 生成候选命令队列。
- 同一 `device_code` 默认 in-flight = 1；只有 manifest 显式声明并通过 ECS 能力校验后才允许提高 `concurrency_limit`。
- 多设备具备同一能力时，优先选择 `IDLE` 且状态快照未过期的设备；若全部 `RUNNING`，只等待到最早 `dispatch_deadline_at`，不得无限排队。
- session 进入 `HOLD` / `RECONCILING` / `CLOSED` 时，未下发命令必须取消或冻结；已下发命令只能等待 ECS callback 或人工 reconcile。
- Runtime 不做 PLC 级抢占、急停复位或运动控制；这些只能由 ECS/现场安全系统处理后以事件形式回传 WES。

**WorkLine 启停门禁**：

- manifest 中标记为 `required=true` 的设备若处于 `OFFLINE` / `UNKNOWN` / `MAINTENANCE`，WorkLine 不允许从 `INACTIVE` 切到 `ACTIVE`。
- manifest 中标记为 `optional=true` 的设备不可用时，WorkLine 可启动，但对应 capability 从候选设备集中剔除，并在 `PlaneSnapshot.warnings[]` 中展示。
- RUNNING session 期间 required 设备变为 `OFFLINE` / `MAINTENANCE` 时，Runtime 进入 `RuntimeHold`，停止新 effect，等待 ECS 恢复或人工 reconcile。

**Authority**：device 到位信号、硬件防呆和设备忙闲状态归 ECS/device；WES 只接收并转换为业务事件。WES 不拥有 PLC 通讯、安全回路、坐标映射或运动控制权。

### 9.7 wms_integration ACL 域

**职责**：WMS 反腐层：能力面 ports + ACL；不复制 WMS 主数据

详见 §5.1 能力面 Port 详细。

**核心数据**：

| 数据 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `WmsFulfillmentRequest` | fulfillment kind, source/target, 11 态 `status`, immutable `request_hash`, `idempotency_key`, `correlation_id` | 外部履约状态 owner；WMS/RCS/provider callback 和 adapter 推进 |
| `WmsCallbackEnvelope` | callback type, `source_event_id`, `source_version`, signature/timestamp/nonce, raw body hash, normalized evidence, normalizer status | 外部 callback 原始归档与 normalize 结果；外部不直接写 envelope API |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/wms-integration/fulfillment/requests` | POST | 创建履约请求 |
| `/wms-integration/fulfillment/requests/{id}` | GET | 履约请求详情 |
| `/wms-integration/callback-envelopes` | GET | 查询已归档 WMS callback envelope（只读，外部不写入） |
| `/wms-integration/inventory/query` | POST | 查询库存 |
| `/wms-integration/reconciliation/reconcile` | POST | 触发对账 |

**入口约束**：外部 callback 写入口只允许 §5.3 的统一 callback API；`wms_integration` 不提供第二个外部 POST 入口，只提供 normalizer、port 和只读 envelope 查询。

**目标态优先**：可复用 `src/app/wms_integration/` 已有 ACL 实现，但允许破坏性整理目录、模型和 import。

### 9.8 reconciliation 对账域

**职责**：统一冲突登记、隔离动作、决议输出和审计；不直接写跨域 owner 状态

详见 §6.4 RECONCILING 冲突决议模型。

**核心数据**：

| 数据 | 关键字段组 | 设计约束 |
| --- | --- | --- |
| `ReconciliationRecord` | `conflict_type`, detector/reason, `evidence_json`, `resolution_decision`, `owner_scope`, `allowed_next_effect_scope`, `recovery_path`, `resolved_at`, `correlation_id` | 决议记录和审计字段；不作为跨域 owner 状态写入指令 |

**API**：

| Endpoint | Method | 用途 |
| --- | --- | --- |
| `/reconciliation/conflicts` | GET | 当前冲突列表 |
| `/reconciliation/conflicts/{id}` | GET | 冲突详情 |
| `/reconciliation/reconcile` | POST | 人工 reconcile |
| `/reconciliation/resolve` | POST | 写入 resolution_decision 并标记解决 |

**API 写入约束**：

- `/reconciliation/reconcile` 与 `/reconciliation/resolve` 只能写 `ReconciliationRecord.resolution_decision`、`owner_scope`、`allowed_next_effect_scope`、`resolved_at` 和 audit log。
- API 不得直接修改 `WmsFulfillmentRequest`、`HandlingOperation`、`ExecutionSession`、`DeviceCommand` 或 active projection；这些 owner 只能根据 reconciliation evidence 自行转移。
- 人工 resolve 必须记录操作者、依据、object scope、允许释放的 effect 范围和幂等键。

**SMT / NG / WMS 对账语义**：

- SMT 入库 P0 可以先保证 WES 本地可信最终去向，但完整目标态必须把目标箱回写失败、WMS 确认/拒绝、NG evidence 消费、WMS confirmation version 和 session 结算材料纳入统一对账。
- NG 周转箱、NG 库位、返工工单主档不归 WES 维护；WES 只保存 typed `ExternalReference`、RuntimeHold、物理交接 evidence、解除条件和回调归因。
- WMS 版本冲突或目标箱回写失败时，不允许本地 projection 冒充 WMS 事实成功；必须写 `ReconciliationRecord`，等待 WMS 重试、人工 reconcile 或 provider callback。
- 返入口真实 EVENT 若需要归因，只关联原 `ExecutionCorrelation` / material identity / external reference，不恢复旧 plugin session 语义。

---

## 10. 实施计划

5 个 Phase，按 critical path 严格串行；Phase 内任务可并行。实施默认允许破坏性清理，不设置旧 API / 旧表 / 旧插件兼容目标。

### 10.1 Phase 0: 目标态锁定（5 项必做）

**目标**：锁定 P0 系统目标和目标态边界，防止后续实现被旧 WorkLine/plugin 形态反向约束。

| 顺序 | Task | Effort | 关联文件 | 验证 |
| --- | --- | --- | --- | --- |
| 1 | **P0-001 目标态契约文档** | M | 本文档 + `docs/architecture/target-state-contract.md` | 明确业务能力语义、域边界、状态所有权、允许破坏性删除范围 |
| 2 | **P0-002 Legacy 清理矩阵** | M | `docs/architecture/legacy-cleanup-matrix.md` | 每个旧模块标记 delete / rebuild / move / keep-contract |
| 3 | **P0-003 行为契约测试基线** | L | `tests/` | 覆盖 start admission、runtime snapshot、handoff、resource projection、分拣机/粗分机入库业务基线的目标语义 |
| 4 | **P0-004 ExecutionCorrelation 迁移矩阵** | L | `docs/architecture/session-correlation-matrix.md` | per-file 迁移路径发布；跨域 FK 收敛策略明确 |
| 5 | **P0-005 ECS 设备接入边界合同** | M | `docs/integration/third_party_integration_whitepaper.md`, `docs/architecture/device-command-contract.md` | 明确 WES 只经 ECS API 下发业务命令；Event_Push 只 ACK；不与 PLC 通讯；RUNNING 有界等待；ERROR/OFFLINE 短退避 |

**Phase 0 完成门禁**：

- [ ] `target-state-contract.md` 发布，不含旧 API/旧表兼容承诺
- [ ] `legacy-cleanup-matrix.md` 发布，旧 WorkLine/plugin/runtime 每个入口都有处理策略
- [ ] 行为契约测试可运行，保护业务语义而非旧代码形态
- [ ] 分拣机/粗分机入库基线被描述为目标态能力，不引用旧 plugin 接口、旧 context schema 或 fake allocator
- [ ] `session-correlation-matrix.md` 发布
- [ ] `device-command-contract.md` 发布，字段与白皮书 Command-Ack-Callback 一致

### 10.2 Phase 1: 目标态骨架与 WMS ACL

**目标**：先建立目标态骨架和 runtime/orchestration 最小运行骨架，不迁移旧执行入口。Phase 1 的完成标准是“runtime 能独立接收 inbox、记录 intent、关联 correlation”，不是 P0 最小可运行闭环，也不是旧 WorkLine/plugin/runtime 已经清空。

| Task | Effort | 关联文件 | 验证 |
| --- | --- | --- | --- |
| **CEO-001** 整理 `wms_integration/` 并补齐 WMS 能力面 ports | M | `src/app/wms_integration/` | 能力面 port 单元测试；覆盖 `wms_rcs_interface_requirements.md` P0 接口映射；内部域无 WMS DTO/client import |
| **CEO-002** 4 方案决策表归档 | S | 本文档 §3.8 | 已归档 |
| **CEO-005** 查询响应 schema 增加 `scope/authority/source/evidence_at` 强制字段 | S | `src/app/*/schemas/` | schema 校验 + 测试 |
| **CEO-006** Authority Matrix 文档发布 | S | `docs/architecture/authority-matrix.md` | 9 类事实类型 + 权威来源 |
| **CEO-007** runtime/orchestration 最小骨架 | M | `src/app/runtime/orchestration/`, `docs/architecture/runtime-orchestration-spec.md` | ExecutionSession / RuntimeInbox / RuntimeIntentLog / ExecutionCorrelation 类型分离；最小 worker + 单元测试 |
| **CEO-008** `ConveyorQueueMembership` 目标模型 | M | `src/app/runtime/orchestration/`, `src/app/workline/` | manifest queue_code 校验 + active 唯一约束测试 |
| **CEO-009** `RuntimeCapabilityContext` / `CapabilityPortRegistry` | M | `src/app/runtime/` | capability 只能拿到 port contract；静态检查拒绝 `src.app.wms_integration.*` DTO/client/异常 import |
| **CEO-010** `DeviceCommand` ECS API contract + manifest concurrency limit | M | `src/app/device/`, `docs/architecture/device-command-contract.md` | command_code 幂等、dispatch 前 IDLE 校验、RUNNING 有界等待、ERROR/OFFLINE 短退避、Event_Push 只 ACK、缺 event_id 不推进、in-flight 限制测试 |
| **CEO-011** WorkLine manifest version pin | M | `src/app/workline/`, `src/app/runtime/orchestration/` | RUNNING session 固定 manifest_version；新 manifest 只影响新 session；activation-time validator 测试 |
| **CEO-012** WorkLine SafetyZone / shared-device manifest schema | M | `src/app/workline/`, `src/app/device/` | shared device 影响范围、required/optional role、SafetyZone validator 测试 |

**Phase 1 完成门禁**：

- [ ] `wms_integration` MasterData / Document / InventoryQuery / InventoryTransaction / Fulfillment / Event / Reconciliation 7 个目标 port 全部实现
- [ ] `wms_rcs_interface_requirements.md` P0 基础数据、业务指令、回调事件均映射到目标 port
- [ ] 内部域无代码直接 import WMS 类型
- [ ] Runtime capability 注入仅暴露 port contract，不暴露 `wms_integration` 实现对象、HTTP client、DTO、异常或 service locator
- [ ] Authority Matrix 文档发布
- [ ] runtime/orchestration 最小骨架完成，RuntimeIntentLog 含 effect ledger 字段并支持崩溃重放
- [ ] DeviceCommand 只面向 ECS API，不包含 PLC/坐标/关节/安全回路字段；dispatch 前必须校验 ECS 设备状态为 IDLE
- [ ] DeviceRuntime 状态快照 TTL 与 DeviceDispatchPolicy 已纳入 manifest/schema 设计
- [ ] ExecutionSession 已 pin `manifest_version`
- [ ] 动态队列 membership 模型替代旧 8 enum 方案
- [ ] WorkLine manifest 能表达 SafetyZone、共享设备和影响范围；WES 不包含 PLC 直连字段

### 10.3 Phase 2: Runtime/Orchestration 迁移与 WorkLine 清空

**目标**：在 Phase 1 新 runtime/orchestration 骨架已独立可运行后，把旧 WorkLine/plugin/runtime 的执行状态、inbox、timeline、hold、effect dispatch 迁出或删除。旧执行入口不做兼容转发。

**启动条件**（满足全部才能启动 Phase 2）：

- Phase 0 全部 5 项完成
- Phase 1 全部任务完成
- 重新跑 autoplan 或同等深度评审，确认 B 方案可执行

#### 10.3.1 B 方案暂停/回退条件（C2 回归）

Phase 2 启动前必须执行 go/no-go 评审。以下任一条件成立时，暂停 B 方案，不进入 XL 级重建：

- Phase 0 P0-003 行为契约测试未覆盖关键业务语义，核心路径覆盖率低于 70%。
- Phase 1 CEO-007 runtime/orchestration 最小骨架无法在不污染状态源的前提下落地。
- 重新评审发现 2 个及以上 P0 阻塞项，或发现需要重新定义 WES/WMS/ECS 边界的基础假设错误。
- legacy cleanup matrix 中存在无法归类为 delete / rebuild / move / keep-contract 的核心入口。

**回退路径**：

| 路径 | 保留资产 | 追加成本 | 目标 |
| --- | --- | --- | --- |
| B -> C | 保留 `wms_integration` 7 port、External callback 鉴权、idempotency、行为契约测试 | 2-3 周 | 先完成 ACL 与外部边界，暂缓 runtime/orchestration 全量拆分 |
| B -> D | 保留 WorkLine manifest、动态队列模型、DeviceCommand ECS contract | 3-5 周 | 暂时保留 workline 单体运行入口，但删除旧 plugin 扩展方式 |
| B 暂停 | 保留 Phase 0/1 文档、测试和 schema 骨架 | 1 周内出复盘 | 重新评审目标态边界，不继续投入 Phase 2 |

回退不代表恢复旧 API/旧表兼容；只代表缩小重构范围，优先保留已验证的目标态契约和新边界。

| Task | Effort | 关联文件 | 验证 |
| --- | --- | --- | --- |
| Runtime/Orchestration 完整迁移 | XL | `src/app/runtime/orchestration/` | Timeline / Hold / EffectPort / worker / replay / dead-letter 补齐，旧 session 语义迁移到 ExecutionCorrelation |
| WorkLine 执行逻辑清空 | L | `src/app/workline/` | WorkLine 仅保留配置 CRUD、manifest、plane scene |
| Legacy 执行入口删除 | M | `src/app/workline/services/`, `src/workline_runtime/` | 旧入口被删除或替换；行为契约测试仍通过 |

**Phase 2 完成门禁**：

- [ ] `runtime/orchestration` 域独立落地
- [ ] WorkLine 不再拥有运行状态
- [ ] legacy 行为契约测试通过

### 10.4 Phase 3: 执行安全与恢复能力补全

**目标**：补全支持 WES 作业期可信恢复、对账、安全的子能力。

| Task | Effort | 关联文件 | 验证 |
| --- | --- | --- | --- |
| **ENG-002** `ReconciliationManager` + RECONCILING 决议模型 | M | `src/app/reconciliation/` | owner-scoped resolution decision + 5/30 分钟升级 + unit test |
| **ENG-003** WorkLine 启动 manifest validator | M | `src/app/workline/` | 集合差检测 + RuntimeHold 拒写 |
| **ENG-004** 11 态机补 4 条 timeout + CB `BLOCKED_BY_CB` | M | `src/app/wms_integration/state_machine.py` | 4 timeout 转移 + CB 集成测试 |
| **ENG-006** `ActiveObjectRegistry` 跨投影 active 归属仲裁读模型 | M | `src/app/active_objects/` | 3 路 UNION 冲突 policy 单元测试 |
| **ENG-008** External callback body HMAC + nonce TTL + allow-list | M | `src/app/callback/`, `src/app/wms_integration/`, `src/app/device/`, `src/core/api_security.py` | WMS/RCS/ECS/device 重放 + 篡改 + 时钟偏差 + allow-list 测试 |
| **ENG-009** idempotency_key 复合主键 + request_hash + session 审计 | M | `src/app/runtime/`, `src/app/wms_integration/`, `src/app/device/` | callback / fulfillment / device_command / device_event / reconciliation 409 + 安全审计事件 |
| **ENG-010** typed `ExternalReference` + typed evidence envelope + WMS drift job | M | `src/app/wms_integration/evidence/`, `docs/contracts/evidence-catalog.md` | GIN 索引 + drift 分类 |
| **ENG-011** RuntimeInbox backpressure + DeviceCommand lease | M | `src/app/runtime/`, `src/app/device/` | inbox 积压降级、死信/人工重放、per-device in-flight 限制、过期 lease 重放/取消测试 |
| **ENG-012** DeviceDispatchPolicy + DeviceRuntime TTL | M | `src/app/device/`, `src/app/runtime/`, WorkLine manifest | 设备能力选择、优先级、deadline、RUNNING 有界等待、状态快照过期重查 ECS、HOLD/RECONCILING 取消测试 |
| **ENG-013** RECONCILING 现场隔离语义 | M | `src/app/reconciliation/`, `src/app/runtime/` | 进入 RECONCILING 后禁发新 effect、冻结 projection 写入、人工恢复审计测试 |
| **ENG-016** Conveyor queue writer 并发幂等与诊断 | M | `src/app/runtime/orchestration/`, `src/app/workline/`, `src/app/reconciliation/` | PostgreSQL 锁/upsert、IntegrityError 重读、placeholder resolve、严格模式测试 |
| **ENG-017** 满箱/换箱/换架 callback + reconciliation 语义 | M | `src/app/handling/`, `src/app/reconciliation/` | FULL_BOX/RACK_BIN exchange 外部成功但本地冲突进入 RECONCILING 的合同测试 |
| **ENG-018** WMS evidence retention + breaker observability | M | `src/app/wms_integration/`, observability 配置 | retention/archive、breaker OPEN/HALF_OPEN、evidence 写入失败指标测试 |
| **ENG-019** Runtime worker / queue / ECS HTTP benchmark gate | M | `tests/load/`, docs | RuntimeInbox claim、queue writer、status GET、command POST 基准报告 |
| **DESIGN-001..005** 5 项 design 修复（schema_version、scene/snapshot 独立、目标态枚举、label/code 分离、极态清单） | 4×S + 1×M | `src/app/workline/v1/plane.py`, `src/app/workline/schemas/` | 单元测试 |

**Phase 3 完成门禁**：

- [ ] P0 最小可运行闭环完成：WorkLine manifest -> ExecutionSession -> RuntimeInbox -> RuntimeIntentLog -> DeviceCommand / WMS fulfillment -> PlaneSnapshot -> RECONCILING 可跑通一条受控作业链路
- [ ] RECONCILING 不再是黑洞状态；owner-scoped resolution decision 有测试覆盖，且 ReconciliationManager 不直接写 owner 状态
- [ ] WorkLine 启动时已知 queue_code typo 不会污染 active projection
- [ ] 11 态机覆盖所有可观察转移
- [ ] External callback 鉴权从"字段级"升级为"body 完整性级"，覆盖 WMS/RCS/ECS/device
- [ ] idempotency 跨域语义统一，覆盖 callback / fulfillment / device_command / device_event / reconciliation
- [ ] RECONCILING 具备软件禁发、投影冻结和人工恢复审计
- [ ] DeviceCommand lease 与 RuntimeInbox backpressure 已覆盖
- [ ] DeviceDispatchPolicy 与 DeviceRuntime TTL 已覆盖
- [ ] Conveyor queue writer 并发、幂等、诊断和严格模式已覆盖 PostgreSQL 语义
- [ ] 满箱/换箱/换架不再按普通 trusted callback 完成处理
- [ ] WMS breaker/evidence、DeviceCommand ACK age、RuntimeInbox/Outbox 等关键指标已纳入观测口径

### 10.5 Phase 4: 后续子领域

**目标**：补全 WES 作业期完整业务语义。Phase 4 不阻塞 Phase 3 的 P0 技术闭环上线验证，但阻塞完整业务能力上线；任何仍承载未重建业务语义的 legacy 不能在对应 Phase 4 能力验收前删除。

| Task | Effort | 关联文件 | 验证 |
| --- | --- | --- | --- |
| `MaterialLocationQuery` 查询服务 | M | `src/app/material/` | 6 入口 + 5 来源位置优先级 |
| `WorklineActiveObjects` / `WorklineCurrentWorkView` 查询服务（配合 ActiveObjectRegistry） | M | `src/app/active_objects/` | 3 路 UNION 归一化 |
| 分拣机/粗分机入库能力目标态重建 | L | Phase 4 SPEC | 按行为契约重建扫码、识别/WMS 校验、箱格分配、NG、投箱、完成；不复用旧 plugin 入口 |
| SMT/NG/WMS 对账闭环 | L | `src/app/reconciliation/`, `src/app/wms_integration/` | NG evidence、WMS 确认/拒绝、目标箱回写失败、版本冲突恢复测试 |
| 未来直连 RCS/AGV/CTU provider adapter 设计 | L | `src/app/rcs_integration/` / `src/app/agv_integration/` / `src/app/ctu_integration/` | `FulfillmentPort` provider 差异化接口 + HybridFulfillmentRouter |

**Phase 4 完成门禁**：

- [ ] `MaterialLocationQuery` 6 入口全部支持
- [ ] `WorklineActiveObjects` 与 `ActiveObjectRegistry` 协同
- [ ] 分拣机/粗分机入库能力按目标态 capability / port 重建，不保留旧插件兼容入口
- [ ] SMT/NG/WMS 对账闭环不复制 WMS/NG/PDA 主数据，只保留 evidence、ExternalReference 和 RuntimeHold 解除条件
- [ ] RCS / AGV / CTU provider adapter 的能力差异、幂等、callback、状态权威边界已形成 SPEC

### 10.6 Phase 5: Legacy 删除与收尾

**目标**：根据 Phase 0 清理矩阵删除旧 WorkLine/plugin/runtime 残留，确保新代码中没有旧插件框架、旧队列 enum、旧 API 兼容转发。

**启动条件（双 lane）**：

- **技术残留清理 lane**：Phase 2 + Phase 3 完成、行为契约测试与新 contract tests 全绿后启动；只删除无业务语义的旧 plugin 框架、旧队列 enum、旧 API 兼容转发和 dead code。
- **业务承载 legacy lane**：对应 Phase 4 capability / port / contract tests 通过后启动；未重建的业务能力只能冻结入口并保留 characterization tests，不得提前 drop 承载业务语义的数据或代码。

| Task | Effort | 关联文件 | 验证 |
| --- | --- | --- | --- |
| **ENG-014** Legacy 路径列 `src/{workline_runtime,workline_plugins}` 5 子目录清理矩阵 | S | `docs/architecture/legacy-cleanup-matrix.md`, `TODOS.md` | per-file delete / rebuild / move / keep-contract 矩阵，并标记是否承载 Phase 4 业务语义 |
| 技术残留删除 PR | M | `src/workline_runtime/`, `src/workline_plugins/`, `src/app/workline/` | 仅在技术残留清理 lane 执行；删除旧 plugin 框架、旧队列 enum、旧 API 兼容转发、无业务语义的 dead code |
| 业务承载 legacy 删除 PR | L | `src/workline_runtime/`, `src/workline_plugins/`, `src/app/workline/` | 仅在业务承载 legacy lane 执行；对应 Phase 4 capability / port / contract tests 已通过后逐项删除 |

**Effort 估算**：L-XL（取决于 Phase 2 已删除比例；清理目标是删除多于搬运）。

### 10.7 总 Effort 估算

| Phase | Effort | Human-team | CC + gstack |
| --- | --- | --- | --- |
| Phase 0 | M | ~1-2 周 | ~2-3 天 |
| Phase 1 | M | ~4-6 周 | ~1-2 周 |
| Phase 2 | XL | ~8-10 周 | ~2-3 周 |
| Phase 3 | M | ~10 周 | ~2 周 |
| Phase 4 | L | ~6 周 | ~1 周 |
| Phase 5 | L | ~1-3 周 | ~2-4 天 |
| **总计** | **XL** | **~30-39 周** | **~7-10 周** |

### 10.8 实施阶段依赖图

```text
Phase 0 ──────────────────────────────────────┐
  ├── P0-001 目标态契约                      │
  ├── P0-002 Legacy 清理矩阵                  │
  ├── P0-003 行为契约测试                    │
  ├── P0-004 ExecutionCorrelation 矩阵        │
  └── P0-005 ECS 设备边界合同                │
                                              │
Phase 1 ──────────────────────────────────────┤
  ├── CEO-001 wms_integration 能力面 ports   │
  ├── CEO-005 scope/authority schema          │
  ├── CEO-006 Authority Matrix                │
  ├── CEO-007 RuntimeIntentLog/Session 拆分   │
  ├── CEO-008 动态队列 membership             │
  ├── CEO-010 DeviceCommand ECS contract      │
  ├── CEO-011 manifest version pin            │
  ├── CEO-012 SafetyZone/shared-device         │
  └── CEO-002 4 方案决策表归档                │
                                              │
Phase 2 ──────────────────────────────────────┤
  └── Runtime 迁移 / WorkLine 清空            │
      (条件性: Phase 0+1 完成 + 重新评审)   │
                                              │
Phase 3 ──────────────────────────────────────┤
  ├── ENG-002 ReconciliationManager           │
  ├── ENG-003 manifest validator             │
  ├── ENG-004 11 态机 + CB                   │
  ├── ENG-006 ActiveObjectRegistry           │
  ├── ENG-008 External callback HMAC         │
  ├── ENG-009 idempotency 复合主键           │
  ├── ENG-010 typed ExternalReference         │
  ├── ENG-011 inbox backpressure + lease      │
  ├── ENG-012 dispatch policy + runtime TTL  │
  ├── ENG-013 RECONCILING 现场隔离            │
  ├── ENG-016 queue writer 并发诊断           │
  ├── ENG-017 满箱交换对账语义                │
  ├── ENG-018 WMS evidence/CB observability   │
  ├── ENG-019 worker/HTTP benchmark           │
  └── DESIGN-001..005 plane 修复             │
                                              │
Phase 4 ──────────────────────────────────────┤
  ├── MaterialLocationQuery                  │
  ├── WorklineActiveObjects                  │
  ├── 入库能力目标态重建                     │
  ├── SMT/NG/WMS 对账闭环                    │
  └── RCS/AGV/CTU provider adapter 设计      │
                                              │
Phase 5 ──────────────────────────────────────┘
  ├── ENG-014 Legacy 清理矩阵                │
  ├── 技术残留清理 lane: Phase 3 门禁后删除  │
  │   无业务语义的旧框架/enum/API/dead code  │
  └── 业务承载 legacy lane: Phase 4 能力验收后
      逐项删除仍承载业务语义的 legacy
```

---

## 11. 执行规范

### 11.1 TDD 纪律

- **行为契约测试建立（P0-003）**：`uv run pytest tests/workline_runtime tests/resource tests/handling tests/wms_integration` 建立旧能力语义样本；测试名称表达业务能力，不绑定旧 service 内部实现。
- **不保护旧代码形态**：`runtime_query_service.py`、`smt_inbound_handoff_service.py`、`workline_service.py`、`start_admission_service.py` 可删除或重建；只要求目标态业务语义测试通过。
- **新 capability 测试矩阵**：每个新 capability 必须有 unit + integration + regression 三层覆盖；可逆 schema migration 必须有 upgrade + downgrade + 结构断言，数据重塑必须有 dry-run 和行数校验

### 11.2 迁移规范

- **可逆 Alembic upgrade + downgrade 都必须可执行**——任何新表/字段必须包含完整 downgrade；不可逆清理必须依赖快照回滚，不写假 downgrade
- **破坏性迁移默认允许**：表名、字段名、API 路径、enum、包路径都可按目标态重建。
- **不保留旧兼容入口**：例如 `session_id: str` → `workline_session_id: int` 不保留 string 兼容入口；其他类似迁移同样执行。
- **过渡脚本必须短生命周期**：若数据搬迁需要临时脚本，必须在同一 Phase 给出清理 PR。

### 11.3 评审制度

- **autoplan 评审存档**：CEO/Design/Eng 评审全文在 `docs/architecture/reviews/autoplan-workline-restructuring-2026-06-23.md`；28 个 auto-decision 在 `docs/architecture/reviews/decision-audit-trail.md`
- **关键决策进 ADR**：B 方案目标态重写、11 态机、typed envelope、plane RBAC、idempotency、HMAC、ExecutionCorrelation、Authority Matrix 共 8 项已记录到 `docs/architecture/adr/workline-restructuring/`
- **B 方案进入需重新评审**：完成 Phase 0 + Phase 1 后，B 方案启动前必须重新跑 autoplan 或同等深度的 CEO/Eng 评审
- **改动顶层设计需重跑评审**：本设计 §1 §2 §3 §4 §5 §6 §7 §8 §9 任一改动需重新 CEO/Eng 评审（不是 PR-level review）

### 11.4 命名规范

- **目标态命名优先**：旧 `BinTransitMembership` / `BinTransitQueue` 不冻结；目标态使用 `ConveyorQueueMembership` + manifest `queue_code`
- **跨域 correlation key 替代 session FK**：不允许新代码用 `execution_session.id` 作为强 FK 跨域引用；必须用 `ExecutionCorrelation.correlation_id`
- **Runtime vs Execution 前缀（M8 回归）**：包名使用 `runtime/orchestration`；会话聚合根使用 `ExecutionSession`；跨域关联使用 `ExecutionCorrelation`；运行时记录使用 `RuntimeInbox` / `RuntimeTimeline` / `RuntimeHold`；effect ledger 使用 `RuntimeIntentLog` / `EffectPort`。
- **禁止 WorklineSession 前缀**：新代码不得新增 `WorklineSession` / `WorkLineRuntimeSession` / `WorklineExecution` 等会把配置域和执行域混在一起的命名。
- **typed Pydantic 模型替代裸字符串/裸 JSON**：`ExternalReference`、`EvidenceEnvelope`、`PlaneSceneView`、`PlaneSnapshot` 等必须用 Pydantic BaseModel，不允许裸 dict
- **WMS DTO 不出 wms_integration 域**：内部域不允许 import WMS 类型；只允许通过 `WmsMasterDataPort` / `WmsDocumentPort` / `WmsInventoryQueryPort` / `WmsInventoryTransactionPort` / `WmsFulfillmentPort` / `WmsEventPort` / `WmsReconciliationPort` 访问
- **Runtime capability 注入 contract**：capability/plugin 只接收 `RuntimeCapabilityContext` 中的 port contract，不允许接收 `wms_integration` service、HTTP client、service locator、DTO 或 provider exception

### 11.5 Legacy 清理规范

#### 实际路径（autoplan ENG-028 修正）

实测旧代码不在 `src/app/`，而在 `src/` 根：

| 旧路径 | 实测 LOC | 清理目标 |
| --- | --- | --- |
| `src/workline_runtime/` | 10,241 | 提取业务语义后删除或重建为 `src/app/runtime/orchestration/` |
| `src/workline_runtime/plugins/` | (待测) | 不作为新架构基础；业务事实提炼为 runtime capability 后删除 |
| `src/workline_runtime/sessions/` | (待测) | 迁入 `ExecutionSession`；correlation key 化 |
| `src/workline_runtime/inbox/` | (待测) | 迁入 `RuntimeInbox`；correlation key 化 |
| `src/workline_plugins/` | 3,085 | 不再追加新插件能力；YAML manifest 转交 `workline` 配置域 |
| `src/app/workline/` | 32,979 | 删除执行能力和旧插件入口；只保留目标态配置 CRUD / manifest / plane scene |

旧 plugin 中直接 import `src.app.wms_integration.*`（例如 WMS 查询 DTO、异常、typed service）的代码是明确清理目标。迁移时先抽取业务决策语义，再改为通过 `RuntimeCapabilityContext.readonly_facts` 或 `RuntimeIntentLog` + EffectPort 使用外部能力。

#### 清理顺序

1. **业务语义提取**：用 characterization tests 固化旧能力中仍需要的业务语义。
2. **目标态骨架落地**：先建立 `runtime/orchestration`、`material`、`ConveyorQueueMembership`、WMS ACL ports。
3. **旧入口分类**：先按清理矩阵标记技术残留、业务承载 legacy、一次性迁移脚本；不做转发兼容。
4. **技术残留删除**：无业务语义的旧入口、旧 enum、旧 plugin 框架和 dead code 进入 Phase 5 技术残留清理 lane；前置条件是 Phase 3 门禁全绿。
5. **业务承载 legacy 延迟删除**：仍承载 Phase 4 业务语义的代码或数据进入 Phase 5 业务承载 legacy lane；只能在对应 capability / port / contract tests 通过后 drop。
6. **数据迁移与 drop**：迁移必要 evidence 后 drop 旧表/旧 enum/旧字段；不可逆 drop 必须有快照点和清理矩阵勾选。
7. **全局校验**：确认 WorkLine 只保留配置职责，新代码不 import 旧 plugin/runtime 包。

### 11.6 工具与命令规范

- **包管理**：`uv sync --dev`（所有命令走 `uv run ...`，不依赖其它 shell 激活环境）
- **测试**：`uv run pytest` + `uv run pytest --cov=...`
- **Lint/Format**：`uv run ruff format . && uv run ruff check .`
- **migration**：可逆 schema migration 执行 `uv run alembic upgrade head` / `uv run alembic downgrade -1`；data reshape / destructive cleanup 执行 dry-run、快照校验、upgrade，不要求 downgrade
- **不在 main 上直接开发**：日常单任务开发从 `develop` 切 `feature/*`、`fix/*`、`chore/*` 等分支

---

## 12. 风险与对策

### 12.1 4 个 CRITICAL gap（autoplan 识别）

| ID | 风险 | 防线 |
| --- | --- | --- |
| F0 | plan 早期"22.6K 数字"是事实错误 | 决策 #1 措辞修订：760 行 + 5 套新 port；实测 `wms_integration` = 2,649 LOC |
| F5 | RECONCILING 黑洞状态无恢复决议 | Phase 3 ENG-002（ReconciliationManager + owner-scoped resolution decision + 5/30 分钟升级） |
| F8 | 32,979 LOC 改造无行为契约测试 | Phase 0 P0-003；关键业务语义 characterization + contract tests |
| F10 | `GET /worklines/{id}/plane` 全员可读全量运营数据 | Phase 3 plane read model；RBAC + 行级 + 脱敏 + 审计 |

### 12.2 队列模型决策

旧 `BinTransitMembership` + 8 个 `BinTransitQueue` enum 不进入目标态。目标态采用 `ConveyorQueueMembership`：

1. 队列编码来自 WorkLine manifest，不是系统级 enum。
2. `queue_role` 是 manifest role 快照，用于展示和审计，不作为写死的业务流程。
3. active 唯一约束按 WorkLine + bin/placeholder 维度表达业务语义。
4. 旧表可通过 migration 搬迁必要 evidence 后删除。

### 12.3 事实修正

- **F0 修正**：`wms_integration` 实测 2,649 LOC（不是 22.6K），其中 typed_ports.py 609 行 + models/ports.py 151 行 = 760 行 typed port

### 12.4 3 个 cross-phase themes（autoplan 双 voice 独立命中）

1. **外部 ACL 应补全并清理**（CEO F1 + Eng F14）→ Phase 1 CEO-001 + ADR 0001
2. **RuntimeIntentLog / RuntimeSession 显式拆分**（CEO F7 + Eng F3/F4）→ Phase 1 CEO-007 + ADR 0007
3. **过早在命名/schema 画死**（CEO F4 + Eng F5/F12）→ 队列改为 manifest 动态配置 + typed envelope（Phase 3 ENG-010）

### 12.5 现状 → 目标态对比

| 维度 | 现状 | 目标 |
| --- | --- | --- |
| 域结构 | workline 混合"配置 + 执行 + 插件"（32,979 LOC），runtime 在 `src/workline_runtime/`（10,241 LOC）独立，plugin 在 `src/workline_plugins/`（3,085 LOC）独立 | workline 仅配置；runtime/orchestration 独立域；plugin 能力在 runtime 域以 port/capability 形式重建 |
| 域间引用 | 16+ 文件含 `session_id` / `execution_session_id` 跨域 FK | `ExecutionCorrelation.correlation_id` 作为跨域 correlation key |
| WMS 集成 | 仅有旧 `WmsInventoryPort` 能力且 query/mutation 混杂 | WMS 能力面 ports 全部实现，库存拆为 Query / Transaction |
| RuntimeIntentLog vs RuntimeSession | 同段自相矛盾 | 显式拆分 |
| conveyor queue | `BinTransitMembership` + 8 个系统级 enum | `ConveyorQueueMembership` + manifest 动态队列 |
| RECONCILING | 黑洞状态无恢复决议 | `ReconciliationManager` + owner-scoped resolution decision |
| Plane 接口 | 全员可读全量运营数据 | 拆 scene + snapshot 独立接口 + RBAC + 行级 + 脱敏 + 审计 |
| idempotency_key | 4 处分散实现，无统一命名空间 | 复合主键 + immutable `request_hash` |
| External callback 鉴权 | `signature + timestamp` 或可选 token，未覆盖全部 provider | HMAC-SHA256 body 签名 + 5 分钟 nonce TTL + provider/source/callback_type allow-list |
| 测试基线 | 32,979 LOC 改造无行为契约基线 | 关键业务语义 characterization + 新 contract tests |
| Legacy 路径 | 实际在 `src/{workline_runtime,workline_plugins}` | 业务语义提取后删除或重建 |

---

## 13. 附录

### 13.1 实施细节 SPEC 触发清单

实施细节（字段定义、状态机转移表、HMAC 合同、typed envelope schema、PlaneSceneView/Snapshot schema 等）**不在本文展开为独立 SPEC**。当对应 Phase 启动前或启动时，按需生成独立 SPEC：

- **Phase 1 启动时** → 写 `wms-integration-ports-spec.md`（MasterData / Document / InventoryQuery / InventoryTransaction / Fulfillment / Event / Reconciliation 各 port 详细字段，并引用 `docs/integration/wms_rcs_interface_requirements.md` 的 P0/P1 接口清单）、`runtime-orchestration-spec.md`（ExecutionSession / RuntimeInbox / RuntimeIntentLog / ExecutionCorrelation 最小骨架）
- **Phase 2 启动时** → 写 `legacy-runtime-migration-spec.md`（旧 WorkLine/plugin/runtime 执行能力迁移、删除和 WorkLine 清空顺序）
- **Phase 3 启动时** → 写 `fulfillment-state-machine-spec.md`（11 态机完整转移图 + 4 timeout 时长表 + BLOCKED_BY_CB 集成）、`reconciliation-manager-spec.md`（触发矩阵 + 隔离动作 + owner-scoped resolution decision + 5/30 分钟升级）、`plane-read-model-spec.md`（PlaneSceneView/Snapshot 字段 + 容量上限 + RBAC 矩阵）、`external-callback-auth-spec.md`（HMAC canonical + nonce TTL + allow-list）、`device-dispatch-policy-spec.md`（能力选择 + deadline + 状态快照 TTL）
- **Phase 4 启动时** → 写 `material-location-query-spec.md`、`workline-active-objects-spec.md`、`fulfillment-provider-adapter-spec.md`
- **Phase 5 启动时** → 写 `legacy-cleanup-execution-plan.md`，逐文件列出 delete / rebuild / move / keep-contract、是否承载 Phase 4 业务语义、对应 capability/port/contract tests、允许 drop 的前置条件和数据库 drop 顺序

**为何不在本文展开**：

- 字段定义、状态机表等在 Phase 启动时容易发现新约束（实测发现数 = 实际编码冲突）
- 预先 SPEC 容易过早画死（autoplan 决策 #4 教训）
- Phase 内 review 更轻，spec 与实现同步

### 13.2 关键决策（ADR 索引）

| # | 决策 | ADR |
| --- | --- | --- |
| 1 | B 方案目标态重写 + 不做向后兼容 | [`0001-b方案选择与capability-freeze.md`](adr/workline-restructuring/0001-b方案选择与capability-freeze.md) |
| 2 | 外部履约 11 态机 + 4 timeout + BLOCKED_BY_CB | [`0002-外部履约-11态机加timeout.md`](adr/workline-restructuring/0002-外部履约-11态机加timeout.md) |
| 3 | typed `ExternalReference` + `EvidenceEnvelope` | [`0003-typed-external-reference-evidence.md`](adr/workline-restructuring/0003-typed-external-reference-evidence.md) |
| 4 | plane 接口 RBAC + 容量上限 + 极态 | [`0004-plane-rbac-bounded-snapshot.md`](adr/workline-restructuring/0004-plane-rbac-bounded-snapshot.md) |
| 5 | idempotency_key 复合主键 + request_hash | [`0005-idempotency-composite-key.md`](adr/workline-restructuring/0005-idempotency-composite-key.md) |
| 6 | External callback body HMAC + nonce TTL | [`0006-external-callback-hmac.md`](adr/workline-restructuring/0006-external-callback-hmac.md) |
| 7 | ExecutionCorrelation correlation key | [`0007-execution-correlation-key.md`](adr/workline-restructuring/0007-execution-correlation-key.md) |
| 8 | Authority Matrix | [`0008-authority-matrix.md`](adr/workline-restructuring/0008-authority-matrix.md) |

### 13.3 现有相关文档

- [`docs/superpowers/specs/2026-06-19-workline-multi-object-state-machine-design.md`](../superpowers/specs/2026-06-19-workline-multi-object-state-machine-design.md) — 状态机子设计
- [`docs/superpowers/specs/2026-06-23-workline-c0-resource-projection-foundation.md`](../superpowers/specs/2026-06-23-workline-c0-resource-projection-foundation.md) — C0 子基础
- [`docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`](adr/2026-05-13-wes-wms-rcs-resource-boundary.md) — 现有 ADR
- [`docs/architecture/adr/2026-05-26-wms-integration-domain.md`](adr/2026-05-26-wms-integration-domain.md) — 现有 ADR
- [`docs/architecture/ARCHITECTURE_EVOLUTION_ROADMAP.md`](ARCHITECTURE_EVOLUTION_ROADMAP.md) — 季度级演进路线图
- [`docs/architecture/REPOSITORY_GUIDE.md`](REPOSITORY_GUIDE.md) — 通用 Repository 使用指南
- [`docs/architecture/SRS.md`](SRS.md) — 软件需求规格说明书

### 13.4 外部参考来源

| 来源 | 用于本设计的约束 |
| --- | --- |
| AutoStore, [Warehouse Execution System: Enhancing Efficiency](https://www.autostoresystem.com/insights/warehouse-execution-system-enhancing-efficiency) | WES 位于 WMS 与现场自动化之间，承担执行编排、自动化衔接和效率优化；支撑本设计中 WMS 权威 + WES 编排 + ECS 执行边界 |
| Blue Yonder, [Warehouse Execution](https://blueyonder.com/solutions/warehouse-management/warehouse-execution) | WES 需要做实时执行、资源协调和自动化设备协同；支撑 DeviceDispatchPolicy、Runtime/Orchestration 和 plane snapshot 的目标 |
| GS1, [EPCIS and CBV Implementation Guideline](https://www.gs1.org/standards/epcis-and-cbv-implementation-guideline/current-standard) | 可观察对象事件应表达 what/where/when/why/source；支撑 `RuntimeLocationEvent` 与位置事实投影契约 |
| Webhooks.fyi, [Replay Prevention](https://webhooks.fyi/security/replay-prevention) | 外部 callback 应使用 timestamp/nonce/body hash 防重放；支撑 External callback HMAC、nonce TTL 和 payload hash |

### 13.5 评审存档

- [`docs/architecture/reviews/autoplan-workline-restructuring-2026-06-23.md`](reviews/autoplan-workline-restructuring-2026-06-23.md) — autoplan CEO/Design/Eng 评审全文
- [`docs/architecture/reviews/decision-audit-trail.md`](reviews/decision-audit-trail.md) — 28 个 auto-decision 记录
- [`docs/architecture/reviews/workline-restructuring-v4-review-2026-06-23.md`](reviews/workline-restructuring-v4-review-2026-06-23.md) — 外部 v4 评审报告；C1-C3 / M1-M10 已回归到本文主体章节

### 13.6 外部 v4 评审回归矩阵

| ID | 回归结果 | 本文落点 |
| --- | --- | --- |
| C1 | 选择破坏性方案 B：`ConveyorQueueMembership` + `conveyor_queue_memberships`，旧 enum/table 删除 | §3.7 |
| C2 | 补 B 方案暂停/回退条件，不恢复旧兼容，只缩小重构范围 | §10.3.1 |
| C3 | 补 WES 内部 idempotency key 命名空间与 operation_kind 细分 | §5.4 |
| M1 | plane 实时性分级：scene 1 Hz，snapshot SSE + 250ms fallback，events SSE | §5.2 |
| M2 | `BLOCKED_BY_CB` 定义为系统侧延迟状态，不计入履约 P95 | §6.3 |
| M3 | 明确 `DRAINING`/`HOLD`/`VALIDATING` 分属 WorkLine/session/activation | §9.1 |
| M4 | 补传感器抖动、通信丢包、重复上报三类物理异常 | §6.4 |
| M5 | 冲突仲裁扩展到 rack/pkg/command 维度 | §6.6 |
| M6 | `DeviceState` 增加 `UNKNOWN` / `MAINTENANCE` | §9.6 |
| M7 | 外部系统 ACL 镜像命名，禁止 `src/app/external/` 父目录 | §3.5 |
| M8 | Runtime/Execution 命名前缀规则 | §11.4 |
| M9 | `work_position_code` 是 WES 内部工作位，与 WMS `location_code` 映射 | §9.4 |
| M10 | drift SLA 与 WMS 可用性分离，WMS 不可用时抑制告警风暴 | §6.5 |

### 13.7 外部 v4 待澄清项决策

| ID | 决策 | 本文落点 |
| --- | --- | --- |
| Q1 | 旧 capability 只保留行为不变量，不保留代码形态 | §2.2 / §11.1 |
| Q2 | P0 plane 面向操作员终端、工程调试台和只读大屏，不面向公开报表或 WMS 全局查询 | §5.2 |
| Q3 | RECONCILING 告警分为 info/warn/critical；外部通知 provider 后续扩展 | §6.4 |
| Q4 | B 方案失败时按 §10.3.1 暂停或降级到 C/D，不恢复旧兼容 | §10.3.1 |
| Q5 | required 设备 OFFLINE/UNKNOWN/MAINTENANCE 阻塞 WorkLine 启动；optional 设备只降级能力 | §9.6 |
| Q6 | 物理现场 RECONCILING 采用 ECS/device push + WES pull 双通道 | §6.4 |
| Q7 | 未来直连 RCS/AGV/CTU 在 provider adapter 层差异化，不扩散到 runtime/handling | §3.5 / §10.5 |
| Q8 | WES 内部 idempotency key 使用 `WES-{OPERATION_KIND}-{HASH}` 命名空间 | §5.4 |
| Q9 | Event_Push 响应体固定 ACK，响应拦截器拒绝 command-like 字段 | §7.1 / §7.5 |
| Q10 | Legacy 清理矩阵移入 Phase 5，Phase 4 聚焦后续子领域能力 | §10.5 / §10.6 |

### 13.8 状态所有权图（详细 ASCII）

```text
WorkLine 配置
  + ConveyorLine / PipelineQueue / EntryPoint / ExitPoint / Device(role)
  |
  | manifest/config pin
  v
runtime/orchestration
  +-- ExecutionSession (session aggregate / PK owner)
  |     +-- RuntimeInbox        <- WMS/RCS/ECS/device callback evidence
  |     +-- RuntimeTimeline
  |     +-- RuntimeHold
  |     +-- ExecutionCorrelation
  |     +-- ConveyorQueueMembership
  |
  +-- RuntimeIntentLog (effect proposal / outbox log, NOT state)
        |
        v
      EffectPort
        +--> handling owner service
        |      +-- HandlingOperation / HandlingMove (correlation_id, 无 FK)
        |
        +--> resource projection writer
        |      +-- RackPlacement / RackBinMount / BinMaterialMount
        |      +-- ResourceStateEvent (ExternalReference + EvidenceEnvelope)
        |
        +--> wms_integration (ACL)
        |      +-- WmsFulfillmentPort / WmsEventPort / WmsReconciliationPort
        |      +-- WmsFulfillmentRequest (11 态机 + 4 timeout owner)
        |      +-- WMS/RCS callback -> RuntimeInbox
        |
        +--> device / DeviceCommandPort
               +-- DeviceCommand -> ECS/device upper system
               +-- ECS/device event/result callback -> RuntimeInbox

material (WES 根实体)
  +-- material_units.current_session_correlation_id (correlation key)

reconciliation (RECONCILING 冲突决议模型)
  +-- ReconciliationManager
        +-- 触发矩阵: 投影冲突 / External callback 不一致 / 设备状态矛盾 / drift
        +-- 强制动作: evidence + RuntimeHold + 通知
        +-- 决议输出: resolution_decision + owner_scope + allowed_next_effect_scope
        +-- owner 自行转移: fulfillment / handling / session / projection / device
```
