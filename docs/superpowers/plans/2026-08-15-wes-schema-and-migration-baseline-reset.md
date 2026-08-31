# Phase 11 数据库 Schema 与迁移基线重置实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在最终模型稳定且旧生产路径归零后，将 Task 1 实测冻结的全部未发布历史 Alembic revision 收敛为一个可从空 PostgreSQL/TimescaleDB 建立系统的初始基线。

**Architecture:** 先以测试锁定最终 metadata 和 PostgreSQL 专有对象，再在隔离空库上由 Alembic generator 生成随机 revision，人工补齐 autogenerate 无法表达的 schema、扩展、函数、触发器、部分索引和 TimescaleDB 对象，最后删除旧 revision 及只验证旧 revision 的测试。整个切换不转换旧数据、不提供 downgrade，也不保留旧 migration 桥接。

**Tech Stack:** Python 3.13、SQLModel/SQLAlchemy、Alembic、PostgreSQL、TimescaleDB、Docker Compose、Pytest、GitNexus、HEAVY selector。

**Current status:** `TASKS 1–5 COMPLETE — TASK 6 LOCAL COMMIT COMPLETE — NOT PUSHED / NOT MERGED`。Phase 10 已通过 #187 合入
`develop@97e6887a83bce1633c09462c5f9fac4f74d2730c`；此前 `834fe59e` candidate 已部署联调并完成 cutover，但 merge commit
未证明重新部署，且供应商、设备物理与业务验收均未完成。Task 1B 已在用户单独授权后完成 old-chain catalog、byte-identical、
cleanup absence 与 final roster 冻结：22 个 schema-deferred identity 终裁为 `21 FINAL_DELETE_AFTER_SUCCESSOR + 1 RETAIN`，另有
两张 catalog-only 历史/orphan 表终裁 `FINAL_DELETE -> NONE`。Task 2 已把 raw old-chain catalog/full-equality 矛盾收敛为 immutable
raw characterization + explicit disposition + strict final manifest；Task 3 的唯一初始 revision `f9c7c2e5f501` 已通过双 fresh-DB
与 `alembic check`。Task 4/5 的 successor、selector、拓扑和 unstaged HEAVY（`324 passed, 0 skipped`）已闭合；
Task 6 最终 staged 门禁、独立 Review 与本地原子 Commit 已完成，尚未 Push/PR/Merge/Deploy。

**Execution authorization:** 用户已明确授权 Phase 11 全部 Tasks 连续实施，不再逐 Task 请求 stateful/DDL/destructive 授权。
该授权不把 Push、PR、Merge 或 Deploy 混入本地 Task 执行；本计划先完成本地实现、验证与获批 Commit，并停在远端集成边界。

## Global Constraints

- 本计划属于 Phase 11；Phase 10 零旧生产路径已随 #187 合入，但 merge 与 deployment 是两个边界。Task 1 未闭合前，任何人不得删除现有 revision chain。
- Task 1B characterization 已闭合；Task 2 前必须先修订 raw catalog/final schema 的矛盾合同，并保留
  `wes_runtime.workline_runtime_status_projections` 的 current `rough_sorter`/reset owner。未完成前不得删除现有 revision chain。
- 退役插件活动残留收敛必须完成，活动模型和当前 head 不得再包含退役插件字段；Phase 11 启动时以项目外归档
  `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-15-wes-retired-plugin-residual-convergence.md` 为完成证据，不得要求已归档的过程计划继续留在项目内。
- Phase 9 已实现的最小执行内核、当前 WMS/RCS Adapter、设备统一接口及 `rough_sorter` 所需持久模型必须稳定；不得为 Phase 12/13 插件预建表、字段或 operation，否则停止，不生成临时基线。
- 不迁移旧数据，不保留兼容 schema、回填脚本、桥接表或 downgrade；开发/测试数据库统一清理后重建。
- 新初始 revision 必须由 Alembic generator 生成随机 revision ID，`down_revision = None`，不得手写 revision ID。
- 只在名称明确的隔离数据库中执行创建/删除；禁止对默认库、生产库或无法确认的连接执行 destructive SQL。
- 旧 revision 测试只有在最终基线 successor 先通过后才能删除；删除说明必须标注 successor 或 `NONE`。
- `docs/hardware/` 不参与数据库基线清理。

## Phase 10 → Phase 11 入口交接（2026-08-30）

Phase 10 已在联调环境完成 target-only cutover。现场 source commit 为
`834fe59e0c44c943487eedb6ed41af1c519df7ad`，backend image digest 为
`sha256:018c1cd82276b876a64ffbdaa9379ceca15a091fc1b1b265960793d732d8e00d`，新建 `wes_db` 的唯一 Alembic head 为
`dd35f04b258f`。3 个 worker 注册的 legacy task、Beat 中 legacy schedule task、旧 WMS profile/effect env/mount 和旧 backend
container 均为 0；详细现场证据位于联调服务器
`/srv/wes/app/releases/phase10-task7-20260830T054232Z-834fe59e/`。

本次 handoff 只传递 `docs/architecture/legacy-cleanup-matrix.csv` 中以下 22 个 `schema-deferred` model/table identity：

- `wes_biz`: `workline_diagnostics`、`workline_bin_cell_reservations`、`workline_dispatch_attempts`、`ng_return_items`、
  `runtime_holds`、`system_outbox`、`wms_circuit_breaker_state`、`wms_call_evidence`。
- `wes_runtime`: `bin_route_instances`、`conveyor_queue_memberships`、`idempotency_keys`、`material_flow_owners`、
  `execution_correlations`、`execution_sessions`、`execution_work_items`、`reconciliation_cases`、`runtime_holds`、`runtime_inbox`、
  `runtime_intent_logs`、`runtime_timelines`、`wms_rack_demands`、`workline_runtime_status_projections`。

这些 identity 的 model definition、`migrations/env.py`、现有 revisions 与 schema-only tests 是 Task 1 重新冻结 FK、index、constraint
和 PostgreSQL/TimescaleDB 专有对象的输入。该列表不传递 DDL、revision ID、删除路径或基线生成方案，也不授权跳过 Task 1 的
独立实施前评审。Phase 10 已通过 #187 合入 `develop@97e6887a`；此前 candidate 的联调部署不证明 merge commit 已重新部署，
也不证明供应商、设备物理或业务验收。Phase 11 Tasks 1–5 已在本实施分支闭合，Task 6 最终 staged 门禁进行中，尚未合入。

---

### Task 1: 验证 Phase 11 入口门禁并冻结对象清单

**Task 1 freeze（2026-08-30）:**

- Git：`HEAD = 97e6887a83bce1633c09462c5f9fac4f74d2730c`；冻结前 tracked worktree clean；Alembic 单一 head
  `dd35f04b258f`。
- revision：128 个 tracked Python revision，全部分类为 Task 3 的 `DELETE_AFTER_SUCCESSOR`；精确路径清单见本 Task 下方，
  清单 SHA-256 为 `bd256cf50b7ce34ce840210a9486cc536893df992665f70fa9b7beac8d54ae4f`。
- HEAVY：128 个 revision 中当前仅 26 个命中 mapping，102 个未映射；Task 3 必须把 128 个精确 deletion tombstone 与新初始
  revision mapping 原子加入，不得使用宽 glob，含删除的变更合入前不得清理 tombstone。
- Phase 10 residual scan：使用 `LC_ALL=C rg --sort path ...` 冻结 208 行、40 个源文件，raw stdout SHA-256
  `3eadb6bb7385ffdc65bec3558728a53afce337ab10e831d6af5df8b0cb94a628`；逐命中 ledger 的 208 个 canonical row
  SHA-256 最终为 `64ea672ba1910e08fd07e167406c508998029b689b197b56b61842bd0c0f5341`，每行均含 classification、owner/
  `NONE` 与 evidence；current-index 已确认 `session_hold_mutation_service.py` 的 1 行 owner 为 `SessionHoldHandler`，并确认
  `runtime_hold_api.py` 的 19 行无外部 consumer、终裁为 `NONE`。该模块唯一真实 consumer 只 import 不在 19 行内的
  `FailedCommandEvidence`。
- current-index：worktree `97e6887a` 的 GitNexus CLI evidence 已冻结到
  `.superpowers/sdd/2026-08-15-wes-schema-and-migration-baseline-reset/task-1-gitnexus-impact.md`，SHA-256
  `eaa6a8bee9df8d1532ebb42d4c697fd1ca667529c186d6306677934279c87eae`；22 identity impact、metadata helper 的 9 个测试
  consumer、`target_metadata` 的 `UNKNOWN`/源码补证、flow truncation 和 partial detect-changes 边界均已记录。该证据不终裁
  22 identity 的 final decision，也不替代 catalog、final metadata/`rough_sorter` roster 或独立 review；其中 `ExecutionSession` 为 HIGH，要求
  22 identity 原子终裁，不能因 wrapper 通过而提前进入 Task 2。
