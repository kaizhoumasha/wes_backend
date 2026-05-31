# Runtime BLOCK 会话人工挂起持久化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `RuntimeIntent.BLOCK` 只写 timeline、未把 Session 持久化为 `MANUAL_HOLD` 的状态不一致问题。

**Architecture:** 沿用已落地的等待态和完成态显式持久化模式，在 `WorklineSessionRepository` 增加 BLOCK 专用的 Session 更新方法，并由 `RuntimeIntentEffectApplier._apply_block()` 调用。该修复只收敛 Session 状态写回，不改变插件决策、命令状态、inbox 流程或设备协议。

**Tech Stack:** Python 3.13, SQLAlchemy async, FastAPI domain service/repository pattern, pytest, Ruff, GitNexus.

---

## Scope Check

本计划只覆盖一个状态持久化缺口：`RuntimeIntent.BLOCK -> SessionStatus.MANUAL_HOLD`。不拆分子项目。

## File Structure

- Modify: `src/app/workline/repositories/session_repository.py`
  - 增加 `persist_manual_hold(...)`，负责原子更新 `workline_sessions`。
- Modify: `src/workline_runtime/runtime_intent_effects.py`
  - 在 `_apply_block()` 中调用 repository 显式持久化。
- Test: `tests/workline_runtime/test_runtime_intent_effects.py`
  - 增强 BLOCK intent 测试，证明 `db.execute` 被调用且等待字段清空。
- Runtime verification: 容器内 API + Celery + PG
  - 复测 `MEASUREMENT_REEL` Result payload invalid 场景，确认 Session 落为 `MANUAL_HOLD`。

## Task 1: 写失败测试

**Files:**
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`

- [ ] **Step 1: 修改 BLOCK intent 测试**

在 `test_block_intent_holds_session_without_command_creation` 中保留现有断言，并新增显式持久化断言：

```python
db.execute.assert_awaited_once()
```

该测试已有以下关键输入：

```python
RuntimeIntent.block(
    scope=BlockScope.MATERIAL,
    reason_code="MATERIAL_BLOCKED",
    message="物料需要人工处理",
    suggested_action="检查标签",
    payload={"evidence_key": "EVD-1234"},
)
```

继续保留以下行为断言：

- `session.status == "MANUAL_HOLD"`
- `session.current_wait_type is None`
- `session.awaiting_command_id is None`
- `session.failure_domain == "MATERIAL"`
- `session.failure_code == "MATERIAL_BLOCKED"`
- timeline payload 包含 `suggested_action` 和 evidence

- [ ] **Step 2: 运行失败测试**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_runtime_intent_effects.py::test_block_intent_holds_session_without_command_creation -q
```

Expected:

- FAIL
- 失败原因应为 `db.execute` 未被 await。

## Task 2: 增加 SessionRepository 持久化方法

**Files:**
- Modify: `src/app/workline/repositories/session_repository.py`

- [ ] **Step 1: 增加 `persist_manual_hold(...)`**

在 `WorklineSessionRepository` 中新增方法，签名约定：

```python
async def persist_manual_hold(
    self,
    db: AsyncSession,
    *,
    session_id: int,
    occurred_at: Any,
    failure_domain: str | None,
    failure_code: str | None,
    failure_message: str | None,
) -> None:
```

该方法使用 `update(WorklineSession)`，更新字段约定：

- `status=SessionStatus.MANUAL_HOLD`
- `current_wait_type=None`
- `waiting_since=None`
- `deadline_at=None`
- `current_wait_timeout_seconds=None`
- `awaiting_command_id=None`
- `ended_at=None`
- `failure_domain=failure_domain`
- `failure_code=failure_code`
- `failure_message=failure_message`

`occurred_at` 当前不写入 Session 字段，仅保留在签名中表达状态发生时间，便于后续如需落 `updated_at` 或审计字段时扩展；实现中使用 `_ = occurred_at` 避免 lint 噪声。

- [ ] **Step 2: 运行 repository 相关静态检查**

Run:

```bash
rtk uv run ruff check src/app/workline/repositories/session_repository.py
rtk uv run ruff format --check src/app/workline/repositories/session_repository.py
```

Expected:

- 两条命令均通过。

## Task 3: 在 Runtime BLOCK 路径调用持久化

**Files:**
- Modify: `src/workline_runtime/runtime_intent_effects.py`

- [ ] **Step 1: 修改 `_apply_block()`**

在 `_apply_block()` 中，保留现有 lifecycle 和 failure 字段赋值，然后在写 timeline 前调用：

```python
await WorklineSessionRepository().persist_manual_hold(
    ctx["db"],
    session_id=resolve_required_pk(session, "session"),
    occurred_at=ctx["now"],
    failure_domain=session.failure_domain,
    failure_code=session.failure_code,
    failure_message=session.failure_message,
)
```

需要引入：

- `WorklineSessionRepository`
- 已存在的 `resolve_required_pk`

调用顺序要求：

1. `manual_hold(...)`
2. 设置 `failure_domain/failure_code/failure_message`
3. `persist_manual_hold(...)`
4. `_emit_timeline(...)`

