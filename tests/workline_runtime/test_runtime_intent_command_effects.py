from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.workline.services import write_back_service as workline_effects
from src.workline_runtime.orchestrator import OrchestratorResult
from src.workline_runtime.runtime_intent import Destination, RuntimeIntent
from src.workline_runtime.runtime_intent_effects import (
    RuntimeIntentEffectApplier,
    _resolve_command_result_timeout_seconds,
)
from tests.workline_runtime.support.runtime_intent_effects import (
    _ctx,
    _session,
)


@pytest.mark.asyncio
async def test_command_intent_creates_command_outbox_and_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    target = SimpleNamespace(id=2, device_code="CONV01", device_role="CONVEYOR", upstream_device_id=1)
    created_command = SimpleNamespace(
        id=88,
        command_code="CMD-TEST-001",
        task_type="MOVE_FORWARD",
        priority=5,
        timeout_ms=30000,
        params={"pkg_id": "PKG-001"},
    )
    created_payloads: list[dict[str, Any]] = []
    timelines: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source], "CONVEYOR": [target]}
    generated_command_codes: list[tuple[str, int | None]] = []

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return created_command

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr(
        workline_effects,
        "_build_command_code",
        lambda task_type, *, session_id=None: (
            generated_command_codes.append((task_type, session_id)) or "CMD-20260101-S123-MOVE_FORWARD-ABCDEF12"
        ),
    )
    monkeypatch.setattr(
        "src.app.device.repositories.command_repository.DeviceCommandRepository.create",
        fake_create,
    )
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="MOVE_FORWARD",
                payload={"pkg_id": "PKG-001"},
                destination=Destination.role("CONVEYOR"),
                timeout_seconds=300,
            )
        ],
    )

    assert created_payloads[0]["device_id"] == 2
    assert created_payloads[0]["command_code"] == "CMD-20260101-S123-MOVE_FORWARD-ABCDEF12"
    assert created_payloads[0]["task_type"] == "MOVE_FORWARD"
    assert created_payloads[0]["correlation_id"] is None
    assert generated_command_codes == [("MOVE_FORWARD", 123)]
    assert session.status == "WAITING_DEVICE_RESULT"
    assert session.awaiting_device_command_code == "CMD-TEST-001"
    db.execute.assert_awaited_once()
    assert db.add.call_count == 1
    assert [timeline["related_command_id"] for timeline in timelines] == [88, 88]
    assert timelines[0]["payload"]["task_type"] == "MOVE_FORWARD"
    assert "command_type" not in timelines[0]["payload"]


@pytest.mark.asyncio
async def test_command_intent_uses_explicit_execution_correlation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    target = SimpleNamespace(id=2, device_code="CONV01", device_role="CONVEYOR", upstream_device_id=1)
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["correlation_id"] = "corr-runtime-001"
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source], "CONVEYOR": [target]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-TEST-001",
            task_type="MOVE_FORWARD",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="MOVE_FORWARD",
                payload={},
                destination=Destination.role("CONVEYOR"),
                timeout_seconds=300,
            )
        ],
    )

    assert created_payloads[0]["correlation_id"] == "corr-runtime-001"


@pytest.mark.asyncio
async def test_command_intent_without_destination_uses_device_role_as_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = SimpleNamespace(id=1, device_code="ARM03", device_role="ROUGH_SORTER_INPUT_ARM")
    target = SimpleNamespace(
        id=2,
        device_code="PIPELINE02",
        device_role="ROUGH_SORTER_CONVEYOR",
        upstream_device_id=1,
    )
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"ROUGH_SORTER_INPUT_ARM": [source], "ROUGH_SORTER_CONVEYOR": [target]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-MOVE-FORWARD",
            task_type="MOVE_FORWARD",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="MOVE_FORWARD",
                device_role="ROUGH_SORTER_CONVEYOR",
                payload={},
                timeout_seconds=30,
            )
        ],
    )

    assert created_payloads[0]["device_id"] == 2
    assert db.add.call_args.args[0].target_code == "PIPELINE02"


@pytest.mark.asyncio
async def test_command_intent_uses_payload_timeout_when_intent_timeout_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timelines: list[dict[str, Any]] = []
    created_payloads: list[dict[str, Any]] = []
    created_command = SimpleNamespace(
        id=88,
        command_code="CMD-NO-TIMEOUT",
        task_type="MEASUREMENT_REEL",
        priority=5,
        timeout_ms=180000,
        params={"business_key": "PKG-001"},
    )
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(
        status="NEW",
        current_wait_type=None,
        awaiting_device_command_code=None,
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
                payload={"task_type": "MEASUREMENT_REEL", "timeout": 180000, "params": {"business_key": "PKG-001"}},
                destination=Destination.current(),
            )
        ],
    )

    assert created_payloads[0]["device_id"] == 1
    assert created_payloads[0]["task_type"] == "MEASUREMENT_REEL"
    assert created_payloads[0]["params"] == {"business_key": "PKG-001"}
    assert session.status == "WAITING_DEVICE_RESULT"
    assert session.current_wait_type == "COMMAND_RESULT"
    assert session.awaiting_device_command_code == "CMD-NO-TIMEOUT"
    assert session.waiting_since == ctx["now"]
    assert session.current_wait_timeout_seconds == 180
    assert session.deadline_at is None
    db.execute.assert_awaited_once()
    assert [timeline["action_type"].value for timeline in timelines] == ["COMMAND_SENT", "WAIT_STARTED"]
    assert timelines[1]["payload"]["wait_token"] == "CMD-NO-TIMEOUT"
    assert timelines[1]["payload"]["deadline_seconds"] == 180


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
                payload={"timeout": 0},
                destination=Destination.current(),
            ),
            1,
        ),
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": -100},
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
        (
            RuntimeIntent.command(
                action="MEASUREMENT_REEL",
                payload={"timeout": 300000},
                destination=Destination.current(),
                timeout_seconds=0,
            ),
            1,
        ),
    ],
)
def test_command_result_timeout_resolution(intent: RuntimeIntent, expected_timeout_seconds: int) -> None:
    assert _resolve_command_result_timeout_seconds(intent) == expected_timeout_seconds


