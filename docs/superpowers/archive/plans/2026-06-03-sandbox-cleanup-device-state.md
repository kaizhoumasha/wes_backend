# 沙箱清理设备运行态修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 SIMULATION 工作线执行“清理沙箱数据”后，绑定设备残留 `ERROR/RUNNING/current_command_id` 导致下一轮沙箱调试仍被阻断的问题。

**Architecture:** 保持 API 和 Service 入口不变，在 `SandboxCleanupRepository._clear_cyclic_refs` 的事务内删除前清理阶段增加设备运行态投影修正。先断开待删除沙箱命令的设备指针，再只把可由 sandbox cleanup 接管的 `ERROR/RUNNING/IDLE+current_command_id` 恢复为 `IDLE`；保留 `MAINTENANCE/OFFLINE` 状态但清掉指向待删除命令的悬空指针。

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy AsyncSession, pytest, Ruff, GitNexus, RTK.

---

## 实施验证状态

日期：2026-06-03。

结论：本计划已完成实现、交替代码评审与验收，并已合入 `develop`。修复提交为 `3794754 fix(workline): 清理沙箱设备残留运行态`，范围只包含沙箱清理仓储和对应回归测试。

已验证通过：

- `rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py -q`，7 passed。
- `rtk uv run ruff check src/app/workline/repositories/sandbox_cleanup_repository.py tests/workline_runtime/test_sandbox_cleanup_service.py`。
- `rtk uv run ruff format --check src/app/workline/repositories/sandbox_cleanup_repository.py tests/workline_runtime/test_sandbox_cleanup_service.py`。

已验收行为：

- SIMULATION 工作线执行沙箱清理后，残留 `ERROR/RUNNING` 设备运行态恢复为 `IDLE`，`current_command_id` 和 `error_code` 清空。
- `RuntimeQueryService` 工作线汇总中的 `error_device_count` 清零。
- 指向待删除沙箱命令的 `MAINTENANCE/OFFLINE` 设备只清空 `current_command_id`，不覆盖人工维护或离线状态。
- AUTO 对照工作线设备不受影响。

未完成 / 风险：

- `rtk npx gitnexus detect-changes` 在当前环境返回 GitNexus CLI 内部错误：`Cannot destructure property 'package' of 'node.target' as it is null`。实现前影响分析曾复核 `_clear_cyclic_refs` 上游风险为 LOW，直接影响 `execute_cleanup`。
- pytest 仍输出既有 SQLAlchemy 外键循环 drop warning，不影响本次测试通过结论。

## Scope Check

本计划只覆盖后端沙箱清理的设备运行态投影修复。它不新增前端能力，不改 API 契约，不改数据库迁移，不调整工作线 cleanup 后 `STOPPED/READY` 语义。Scope 与已评审设计文档 `docs/superpowers/archive/specs/2026-06-03-sandbox-cleanup-device-state-design.md` 一致，适合作为单个实现计划。

## File Map

- Modify: `src/app/workline/repositories/sandbox_cleanup_repository.py`
  - 责任：在删除沙箱运行图前断开设备命令引用，并按状态决策表修正 SIMULATION 工作线绑定设备运行态。
- Modify: `tests/workline_runtime/test_sandbox_cleanup_service.py`
  - 责任：补充回归测试与边界测试，覆盖 `ERROR/RUNNING/MAINTENANCE/OFFLINE`、AUTO 对照线、运行汇总 `error_device_count`。

不修改：

- `src/app/workline/services/sandbox_cleanup_service.py`
- `src/app/workline/v1/operation.py`
- 前端文件
- Alembic migration

---

### Task 1: 补充失败回归测试

**Files:**
- Modify: `tests/workline_runtime/test_sandbox_cleanup_service.py`

- [ ] **Step 1: 确认当前工作区和设计文档状态**

Run:

```bash
rtk git status --short
rtk sed -n '1,240p' docs/superpowers/archive/specs/2026-06-03-sandbox-cleanup-device-state-design.md
```

Expected:

- 能看到已评审的设计文档。
- 现有 `AGENTS.md` / `CLAUDE.md` 改动不要纳入本任务提交。

