---
status: Ready for implementation
source_design: docs/superpowers/specs/2026-06-19-workline-multi-object-state-machine-design.md
phase: C0
created_at: 2026-06-23
---

# WorkLine C0：Resource Projection 与 BinTransitMembership 基座治理任务文档

## 背景

`v0.8.0.0` 已完成 WorkLine 料盘根域 Phase 1：`material_units` 成为料盘根实体，关键读写路径已能通过 `pkg_code/current_material_unit_id/current_location` 串联粗分机与 SMT。

下一阶段不应直接进入 Runtime 强校验。强校验需要可信的 resource/handling 事件输入，否则非法 transition、投影冲突、队列漂移会混在一起，排障时无法判断是状态机错、resource 投影错，还是历史隐式关联导致的脏数据。

因此本阶段定义为 **C0：Resource Projection 与 BinTransitMembership 基座治理**。它是 C1 Runtime 强校验的前置阶段，不做强阻断，不改前端，不实现扫码点 3 转线。

## 为什么现在做

- Phase 1 已把料盘状态写入 `material_units.status`，但 resource 域仍有历史类型债和隐式关联。
- C 阶段设计要求 `resource domain` 产出 projection transition event，`handling domain` 产出 queue membership transition event。
- 当前 `ResourceStateEvent` 和 active projection 已存在，但 `session_id` 类型不一致、投影间没有 SQL 外键、`BinTransitMembership` 尚未落库。
- 如果跳过 C0，C1 的 Runtime 强校验会依赖不稳定的投影事实，后续回滚和对账成本更高。

## 已验证当前状态

| 事实 | 代码证据 | 影响 |
| --- | --- | --- |
| resource append-only 事实存在，`session_id` 是 `str \| None` | `src/app/resource/models/resource.py:350-379` | 与 `WorklineSession.id` 不能直接类型安全 join |
| `RackPlacement`/`RackBinMount`/`BinPlacement` active projection 的 `session_id` 是字符串 | `src/app/resource/models/resource.py:400-522` | 同一 Session 在不同表里有 string/int 双轨 |
| `BinMaterialMount` 与 `BinCellOccupancy` 均保存物料属性字段 | `src/app/resource/models/resource.py:550-632` | `material_identity_key/material_code/lot_code/date_code` 存在重复写入 |
| `BinMaterialMount.bin_cell_occupancy_id` 只是 int 约定，不是 SQL 外键 | `src/app/resource/models/resource.py:550-586` | 脏数据只能靠应用层发现 |
| active `pkg_code` 唯一索引已存在 | `src/app/resource/models/resource.py:657-694` | 这是正确能力，应保留 |
| `BinContentSnapshot` 使用 `source_session_id: int` | `src/app/resource/models/resource.py:697-752` | 证明 resource 域内已经存在 int session 口径 |
| `ResourceProjectionService` 同时接收 `session_id: str` 与 `workline_session_id: int` | `src/app/resource/services/projection_service.py:220-239`、`584-612`、`771-793` | 需要统一边界，不应长期双轨 |
| `material_units.current_location` 已由 mount/unmount 同事务更新 | `src/app/resource/services/projection_service.py:764-768`、`922-927`、`933-947` | 需要补一致性校验和修复路径 |
| `HandlingMove` 是单次搬运记录，不适合作为队列 membership | `src/app/handling/models/operation.py:139-207` | 需要独立 `BinTransitMembership` 投影视图 |
| `RESOURCE_WAIT` 仍写入 Session context 与诊断记录 | `src/workline_runtime/runtime_intent_effects.py:1660-1720` | C0 要把等待原因与 manifest subject 对齐 |

## 目标

1. 统一 resource 投影中的 Session 关联口径，消除 `session_id` string/int 双轨。
2. 明确 resource active projection 与 `material_units.current_location` 的权威边界和一致性校验。
3. 为 resource projection 变更产出统一 transition event，供 C1 Runtime 强校验和 Trace 消费。
4. 新建 `BinTransitMembership`，表达料箱在 SMT 流水线队列中的当前 membership，不复用 `HandlingMove`。
5. 为 handling queue membership 变更产出 transition event。
6. 将 `RESOURCE_WAIT` 的 `resource_kind/resource_key` 与 manifest 声明的 subject 对齐。

## 非目标

- 不做 `RuntimeIntent.transition(...)` 强阻断；这是 C1。
- 不把 Phase 1 的 WARN-only transition 校验升级为 BLOCK；这是 C1。
- 不做前端对象视图；这是 C2。
- 不一次性清理全部 `context_json.phase/business_phase` 散读面；这是 C3。
- 不实现扫码点 3 转线、多扫码点、多料箱并发完整队列 writer；这是 C4 或后续。
- 不合并 `BinContentSnapshot` 与 active projection；快照是时间点证据，active projection 是当前状态，二者语义不同。
- 不把 `material_units.current_location` 提升为格位容量权威；resource projection 仍是格位容量、冲突、对账事实源。

## 范围拆分

| # | 子任务 | 优先级 | 依赖 |
| --- | --- | --- | --- |
| C0-1 | Resource Session 关联口径统一 | P1 | 无 |
| C0-2 | Resource 投影外键/应用层约束策略 | P1 | C0-1 |
| C0-3 | Mount/Occupancy 物料属性冗余治理 | P1 | C0-1 |
| C0-4 | `material_units.current_location` 一致性校验与修复入口 | P1 | C0-2, C0-3 |
| C0-5 | Resource projection transition event | P1 | C0-2 |
| C0-6 | `BinTransitMembership` 投影视图 | P1 | 无 |
| C0-7 | Handling queue membership transition event | P1 | C0-6 |
| C0-8 | `RESOURCE_WAIT` subject 合同对齐 | P1 | C0-5, C0-7 |
| C0-9 | C0 测试矩阵与迁移验收 | P1 | 全部 |

