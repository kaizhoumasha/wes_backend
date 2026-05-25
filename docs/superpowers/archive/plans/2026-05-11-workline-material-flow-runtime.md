# 已归档：禁止继续执行

本文是 2026-05-11 的早期 Material Flow Runtime 原型计划，已被当前收尾收敛口径取代。后续开发不得继续按本文新增 `MaterialRun` 表、`RuntimeEvent` 持久化表、`MaterialFlowEngine` 或独立 metrics/alerts/projections 原型模块。

当前权威入口：`docs/business/workline_material_flow_runtime.md`。

# Workline Material Flow Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the workline plugin runtime around material flow, device actions, Runtime-owned state, and event-derived monitoring instead of per-plugin state machines.

**Architecture:** Plugins become business decision functions: they parse device events/results and return RuntimeIntent objects. Runtime owns material position, command execution, waiting, blocking, trace events, projections, metrics, and alerts. Device topology is the execution constraint and RuntimeEvent is the single fact stream for monitoring, statistics, replay, and diagnostics.

**Tech Stack:** Python 3.13, FastAPI, SQLModel/SQLAlchemy, Alembic, Celery, Pydantic, pytest, existing WES workline/device modules.

---

## Scope Check

This refactor spans Runtime core, plugin SDK, persistence, monitoring, statistics, and plugin migration. The plan is intentionally split into independently testable vertical slices. Each task should be completed and committed before moving to the next task.

The target design ignores legacy compatibility except where a short-lived migration adapter is explicitly listed. WES is unreleased, so destructive simplification is allowed.

## Target Principles

- Plugin owns business judgement.
- Runtime owns all state.
- Device topology constrains execution.
- MaterialRun is the current state source of truth.
- RuntimeCommand is the command lifecycle source of truth.
- RuntimeBlocker is the blocking and recovery source of truth.
- RuntimeEvent is the fact stream for trace, monitoring, statistics, and replay.
- Plugin developers never maintain state machine files, transitions, outbox records, retries, timeout scanning, metrics, or timeline entries.

## Target File Structure

### New Runtime Core

- `src/workline_runtime/material_run.py`
  - SQLModel for a material/process run.
  - Holds current material position, current action, lifecycle, wait anchor, blocker anchor, and business context.
- `src/workline_runtime/runtime_command.py`
  - SQLModel for Runtime-owned command lifecycle.
  - Owns ACK/result/timeout/retry facts.
- `src/workline_runtime/runtime_blocker.py`
  - SQLModel for unified blocking, exception, manual intervention, and recovery.
- `src/workline_runtime/runtime_event.py`
  - SQLModel and Pydantic contracts for append-only fact events.
- `src/workline_runtime/runtime_intent.py`
  - Pydantic contracts returned by plugins.
  - Includes command, route, complete, block, mark_ng, continue_next, and context patch intents.
- `src/workline_runtime/material_flow_engine.py`
  - Runtime engine that applies RuntimeIntent to MaterialRun, RuntimeCommand, RuntimeBlocker, and RuntimeEvent.
- `src/workline_runtime/material_target_resolver.py`
  - Device/topology target resolution used by the engine.

### New Projections And Metrics

- `src/workline_runtime/projections.py`
  - Current views: line, device, material, blocker.
- `src/workline_runtime/metrics.py`
  - Aggregates production, command reliability, device reliability, and plugin quality metrics from RuntimeEvent.
- `src/workline_runtime/alerts.py`
  - Converts RuntimeEvent and projections into actionable alerts.

### Plugin SDK

- `src/workline_runtime/plugin_next.py`
  - Fluent helper exposed as `ctx.next`.
- `src/workline_runtime/plugin_context.py`
  - Modify to expose material, current device, topology, and `ctx.next`.
- `src/workline_runtime/plugin_base.py`
  - Modify to remove state-machine-facing decorator requirements from the target API.

### Migration Adapters

- `src/workline_runtime/legacy_plugin_adapter.py`
  - Temporary adapter that maps old PluginResult to RuntimeIntent during migration.
- `src/workline_plugins/smt_classifier_v2/plugin.py`
  - First target-style plugin.
- `src/workline_plugins/inbound_tote_qc_v2/plugin.py`
  - Second target-style plugin.

### Tests

- `tests/workline_runtime/test_runtime_intent.py`
- `tests/workline_runtime/test_material_target_resolver.py`
- `tests/workline_runtime/test_material_flow_engine.py`
- `tests/workline_runtime/test_runtime_event_contract.py`
- `tests/workline_runtime/test_runtime_projections.py`
- `tests/workline_runtime/test_runtime_metrics.py`
- `tests/workline_runtime/test_plugin_next.py`
- `tests/workline_plugins/test_smt_classifier_v2.py`
- `tests/workline_plugins/test_inbound_tote_qc_v2.py`

---

## Task 1: Runtime Intent Contract

**Files:**
- Create: `src/workline_runtime/runtime_intent.py`
- Create: `tests/workline_runtime/test_runtime_intent.py`

- [ ] **Step 1: Write failing tests for plugin-visible intents**

Create `tests/workline_runtime/test_runtime_intent.py`:

```python
import pytest

from src.workline_runtime.runtime_intent import (
    BlockScope,
    Destination,
    DestinationKind,
    RuntimeIntent,
    RuntimeIntentKind,
)


def test_command_intent_describes_device_action_and_destination():
    intent = RuntimeIntent.command(
        device_role="WEIGH_SCALE",
        action="WEIGH_TOTE",
        payload={"tote_id": "T-001"},
        destination=Destination.role("WEIGH_SCALE"),
        timeout_seconds=120,
    )

    assert intent.kind == RuntimeIntentKind.COMMAND
    assert intent.device_role == "WEIGH_SCALE"
    assert intent.action == "WEIGH_TOTE"
    assert intent.payload_json == {"tote_id": "T-001"}
    assert intent.destination == Destination(kind=DestinationKind.ROLE, value="WEIGH_SCALE")
    assert intent.timeout_seconds == 120


def test_block_intent_requires_reason_and_scope():
    intent = RuntimeIntent.block(
        scope=BlockScope.MATERIAL,
        reason_code="BARCODE_INVALID",
        message="条码无法识别",
        suggested_action="人工复核条码",
    )

    assert intent.kind == RuntimeIntentKind.BLOCK
    assert intent.block_scope == BlockScope.MATERIAL
    assert intent.reason_code == "BARCODE_INVALID"
    assert intent.message == "条码无法识别"
    assert intent.suggested_action == "人工复核条码"


def test_invalid_command_requires_action():
    with pytest.raises(ValueError, match="action"):
        RuntimeIntent.command(device_role="WEIGH_SCALE", action="", payload={})
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_intent.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.workline_runtime.runtime_intent'`.

- [ ] **Step 3: Implement the minimal RuntimeIntent contract**

Create `src/workline_runtime/runtime_intent.py`:

```python
"""Plugin-facing Runtime intent contracts.

Plugins describe what should happen next. Runtime owns whether the intent is
legal, how target devices are resolved, and how state is persisted.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class RuntimeIntentKind(str, Enum):
    COMMAND = "COMMAND"
    ROUTE = "ROUTE"
    COMPLETE = "COMPLETE"
    BLOCK = "BLOCK"
    MARK_NG = "MARK_NG"
    CONTINUE_NEXT = "CONTINUE_NEXT"
    UPDATE_CONTEXT = "UPDATE_CONTEXT"


class DestinationKind(str, Enum):
    CURRENT = "CURRENT"
    NEXT = "NEXT"
    ROLE = "ROLE"
    DEVICE = "DEVICE"
    PASS_ROUTE = "PASS_ROUTE"
    NG_ROUTE = "NG_ROUTE"
    EXIT = "EXIT"


class BlockScope(str, Enum):
    WORKLINE = "WORKLINE"
    DEVICE = "DEVICE"
    MATERIAL = "MATERIAL"
    COMMAND = "COMMAND"


class Destination(BaseModel):
    kind: DestinationKind
    value: str | int | None = None

    @classmethod
    def current(cls) -> "Destination":
        return cls(kind=DestinationKind.CURRENT)

    @classmethod
    def next(cls) -> "Destination":
        return cls(kind=DestinationKind.NEXT)

    @classmethod
    def role(cls, role: str) -> "Destination":
        return cls(kind=DestinationKind.ROLE, value=role)

    @classmethod
    def device(cls, device_id: int) -> "Destination":
        return cls(kind=DestinationKind.DEVICE, value=device_id)

    @classmethod
    def ng_route(cls) -> "Destination":
        return cls(kind=DestinationKind.NG_ROUTE)

    @classmethod
    def pass_route(cls) -> "Destination":
        return cls(kind=DestinationKind.PASS_ROUTE)

    @classmethod
    def exit(cls) -> "Destination":
        return cls(kind=DestinationKind.EXIT)


class RuntimeIntent(BaseModel):
    kind: RuntimeIntentKind
    device_role: str | None = None
    target_device_id: int | None = None
    action: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)
    destination: Destination | None = None
    timeout_seconds: int | None = None
    block_scope: BlockScope | None = None
    reason_code: str | None = None
    message: str | None = None
    suggested_action: str | None = None
    context_patch: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def command(
        cls,
        *,
        device_role: str | None = None,
        target_device_id: int | None = None,
        action: str,
        payload: dict[str, Any] | None = None,
        destination: Destination | None = None,
        timeout_seconds: int | None = None,
    ) -> "RuntimeIntent":
        return cls(
            kind=RuntimeIntentKind.COMMAND,
            device_role=device_role,
            target_device_id=target_device_id,
            action=action,
            payload_json=payload or {},
            destination=destination,
            timeout_seconds=timeout_seconds,
        )

    @classmethod
    def block(
        cls,
        *,
        scope: BlockScope,
        reason_code: str,
        message: str,
        suggested_action: str | None = None,
    ) -> "RuntimeIntent":
        return cls(
            kind=RuntimeIntentKind.BLOCK,
            block_scope=scope,
            reason_code=reason_code,
            message=message,
            suggested_action=suggested_action,
        )

    @model_validator(mode="after")
    def validate_intent(self) -> "RuntimeIntent":
        if self.kind == RuntimeIntentKind.COMMAND and not self.action:
            raise ValueError("COMMAND intent requires action")
        if self.kind == RuntimeIntentKind.BLOCK:
            if self.block_scope is None:
                raise ValueError("BLOCK intent requires block_scope")
            if not self.reason_code:
                raise ValueError("BLOCK intent requires reason_code")
            if not self.message:
                raise ValueError("BLOCK intent requires message")
        return self


__all__ = [
    "BlockScope",
    "Destination",
    "DestinationKind",
    "RuntimeIntent",
    "RuntimeIntentKind",
]
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_intent.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_runtime/runtime_intent.py tests/workline_runtime/test_runtime_intent.py
git commit -m "feat: add runtime intent contract"
```

---

## Task 2: MaterialRun Source Of Truth

**Files:**
- Create: `src/workline_runtime/material_run.py`
- Create: `tests/workline_runtime/test_material_run_contract.py`

- [ ] **Step 1: Write failing tests for MaterialRun lifecycle**

Create `tests/workline_runtime/test_material_run_contract.py`:

```python
from src.workline_runtime.material_run import LifecycleState, MaterialRun


def test_material_run_tracks_current_device_and_action():
    run = MaterialRun(
        run_code="MR-001",
        material_identity_key="pkg:PKG-001",
        workline_id=10,
        current_device_id=21,
        current_device_role="ENTRY_SCANNER",
        current_action="SCAN_COMPLETED",
        lifecycle_state=LifecycleState.ACTIVE,
    )

    assert run.material_identity_key == "pkg:PKG-001"
    assert run.current_device_id == 21
    assert run.current_device_role == "ENTRY_SCANNER"
    assert run.current_action == "SCAN_COMPLETED"
    assert run.lifecycle_state == LifecycleState.ACTIVE


def test_material_run_can_record_wait_anchor():
    run = MaterialRun(
        run_code="MR-002",
        material_identity_key="pkg:PKG-002",
        workline_id=10,
        lifecycle_state=LifecycleState.WAITING,
        awaiting_command_id=300,
        wait_reason="COMMAND_RESULT",
    )

    assert run.awaiting_command_id == 300
    assert run.wait_reason == "COMMAND_RESULT"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_material_run_contract.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.workline_runtime.material_run'`.

- [ ] **Step 3: Implement MaterialRun contract**

Create `src/workline_runtime/material_run.py`:

```python
"""Material flow source of truth for workline runtime."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LifecycleState(str, Enum):
    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MaterialRun(BaseModel):
    run_code: str
    material_identity_key: str
    workline_id: int
    current_device_id: int | None = None
    current_device_role: str | None = None
    current_action: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    awaiting_command_id: int | None = None
    blocker_id: int | None = None
    wait_reason: str | None = None
    context_json: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    ended_at: datetime | None = None


__all__ = ["LifecycleState", "MaterialRun"]
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_material_run_contract.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_runtime/material_run.py tests/workline_runtime/test_material_run_contract.py
git commit -m "feat: add material run contract"
```

---

## Task 3: Runtime Event Fact Stream

**Files:**
- Create: `src/workline_runtime/runtime_event.py`
- Create: `tests/workline_runtime/test_runtime_event_contract.py`

- [ ] **Step 1: Write failing tests for required event dimensions**

Create `tests/workline_runtime/test_runtime_event_contract.py`:

```python
from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


def test_runtime_event_carries_monitoring_dimensions():
    event = RuntimeEvent(
        event_type=RuntimeEventType.COMMAND_SUCCEEDED,
        trace_id="trace-1",
        material_run_id=100,
        material_identity_key="pkg:PKG-001",
        workline_id=10,
        device_id=21,
        device_role="WEIGH_SCALE",
        plugin_key="inbound_tote_qc",
        action="WEIGH_TOTE",
        command_id=300,
        duration_ms=1500,
        result="SUCCESS",
        reason_code=None,
        failure_domain=None,
        owner=None,
    )

    assert event.event_type == RuntimeEventType.COMMAND_SUCCEEDED
    assert event.material_identity_key == "pkg:PKG-001"
    assert event.duration_ms == 1500


def test_runtime_event_payload_defaults_to_empty_dict():
    event = RuntimeEvent(
        event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
        trace_id="trace-2",
        workline_id=10,
    )

    assert event.payload_json == {}
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_event_contract.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.workline_runtime.runtime_event'`.

- [ ] **Step 3: Implement RuntimeEvent contract**

Create `src/workline_runtime/runtime_event.py`:

```python
"""Append-only runtime facts used for trace, projections, metrics, and replay."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from src.utils.timezone import timezone


class RuntimeEventType(str, Enum):
    MATERIAL_ENTERED_DEVICE = "MATERIAL_ENTERED_DEVICE"
    MATERIAL_LEFT_DEVICE = "MATERIAL_LEFT_DEVICE"
    COMMAND_CREATED = "COMMAND_CREATED"
    COMMAND_ACKED = "COMMAND_ACKED"
    COMMAND_SUCCEEDED = "COMMAND_SUCCEEDED"
    COMMAND_FAILED = "COMMAND_FAILED"
    COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
    PLUGIN_DECISION_MADE = "PLUGIN_DECISION_MADE"
    PROCESS_BLOCKED = "PROCESS_BLOCKED"
    PROCESS_UNBLOCKED = "PROCESS_UNBLOCKED"
    PROCESS_COMPLETED = "PROCESS_COMPLETED"
    PROCESS_FAILED = "PROCESS_FAILED"
    DEVICE_STATUS_CHANGED = "DEVICE_STATUS_CHANGED"


class RuntimeEvent(BaseModel):
    event_type: RuntimeEventType
    trace_id: str
    material_run_id: int | None = None
    material_identity_key: str | None = None
    workline_id: int
    device_id: int | None = None
    device_role: str | None = None
    plugin_key: str | None = None
    action: str | None = None
    command_id: int | None = None
    occurred_at: datetime = Field(default_factory=timezone.now_for_db)
    duration_ms: int | None = None
    result: str | None = None
    reason_code: str | None = None
    failure_domain: str | None = None
    owner: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)


__all__ = ["RuntimeEvent", "RuntimeEventType"]
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_event_contract.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_runtime/runtime_event.py tests/workline_runtime/test_runtime_event_contract.py
git commit -m "feat: add runtime event contract"
```

---

## Task 4: Topology Target Resolution For Material Flow

**Files:**
- Create: `src/workline_runtime/material_target_resolver.py`
- Create: `tests/workline_runtime/test_material_target_resolver.py`

- [ ] **Step 1: Write failing tests for destination resolution**

Create `tests/workline_runtime/test_material_target_resolver.py`:

```python
from dataclasses import dataclass

import pytest

from src.workline_runtime.material_target_resolver import resolve_destination_device
from src.workline_runtime.runtime_intent import Destination


@dataclass
class Device:
    id: int
    device_role: str
    upstream_device_id: int | None = None
    sort_order: int = 0
    role_index: int = 1


def test_resolves_next_device_from_topology():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    weigh = Device(id=2, device_role="WEIGH_SCALE", upstream_device_id=1)

    resolved = resolve_destination_device(
        destination=Destination.next(),
        source_device=source,
        devices=[source, weigh],
    )

    assert resolved == weigh


def test_resolves_role_within_downstream_candidates():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    conveyor = Device(id=2, device_role="CONVEYOR", upstream_device_id=1)
    weigh = Device(id=3, device_role="WEIGH_SCALE", upstream_device_id=1)

    resolved = resolve_destination_device(
        destination=Destination.role("WEIGH_SCALE"),
        source_device=source,
        devices=[source, conveyor, weigh],
    )

    assert resolved == weigh


def test_raises_for_ambiguous_role():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    left = Device(id=2, device_role="WEIGH_SCALE", upstream_device_id=1)
    right = Device(id=3, device_role="WEIGH_SCALE", upstream_device_id=1)

    with pytest.raises(ValueError, match="Ambiguous"):
        resolve_destination_device(
            destination=Destination.role("WEIGH_SCALE"),
            source_device=source,
            devices=[source, left, right],
        )
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_material_target_resolver.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.workline_runtime.material_target_resolver'`.

- [ ] **Step 3: Implement destination resolution**

Create `src/workline_runtime/material_target_resolver.py`:

```python
"""Resolve material destinations against device topology."""

from __future__ import annotations

from typing import Any

from src.workline_runtime.runtime_intent import Destination, DestinationKind


def _device_id(device: Any) -> int | None:
    value = getattr(device, "id", None)
    return value if isinstance(value, int) else None


def _device_role(device: Any) -> str | None:
    value = getattr(device, "device_role", None)
    return value if isinstance(value, str) and value else None


def _upstream_device_id(device: Any) -> int | None:
    value = getattr(device, "upstream_device_id", None)
    return value if isinstance(value, int) else None


def _sort_key(device: Any) -> tuple[int, int, int]:
    sort_order = getattr(device, "sort_order", 0)
    role_index = getattr(device, "role_index", 0)
    return (
        sort_order if isinstance(sort_order, int) else 0,
        role_index if isinstance(role_index, int) else 0,
        _device_id(device) or 0,
    )


def _downstream_devices(source_device: Any, devices: list[Any]) -> list[Any]:
    source_id = _device_id(source_device)
    if source_id is None:
        raise ValueError("Source device missing id")
    return [device for device in devices if _upstream_device_id(device) == source_id]


def _single(candidates: list[Any], *, description: str) -> Any:
    ordered = sorted(candidates, key=_sort_key)
    if len(ordered) == 1:
        return ordered[0]
    if not ordered:
        raise ValueError(f"No destination matched {description}")
    raise ValueError(f"Ambiguous destination matched {description}")


def resolve_destination_device(*, destination: Destination, source_device: Any, devices: list[Any]) -> Any:
    if destination.kind == DestinationKind.CURRENT:
        return source_device
    if destination.kind == DestinationKind.NEXT:
        return _single(_downstream_devices(source_device, devices), description="NEXT")
    if destination.kind == DestinationKind.ROLE:
        role = destination.value
        candidates = [device for device in _downstream_devices(source_device, devices) if _device_role(device) == role]
        return _single(candidates, description=f"ROLE {role}")
    if destination.kind == DestinationKind.DEVICE:
        device_id = destination.value
        candidates = [device for device in devices if _device_id(device) == device_id]
        return _single(candidates, description=f"DEVICE {device_id}")
    raise ValueError(f"Destination {destination.kind.value} does not resolve to a concrete device")


__all__ = ["resolve_destination_device"]
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_material_target_resolver.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_runtime/material_target_resolver.py tests/workline_runtime/test_material_target_resolver.py
git commit -m "feat: resolve material destinations from topology"
```

