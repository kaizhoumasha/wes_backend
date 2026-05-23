# 货架操作服务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将粗分机“当前工作位货架移出 + 新空箱货架补入 + session 恢复”收敛为货架级 operation；`workline_sessions` 只承载物料/料盘 session，`resource` 域只保存资源现状，`workline_rack_tasks` 只记录货架动作任务生命周期并用 `operation_key` 关联同一业务操作。

**Architecture:** 粗分机插件在请求分配料格失败时发起货架操作；操作服务在短事务内校验容量、创建同一 `operation_key` 下的一条或多条 `workline_rack_tasks`、创建 outbox；事务提交后由既有 outbox 派发 WMS/RCS；回调只更新单个 task，session 恢复由 operation 派生状态驱动；resource projection 按位置 `capacity` 保存并校验现状。

**Tech Stack:** Python 3.13 + FastAPI + SQLModel/SQLAlchemy Async + Alembic + pytest + Ruff + WorkLine Runtime。

---

## 规格来源

本计划执行并固化以下设计结论：

- 主设计：`docs/superpowers/specs/2026-05-20-rack-operation-service-design.md`
- 工程评审结论：
  - 同一 operation 的“释放容量”规则必须定义到 resource projection 和容量校验。
  - “移出旧货架 + 补入新货架”必须拆成两个 task，只共享 `operation_key`，不能用单 task 承载多动作终态。
  - material session 只能等待 `operation_key`，不能等待单个 `task_key`。
  - 容量检查与 task 创建必须是短事务，不能持有 DB 锁跨 WMS/RCS HTTP 派发。

不属于本次优化范围：

- `docs/superpowers/specs/2026-05-19-smt-sorter-inbound-plugin-spec.md`
- `docs/business/smt_sorter_inbound_workflow_guide.md`
- 分拣机 CTU 投料/出料实现。
- 新增 `workline_rack_operations` 表。

## 当前代码地图

模型与迁移：

- `src/app/workline/models/rack_task.py`：现有 `WorklineRackTask` 仍包含 `RACK_SUPPLY`、`FULL_BOX_EXCHANGE`、`MOVE_TO_EMPTY_AREA`，需要替换为低级货架动作。
- `src/app/workline/models/rack_position.py`：现有 `capacity = 1` 约束，需要改为容量模型。
- `src/app/resource/models/resource.py`：`RackPlacement` 现有 active `(workline_code, position_code)` 唯一索引，需要替换为容量校验。
- `migrations/versions/20260519_0004_bbaa8662d7fe_add_workline_rack_positions_and_bin_.py`：当前未发布迁移包含 `workline_rack_positions`。
- `migrations/versions/20260520_1453_083e85d1bf93_add_workline_rack_tasks.py`：当前未发布迁移包含 `workline_rack_tasks`。

服务与运行时：

- `src/app/workline/services/rack_task_service.py`：当前服务混合了任务创建、回调状态解释和旧满箱交换命名。
- `src/app/workline/repositories/rack_task_repository.py`：需要按 `operation_key`、`target_position_code`、`task_status` 增加查询。
- `src/app/workline/services/rack_position_service.py`：当前 `require_enabled_position()` 拒绝 `capacity != 1`。
- `src/app/resource/repositories/resource_repository.py`：当前 `get_active_by_workline_position()` 只返回单条 active placement。
- `src/app/resource/services/projection_service.py`：当前到位投影把同一位置第二个 active rack 判定为冲突。
- `src/workline_runtime/runtime_intent.py`、`src/workline_runtime/plugin_next.py`、`src/workline_runtime/runtime_intent_effects.py`：当前 `RACK_TASK_REQUEST` 是单任务形态。
- `src/workline_runtime/session_resolver.py`：当前外部 HTTP 回调仍按单 task/session 关联，需要支持 operation 派生恢复。
- `src/workline_plugins/smt_classifier/plugin.py`：当前粗分机插件用 `RACK_SUPPLY` 语义等待单个 rack task。
- `src/app/resource/services/smt_rack_bin_scheduling_service.py`：当前调度决策返回 `rack_supply_request`，需要改为货架操作请求。

## 目标数据流

```text
物料 session 申请料格
  └─ SmtRackBinSchedulingService 无有效料格
      └─ 粗分机插件发起 rack operation intent
          └─ WorklineRackOperationService 短事务
              ├─ 读取 resource 当前工作位现状
              ├─ 读取 WorklineRackPosition.capacity
              ├─ 创建 MOVE_RACK task（可选，有旧货架时）
              ├─ 创建 ALLOCATE_AND_MOVE_RACK task（必需，补新空箱货架）
              └─ 创建对应 outbox
          └─ outbox 提交后派发 WMS/RCS
              └─ 回调逐条更新 task
                  └─ operation 派生成功后恢复 material session
```