## 依赖图

```text
C0-1 Resource Session 口径
  ├── C0-2 FK/应用层约束策略
  │     ├── C0-4 current_location 一致性校验
  │     └── C0-5 resource projection transition event
  └── C0-3 Mount/Occupancy 冗余治理
        └── C0-4 current_location 一致性校验

C0-6 BinTransitMembership
  └── C0-7 handling queue membership transition event

C0-5 + C0-7
  └── C0-8 RESOURCE_WAIT subject 合同对齐

全部
  └── C0-9 测试矩阵与迁移验收
```

## 子任务细化

### C0-1：Resource Session 关联口径统一

**问题**：resource 事实与 active projection 多处 `session_id` 是字符串，但 WorkLine/handling 域使用 `int`。这让 SQL join、诊断查询、Trace 拼装和后续强校验都要做类型猜测。

**任务**：

- 盘点 resource 表中的 `session_id` 字段，区分展示字段与关联字段。
- 统一新增或迁移到 `workline_session_id: int | None` 口径。
- 对确需保留的外部展示字段改名为 `source_session_code` 或放入 `payload_json`，避免与 `WorklineSession.id` 混淆。
- C0 不保留旧 string `session_id` 兼容入口；新写面和新查询面统一只使用 `workline_session_id`。

**验收**：

- resource active projection 表可通过 int `workline_session_id` 与 `workline_sessions.id` 关联。
- 旧 string `session_id` 不再作为新逻辑的关联依据。
- 迁移有 downgrade；历史 string 值无法转换时进入 evidence/payload，不静默丢弃。

### C0-2：Resource 投影外键/应用层约束策略

**问题**：resource 投影表之间存在隐式 int 约定，例如 `bin_cell_occupancy_id`，但没有 SQL 外键。是否补 FK 需要按现有写入顺序、历史数据质量和 downgrade 成本决策。

**任务**：

- 默认补 SQL FK：`BinMaterialMount → BinCellOccupancy`、resource projection → `WorklineSession`、`BinTransitMembership → HandlingOperation/HandlingMove` 等 active projection 强关联必须由数据库约束兜底。
- 仅多态 append-only evidence 引用允许使用应用层 integrity check，并写明不能补 FK 的原因。
- 建立 `resource_projection_integrity_check` 风格的诊断入口，输出 orphan mount、orphan session、active duplicate、material/location drift。

**验收**：

- 每个 active projection 隐式关联都有 SQL FK；只有多态 evidence 引用可退为明确的应用层校验。
- 校验入口可列出脏数据明细，不只返回计数。
- 脏数据不自动修复，除非调用专门 repair dry-run/confirm 流程。

### C0-3：Mount/Occupancy 物料属性冗余治理

**问题**：`BinMaterialMount` 是料盘明细，`BinCellOccupancy` 是格位聚合，但两者都保存 `material_identity_key/material_code/lot_code/date_code`。Phase 1 引入 `material_units` 后，料盘属性源头应更清楚。

**任务**：

- 明确字段权威：料盘身份与物料属性以 `material_units` 为主，`BinMaterialMount` 保留事件证据快照，`BinCellOccupancy` 仅保留格位聚合需要的最小字段。
- 设计迁移策略：先新增派生/兼容读取，再减少新写入冗余，不做一次性破坏历史快照。
- 保留 `BinContentSnapshot`，不把快照表误合并到 active projection。

**验收**：

- 新写入路径不再把同一物料属性无意义双写到多个 active 表。
- 容量计算仍以 `BinCellOccupancy.reel_count/used_depth_mm/remaining_depth_mm` 为准。
- 对账快照仍能还原当时 evidence。

### C0-4：`material_units.current_location` 一致性校验与修复入口

**问题**：Phase 1 已让 mount/unmount 同事务更新 `material_units.current_location`，但还没有系统化校验 `material_units` 与 resource active projection 是否漂移。

**任务**：

- 增加一致性校验：按 `pkg_code` 比对 `material_units.current_location` 与 active `BinMaterialMount/BinCellOccupancy`。
- 对缺失、冲突、多 active mount、location 不一致分别给出 reason_code。
- 修复入口默认 dry-run；确认后按 resource projection 为权威修复 `material_units.current_location`，或进入 RECONCILING。

**验收**：

- drift 可检测、可解释、可测试。
- 修复不会反向用 `material_units.current_location` 覆盖 resource projection。
- 检测到冲突时进入 RECONCILING/hold，而不是静默覆盖。

### C0-5：Resource projection transition event

**问题**：C 阶段要求 resource domain 输出 projection transition event。当前已有 `ResourceStateEvent` 事实账本，但缺少面向状态机/Trace 的 from/to projection transition。

**任务**：

- 新增统一 `object_transition_events` append-only 表，resource projection 与 handling queue membership 共用同一事件合同。
- `object_transition_events` 的模型、repository、service 归属 `src/app/workline` 共享 evidence/trace 边界；resource 与 handling 只调用共享 `ObjectTransitionEventService`，不各自维护私有 transition event 写入器。
- 事件结构：`domain/object_type/object_key/projection_type/from_state/to_state/reason_code/source_event_id/source_ref_json/evidence_json/workline_session_id/trace_id/occurred_at/idempotency_key`。
- `idempotency_key` 必须按派生 transition 粒度生成，不复用原始 fact 的单键；建议 key builder 至少包含 `source_event_id + domain + object_type + object_key + projection_type + to_state/reason_code`，确保一条原始 fact 派生多条 transition 时不会互相吞掉。
- 索引合同：
  - `idempotency_key IS NOT NULL` partial unique，防止幂等重放重复生成事件。
  - `trace_id, occurred_at`，支撑 Trace 时间线查询。
  - `workline_session_id, occurred_at`，支撑 Session 级回放。
  - `domain, object_type, object_key, occurred_at`，支撑对象详情页和审计查询。
  - `domain, source_event_id`，支撑从原始事实或 handling move 反查 transition evidence。
