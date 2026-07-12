"""Tests for RuntimeInboxService internal event acceptors (Task 7c-a).

覆盖 accept_device_event / accept_internal_event / accept_command_result 三个统一持久化方法。

约束:
- device/internal 必须提供持久 event_id；command result 可从 command_code 派生稳定 identity。
- source_event_id 同 hash ACK、异 hash 冲突。
- provider_code 按调用方语义派生 (device_code 前缀 / 固定 RUNTIME / 固定 DEVICE_RESULT)。
- kind 字段为字符串 (Revision A), 与 accept_received 的 source_event_id-based 路径并存。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

# 这些模型 import 用于注册隔离 SQLite create_all 所需的跨表 FK metadata。
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox

NOW_MS = 1_700_000_000_000

runtime_inbox_service_module = importlib.import_module("src.app.runtime.orchestration.consumers.runtime_inbox_service")
RuntimeInboxService = runtime_inbox_service_module.RuntimeInboxService
RuntimeInboxCorrelationUnavailable = runtime_inbox_service_module.RuntimeInboxCorrelationUnavailable
RuntimeInboxConflict = runtime_inbox_service_module.RuntimeInboxConflict


def test_claim_bucket_separates_workline_and_execution_session_namespaces() -> None:
    common = {
        "provider_code": "TEST",
        "event_type": "INTERNAL_EVENT",
        "source_event_id": "evt-1",
    }

    workline_key = runtime_inbox_service_module._runtime_claim_bucket_key(session_id=41, **common)
    execution_key = runtime_inbox_service_module._runtime_claim_bucket_key(execution_session_id=41, **common)
    both_key = runtime_inbox_service_module._runtime_claim_bucket_key(
        session_id=41,
        execution_session_id=41,
        **common,
    )

    assert workline_key == "workline-session:41"
    assert execution_key == "execution-session:41"
    assert workline_key != execution_key
    assert both_key == workline_key


# ============================================================
# accept_device_event
# ============================================================


@pytest.mark.asyncio
async def test_accept_device_event_rejects_missing_upstream_event_id(db_session) -> None:
    """缺少持久上游 event_id 时必须拒绝，不能把内容 hash 当 occurrence identity。"""

    service = RuntimeInboxService()

    with pytest.raises(ValueError, match="event_id"):
        await service.accept_device_event(
            db_session,
            device_code="ARM_01",
            event_type="SCAN_COMPLETED",
            payload_json={"barcode": "B-001", "qty": 1},
        )


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
async def test_accept_device_event_orphan_trace_does_not_synthesize_correlation_id(db_session) -> None:
    """trace_id 查不到 ExecutionCorrelation 时，不得写入受外键约束的 correlation_id。"""

    service = RuntimeInboxService()

    result = await service.accept_device_event(
        db_session,
        device_code="ARM_02",
        event_type="BARRIER_OPENED",
        payload_json={},
        trace_id="trace-orphan-001",
        event_id="evt-barrier-opened-001",
    )

    assert result.created is True
    assert result.record.correlation_id is None


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
        event_id="evt-auto-commit-001",
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
        event_id="evt-no-auto-commit-001",
        auto_commit=False,
    )
    assert commit_spy.await_count == 0


# ============================================================
# accept_internal_event
# ============================================================


@pytest.mark.asyncio
async def test_accept_internal_event_rejects_missing_upstream_event_id(db_session) -> None:
    """内部事件也必须由 producer 提供持久 occurrence identity。"""

    service = RuntimeInboxService()

    with pytest.raises(ValueError, match="event_id"):
        await service.accept_internal_event(
            db_session,
            event_type="SOURCE_PICK_REQUESTED",
            payload_json={"handoff_demand_id": 1},
        )


@pytest.mark.asyncio
async def test_same_payload_without_occurrence_identity_is_rejected_instead_of_acked(db_session) -> None:
    """两次同内容可能是两次发生；缺 identity 时两次都拒绝，不能第二次错误 ACK。"""

    service = RuntimeInboxService()
    for _occurrence in range(2):
        with pytest.raises(ValueError, match="event_id"):
            await service.accept_device_event(
                db_session,
                device_code="ARM_01",
                event_type="SAME_CONTENT",
                payload_json={"value": "same"},
            )
        with pytest.raises(ValueError, match="event_id"):
            await service.accept_internal_event(
                db_session,
                event_type="SAME_CONTENT",
                payload_json={"value": "same"},
            )

    assert await db_session.scalar(select(func.count()).select_from(RuntimeInbox)) == 0


@pytest.mark.parametrize("producer_kind", ["device", "internal"])
@pytest.mark.asyncio
async def test_persistent_event_id_strips_whitespace_and_accepts_120_char_boundary(
    db_session,
    producer_kind: str,
) -> None:
    """真实 occurrence identity 只做 strip；120 字符边界不得截断。"""

    service = RuntimeInboxService()
    expected_event_id = "e" * 120
    common = {
        "event_type": "IDENTITY_BOUNDARY",
        "payload_json": {"producer": producer_kind},
        "event_id": f"  {expected_event_id}  ",
    }
    if producer_kind == "device":
        result = await service.accept_device_event(db_session, device_code="ARM_01", **common)
    else:
        result = await service.accept_internal_event(db_session, **common)

    assert result.record.source_event_id == expected_event_id
    assert result.record.event_id == expected_event_id
    assert len(result.record.event_id) == 120


@pytest.mark.parametrize("producer_kind", ["device", "internal"])
@pytest.mark.asyncio
async def test_persistent_event_id_rejects_121_chars_before_repository_write(
    db_session,
    producer_kind: str,
) -> None:
    """event_id 超过数据库 120 字符上限时必须在 repository 写入前拒绝。"""

    service = RuntimeInboxService()
    add_spy = AsyncMock(wraps=service.repository.add_received)
    service.repository.add_received = add_spy  # type: ignore[method-assign]
    common = {
        "event_type": "IDENTITY_TOO_LONG",
        "payload_json": {"producer": producer_kind},
        "event_id": "e" * 121,
    }

    with pytest.raises(ValueError, match=r"event_id.*120"):
        if producer_kind == "device":
            await service.accept_device_event(db_session, device_code="ARM_01", **common)
        else:
            await service.accept_internal_event(db_session, **common)

    assert add_spy.await_count == 0


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
    """调用方显式传入已持久化 correlation_id 时，必须直接采用。"""

    service = RuntimeInboxService()
    correlation = ExecutionCorrelation(
        correlation_id="corr-explicit-001",
        trace_id="trace-persisted-correlation",
    )
    db_session.add(correlation)
    await db_session.flush()

    result = await service.accept_internal_event(
        db_session,
        event_type="INTERNAL_HEARTBEAT",
        payload_json={},
        trace_id="trace-not-in-table",
        event_id="evt-explicit-correlation-001",
        correlation_id="corr-explicit-001",
    )

    assert result.created is True
    assert result.record.correlation_id == "corr-explicit-001"


@pytest.mark.asyncio
async def test_accept_internal_event_rejects_unknown_explicit_correlation_id(db_session) -> None:
    """显式 correlation_id 不存在时必须在 service 边界拒绝，而非等待 FK flush 失败。"""

    service = RuntimeInboxService()

    with pytest.raises(RuntimeInboxCorrelationUnavailable) as exc_info:
        await service.accept_internal_event(
            db_session,
            event_type="INTERNAL_HEARTBEAT",
            payload_json={},
            event_id="evt-unknown-correlation-001",
            correlation_id="corr-not-persisted",
        )

    assert exc_info.value.correlation_id == "corr-not-persisted"


@pytest.mark.asyncio
async def test_accept_internal_event_orphan_trace_does_not_synthesize_correlation_id(db_session) -> None:
    """内部事件的孤立 trace_id 只用于追踪，不得冒充 ExecutionCorrelation 外键。"""

    service = RuntimeInboxService()

    result = await service.accept_internal_event(
        db_session,
        event_type="SMT_SOURCE_PICK_REQUESTED",
        payload_json={"source_item_id": 101},
        trace_id="evt-smt-source-pick-101",
        event_id="evt-smt-source-pick-101",
    )

    assert result.record.trace_id == "evt-smt-source-pick-101"
    assert result.record.correlation_id is None


@pytest.mark.asyncio
async def test_accept_internal_event_duplicate_trace_does_not_choose_arbitrary_correlation_id(db_session) -> None:
    """trace_id 非唯一时不得任意选择一条 ExecutionCorrelation。"""

    service = RuntimeInboxService()
    db_session.add_all(
        [
            ExecutionCorrelation(correlation_id="corr-duplicate-trace-a", trace_id="trace-duplicate"),
            ExecutionCorrelation(correlation_id="corr-duplicate-trace-b", trace_id="trace-duplicate"),
        ]
    )
    await db_session.flush()

    result = await service.accept_internal_event(
        db_session,
        event_type="INTERNAL_HEARTBEAT",
        payload_json={},
        trace_id="trace-duplicate",
        event_id="evt-duplicate-trace-001",
    )

    assert result.record.correlation_id is None


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
        get_by_source_event_identity=AsyncMock(return_value=None),
        add_received=AsyncMock(side_effect=IntegrityError("INSERT", {}, Exception("constraint"))),
    )
    service.repository = broken_repo  # type: ignore[assignment]

    with pytest.raises(IntegrityError):
        await service.accept_internal_event(
            db_session,
            event_type="EVT",
            payload_json={},
            event_id="evt-integrity-error-001",
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


@pytest.mark.asyncio
async def test_internal_producers_write_non_empty_priority_bucket_and_received_at(db_session) -> None:
    """内部 producer 必须按身份优先级写稳定桶和毫秒时间。"""

    service = RuntimeInboxService()
    session = ExecutionSession(workline_id=31, manifest_version="manifest-v1", state="RUNNING")
    db_session.add(session)
    await db_session.flush()
    correlation = ExecutionCorrelation(
        correlation_id="corr-internal-lower-priority",
        execution_session_id=session.id,
        trace_id="trace-internal-lower-priority",
    )
    db_session.add(correlation)
    await db_session.flush()

    device = await service.accept_device_event(
        db_session,
        device_code="ARM_09",
        event_type="SCAN_COMPLETED",
        payload_json={"event_type": "SCAN_COMPLETED", "data": {}},
        trace_id="corr-device-lower-priority",
        event_id="evt-device-bucket-001",
        workline_id=31,
        device_id=91,
        command_id=191,
    )
    internal = await service.accept_internal_event(
        db_session,
        event_type="SOURCE_PICK_REQUESTED",
        payload_json={"event_type": "SOURCE_PICK_REQUESTED", "data": {}},
        event_id="evt-internal-bucket-001",
        execution_session_id=session.id,
        correlation_id="corr-internal-lower-priority",
        workline_id=31,
    )
    command = await service.accept_command_result(
        db_session,
        command_code="CMD-BUCKET-001",
        workline_id=31,
        command_id=191,
        payload_json={"event_type": "COMMAND_RESULT", "data": {}},
    )
    fallback = await service.accept_internal_event(
        db_session,
        event_type="RUNTIME_HEARTBEAT",
        payload_json={"event_type": "RUNTIME_HEARTBEAT", "data": {}},
        event_id="evt-fallback-001",
    )

    assert device.record.claim_bucket_key == "device:91"
    assert internal.record.claim_bucket_key == f"execution-session:{session.id}"
    assert command.record.claim_bucket_key == "workline:31"
    assert fallback.record.claim_bucket_key
    assert fallback.record.claim_bucket_key.startswith("source:RUNTIME:RUNTIME_HEARTBEAT:evt-fallback-001")
    assert all(
        isinstance(result.record.received_at, int) and result.record.received_at > 0
        for result in (device, internal, command, fallback)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("producer_kind", ["device", "internal", "command"])
async def test_internal_producers_ack_duplicate_source_identity_with_same_payload(
    db_session, producer_kind: str
) -> None:
    """内部 producer 的稳定 source identity 必须同 hash ACK，不能把唯一索引冲突泄漏给调用方。"""

    service = RuntimeInboxService()
    payload = {"event_type": "DUPLICATE_TEST", "data": {"value": 1}}

    if producer_kind == "device":
        accept = service.accept_device_event
        kwargs = {
            "device_code": "ARM_01",
            "event_type": "DUPLICATE_TEST",
            "event_id": "evt-internal-duplicate-001",
            "payload_json": payload,
        }
    elif producer_kind == "internal":
        accept = service.accept_internal_event
        kwargs = {
            "event_type": "DUPLICATE_TEST",
            "event_id": "evt-internal-duplicate-001",
            "payload_json": payload,
        }
    else:
        accept = service.accept_command_result
        kwargs = {
            "command_code": "CMD-INTERNAL-DUPLICATE-001",
            "payload_json": payload,
        }

    first = await accept(db_session, **kwargs)
    duplicate = await accept(db_session, **kwargs)

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.record.id == first.record.id
    assert first.record.payload_hash


@pytest.mark.asyncio
async def test_internal_producer_rejects_duplicate_source_identity_with_different_payload(db_session) -> None:
    """同一内部 source identity 的不同 canonical payload 必须走显式冲突合同。"""

    service = RuntimeInboxService()
    common = {
        "event_type": "DUPLICATE_TEST",
        "event_id": "evt-internal-conflict-001",
    }
    await service.accept_internal_event(db_session, payload_json={"value": 1}, **common)

    with pytest.raises(RuntimeInboxConflict):
        await service.accept_internal_event(db_session, payload_json={"value": 2}, **common)


@pytest.mark.asyncio
async def test_accept_timer_timeout_writes_canonical_idempotent_runtime_inbox(db_session) -> None:
    """TIMER_TIMEOUT 必须保存 canonical payload、完整路由证据和稳定 source identity。"""

    service = RuntimeInboxService()
    legacy_session_id = 941

    kwargs = {
        "session_id": legacy_session_id,
        "workline_id": 41,
        "deadline_at": "2026-07-11T08:00:00",
        "trace_id": "trace-timeout-001",
        "wait_token": "CMD-TIMEOUT-001",
        "wait_type": "DEVICE_RESULT",
        "awaiting_device_command_code": "CMD-TIMEOUT-001",
        "command_code": "CMD-TIMEOUT-001",
        "device_id": 51,
        "device_code": "ARM_51",
        "command_id": 61,
        "command_status": "ACK_RECEIVED",
        "ack_received_at": "2026-07-11T07:59:00",
        "now_ms": NOW_MS,
    }

    first = await service.accept_timer_timeout(db_session, **kwargs)
    second = await service.accept_timer_timeout(db_session, **kwargs)

    assert first.created is True
    assert second.created is False
    assert second.record.id == first.record.id
    record = first.record
    assert record.kind == "TIMER_TIMEOUT"
    assert record.event_type == "TIMER_TIMEOUT"
    assert record.provider_code == "RUNTIME"
    assert record.source_event_id == (
        f"timeout:{legacy_session_id}:2026-07-11T08:00:00:CMD-TIMEOUT-001:CMD-TIMEOUT-001"
    )
    assert record.execution_session_id is None
    assert record.workline_id == 41
    assert record.device_id == 51
    assert record.command_id == 61
    assert record.trace_id == "trace-timeout-001"
    assert record.claim_bucket_key == f"workline-session:{legacy_session_id}"
    assert record.received_at == NOW_MS
    assert record.payload_hash
    assert record.payload_schema_version == 1
    assert record.payload_json == {
        "event_type": "TIMER_TIMEOUT",
        "data": {
            "session_id": legacy_session_id,
            "workline_id": 41,
            "deadline_at": "2026-07-11T08:00:00",
            "wait_token": "CMD-TIMEOUT-001",
            "wait_type": "DEVICE_RESULT",
            "awaiting_device_command_code": "CMD-TIMEOUT-001",
            "command_code": "CMD-TIMEOUT-001",
            "device_id": 51,
            "device_code": "ARM_51",
            "command_status": "ACK_RECEIVED",
            "ack_received_at": "2026-07-11T07:59:00",
        },
    }


@pytest.mark.asyncio
async def test_accept_timer_timeout_keeps_legacy_and_execution_session_identities_separate(db_session) -> None:
    """仅有真实 runtime 映射时写 execution FK，业务 identity 仍使用 legacy session。"""

    execution_session = ExecutionSession(workline_id=41, manifest_version="manifest-v1", state="RUNNING")
    db_session.add(execution_session)
    await db_session.flush()
    legacy_session_id = 1941

    result = await RuntimeInboxService().accept_timer_timeout(
        db_session,
        session_id=legacy_session_id,
        execution_session_id=execution_session.id,
        workline_id=41,
        deadline_at="2026-07-11T09:00:00",
        wait_token="WAIT-MAPPED-001",
    )

    assert result.record.execution_session_id == execution_session.id
    assert result.record.claim_bucket_key == f"workline-session:{legacy_session_id}"
    assert result.record.source_event_id.startswith(f"timeout:{legacy_session_id}:")
    assert result.record.payload_json["data"]["session_id"] == legacy_session_id


@pytest.mark.asyncio
async def test_smt_source_pick_producer_emits_canonical_workline_session_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SMT INTERNAL_EVENT 必须把 WorklineSession ID 写入 canonical payload。"""
    service_module = importlib.import_module(
        "src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service"
    )
    captured: dict[str, Any] = {}

    class _RuntimeInboxService:
        async def accept_internal_event(self, db: object, **kwargs: Any) -> SimpleNamespace:
            _ = db
            captured.update(kwargs)
            return SimpleNamespace(record=SimpleNamespace(id=501))

    monkeypatch.setattr(runtime_inbox_service_module, "runtime_inbox_service", _RuntimeInboxService())
    producer = service_module.SmtInboundHandoffService()

    record = await producer._create_source_pick_request_inbox(
        SimpleNamespace(),
        demand=SimpleNamespace(
            id=11,
            trace_id="trace-smt",
            rack_release_id="release-1",
            single_layer_rack_code="RACK-1",
        ),
        item=SimpleNamespace(
            id=22,
            claim_attempt_no=3,
            bin_code="BIN-1",
            bin_cell_index=1,
            bin_cell_code="CELL-1",
            material_identity_key="MAT-1",
            pkg_code="PKG-1",
            reel_thickness_mm=None,
        ),
        session=SimpleNamespace(id=33),
        workline_id=44,
        trace_id=None,
        route_evidence={},
    )

    assert record.id == 501
    assert captured["payload_json"]["data"]["session_id"] == 33
    assert captured["execution_session_id"] is None