## 核心不变量

- `operation_key` 是一次货架业务操作的稳定幂等键，所有 sibling task 共享它。
- `task_key` 是单个低级动作的幂等键；同一 operation 内 `sequence_no` 唯一。
- `sequence_no` 只表示阅读顺序和幂等定位，不表示串行依赖。
- “移出旧货架 + 补入新货架”创建两个 task；两者可以在同一事务后一起派发。
- `MOVE_RACK` 的源位置释放容量只对同一 `operation_key` 的 sibling supply 生效，不能释放给其他 operation 抢占。
- material session context 只记录 `waiting_rack_operation_key`；兼容字段 `waiting_rack_task_key`、`waiting_rack_task_id` 在本次破坏性优化中移除。
- operation 成功条件：同一 `operation_key` 下所有 required task 为 `SUCCEEDED`，并且 resource projection 确认目标位置存在可用货架或目标容量被满足。
- operation 失败条件：任一 required task 为 `FAILED`、`TIMEOUT`、`RECONCILING` 或 resource projection 未确认目标可用；session 不自动恢复。
- 任何 DB 锁和容量校验只覆盖“读现状 + 创建 task/outbox + 更新 session context”的短事务；WMS/RCS HTTP 派发不在该事务内执行。

## 命名决策

- 保留表名 `workline_rack_tasks`。
- 将现有服务职责拆分为：
  - `WorklineRackTaskLifecycleService`：只负责 task 创建幂等、回调更新、单 task 状态流转。
  - `WorklineRackOperationService`：负责 operation 编排、容量预留、sibling task 创建、operation 派生状态。
- 第一阶段不新增 `workline_rack_operations` 表；operation 查询由 `workline_rack_tasks.operation_key` 聚合得到。

## Task 0：执行前护栏与基线确认

- [ ] 读取本计划、主设计文档和 AGENTS 规则。
- [ ] 检查 GitNexus 索引状态；仅当 MCP 工具提示索引过期时刷新索引：

```bash
rtk npx gitnexus analyze
```

- [ ] 使用 GitNexus MCP `impact` 对核心 symbol 做 upstream 影响分析，记录直接调用方、受影响流程和风险级别。

需要覆盖的 symbol：

- `WorklineRackTask`
- `WorklineRackTaskService`
- `WorklineRackPosition`
- `RackPlacement`
- `ResourceProjectionService.record_rack_arrived_at_workline_position`
- `RuntimeIntent.rack_task_request`
- `SmtRackBinSchedulingService`
- `SmtClassifierPlugin`

- [ ] 若 GitNexus 对任一核心 symbol 返回 HIGH 或 CRITICAL 风险，先把直接调用方、受影响流程和计划调整记录在实现说明中，再继续改代码。
- [ ] 建立测试基线：

```bash
rtk uv run pytest tests/workline_runtime/test_workline_rack_task_service.py \
  tests/workline_runtime/test_workline_rack_position_service.py \
  tests/resource/test_resource_projection_service.py \
  tests/workline_runtime/test_runtime_intent.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/workline_runtime/test_session_resolver.py \
  tests/workline_runtime/test_smt_rack_bin_scheduling_service.py \
  tests/workline_runtime/test_smt_single_layer_rack_full_box_flow.py
```

通过标准：

- 基线结果记录清楚；若失败来自本次改造目标覆盖的旧语义，后续 task 用新的测试替换，不按旧行为修复。

## Task 1：重塑 `workline_rack_tasks` 为低级货架任务账本

目标：去掉满箱交换/补架业务名，把 task model 降到低级货架动作；同一 operation 多 task 通过字段聚合。

