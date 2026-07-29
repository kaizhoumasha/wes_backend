> **拆分说明**: 本文档从 `workline-and-plugin-restructuring.md` v7 拆分（2026-07-10）。
> 完整设计目标与 frontmatter 仍以原文件为准, 详见同目录 index。
> 章节对应: overview = 原文件 §1 引言 + §2 系统概述 + §13 附录。

---

---
status: Draft v7 — WorkLine restructuring cleanup completed（GB/T 8567 风格）
created_at: 2026-06-23
updated_at: 2026-07-08
parent_goal: 对当前 WORKLINE + PLUGIN 体系进行全面重构/重做
document_type: 概要设计说明书 + 详细设计（Outline Design + Detailed Design）
audience: eng/arch lead, WES owner, WMS 集成 lead, code reviewer
related_specs:
  - docs/superpowers/archive/specs/2026-06-19-workline-multi-object-state-machine-design.md  (历史子设计)
  - docs/superpowers/archive/specs/2026-06-23-workline-c0-resource-projection-foundation.md  (历史 C0 子基础)
  - docs/architecture/runtime-orchestration-spec.md  (Runtime/Orchestration 域最小骨架 SPEC)
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
  2026-07-08 同步：technical cleanup scope 已通过 PR #78 合并到 develop（v0.13.0.0）；legacy plugin runtime/import 框架已退出 src 可 import 路径。runtime production closure 与 material-flow production evidence bundle 已可重新生成并通过 gate；business legacy cleanup scope readiness 与 business legacy absence gate 已通过 PR #79 合并到 develop（v0.14.0.0，merge SHA 8c833610c08005005406b3a774c92519f69b7886），业务执行合同已迁入 material-flow runtime capability 目标态。本 restructuring cleanup 进一步删除旧 handling 队列表面与 WorkLine 运行态物理列，运行状态由 `wes_runtime.workline_runtime_status_projections` 承接。active code、gate 与默认回归测试的命名策略见 `docs/architecture/process-naming-policy.md`。
---

# WORKLINE + PLUGIN 体系全面重构顶层设计

> 概要设计说明书（GB/T 8567 风格）+ 详细设计
> 版本：Draft v7（2026-07-08 WorkLine restructuring cleanup completed）
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
- 给出**实施计划**（Phase 0-5 六阶段路线图 + critical path + 总 Effort 估算）（§10）
- 给出**执行规范**（TDD 纪律、破坏性迁移规范、评审制度、命名规范、Legacy 清理规范、工具与命令规范）（§11）
- 列出**风险与对策**（4 CRITICAL、1 TASTE、2 事实修正、3 跨阶段主题）（§12）
- **附录**：实施细节 SPEC 触发清单、ADR 索引、现有相关文档、评审存档（§13）

### 1.2 范围

**本文档范围**：

- WORKLINE + PLUGIN 体系全面重构的**顶层设计**（含概要 + 详细）
- 8 个域的边界与责任：workline（配置域）/ runtime/orchestration（执行域）/ handling（搬运意图）/ resource（运行投影）/ material（WES 根实体）/ device（设备接入）/ wms_integration（WMS ACL）/ reconciliation（对账）
- 7 个目标 WMS port（MasterData / Document / InventoryQuery / InventoryTransaction / Fulfillment / Event / ReconciliationQuery）
- Phase 0-5 六阶段实施路线图

**本文档不包含**（实施 SPEC 阶段展开）：

- 各 port 详细字段定义（Phase 1 单 PR 的 Packet B / CEO-001 代码实现前写 `wms-integration-ports-spec.md`）
- 11 态机完整转移表（Phase 3 启动时写 `fulfillment-state-machine-spec.md`）
- HMAC canonical 字符串（Phase 3 启动时写 `external-callback-auth-spec.md`）
- PlaneSceneView/PlaneSnapshot 完整 schema（Phase 3 启动时写 `plane-read-model-spec.md`）
- ReconciliationManager 触发矩阵（Phase 3 启动时写 `reconciliation-manager-spec.md`）

### 1.3 术语与缩略语