- proprietary SQL scan：使用 `LC_ALL=C rg --sort path ...` 冻结 321 行、60 个文件，raw stdout SHA-256
  `e27ce51c40be58a0893db52e0894ee72959a09c2f24d0f37d3d8f463085e95f0`；live catalog 终裁 extensions 为
  `plpgsql 1.0`、`timescaledb 2.23.1`，user routine/trigger/view/hypertable/continuous aggregate/job 均为 `0`。
- old-chain canonical catalog 已冻结：PostgreSQL `17.6`、75 表，SHA-256
  `77214740a6fd48c113c043aeb32887209681c4fd213833e7e9ee391f317117c9`；第二次查询 byte-identical。Current metadata 为 73 表，
  多出的 `wes_biz.resource_c0_session_cleanup_report` 与 `wes_runtime.device_runtime_projections` 均无 current owner，终裁
  `FINAL_DELETE -> NONE`。raw artifact 保持不可变，不允许通过过滤 artifact 伪造最终 schema。
- 其它待归档迁移过程文档：`NONE`。既有退役插件残余计划的权威归档位于
  `/Users/kaizhou/codeDev/archive_docs/wes_backend/docs/superpowers/plans/2026-08-15-wes-retired-plugin-residual-convergence.md`；
  worktree 相对路径不得误解析到 `wes_backend-worktrees/archive_docs/`。
- 完整逐路径 inventory 与判定证据保存在忽略的 SDD 工作区
  `.superpowers/sdd/2026-08-15-wes-schema-and-migration-baseline-reset/task-1-inventory.md`，不是第二套永久 registry；Phase 10
  disposition 的唯一永久真源仍是 `scripts/generate_legacy_matrix.py` 与 `docs/architecture/legacy-cleanup-matrix.csv`。
- 208 行逐命中 ledger 的精确路径为
  `.superpowers/sdd/2026-08-15-wes-schema-and-migration-baseline-reset/task-1-residual-hit-ledger.tsv`；它是当前 Task 1 审计附件，
  不是永久 disposition registry。

**Files:**

- Inspect: `migrations/versions/*.py`
- Inspect: `migrations/env.py`
- Inspect: `src/**/models/*.py` 及所有注册到 `SQLModel.metadata` 的模型
- Inspect: `tests/{database,migrations,integration,deployment}/`
- Inspect: `scripts/`、`tests/support/runtime_inbox_postgresql.py` 中已有的隔离数据库安全原语
- Inspect: `docs/superpowers/plans/*.md` 和其它仍引用旧 migration 过程的当前文档

**Interfaces:**

- Consumes: Phase 10 零旧路径证据、最终 SQLModel metadata、当前 PostgreSQL head。
- Produces: 可审计的 revision 清单，以及独立评审确认的 schema-qualified 完整数据库 manifest；该 manifest 不从 `migrations/env.py` 或当前 `SQLModel.metadata` 反向生成。

#### 22 个 schema-deferred identity 的 final lifecycle

Live catalog、final metadata/`rough_sorter` roster 与独立只读 review 已终裁：除
`wes_runtime.workline_runtime_status_projections` 为 `RETAIN` 外，其余 21 项均为 `FINAL_DELETE_AFTER_SUCCESSOR`。retained identity
当前由 rough_sorter seed 与 reset tooling 直接写入，且不在 21 表 FK 闭包内；没有 22 之外的 inbound FK 指向该闭包。successor
未建立前不得删除 source、从 metadata 移除或改变唯一 registry；Task 2 继续 blocked。

1. Task 3 只对 21 个 `FINAL_DELETE_AFTER_SUCCESSOR` identity 在 schema successor 绿灯后删除 source model 文件，并从
   `migrations/env.py` 移除 import；同文件两个 identity 只删除一次文件。retained projection 保持注册。
2. `scripts/generate_legacy_matrix.py` 与生成的 `docs/architecture/legacy-cleanup-matrix.csv` 保留唯一审计 owner；只把 21 个 delete
   identity 从 `schema-deferred` 改为 `delete`、target `NONE`，retained projection 保持 current disposition，不创建第二个 registry。
3. `tests/architecture/test_cleanup_matrix_guardrail.py` 只对 21 个 delete identity 停止要求 table 存在，并证明其 `delete -> NONE`、
   source 缺席；retained projection 必须继续证明 source、metadata registration 与最终 schema presence。
4. 21 个 delete identity 的 final schema lifecycle 为 `ABSENT`；retained projection 为 `PRESENT`。每个 delete source 路径与每个旧
   revision 路径在 Task 3 增加指向最终 PostgreSQL successor 的精确 HEAVY tombstone，合入前保留，合入后才按独立授权清理。

| Source model | Schema identity |
| --- | --- |
| `src/app/runtime/orchestration/models/diagnostic.py::WorklineDiagnostic` | `wes_biz.workline_diagnostics` |
| `src/app/runtime/orchestration/bin_route_instance.py::BinRouteInstance` | `wes_runtime.bin_route_instances` |
| `src/app/runtime/orchestration/conveyor_queue_membership.py::ConveyorQueueMembership` | `wes_runtime.conveyor_queue_memberships` |
| `src/app/runtime/orchestration/idempotency_key.py::IdempotencyKey` | `wes_runtime.idempotency_keys` |
| `src/app/runtime/orchestration/material_flow_owner.py::MaterialFlowOwner` | `wes_runtime.material_flow_owners` |
| `src/app/runtime/orchestration/execution_correlation.py::ExecutionCorrelation` | `wes_runtime.execution_correlations` |
| `src/app/runtime/orchestration/execution_session.py::ExecutionSession` | `wes_runtime.execution_sessions` |
| `src/app/runtime/orchestration/execution_work_item.py::ExecutionWorkItem` | `wes_runtime.execution_work_items` |
| `src/app/runtime/orchestration/models/bin_cell_reservation.py::WorklineBinCellReservation` | `wes_biz.workline_bin_cell_reservations` |
| `src/app/runtime/orchestration/models/dispatch_attempt.py::WorklineDispatchAttempt` | `wes_biz.workline_dispatch_attempts` |
| `src/app/runtime/orchestration/models/runtime_hold.py::NgReturnItem` | `wes_biz.ng_return_items` |
| `src/app/runtime/orchestration/models/runtime_hold.py::RuntimeHold` | `wes_biz.runtime_holds` |
| `src/app/runtime/orchestration/reconciliation_case.py::ReconciliationCase` | `wes_runtime.reconciliation_cases` |
| `src/app/runtime/orchestration/runtime_hold.py::RuntimeHold` | `wes_runtime.runtime_holds` |
| `src/app/runtime/orchestration/runtime_inbox.py::RuntimeInbox` | `wes_runtime.runtime_inbox` |
| `src/app/runtime/orchestration/runtime_intent_log.py::RuntimeIntentLog` | `wes_runtime.runtime_intent_logs` |
| `src/app/runtime/orchestration/runtime_timeline.py::RuntimeTimeline` | `wes_runtime.runtime_timelines` |
| `src/app/runtime/orchestration/wms_rack_demand.py::WmsRackDemand` | `wes_runtime.wms_rack_demands` |
| `src/app/runtime/orchestration/workline_runtime_status_projection.py::WorklineRuntimeStatusProjection` | `wes_runtime.workline_runtime_status_projections` |
| `src/app/sys/models/outbox.py::SystemOutbox` | `wes_biz.system_outbox` |
| `src/app/wms_integration/models/circuit_breaker.py::WmsCircuitBreakerState` | `wes_biz.wms_circuit_breaker_state` |
| `src/app/wms_integration/models/evidence.py::WmsCallEvidence` | `wes_biz.wms_call_evidence` |

- [x] **Step 1: 检查入口条件**

  Run: `LC_ALL=C rg --sort path -n "RuntimeIntent|RuntimeHold|Manifest|Capability|Effect|PLUGIN_|plugin_key|plugin_contract_version" src --glob '*.py'`

  Expected: 每个命中都能逐条关联 Phase 10 已评审的最终 owner 或 `NONE` 处置，未分类命中数为 `0`；不得用人工浏览后声称“看起来只有允许对象”。出现无 owner 旧路径立即停止 Phase 11。

  当前结论：208 个 hit 已逐行分类。current-index graph 加 exact sorted `rg` 已确认
  `session_hold_mutation_service.py` 的 1 行为 `RETAIN_CURRENT_OWNER`、owner `SessionHoldHandler`；`runtime_hold_api.py` 的 19 行
  RuntimeHold DTO 仅出现指向 `trace_response_builder.py` 的 module relation，而该 consumer 只 import 不在这 19 行内的
  `FailedCommandEvidence`。七个 DTO symbol 在 `src/tests/workline_plugins/deployment` 外部 consumer 均为 0，19 行已终裁
  `NONE`；Step 1 通过。