- [ ] 修改 `src/app/workline/models/rack_task.py`。
  - `WorklineRackTaskType` 只保留当前低级动作：
    - `MOVE_RACK`
    - `ALLOCATE_AND_MOVE_RACK`
    - `TURN_RACK_SIDE`
  - 删除：
    - `RACK_SUPPLY`
    - `FULL_BOX_EXCHANGE`
    - `MOVE_TO_EMPTY_AREA`
  - `WorklineRackTaskStatus` 保留现有生命周期并新增 `TIMEOUT`：
    - `PLANNED`
    - `REQUESTED`
    - `IN_PROGRESS`
    - `SUCCEEDED`
    - `FAILED`
    - `TIMEOUT`
    - `RECONCILING`
    - `CANCELLED`
  - 新增 operation 字段：
    - `operation_key: str`
    - `operation_type: str`
    - `sequence_no: int`
  - 新增货架/位置字段：
    - `rack_kind: str | None`
    - `source_position_code: str | None`
    - `target_position_code: str | None`
    - `target_position_role: str | None`
  - 新增调度 payload 字段：
    - `actions_json: dict[str, Any]`
  - 删除旧 `position_code` 字段；所有调用改用 `source_position_code` 或 `target_position_code`。
  - 索引约束：
    - `task_key` 唯一。
    - `dispatch_key` 唯一。
    - `(operation_key, sequence_no)` 唯一。
    - `(operation_key, task_status)` 普通索引。
    - `(material_session_id, operation_key)` 普通索引。
    - `(workline_code, target_position_code, task_status)` 普通索引。

- [ ] 更新未发布迁移 `migrations/versions/20260520_1453_083e85d1bf93_add_workline_rack_tasks.py`。
  - 迁移创建的新表字段必须与模型一致。
  - 删除旧 task type 的默认值、注释或约束。
  - 不保留旧字段兼容迁移；开发环境可清理数据。

- [ ] 更新 `src/app/workline/repositories/rack_task_repository.py`。
  - 增加 `get_by_operation_sequence(db, *, operation_key, sequence_no)`。
  - 增加 `list_by_operation_key(db, *, operation_key)`，按 `sequence_no` 升序。
  - 增加 `list_active_by_material_session(db, *, material_session_id)`，active 状态为 `PLANNED/REQUESTED/IN_PROGRESS/RECONCILING`。
  - 增加 `list_active_by_target_position(db, *, workline_code, target_position_code)`。
  - 保留 `get_by_dispatch_key()` 供回调用。

- [ ] 将现有 `WorklineRackTaskService` 重命名为 `WorklineRackTaskLifecycleService`。
  - 执行前使用 GitNexus MCP `rename` 预览并执行重命名，不能用裸文本全局替换。
  - 实例名改为 `workline_rack_task_lifecycle_service`。
  - 更新 `src/app/workline/services/__init__.py` 导出。
  - 更新所有导入方，删除旧 `workline_rack_task_service` 全局实例。

- [ ] 修改生命周期服务创建接口。
  - 新接口名：`record_requested_task(...)`。
  - 入参必须包含 `operation_key`、`operation_type`、`sequence_no`、`task_type`、`task_key`、`dispatch_key`、`target_code`、`request_json`。
  - 幂等规则：
    - `task_key` 命中已有 task 时返回已有 task。
    - 已有 task 的 `operation_key`、`sequence_no`、`dispatch_key` 与本次请求不一致时抛出 `ValueError`，避免同一个幂等键覆盖不同动作。

- [ ] 删除生命周期服务里的旧满箱交换错误码拼接。
  - `FULL_BOX_EXCHANGE_*` 不再出现在 `rack_task_service.py`、`rack_task.py` 和 rack task tests。
  - 回调状态解释只输出低级 task 状态和外部系统原始错误码。

- [ ] 更新测试 `tests/workline_runtime/test_workline_rack_task_service.py`。
  - `test_record_requested_task_creates_low_level_task_idempotently`
  - `test_record_requested_task_rejects_same_task_key_with_different_operation`
  - `test_record_callback_updates_single_task_status_only`
  - `test_record_callback_keeps_operation_incomplete_when_sibling_pending`

通过标准：

- `rtk rg -n "FULL_BOX_EXCHANGE|RACK_SUPPLY|MOVE_TO_EMPTY_AREA|position_code" src/app/workline/models/rack_task.py src/app/workline/services/rack_task_service.py tests/workline_runtime/test_workline_rack_task_service.py` 无业务旧名命中。
- Rack task 单元测试通过。

## Task 2：把位置容量模型贯穿 Workline 与 Resource 投影

目标：位置按容量建模；resource 域允许同一 `(workline_code, position_code)` 存在不超过 `capacity` 的 active rack placement。

