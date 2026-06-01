# Command Session Wait State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复设备命令已下发但 `WorklineSession` 仍停留在 `NEW` 的运行态缺陷，使所有 `COMMAND` intent 都遵循白皮书的 `Command -> Ack -> Callback` 状态链路。

**Architecture:** 在 Runtime effect 层统一保证 command 写回后进入 `COMMAND_RESULT` 等待态，而不是依赖插件显式传 `timeout_seconds`。ACK 通信重试继续由 Outbox 派发层负责，Session 只保存 Result 等待窗口，ACK 到达后由现有 reconciliation service 激活 `deadline_at`。

**Tech Stack:** Python 3.13, FastAPI, SQLModel/SQLAlchemy async, pytest, uv, GitNexus.

---

## Scope Check

本计划只修改 `COMMAND` intent 的写回行为和对应测试，属于单一运行态状态机修复。不修改第三方协议、不改 Result Callback 编排、不新增数据库字段、不调整 Outbox 重试策略。

## File Structure

- Modify: `src/workline_runtime/runtime_intent_effects.py`
  - 负责 RuntimeIntent 到 Session / Command / Outbox / Timeline 的 effect 落地。
  - 新增 command Result timeout 解析 helper。
  - 移除 `COMMAND` intent 在 `timeout_seconds is None` 时跳过等待态的行为。
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`
  - 覆盖无显式 timeout 的 command 仍进入 `WAITING_DEVICE_RESULT`。
  - 覆盖 payload 顶层 `timeout` 毫秒换算为秒的边界。
- Read-only reference: `docs/superpowers/specs/2026-05-29-command-session-wait-state-design.md`
  - 实现前确认验收标准。

## Task 1: 增加失败测试，锁定无 timeout command 必须进入等待态

**Files:**
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`
- Reference: `src/workline_runtime/runtime_intent_effects.py:447-496`

- [ ] **Step 1: 运行 GitNexus impact，确认修改范围**

Run:

```bash
gitnexus_impact({target: "_apply_command_wait", direction: "upstream", repo: "wes_backend"})
gitnexus_impact({target: "_apply_command", direction: "upstream", repo: "wes_backend"})
```

Expected: risk 不是 HIGH/CRITICAL；如果是 HIGH/CRITICAL，先把影响范围汇报给用户再继续。

- [ ] **Step 2: 写失败测试**

在 `tests/workline_runtime/test_runtime_intent_effects.py` 中，放在现有 command intent 测试附近，例如 `test_command_intent_creates_command_outbox_and_waits_for_result` 后面，新增：