- 事件来源覆盖 rack placement、rack-bin mount、bin placement、bin material mount、bin cell occupancy。
- 同一派生 idempotency key 重放时不得重复产出 transition；同一 `source_event_id` 派生出的兄弟 transition 必须能同时保留。
- transition event 不替代原 `ResourceStateEvent`，而是在事实落账和 active projection 更新后生成的可观测层。

**验收**：

- `MATERIAL_MOUNTED`、`MATERIAL_UNMOUNTED`、`RACK_ARRIVED`、`BIN_ARRIVED` 至少四类路径产出 transition event。
- RECONCILING 路径也产出失败/不可信 transition evidence。
- Trace 可按 `trace_id/workline_session_id/object_key` 查询这些事件。
- migration source 结构测试断言上述索引和 partial unique 存在。

### C0-6：`BinTransitMembership` 投影视图

**问题**：SMT 料箱流水线队列是当前对象状态，但 `HandlingMove` 只是单次搬运记录。复用 `HandlingMove` 会把“当前在哪个队列”和“某次从 A 到 B 搬运”混成一个概念。

**任务**：

- 新建 `BinTransitMembership` 模型、repository、service，并按项目规则导出。
- 字段建议：`bin_code/placeholder_key/workline_id/workline_code/current_queue/membership_status/handling_operation_id/handling_move_id/trace_id/workline_session_id/entered_at/left_at/evidence_json`。
- active 约束：同一真实 `bin_code` 同一时间只能有一个 active membership；placeholder 同理。
- `handling_operation_id`/`handling_move_id` 只做证据关联，不反向承载 `HandlingMove`。
- schema 支持 `INFEED_BUFFER_QUEUE/ENTRY_SCAN_QUEUE/WORKSTATION_WAIT_QUEUE/WORKSTATION_ACTIVE/EXIT_ROUTING_SCAN_QUEUE/RETURN_SCAN_QUEUE/RETURN_WAIT_QUEUE/NG_REJECT_QUEUE` 等完整队列枚举；C0 runtime writer 只启用当前代码可观测子集，Gate3/Gate4 和多扫码点全量队列进入后续 TODO。

**验收**：

- 料箱进入/离开队列有 active/history 记录。
- 同一 bin 不允许同时 active 于两个队列。
- 未扫码 placeholder 可先占位，扫码解析后能转为真实 `bin_code`。

### C0-7：Handling queue membership transition event

**问题**：有了 `BinTransitMembership` 后，队列变化也需要统一 transition event，供 Trace/current_activity/C1 合同校验使用。

**任务**：

- 使用统一 `object_transition_events` 记录 queue membership transition：`domain=handling/object_type=BIN_TRANSIT/object_key/from_queue/to_queue/reason_code/workline_session_id/trace_id`。
- `handling_operation_id/handling_move_id` 写入 `source_ref_json` 或 `evidence_json`，不把 handling-only 字段扩散成统一表的一等列。
- 在 handling lifecycle 或 operation service 的队列变化边界写入事件。
- 与 `HandlingMove` 保持单向关联：move 可触发 membership 变化，但 membership 不复用 move 状态。

**验收**：

- 队列进入、队列离开、队列切换、RECONCILING 四类路径有事件。
- 事件能串联回 handling operation 和 WorkLine trace。
- 对同一幂等输入重复处理不会重复生成 active membership。

### C0-8：`RESOURCE_WAIT` subject 合同对齐

**问题**：`RESOURCE_WAIT` 当前记录 `resource_kind/resource_key`，但 C 阶段要求它必须引用 manifest 中声明的 subject。否则等待状态无法被前端/Trace/强校验准确归类。

**任务**：

- 扩展 `RESOURCE_WAIT` evidence，加入 `subject_type/subject_key/projection_type`。
- 校验 `subject_type` 必须能映射到 manifest `session_subject/state_machines/pipeline_queues/resource_boundaries` 中的声明。
- C0 不保留旧 `resource_kind/resource_key` 作为新写面兼容入口；旧值只作为 evidence 展示或迁移材料。
- 同步更新 `ResourceWaitEvidence`、diagnostics builder/registry、`inbox_batch_processor` 的 retry/resolve context，避免旧 `resource_key` 继续作为内部清理和诊断主键。

**验收**：

- 资源等待能明确指向料盘、料箱、货架位、队列 membership 中的一类。
- 未声明 subject 的 `RESOURCE_WAIT` 返回受控错误/诊断 evidence，不再写入新的模糊等待。
- 诊断记录和 timeline payload 包含 subject 字段。

### C0-9：测试矩阵与迁移验收

**任务**：

- 为每个子任务补 unit/integration 测试。
- 对所有新迁移覆盖 upgrade/downgrade，并用 migration source 结构测试断言 FK、partial unique index、drop 顺序和不可逆 guard。
- 对 `object_transition_events` 覆盖索引结构测试：幂等 partial unique、trace/session/object/source 查询索引均必须存在。
- 对 `object_transition_events` 覆盖派生幂等测试：一条原始 fact 可派生多条 transition；同一派生 transition 重放不重复，但兄弟 transition 不被误杀。
- 迁移旧 resource 测试合同：新写面只传 `workline_session_id`，不再传 string `session_id`。
- 增加 grep/结构性断言，避免 `src/app/resource/**` 新逻辑继续依赖 string `session_id` 做关联。
- 迁移 `RESOURCE_WAIT` 测试合同：`subject_type/subject_key/projection_type` 必填，未声明 subject 受控错误，context/diagnostic/timeline payload 都包含 subject。
- 增加导出结构测试：新增 workline/handling 模型、repository、service 必须出现在各自 `__init__.py` 的 import 与 `__all__` 中。
- 增加 TODO/监控合同结构检查：后续监控和 benchmark 文档不得再把新 `RESOURCE_WAIT` 主分组写成 `resource_kind/resource_key`。

