"""Tests for RuntimeInboxService internal event acceptors (Task 7c-a).

覆盖 accept_device_event / accept_internal_event / accept_command_result 三个适配方法。
这些方法由 Task 7a 标注的 3 个遗留 WorklineInbox 调用点迁移而来 (Task 7c 启用):

- accept_device_event  ->  WorklineInboxService.create_device_event_inbox
                          (used by runtime_intent_effects._apply_device_event)
- accept_internal_event ->  WorklineInboxService.create_internal_event_inbox
                          (used by smt_inbound_handoff_service._publish_source_pick_request)
- accept_command_result ->  inbox_repo.create with InboxKind.COMMAND_RESULT
                          (used by runtime_hold_release_service)

约束:
- 跳过 source_event_id 幂等检查 (内部事件, source_event_id 不保证唯一)。
- provider_code 按调用方语义派生 (device_code 前缀 / 固定 RUNTIME / 固定 DEVICE_RESULT)。
- kind 字段为字符串 (Revision A), 与 accept_received 的 source_event_id-based 路径并存。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

# 这些模型 import 用于注册隔离 SQLite create_all 所需的跨表 FK metadata。
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox

NOW_MS = 1_700_000_000_000

runtime_inbox_service_module = importlib.import_module("src.app.runtime.orchestration.consumers.runtime_inbox_service")
RuntimeInboxService = runtime_inbox_service_module.RuntimeInboxService


# ============================================================
# accept_device_event
# ============================================================


@pytest.mark.asyncio
async def test_accept_device_event_minimal_args_writes_received(db_session) -> None:
    """最小参数 (仅 device_code + event_type + payload_json) 必须成功写入 DEVICE_EVENT 行。"""

    service = RuntimeInboxService()

    result = await service.accept_device_event(
        db_session,
        device_code="ARM_01",
        event_type="SCAN_COMPLETED",
        payload_json={"barcode": "B-001", "qty": 1},
    )

    assert result.created is True
    record = result.record
    assert record.id is not None
    assert record.kind == "DEVICE_EVENT"
    assert record.provider_code == "ARM"
    assert record.event_type == "SCAN_COMPLETED"
    assert record.source_event_id is None
    assert record.status == "RECEIVED"
    assert record.payload_json == {"barcode": "B-001", "qty": 1}
    assert record.workline_id is None
    assert record.device_id is None
    assert record.command_id is None
    assert record.trace_id is None
    assert record.event_id is None
    assert record.causation_id is None
    assert record.correlation_id is None
    assert record.max_retries == 5
    assert record.attempt_count == 0

    # 数据库行确实落库 (无 commit 也会被 session.flush 持久化到 transaction)。
    rows = (await db_session.execute(select(RuntimeInbox).where(RuntimeInbox.kind == "DEVICE_EVENT"))).scalars()
    assert {row.id for row in rows} == {record.id}


@pytest.mark.asyncio
async def test_accept_device_event_with_all_optional_args(db_session) -> None:
    """所有可选参数 (trace_id/event_id/causation_id/workline/device/command) 必须正确落库。"""

    service = RuntimeInboxService()

    session = ExecutionSession(workline_id=11, manifest_version="manifest-v1", state="RUNNING")
    db_session.add(session)
    await db_session.flush()

    trace_id = "trace-device-evt-001"
    correlation = ExecutionCorrelation(
        correlation_id="corr-device-evt-001",
        execution_session_id=session.id,
        trace_id=trace_id,
    )
    db_session.add(correlation)
    await db_session.flush()

    result = await service.accept_device_event(
        db_session,
        device_code="OVEN_03",
        event_type="ESTOP_PRESSED",
        payload_json={"severity": "high"},
        trace_id=trace_id,
        event_id="evt-device-001",
        causation_id="evt-device-cause-001",
        workline_id=11,
        device_id=42,
        command_id=99,
    )

    assert result.created is True
    record = result.record
    assert record.kind == "DEVICE_EVENT"
    assert record.provider_code == "OVEN"
    assert record.event_type == "ESTOP_PRESSED"
    assert record.source_event_id == "evt-device-001"
    assert record.workline_id == 11
    assert record.device_id == 42
    assert record.command_id == 99
    assert record.trace_id == trace_id
    assert record.event_id == "evt-device-001"
    assert record.causation_id == "evt-device-cause-001"
    # trace_id 反查命中 -> correlation_id 取 ExecutionCorrelation.correlation_id
    assert record.correlation_id == "corr-device-evt-001"


@pytest.mark.asyncio
async def test_accept_device_event_trace_id_falls_back_when_correlation_missing(db_session) -> None:
    """trace_id 在 ExecutionCorrelation 查不到时, correlation_id 回退为 trace_id 自身。"""

    service = RuntimeInboxService()

    result = await service.accept_device_event(
        db_session,
        device_code="ARM_02",
        event_type="BARRIER_OPENED",
        payload_json={},
        trace_id="trace-orphan-001",
    )

    assert result.created is True
    assert result.record.correlation_id == "trace-orphan-001"


@pytest.mark.parametrize(
    ("device_code", "expected_provider_code"),
    [
        ("ARM_01", "ARM"),
        ("OVEN_99", "OVEN"),
        ("agv_07", "AGV"),
        ("X", "X"),
        ("", "ECS"),
    ],
)
def test_accept_device_event_provider_code_derivation(device_code: str, expected_provider_code: str) -> None:
    """device_code 前缀 (大小写不敏感) 必须稳定派生 provider_code, 缺省 "ECS"。"""

    derived = RuntimeInboxService._derive_provider_code_for_device(device_code)
    assert derived == expected_provider_code


@pytest.mark.asyncio
async def test_accept_device_event_rejects_missing_event_type(db_session) -> None:
    """event_type 缺失必须抛 ValueError, 不静默落库。"""

    service = RuntimeInboxService()

    with pytest.raises(ValueError, match="event_type"):
        await service.accept_device_event(
            db_session,
            device_code="ARM_01",
            event_type="",
            payload_json={},
        )

    rows = (await db_session.execute(select(RuntimeInbox))).scalars()
    assert list(rows) == []


@pytest.mark.asyncio
async def test_accept_device_event_rejects_non_dict_payload(db_session) -> None:
    """payload_json 非 dict 必须抛 TypeError, 不静默落库。"""

    service = RuntimeInboxService()

    with pytest.raises(TypeError, match="payload_json"):
        await service.accept_device_event(
            db_session,
            device_code="ARM_01",
            event_type="SCAN_COMPLETED",
            payload_json="not-a-dict",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_accept_device_event_auto_commit_true_calls_db_commit(db_session) -> None:
    """auto_commit=True 必须调用 db.commit(); auto_commit=False 必须不调用。"""

    service = RuntimeInboxService()

    commit_spy = AsyncMock()
    db_session.commit = commit_spy  # type: ignore[method-assign]

    result = await service.accept_device_event(
        db_session,
        device_code="ARM_01",
        event_type="SCAN_COMPLETED",
        payload_json={"x": 1},
        auto_commit=True,
    )
    assert result.created is True
    assert commit_spy.await_count == 1

    commit_spy.reset_mock()
    _ = await service.accept_device_event(
        db_session,
        device_code="ARM_02",
        event_type="SCAN_COMPLETED",
        payload_json={"x": 2},
        auto_commit=False,
    )
    assert commit_spy.await_count == 0


# ============================================================
# accept_internal_event
# ============================================================


@pytest.mark.asyncio
async def test_accept_internal_event_minimal_args_writes_received(db_session) -> None:
    """最小参数 (仅 event_type + payload_json) 必须成功写入 INTERNAL_EVENT 行。"""

    service = RuntimeInboxService()

    result = await service.accept_internal_event(
        db_session,
        event_type="SOURCE_PICK_REQUESTED",
        payload_json={"handoff_demand_id": 1},
    )

    assert result.created is True
    record = result.record
    assert record.id is not None
    assert record.kind == "INTERNAL_EVENT"
    assert record.provider_code == "RUNTIME"
    assert record.event_type == "SOURCE_PICK_REQUESTED"
    assert record.source_event_id is None
    assert record.status == "RECEIVED"
    assert record.payload_json == {"handoff_demand_id": 1}
    assert record.correlation_id is None
    assert record.execution_session_id is None


@pytest.mark.asyncio
async def test_accept_internal_event_with_all_optional_args(db_session) -> None:
    """所有可选参数必须正确落库, correlation_id 走 trace_id 反查。"""

    service = RuntimeInboxService()

    session = ExecutionSession(workline_id=21, manifest_version="manifest-v1", state="RUNNING")
    db_session.add(session)
    await db_session.flush()

    trace_id = "trace-internal-001"
    correlation = ExecutionCorrelation(
        correlation_id="corr-internal-001",
        execution_session_id=session.id,
        trace_id=trace_id,
    )
    db_session.add(correlation)
    await db_session.flush()

    result = await service.accept_internal_event(
        db_session,
        event_type="SOURCE_PICK_REQUESTED",
        payload_json={"handoff_demand_id": 7},
        trace_id=trace_id,
        event_id="evt-internal-001",
        causation_id="evt-internal-cause-001",
        workline_id=21,
        execution_session_id=session.id,
        correlation_id=None,
    )

    assert result.created is True
    record = result.record
    assert record.kind == "INTERNAL_EVENT"
    assert record.provider_code == "RUNTIME"
    assert record.event_type == "SOURCE_PICK_REQUESTED"
    assert record.source_event_id == "evt-internal-001"
    assert record.workline_id == 21
    assert record.execution_session_id == session.id
    assert record.trace_id == trace_id
    assert record.event_id == "evt-internal-001"
    assert record.causation_id == "evt-internal-cause-001"
    assert record.correlation_id == "corr-internal-001"


@pytest.mark.asyncio
async def test_accept_internal_event_uses_explicit_correlation_id(db_session) -> None:
    """调用方显式传 correlation_id 时, 必须直接采纳, 不做 trace_id 反查。"""

    service = RuntimeInboxService()

    result = await service.accept_internal_event(
        db_session,
        event_type="INTERNAL_HEARTBEAT",
        payload_json={},
        trace_id="trace-not-in-table",
        correlation_id="corr-explicit-001",
    )

    assert result.created is True
    assert result.record.correlation_id == "corr-explicit-001"


@pytest.mark.asyncio
async def test_accept_internal_event_rejects_missing_event_type(db_session) -> None:
    """event_type 缺失必须抛 ValueError。"""

    service = RuntimeInboxService()

    with pytest.raises(ValueError, match="event_type"):
        await service.accept_internal_event(
            db_session,
            event_type="",
            payload_json={},
        )


@pytest.mark.asyncio
async def test_accept_internal_event_propagates_db_integrity_error(db_session) -> None:
    """repository.add_received 抛 IntegrityError 时, accept_internal_event 必须透传。"""

    service = RuntimeInboxService()

    broken_repo = SimpleNamespace(
        add_received=AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("constraint")))
    )
    service.repository = broken_repo  # type: ignore[assignment]

    with pytest.raises(IntegrityError):
        await service.accept_internal_event(
            db_session,
            event_type="EVT",
            payload_json={},
        )

    assert broken_repo.add_received.await_count == 1


# ============================================================
# accept_command_result
# ============================================================


@pytest.mark.asyncio
async def test_accept_command_result_with_device_code_uses_device_result_provider(db_session) -> None:
    """有 device_code 时, provider_code 必须是 "DEVICE_RESULT"。"""

    service = RuntimeInboxService()

    result = await service.accept_command_result(
        db_session,
        command_code="CMD-CONT-001",
        device_code="ARM_05",
        workline_id=11,
        device_id=42,
        command_id=99,
        trace_id="trace-cmd-001",
        event_id="evt-cmd-001",
        causation_id="evt-cmd-cause-001",
        payload_json={"result": "SUCCESS", "data": {"qty": 1}},
    )

    assert result.created is True
    record = result.record
    assert record.kind == "COMMAND_RESULT"
    assert record.provider_code == "DEVICE_RESULT"
    assert record.event_type == "COMMAND_RESULT"
    assert record.source_event_id == "evt-cmd-001"
    assert record.workline_id == 11
    assert record.device_id == 42
    assert record.command_id == 99
    assert record.trace_id == "trace-cmd-001"
    assert record.event_id == "evt-cmd-001"
    assert record.causation_id == "evt-cmd-cause-001"
    assert record.payload_json == {"result": "SUCCESS", "data": {"qty": 1}}


@pytest.mark.asyncio
async def test_accept_command_result_without_device_uses_runtime_provider(db_session) -> None:
    """无 device_code 时 (synthesize 场景), provider_code 必须回退 "RUNTIME"。"""

    service = RuntimeInboxService()

    result = await service.accept_command_result(
        db_session,
        command_code="CMD-CONT-002",
        trace_id="trace-synth-001",
        event_id="evt-synth-001",
        payload_json={"runtime_hold_release": True},
    )

    assert result.created is True
    record = result.record
    assert record.kind == "COMMAND_RESULT"
    assert record.provider_code == "RUNTIME"
    assert record.source_event_id == "evt-synth-001"
    assert record.workline_id is None


@pytest.mark.asyncio
async def test_accept_command_result_synthesizes_source_event_id_when_missing(db_session) -> None:
    """无 event_id 时, source_event_id 必须派生为稳定可读字符串 (含 command_code)。"""

    service = RuntimeInboxService()

    result = await service.accept_command_result(
        db_session,
        command_code="CMD-CONT-003",
        device_code="ARM_01",
        payload_json={"result": "SUCCESS"},
    )

    assert result.created is True
    record = result.record
    assert record.source_event_id is not None
    assert "CMD-CONT-003" in record.source_event_id
    assert record.event_id is None
    # provider_code 走 DEVICE_RESULT 路径 (有 device_code)
    assert record.provider_code == "DEVICE_RESULT"


@pytest.mark.asyncio
async def test_accept_command_result_rejects_missing_command_code(db_session) -> None:
    """command_code 缺失必须抛 ValueError, 不静默落库。"""

    service = RuntimeInboxService()

    with pytest.raises(ValueError, match="command_code"):
        await service.accept_command_result(
            db_session,
            command_code="",
            device_code="ARM_01",
        )


@pytest.mark.asyncio
async def test_accept_command_result_auto_commit_true_calls_db_commit(db_session) -> None:
    """auto_commit=True 必须调用 db.commit(); auto_commit=False 必须不调用。"""

    service = RuntimeInboxService()

    commit_spy = AsyncMock()
    db_session.commit = commit_spy  # type: ignore[method-assign]

    result = await service.accept_command_result(
        db_session,
        command_code="CMD-COMMIT-001",
        device_code="ARM_01",
        payload_json={"result": "SUCCESS"},
        auto_commit=True,
    )
    assert result.created is True
    assert commit_spy.await_count == 1

    commit_spy.reset_mock()
    _ = await service.accept_command_result(
        db_session,
        command_code="CMD-COMMIT-002",
        device_code="ARM_01",
        payload_json={"result": "SUCCESS"},
        auto_commit=False,
    )
    assert commit_spy.await_count == 0