- [x] **Step 2: 冻结 Git 与 Alembic 基线**

  Run: `git rev-parse HEAD && git status --short && uv run alembic heads && LC_ALL=C rg --files --sort path migrations/versions -g '*.py'`

  Expected: 单一 head；记录精确提交和 revision 文件清单。revision 数量和路径只以 Task 1 当前命令实测冻结清单为准，不沿用规划编写时的快照数字。

  冻结结果：以下 128/128 路径全部为 `DELETE_AFTER_SUCCESSOR`，不得提前删除或按目录猜测：

  ```text
  migrations/versions/20260316_2040_be330a8cda0a_initial_migration_all_models_with_.py
  migrations/versions/20260317_0919_4884d68cbe09_add_session_id_and_workline_id_to_.py
  migrations/versions/20260317_0925_e86574a11839_fix_audit_log_status_enum_type.py
  migrations/versions/20260317_0930_8f8180e751c3_create_workline_session_timeline_inbox_.py
  migrations/versions/20260317_1608_ab8b14fe397c_add_callback_log_table.py
  migrations/versions/20260325_1540_4d2d6f0d9d8a_add_workline_plugin_fields.py
  migrations/versions/20260325_1840_9b1a6a4f2d3e_add_command_result_inbox_kind.py
  migrations/versions/20260328_1235_c6f8e1a2b4d9_fix_workline_inbox_kind_constraint_name.py
  migrations/versions/20260329_1100_7a2c4d6e8f10_add_workline_contract_snapshots.py
  migrations/versions/20260330_1030_b9e1c2d3f4a5_drop_device_event_logs.py
  migrations/versions/20260331_1600_5d26a11a4cd9_add_has_children_field_to_tree_mixin.py
  migrations/versions/20260331_1607_6f84ab2bf6e6_add_parent_sort_index_to_menus.py
  migrations/versions/20260405_1353_60cd0040b702_extend_task_type_enum_for_conveyor_and_.py
  migrations/versions/20260405_2022_ee46b1b4e252_drop_deprecated_device_fields.py
  migrations/versions/20260408_1700_f4cd014e337e_initialize_has_children_for_existing_.py
  migrations/versions/20260413_1815_3b7c9d2e4f11_extend_workline_inbox_retry_status.py
  migrations/versions/20260413_2130_a1b2c3d4e5f6_add_log_center_fields.py
  migrations/versions/20260416_1505_c7d8e9f0a1b2_add_runtime_governance_fields_to_device_and_workline.py
  migrations/versions/20260416_1845_d1e2f3a4b5c6_add_ingress_fields_to_callback_logs.py
  migrations/versions/20260417_1115_e2f4a6b8c9d0_add_ingress_count_to_workline_sessions.py
  migrations/versions/20260417_1145_f3a5b7c9d1e2_add_last_ingress_fields_to_workline_sessions.py
  migrations/versions/20260417_1730_a7c4d5e6f7a8_drop_dead_workline_fields.py
  migrations/versions/20260418_0030_c66ad6e468a8_drop_trace_soft_delete_and_version_.py
  migrations/versions/20260425_1100_d4e5f6a7b8c9_add_workline_run_mode.py
  migrations/versions/20260425_1200_e5f6a7b8c9d0_relax_device_command_task_type.py
  migrations/versions/20260425_1500_f6a7b8c9d0e1_add_device_maintenance_status.py
  migrations/versions/20260425_1515_f7a8b9c0d1e2_repair_failed_outbox_device_commands.py
  migrations/versions/20260427_1200_a8c9d0e1f2a3_workline_record_diagnostics.py
  migrations/versions/20260505_1219_b9d0e1f2a3b4_drop_workline_capacity_sort_order.py
  migrations/versions/20260505_2330_c0d1e2f3a4b5_drop_workline_owner_support.py
  migrations/versions/20260506_1535_9b7c6d5e4f3a_add_workline_safety_incidents.py
  migrations/versions/20260507_1015_a1b2c3d4e5f7_govern_device_runtime_state.py
  migrations/versions/20260508_1558_2555f6c1b08d_add_runtime_reconciliation_fields.py
  migrations/versions/20260508_2117_49e5ef9fa864_rename_workline_plugin_state.py
  migrations/versions/20260509_1737_608d8cdb5aa0_add_runtime_hold.py
  migrations/versions/20260511_0201_5a43d0d64ce1_allow_workflow_ng_return_items.py
  migrations/versions/20260511_1039_7782860238c2_add_open_session_business_key_guard.py
  migrations/versions/20260511_1145_3a31e15009d7_rename_callback_log_subject_code.py
  migrations/versions/20260512_1255_78ff506d4d9a_drop_legacy_plugin_state_fields.py
  migrations/versions/20260516_1750_13140cee49a7_add_resource_runtime_base.py
  migrations/versions/20260516_1839_7541d77ecf3b_add_resource_fact_projections.py
  migrations/versions/20260516_1858_1fdeed75fd3a_add_wms_writeback_evidence.py
  migrations/versions/20260516_2032_e9ec8588062f_add_rack_release_exchange_snapshots.py
  migrations/versions/20260517_1658_2f424528ea71_drop_resource_record_enterprise_columns.py
  migrations/versions/20260518_1646_5f4e9323a65a_slim_resource_model_mixins.py
  migrations/versions/20260519_0004_bbaa8662d7fe_add_workline_rack_positions_and_bin_.py
  migrations/versions/20260519_1417_286ddc5bc27d_add_bin_cell_occupancy_aggregate.py
  migrations/versions/20260519_1431_b4685be483de_add_material_mount_stack_position.py
  migrations/versions/20260520_1453_083e85d1bf93_add_workline_rack_tasks.py
  migrations/versions/20260521_1513_97dbf218ed9f_add_rack_operation_task_metadata.py
  migrations/versions/20260522_0024_c0ff648f8718_add_rack_task_source_claim_guard.py
  migrations/versions/20260522_1449_745068e173c2_add_handling_core.py
  migrations/versions/20260525_1239_3cf0dc588be9_system_outbox_and_rack_operation_domain.py
  migrations/versions/20260526_1544_c5d469c98d89_set_handling_full_box_completion_policy.py
  migrations/versions/20260527_0025_793f8773f841_add_wms_call_evidence.py
  migrations/versions/20260527_0105_07be7a97f4a6_add_wms_circuit_breaker_state.py
  migrations/versions/20260527_1434_a6c2c77adabd_add_workline_inbox_hot_queue_indexes.py
  migrations/versions/20260528_1432_4d08cdff2766_drop_demo_module_tables.py
  migrations/versions/20260529_1053_c1ea657cb2d7_workline_activation_state_default.py
  migrations/versions/20260530_0144_86b2d22f0103_repair_system_outbox_deployed_schema_.py
  migrations/versions/20260602_0005_ec493e8e53a1_add_workline_stopped_start_admission.py
  migrations/versions/20260602_0730_1bda271cfeb5_numeric_bin_cell_depth.py
  migrations/versions/20260604_0133_fa9a235a48fd_add_system_outbox_resource_wait_metadata.py
  migrations/versions/20260609_2208_2937b05e1b1c_add_workline_inbox_claim_bucket_key.py
  migrations/versions/20260610_0959_e563116f56f1_add_workline_inbox_processing_hot_queue_.py
  migrations/versions/20260611_0731_fb02178f9772_add_smt_inbound_handoff.py
  migrations/versions/20260621_1018_e680301d30c8_add_material_units_reel_root_entity.py
  migrations/versions/20260622_1052_84c693e1bac9_sync_workline_plugin_contract_versions.py
  migrations/versions/20260623_0224_194dcb39daf4_add_object_transition_events.py
  migrations/versions/20260623_0237_9b660037b4bb_resource_c0_session_fk_integrity.py
  migrations/versions/20260623_0406_8a1b17cba3db_handling_bin_transit_membership.py
  migrations/versions/20260626_1140_c0bccb9de6f3_add_execution_session_correlation.py
  migrations/versions/20260626_1200_0e9de1e6c7e3_phase1_device_fk_ring_dissolve.py
  migrations/versions/20260626_1719_f04718a3f04f_add_remaining_runtime_orchestration_.py
  migrations/versions/20260702_0953_629d8e64eb13_add_wms_evidence_gin_indexes.py
  migrations/versions/20260702_1913_f88092809f4b_add_device_runtime_projection.py
  migrations/versions/20260704_0158_de288342b42d_phase4_runtime_location_and_reservation.py
  migrations/versions/20260708_0106_f0851c5bcfdb_drop_legacy_workline_runtime_residuals.py
  migrations/versions/20260711_1815_b8a28e1bfec8_extend_runtime_inbox.py
  migrations/versions/20260711_1819_ec426c628516_retire_workline_inbox.py
  migrations/versions/20260714_1103_e0d58415afc9_create_runtime_inbox_indexes_.py
  migrations/versions/20260717_0739_fa15ba0aef65_add_workline_plugin_runtime_binding.py
  migrations/versions/20260718_0117_a92c1f8ee28b_add_workline_session_optimistic_version.py
  migrations/versions/20260722_0125_8db8cbba582c_add_query_shadow_readiness.py
  migrations/versions/20260722_1053_8fb4b595a85c_converge_effect_state_contract.py
  migrations/versions/20260722_1220_df58f4068f02_freeze_external_http_canonical_payload_.py
  migrations/versions/20260722_1312_8de7cb4de434_record_typed_external_http_transport_.py
  migrations/versions/20260723_0027_c325aab03400_add_effect_reconciliation_cases.py
  migrations/versions/20260723_0108_2c1407a3606e_add_system_outbox_dispatch_concurrency.py
  migrations/versions/20260723_0939_7824db01402d_freeze_external_http_delivery_binding.py
  migrations/versions/20260723_1445_5d251fdbb1e8_drop_legacy_port_method_snapshots.py
  migrations/versions/20260723_1933_bba1942e9ea8_add_provider_identity_to_wms_query_.py
  migrations/versions/20260724_2200_6ea20f0c0d22_add_system_outbox_idempotency_key.py
  migrations/versions/20260724_2315_65e212c90737_add_wms_effect_status_polling_state.py
  migrations/versions/20260725_0208_deb3e0c39e98_remove_query_shadow_readiness.py
  migrations/versions/20260727_1742_be496b91f3e3_enforce_runtime_plugin_binding.py
  migrations/versions/20260729_1826_36aa187238cc_allow_external_http_none_auth.py
  migrations/versions/20260730_0340_f9ffbef8992a_新增_wms_履约领域关系.py
  migrations/versions/20260730_0811_8612c6926f4c_add_e12_entry_membership_constraints.py
  migrations/versions/20260730_0859_9cc0848560c6_扩展_e12_unknown_入口位置占用.py
  migrations/versions/20260730_1159_f557c7b749b1_allow_runtime_domain_effect_without_.py
  migrations/versions/20260730_1314_46f11dd0a874_drop_legacy_rack_handling_operation_.py
  migrations/versions/20260730_1417_7fadfb5469ee_add_wms_business_event_idempotency_index.py
  migrations/versions/20260809_2029_a8d9b9eba49b_新增_agv_ctu_通用搬运聚合.py
  migrations/versions/20260810_2214_de392f5ff5d0_remove_workline_plugin_execution_schema.py
  migrations/versions/20260813_1016_a08d72f135d2_rebuild_device_command_ecs_lifecycle.py
  migrations/versions/20260814_0516_ce53af214081_对齐_wms_搬运最终结果来源版本.py
  migrations/versions/20260815_0427_fa685260524f_remove_retired_plugin_residuals.py
  migrations/versions/20260816_0229_ef9495ba331d_对齐_transport_回调收据与冻结请求体.py
  migrations/versions/20260816_2248_48c71f31cafb_收敛执行对象.py
  migrations/versions/20260817_1325_72ecc4fd560f_add_phase8_decision_processing.py
  migrations/versions/20260817_2308_5695afa99545_闭合粗分持久触发.py
  migrations/versions/20260818_0131_ec18b2a79400_闭合粗分持久触发.py
  migrations/versions/20260819_0804_53e560430c1a_为运行代际冻结配置快照.py
  migrations/versions/20260819_1202_a05b2676f681_删除旧start准入字段.py
  migrations/versions/20260819_1359_0a6378b66e1a_增加设备派发端点.py
  migrations/versions/20260820_1808_db0859fd3259_align_tree_parent_ids.py
  migrations/versions/20260823_0007_1000c501e52a_支持设备指令手动联调.py
  migrations/versions/20260823_1616_11013119b97d_audit_manual_device_command.py
  migrations/versions/20260824_1152_fe7280088174_修复_runtimeinbox_毫秒字段类型.py
  migrations/versions/20260825_0021_f11b613771fa_对齐设备结果证据外键类型.py
  migrations/versions/20260825_0350_d68e6be4006e_支持事件联调自动指令.py
  migrations/versions/20260826_0109_9624cc34fa93_drop_menu_persistence.py
  migrations/versions/20260827_0433_71eeea05c864_记录_event_命令阻塞因果.py
  migrations/versions/20260829_0800_7bdca6f754ee_记录_transport_位置投影来源任务.py
  migrations/versions/20260829_1129_baf328359533_add_execution_authority_projections.py
  migrations/versions/20260829_1159_273898a3f09b_add_material_admission_fifo.py
  migrations/versions/20260829_1217_dd35f04b258f_add_safety_evidence_authority.py
  ```

  当前 26 个已映射 revision 的精确集合为：

  ```text
  20260626_1719_f04718a3f04f_add_remaining_runtime_orchestration_.py
  20260809_2029_a8d9b9eba49b_新增_agv_ctu_通用搬运聚合.py
  20260810_2214_de392f5ff5d0_remove_workline_plugin_execution_schema.py
  20260813_1016_a08d72f135d2_rebuild_device_command_ecs_lifecycle.py
  20260814_0516_ce53af214081_对齐_wms_搬运最终结果来源版本.py
  20260815_0427_fa685260524f_remove_retired_plugin_residuals.py
  20260816_0229_ef9495ba331d_对齐_transport_回调收据与冻结请求体.py
  20260816_2248_48c71f31cafb_收敛执行对象.py
  20260817_1325_72ecc4fd560f_add_phase8_decision_processing.py
  20260817_2308_5695afa99545_闭合粗分持久触发.py
  20260818_0131_ec18b2a79400_闭合粗分持久触发.py
  20260819_0804_53e560430c1a_为运行代际冻结配置快照.py
  20260819_1202_a05b2676f681_删除旧start准入字段.py
  20260819_1359_0a6378b66e1a_增加设备派发端点.py
  20260820_1808_db0859fd3259_align_tree_parent_ids.py
  20260823_0007_1000c501e52a_支持设备指令手动联调.py
  20260823_1616_11013119b97d_audit_manual_device_command.py
  20260824_1152_fe7280088174_修复_runtimeinbox_毫秒字段类型.py
  20260825_0021_f11b613771fa_对齐设备结果证据外键类型.py
  20260825_0350_d68e6be4006e_支持事件联调自动指令.py
  20260826_0109_9624cc34fa93_drop_menu_persistence.py
  20260827_0433_71eeea05c864_记录_event_命令阻塞因果.py
  20260829_0800_7bdca6f754ee_记录_transport_位置投影来源任务.py
  20260829_1129_baf328359533_add_execution_authority_projections.py
  20260829_1159_273898a3f09b_add_material_admission_fifo.py
  20260829_1217_dd35f04b258f_add_safety_evidence_authority.py
  ```

  其余 102 个 revision 为当前未映射集合；该差集不是 `NONE`，Task 3 仍须为 128/128 精确路径建立 deletion tombstone。

