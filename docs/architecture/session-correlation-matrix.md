---
status: Phase 0 迁移矩阵
created_at: 2026-06-25
parent: docs/architecture/workline-and-plugin-restructuring.md
spec: docs/superpowers/archive/specs/2026-06-25-workline-restructuring-phase-0-spec.md
related: docs/architecture/target-state-contract.md
note: |
  本矩阵逐文件列出跨域 session FK 的目标态收敛方式。
  扫描基线：develop @ 2026-06-25，git grep workline_session_id /
  material_session_id / execution_session_id / current_session_id /
  source_session_id / sorting_session_id。
---

# ExecutionCorrelation 跨域 Session FK 迁移矩阵（P0-004）

> 父设计：主计划 §3.3 状态所有权、§4.2 ExecutionCorrelation correlation key
> 目标态合同：`target-state-contract.md` §3.2

## 1. 背景

主计划 §4.2 实测 16+ 模型文件包含 `session_id` / `execution_session_id` / `current_session_id` 跨域 FK。**runtime 之外的域不能把 `execution_session.id` 作为强 FK 扩散**（主计划 §3.3）。本矩阵给出逐文件收敛路径。

**核心原则**：
- 跨域读写都通过 `ExecutionCorrelation.correlation_id`，不通过 `execution_session.id`
- runtime 域内才使用 `execution_session_id` 强 FK；其他域只持 `correlation_id` 引用
- 不保留旧 string `session_id` 兼容入口（C0 已决定破坏性切换）

## 2. 矩阵字段

| 字段 | 说明 |
| --- | --- |
| `source_path` | 当前引用 session FK 的文件 |
| `current_symbol_or_table` | 当前符号、字段或表 |
| `owner_domain` | 当前状态真实 owner |
| `target_reference` | `ExecutionCorrelation.correlation_id`、runtime 内部 `execution_session_id` 或删除 |
| `phase` | Phase 1/2/3/5 |
| `migration_action` | add / replace / delete / keep-in-runtime |
| `risk` | LOW / MEDIUM / HIGH |

## 3. ExecutionCorrelation 目标 schema 草案

字段对齐主计划 §4.1/§4.2/§9.2；idempotency 不并入本表，按主计划 §5.4 独立 `idempotency_keys` 表通过 `execution_correlation_id` 引用：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | bigint | yes | 内部主键 |
| `correlation_id` | string(120) | yes | 跨域稳定 correlation key，唯一 |
| `execution_session_id` | bigint | nullable | runtime/orchestration 内部强 FK；跨域不得引用 |
| `trace_id` | string(120) | yes | 跨域 trace 时间线 |
| `source_event_id` | string(160) | nullable | 外部事件归因（request_id / event_id / command_code） |
| `business_owner_key` | string(160) | nullable | 业务 owner 审计、查询和冲突定位 |
| `created_at` | datetime | yes | naive UTC for DB |
| `updated_at` | datetime | yes | naive UTC for DB |

> `idempotency_keys` 独立表（主计划 §5.4）：PRIMARY KEY `(provider_code, operation_kind, idempotency_key)`，含 `request_hash`、`execution_correlation_id`、`business_owner_key`、`created_at`（TTL 30 天）。

## 4. 迁移矩阵

### 4.1 resource 域（workline_session_id → correlation_id）

| source_path | current_symbol_or_table | owner_domain | target_reference | phase | migration_action | risk |
| --- | --- | --- | --- | --- | --- | --- |
| `src/app/resource/models/resource.py:372` | `RackPlacement.workline_session_id` | resource | `correlation_id` | phase1 | replace | MEDIUM |
| `src/app/resource/models/resource.py:436` | `RackBinMount.workline_session_id` | resource | `correlation_id` | phase1 | replace | MEDIUM |
| `src/app/resource/models/resource.py:487` | `BinPlacement.workline_session_id` | resource | `correlation_id` | phase1 | replace | MEDIUM |
| `src/app/resource/models/resource.py:541` | `BinMaterialMount.workline_session_id` | resource | `correlation_id` | phase1 | replace | MEDIUM |
| `src/app/resource/models/resource.py:635` | `BinCellOccupancy.workline_session_id` | resource | `correlation_id` | phase1 | replace | MEDIUM |
| `src/app/resource/models/resource.py:705` | `RuntimeLocationEvent.workline_session_id` | resource | `correlation_id` | phase1 | replace | MEDIUM |
| `src/app/resource/models/resource.py:781` | `*Snapshot.source_session_id` | resource | `correlation_id`（快照归因） | phase1 | replace | MEDIUM |
| `src/app/resource/services/projection_service.py` | `workline_session_id` 参数与传参（多处） | resource | `correlation_id` | phase1 | replace | MEDIUM |
| `src/app/resource/services/projection_integrity_service.py:114-125` | `workline_session_id` 一致性校验 | resource | `correlation_id` 校验 | phase1 | replace | MEDIUM |
| `src/app/resource/services/relation_service.py` | `workline_session_id` 引用 | resource | `correlation_id` | phase1 | replace | MEDIUM |
| `src/app/resource/services/smt_rack_bin_scheduling_service.py` | `workline_session_id` 引用 | resource | `correlation_id` | phase1 | replace | MEDIUM |