**验收命令**：

- `uv run pytest tests/resource/ tests/handling/ tests/workline_runtime/`
- `uv run pytest tests/integration/workline_runtime/`
- `uv run ruff format --check .`
- `uv run ruff check .`
- 如涉及真实 DB 迁移：`uv run alembic upgrade head` 与 `uv run alembic downgrade -1`

## 测试矩阵

| 层级 | 场景 | 最低数量 |
| --- | --- | --- |
| Model | 新字段、CHECK、索引、active 唯一约束 | +6 |
| Migration | C0 迁移 upgrade/downgrade、历史 `session_id` 清理、FK/index/transition event 查询索引/guard 结构断言 | +7 |
| Resource Unit | mount/unmount transition event、drift 检测、repair dry-run、旧 string `session_id` 结构禁用 | +10 |
| Workline Unit | 共享 `ObjectTransitionEventService`、派生幂等 key、一 fact 多 transition、source_ref/evidence、重复重放去重 | +8 |
| Handling Unit | membership active/history、placeholder 解析、重复幂等、C0 writer 子集 | +7 |
| Runtime Unit | `RESOURCE_WAIT` subject 校验、未声明 subject 受控错误、evidence/diagnostic/retry context/timeline payload | +9 |
| Export Contract | workline/handling/resource 新模型、repository、service 导出结构 | +3 |
| Integration | 料盘入格→出格→location 校验、料箱队列进入→切换→离开 | +4 |
| Regression | Phase 1 material_units 状态写面、NG、SMT complete 不回退 | 现有相关测试继续通过 |

## 回滚策略

- 所有新增表和字段必须有 Alembic downgrade。
- 迁移不删除 `ResourceStateEvent.payload_json`、`BinContentSnapshot`、`BinContentSnapshotItem` 原始 evidence。
- `session_id` 统一迁移采用破坏性清理口径：开发/测试库可先清理脏数据；旧 string 不保留为关联字段，需要展示时迁入 `payload_json` 或 `source_session_code`。
- `BinTransitMembership` 可回滚为只读/停止写入，不影响 `HandlingMove` 原有生命周期。
- transition event 是可观测层，回滚时不应影响原 resource projection active 事实。

## 风险与防线

| 风险 | 防线 |
| --- | --- |
| 历史 string `session_id` 含非数字值 | 迁移前输出清理报告；开发/测试库允许破坏性清理；需要保留的展示值进入 payload/evidence |
| FK 直接补上导致历史脏数据迁移失败 | 先跑 integrity check 和清理脚本；active projection 强关联默认补 SQL FK |
| `material_units.current_location` 被误当权威 | 文档、测试和修复入口都规定 resource projection 为权威 |
| `HandlingMove` 与 membership 双写不一致 | membership 由明确服务维护，move 只作为证据引用 |
| transition event 重放重复 | 所有事件必须有 idempotency key 或唯一约束 |
| transition event 查询随数据量退化 | `object_transition_events` 必须随表定义同步落地 trace/session/object/source/time 索引 |
| C0 范围膨胀到 C1/C2 | 不做 Runtime 强阻断，不做前端，只产出后端基座和事件 |

## 文件影响面

| 路径 | 预期变更 |
| --- | --- |
| `migrations/versions/` | 新增 C0 schema 迁移 |
| `src/app/resource/models/resource.py` | `workline_session_id` 关联口径、resource active projection FK/索引 |
| `src/app/resource/repositories/` | integrity/repair 相关 repository |
| `src/app/resource/services/projection_service.py` | 保留 active projection 编排，调用 resource C0 协作者与共享 transition event service |
| `src/app/resource/services/projection_integrity_service.py` | orphan/duplicate/material-location drift 诊断 |
| `src/app/resource/services/material_location_consistency_service.py` | current_location dry-run/confirm repair |
| `src/app/resource/services/__init__.py` | 新 service 导出 |
| `src/app/workline/models/object_transition_event.py` | 共享 `ObjectTransitionEvent` append-only 模型、source_ref/evidence 与索引合同 |
| `src/app/workline/models/__init__.py` | 导出 `ObjectTransitionEvent` 相关模型/Schema/枚举 |
| `src/app/workline/repositories/object_transition_event_repository.py` | 共享 transition event repository |
| `src/app/workline/repositories/__init__.py` | 导出共享 transition event repository |
| `src/app/workline/services/object_transition_event_service.py` | 共享 `object_transition_events` 写入、派生幂等键与查询服务 |
| `src/app/workline/services/__init__.py` | 导出共享 transition event service |
| `src/app/handling/models/operation.py` 或新模型文件 | `BinTransitMembership` 模型 |
| `src/app/handling/models/__init__.py` | 导出 `BinTransitMembership` 模型/Schema/枚举 |
| `src/app/handling/repositories/` | membership repository |
| `src/app/handling/repositories/__init__.py` | 导出 membership repository |
| `src/app/handling/services/` | membership service，调用统一 transition event 写入 |
| `src/app/handling/services/__init__.py` | 导出 membership service |
| `src/workline_runtime/runtime_intent.py` | 如需扩展 `RESOURCE_WAIT` payload contract |
| `src/workline_runtime/runtime_intent_effects.py` | `RESOURCE_WAIT` subject 写入和校验 |
| `src/workline_runtime/resource_wait_evidence.py` | `RESOURCE_WAIT` subject 证据、diagnostic key、session context 合同 |
| `src/workline_runtime/diagnostics/builder.py` | `RESOURCE_WAIT` operator guidance 改为 subject 维度 |
| `src/workline_runtime/diagnostics/registry.py` | `RESOURCE_WAIT` registry fix/operator action 改为 subject 维度 |
| `src/workline_runtime/plugin_manifest.py` | subject 引用校验辅助 |
| `src/app/workline/services/inbox_batch_processor.py` | RESOURCE_WAIT retry/resolve context 从 resource_key 切到 subject key |
| `tests/resource/` | resource 模型、迁移、投影事件、drift 测试 |
| `tests/workline/` | 共享 transition event service、派生幂等、查询索引结构测试 |
| `tests/handling/` | membership 模型和 lifecycle 测试 |
| `tests/workline_runtime/` | RESOURCE_WAIT subject 和 timeline 测试 |