```python
@pytest.mark.asyncio
async def test_command_intent_without_timeout_still_waits_for_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    created_payloads: list[dict[str, Any]] = []
    created_command = SimpleNamespace(id=88, command_code="CMD-NO-TIMEOUT")
    db = SimpleNamespace(add=MagicMock())
    session = _session(
        status="NEW",
        current_wait_type=None,
        awaiting_command_id=None,
        waiting_since=None,
        deadline_at=None,
        current_wait_timeout_seconds=None,
    )
    source = SimpleNamespace(id=1, device_code="ARM01")
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source]}
    ctx["current_status"] = "NEW"

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return created_command

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr(
        "src.app.device.repositories.command_repository.DeviceCommandRepository.create",
        fake_create,
    )
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"task_type": "MEASUREMENT_REEL", "timeout": 300000},
                destination=Destination.current(),
            )
        ],
    )

    assert created_payloads[0]["device_id"] == 1
    assert created_payloads[0]["task_type"] == "MEASUREMENT_REEL"
    assert session.status == "WAITING_DEVICE_RESULT"
    assert session.current_wait_type == "COMMAND_RESULT"
    assert session.awaiting_command_id == 88
    assert session.waiting_since == ctx["now"]
    assert session.current_wait_timeout_seconds == 300
    assert session.deadline_at is None
    assert [timeline["action_type"].value for timeline in timelines] == ["COMMAND_SENT", "WAIT_STARTED"]
    assert timelines[1]["payload"]["wait_token"] == "CMD-NO-TIMEOUT"
    assert timelines[1]["payload"]["deadline_seconds"] == 300
```

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_intent_effects.py::test_command_intent_without_timeout_still_waits_for_result -q
```

Expected: FAIL。失败点应显示 session 仍为 `RUNNING` 或 `current_wait_timeout_seconds` 不是 `300`。

- [ ] **Step 4: 提交失败测试**

```bash
git add tests/workline_runtime/test_runtime_intent_effects.py
git commit -m "test(workline): 覆盖命令下发后的会话等待态"
```

Expected: commit 成功；如果 pre-commit 因失败测试阻止提交，先不要跳过 hook，把本步骤合并到 Task 2 的实现提交。

## Task 2: 实现 COMMAND intent 默认等待态和 timeout 解析

**Files:**
- Modify: `src/workline_runtime/runtime_intent_effects.py`
- Test: `tests/workline_runtime/test_runtime_intent_effects.py`

- [ ] **Step 1: 新增 timeout 解析 helper**

在 `src/workline_runtime/runtime_intent_effects.py` 顶部常量区域新增默认值：

```python
_DEFAULT_COMMAND_RESULT_TIMEOUT_SECONDS = 300
```

在 `_validate_runtime_intents` 前后任一 helper 区域新增：

```python
def _positive_int(value: Any) -> int | None:
    resolved = optional_int(value)
    if resolved is None or resolved <= 0:
        return None
    return resolved


def _resolve_command_result_timeout_seconds(intent: RuntimeIntent) -> int:
    explicit_timeout = _positive_int(intent.timeout_seconds)
    if explicit_timeout is not None:
        return explicit_timeout

    payload = intent.payload_json if isinstance(intent.payload_json, Mapping) else {}
    timeout_ms = _positive_int(payload.get("timeout"))
    if timeout_ms is not None:
        return max(1, (timeout_ms + 999) // 1000)

    return _DEFAULT_COMMAND_RESULT_TIMEOUT_SECONDS
```

- [ ] **Step 2: 修改 `_apply_command` 始终进入 command wait**

在 `RuntimeIntentEffectApplier._apply_command` 中删除或替换以下逻辑：

```python
if intent.timeout_seconds is None:
    workline_effects.workline_session_lifecycle_service.running(ctx["session"])
    workline_effects._clear_session_wait(ctx["session"])
    return

await self._apply_command_wait(ctx, intent)
```

替换为：

```python
await self._apply_command_wait(ctx, intent)
```

- [ ] **Step 3: 修改 `_apply_command_wait` 使用统一解析**

把 `_apply_command_wait` 中：

```python
timeout_seconds = intent.timeout_seconds or 300
```

替换为：

```python
timeout_seconds = _resolve_command_result_timeout_seconds(intent)
```

保持 `start_wait(... deadline_seconds=timeout_seconds)` 和 WAIT_STARTED timeline payload 继续使用同一个 `timeout_seconds`。

- [ ] **Step 4: 运行 Task 1 测试确认通过**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_intent_effects.py::test_command_intent_without_timeout_still_waits_for_result -q
```

Expected: PASS。

- [ ] **Step 5: 提交实现**

```bash
git add src/workline_runtime/runtime_intent_effects.py tests/workline_runtime/test_runtime_intent_effects.py
git commit -m "fix(workline): 命令下发后进入会话等待态"
```

Expected: commit 成功。

## Task 3: 补齐 timeout 换算边界测试

**Files:**
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`
- Test: `tests/workline_runtime/test_runtime_intent_effects.py`

- [ ] **Step 1: 增加 helper 级参数化测试**

在 `tests/workline_runtime/test_runtime_intent_effects.py` 的 import 区域修改：

```python
from src.workline_runtime.runtime_intent_effects import RuntimeIntentEffectApplier
```

替换为：

```python
from src.workline_runtime.runtime_intent_effects import (
    RuntimeIntentEffectApplier,
    _resolve_command_result_timeout_seconds,
)
```

在 command intent 测试附近新增：

```python
@pytest.mark.parametrize(
    ("intent", "expected_timeout_seconds"),
    [
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": 1500},
                destination=Destination.current(),
            ),
            2,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": 500},
                destination=Destination.current(),
            ),
            1,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": 300000},
                destination=Destination.current(),
            ),
            300,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={},
                destination=Destination.current(),
            ),
            300,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": 300000},
                destination=Destination.current(),
                timeout_seconds=42,
            ),
            42,
        ),
    ],
)
def test_command_result_timeout_resolution(intent: RuntimeIntent, expected_timeout_seconds: int) -> None:
    assert _resolve_command_result_timeout_seconds(intent) == expected_timeout_seconds