**收敛策略**：resource 域全部 `workline_session_id` 收敛为 `correlation_id`（string，引用 `ExecutionCorrelation.correlation_id`）。Phase 1 CEO-007 ExecutionCorrelation 落地后，resource 投影写入与查询改用 correlation key。

### 4.2 handling 域（material_session_id / workline_session_id → correlation_id）

| source_path | current_symbol_or_table | owner_domain | target_reference | phase | migration_action | risk |
| --- | --- | --- | --- | --- | --- | --- |
| `src/app/handling/models/operation.py:107` | `HandlingOperation.material_session_id` | handling | `correlation_id`（无 session FK，主计划 §3.3） | phase1 | replace | MEDIUM |
| `src/app/handling/models/bin_transit_membership.py:83` | `BinTransitMembership.workline_session_id` | handling（legacy，主计划 §3.7 删除） | 删除（重建为 `ConveyorQueueMembership`） | phase2 | delete | HIGH |
| `src/app/handling/services/bin_transit_membership_service.py` | `workline_session_id` 引用 | handling（legacy） | 随模型删除 | phase2 | delete | HIGH |
| `src/app/handling/services/lifecycle_service.py` | `session_id` 引用 | handling | `correlation_id` | phase1 | replace | MEDIUM |
| `src/app/handling/services/operation_service.py` | `session_id` 引用 | handling | `correlation_id` | phase1 | replace | MEDIUM |

**收敛策略**：handling 域不持 session FK（主计划 §3.3），全部改为 `correlation_id`。旧 `BinTransitMembership` 在 Phase 2 删除重建为 `ConveyorQueueMembership`。

### 4.3 rack 域（legacy，迁入 resource/handling）

| source_path | current_symbol_or_table | owner_domain | target_reference | phase | migration_action | risk |
| --- | --- | --- | --- | --- | --- | --- |
| `src/app/rack/models/operation.py:75` | `RackOperation.material_session_id` | rack（legacy） | `correlation_id`；文件迁入 resource 或 handling | phase2 | replace + move | HIGH |
| `src/app/rack/models/operation.py:135` | `RackOperation.material_session_id`（第二处） | rack（legacy） | `correlation_id` | phase2 | replace + move | HIGH |
| `src/app/rack/repositories/operation_repository.py` | `session_id` 引用 | rack（legacy） | 随域迁移 | phase2 | move | MEDIUM |
| `src/app/rack/services/gateway.py` | `session_id` 引用 | rack（legacy） | 随域迁移 | phase2 | move | MEDIUM |
| `src/app/rack/services/operation_service.py` | `session_id` 引用 | rack（legacy） | 随域迁移 | phase2 | move | MEDIUM |
| `src/app/rack/services/task_lifecycle_service.py` | `session_id` 引用 | rack（legacy） | 随域迁移 | phase2 | move | MEDIUM |

> `rack` 不在主计划 §3.2 目标域结构中（target-state-contract.md §3.1）。rack 域是 legacy，整体在 Phase 2 迁入 resource 或 handling，session_id 随域迁移收敛为 correlation_id。具体迁入目标域由 P0-002 legacy matrix 的 `target_path` 确定。

### 4.4 device 域（session_id / session_id_int → correlation_id）

| source_path | current_symbol_or_table | owner_domain | target_reference | phase | migration_action | risk |
| --- | --- | --- | --- | --- | --- | --- |
| `src/app/device/models/command.py:263` | `DeviceCommand.session_id`（string） | device | `correlation_id`（string，引用 ExecutionCorrelation） | phase1 | replace | HIGH |
| `src/app/device/models/command.py:269` | `DeviceCommand.session_id_int`（int FK → workline_sessions） | device | 删除强 FK；保留 `correlation_id` 引用 | phase1 | delete（FK）+ replace（字段） | HIGH |

**外键环问题**（主计划 §4.2 未明确，本轮发现）：
- `DeviceCommand.session_id_int` → `workline_sessions.id`（`use_alter=True`）
- `WorklineSession.awaiting_command_id` → `device_commands.id`（`use_alter=True`，`src/app/workline/models/session.py:212`）