@pytest.mark.asyncio
async def test_command_destination_current_targets_source_device(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-CURRENT",
            task_type="SCAN",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [RuntimeIntent.command(action="SCAN", payload={}, destination=Destination.current(), timeout_seconds=30)],
    )

    assert created_payloads[0]["device_id"] == 1


@pytest.mark.asyncio
async def test_command_destination_next_targets_topology_downstream(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    target = SimpleNamespace(id=2, device_code="CONV01", device_role="CONVEYOR", upstream_device_id=1)
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source], "CONVEYOR": [target]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-NEXT",
            task_type="MOVE_FORWARD",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [RuntimeIntent.command(action="MOVE_FORWARD", payload={}, destination=Destination.next(), timeout_seconds=30)],
    )

    assert created_payloads[0]["device_id"] == 2


@pytest.mark.asyncio
async def test_command_destination_device_outside_topology_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    timelines: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source]}

    async def capture_timeline(_ctx: dict[str, Any], **kwargs: Any) -> None:
        timelines.append(kwargs)

    create_command = AsyncMock()
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", create_command)
    monkeypatch.setattr(workline_effects, "_emit_timeline", capture_timeline)

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="MOVE_FORWARD", payload={}, destination=Destination.device(99), timeout_seconds=30
            )
        ],
    )

    create_command.assert_not_awaited()
    assert session.status == "MANUAL_HOLD"
    assert session.failure_code == "DESTINATION_UNREACHABLE"
    assert "No destination matched" in timelines[0]["payload"]["message"]


@pytest.mark.asyncio
async def test_command_destination_ng_route_uses_configured_route_role(monkeypatch: pytest.MonkeyPatch) -> None:
    source = SimpleNamespace(id=1, device_code="ARM01", device_role="INPUT_ARM")
    ng_target = SimpleNamespace(id=3, device_code="NG01", device_role="NG_BUFFER", upstream_device_id=1)
    created_payloads: list[dict[str, Any]] = []
    db = SimpleNamespace(add=MagicMock(), execute=AsyncMock())
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session, db=db)
    ctx["workline"].config = {"route_roles": {"NG_ROUTE": "NG_BUFFER"}}
    ctx["source_device"] = source
    ctx["devices_by_role"] = {"INPUT_ARM": [source], "NG_BUFFER": [ng_target]}

    async def fake_create(_repo, _db, payload):
        created_payloads.append(payload)
        return SimpleNamespace(
            id=88,
            command_code="CMD-NG",
            task_type="PICK_AND_PUT",
            priority=5,
            timeout_ms=30000,
            params={},
        )

    monkeypatch.setattr(workline_effects, "_enforce_device_command_governance", lambda *_, **__: None)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", fake_create)
    monkeypatch.setattr(workline_effects, "_emit_timeline", AsyncMock())

    await RuntimeIntentEffectApplier().apply(
        ctx,
        [
            RuntimeIntent.command(
                action="PICK_AND_PUT", payload={}, destination=Destination.ng_route(), timeout_seconds=30
            )
        ],
    )

    assert created_payloads[0]["device_id"] == 3


@pytest.mark.asyncio
async def test_invalid_combinations_are_rejected_before_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)

    with pytest.raises(ValueError, match="terminal RuntimeIntent must be final intent"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.complete({"done": True}),
                RuntimeIntent.update_context({"late": True}),
            ],
        )

    assert session.status == "RUNNING"
    assert session.context_json == {}
    emit_timeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_before_terminal_intent_is_rejected_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(status="RUNNING", current_wait_type=None, awaiting_device_command_code=None)
    ctx = _ctx(OrchestratorResult(success=True, intents=[]), session=session)
    emit_timeline = AsyncMock()
    create_command = AsyncMock()

    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    monkeypatch.setattr("src.app.device.repositories.command_repository.DeviceCommandRepository.create", create_command)

    with pytest.raises(ValueError, match="terminal RuntimeIntent cannot follow command-producing RuntimeIntent"):
        await RuntimeIntentEffectApplier().apply(
            ctx,
            [
                RuntimeIntent.command(action="MOVE_FORWARD", payload={}, destination=Destination.current()),
                RuntimeIntent.complete({"done": True}),
            ],
        )

    assert session.status == "RUNNING"
    assert session.ended_at is None
    assert ctx["db"].add.call_count == 0
    create_command.assert_not_awaited()
    emit_timeline.assert_not_awaited()