- [x] **Step 3: 枚举 autogenerate 不会完整表达的对象**

  Run: `LC_ALL=C rg --sort path -n "op\.execute|CREATE (SCHEMA|EXTENSION|FUNCTION|TRIGGER|VIEW|INDEX)|create_hypertable|timescaledb" migrations/versions migrations/env.py`

  Expected: 每个命中都被判定为最终保留或 `NONE`；不得把历史 SQL 原样复制进新基线。把最终保留对象固化为 Task 2 的 `EXPECTED_SCHEMA_MANIFEST` 输入，至少包含：

  - 每张表的 schema/name，以及每列的规范化 PostgreSQL type、nullable、server default；
  - 全部最终 PK、FK、UNIQUE、CHECK、EXCLUDE 约束的 schema/table/name/type 和规范化定义；
  - 全部索引的 schema/table/name、unique、access method、列或表达式、include 列和 predicate；
  - extension、function、trigger、view、TimescaleDB 对象等专有对象的稳定 identity 与规范化定义。

  `NULL` default 与不存在 default 必须按 PostgreSQL catalog 语义明确区分；不得只冻结对象名称或依赖 metadata 补齐定义。

  最终结论：321 个 scan hit 已由独立 live catalog 终裁。Retain extensions 为 `plpgsql 1.0` 与 `timescaledb 2.23.1`；user
  function/procedure、non-internal trigger、view/materialized view、hypertable、continuous aggregate 与 user job 均为 `NONE`。
  每张表、列、约束和索引的规范化定义保存在不可变 raw catalog；final successor 只按已评审 disposition 选取 retained objects。

- [x] **Step 4: GitNexus 检查迁移环境与 metadata 注册影响**

  对 `migrations/env.py` 实际导入的 metadata 注册模块及 `tests/support/sqlmodel_metadata.py` 中的注册 helper，使用 GitNexus 的 `file_path` 参数运行 context/impact，避免同名模型或 helper 误命中。

  Expected: 明确所有模型注册入口；HIGH/CRITICAL 先向用户报告并取得确认。

  当前结论：controller-owned current-index 已固定在 worktree `97e6887a`。file-qualified impact 显示
  `register_required_sqlmodel_metadata` 为 MEDIUM、9 个 direct consumer，exact `rg` 一致且均为 2 个 integration 与 7 个 runtime
  transport 测试文件。`migrations/env.py::target_metadata` context exact，但 impact 为 `UNKNOWN`/0；graph 不追踪 module-scope bare
  variable read，因此以 `migrations/env.py:131,138-139,225,263` 的 exact source lines 补证，绝不把 `UNKNOWN` 当安全。22 identity
  current-index impact、flow truncation 与 CLI/MCP storage-version 边界见忽略的 `task-1-gitnexus-impact.md`；该 Step 只闭合
  current-index 部分，不终裁 proposal 或替代 catalog/roster/review。