---

## Task 5: Runtime Engine Applies Plugin Intents

**Files:**
- Create: `src/workline_runtime/material_flow_engine.py`
- Create: `tests/workline_runtime/test_material_flow_engine.py`

- [ ] **Step 1: Write failing tests for command and block application**

Create `tests/workline_runtime/test_material_flow_engine.py`:

```python
from dataclasses import dataclass

from src.workline_runtime.material_flow_engine import MaterialFlowEngine
from src.workline_runtime.material_run import LifecycleState, MaterialRun
from src.workline_runtime.runtime_event import RuntimeEventType
from src.workline_runtime.runtime_intent import BlockScope, Destination, RuntimeIntent


@dataclass
class Device:
    id: int
    device_role: str
    upstream_device_id: int | None = None
    device_status: str = "IDLE"
    current_command_id: int | None = None


def test_command_intent_moves_run_to_waiting_and_emits_events():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    weigh = Device(id=2, device_role="WEIGH_SCALE", upstream_device_id=1)
    run = MaterialRun(
        run_code="MR-001",
        material_identity_key="pkg:PKG-001",
        workline_id=10,
        current_device_id=1,
        current_device_role="ENTRY_SCANNER",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    engine = MaterialFlowEngine(command_id_factory=lambda: 501)

    result = engine.apply(
        run=run,
        source_device=source,
        devices=[source, weigh],
        plugin_key="inbound_tote_qc",
        trace_id="trace-1",
        intent=RuntimeIntent.command(
            device_role="WEIGH_SCALE",
            action="WEIGH_TOTE",
            payload={"tote_id": "T-001"},
            destination=Destination.role("WEIGH_SCALE"),
            timeout_seconds=120,
        ),
    )

    assert result.run.lifecycle_state == LifecycleState.WAITING
    assert result.run.current_device_id == 2
    assert result.run.current_device_role == "WEIGH_SCALE"
    assert result.run.current_action == "WEIGH_TOTE"
    assert result.run.awaiting_command_id == 501
    assert [event.event_type for event in result.events] == [
        RuntimeEventType.PLUGIN_DECISION_MADE,
        RuntimeEventType.COMMAND_CREATED,
        RuntimeEventType.MATERIAL_ENTERED_DEVICE,
    ]


def test_block_intent_moves_run_to_blocked_and_emits_block_event():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    run = MaterialRun(
        run_code="MR-002",
        material_identity_key="pkg:PKG-002",
        workline_id=10,
        current_device_id=1,
        current_device_role="ENTRY_SCANNER",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    engine = MaterialFlowEngine(blocker_id_factory=lambda: 901)

    result = engine.apply(
        run=run,
        source_device=source,
        devices=[source],
        plugin_key="smt_classifier",
        trace_id="trace-2",
        intent=RuntimeIntent.block(
            scope=BlockScope.MATERIAL,
            reason_code="BARCODE_INVALID",
            message="条码无法识别",
            suggested_action="人工复核条码",
        ),
    )

    assert result.run.lifecycle_state == LifecycleState.BLOCKED
    assert result.run.blocker_id == 901
    assert result.events[-1].event_type == RuntimeEventType.PROCESS_BLOCKED
    assert result.events[-1].reason_code == "BARCODE_INVALID"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_material_flow_engine.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.workline_runtime.material_flow_engine'`.

- [ ] **Step 3: Implement the in-memory engine contract**

Create `src/workline_runtime/material_flow_engine.py`:

```python
"""Runtime-owned material flow engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.workline_runtime.material_run import LifecycleState, MaterialRun
from src.workline_runtime.material_target_resolver import resolve_destination_device
from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType
from src.workline_runtime.runtime_intent import Destination, RuntimeIntent, RuntimeIntentKind


@dataclass(frozen=True)
class MaterialFlowResult:
    run: MaterialRun
    command_id: int | None
    blocker_id: int | None
    events: list[RuntimeEvent]


class MaterialFlowEngine:
    def __init__(
        self,
        *,
        command_id_factory: Callable[[], int] | None = None,
        blocker_id_factory: Callable[[], int] | None = None,
    ) -> None:
        self.command_id_factory = command_id_factory or (lambda: 1)
        self.blocker_id_factory = blocker_id_factory or (lambda: 1)

    def apply(
        self,
        *,
        run: MaterialRun,
        source_device: Any,
        devices: list[Any],
        plugin_key: str,
        trace_id: str,
        intent: RuntimeIntent,
    ) -> MaterialFlowResult:
        events = [
            RuntimeEvent(
                event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
                trace_id=trace_id,
                material_identity_key=run.material_identity_key,
                workline_id=run.workline_id,
                device_id=run.current_device_id,
                device_role=run.current_device_role,
                plugin_key=plugin_key,
                action=intent.action,
                reason_code=intent.reason_code,
                payload_json=intent.model_dump(mode="json"),
            )
        ]

        if intent.kind == RuntimeIntentKind.COMMAND:
            command_id = self.command_id_factory()
            target = resolve_destination_device(
                destination=intent.destination or Destination.current(),
                source_device=source_device,
                devices=devices,
            )
            target_device_id = getattr(target, "id")
            target_role = getattr(target, "device_role")
            updated = run.model_copy(
                update={
                    "current_device_id": target_device_id,
                    "current_device_role": target_role,
                    "current_action": intent.action,
                    "lifecycle_state": LifecycleState.WAITING,
                    "awaiting_command_id": command_id,
                    "wait_reason": "COMMAND_RESULT",
                }
            )
            events.extend(
                [
                    RuntimeEvent(
                        event_type=RuntimeEventType.COMMAND_CREATED,
                        trace_id=trace_id,
                        material_identity_key=updated.material_identity_key,
                        workline_id=updated.workline_id,
                        device_id=target_device_id,
                        device_role=target_role,
                        plugin_key=plugin_key,
                        action=intent.action,
                        command_id=command_id,
                    ),
                    RuntimeEvent(
                        event_type=RuntimeEventType.MATERIAL_ENTERED_DEVICE,
                        trace_id=trace_id,
                        material_identity_key=updated.material_identity_key,
                        workline_id=updated.workline_id,
                        device_id=target_device_id,
                        device_role=target_role,
                        plugin_key=plugin_key,
                        action=intent.action,
                    ),
                ]
            )
            return MaterialFlowResult(run=updated, command_id=command_id, blocker_id=None, events=events)

        if intent.kind == RuntimeIntentKind.BLOCK:
            blocker_id = self.blocker_id_factory()
            updated = run.model_copy(update={"lifecycle_state": LifecycleState.BLOCKED, "blocker_id": blocker_id})
            events.append(
                RuntimeEvent(
                    event_type=RuntimeEventType.PROCESS_BLOCKED,
                    trace_id=trace_id,
                    material_identity_key=updated.material_identity_key,
                    workline_id=updated.workline_id,
                    device_id=updated.current_device_id,
                    device_role=updated.current_device_role,
                    plugin_key=plugin_key,
                    action=updated.current_action,
                    reason_code=intent.reason_code,
                    payload_json={"message": intent.message, "suggested_action": intent.suggested_action},
                )
            )
            return MaterialFlowResult(run=updated, command_id=None, blocker_id=blocker_id, events=events)

        raise ValueError(f"Unsupported intent kind: {intent.kind.value}")


__all__ = ["MaterialFlowEngine", "MaterialFlowResult"]
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_material_flow_engine.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_runtime/material_flow_engine.py tests/workline_runtime/test_material_flow_engine.py
git commit -m "feat: apply runtime intents to material flow"
```