## Definition of Done

1. Resource projection 的 Session 关联有统一 int 口径，旧 string 字段不再作为新逻辑 join 依据。
2. Resource active projection 隐式关系默认有 SQL FK；只有多态 evidence 引用可退为应用层 integrity check。
3. `BinMaterialMount`/`BinCellOccupancy` 的物料属性冗余写入策略明确，新增写面不继续无意义双写。
4. `material_units.current_location` drift 可检测、可 dry-run、可按 resource 权威修复或进入 RECONCILING。
5. 统一 `object_transition_events` 归属 `src/app/workline` 共享 evidence/trace 边界，覆盖至少 rack/bin/material mount/unmount 主路径。
6. `object_transition_events` 具备派生 transition 粒度幂等唯一约束、domain-specific source_ref/evidence，以及 Trace/Session/Object/Source 回查索引。
7. `BinTransitMembership` 支持 active/history、placeholder、真实 bin 切换和唯一 active 约束。
8. Handling queue membership transition event 写入统一 `object_transition_events`，可被 Trace/current_activity 消费。
9. `RESOURCE_WAIT` 包含 manifest subject 引用，不再只有模糊 `resource_kind/resource_key`。
10. 所有迁移可 upgrade/downgrade，新增测试覆盖成功、失败、幂等、冲突路径。
11. `uv run pytest` 相关测试、`uv run ruff format --check .`、`uv run ruff check .` 通过。

## 后续阶段衔接

C0 完成后进入：

- **C1 Runtime 强校验**：`RuntimeIntent.transition(...)`、manifest transition 强阻断、发令快照、RECONCILING 出口强校验。
- **C2 Trace/前端对象视图**：按料盘、货架位 projection、料箱 projection、料箱队列、命令活动展示。
- **C3 context_json 散读清理**：废弃残留 `phase/business_phase`。
- **C4 转线/多队列扩展**：扫码点 3 转线、多扫码点、多料箱并发队列。

## plan-eng-review 结论

### Step 0：范围挑战

复杂度门禁已触发：C0 计划触达 `resource`、`handling`、`workline_runtime`、迁移和测试，超过 8 个文件且新增服务超过 2 个。用户选择按当前 C0 单 PR 完整评审，不缩小为只做 resource projection。

Scope accepted as-is 的前提：

- 这是未发布系统，允许破坏性优化，开发/测试库可做破坏性清理。
- 不保留旧 string `session_id` 新写面兼容入口，统一切到 `workline_session_id`。
- `RESOURCE_WAIT` subject 合同是 C0 P1，不再作为 P2 延后。
- 后续 Gate3/Gate4、多扫码点、完整队列 writer（7 个物理队列 + `NG_REJECT_QUEUE`）进入 TODO，不塞进 C0。

### Architecture Review

1. `[P1] (confidence: 9/10) src/app/resource/models/resource.py:350-379 — Resource append-only 事实已有 string session_id，C0 必须统一 int Session 关联口径。`
   结论：破坏性切换到 `workline_session_id: int | None`，旧 string 值只作为 payload/evidence 保留。
2. `[P1] (confidence: 9/10) src/app/resource/models/resource.py:550-586 — BinMaterialMount.bin_cell_occupancy_id 只是 int/index，不是 SQL FK。`
   结论：active projection 强关联默认补 SQL FK，多态 append-only evidence 才允许应用层 integrity check。
3. `[P1] (confidence: 9/10) src/app/handling/models/operation.py:139-207 — HandlingMove 是单次 move，不具备 current_queue/transit_status 语义。`
   结论：新建 `BinTransitMembership`，不复用 `HandlingMove`。
4. `[P1] (confidence: 9/10) docs/superpowers/specs/2026-06-19-workline-multi-object-state-machine-design.md:700-765 — 设计要求完整多队列模型，但当前代码只支持部分扫码点。`
   结论：schema 支持完整队列枚举，C0 runtime writer 只启用当前可观测子集，完整 writer 进入 TODO。
5. `[P1] (confidence: 9/10) src/workline_runtime/runtime_intent_effects.py:1660-1720 — RESOURCE_WAIT 当前只写 resource_kind/resource_key。`
   结论：`RESOURCE_WAIT` 新写面改为 manifest subject 合同，未声明 subject 返回受控错误。
6. `[P1] (confidence: 9/10) src/app/workline/models/timeline.py:115-120, src/app/workline/services/trace_resource_view_builder.py:40-63 — workline 域已承载 timeline/trace evidence 视图，object_transition_events 不应落在 resource 私有边界。`
   结论：`ObjectTransitionEvent` 模型、repository、service 放到 `src/app/workline` 共享 owner，resource 和 handling 都调用共享服务。
7. `[P1] (confidence: 9/10) src/workline_runtime/resource_wait_evidence.py:46-124, src/workline_runtime/diagnostics/builder.py:123-129, src/app/workline/services/inbox_batch_processor.py:1125-1137 — RESOURCE_WAIT 旧合同散布在 evidence、diagnostic 和 retry resolve 中。`
   结论：C0 必须一次性把 helper、diagnostics 文案和 retry/resolve context 切到 subject 合同，不能只改 intent/effects。