- [x] **Step 5: 对冻结清单执行实施前复审**

  使用 `superpowers:requesting-code-review` 只读评审 Phase 10 退出证据、`EXPECTED_SCHEMA_MANIFEST`、revision 清单和专有对象处置；通过 `superpowers:receiving-code-review` 核实意见并修订本计划。

  Expected: 无可操作意见后才能进入 Task 2；复审同时冻结安全数据库 wrapper、当时全部 revision 的精确删除分类、待归档文档和待删除 revision 的精确路径，并把最终数量与路径清单写回本计划后再次评审。若没有待归档的其它迁移过程文档，清单必须显式记录 `NONE`。若当时没有满足“loopback + 精确库名 + 子进程 URL 透传 + 异常清理 + DROP 后复查”的现成 wrapper，必须先在本计划 Task 2 前插入独立 TDD 任务并再次评审，不得在 Task 3 临场拼接 destructive shell。本文件当前内容不得替代这次实施时复审。

  当前结论：128/128 revision 删除分类、22 identity final lifecycle、current-index evidence、其它待归档迁移过程文档 `NONE`、
  Task 1A wrapper、Task 1B canonical catalog/final roster/cleanup absence 与独立 review 均已闭合。Task 2 因 raw catalog/full-equality
  合同与 final disposition 冲突继续 blocked，不是因为 Task 1B 证据缺失。

### Task 1A: 先以 TDD 建立固定用途数据库安全 wrapper

**Classification:** 高风险测试基础设施；必须在 Task 2 前单独完成 RED → GREEN 与只读评审，不执行 Phase 11 DDL。

**Files:**

- Modify: `tests/support/postgresql_heavy.py`
- Create: `tests/database/test_postgresql_heavy_fixed_database.py`
- Verify existing mapping: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: 现有 preflight、cleanup、URL render 与 subprocess environment primitives。
- Produces: 仅允许固定 `wes_baseline_generation`、host 必须为 loopback、明确把同一
  `ALEMBIC_DATABASE_URL` 传给每个子进程、异常/取消/成功均清理且 DROP 后复查 `pg_database` 缺席的 context manager。

- [x] **Step 1: 写安全红灯**

  使用 fake async driver 覆盖：拒绝 `db` 或外部 allowlist host、拒绝错误数据库名、只把 URL 放入子进程 environment、不在输出或
  exception 泄露密码、场景异常和子进程异常仍清理、DROP 失败或 DROP 后 catalog 仍存在时 fail closed。

  Evidence: 首轮 RED 共 10 cases，9 failed / 1 passed，失败由缺少目标 API 导致；补充 primary-error preservation case 后的第二轮
  RED 共 11 cases，1 failed / 10 passed，精确证明 cleanup error 会覆盖 primary error。

- [x] **Step 2: 最小实现固定用途 wrapper**

  复用现有 `_integration_url`、preflight、cleanup 与 subprocess helper；只扩展固定数据库名及 post-DROP catalog verification，
  不引入通用任意库名 API，不把 destructive shell 拼接留到 Task 3。

  Historical evidence: 首轮 GREEN 的 10 cases 全部通过；补齐 primary-error preservation 后，历史第二轮 GREEN 的 11 cases
  全部通过。后续 GStack safety review 与追加回归见 Step 3 当前最终证据。

- [x] **Step 3: 验证与评审**

  Run: `uv run pytest tests/database/test_postgresql_heavy_fixed_database.py -q`

  Run: `uv run scripts/select_heavy_tests.py --scope unstaged`

  Expected: focused FAST PASS；selector 继续按 `tests/support/postgresql_heavy.py` 的既有精确 mapping 选择真实 PostgreSQL owners。
  独立 reviewer 只读检查固定名、loopback、subprocess URL、异常清理与 DROP 后复查；无意见后才允许 Task 2。

  Current final evidence: focused `16 passed`，ruff 与 format check 通过；selector exit `0` 并列出 40 个 existing PostgreSQL
  owners，未执行这些 HEAVY owners。GStack 首轮 CRITICAL 指出以 `create_attempted` 授权 cleanup 会误删预存固定库或 CREATE
  失败时从未归本次调用所有的库；实现已改为 confirmed CREATE ownership。post-DROP `fetchval` exception 与 CREATE-await
  cancellation race 两个 Minor 覆盖均已闭合。同一独立 reviewer 随后完成 fresh review：`Approved`，无
  Critical/Important/Minor。真实 Alembic URL 绑定与 stateful 行为仍只属于 Task 1B，不由 fake-driver 证据替代。
  QUALITY process-naming guardrail 7 passed；固定用途名为 `wes_baseline_generation`，没有增加 allowlist。

### Task 1B: 单独授权并冻结独立 PostgreSQL catalog manifest

**Classification:** stateful PostgreSQL/TimescaleDB characterization；用户已单独授权。Task 1A safety wrapper 与 Task 1B
controller 分别通过 focused `16 passed`、`27 passed`；controller 在真实 PostgreSQL/TimescaleDB 上闭合两处版本兼容问题后，
同一独立 reviewer fresh review 均为 `Approved`。本 Task 已完成，但不授权 Task 2 DDL 或 revision/model 删除。

**Fixed authority and artifacts:**

- Exact database: `wes_baseline_generation`；只能通过 Task 1A wrapper 创建/清理，host 必须为 loopback，禁止复用开发库、
  integration `wes_db`、生产库或其它名称。
- Frozen source: `develop@97e6887a83bce1633c09462c5f9fac4f74d2730c`、128-revision path-list SHA-256
  `bd256cf50b7ce34ce840210a9486cc536893df992665f70fa9b7beac8d54ae4f`、old head `dd35f04b258f`。
- Canonical artifact:
  `.superpowers/sdd/2026-08-15-wes-schema-and-migration-baseline-reset/task-1-catalog-manifest.json`；完成时把稳定排序、canonical
  JSON 的 SHA-256 回写本计划和 `task-1-report.md`，不得只记录终端输出或未哈希临时文件。
- Final metadata/`rough_sorter` roster artifact:
  `.superpowers/sdd/2026-08-15-wes-schema-and-migration-baseline-reset/task-1-final-model-roster.md`；必须由 current-index impact、
  明确模型注册入口和插件当前合同联合评审形成，不得从 grep、`migrations/env.py` 或 `SQLModel.metadata` 单方猜测。

- [x] **Step 1: 复核授权与 wrapper 证据**

  冻结 exact database、loopback host/port、source commit、revision-list hash、执行人授权和 Task 1A focused/selector/review 证据。
  任一不一致立即停止，不得创建数据库。

- [x] **Step 2: 用旧 chain 建立 catalog source database**

  wrapper 创建 `wes_baseline_generation`，只把该库的 `ALEMBIC_DATABASE_URL` 传给子进程；运行当前 128-revision old chain
  `uv run alembic upgrade dd35f04b258f`。必须验证实际 head 为 `dd35f04b258f`，upgrade failure/skip/漂移均保持 Task 2 blocked。

- [x] **Step 3: 独立读取并规范化 catalog**

  直接从 PostgreSQL/TimescaleDB catalog 生成稳定排序的 canonical JSON，至少包含：schema/table；逐列 `format_type`、nullable、
  identity/generated、collation、server default，并区分 default absent 与 SQL `NULL`；PK/FK/UNIQUE/CHECK/EXCLUDE 的名称、类型和
  `pg_get_constraintdef`；index 的 unique、access method、key/expression、include、opclass/collation、predicate 与
  `pg_get_indexdef`；extension/version、function identity/definition、非内部 trigger、view/materialized view definition、TimescaleDB
  hypertable/continuous aggregate 等稳定 identity。排除项只能是已评审的 PostgreSQL/TimescaleDB internal objects，不能以
  `migrations/env.py` 或 metadata 补缺。

- [x] **Step 4: 冻结 artifact、hash 与 final-model roster**

  写入 canonical catalog artifact，计算 SHA-256，重复同一查询并要求 byte-identical；同时冻结 current-index GitNexus 验证过的
  metadata 注册入口、当前核心模型和 `rough_sorter` 模型 roster。catalog 与 roster 任一对象无法解释、22 proposal 任一仍有 owner/
  FK/index/constraint/专有对象依赖、或 hash 不稳定，都必须记录精确差异并停止。

  Result: canonical catalog SHA-256 `77214740a6fd48c113c043aeb32887209681c4fd213833e7e9ee391f317117c9`；
  run artifact SHA-256 `031ff8f3d7e251be518ad5394263c5325563b93478d87289e481c0dffa9d8213`；两次 manifest hash 相同。
  Final roster 位于 `task-1-final-model-roster.md`。