---

## Task 6: Plugin Next Helper

**Files:**
- Create: `src/workline_runtime/plugin_next.py`
- Create: `tests/workline_runtime/test_plugin_next.py`

- [ ] **Step 1: Write failing tests for plugin developer ergonomics**

Create `tests/workline_runtime/test_plugin_next.py`:

```python
from src.workline_runtime.plugin_next import PluginNext
from src.workline_runtime.runtime_intent import BlockScope, DestinationKind, RuntimeIntentKind


def test_plugin_next_command_builds_runtime_intent():
    intent = PluginNext().command(
        device_role="WEIGH_SCALE",
        action="WEIGH_TOTE",
        payload={"tote_id": "T-001"},
        destination_role="WEIGH_SCALE",
        timeout_seconds=120,
    )

    assert intent.kind == RuntimeIntentKind.COMMAND
    assert intent.destination.kind == DestinationKind.ROLE
    assert intent.destination.value == "WEIGH_SCALE"


def test_plugin_next_block_builds_runtime_intent():
    intent = PluginNext().block(
        scope=BlockScope.MATERIAL,
        reason_code="BARCODE_INVALID",
        message="条码无法识别",
        suggested_action="人工复核条码",
    )

    assert intent.kind == RuntimeIntentKind.BLOCK
    assert intent.reason_code == "BARCODE_INVALID"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_next.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.workline_runtime.plugin_next'`.

- [ ] **Step 3: Implement PluginNext**

Create `src/workline_runtime/plugin_next.py`:

```python
"""Fluent helper for plugin authors to create RuntimeIntent objects."""

from __future__ import annotations

from typing import Any

from src.workline_runtime.runtime_intent import BlockScope, Destination, RuntimeIntent


class PluginNext:
    def command(
        self,
        *,
        device_role: str | None,
        action: str,
        payload: dict[str, Any] | None = None,
        destination_role: str | None = None,
        timeout_seconds: int | None = None,
    ) -> RuntimeIntent:
        destination = Destination.role(destination_role) if destination_role else Destination.next()
        return RuntimeIntent.command(
            device_role=device_role,
            action=action,
            payload=payload or {},
            destination=destination,
            timeout_seconds=timeout_seconds,
        )

    def block(
        self,
        *,
        scope: BlockScope,
        reason_code: str,
        message: str,
        suggested_action: str | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.block(
            scope=scope,
            reason_code=reason_code,
            message=message,
            suggested_action=suggested_action,
        )


__all__ = ["PluginNext"]
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_next.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_runtime/plugin_next.py tests/workline_runtime/test_plugin_next.py
git commit -m "feat: add plugin next helper"
```

---

## Task 7: Runtime Projections For Operator Views

**Files:**
- Create: `src/workline_runtime/projections.py`
- Create: `tests/workline_runtime/test_runtime_projections.py`

- [ ] **Step 1: Write failing tests for material and device current views**

Create `tests/workline_runtime/test_runtime_projections.py`:

```python
from src.workline_runtime.projections import ProjectionState
from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


def test_projection_tracks_material_current_device():
    state = ProjectionState()

    state.apply(
        RuntimeEvent(
            event_type=RuntimeEventType.MATERIAL_ENTERED_DEVICE,
            trace_id="trace-1",
            material_identity_key="pkg:PKG-001",
            workline_id=10,
            device_id=21,
            device_role="WEIGH_SCALE",
            action="WEIGH_TOTE",
        )
    )

    material = state.materials["pkg:PKG-001"]
    assert material.current_device_id == 21
    assert material.current_device_role == "WEIGH_SCALE"
    assert material.current_action == "WEIGH_TOTE"


def test_projection_tracks_blocker_count_by_line():
    state = ProjectionState()

    state.apply(
        RuntimeEvent(
            event_type=RuntimeEventType.PROCESS_BLOCKED,
            trace_id="trace-2",
            material_identity_key="pkg:PKG-002",
            workline_id=10,
            device_id=22,
            device_role="DIVERT_CONVEYOR",
            reason_code="DEVICE_TIMEOUT",
        )
    )

    assert state.lines[10].blocked_count == 1
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_projections.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.workline_runtime.projections'`.

- [ ] **Step 3: Implement projection models**

Create `src/workline_runtime/projections.py`:

```python
"""In-memory runtime projections used as contract for persisted current views."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


@dataclass
class MaterialRuntimeView:
    material_identity_key: str
    workline_id: int
    current_device_id: int | None = None
    current_device_role: str | None = None
    current_action: str | None = None
    blocked: bool = False
    block_reason: str | None = None


@dataclass
class LineRuntimeView:
    workline_id: int
    blocked_count: int = 0


@dataclass
class ProjectionState:
    materials: dict[str, MaterialRuntimeView] = field(default_factory=dict)
    lines: dict[int, LineRuntimeView] = field(default_factory=dict)

    def apply(self, event: RuntimeEvent) -> None:
        line = self.lines.setdefault(event.workline_id, LineRuntimeView(workline_id=event.workline_id))
        material_key = event.material_identity_key
        if material_key:
            material = self.materials.setdefault(
                material_key,
                MaterialRuntimeView(material_identity_key=material_key, workline_id=event.workline_id),
            )
        else:
            material = None

        if event.event_type == RuntimeEventType.MATERIAL_ENTERED_DEVICE and material is not None:
            material.current_device_id = event.device_id
            material.current_device_role = event.device_role
            material.current_action = event.action
            return

        if event.event_type == RuntimeEventType.PROCESS_BLOCKED:
            line.blocked_count += 1
            if material is not None:
                material.blocked = True
                material.block_reason = event.reason_code


__all__ = ["LineRuntimeView", "MaterialRuntimeView", "ProjectionState"]
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_projections.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_runtime/projections.py tests/workline_runtime/test_runtime_projections.py
git commit -m "feat: add runtime projection contracts"
```

---

## Task 8: Runtime Metrics From Events

**Files:**
- Create: `src/workline_runtime/metrics.py`
- Create: `tests/workline_runtime/test_runtime_metrics.py`

- [ ] **Step 1: Write failing tests for production and command metrics**