8. `[P2] (confidence: 8/10) TODOS.md:135, TODOS.md:188 — 后续监控/benchmark TODO 仍按 resource_kind/resource_key 统计 RESOURCE_WAIT。`
   结论：后续监控维度同步改成 `subject_type/subject_key/projection_type`，避免 C0 后续任务沿用旧合同。

### Code Quality Review

1. `[P1] (confidence: 8/10) src/app/resource/services/projection_service.py:720-947 — ResourceProjectionService 已同时承担事实、投影、快照、material_units cache 更新。`
   结论：C0 新增 `ResourceProjectionService` 专用协作者，拆出 transition event、integrity check、material location consistency 服务，主服务保留编排。
2. `[P1] (confidence: 9/10) src/app/resource/models/resource.py:350-632 — 字段合同跨表不一致，session_id string/int 双轨会扩散。`
   结论：现在同步字段合同，不保留长期双轨。
3. `[P2] (confidence: 8/10) docs/superpowers/specs/2026-06-23-workline-c0-resource-projection-foundation.md — 回滚策略原本偏兼容，和未发布系统状态不匹配。`
   结论：按破坏性清理口径写明开发/测试库清理、旧值 evidence 化和 FK 清理前置。
4. `[P1] (confidence: 9/10) src/app/workline/services/__init__.py:1-104, src/app/handling/services/__init__.py:1-17 — 项目要求新 Service 必须导出，计划只写了 resource service 导出。`
   结论：新增 workline/handling 模型、repository、service 都必须同步更新对应 `__init__.py`，并用结构测试兜底。

### Test Review

```text
CODE PATHS                                                     USER FLOWS
[+] Resource session migration                                 [+] Trace/resource audit
  ├── [GAP] string session_id -> workline_session_id              ├── [GAP] [→E2E] 按 trace_id 回放 resource + handling transition
  ├── [GAP] non-numeric legacy value -> payload/evidence          └── [GAP]        按 workline_session_id 回放 C0 evidence
  └── [GAP] new write rejects string session_id

[+] Active projection FK/integrity                              [+] 现场对账/修复
  ├── [GAP] orphan mount/session detection                        ├── [GAP] dry-run 输出漂移明细
  ├── [GAP] duplicate active detection                            └── [GAP] confirm 后按 resource 权威修复 material_units
  └── [GAP] FK/index/drop order migration structure

[+] object_transition_events                                    [+] 对象详情/审计
  ├── [GAP] shared ObjectTransitionEventService owner             ├── [GAP] 对象键查询按时间排序
  ├── [GAP] mount/unmount/rack/bin transition                     ├── [GAP] 按 workline_session_id 回放 transition
  ├── [GAP] RECONCILING transition evidence                       ├── [GAP] 幂等重放不重复展示
  ├── [GAP] one source_event_id -> multiple sibling transitions   └── [GAP] 兄弟 transition 不被同一 fact key 误杀
  ├── [GAP] source_ref_json/evidence_json stores domain refs
  ├── [GAP] idempotency partial unique
  └── [GAP] trace/session/object/source indexes

[+] BinTransitMembership                                        [+] SMT 料箱流水线
  ├── [GAP] active/history lifecycle                              ├── [GAP] [→E2E] 进入队列 -> 切换 -> 离开
  ├── [GAP] placeholder -> bin_code resolution                    └── [GAP] 同一 bin 双 active 冲突可解释
  └── [GAP] C0 writer 子集，不覆盖 Gate3/Gate4 全量 writer

[+] RESOURCE_WAIT subject                                       [+] Runtime 等待诊断
  ├── [GAP] subject_type/subject_key/projection_type required     ├── [GAP] 未声明 subject 受控错误
  ├── [GAP] ResourceWaitEvidence diagnostic key uses subject       ├── [GAP] retry resolve 按 subject 清理诊断
  └── [GAP] context/diagnostic/timeline payload contains subject  └── [GAP] 前端/Trace 不再看到模糊 resource wait

[+] Export contracts                                             [+] 后续任务一致性
  ├── [GAP] workline models/repositories/services __init__ exports ├── [GAP] TODO 监控按 subject 维度统计
  └── [GAP] handling models/repositories/services __init__ exports └── [GAP] benchmark 不再使用 resource_key 主分组

COVERAGE: 0/36 planned C0 paths currently covered by the plan-specific tests
QUALITY: ★★★:0 ★★:0 ★:0 | GAPS: 36 (3 E2E/integration-worthy)
Legend: ★★★ behavior + edge + error | ★★ happy path | ★ smoke check | [→E2E] needs integration test
```

测试结论：C0 是计划阶段，当前没有新实现代码。所有 GAP 都已进入 C0-9 测试矩阵和验收命令，旧 string `session_id`、`RESOURCE_WAIT` subject 全链路、共享 transition owner、source_ref/evidence、派生幂等 key、导出合同、migration/FK/index 结构测试列为 P1。

### Performance Review

1. `[P1] (confidence: 9/10) src/app/resource/models/resource.py:382-397 — ResourceStateEvent 已有幂等/source/resource_time 索引，新 object_transition_events 如果不定义索引会低于现有事实账本能力。`
   结论：新增 `object_transition_events` 时同步落地幂等 partial unique、Trace/Session/Object/Source 查询索引，并用 migration source 结构测试断言。
2. `[P1] (confidence: 9/10) docs/superpowers/specs/2026-06-23-workline-c0-resource-projection-foundation.md:167-175 — transition event 幂等如果只复用原始 fact 单键，会把一条 fact 派生出的多条 transition 误判为重复。`
   结论：幂等 key 按派生 transition 粒度生成，至少包含 source、domain、object、projection、to_state/reason_code。