- [x] **Step 5: 无条件清理并证明数据库缺席**

  无论 upgrade、introspection、hash 或 review 成功/失败，wrapper 都必须终止剩余连接、DROP exact database，再查询
  `pg_database` 证明 `datname = 'wes_baseline_generation'` 为 0 行。把 cleanup result 与 absence-query hash 写入
  `task-1-report.md`；DROP 失败、复查非零或证据缺失均为 blocker。

  Result: wrapper parameterized absence query SHA-256
  `639664af82d7c69f52dbaa8fbab16a3f6209c9a998871a1a57a70ff0db83ad2f`；controller 返回后独立 exact-name query SHA-256
  `a069fb8411f0f1af5e957203c4172a165e98ce080cbb6097ed185dabd60cb88d`，结果 `0`；隔离 Compose project 已移除。

- [x] **Step 6: current-index impact 与独立只读 review**

  controller 刷新并固定 current-index 后，对 `migrations/env.py` 注册入口、`tests/support/sqlmodel_metadata.py` helper、22 identity
  source 和 `rough_sorter` roster 做 file-qualified context/impact。独立 reviewer 核对 old-chain source、catalog normalization、artifact
  hash、cleanup absence、proprietary-object `RETAIN`/`NONE`、final-model roster 与 22 identity proposed lifecycle；无可操作意见后才能
  将逐 identity 结论改为 final，并进入 Task 2。

  最终结论：metadata 注册入口、helper、22 identity source、final-model/`rough_sorter` roster、catalog、cleanup absence 与独立
  review 均已闭合。22 identity 终裁为 `21 FINAL_DELETE_AFTER_SUCCESSOR + 1 RETAIN`；两张 catalog-only 表终裁
  `FINAL_DELETE -> NONE`。Task 2 已将 raw catalog/full-equality 矛盾收敛为 immutable characterization + explicit
  disposition + strict final manifest，并通过独立评审。

**Exit result:** Task 1B 的 source/head/list hash、catalog、roster、208 residual hits（其中 19 行终裁 `NONE`）、22 identity、
cleanup absence 与独立 review 均闭合。
Task 2 admission 已通过：raw catalog 保持不可变 characterization，最终 `EXPECTED_SCHEMA_MANIFEST` 只由已评审 retain
disposition 形成并显式引用 21+2 个排除决定；operational consumer successor 在 Tasks 2–3 内按 TDD 闭合。

### Task 2: 先建立结构红灯与最终 Schema 绿灯 successor

**Files:**

- Create: `tests/integration/test_initial_schema_baseline_postgresql.py`
- Create: `tests/architecture/test_migration_baseline_structure.py`
- Create: `tests/support/postgresql_catalog.py`
- Create: `tests/integration/fixtures/initial_schema_old_chain_catalog.json`
- Create: `tests/integration/fixtures/initial_schema_final_manifest.json`
- Create: `tests/integration/fixtures/initial_schema_disposition.json`
- Modify only if its existing runtime-status assertions require adaptation: `tests/architecture/test_runtime_status_owner_guardrail.py`
- Preserve: `tests/support/sqlmodel_metadata.py`（只服务当前 8-model SQLite fixture，不作为最终 schema oracle）
- Modify in Task 3 after successor turns green: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: Task 1B 不可变 raw catalog、final roster/disposition 和 PostgreSQL 专有对象清单。
- Produces: 单 revision、空库 upgrade、metadata/schema 一致性和专有对象存在性的权威 HEAVY 验收。

- [x] **Step 1: 写失败的单基线结构测试**

  在新的 `test_migration_baseline_structure.py` 中断言 `migrations/versions/` 只有一个 Python revision、`down_revision is None`、Alembic 只有一个 head；当前 revision chain 尚未收敛时应失败。`test_runtime_status_owner_guardrail.py` 继续只拥有 runtime-status 读写 owner，不承接 revision 数量。

- [x] **Step 2: 写 PostgreSQL old-chain characterization 与 final-schema successor**

  使用现有 `temporary_database()` harness，在隔离数据库运行 `alembic upgrade head`。测试 fixture 固化三份独立评审输入：
  Task 1B raw catalog、`21 DELETE + 1 RETAIN + 2 catalog-only NONE` disposition，以及只含 retained objects 的 final manifest。
  `test_initial_schema_baseline_postgresql.py` 逐项验证：

  - `wes_sys`、`wes_biz`、`wes_runtime` 等最终 schema；
  - 全部最终表及逐列 type/nullability/server default；
  - 全部最终 PK、FK、UNIQUE、CHECK、EXCLUDE 约束及规范化定义；
  - 全部最终索引的 unique/access method/列或表达式/include/predicate 定义；
  - 冻结清单中明确保留的 PostgreSQL/TimescaleDB 专有对象；
  - `alembic_version` 位于 `wes_sys`；
  - 退役插件表、字段、索引和约束不存在。

  删除旧 chain 前，old-chain characterization 必须完整等于 raw catalog，并证明 raw-minus-final 的 table/object 差集只来自
  已评审 disposition；final-schema successor 此时应只因 21+2 个获批排除对象仍存在而 RED。Task 4 对新初始基线执行 final
  manifest 完整等值验收，并以 `alembic check` 证明 final schema 与当时 `migrations/env.py` target metadata 一致。Raw artifact
  不得修改，final manifest 不得从 metadata 反向生成，也不得静默过滤未在 disposition 中登记的差异。

- [x] **Step 3: 分别验证结构红灯和 Schema successor 绿灯**

  Run: `uv run pytest tests/architecture/test_migration_baseline_structure.py tests/architecture/test_runtime_status_owner_guardrail.py -q`

  Run with isolated PostgreSQL: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: structure test 只因当前存在多条 revision 而 FAIL；runtime-status owner PASS；old-chain raw characterization PASS、
  final-schema successor 只对 21+2 个 disposition 对象 RED。任何其它 catalog 差异、skip 或环境未执行都必须停止，禁止删除旧 revision。

- [x] **Step 4: 冻结 HEAVY mapping 处置清单**

  对 Task 1 冻结的全部 revision 逐个建立精确 deletion classification，并分别记录已有 mapping 与未配置数量。不得只枚举已有 mapping owner，也不得新增 `migrations/versions/**` 宽泛 mapping；它会与精确规则形成不同策略重叠。新基线 revision 与所有待删除 revision 的精确 tombstone mapping 在 Task 3 原子加入当前配置，统一指向 `test_initial_schema_baseline_postgresql.py`，以覆盖 staged 和 CI base diff；含删除的提交合入 `develop` 前不得移除这些 tombstone。

Task 2 于 2026-08-31 闭合：old-chain live characterization 在隔离 PostgreSQL 上确认数据库 head 精确为
`dd35f04b258f` 并完整匹配 immutable raw catalog；final-schema successor 的唯一 RED 为 disposition 冻结的 21 个
schema-deferred 与 2 个 catalog-only identity。结构测试的唯一 RED 为 128 个旧 revision，runtime-status owner guardrail
通过；独立 fresh review 结论为 `TASK2 APPROVED`。临时数据库与 Compose project 均已清理并复查不存在。

Task 3 runtime evidence 使上述 approval 的 final-manifest 部分失效：current metadata 生成的候选可从空库 upgrade，补齐两个
`use_alter=True` 循环 FK 后 `alembic check` 无漂移，但 live catalog 与“raw 删除 23 表”fixture 并不等值。surviving tables
存在 column default/type/nullability/order、constraint/FK name 和 index 集合差异；候选 409 个 index，旧 fixture 343 个。
因此重新打开 Task 2 final-manifest：raw fixture/SHA 保持不可变，先对 fresh live candidate 做两次 byte-identical 采集，建立
覆盖全部 surviving column/constraint/index drift 的逐项 disposition，`UNRESOLVED=0` 且独立评审后，才能把 live candidate
提升为 immutable final manifest。禁止直接覆盖 fixture、放宽 comparator 或把 66 个净新增 index 批量解释为 metadata 噪声。

Task 2 已在 Task 3 修正后重新闭合：old-derived catalog SHA-256 为
`87c2f3461068b97c713032bd83312bc64cdfbe481574d4d8c4e331b37a5bc0b6`，两次 fresh live candidate byte-identical；
逐项 transition disposition 覆盖 485 个 differences 且 `UNRESOLVED=0`，其 SHA-256 为
`4192a879f7cf4ed006d63b95ea74529d36f1678fd0ad9ae490287e3efd5730b7`。最终 immutable manifest SHA-256 为
`2cf84f8ebdd9b9533813c4765abb186d9c024312efd5b494866abb7be5cd389d`，严格 comparator 与 provenance 绑定测试通过，
独立 fresh review 已批准当前 final/transition 合同。

### Task 3: 在隔离空库生成唯一初始 revision

**Files:**

- Create: `migrations/versions/<generated>_create_initial_wes_schema.py`
- Delete after generation: 旧 `migrations/versions/*.py`
- Delete only after Task 1B final review and successor: 删除 21 个 `FINAL_DELETE_AFTER_SUCCESSOR` identity 的精确 source-model
  路径（`models/runtime_hold.py` 含两个 identity）；保留 `workline_runtime_status_projection.py` 与其 metadata registration