Create `tests/workline_runtime/test_runtime_metrics.py`:

```python
from src.workline_runtime.metrics import aggregate_runtime_metrics
from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


def test_aggregates_completed_count_and_ng_rate():
    metrics = aggregate_runtime_metrics(
        [
            RuntimeEvent(event_type=RuntimeEventType.PROCESS_COMPLETED, trace_id="t1", workline_id=1),
            RuntimeEvent(
                event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
                trace_id="t2",
                workline_id=1,
                result="NG",
            ),
            RuntimeEvent(
                event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
                trace_id="t3",
                workline_id=1,
                result="OK",
            ),
        ]
    )

    assert metrics.completed_count == 1
    assert metrics.plugin_decision_count == 2
    assert metrics.ng_rate == 0.5


def test_aggregates_command_success_and_timeout_rate():
    metrics = aggregate_runtime_metrics(
        [
            RuntimeEvent(event_type=RuntimeEventType.COMMAND_SUCCEEDED, trace_id="t1", workline_id=1),
            RuntimeEvent(event_type=RuntimeEventType.COMMAND_TIMEOUT, trace_id="t2", workline_id=1),
        ]
    )

    assert metrics.command_count == 2
    assert metrics.command_success_rate == 0.5
    assert metrics.command_timeout_rate == 0.5
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_metrics.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.workline_runtime.metrics'`.

- [ ] **Step 3: Implement event aggregation**

Create `src/workline_runtime/metrics.py`:

```python
"""Runtime metrics derived from RuntimeEvent facts."""

from __future__ import annotations

from dataclasses import dataclass

from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


@dataclass(frozen=True)
class RuntimeMetrics:
    completed_count: int = 0
    plugin_decision_count: int = 0
    ng_decision_count: int = 0
    command_count: int = 0
    command_success_count: int = 0
    command_timeout_count: int = 0

    @property
    def ng_rate(self) -> float:
        return self.ng_decision_count / self.plugin_decision_count if self.plugin_decision_count else 0.0

    @property
    def command_success_rate(self) -> float:
        return self.command_success_count / self.command_count if self.command_count else 0.0

    @property
    def command_timeout_rate(self) -> float:
        return self.command_timeout_count / self.command_count if self.command_count else 0.0


def aggregate_runtime_metrics(events: list[RuntimeEvent]) -> RuntimeMetrics:
    completed_count = 0
    plugin_decision_count = 0
    ng_decision_count = 0
    command_count = 0
    command_success_count = 0
    command_timeout_count = 0

    for event in events:
        if event.event_type == RuntimeEventType.PROCESS_COMPLETED:
            completed_count += 1
        if event.event_type == RuntimeEventType.PLUGIN_DECISION_MADE:
            plugin_decision_count += 1
            if event.result == "NG":
                ng_decision_count += 1
        if event.event_type in {
            RuntimeEventType.COMMAND_SUCCEEDED,
            RuntimeEventType.COMMAND_FAILED,
            RuntimeEventType.COMMAND_TIMEOUT,
        }:
            command_count += 1
        if event.event_type == RuntimeEventType.COMMAND_SUCCEEDED:
            command_success_count += 1
        if event.event_type == RuntimeEventType.COMMAND_TIMEOUT:
            command_timeout_count += 1

    return RuntimeMetrics(
        completed_count=completed_count,
        plugin_decision_count=plugin_decision_count,
        ng_decision_count=ng_decision_count,
        command_count=command_count,
        command_success_count=command_success_count,
        command_timeout_count=command_timeout_count,
    )


__all__ = ["RuntimeMetrics", "aggregate_runtime_metrics"]
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_metrics.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_runtime/metrics.py tests/workline_runtime/test_runtime_metrics.py
git commit -m "feat: derive runtime metrics from events"
```

---

## Task 9: Convert Inbound Tote QC Plugin To Target API

**Files:**
- Create: `src/workline_plugins/inbound_tote_qc_v2/plugin.py`
- Create: `tests/workline_plugins/test_inbound_tote_qc_v2.py`

- [ ] **Step 1: Write failing tests for state-machine-free plugin behavior**

Create `tests/workline_plugins/test_inbound_tote_qc_v2.py`:

```python
from src.workline_plugins.inbound_tote_qc_v2.plugin import handle_tote_arrived, handle_weigh_result
from src.workline_runtime.runtime_intent import DestinationKind, RuntimeIntentKind


def test_tote_arrived_returns_weigh_command_intent():
    intent = handle_tote_arrived({"tote_id": "T-001", "station_code": "S1"})

    assert intent.kind == RuntimeIntentKind.COMMAND
    assert intent.device_role == "WEIGH_SCALE"
    assert intent.action == "WEIGH_TOTE"
    assert intent.destination.kind == DestinationKind.ROLE
    assert intent.destination.value == "WEIGH_SCALE"


def test_weigh_result_routes_out_of_range_to_ng_lane():
    intent = handle_weigh_result(
        {
            "tote_id": "T-001",
            "actual_weight_kg": 12.5,
            "expected_weight_kg": 10.0,
            "tolerance_kg": 1.0,
        }
    )

    assert intent.kind == RuntimeIntentKind.COMMAND
    assert intent.device_role == "DIVERT_CONVEYOR"
    assert intent.action == "DIVERT_TOTE"
    assert intent.payload_json["destination_lane"] == "NG"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/workline_plugins/test_inbound_tote_qc_v2.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.workline_plugins.inbound_tote_qc_v2'`.

- [ ] **Step 3: Implement the target-style plugin functions**

Create `src/workline_plugins/inbound_tote_qc_v2/plugin.py`:

```python
"""Inbound tote QC plugin using RuntimeIntent instead of plugin state machine."""

from __future__ import annotations

from src.workline_runtime.runtime_intent import Destination, RuntimeIntent


def handle_tote_arrived(payload: dict[str, object]) -> RuntimeIntent:
    tote_id = str(payload["tote_id"])
    station_code = str(payload["station_code"])
    return RuntimeIntent.command(
        device_role="WEIGH_SCALE",
        action="WEIGH_TOTE",
        payload={"tote_id": tote_id, "station_code": station_code},
        destination=Destination.role("WEIGH_SCALE"),
        timeout_seconds=120,
    )


def handle_weigh_result(payload: dict[str, object]) -> RuntimeIntent:
    tote_id = str(payload["tote_id"])
    actual = float(payload["actual_weight_kg"])
    expected = float(payload["expected_weight_kg"])
    tolerance = float(payload["tolerance_kg"])
    destination_lane = "PASS" if abs(actual - expected) <= tolerance else "NG"
    return RuntimeIntent.command(
        device_role="DIVERT_CONVEYOR",
        action="DIVERT_TOTE",
        payload={"tote_id": tote_id, "destination_lane": destination_lane},
        destination=Destination.role("DIVERT_CONVEYOR"),
        timeout_seconds=120,
    )


__all__ = ["handle_tote_arrived", "handle_weigh_result"]
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/workline_plugins/test_inbound_tote_qc_v2.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_plugins/inbound_tote_qc_v2/plugin.py tests/workline_plugins/test_inbound_tote_qc_v2.py
git commit -m "feat: add state-machine-free inbound tote qc plugin"
```