- [ ] 修改 `src/app/workline/models/rack_position.py`。
  - 删除 `CheckConstraint("capacity = 1", ...)`。
  - 保留 `capacity > 0` 约束。
  - 更新字段说明：`capacity` 表示该 workline 位置可同时容纳的 rack 数量。
  - 位置角色最小集合：
    - `SMT_CLASSIFIER_SINGLE_RACK_WORK`
    - `SMT_FULL_BOX_EXCHANGE`
    - `SMT_SORTER_QUEUE`
    - `SMT_SORTER_STATION`
    - `SMT_EMPTY_RACK_AREA`

- [ ] 更新未发布迁移 `migrations/versions/20260519_0004_bbaa8662d7fe_add_workline_rack_positions_and_bin_.py`。
  - 删除 `ck_workline_rack_positions_capacity_one`。
  - 保留 `capacity` 正数约束。

- [ ] 修改 `src/app/workline/services/rack_position_service.py`。
  - `require_enabled_position()` 不再拒绝 `capacity != 1`。
  - 新增 `require_position_capacity(db, *, workline_code, position_code) -> int`，所有容量读取统一调用该方法。
  - 缺失/禁用位置仍抛出明确异常。

- [ ] 修改 `src/app/resource/models/resource.py`。
  - 删除 `RackPlacement` 上 active `(workline_code, position_code)` 唯一索引。
  - 保留 active `rack_code` 唯一索引，避免同一货架同时出现在多个位置。
  - 新增非唯一 `(workline_code, position_code, ended_at)` 索引，支撑容量计数查询。

- [ ] 更新 resource 未发布迁移或新增生成迁移。
  - 若现有 resource placement 迁移未发布且可编辑，直接调整该迁移。
  - 若该迁移已被本地数据库应用，执行：

```bash
rtk uv run alembic revision -m "make rack placement position capacity aware"
```

  - 生成后编辑迁移，删除旧 active position unique index 并添加非唯一索引。

- [ ] 修改 `src/app/resource/repositories/resource_repository.py`。
  - 替换或补充 `get_active_by_workline_position()`：
    - `list_active_by_workline_position(db, *, workline_code, position_code) -> list[RackPlacement]`
    - `count_active_by_workline_position(db, *, workline_code, position_code) -> int`
  - 保留 `get_active_by_rack_code()` 保护单 rack 唯一现状。

- [ ] 修改 `src/app/resource/services/projection_service.py`。
  - `record_rack_arrived_at_workline_position()` 到位逻辑：
    - 若同一 `rack_code` 已 active 在同一位置，视为幂等成功。
    - 若同一 `rack_code` active 在其他位置，结束旧 placement 后写入新 placement。
    - 读取目标位置 `capacity`。
    - 统计目标位置 active rack 数。
    - `active_count < capacity` 时允许写入。
    - `active_count >= capacity` 且不是同 rack 幂等到位时，创建 runtime hold，返回 `RECONCILING`。
  - `capacity` 缺失或位置禁用时返回 `RECONCILING`，不静默创建无限容量位置。

- [ ] 更新 `tests/resource/test_resource_projection_service.py`。
  - `test_record_rack_arrived_allows_second_rack_when_position_capacity_two`
  - `test_record_rack_arrived_reconciles_when_capacity_exhausted`
  - `test_record_rack_arrived_is_idempotent_for_same_rack_same_position`
  - `test_record_rack_arrived_moves_same_rack_from_old_position`

- [ ] 更新 `tests/workline_runtime/test_workline_rack_position_service.py`。
  - 覆盖 `capacity=2` 的 enabled position 可被读取。
  - 覆盖禁用位置仍失败。

通过标准：

- 同一工作线位置 capacity=2 时，resource projection 可存在两条 active placement。
- capacity=1 时，第二个不同 rack 到位仍进入 `RECONCILING`。

## Task 3：实现 `WorklineRackOperationService`

目标：operation 服务负责把一个业务操作拆成低级 task，并在同一短事务内完成容量校验、task/outbox 创建、session 等待标记。

- [ ] 新增 `src/app/workline/services/rack_operation_service.py`。
  - 服务名：`WorklineRackOperationService`。
  - 实例名：`workline_rack_operation_service`。
  - 导出位置：`src/app/workline/services/__init__.py`。

- [ ] 定义最小 operation 类型。
  - `REPLACE_CLASSIFIER_WORK_RACK`：粗分机当前工作位换新空箱货架。
  - `MOVE_RACK_TO_POSITION`：将已知货架移动到指定位置。
  - `ALLOCATE_RACK_TO_POSITION`：请求 WMS/RCS 分配指定类型货架并移动到指定位置。
  - `TURN_RACK_SIDE`：请求货架换面。