```

- [ ] **Step 2: 运行参数化测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_intent_effects.py::test_command_result_timeout_resolution -q
```

Expected: PASS，5 个参数化 case 全通过。

- [ ] **Step 3: 运行相关 runtime intent effects 测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_intent_effects.py -q
```

Expected: PASS。

- [ ] **Step 4: 提交边界测试**

```bash
git add tests/workline_runtime/test_runtime_intent_effects.py
git commit -m "test(workline): 覆盖命令等待超时换算"
```

Expected: commit 成功。

## Task 4: 回归验证 ACK / Result / Outbox 重试相关测试

**Files:**
- Test: `tests/workline_runtime/test_workline_operation_service.py`
- Test: `tests/workline_runtime/test_outbox_repository.py`
- Test: `tests/workline_runtime/test_outbox_dispatch_service.py`
- Test: `tests/workline_runtime/test_runtime_reconciliation_service.py`

- [ ] **Step 1: 运行 sandbox ACK 服务测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_workline_operation_service.py -q
```

Expected: PASS。

- [ ] **Step 2: 运行 Outbox 重试测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_repository.py::test_mark_as_failed_uses_three_retry_backoff_then_exhausts -q
```

Expected: PASS，确认 ACK 缺失仍复用 outbox 重试并耗尽。

- [ ] **Step 3: 运行派发和对账核心测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_outbox_dispatch_service.py tests/workline_runtime/test_runtime_reconciliation_service.py -q
```

Expected: PASS。

- [ ] **Step 4: 运行粗分机插件相关测试**

Run:

```bash
uv run pytest tests/workline_plugins/test_rough_sorter_plugin.py tests/integration/workline_runtime/test_rough_sorter_physical_flow_integration.py -q
```

Expected: PASS。

- [ ] **Step 5: 运行格式和 lint**

Run:

```bash
uv run ruff format src/workline_runtime/runtime_intent_effects.py tests/workline_runtime/test_runtime_intent_effects.py
uv run ruff check src/workline_runtime/runtime_intent_effects.py tests/workline_runtime/test_runtime_intent_effects.py
```

Expected: format 无异常，ruff check PASS。

## Task 5: 手动沙箱验收

**Files:**
- No code changes.
- Reference: `docs/superpowers/specs/2026-05-29-command-session-wait-state-design.md`

- [ ] **Step 1: 启动或确认本地服务运行**

Run:

```bash
docker ps --format '{{.Names}} {{.Ports}}' | rg 'wes_api_dev|wes_postgres_dev|wes_redis_dev'
```

Expected: `wes_api_dev`、`wes_postgres_dev`、`wes_redis_dev` 均运行。

- [ ] **Step 2: 重新触发粗分机 `SCAN_COMPLETED` 沙箱流程**

使用现有前端或 API 沙箱入口触发一笔 `SCAN_COMPLETED`，确认系统生成新的 `MEASUREMENT_REEL` command。