| 术语 | 定义 |
| --- | --- |
| WES | Warehouse Execution System，本系统，仓储现场自动化执行中台 |
| WMS | Warehouse Management System，外部权威系统，持有库存/单据/库位主数据 |
| RCS | Robot Control System，当前阶段由 WMS 统一调度；WES 只消费 WMS E08–E14 typed ACK/status/terminal result；直连能力仅作条件触发扩展，生产前默认不做 |
| AGV / CTU | Automated Guided Vehicle / Container Transfer Unit，外部搬运设备 |
| PLC | Programmable Logic Controller，ECS 内部控制组件；WES 不与 PLC 通讯 |
| WorkLine | 工作线配置域的根实体；只拥有配置，不拥有运行状态 |
| Runtime | 工作线执行域（重构后）；拥有 ExecutionSession 等状态 |
| ExecutionSession | runtime/orchestration 的会话聚合根；一次作业的执行会话 |
| ExecutionWorkItem | runtime/orchestration 的对象级执行令牌；一个料盘、物料、料箱或外部履约子项在会话内独立推进 |
| RuntimeIntentLog | 运行时输出的下一步意图记录（effect proposal / outbox log，**不是**状态源） |
| ExecutionCorrelation | 跨域 correlation key，替代 `execution_session.id` 跨域 FK |
| EffectPort | Runtime 向 handling/device/resource/material/wms_integration 分发副作用的稳定接口 |
| InboundEventPort | 外部 callback/event 入站标准接口；负责 normalizer、原始归档、typed evidence，写入 `RuntimeInbox`，不是 effect ledger |
| ConveyorQueueMembership | 料箱在滚筒线队列中的 runtime active 投影；队列由 WorkLine manifest 动态定义 |
| Operation-specific fulfillment contracts | WMS 具名履约 operation 与 typed request/result |
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
| 3 | `docs/superpowers/archive/specs/2026-06-19-workline-multi-object-state-machine-design.md` | 历史状态机子设计 |
| 4 | `docs/superpowers/archive/specs/2026-06-23-workline-c0-resource-projection-foundation.md` | 历史 C0 资源投影子基础 |
| 5 | `docs/architecture/ARCHITECTURE_EVOLUTION_ROADMAP.md` | 季度级演进路线图 |
| 6 | `docs/architecture/REPOSITORY_GUIDE.md` | 通用 Repository 使用指南 |
| 7 | `docs/architecture/SRS.md` | 软件需求规格说明书 |
| 8 | `docs/integration/wms_rcs_interface_requirements.md` | WES 对 WMS/RCS 接口需求分析：本阶段集成边界、基础数据、标准回调、WES→WMS 指令 |
| 9 | `docs/integration/third_party_integration_whitepaper.md` | 第三方设备接入白皮书：WES 与 ECS/设备上位机的 API 边界、Command-Ack-Callback、逻辑位置约束 |
| 10 | `docs/architecture/reviews/autoplan-workline-restructuring-2026-06-23.md` | autoplan CEO/Design/Eng 评审全文 |
| 11 | `docs/architecture/reviews/decision-audit-trail.md` | 28 auto-decision 审计 |
| 12 | `docs/architecture/adr/workline-restructuring/0001-0008` | 8 个关键决策 ADR |
| 13 | `docs/architecture/runtime-orchestration-spec.md` | Runtime/Orchestration 域最小骨架 SPEC（7 核心实体 + 支撑实体 + 设计决策） |
| 14 | `docs/architecture/process-naming-policy.md` | active code / gate / test 的稳定命名策略 |

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
- Pipeline concurrency：`ExecutionSession` 不能成为整条 WorkLine 的串行锁；料盘、物料、料箱和履约子项必须以 `ExecutionWorkItem` 或等价对象级令牌独立推进。某设备完成当前对象步骤并回调后，该设备即可接收下一对象命令，不等待该对象完整业务流结束。
- Handoff：任何物料/料箱/货架交接必须以 External callback 或 RuntimeIntentLog evidence 推进，禁止 API 层直接改投影。
- Resource projection：同一 object 在同一 WorkLine 内只能有一个可解释的 active 归属；瞬态冲突必须带 `transient_until`，超时进入 `RECONCILING`。
- Device command：每个物理动作必须有 `command_code + idempotency_key + request_hash + callback result` 闭环。
- WMS fulfillment：任何 E08–E14 外部履约必须有 typed ACK、status query、typed terminal result、timeout 和失败恢复路径；E12/E13 批量履约必须以 ACK 冻结成员，并只消费批次级权威结果。
- Inbound flow baseline：分拣机/粗分机入库链路的业务语义必须被行为契约测试覆盖，包括对象级流水并发、扫码、测量或识别、WMS 校验、箱格分配/预约、滚筒线路由、满箱/换架、NG、投箱和完成；旧插件接口、旧 context 字段和旧 fake allocator 不进入目标态合同。
- Full-box exchange：满箱、满货架、换空箱、换货架属于 E11 typed ACK/status/terminal result + 对账闭环；只有 terminal result 与 owner 投影均校验通过后才能完成。

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
| 9 | 本阶段直接调度 RCS / AGV / CTU | `wms_rcs_interface_requirements.md` 明确 RCS 仍由 WMS 统一调度；直连能力仅作条件触发 provider adapter，生产前默认不做 |
| 10 | 让 WMS 直连自动化设备 | 自动化设备只通过 WES 接入，WMS 不直连设备 |
| 11 | 让 WES 承接 PDA 应用交互 | PDA 仅对接 WMS；WES 如需感知 PDA 结果，由 WMS 事件推送或同步 |
| 12 | WES 直接控制 PLC、下发物理坐标、关节角度或安全回路指令 | `third_party_integration_whitepaper.md` 明确 WES 与 ECS/设备上位机通过 API 交互；ECS 自主完成物理控制、坐标映射和硬件防呆 |
| 13 | 在设备 Event_Push HTTP 响应中直接返回动作指令 | 白皮书要求 Event_Push 只 ACK；后续动作必须通过 Receive Command 下发，保证指令可追踪、可幂等、可审计 |
| 14 | 在 WES 内建 NG 周转箱/库位主数据、返工工单主档或 PDA 离线原生流程 | 这些能力归 WMS/MES/PDA 体系；WES 只保留 RuntimeHold、ExternalReference、evidence 和解除条件 |