- [ ] 定义最小 task spec 数据结构。
  - 字段：
    - `sequence_no`
    - `task_type`
    - `rack_code`
    - `rack_kind`
    - `source_position_code`
    - `target_position_code`
    - `target_position_role`
    - `dispatch_key`
    - `target_code`
    - `request_json`
    - `actions_json`
    - `required`

- [ ] 新增 operation 派生状态方法。
  - 方法名：`derive_operation_status(db, *, operation_key)`。
  - 返回状态：
    - `PENDING`：至少一个 required task 仍为 `PLANNED/REQUESTED/IN_PROGRESS`。
    - `SUCCEEDED`：所有 required task 为 `SUCCEEDED` 且 resource projection 确认目标可用。
    - `FAILED`：任一 required task 为 `FAILED/TIMEOUT/CANCELLED`。
    - `RECONCILING`：任一 required task 为 `RECONCILING` 或目标资源投影不满足。
  - 不写 `workline_rack_operations` 表。

- [ ] 新增粗分机换架入口。
  - 方法名：`request_replace_classifier_work_rack(...)`。
  - 入参必须包含：
    - `operation_key`
    - `workline`
    - `session`
    - `work_position_code`
    - `new_rack_kind`
    - `move_out_target_position_role`
    - `supply_target_code`
    - `trace_id`
  - 行为：
    - 若当前工作位有 active rack，创建 `MOVE_RACK` task，`sequence_no=1`。
    - 无当前工作位 rack 时不创建 move-out task。
    - 总是创建 `ALLOCATE_AND_MOVE_RACK` task，`sequence_no=2`；若无 move-out task，仍使用 `sequence_no=2`，保持语义稳定。
    - 两条 task 共享同一个 `operation_key`。
    - 两条 task 在同一 DB 事务里创建，但事务提交后再由 outbox 派发。

- [ ] 实现同 operation 释放容量规则。
  - 读取目标 `work_position_code` active placement 数量。
  - 若创建了 sibling `MOVE_RACK` 且其 `source_position_code == work_position_code`，本 operation 可预占一个释放容量。
  - 可用容量公式：

```text
available_capacity_for_operation =
  position.capacity - active_count + same_operation_release_count - existing_same_operation_supply_count
```

  - `same_operation_release_count` 只能来自本次或已存在同一 `operation_key` 的 `MOVE_RACK` task。
  - 不同 `operation_key` 的 move-out 不能释放本 operation 容量。
  - `available_capacity_for_operation <= 0` 时不创建 supply task，返回明确异常或 blocked decision，由调用方阻塞 session。

- [ ] 保证事务边界。
  - 使用一个短事务完成：
    - lock session 或 business key。
    - 读取当前 rack placement。
    - 读取位置 capacity。
    - 创建 task。
    - 创建 outbox。
    - 写入 `session.context_json["waiting_rack_operation_key"]`。
    - 设置 session wait/block 状态。
  - 禁止在上述事务里执行 HTTP、Celery send_task 或 WMS/RCS SDK 调用。

- [ ] 增加幂等规则。
  - 同一 `operation_key` 重复请求返回已有 task 列表。
  - 已有 task 的 operation 形状与本次请求不一致时抛出 `ValueError`：
    - task 数量不同。
    - required task type 不同。
    - `source_position_code` 或 `target_position_code` 不同。
    - `new_rack_kind` 不同。

- [ ] 新增测试 `tests/workline_runtime/test_workline_rack_operation_service.py`。
  - `test_replace_classifier_work_rack_creates_move_out_and_supply_tasks_with_same_operation_key`
  - `test_replace_classifier_work_rack_without_current_rack_creates_only_supply_task`
  - `test_same_operation_move_out_releases_capacity_for_supply`
  - `test_other_operation_move_out_does_not_release_capacity`
  - `test_repeated_replace_operation_returns_existing_tasks`
  - `test_operation_request_does_not_dispatch_http_inside_db_transaction`
  - `test_derive_operation_status_requires_all_required_tasks_succeeded`
  - `test_derive_operation_status_requires_resource_projection_confirmation`

通过标准：

- 无当前工作位货架时，只创建补入任务。
- 有当前工作位货架时，创建同一 `operation_key` 下两条 task。
- 同一 operation 的释放容量允许补入，不同 operation 的释放容量不允许抢占。
- 测试能证明外部派发不在创建事务内执行。

## Task 4：调整 RuntimeIntent 与 effect，使 session 等待 operation

目标：插件只等待货架 operation，不等待单个 task；effect 能把 operation 请求交给 operation service。