- [ ] **Step 2: 运行 GitNexus 影响分析**

Run:

```bash
rtk npx gitnexus impact 'Method:src/app/workline/repositories/sandbox_cleanup_repository.py:SandboxCleanupRepository._clear_cyclic_refs#3' --direction upstream
```

Expected:

- 风险为 `LOW`，直接影响 `execute_cleanup`。
- 如果 GitNexus 提示索引 stale，先运行：

```bash
rtk npx gitnexus analyze
```

然后重复 impact 命令。若风险变为 HIGH 或 CRITICAL，停止并向用户汇报。

- [ ] **Step 3: 给执行清理 fixture 增加沙箱残留设备**

在 `tests/workline_runtime/test_sandbox_cleanup_service.py` 的 `_create_executable_cleanup_graph` 中，现有 `sandbox_device` 和 `auto_device` 后新增 4 个 SIMULATION 工作线绑定设备：

```python
error_orphan_device = Device(... device_status=DeviceStatus.ERROR, error_code="SANDBOX_LEFTOVER_ERROR")
running_orphan_device = Device(... device_status=DeviceStatus.RUNNING, error_code=None)
maintenance_device = Device(... device_status=DeviceStatus.MAINTENANCE, maintenance_mode=True, error_code="MAINTENANCE")
offline_device = Device(... device_status=DeviceStatus.OFFLINE, error_code="HEARTBEAT_TIMEOUT")
```

约定：

- `error_orphan_device.current_command_id` 保持 `None`，复现用户看到的残留 `ERROR`。
- `running_orphan_device.current_command_id` 保持 `None`，覆盖残留忙碌态。
- `maintenance_device.current_command_id` 和 `offline_device.current_command_id` 在创建沙箱命令后指向待删命令，用来验证只清指针、不改状态。
- 所有新增设备 `work_line_id` 必须是 `simulation_workline.id`。

- [ ] **Step 4: 给 MAINTENANCE/OFFLINE 设备创建待删命令**

在同一个 fixture 中，为 `maintenance_device` 和 `offline_device` 分别创建 `DeviceCommand`，字段沿用现有 `sandbox_command` 的模式：

```python
maintenance_command = DeviceCommand(... workline_id=simulation_workline.id, session_id_int=sandbox_session.id)
offline_command = DeviceCommand(... workline_id=simulation_workline.id, session_id_int=sandbox_session.id)
```

然后设置：

```python
maintenance_device.current_command_id = maintenance_command.id
offline_device.current_command_id = offline_command.id
```

这些命令必须进入 `selection.commands`，这样旧实现会删除命令但没有完整处理保留状态设备的不变量。

- [ ] **Step 5: 把新增 ID 放入 fixture 返回值**

在 fixture 返回字典中增加：

```python
"error_orphan_device_id": error_orphan_device.id,
"running_orphan_device_id": running_orphan_device.id,
"maintenance_device_id": maintenance_device.id,
"offline_device_id": offline_device.id,
"maintenance_command_id": maintenance_command.id,
"offline_command_id": offline_command.id,
```

同时将 `maintenance_command` 和 `offline_command` 纳入一个可单独断言删除的列表，例如：

```python
"extra_sandbox_command_ids": [maintenance_command.id, offline_command.id]
```

- [ ] **Step 6: 扩展现有执行清理测试断言**

在 `test_cleanup_workline_deletes_sandbox_runtime_graph_and_resets_runtime_state` 中，保留已有断言，并新增以下断言：

```python
error_orphan_device = await db_session.get(Device, graph["error_orphan_device_id"])
assert error_orphan_device.device_status == DeviceStatus.IDLE
assert error_orphan_device.current_command_id is None
assert error_orphan_device.error_code is None

running_orphan_device = await db_session.get(Device, graph["running_orphan_device_id"])
assert running_orphan_device.device_status == DeviceStatus.IDLE
assert running_orphan_device.current_command_id is None
assert running_orphan_device.error_code is None

maintenance_device = await db_session.get(Device, graph["maintenance_device_id"])
assert maintenance_device.device_status == DeviceStatus.MAINTENANCE
assert maintenance_device.current_command_id is None
assert maintenance_device.error_code == "MAINTENANCE"
assert maintenance_device.maintenance_mode is True

offline_device = await db_session.get(Device, graph["offline_device_id"])
assert offline_device.device_status == DeviceStatus.OFFLINE
assert offline_device.current_command_id is None
assert offline_device.error_code == "HEARTBEAT_TIMEOUT"
```