### 2.4 重构前体系规模基线（实测 2026-06-23）

> 本表保留重构启动时的规模基线，用于解释 Phase 0-5 拆分来源。technical cleanup scope 后，旧 `src/workline_plugins/*`、`src/app/workline/plugins/*`、`src/workline_plugin_registry.py` 和 `docs/templates/workline_plugin/*` 已退出运行/模板路径；restructuring cleanup 后当前逐入口状态以 `docs/architecture/legacy-cleanup-matrix.md`（636 条）和 `docs/architecture/legacy-cleanup-execution-plan.md` 为准。

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
| 2 | WMS 反腐层 (wms_integration ACL) | 7 个目标 port：MasterData / Document / InventoryQuery / InventoryTransaction / Fulfillment / Event / ReconciliationQuery |
| 3 | Authority Matrix | 外部事实按事实类型拆分权威来源；WES material 域是唯一自有根实体 |
| 4 | 目标态契约 | 锁定目标业务能力、域边界、状态所有权和外部 port；不锁定旧 API/旧表 |
| 5 | 4 方案决策表 | A（workline 单体）/ B（本设计）/ C（增量 ACL）/ D（port-only）；选择 B，C 的可复用部分并入 B |
| 6 | implementation task | 5 阶段实施 roadmap（详见 §10） |

### 2.6 P0 验收标准