- [ ] 修改 `src/workline_runtime/runtime_intent.py`。
  - 用 `RuntimeIntentKind.RACK_OPERATION_REQUEST` 替换 `RuntimeIntentKind.RACK_TASK_REQUEST`。
  - 新增构造方法 `RuntimeIntent.rack_operation_request(...)`。
  - 必填字段：
    - `operation_type`
    - `operation_key`
    - `target_code`
    - `payload`
    - `timeout_seconds`
  - `idempotency_key` 使用 `operation_key`。
  - 不再要求单个 `task_key`。

- [ ] 修改 `src/workline_runtime/plugin_next.py`。
  - 新增 `rack_operation_request(...)` 便捷方法。
  - 删除插件可调用的 `rack_task_request(...)` 便捷方法；低级 task 只能由 `WorklineRackOperationService` 创建。
  - 同步删除旧 `rack_task_request(...)` 测试和调用。

- [ ] 修改 `src/workline_runtime/runtime_intent_effects.py`。
  - `RACK_OPERATION_REQUEST` 分支调用 `workline_rack_operation_service`。
  - effect 写入 session context：
    - `waiting_rack_operation_key`
    - `rack_operation.operation_key`
    - `rack_operation.status = "PENDING"`
  - 不再写入：
    - `waiting_rack_task_id`
    - `waiting_rack_task_key`
  - outbox 创建后沿用既有 outbox 派发机制。

- [ ] 修改 `src/celery_app/tasks/workline.py` 中 intent 分类。
  - `RACK_OPERATION_REQUEST` 与外部请求一样进入 dispatch prepare / outbox 派发路径。
  - 保持派发在事务提交后执行。

- [ ] 更新 `tests/workline_runtime/test_runtime_intent.py`。
  - `test_rack_operation_request_intent_describes_rack_operation`
  - `test_rack_operation_request_requires_operation_key_target_payload_and_timeout`

- [ ] 更新 `tests/workline_runtime/test_runtime_intent_effects.py`。
  - `test_rack_operation_request_creates_operation_tasks_and_waits_by_operation_key`
  - `test_rack_operation_request_does_not_store_waiting_rack_task_key`

- [ ] 更新 `tests/workline_runtime/test_plugin_next.py`。
  - 覆盖 `PluginNext().rack_operation_request(...)`。
  - 删除旧 `FULL_BOX_EXCHANGE` 和 `RACK_SUPPLY` 的 plugin-next 测试期望。

通过标准：

- `session.context_json` 中只有 operation 等待键，没有 rack task 等待键。
- runtime effect 不直接拼低级 task；task 拆分由 `WorklineRackOperationService` 完成。

## Task 5：让粗分机插件按 operation 边界换架

目标：粗分机插件的边界收敛为“料格分配失败时请求换架，operation 成功后恢复当前 material session 继续分配料格”。

- [ ] 修改 `src/app/resource/services/smt_rack_bin_scheduling_service.py`。
  - 将 `rack_supply_request` 概念替换为 `rack_operation_request`。
  - 决策 kind 最小集合：
    - `ALLOCATED`
    - `RACK_OPERATION_REQUIRED`
    - `BLOCKED`
  - `RACK_OPERATION_REQUIRED` payload 必须包含：
    - `operation_type = "REPLACE_CLASSIFIER_WORK_RACK"`
    - `operation_key`
    - `work_position_code`
    - `new_rack_kind`
    - `move_out_target_position_role`
    - `target_code`
    - `reason_code`
  - 删除 `ACTIVE_RACK_TEMPLATE_SESSION_LOOKBACK` 或任何按历史数量回看选择 active rack 的逻辑。
  - active rack 来源只能来自 resource 当前投影。

- [ ] 修改 `src/workline_plugins/smt_classifier/plugin.py`。
  - 当调度结果为 `RACK_OPERATION_REQUIRED`：
    - 发出 `ctx.next.rack_operation_request(...)`。
    - session context 写入 `waiting_rack_operation_key`。
    - 当前 material session 进入等待，不结束。
  - 当 session 已有 `waiting_rack_operation_key`：
    - 调用 operation 派生状态读取。
    - `SUCCEEDED` 后清除等待键并重新执行料格分配。
    - `PENDING` 时继续等待。
    - `FAILED/RECONCILING` 时 block 当前 session，写明 reason_code。
  - 删除 `waiting_rack_task_id` 和 `waiting_rack_task_key` 分支。