并断言额外沙箱命令已删除：

```python
for command_id in graph["extra_sandbox_command_ids"]:
    assert await db_session.get(DeviceCommand, command_id) is None
```

- [ ] **Step 7: 增加运行汇总可见性断言**

在测试文件 imports 中加入：

```python
from src.app.workline.services.runtime_query_service import RuntimeQueryService
```

在 cleanup 后构造同一工作线剩余设备列表，并断言用户可见 `ERROR` 计数清零。极短示例：

```python
devices = (
    await db_session.execute(select(Device).where(Device.work_line_id == simulation_workline_id))
).scalars().all()
summary = RuntimeQueryService()._build_workline_summary(refreshed_workline, devices, [])
assert summary.error_device_count == 0
```

注意：因为本设计保留 `OFFLINE/MAINTENANCE`，这里只断言 `error_device_count`，不要断言 offline/maintenance 计数为 0。

- [ ] **Step 8: 运行测试确认失败**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py::test_cleanup_workline_deletes_sandbox_runtime_graph_and_resets_runtime_state -q
```

Expected:

- FAIL。
- 失败点应显示 `error_orphan_device.device_status` 仍为 `ERROR`，或 `running_orphan_device.device_status` 仍为 `RUNNING`。

如果失败来自 fixture 外键、枚举字段或测试构造错误，先修正测试构造，不进入实现。

---

### Task 2: 实现两阶段设备运行态清理

**Files:**
- Modify: `src/app/workline/repositories/sandbox_cleanup_repository.py`
- Test: `tests/workline_runtime/test_sandbox_cleanup_service.py`

- [ ] **Step 1: 在仓储中增加设备状态选择集合**

在 `src/app/workline/repositories/sandbox_cleanup_repository.py` 中，为可恢复状态定义小型局部集合，位置靠近 `ID_CHUNK_SIZE`：

```python
_SANDBOX_RESETTABLE_DEVICE_STATUSES = (DeviceStatus.ERROR, DeviceStatus.RUNNING)
_SANDBOX_PRESERVED_DEVICE_STATUSES = (DeviceStatus.MAINTENANCE, DeviceStatus.OFFLINE)
```

目的：

- `ERROR/RUNNING` 可由 cleanup 恢复为 `IDLE`。
- `MAINTENANCE/OFFLINE` 状态保留。

- [ ] **Step 2: 拆分 `_clear_cyclic_refs` 中的设备清理职责**

在 `_clear_cyclic_refs` 中保留现有 RuntimeHold、SystemOutbox、WorklineSession 逻辑。把设备相关逻辑替换为两个私有 helper 调用：

```python
await self._clear_deleted_command_device_refs(db, workline_id=workline_id, selection=selection)
await self._reset_sandbox_bound_device_runtime_state(db, workline_id=workline_id)
```

这两个 helper 都放在 `SandboxCleanupRepository` 类内，靠近 `_clear_cyclic_refs`，保持仓储职责集中。

- [ ] **Step 3: 实现待删命令指针清理 helper**

新增 `_clear_deleted_command_device_refs`，行为：

- 遍历 `selection.commands` chunks。
- 对 `Device.work_line_id == workline_id` 且 `Device.current_command_id in command_ids` 的行清空 `current_command_id`。
- 不在这一步修改 `device_status/error_code/maintenance_mode`。

关键 SQL 形态：

```python
update(Device).where(
    device_columns.work_line_id == workline_id,
    device_columns.current_command_id.in_(command_ids),
).values(current_command_id=None)
```

这样 `MAINTENANCE/OFFLINE` 指向待删命令时不会留下悬空指针，但状态仍被保留。

- [ ] **Step 4: 实现可恢复设备运行态 helper**

新增 `_reset_sandbox_bound_device_runtime_state`，行为：

- 只处理 `Device.work_line_id == workline_id`。
- 只恢复非 `MAINTENANCE/OFFLINE` 的设备。
- 满足任一条件时恢复为 `IDLE`：
  - `device_status in (ERROR, RUNNING)`
  - `current_command_id is not None`
- 恢复时设置：
  - `device_status=DeviceStatus.IDLE`
  - `current_command_id=None`
  - `error_code=None`
  - `maintenance_mode=False`

关键 SQL 条件形态：

```python
update(Device).where(
    device_columns.work_line_id == workline_id,
    device_columns.device_status.notin_(_SANDBOX_PRESERVED_DEVICE_STATUSES),
    or_(
        device_columns.device_status.in_(_SANDBOX_RESETTABLE_DEVICE_STATUSES),
        device_columns.current_command_id.isnot(None),
    ),
).values(...)
```

注意：

- 本文件已导入 `or_`，可复用。
- 不要清理其它工作线设备。
- 不要把 `OFFLINE/MAINTENANCE` 改为 `IDLE`。

- [ ] **Step 5: 运行目标测试确认通过**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py::test_cleanup_workline_deletes_sandbox_runtime_graph_and_resets_runtime_state -q
```