- [ ] **Step 2: 运行失败测试并确认转绿**

Run:

```bash
rtk uv run pytest tests/workline_runtime/test_runtime_intent_effects.py::test_block_intent_holds_session_without_command_creation -q
```

Expected:

- PASS

## Task 4: 回归测试与质量检查

**Files:**
- Test: `tests/workline_runtime/test_runtime_intent_effects.py`
- Test: `tests/workline_runtime/test_orchestrator_write_back_service.py`
- Test: `tests/workline_runtime/test_workline_operation_service.py`
- Test: `tests/workline_runtime/test_runtime_reconciliation_service.py`

- [ ] **Step 1: 运行 workline runtime 回归**

Run:

```bash
rtk uv run pytest \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/workline_runtime/test_orchestrator_write_back_service.py \
  tests/workline_runtime/test_workline_operation_service.py \
  tests/workline_runtime/test_runtime_reconciliation_service.py \
  -q
```

Expected:

- 全部通过。

- [ ] **Step 2: 运行 Ruff**

Run:

```bash
rtk uv run ruff check \
  src/app/workline/repositories/session_repository.py \
  src/workline_runtime/runtime_intent_effects.py \
  tests/workline_runtime/test_runtime_intent_effects.py

rtk uv run ruff format --check \
  src/app/workline/repositories/session_repository.py \
  src/workline_runtime/runtime_intent_effects.py \
  tests/workline_runtime/test_runtime_intent_effects.py
```

Expected:

- check 通过
- format check 通过

## Task 5: 容器内复测业务场景

**Files:**
- No source file changes.

- [ ] **Step 1: 重启 API 与 worker**

Run:

```bash
rtk docker restart wes_api_dev wes_backend-celery_worker-1 wes_celery_beat_dev
```

Expected:

- 三个容器重新启动。

- [ ] **Step 2: 等待健康状态**

Run:

```bash
rtk docker inspect -f '{{.State.Health.Status}}' wes_api_dev
rtk docker inspect -f '{{.State.Health.Status}}' wes_backend-celery_worker-1
rtk docker inspect -f '{{.State.Health.Status}}' wes_celery_beat_dev
```

Expected:

- 均为 `healthy`。

- [ ] **Step 3: 复测 payload invalid 场景**

触发与 `b09b133beac18408` 同类的 `MEASUREMENT_REEL` Result payload invalid 场景：

1. 发送 `SCAN_COMPLETED`。
2. 对 `MEASUREMENT_REEL` 命令 ACK。
3. 提交 Result，payload 保持缺少有效顶层 `reel_diameter/reel_thickness`，触发 `ROUGH_SORTER_MEASUREMENT_PAYLOAD_INVALID`。

Expected:

- `COMMAND_RESULT` inbox 为 `PROCESSED`
- 命令为 `COMPLETED`
- timeline 存在 `MANUAL_HOLD`
- Session 为 `MANUAL_HOLD`
- `current_wait_type / awaiting_command_id / deadline_at` 均为空

- [ ] **Step 4: 查询 PG 验证最终状态**

Run:

```bash
rtk docker exec -i wes_postgres_dev psql -U wes_user -d wes_db -P pager=off -c "
select id, session_code, business_key, status, current_wait_type, awaiting_command_id, deadline_at, failure_code, failure_message
from wes_biz.workline_sessions
where workline_id = 45
order by id desc
limit 1;
"
```

Expected:

- `status = MANUAL_HOLD`
- 等待字段为空
- `failure_code = ROUGH_SORTER_MEASUREMENT_PAYLOAD_INVALID`

## Task 6: 提交

**Files:**
- Modify: `src/app/workline/repositories/session_repository.py`
- Modify: `src/workline_runtime/runtime_intent_effects.py`
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`

- [ ] **Step 1: GitNexus 变更检测**

Run:

```bash
gitnexus_detect_changes(scope="all")
```

Expected:

- risk 不高于 `low` 或 `medium`
- affected processes 符合 Runtime BLOCK 状态写回范围

- [ ] **Step 2: 检查 diff**

Run:

```bash
rtk git diff --check
rtk git status --short
```

Expected:

- 无空白错误
- 只有本计划涉及文件有变更

- [ ] **Step 3: 提交**

Run:

```bash
rtk git add \
  src/app/workline/repositories/session_repository.py \
  src/workline_runtime/runtime_intent_effects.py \
  tests/workline_runtime/test_runtime_intent_effects.py

rtk git commit -m "fix(workline): 持久化 BLOCK 人工挂起态"
```

Expected:

- 生成一个修复提交。

## Self-Review

- Spec coverage：覆盖 BLOCK → MANUAL_HOLD、等待字段清空、failure 字段、timeline 保持、测试和容器复测。
- Placeholder scan：无未完成标记。
- Type consistency：使用现有 `WorklineSessionRepository`、`RuntimeIntentEffectApplier._apply_block()`、`resolve_required_pk()`、`SessionStatus.MANUAL_HOLD`。