Expected: 得到新的 `session_code`、`business_key`、`command_code`。

- [ ] **Step 3: 查询 Session / Command / Outbox 状态**

把 `<COMMAND_CODE>` 和 `<BUSINESS_KEY>` 替换为 Step 2 的实际值：

```bash
docker exec wes_postgres_dev psql -U wes_user -d wes_db -P pager=off -x -c "select id, session_code, status, current_wait_type, awaiting_command_id, current_wait_timeout_seconds, deadline_at from wes_biz.workline_sessions where business_key='<BUSINESS_KEY>' order by id desc limit 1; select id, command_code, status, sent_at, ack_received_at from wes_biz.device_commands where command_code='<COMMAND_CODE>'; select id, dispatch_key, status, sent_at, last_error from wes_biz.system_outbox where payload_json->>'command_code'='<COMMAND_CODE>';"
```

Expected before ACK:

- Session `status = WAITING_DEVICE_RESULT`
- Session `current_wait_type = COMMAND_RESULT`
- Session `awaiting_command_id` equals command id
- Session `current_wait_timeout_seconds` is positive
- Session `deadline_at` is `NULL`
- Command is `PENDING` / `SENT` depending on dispatch timing
- Outbox is `NEW` / `DISPATCHING` / `SENT` depending on dispatch timing

- [ ] **Step 4: 执行 sandbox ACK**

通过前端或 API 对 `device-command:<COMMAND_CODE>` 执行 sandbox ACK。

Expected:

- Command `status = ACK_RECEIVED`
- Command `ack_received_at` is not null
- Session remains `WAITING_DEVICE_RESULT`
- Session `deadline_at` is not null after ACK

- [ ] **Step 5: 执行 sandbox Result**

对 `<COMMAND_CODE>` 提交 `SUCCESS` Result，payload 包含粗分机测量数据：

```json
{
  "reel_diameter": "178.0",
  "reel_thickness": "15.0"
}
```

Expected: Result 被接收并进入后续插件流程；如果后续业务需要等待外部系统或其他设备，Session 进入对应下一等待态，不再卡在 `NEW`。

## Task 6: 最终变更审查

**Files:**
- Review: `src/workline_runtime/runtime_intent_effects.py`
- Review: `tests/workline_runtime/test_runtime_intent_effects.py`

- [ ] **Step 1: 运行 GitNexus detect changes**

Run:

```bash
gitnexus_detect_changes({repo: "wes_backend", scope: "all"})
```

Expected: changed symbols 包含 `_apply_command`、`_apply_command_wait` 和新增 timeout helper；affected processes 与 workline runtime command effects 一致。

- [ ] **Step 2: 检查 git diff**

Run:

```bash
git diff --stat
git diff -- src/workline_runtime/runtime_intent_effects.py tests/workline_runtime/test_runtime_intent_effects.py
```

Expected:

- 没有无关文件变更。
- 没有修改协议文档或数据库迁移。
- 没有放宽 sandbox ACK 校验。

- [ ] **Step 3: 运行质量门禁的聚焦子集**

Run:

```bash
uv run ruff check src/workline_runtime/runtime_intent_effects.py tests/workline_runtime/test_runtime_intent_effects.py
uv run pytest tests/workline_runtime/test_runtime_intent_effects.py tests/workline_runtime/test_workline_operation_service.py::test_sandbox_ack_marks_command_ack_and_keeps_outbox_sent -q
```

Expected: PASS。

- [ ] **Step 4: 提交最终清理**

如果 Task 2/3/4 已经分别提交且当前工作区干净，本步骤无需提交。若存在测试或格式化产生的后续修改：

```bash
git add src/workline_runtime/runtime_intent_effects.py tests/workline_runtime/test_runtime_intent_effects.py
git commit -m "chore(workline): 整理命令等待态验证"
```

Expected: commit 成功或工作区已干净。