## NOT in scope

- C1 Runtime 强阻断：C0 只产出可信 projection/evidence，不把 WARN-only transition 升级为 BLOCK。
- C2 Trace/前端对象视图：C0 只保证后端事件和查询索引，UI 展示后置。
- C3 `context_json.phase/business_phase` 散读清理：与 projection 基座无直接依赖。
- C4 转线、多扫码点、7 队列 full runtime writer：schema 合同先支持，完整 runtime writer 作为 TODO 后置。
- `BinContentSnapshot` 合并进 active projection：快照是时间点 evidence，active projection 是当前事实。
- 生产监控阈值：C0 可产出结构化 evidence，阈值需等真实运行数据。

## What already exists

| 现有能力 | 复用方式 | 是否重建 |
| --- | --- | --- |
| `ResourceStateEvent` append-only 事实账本 | 保留为原始事实，transition event 作为可观测层叠加 | 不重建 |
| `ResourceProjectionService` mount/unmount 写面 | 保留编排入口，拆出 C0 专用协作者 | 不重写主流程 |
| `workline` timeline/trace resource view | 作为共享 transition evidence owner 的归属依据 | 不放进 resource 私有边界 |
| active projection partial unique 索引 | 延续现有 active 唯一约束风格 | 复用模式 |
| `BinContentSnapshot`/Item | 保留对账快照 evidence | 不合并 |
| `HandlingMove` | 作为 membership evidence 引用 | 不复用为队列状态 |
| manifest `session_subject/state_machines/pipeline_queues` dataclass | 作为 `RESOURCE_WAIT` subject 校验源 | 不另造配置格式 |

## Failure modes

| 新路径 | 现实故障 | 测试覆盖 | 错误处理要求 | 用户/运维可见性 |
| --- | --- | --- | --- | --- |
| session 迁移 | 历史 string 值非数字 | P1 migration 测试 | 清理报告 + evidence 化 | 可见，不静默丢 |
| active FK | 历史 orphan 阻塞迁移 | P1 migration/integrity 测试 | 先 dry-run 清理，再加 FK | 可见脏数据明细 |
| material location consistency | 多 active mount 导致 location 冲突 | P1 resource unit/integration | 进入 RECONCILING，不覆盖 | 可见 reason_code |
| transition event owner | resource/handling 各自实现私有 writer 导致幂等和查询语义漂移 | P1 workline unit/结构测试 | 单一共享 `ObjectTransitionEventService` | Trace 口径一致 |
| transition event evidence | 把 handling_operation_id 等 domain-only 字段做成统一表列导致表语义膨胀 | P1 workline unit/模型测试 | `source_ref_json/evidence_json` 保存 domain ref | 统一表保持稳定 |
| transition event | 幂等重放重复生成事件 | P1 unit + index 结构测试 | partial unique + duplicate 返回 | Trace 不重复 |
| transition event 派生 | 一条 fact 派生多条 transition 被单 fact key 误杀 | P1 workline unit | 派生粒度 key builder | 兄弟 transition 都可见 |
| transition event 查询 | Trace/session/object 查询退化 | P1 migration 结构测试 | 强制索引合同 | 性能不靠人工排查 |
| BinTransitMembership | 同一 bin 同时 active 于两队列 | P1 handling unit | unique active + RECONCILING | 可见冲突 evidence |
| placeholder 解析 | placeholder 与真实 bin 绑定错 | P1 handling unit/integration | 幂等解析 + 冲突诊断 | 可见 reason_code |
| RESOURCE_WAIT subject | 插件未声明 subject | P1 runtime unit | 受控错误/诊断 evidence | 不再写模糊 wait |
| RESOURCE_WAIT retry cleanup | 已处理 inbox 后仍按旧 resource_key resolve 诊断，导致等待卡片残留 | P1 runtime unit | retry/resolve context 使用 subject key | 诊断可正确关闭 |
| module exports | 新服务未导出导致实现后 ImportError 或调用方绕路径 import | P1 export contract test | 更新各层 `__init__.py` | 导入路径稳定 |

Critical gaps flagged: 0 after the planned tests and error handling above are included.

## Worktree parallelization strategy

| Step | Modules touched | Depends on |
| --- | --- | --- |
| Shared transition event schema/service | `migrations/`, `src/app/workline/`, `tests/workline/` | — |
| Resource schema + migration | `migrations/`, `src/app/resource/models/` | Shared transition event schema/service |
| Resource services + tests | `src/app/resource/services/`, `tests/resource/` | Resource schema |
| Handling membership + tests | `src/app/handling/`, `tests/handling/` | Shared transition event schema/service |
| Runtime RESOURCE_WAIT subject | `src/workline_runtime/`, `tests/workline_runtime/` | manifest subject contract |
| Integration tests | `tests/integration/` | Resource + Handling + Runtime |

Parallel lanes:

- Lane 0: Shared transition event schema/service。先固化共享 owner、派生幂等 key 和索引合同。
- Lane A: Resource schema + migration → Resource services + tests。
- Lane B: Handling membership + tests。可与 Lane A 的 service 部分并行。
- Lane C: Runtime `RESOURCE_WAIT` subject。可与 Lane B 并行。
- Lane D: Integration tests。等待 A/B/C 合并后执行。

Execution order: 先落 Lane 0 共享 migration/model/service 合同，再并行推进 Resource、Handling、Runtime，最后跑 integration 和全量质量门禁。

Conflict flags: Lane 0 与 Resource/Handling 都会碰 `migrations/versions/`，建议由 Lane 0 统一生成 C0 共享 migration 骨架，后续 lane 只追加在同一迁移或明确顺序生成，避免并行 worktree 产生冲突 revision。

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~2h / CC: ~20min)** — Resource schema — 统一 resource projection Session 关联口径
  - Surfaced by: Architecture Review — string `session_id` 与 `workline_session_id` 双轨。
  - Files: `src/app/resource/models/resource.py`, `migrations/versions/`, `tests/resource/`
  - Verify: `uv run pytest tests/resource/`