两端构成循环依赖，当前用 `use_alter=True` 规避 drop 顺序。目标态收敛为：
- `DeviceCommand` 持 `correlation_id`（无 session FK）
- `WorklineSession.awaiting_command_id` 改为 `awaiting_command_correlation_id`（引用 `DeviceCommand.command_code`，无 device FK）
- 移除两端 `use_alter=True` 外键环

此项是 HIGH 风险，Phase 1 CEO-010 DeviceCommand 合同实施时必须同步处理 `session.py` 的 awaiting 字段，进入 Phase 1 SPEC 风险表。

### 4.5 wms_integration 域

| source_path | current_symbol_or_table | owner_domain | target_reference | phase | migration_action | risk |
| --- | --- | --- | --- | --- | --- | --- |
| `src/app/wms_integration/services/transport_contract.py` | `workline_session_id` / `material_session_id` 引用 | wms_integration | `correlation_id` | phase1 | replace | MEDIUM |

**收敛策略**：wms_integration 是 ACL，只持 evidence + `correlation_id`，不持 session FK。`transport_contract` 的 session 引用收敛为 correlation key。

### 4.6 workline 域 runtime 相关（move 到 runtime/orchestration）

| source_path | current_symbol_or_table | owner_domain | target_reference | phase | migration_action | risk |
| --- | --- | --- | --- | --- | --- | --- |
| `src/app/workline/models/runtime.py`（13 处） | `WorklineSession` / `RuntimeInbox` / `RuntimeTimeline` / `RuntimeHold` 等 `session_id` | workline（当前）→ runtime | runtime 内部 `execution_session_id`（保留强 FK） | phase2 | move + keep-in-runtime | HIGH |
| `src/app/workline/models/timeline.py:118` | `RuntimeTimeline.session_id` | workline → runtime | runtime 内部 `execution_session_id` | phase2 | move + keep-in-runtime | MEDIUM |
| `src/app/workline/models/inbox.py:149` | `WorklineInbox.session_id` | workline → runtime | runtime 内部 `execution_session_id`（重建为 `RuntimeInbox`） | phase2 | move + rebuild | HIGH |
| `src/app/workline/models/runtime_hold.py:112,239` | `RuntimeHold.session_id` / `source_session_id` | workline → runtime | runtime 内部 `execution_session_id` | phase2 | move + keep-in-runtime | MEDIUM |
| `src/app/workline/models/runtime_hold_api.py`（3 处） | API schema `session_id` / `source_session_id` | workline → runtime | runtime 内部 `execution_session_id` | phase2 | move + keep-in-runtime | MEDIUM |
| `src/app/workline/models/object_transition_event.py:54` | `workline_session_id` | workline → runtime | `correlation_id`（event 跨域引用） | phase2 | move + replace | MEDIUM |
| `src/app/workline/models/smt_inbound_handoff.py:187,292` | `sorting_session_id` | workline → runtime | `correlation_id` | phase2 | move + replace | MEDIUM |
| `src/app/workline/models/bin_cell_reservation.py:33` | `session_id`（FK → workline_sessions） | workline → runtime | runtime 内部 `execution_session_id` 或 `correlation_id` | phase2 | move | MEDIUM |
| `src/app/workline/models/diagnostic.py:35` | `session_id`（FK → workline_sessions） | workline → runtime | runtime 内部 `execution_session_id` | phase2 | move + keep-in-runtime | LOW |
| `src/app/workline/models/operation.py:18,44,64` | `session_id` / `affected_session_ids` | workline | `correlation_id` / `affected_correlation_ids` | phase2 | replace | MEDIUM |
| `src/app/workline/models/integration_debug.py:49` | `session_id` | workline | `correlation_id` | phase2 | replace | LOW |
| `src/app/workline/repositories/debug_data_cleanup_repository.py` | `session_id` 引用 | workline | `correlation_id` | phase2 | replace | LOW |
| `src/app/workline/repositories/object_transition_event_repository.py` | `session_id` 引用 | workline → runtime | 随 event 迁移 | phase2 | move | LOW |
| `src/app/workline/repositories/sandbox_cleanup_repository.py` | `session_id` 引用 | workline | `correlation_id` | phase2 | replace | LOW |
| `src/app/workline/services/object_transition_event_service.py` | `session_id` 引用 | workline → runtime | `correlation_id` | phase2 | move + replace | MEDIUM |
| `src/app/workline/services/runtime_reconciliation_service.py` | `session_id` 引用 | workline → runtime | runtime 内部 `execution_session_id` | phase2 | move + keep-in-runtime | MEDIUM |
| `src/app/workline/services/smt_inbound_handoff_service.py` | `session_id` 引用 | workline → runtime | `correlation_id` | phase2 | move + replace | MEDIUM |