- [x] 顶层边界文档能清楚回答"WES 做什么、不做什么、谁是外部权威、内部各域拥有什么状态"
- [x] 任一核心对象都能明确状态所有权：WorkLine 配置、Runtime 执行、Handling 业务意图、Resource 投影、Material 作业期实体、Device 事件命令、WMS 外部事实
- [x] 任一外部 WMS 能力都必须经 `wms_integration` port 进入系统，不能从内部领域直接依赖 WMS DTO/client
- [x] 前端最小平面态势图只消费后端 `PlaneSceneView + PlaneSnapshot`，不直接拼散表
- [x] Legacy 清理策略能指导从旧 WorkLine/plugin/runtime 体系提取业务事实、删除旧形态，而不是反向约束目标态
- [x] 目标态契约落地，明确哪些业务能力必须保留，哪些旧 API/旧表/旧插件允许破坏性删除
- [x] Authority Matrix 落地，外部事实的权威来源按事实类型显式拆分
- [x] 行为契约测试基线建立：覆盖关键业务语义，不以旧 service 行覆盖率作为目标

**P0 验收分层**：

| 层级 | 目的 | 必须完成 | 不要求完成 |
| --- | --- | --- | --- |
| P0 文档验收 | 锁定目标态边界，防止旧体系反向约束重构 | 本节全部检查项、行为契约测试清单、Legacy 清理矩阵、ECS 设备边界合同 | 新 runtime 全量代码落地 |
| P0 最小可运行闭环（Phase 3 完成） | 证明新边界能跑通一条受控作业链路 | WorkLine manifest、ExecutionSession、RuntimeInbox、RuntimeIntentLog、DeviceCommand、WMS fulfillment、PlaneSnapshot、RECONCILING 最小闭环 | 分拣机/粗分机完整生产能力 |
| 完整业务能力 | 覆盖现场可交付业务闭环 | 分拣机/粗分机入库、SMT/NG/WMS 对账、MaterialLocationQuery、WorklineActiveObjects | 任何旧 plugin/API/表兼容承诺 |

后续实现阶段验收（不作为 P0 文档验收，但必须在对应 Phase 门禁中落地）：

- [x] plane 接口安全门禁：`biz:workline:view-plane-scene` / `biz:workline:view-plane-snapshot` + 行级过滤 + 脱敏 + 审计。PR #73 已落地 `PlaneSceneView` / `PlaneSnapshot` 读模型和 route；本分支补齐 `PlaneReadSecurityPolicy`，集中声明专用权限、`WORKLINE_LOCAL` scope、脱敏字段 deny-list 与审计 action，并将 route 权限依赖改为引用该 policy；本分支继续接入 `PlaneReadPrincipal`，以 superuser 或 `created_by` owner 作为首版 WorkLine-local 行级过滤口径，并在 scene/snapshot 成功读取后写入统一 audit log。
- [x] External callback HMAC body 签名 + idempotency 复合主键 + typed `ExternalReference` 全部就绪。PR #73 已落地 body HMAC、nonce、payload hash、`API_PATH` 感知校验和 typed evidence envelope；本分支补齐 `ExternalReferenceCatalog`、source-version drift 分类合同、WMS evidence JSONB GIN 索引、只读 drift job 与 `docs/contracts/evidence-catalog.md`，并完成 callback / fulfillment / device_command / device_event / reconciliation 跨域 idempotency 矩阵。
- [x] RuntimeInbox 支持 ACK-before-processing 后的重试、死信、人工重放和幂等审计。technical cleanup scope 已将入站热路径收敛为 `RuntimeInbox -> InboundNormalizerRegistry -> RuntimeCapabilityDispatcher -> material-flow runtime service -> RuntimeIntent / EffectPort`，并用 `tests/workline_runtime/test_runtime_capability_dispatcher.py` 与 legacy absence guardrail 覆盖未知 capability、未声明 profile、duplicate callback 和旧 import 回流。
- [x] DeviceCommand 调度策略支持设备能力选择、优先级、deadline、串行/限流、取消和状态快照 TTL。PR #73 已落地可过期 command lease；本分支补齐 `DeviceDispatchPolicy` 策略合同与 fresh IDLE、stale/UNKNOWN、RUNNING deadline、HOLD/RECONCILING 冻结测试，并接入 DeviceCommandGateway 热路径：过期本地 IDLE 快照必须先重查 ECS status probe，fresh busy/hard-state 本地短路。

---