- [ ] 修改和新增粗分机插件测试。
  - `tests/workline_runtime/test_smt_rack_bin_scheduling_service.py`
    - `test_no_cell_returns_rack_operation_required_from_resource_projection`
    - `test_no_active_rack_requests_only_allocate_and_move`
    - `test_active_rack_requests_replace_classifier_work_rack_operation`
    - `test_pending_rack_operation_blocks_duplicate_operation_request`
  - `tests/workline_runtime/test_smt_single_layer_rack_full_box_flow.py`
    - 将旧 `RACK_SUPPLY_REQUIRED` 断言替换为 `RACK_OPERATION_REQUIRED`。
    - 覆盖 operation 成功后 session 重新分配料格。
    - 覆盖 operation 失败后 session 不继续分配料格。
  - `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`
    - 更新 runtime intent kind 与 payload 断言。

通过标准：

- 粗分机插件没有满箱交换职责。
- 粗分机插件没有“把满箱从单层货架交换到五层货架”的任务创建代码。
- 粗分机插件只在料格分配失败时请求货架 operation，并在 operation 成功后恢复同一 material session。

## Task 6：回调归属与 operation 派生恢复

目标：外部回调仍更新单 task，但 material session 归属和恢复必须按 operation 派生状态执行。

- [ ] 修改 `src/workline_runtime/session_resolver.py`。
  - 外部 HTTP 回调按 `dispatch_key` 找到 `WorklineRackTask`。
  - 通过 `task.operation_key` 找到等待该 operation 的 open material session。
  - 如果找不到等待 session，复用原 trace/session 关联兜底，但必须记录可观测日志或 timeline payload。

- [ ] 修改 `src/app/workline/services/rack_task_service.py` 的回调入口。
  - 单 task 状态更新后调用 `WorklineRackOperationService.derive_operation_status(...)`。
  - 当 operation 派生状态为 `SUCCEEDED`：
    - 清除 session context 的 `waiting_rack_operation_key`。
    - 清除 `rack_operation.status` 或置为 `SUCCEEDED`。
    - 将 session 恢复为可继续运行的状态。
  - 当派生状态为 `PENDING`：
    - session 继续等待。
  - 当派生状态为 `FAILED/RECONCILING`：
    - session block，写入 `reason_code` 和外部错误信息。

- [ ] 统一外部回调状态映射。
  - 成功事件映射到 `SUCCEEDED`。
  - 进行中事件映射到 `IN_PROGRESS`。
  - 失败事件映射到 `FAILED`。
  - 超时事件映射到 `TIMEOUT`。
  - 无法确认资源投影的事件映射到 `RECONCILING`。
  - `callback_json` 保留原始 payload。

- [ ] 修改 `src/app/callback/services/callback_ingress_service.py`。
  - 删除 `WMS_FULL_BOX_EXCHANGE_RESULT` 专属必填字段逻辑。
  - Rack task 回调统一按 `dispatch_key`、`task_status` 或外部 WMS/RCS 状态解析。
  - 必填字段最小集合：
    - `dispatch_key`
    - `status` 或外部等价状态字段
    - `occurred_at` 或回调接收时间兜底

- [ ] 更新回调测试。
  - `tests/workline_runtime/test_session_resolver.py`
    - `test_external_http_callback_resolves_session_by_rack_operation_key`
    - `test_external_http_callback_does_not_resume_session_until_all_operation_tasks_succeeded`
  - `tests/api/test_callback_api.py`
    - 删除 full-box exchange callback 断言。
    - 增加通用 rack task callback 成功、失败、重复回调场景。

通过标准：

- 第一条 sibling task 成功不会恢复 session。
- 所有 required task 成功且 resource projection 满足后才恢复 session。
- 任一 sibling task 失败或超时会阻塞 session。

## Task 7：清理过期业务模型、命名和测试残留

目标：未发布系统不保留旧满箱交换插件语义，不保留旧补架单 task 语义。

- [ ] 全局搜索并删除或重命名旧业务词。

```bash
rtk rg -n "FULL_BOX_EXCHANGE|SMT_FULL_BOX_EXCHANGE|RACK_SUPPLY|SMT_RACK_SUPPLY|waiting_rack_task|rack_supply" src tests docs
```

- [ ] 对命中项逐一处理：
  - 代码路径改为 `RACK_OPERATION`、`REPLACE_CLASSIFIER_WORK_RACK` 或低级 task type。
  - 测试 fixture 改成 operation payload。
  - mock callback 改成通用 rack task callback。
  - 文档中属于 `2026-05-19` 后续分析文档的旧词不纳入本次修改。