**收敛策略**：`workline/models/runtime.py`、`timeline.py`、`inbox.py`、`runtime_hold*.py` 是 runtime owner 状态源，整体 `move` 到 `src/app/runtime/orchestration/models/`，内部 `session_id` 保留为 `execution_session_id`（runtime 域内强 FK，主计划 §3.3 允许）。跨域 event（`object_transition_event`、`smt_inbound_handoff`）的 session 引用改为 `correlation_id`。

### 4.7 material 域（current_session_id → current_session_correlation_id）

| source_path | current_symbol_or_table | owner_domain | target_reference | phase | migration_action | risk |
| --- | --- | --- | --- | --- | --- | --- |
| `src/app/workline/models/material_unit.py:63` | `MaterialUnit.current_session_id` | material（WES 自有根实体，主计划 §3.4） | `current_session_correlation_id`（主计划 §4.1） | phase1 | replace + move（material 域新建） | HIGH |

**收敛策略**：material_units 是 WES 自有根实体（target-state-contract.md §4），`current_session_id` 无 FK（已遵循辅助追溯规范），改为 `current_session_correlation_id` 引用 `ExecutionCorrelation.correlation_id`。文件迁入 `src/app/material/`（Phase 1 新建域）。

### 4.8 sys 域（outbox → RuntimeIntentLog）

| source_path | current_symbol_or_table | owner_domain | target_reference | phase | migration_action | risk |
| --- | --- | --- | --- | --- | --- | --- |
| `src/app/sys/models/outbox.py:75,83` | `SystemOutbox.session_id`（FK → workline_sessions） | sys（legacy effect ledger） | runtime 内部 `execution_session_id`（重建为 `RuntimeIntentLog`） | phase2 | move + rebuild | HIGH |
| `src/app/sys/models/outbox.py:134` | `blocked_by_reconciliation_session_id` | sys | `correlation_id`（reconciliation 引用） | phase2 | replace | MEDIUM |

**收敛策略**：`SystemOutbox` 是 effect ledger 前身，目标态重建为 `RuntimeIntentLog`（主计划 §9.2），迁入 runtime/orchestration 域，内部 session 改 `execution_session_id`。`blocked_by_reconciliation_session_id` 改 correlation 引用。

## 5. 风险与验收

### 5.1 高风险项（须进入 Phase 1/2 SPEC 风险表）

| 风险项 | phase | 说明 |
| --- | --- | --- |
| device `session_id_int` ↔ session `awaiting_command_id` 外键环 | phase1 | Phase 1 CEO-010 DeviceCommand 实施时必须同步处理 `session.py` awaiting 字段，移除 `use_alter=True` 环 |
| `workline/models/inbox.py` 重建为 `RuntimeInbox` | phase2 | 旧 `WorklineInbox` 只作 characterization 来源（SPEC C5），不反向决定状态命名 |
| `sys/models/outbox.py` 重建为 `RuntimeIntentLog` | phase2 | effect ledger 迁移须保证崩溃重放不丢 intent |
| `rack` 域整体迁移目标域 | phase2 | rack 不在目标域结构，须 P0-002 确定迁入 resource 或 handling |
| `material_unit.py` 迁入新建 material 域 | phase1 | WES 自有根实体迁移，current_session_id 改名 |

### 5.2 验收要求（SPEC P0-004）

1. ✅ 跨域 FK 必须收敛为 correlation key（§4.1-4.5、4.7-4.8 全部 `correlation_id`）
2. ✅ runtime/orchestration 内部可以保留 `execution_session_id`，但跨域只能通过 `ExecutionCorrelation`（§4.6 `keep-in-runtime` 项）
3. ✅ 高风险迁移项已进入 Phase 1 或 Phase 2 SPEC 的风险表（§5.1）

## 6. 索引汇总

| 域 | 文件数 | 主策略 |
| --- | ---: | --- |
| resource | 4 文件（11 字段） | replace → correlation_id |
| handling | 5 文件 | replace / delete（BinTransitMembership） |
| rack（legacy） | 6 文件 | replace + move |
| device | 1 文件（2 字段） | replace + delete FK（外键环） |
| wms_integration | 1 文件 | replace → correlation_id |
| workline → runtime | 16 文件 | move + keep-in-runtime / replace |
| material | 1 文件 | replace + move（新建域） |
| sys（outbox） | 1 文件 | move + rebuild → RuntimeIntentLog |