- Review and modify: `migrations/env.py`
- Modify: `scripts/generate_legacy_matrix.py`
- Regenerate: `docs/architecture/legacy-cleanup-matrix.csv`
- Modify: `tests/architecture/test_cleanup_matrix_guardrail.py`
- Modify: `tests/scripts/test_select_heavy_tests.py`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Execute through: Task 1 冻结并通过自动化测试的精确安全数据库 wrapper；不得直接拼接临时 shell

**Interfaces:**

- Consumes: Task 1 对象清单和 Task 2 红灯测试。
- Produces: `down_revision = None` 的单一初始 migration。

- [x] **Step 1: 创建并验证精确隔离数据库**

  使用 Task 1 冻结的 wrapper 在本地 HEAVY Compose PostgreSQL 上创建固定用途数据库 `wes_baseline_generation`。wrapper 必须在不打印密码的前提下输出 host、port、database，确认 host 为 loopback 且 database 名完全匹配，并把只指向该库的 `ALEMBIC_DATABASE_URL` 传给后续每个 Alembic 子进程；不复用现有开发库。整个生成流程由 wrapper 的 trap/finally 清理 guard 包裹，任何中间异常都进入 Step 6 的精确清理。

- [x] **Step 2: 只 stamp 当前 head，不建立旧 schema**

  对该空数据库运行 `uv run alembic stamp head`，使 Alembic 允许从最终 metadata autogenerate。`migrations/env.py` 会预建最终空 schema，因此验收应为：只允许配置声明的空 schema 与 `wes_sys.alembic_version`，不得存在任何业务表、业务索引、函数、触发器或 TimescaleDB 对象。

- [x] **Step 3: 使用 generator 生成 revision**

  Run: `uv run alembic revision --autogenerate -m "建立最终初始数据库基线"`

  Expected: Alembic 生成随机 revision ID；不得手工创建 migration 文件。

- [x] **Step 4: 将生成 revision 转为初始基线**

  将 `down_revision` 改为 `None`，检查 upgrade 先建立所需 schema，再创建表和约束。按照 Task 1 冻结清单补入 autogenerate 无法表达但最终仍需要的 PostgreSQL/TimescaleDB 对象；`downgrade()` 明确抛出 `NotImplementedError`。同时审查 `migrations/env.py` 的 `transaction_per_migration` 与 autocommit 配置：若新基线仍含并发索引/autocommit block，则保留并改为不绑定历史 revision 名称的当前事实注释；若不再需要，则最小化配置并删除“Revision C”等失效叙述。

- [x] **Step 5: 删除旧 revision 文件**

  仅删除 Task 1 已冻结清单中的旧 tracked revision；不得使用仓库级 `git clean` 或通配递归删除。Git 历史本身保留追溯能力，不在项目内复制旧 migration。为新生成 revision 增加只指向 `test_initial_schema_baseline_postgresql.py` 的精确 mapping；对每个被删旧 revision 保留同一 successor 的精确 tombstone mapping，保证 staged 与 `origin/develop` base diff 都能分类。不得在含删除的同一分支中直接删除旧 mapping，也不得用宽 glob 代替 Task 1 冻结的逐文件精确分类。

  在 `tests/scripts/test_select_heavy_tests.py` 先增加基于 Task 1 冻结删除清单的合同：把全部旧 revision 路径作为 changed files 输入当前配置时，不得 fail closed，且选择结果包含最终基线 PostgreSQL successor；任意漏配路径仍应失败。先观察测试失败，再为冻结清单中的每条旧 revision 加入精确 tombstone，并加入新 revision mapping 使其通过。

  只有 Task 1B current-index/catalog/review 把对应 proposal 逐项终裁为 delete 后，才在同一原子 diff 删除获批的 source-model 文件、
  移除 `migrations/env.py` import，并把唯一 generator/CSV 中相同 identity 的 `schema-deferred` 审计行终裁为 `delete -> NONE`；
  cleanup-matrix guardrail 改为相同 source 缺席 owner。每个实际 source 删除路径必须有指向
  `test_initial_schema_baseline_postgresql.py` 的精确 tombstone，不得依赖 revision tombstone 或宽 source glob 代替。

- [x] **Step 6: 清理基线生成数据库**

  无论 autogenerate 成功或失败，wrapper 都必须终止该数据库的剩余连接并删除名称完全匹配的 `wes_baseline_generation`；再次查询系统目录证明数据库不存在。不得把该固定生成库留给 Task 4 或后续开发复用；wrapper 的拒绝非 loopback、拒绝错误库名、子进程失败仍清理和成功清理路径必须已有自动化测试。

- [x] **Step 7: 运行绿灯结构测试**

  Run: `uv run alembic heads`

  Run: `uv run pytest tests/architecture/test_migration_baseline_structure.py tests/architecture/test_runtime_status_owner_guardrail.py tests/scripts -q`

  Expected: 唯一 head；revision 目录结构测试 PASS。

### Task 4: 用空库验证新基线与 metadata 完全一致

**Files:**

- Modify if failures expose real gaps: `migrations/versions/<generated>_create_initial_wes_schema.py`
- Modify if final metadata registration is incomplete: `migrations/env.py`
- Preserve: `tests/support/sqlmodel_metadata.py`（不得扩张为最终 schema oracle）

**Interfaces:**

- Consumes: 唯一初始 revision。
- Produces: 可重复建立且无 metadata 漂移的最终数据库。

- [x] **Step 1: 从全新临时库执行 upgrade**

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: selector 实际运行 `test_initial_schema_baseline_postgresql.py`，JUnit 中 `total > 0` 且 `skipped = 0`。

- [x] **Step 2: 验证 Alembic 无后续差异**

  `test_initial_schema_baseline_postgresql.py` 必须在同一个临时数据库完成 upgrade 后，用当前 Python 解释器执行 `python -m alembic check`；测试通过环境变量把同一个临时数据库 URL 传给子进程，禁止嵌套调用 `uv run` 或切换数据库。

  Expected: 子进程输出 `No new upgrade operations detected.`。

- [x] **Step 3: 验证重复建库**

  删除第一次临时数据库后重新创建第二个随机临时数据库，再次执行 `alembic upgrade head` 和完整基线测试。

  Expected: 第二次结果一致；不存在依赖第一次运行残留的 schema、extension 或全局状态。

### Task 5: 迁移 revision 专属测试到最终 schema owner

**Files:**

- Review and possibly delete: `tests/database/test_*_migration.py`
- Review and possibly delete: `tests/migrations/test_*.py`
- Review and possibly delete: `tests/deployment/test_retire_workline_inbox_migration.py`
- Review and possibly delete: `tests/integration/test_workline_plugin_schema_retirement.py`
- Review and update: `tests/architecture/test_cleanup_matrix_guardrail.py`（只对 Task 1B 已终裁 delete 的 identity 保留
  `delete -> NONE` 与 source absence owner）