Expected:

- PASS。

- [ ] **Step 6: 运行完整沙箱清理测试**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py -q
```

Expected:

- PASS。
- 非 SIMULATION 拒绝、错误 confirmation 不删除数据、预览计数仍通过。

- [ ] **Step 7: 运行格式与 lint**

Run:

```bash
rtk uv run ruff check src/app/workline/repositories/sandbox_cleanup_repository.py tests/workline_runtime/test_sandbox_cleanup_service.py
rtk uv run ruff format --check src/app/workline/repositories/sandbox_cleanup_repository.py tests/workline_runtime/test_sandbox_cleanup_service.py
```

Expected:

- 两条命令都 PASS。

- [ ] **Step 8: 提交前 GitNexus 变更检测**

Run:

```bash
rtk npx gitnexus detect-changes
```

Expected:

- 变更范围只包括 `SandboxCleanupRepository` 和 `test_sandbox_cleanup_service.py` 中的测试符号。
- 如果返回 stale，先运行 `rtk npx gitnexus analyze` 后重试。

- [ ] **Step 9: 检查最终 diff**

Run:

```bash
rtk git diff -- src/app/workline/repositories/sandbox_cleanup_repository.py tests/workline_runtime/test_sandbox_cleanup_service.py
rtk git status --short
```

Expected:

- diff 只包含本计划两份实现文件。
- `AGENTS.md`、`CLAUDE.md` 和已评审 spec 若仍在工作区，不要纳入本次实现提交，除非用户另外要求。

---

## Final Verification

完整验证命令：

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py -q
rtk uv run ruff check src/app/workline/repositories/sandbox_cleanup_repository.py tests/workline_runtime/test_sandbox_cleanup_service.py
rtk uv run ruff format --check src/app/workline/repositories/sandbox_cleanup_repository.py tests/workline_runtime/test_sandbox_cleanup_service.py
rtk npx gitnexus detect-changes
```

验收信号：

- 清理后 SIMULATION 绑定设备的残留 `ERROR` 被恢复为 `IDLE`。
- `RuntimeQueryService._build_workline_summary(...).error_device_count == 0`。
- `MAINTENANCE/OFFLINE` 状态不被自动覆盖，但不再保留指向已删除沙箱命令的 `current_command_id`。
- AUTO 对照工作线设备保持原状态。

## Commit Guidance

实现完成并通过验证后，建议提交：

```bash
rtk git add src/app/workline/repositories/sandbox_cleanup_repository.py tests/workline_runtime/test_sandbox_cleanup_service.py
rtk git commit -m "fix(workline): 清理沙箱设备残留运行态"
```

不要把 `AGENTS.md`、`CLAUDE.md` 或设计文档一起提交，除非用户明确要求。