---

## Task 10: Runtime Alerts From Blockers And Metrics

**Files:**
- Create: `src/workline_runtime/alerts.py`
- Create: `tests/workline_runtime/test_runtime_alerts.py`

- [ ] **Step 1: Write failing tests for actionable alerts**

Create `tests/workline_runtime/test_runtime_alerts.py`:

```python
from src.workline_runtime.alerts import build_alerts
from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


def test_block_event_generates_actionable_alert():
    alerts = build_alerts(
        [
            RuntimeEvent(
                event_type=RuntimeEventType.PROCESS_BLOCKED,
                trace_id="trace-1",
                material_identity_key="pkg:PKG-001",
                workline_id=10,
                device_id=21,
                device_role="WEIGH_SCALE",
                reason_code="DEVICE_TIMEOUT",
                failure_domain="HARDWARE",
                owner="MAINTENANCE",
                payload_json={"suggested_action": "检查称重设备通讯"},
            )
        ]
    )

    assert len(alerts) == 1
    assert alerts[0].reason_code == "DEVICE_TIMEOUT"
    assert alerts[0].owner == "MAINTENANCE"
    assert alerts[0].suggested_action == "检查称重设备通讯"
```

- [ ] **Step 2: Run the failing tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_alerts.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.workline_runtime.alerts'`.

- [ ] **Step 3: Implement alerts**

Create `src/workline_runtime/alerts.py`:

```python
"""Actionable runtime alerts derived from RuntimeEvent facts."""

from __future__ import annotations

from dataclasses import dataclass

from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


@dataclass(frozen=True)
class RuntimeAlert:
    trace_id: str
    workline_id: int
    device_id: int | None
    material_identity_key: str | None
    reason_code: str
    owner: str | None
    suggested_action: str | None


def build_alerts(events: list[RuntimeEvent]) -> list[RuntimeAlert]:
    alerts: list[RuntimeAlert] = []
    for event in events:
        if event.event_type != RuntimeEventType.PROCESS_BLOCKED:
            continue
        alerts.append(
            RuntimeAlert(
                trace_id=event.trace_id,
                workline_id=event.workline_id,
                device_id=event.device_id,
                material_identity_key=event.material_identity_key,
                reason_code=event.reason_code or "UNKNOWN",
                owner=event.owner,
                suggested_action=event.payload_json.get("suggested_action"),
            )
        )
    return alerts


__all__ = ["RuntimeAlert", "build_alerts"]
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_alerts.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_runtime/alerts.py tests/workline_runtime/test_runtime_alerts.py
git commit -m "feat: derive runtime alerts from block events"
```

---

## Task 11: Remove Per-Plugin State Machine Requirement

**Files:**
- Modify: `src/workline_runtime/plugin_base.py`
- Modify: `src/workline_runtime/plugin_context.py`
- Modify: `src/workline_runtime/plugin_manifest.py`
- Test: `tests/workline_runtime/test_plugin_base.py`

- [ ] **Step 1: Add failing test that target-style plugins do not declare state machines**

Append to `tests/workline_runtime/test_plugin_base.py`:

```python
from src.workline_runtime.plugin_next import PluginNext


def test_target_plugin_can_return_runtime_intent_without_state_machine():
    next_action = PluginNext()
    intent = next_action.command(
        device_role="WEIGH_SCALE",
        action="WEIGH_TOTE",
        payload={"tote_id": "T-001"},
        destination_role="WEIGH_SCALE",
    )

    assert intent.action == "WEIGH_TOTE"
    assert intent.device_role == "WEIGH_SCALE"
```

- [ ] **Step 2: Run targeted tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_base.py::test_target_plugin_can_return_runtime_intent_without_state_machine -q
```

Expected: PASS after previous tasks; this test locks the new plugin developer surface.

- [ ] **Step 3: Modify plugin context to expose `ctx.next`**

In `src/workline_runtime/plugin_context.py`, add import:

```python
from src.workline_runtime.plugin_next import PluginNext
```

Add field to `PluginContext`:

```python
next: PluginNext = Field(default_factory=PluginNext)
```

In `PluginContextBuilder.build(...)`, pass:

```python
next=PluginNext(),
```

- [ ] **Step 4: Run plugin context and plugin base tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_base.py tests/workline_runtime/test_plugin_next.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_runtime/plugin_context.py tests/workline_runtime/test_plugin_base.py
git commit -m "feat: expose runtime next helper to plugins"
```

---

## Task 12: Migration And Deletion Plan For Legacy State Machines

**Files:**
- Create: `docs/business/workline_material_flow_runtime.md`
- Modify: `docs/business/workline_plugin_architecture_design.md`

- [ ] **Step 1: Write target architecture document**

Create `docs/business/workline_material_flow_runtime.md`:

```markdown
# Workline Material Flow Runtime

## Principle

Plugin owns business judgement. Runtime owns state.

## Plugin Developer Model

The plugin answers:

1. What happened on the current device?
2. What does it mean for the material?
3. Which device should do what next?
4. Should the material complete, block, or go to NG?

The plugin never maintains a state machine, writes persistence state, creates commands directly, handles retries, scans timeouts, or writes monitoring records.

## Runtime Responsibilities

Runtime validates the plugin intent, resolves devices through topology, creates commands, updates MaterialRun, writes RuntimeEvent, creates blockers, updates projections, calculates metrics, and emits alerts.

## Fact Stream

RuntimeEvent is the single source for replay, trace, current views, metrics, and alerts.
```

- [ ] **Step 2: Mark legacy plugin state machine as replaced**

Modify `docs/business/workline_plugin_architecture_design.md` by adding this note near the plugin state machine section:

```markdown
> Target architecture update: per-plugin state machines are replaced by Material Flow Runtime. Plugins return RuntimeIntent; Runtime owns MaterialRun lifecycle and validates topology/device execution.
```

- [ ] **Step 3: Run docs grep check**

Run:

```bash
rtk rg -n "Material Flow Runtime|RuntimeIntent|Plugin owns business judgement" docs/business/workline_material_flow_runtime.md docs/business/workline_plugin_architecture_design.md
```

Expected: Matches in both docs.

- [ ] **Step 4: Commit**

```bash
git add docs/business/workline_material_flow_runtime.md docs/business/workline_plugin_architecture_design.md
git commit -m "docs: document material flow runtime target"
```

---

## Task 13: Complete Operational Metrics Coverage

**Files:**
- Modify: `src/workline_runtime/metrics.py`
- Modify: `tests/workline_runtime/test_runtime_metrics.py`

- [ ] **Step 1: Add failing tests for command latency, plugin quality, and WIP basis**

Append to `tests/workline_runtime/test_runtime_metrics.py`:

```python
from src.workline_runtime.material_run import LifecycleState, MaterialRun
from src.workline_runtime.metrics import aggregate_operational_metrics


def test_operational_metrics_calculate_latency_and_plugin_quality():
    metrics = aggregate_operational_metrics(
        events=[
            RuntimeEvent(
                event_type=RuntimeEventType.COMMAND_ACKED,
                trace_id="t1",
                workline_id=1,
                duration_ms=120,
            ),
            RuntimeEvent(
                event_type=RuntimeEventType.COMMAND_SUCCEEDED,
                trace_id="t1",
                workline_id=1,
                duration_ms=900,
            ),
            RuntimeEvent(
                event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
                trace_id="t2",
                workline_id=1,
                result="INVALID_INTENT",
            ),
            RuntimeEvent(
                event_type=RuntimeEventType.PLUGIN_DECISION_MADE,
                trace_id="t3",
                workline_id=1,
                result="ROUTE_UNREACHABLE",
            ),
        ],
        material_runs=[
            MaterialRun(
                run_code="MR-1",
                material_identity_key="pkg:1",
                workline_id=1,
                lifecycle_state=LifecycleState.ACTIVE,
            ),
            MaterialRun(
                run_code="MR-2",
                material_identity_key="pkg:2",
                workline_id=1,
                lifecycle_state=LifecycleState.BLOCKED,
            ),
            MaterialRun(
                run_code="MR-3",
                material_identity_key="pkg:3",
                workline_id=1,
                lifecycle_state=LifecycleState.COMPLETED,
            ),
        ],
    )

    assert metrics.wip_count == 2
    assert metrics.ack_latency_avg_ms == 120
    assert metrics.result_latency_avg_ms == 900
    assert metrics.invalid_intent_count == 1
    assert metrics.route_unreachable_count == 1
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_metrics.py::test_operational_metrics_calculate_latency_and_plugin_quality -q
```

Expected: FAIL with `ImportError: cannot import name 'aggregate_operational_metrics'`.

- [ ] **Step 3: Extend metrics implementation**

Append to `src/workline_runtime/metrics.py`:

```python
from src.workline_runtime.material_run import LifecycleState, MaterialRun


@dataclass(frozen=True)
class OperationalMetrics:
    wip_count: int = 0
    ack_latency_avg_ms: int | None = None
    result_latency_avg_ms: int | None = None
    invalid_intent_count: int = 0
    route_unreachable_count: int = 0


def _avg(values: list[int]) -> int | None:
    return int(sum(values) / len(values)) if values else None


def aggregate_operational_metrics(*, events: list[RuntimeEvent], material_runs: list[MaterialRun]) -> OperationalMetrics:
    ack_latencies: list[int] = []
    result_latencies: list[int] = []
    invalid_intent_count = 0
    route_unreachable_count = 0

    for event in events:
        if event.event_type == RuntimeEventType.COMMAND_ACKED and event.duration_ms is not None:
            ack_latencies.append(event.duration_ms)
        if event.event_type == RuntimeEventType.COMMAND_SUCCEEDED and event.duration_ms is not None:
            result_latencies.append(event.duration_ms)
        if event.event_type == RuntimeEventType.PLUGIN_DECISION_MADE and event.result == "INVALID_INTENT":
            invalid_intent_count += 1
        if event.event_type == RuntimeEventType.PLUGIN_DECISION_MADE and event.result == "ROUTE_UNREACHABLE":
            route_unreachable_count += 1

    wip_states = {LifecycleState.ACTIVE, LifecycleState.WAITING, LifecycleState.BLOCKED}
    wip_count = sum(1 for run in material_runs if run.lifecycle_state in wip_states)
    return OperationalMetrics(
        wip_count=wip_count,
        ack_latency_avg_ms=_avg(ack_latencies),
        result_latency_avg_ms=_avg(result_latencies),
        invalid_intent_count=invalid_intent_count,
        route_unreachable_count=route_unreachable_count,
    )
```

Extend the existing `__all__` list in `src/workline_runtime/metrics.py`:

```python
__all__ = [
    "OperationalMetrics",
    "RuntimeMetrics",
    "aggregate_operational_metrics",
    "aggregate_runtime_metrics",
]
```

- [ ] **Step 4: Run metrics tests**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_metrics.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workline_runtime/metrics.py tests/workline_runtime/test_runtime_metrics.py
git commit -m "feat: cover operational runtime metrics"
```

---

## Final Verification

- [ ] Run all new tests:

```bash
uv run pytest \
  tests/workline_runtime/test_runtime_intent.py \
  tests/workline_runtime/test_material_run_contract.py \
  tests/workline_runtime/test_runtime_event_contract.py \
  tests/workline_runtime/test_material_target_resolver.py \
  tests/workline_runtime/test_material_flow_engine.py \
  tests/workline_runtime/test_plugin_next.py \
  tests/workline_runtime/test_runtime_projections.py \
  tests/workline_runtime/test_runtime_metrics.py \
  tests/workline_runtime/test_runtime_alerts.py \
  tests/workline_plugins/test_inbound_tote_qc_v2.py \
  -q
```

Expected: PASS.

- [ ] Run formatting and linting for touched runtime/plugin files:

```bash
uv run ruff format src/workline_runtime tests/workline_runtime tests/workline_plugins/test_inbound_tote_qc_v2.py
uv run ruff check src/workline_runtime tests/workline_runtime tests/workline_plugins/test_inbound_tote_qc_v2.py
```

Expected: PASS.

- [ ] Run existing workline runtime tests:

```bash
uv run pytest tests/workline_runtime tests/workline_plugins -q
```

Expected: PASS or only failures caused by intentionally deleted legacy state-machine tests after the deletion task. If legacy tests fail before deletion, update the target API tests and keep old tests isolated.

## Self-Review Checklist

## Completion Note

2026-05-12 旧链路物理清理由 `docs/superpowers/plans/2026-05-12-legacy-workline-runtime-cleanup.md` 承接：生产 callback 链路已迁移到 `RuntimeIntent`，旧 `PluginResult`/per-plugin state machine/状态字段清理以该计划的验收为准。

- [ ] RuntimeIntent covers command, route, complete, block, mark_ng, continue_next, and context patch.
- [ ] MaterialRun directly answers current material device, action, wait, block, completion, and failure.
- [ ] RuntimeEvent carries dimensions for management metrics, operator health, exception attribution, command reliability, and plugin quality.
- [ ] Plugin developer examples do not mention transition, state machine, outbox, retry, timeout scan, or timeline writes.
- [ ] Runtime engine validates topology before command creation.
- [ ] Projections support line, device, material, and blocker views.
- [ ] Metrics cover throughput, WIP basis, step/device durations, NG rate, failure rate, ACK latency, result latency, timeout rate, retry count, busy reject count, offline frequency, and plugin quality counters.
- [ ] Alerts carry affected material, device, reason, owner, suggested action, and trace id.