- [ ] 删除不再存在的“满箱交换插件”入口、测试和 mock。
  - 若只有 mock 文件仍提供粗分机 rack operation 回调模拟，重命名为 rack operation mock。
  - 若 mock 仍含满箱交换实体，删除实体字段。

- [ ] 更新 `docs/superpowers/specs/2026-05-20-rack-operation-service-design.md`。
  - 只更新与本次实现保持一致的命名、状态和验收规则。
  - 不新增 2026-05-19 两份文档的实现承诺。

通过标准：

- `src/` 下无 `FULL_BOX_EXCHANGE`。
- `src/` 下无 `waiting_rack_task`。
- `src/` 下旧 `RACK_SUPPLY` 只允许出现在历史迁移注释中；若迁移也可破坏性调整，则完全删除。

## Task 8：端到端验证与质量门禁

目标：用最小但覆盖关键风险的测试证明新边界成立。

- [ ] 运行格式化和 lint。

```bash
rtk uv run ruff format src/app/workline src/app/resource src/workline_runtime src/workline_plugins tests/workline_runtime tests/resource tests/api
rtk uv run ruff check src/app/workline src/app/resource src/workline_runtime src/workline_plugins tests/workline_runtime tests/resource tests/api
```

- [ ] 运行聚焦测试。

```bash
rtk uv run pytest tests/workline_runtime/test_workline_rack_task_service.py \
  tests/workline_runtime/test_workline_rack_operation_service.py \
  tests/workline_runtime/test_workline_rack_position_service.py \
  tests/resource/test_resource_projection_service.py \
  tests/workline_runtime/test_runtime_intent.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/workline_runtime/test_plugin_next.py \
  tests/workline_runtime/test_session_resolver.py \
  tests/workline_runtime/test_smt_rack_bin_scheduling_service.py \
  tests/workline_runtime/test_smt_single_layer_rack_full_box_flow.py \
  tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py \
  tests/api/test_callback_api.py
```

- [ ] 运行全量 workline/resource 相关测试。

```bash
rtk uv run pytest tests/workline_runtime tests/resource tests/integration/workline_plugins tests/api/test_callback_api.py
```

- [ ] 运行 Alembic 检查。

```bash
rtk uv run alembic upgrade head
```

- [ ] 运行架构违规检查。

```bash
rtk grep -r "from sqlalchemy import select" src/app/*/v1/ || true
rtk grep -r "db.execute(" src/app/*/v1/ || true
```

- [ ] 提交前运行 GitNexus MCP `detect_changes`。
  - 参数：`repo="wes_backend"`，`scope="all"`。

通过标准：

- Ruff format/check 通过。
- 聚焦测试通过。
- Alembic upgrade head 通过。
- API 层无直接数据库访问。
- GitNexus `detect_changes` 影响范围与本计划文件列表一致。

## 风险与验收

关键风险：

- 只改 `workline_rack_positions.capacity` 而不改 `resource_rack_placements` 唯一索引，会导致 capacity>1 在业务层允许、数据库层拒绝。
- session 继续等待单 task，会在第一条 sibling task 成功后提前恢复。
- operation 创建事务里直接派发 WMS/RCS，会在长时间外部任务期间持有 DB 锁。
- 不区分同 operation 释放容量，会让其他 operation 抢占尚未物理释放的位置。

验收标准：

- 粗分机当前工作位没有货架时，只请求补入新空箱货架。
- 粗分机当前工作位有货架时，同时请求旧货架移出和新空箱货架补入，两条 task 共享一个 `operation_key`。
- 第一条 task 成功不会恢复 material session。
- sibling task 全部成功并且 resource projection 确认目标位置可用后，material session 才恢复并重新分配料格。
- capacity=2 的位置允许两个 active rack placement。
- capacity=1 的位置在无同 operation 释放容量时拒绝第二个不同 rack 到位。
- `workline_sessions` 不保存货架生命周期职责，只保存 material session 当前等待哪个 `operation_key`。
- `resource` 域只保存资源现状，不保存 operation 生命周期。

## 推荐提交拆分

- `refactor(workline): reshape rack task ledger around operation key`
- `refactor(resource): make rack placement capacity aware`
- `feat(workline): add rack operation service for classifier rack replacement`
- `refactor(runtime): wait rack flows by operation key`
- `refactor(smt): request rack operation from classifier bin allocation`
- `test(workline): cover rack operation capacity and callback lifecycle`