- Review and update: `tests/scripts/test_select_heavy_tests.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: 已通过的 `test_initial_schema_baseline_postgresql.py`。
- Produces: 只验证最终 schema/行为的测试树，不再读取已删除的 revision 文件名或正文。

- [x] **Step 1: 逐个建立 successor/`NONE` 清单**

  Run: `LC_ALL=C rg --sort path -n "migrations/versions|MIGRATION =|glob\(.*migration|read_text\(" tests/{database,migrations,integration,deployment,scripts} --glob '*.py'`

  对每个命中明确：最终 schema successor、仍有独立行为价值的测试 owner，或 `NONE`。不得按文件名关键词批量删除。

  Task 1B 已终裁 delete 的 identity 不得继续由旧 schema-only 测试证明“存在”：数据库终态统一由
  `test_initial_schema_baseline_postgresql.py` 证明 `ABSENT`；cleanup-matrix guardrail 只证明唯一 registry 中对应行为
  `delete -> NONE` 且获批 source 路径缺席。任何未终裁或 RETAIN identity 保持原 owner，不能被统一决定顺带删除；其它 migration
  测试仍按本 Step 逐个裁定。

- [x] **Step 2: 先运行 successor**

  Run: `./scripts/run_selected_heavy_local.sh --scope unstaged`

  Expected: PASS 且无跳过。未通过前不得删除旧测试。

- [x] **Step 3: 删除旧 revision 专属断言并更新 selector**

  删除只读取旧 migration 文件名、SQL 文本、upgrade/downgrade 或回填过程的测试；保留最终模型行为、可靠性和数据库约束测试。HEAVY mapping 统一指向最终基线测试和仍有效的领域 PostgreSQL 测试。

- [x] **Step 4: 运行测试拓扑与 selector 合同**

  Run: `uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py tests/scripts -q`

  Expected: PASS。

- [x] **Step 5: 冻结数据库基线原子提交清单**

  **Task 5 freeze result（2026-08-31）:** 待提交清单共 236 个精确文件路径（`docs` 11、`migrations` 130、
  `scripts` 4、`src` 37、`tests` 54），排序路径以 LF 连接后 SHA-256 为
  `a7305f623fc0e2db8ad12af62a8297ec3f046a7e1641d79322c6dfa53b08e236`。清单包含 128 个旧 revision 删除、唯一新 revision、
  21 个 source-model 删除、最终 manifest/successor、裁定的旧 migration tests 及当前状态文档；不包含忽略的
  `.superpowers/` 本地执行 artifact。QUALITY 首轮发现业务 legacy ledger 仍引用已删除的 migration test 后，清单精确加入
  `docs/architecture/business-legacy-absence-ledger.csv`，其 owner 已收敛到现存 `test_ng_reason_contract.py`。

  根据 Task 1 的 revision 删除清单和本任务的 successor/`NONE` 清单，冻结待提交的精确路径；此处不得提交。删除项必须通过 `git add -u -- migrations/versions` 暂存，新增/修改项必须逐个列出完整路径，禁止 `git add migrations tests` 等目录级暂存。

  Expected: 清单包含生成的新 revision、所有旧 revision 删除、实际修改的 `migrations/env.py`、两个新测试 owner、实际保留/删除的旧迁移测试、`tests/scripts/test_select_heavy_tests.py` 和 HEAVY TOML；Commit/PR 说明草稿逐项列出被删除测试的 successor 或 `NONE`。

### Task 6: 最终质量门禁、独立评审与文档生命周期收尾

**Files:**

- Modify: `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- Modify: `docs/superpowers/README.md`
- Verify archived prerequisite: `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-15-wes-retired-plugin-residual-convergence.md`
- Archive externally when completed: `docs/superpowers/plans/2026-08-15-wes-schema-and-migration-baseline-reset.md` → `../archive_docs/wes_backend/docs/superpowers/plans/2026-08-15-wes-schema-and-migration-baseline-reset.md`
- Archive externally when completed, only if Task 1 freezes exact paths: 被本计划取代的其它迁移过程文档逐项源路径 → 逐项目标路径；当前尚未冻结，Task 2 前必须回写精确清单或 `NONE`
- Other superseded migration-process documents: `NONE`（Task 1 frozen）

**Interfaces:**

- Consumes: Tasks 1–5 的完整 diff。
- Produces: Phase 11 退出证据和 Phase 12 教学式插件开发使用的唯一空库基线。

- [x] **Step 1: 精确暂存完整数据库基线差异**

  先确认工作区不存在 Task 1 冻结范围外的并发变更。对旧 revision 删除执行 `git add -u -- migrations/versions`；21 个 source
  model 删除逐路径暂存。随后只按 Task 5 冻结清单逐个暂存生成的新 revision、实际修改的 `migrations/env.py`、
  `scripts/generate_legacy_matrix.py`、生成的 `docs/architecture/legacy-cleanup-matrix.csv`、
  `tests/architecture/test_cleanup_matrix_guardrail.py`、`tests/architecture/test_migration_baseline_structure.py`、
  `tests/integration/test_initial_schema_baseline_postgresql.py`、实际保留或修改的迁移测试、
  `tests/scripts/test_select_heavy_tests.py` 和 `docs/architecture/heavy-test-impact.toml`。禁止暂存目录、glob、尚未核对的测试或外部文档。

  Run: `git diff --cached --name-status`

  Expected: 与 Task 5 的精确原子提交清单完全相等；否则取消错误路径的暂存并停止。

- [x] **Step 2: 运行默认与质量门禁**

  Run: `uv run pytest --collect-only -q -o addopts='' | tail -5`

  Run: `./scripts/git-quality-gate.sh --profile quality`

  Expected: 收集成功；质量门禁退出码 `0`。

- [x] **Step 3: 运行暂存 HEAVY**

  Run: `uv run scripts/select_heavy_tests.py --scope staged`

  Run: `./scripts/run_selected_heavy_local.sh --scope staged`

  Expected: 新基线、受影响领域 PostgreSQL 测试全部实际执行且无跳过。

- [x] **Step 4: 验证最终缺席与单基线**

  Run: `test "$(rg --files migrations/versions -g '*.py' | wc -l | tr -d ' ')" = "1"`

  Run: `rg -n "smt_classifier|smt_sorting_inbound" migrations/versions --glob '*.py'`

  Expected: 单 revision；Phase 5 明确退役的业务 schema 词汇零命中；`test_initial_schema_baseline_postgresql.py` 按 Task 1 冻结的表/列/索引/约束矩阵证明全部退役对象不存在。不得把 `plugin_key`、`plugin_contract_version` 或 `rough_sorter` 目标身份作为全库禁词；其合法位置必须逐条来自 Task 1 冻结清单。Phase 12/13 插件名称和预留 schema 必须不存在。

- [x] **Step 5: 提交前运行 GitNexus 变更检测**

  Run `gitnexus_detect_changes({scope: "staged"})`。

  Expected: 变更只影响数据库基线、迁移测试、selector 和 Phase 11 当前态文档。

  **Task 6 final-gate evidence（2026-08-31）:** 默认拓扑收集 2316 项；QUALITY 通过（FAST 2311 passed、5 个默认条件
  skip，不作为 HEAVY 证据）；staged selector 的 32 个 owner 实际执行 324 passed、0 skipped；迁移目录仅保留
  `f9c7c2e5f501`，退役 schema 词汇零命中，固定生成库复查不存在。GitNexus 在刷新索引并恢复其超范围入口改写后，
  staged detect 为 57 files、333 symbols、0 affected processes、low risk；最终 staged 清单仍为 236 路径且路径 SHA-256
  为 `a7305f623fc0e2db8ad12af62a8297ec3f046a7e1641d79322c6dfa53b08e236`。

- [x] **Step 6: 独立代码评审**

  使用 `superpowers:requesting-code-review` 对完整 staged diff 做只读评审，并向 reviewer 提供 Step 1 冻结的路径、精确 staged 状态和 Steps 2–5 的验证证据；reviewer 不启动 Docker、不执行 HEAVY。通过 `superpowers:receiving-code-review` 核实意见并按 TDD 修复，修复后重新精确暂存并运行直接受影响测试；既有最终门禁证据标记为 `STALE`，中间评审轮次不重复执行完整 Steps 2–5。循环至无可操作问题后，回到 Step 1 核对最终 staged 清单，并在最终快照上完整重跑 Steps 2–5 一次；全部通过后才能提交。

  Final closure review：`APPROVED`，无 Critical、Important 或 Minor；Reviewer 未重复运行 QUALITY、HEAVY、数据库或迁移。

- [x] **Step 7: 提交数据库基线原子变更**

  在 staged HEAVY、GitNexus 检测和独立评审均无意见后运行：

  Run: `git commit -m "refactor(database): 重置未发布系统迁移基线"`

  Commit/PR 说明必须列出被删除测试的 successor 或 `NONE`；不得使用 `--no-verify`。

- [ ] **Step 8: 合入后清理 revision tombstone mappings**

  含 Task 1 冻结 revision 与 21 个 source-model 删除的提交合入 `develop` 后，以独立 cleanup 提交删除这些已不存在路径的精确
  tombstone mappings，并同步删除只约束该过渡清单的 selector 测试数据；保留新基线 revision mapping 和通用 fail-closed 合同。
  先运行 `uv run pytest tests/scripts -q`，再对 cleanup 的 staged diff 运行 selector 与
  `gitnexus_detect_changes({scope: "staged"})`，确认 CI base diff 已不再包含旧 revision/source 删除；全部通过后提交
  `chore(database): 清理旧迁移 HEAVY tombstone`。不得在原基线 PR 内提前清理。

- [ ] **Step 9: 更新阶段状态并归档过程文档**

  只有全部门禁和 tombstone cleanup 通过后，才把 Phase 11 标记为完成并进入 Phase 12 教学式开发。先更新 master plan、README 和项目内所有当前态引用；确认活动残余计划已位于上述精确外部路径，否则停止。

  对本计划及 Task 1 写回的每个其它精确源路径分别计算 SHA-256，确认目标不存在；发生重名时先确定唯一目标名并回写清单，不得覆盖。逐项移动后验证项目内原路径缺席、外部归档存在且 SHA-256 相等；项目内不得保留副本、占位、软链接或转发文档。最后只精确暂存 master plan、README 和这些源文件删除，核对 `git diff --cached --name-status`，运行 `git diff --check`、引用扫描及 `gitnexus_detect_changes({scope: "staged"})`；全部通过后提交 `docs(database): 归档迁移基线重置计划`。

  Repository 交付到此最多报告 `MERGED — NOT DEPLOYED`。旧联调数据库位于被替换的 `dd35f04b258f` chain，禁止宣称可原地
  upgrade；任何联调 backup/rebuild/redeploy 必须取得单独 Deploy/Cutover 授权并精确命名数据库，不能由 merge、健康检查或本计划
  自动扩权，也不能冒充供应商、设备物理或业务验收。