- [ ] **T2 (P1, human: ~3h / CC: ~30min)** — Resource integrity — 为 active projection 补 FK 与 integrity check
  - Surfaced by: Architecture Review — active projection 存在隐式 int 关联。
  - Files: `src/app/resource/models/resource.py`, `src/app/resource/services/projection_integrity_service.py`, `tests/resource/`
  - Verify: `uv run pytest tests/resource/`
- [ ] **T3 (P1, human: ~3h / CC: ~30min)** — Transition event — 新增统一 `object_transition_events` 与索引合同
  - Surfaced by: Architecture/Performance Review — resource/handling 需要共用 transition evidence，且查询不能退化。
  - Files: `src/app/workline/models/object_transition_event.py`, `src/app/workline/repositories/object_transition_event_repository.py`, `src/app/workline/services/object_transition_event_service.py`, `migrations/versions/`, `tests/workline/`
  - Verify: `uv run pytest tests/workline/`
- [ ] **T4 (P1, human: ~3h / CC: ~30min)** — Material location consistency — 增加 drift 检测与 dry-run/confirm 修复入口
  - Surfaced by: Code Quality/Test Review — `material_units.current_location` 已被同事务更新但缺少漂移治理。
  - Files: `src/app/resource/services/material_location_consistency_service.py`, `tests/resource/`, `tests/integration/workline_runtime/`
  - Verify: `uv run pytest tests/resource/ tests/integration/workline_runtime/`
- [ ] **T5 (P1, human: ~4h / CC: ~45min)** — Handling membership — 新增 `BinTransitMembership` active/history 投影视图
  - Surfaced by: Architecture Review — `HandlingMove` 与队列 membership 语义不同。
  - Files: `src/app/handling/models/`, `src/app/handling/repositories/`, `src/app/handling/services/`, `tests/handling/`
  - Verify: `uv run pytest tests/handling/`
- [ ] **T6 (P1, human: ~2h / CC: ~20min)** — Runtime wait subject — 对齐 `RESOURCE_WAIT` manifest subject 合同
  - Surfaced by: Architecture/Test Review — 模糊 `resource_kind/resource_key` 无法支撑 Trace 和强校验，且旧合同散布在 evidence/diagnostic/retry cleanup。
  - Files: `src/workline_runtime/runtime_intent.py`, `src/workline_runtime/runtime_intent_effects.py`, `src/workline_runtime/resource_wait_evidence.py`, `src/workline_runtime/diagnostics/builder.py`, `src/workline_runtime/diagnostics/registry.py`, `src/app/workline/services/inbox_batch_processor.py`, `src/workline_runtime/plugin_manifest.py`, `tests/workline_runtime/`
  - Verify: `uv run pytest tests/workline_runtime/`
- [ ] **T7 (P1, human: ~3h / CC: ~30min)** — C0 verification — 补 migration/FK/index/旧合同禁用结构测试
  - Surfaced by: Test Review — C0 迁移和破坏性合同需要结构测试兜底。
  - Files: `tests/resource/`, `tests/workline/`, `tests/handling/`, `tests/workline_runtime/`, `tests/integration/workline_runtime/`
  - Verify: `uv run pytest tests/resource/ tests/workline/ tests/handling/ tests/workline_runtime/ tests/integration/workline_runtime/`
- [ ] **T8 (P1, human: ~1h / CC: ~10min)** — Export contracts — 补齐 workline/handling 导出与结构测试
  - Surfaced by: Code Quality/Test Review — 新模型、repository、service 若不进 `__init__.py` 会违背项目导出规则。
  - Files: `src/app/workline/models/__init__.py`, `src/app/workline/repositories/__init__.py`, `src/app/workline/services/__init__.py`, `src/app/handling/models/__init__.py`, `src/app/handling/repositories/__init__.py`, `src/app/handling/services/__init__.py`, `tests/workline/`, `tests/handling/`
  - Verify: `uv run pytest tests/workline/ tests/handling/`

## Completion Summary

- Step 0: Scope Challenge — scope accepted as-is。
- Architecture Review: 8 issues found。
- Code Quality Review: 4 issues found。
- Test Review: diagram produced, 36 gaps identified and folded into C0-9。
- Performance Review: 2 issues found。
- NOT in scope: written。
- What already exists: written。
- TODOS.md updates: 1 prior item kept, 2 old TODO scopes aligned to subject contract。
- Failure modes: 0 critical gaps after planned controls。
- Outside voice: skipped，本轮未调用独立模型。
- Parallelization: 5 lanes, 3 parallel after shared schema, 1 sequential integration lane。
- Lake Score: 20/20 recommendations chose complete option。

## Implementation Readiness

- Ready to implement：是。实施前终审新增问题已折入任务、测试矩阵、文件影响面和 TODO。
- 先做 Lane 0：共享 `object_transition_events` schema/service、`source_ref_json/evidence_json`、派生幂等 key、导出结构测试。
- 再并行 Lane A/B/C：resource session/FK/drift、handling membership、`RESOURCE_WAIT` subject 全链路。
- 最后 Lane D：integration/E2E、migration upgrade/downgrade、ruff。
- 硬门禁：不得留下新写面 `resource_kind/resource_key`，不得新增未导出的 service/repository/model。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 未运行 |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | 未运行 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | CLEAR | 14 findings + 36 test gaps, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 后端计划，无 UI scope |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 未运行 |

- **VERDICT:** ENG CLEARED — ready to implement C0。

NO UNRESOLVED DECISIONS
