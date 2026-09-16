"""持久 drain 前驱链与原 CTU03 接纳释放，使用真实集合查询。"""

from datetime import datetime, timedelta
from importlib import import_module

import pytest
import wes_plugin_sdk as sdk
from test_batch_repository import _new_sessions

from src.app.execution.models import InboundEvidence, TransportDecisionBinding, WmsConfirmation
from src.app.transport.models import TransportTask
from src.app.wms_adapter.return_buffer_drain.typed import encode_request
from src.app.wms_adapter.return_buffer_drain.wire import RETURN_BUFFER_DRAIN_OPERATION
from src.utils.canonical_json import canonical_json_digest

NOW = datetime(2026, 9, 15, 12)


def op(n):
    return f"019f0000-0000-7000-8000-{n:012d}"


async def add_decision(
    db,
    n=1,
    *,
    predecessor=None,
    status="COMPLETED",
    result="READY",
    published=True,
    drain_reason="PICKING_TASK_COMPLETED",
):
    payload = encode_request(
        sdk.wms_operations.workline_return_buffer_drain_rack_decide(
            operation_id=op(n),
            workline_code="LINE-1",
            plugin_key="manual-picking",
            drain_reason=drain_reason,
            previous_operation_id=op(predecessor) if predecessor else None,
            return_candidates=(sdk.BinReturnCandidate(1, "B1", "OUTLET"),),
        ),
        timestamp=1,
    )
    c = WmsConfirmation(
        operation=RETURN_BUFFER_DRAIN_OPERATION,
        operation_id=op(n),
        workline_id=7,
        request_payload=payload,
        request_digest=canonical_json_digest(payload),
        deadline_at=NOW,
        created_at=NOW + timedelta(seconds=n),
        status=status,
        completed_at=NOW + timedelta(seconds=n),
        response_result=result,
    )
    response = {
        "operation_id": op(n),
        "code": "DECIDED",
        "timestamp": 1,
        "data": (
            {"result": "READY", "rack_id": "DR1", "rack_face": "90"}
            if result == "READY"
            else {"result": "WAIT", "reason_code": "NO_DRAIN_RACK_AVAILABLE", "retry_after_ms": 1000}
        ),
    }
    e = InboundEvidence(
        kind="WMS_RESULT",
        source_identity=f"drain:{n}",
        workline_id=7,
        operation=RETURN_BUFFER_DRAIN_OPERATION,
        operation_id=op(n),
        normalized_payload=response,
        payload_digest=canonical_json_digest(response),
        received_at=NOW,
        apply_status="APPLIED",
        decision_digest="a" * 64,
        published_at=NOW if published else None,
    )
    db.add(e)
    await db.flush()
    c.response_evidence_id = e.id
    db.add(c)
    await db.flush()
    return c, e


async def add_departure(db, evidence, status, *, accepted=False, wrong_evidence=False, identity="out"):
    db.add(
        TransportDecisionBinding(
            workline_id=7,
            correlation_id=f"drain:{evidence.operation_id}",
            step="MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_OUT",
            resource_fence_id="DR1",
            source_evidence_id=evidence.id + 1 if wrong_evidence else evidence.id,
            client_request_id=identity,
        )
    )
    db.add(
        TransportTask(
            transport_task_id=identity,
            client_request_id=identity,
            request_digest="a" * 64,
            kind="RACK_MOVE",
            caller_json={"workline_id": "7"},
            request_json={"rack_id": "DR1"},
            submit_operation_id=identity,
            submit_timestamp_ms=1,
            submit_request_body="{}",
            submit_request_body_digest="b" * 64,
            status=status,
            authority_workline_id=7,
            created_at=NOW,
            updated_at=NOW,
            result_deadline_at=NOW if accepted else None,
        )
    )
    await db.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,accepted,closed",
    [
        ("PENDING", False, False),
        ("ACCEPTED", True, True),
        ("SUCCEEDED", True, True),
        ("FAILED", True, True),
        ("RECONCILING", False, False),
        ("RECONCILING", True, True),
        ("REJECTED", False, False),
    ],
)
async def test_chain_reservation_releases_only_matching_durable_departure(status, accepted, closed):
    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            _, e = await add_decision(db)
            repo = module.DrainRepository()
            assert await repo.is_reserved(db, 7)
            await add_departure(db, e, status, accepted=accepted)
            assert await repo.is_reserved(db, 7) is not closed
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "fork",
        "broken",
        "second_root",
        "request_drift",
        "owner_drift",
        "premature_successor",
        "unsupported_reason",
    ],
)
async def test_malformed_chain_fails_closed(case):
    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            if case == "unsupported_reason":
                await add_decision(db, result="WAIT", drain_reason="WORKLINE_STOPPING")
                await db.flush()
                with pytest.raises(ValueError, match="unsupported"):
                    await module.DrainRepository().current(db, 7)
                return
            c, _e = await add_decision(db, result="WAIT")
            if case == "request_drift":
                c.request_digest = "f" * 64
            elif case == "owner_drift":
                c.request_payload = {**c.request_payload, "data": {**c.request_payload["data"], "plugin_key": "other"}}
            elif case == "second_root":
                await add_decision(db, 2)
            elif case == "broken":
                await add_decision(db, 2, predecessor=99)
            elif case == "premature_successor":
                c.completed_at = NOW + timedelta(seconds=5)
                await add_decision(db, 2, predecessor=1)
            else:
                await add_decision(db, 2, predecessor=1, result="WAIT")
                await add_decision(db, 3, predecessor=1)
            await db.flush()
            with pytest.raises(ValueError):
                await module.DrainRepository().current(db, 7)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wait_chain_is_direct_and_survives_reader_restart():
    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            await add_decision(db, result="WAIT")
            await add_decision(db, 2, predecessor=1, result="WAIT")
            await add_decision(db, 3, predecessor=2)
        async with sessions.begin() as db:
            current = await module.DrainRepository().current(db, 7)
            assert current.intent.operation_id == op(3) and current.intent.previous_operation_id == op(2)
            assert current.result == sdk.ReturnBufferDrainReady("DR1", "90")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unpublished_ready_and_wrong_departure_evidence_retain_chain():
    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            _, e = await add_decision(db, published=False)
            repo = module.DrainRepository()
            assert (await repo.current(db, 7)).result is None
            e.published_at = NOW
            await db.flush()
            await add_departure(db, e, "ACCEPTED", accepted=True, wrong_evidence=True)
            with pytest.raises(ValueError):
                await repo.current(db, 7)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "wrong_transport",
        "pending",
        "wrong_object",
        "wrong_kind",
        "unknown",
        "wrong_position",
        "wrong_face",
        "failed_member",
    ],
)
async def test_drain_arrival_requires_exact_successful_member_and_projection(case):
    from types import SimpleNamespace

    from src.app.transport.models import TransportMember

    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            db.add(
                TransportMember(
                    transport_task_id="in",
                    ordinal=1,
                    object_type="BIN" if case == "wrong_kind" else "RACK",
                    object_id="OTHER" if case == "wrong_object" else "DR1",
                    source_json={},
                    target_json={},
                    status="FAILED" if case == "failed_member" else "SUCCEEDED",
                    updated_at=NOW,
                    position_unknown=case == "unknown",
                    arrival_face="270" if case == "wrong_face" else "90",
                    final_position_json={
                        "kind": "RACK_POSITION",
                        "location_code": "OTHER" if case == "wrong_position" else "FIVE",
                    },
                )
            )
            await db.flush()
            transport = SimpleNamespace(
                transport_task_id="other" if case == "wrong_transport" else "in",
                status="PENDING" if case == "pending" else "SUCCEEDED",
            )
            projection = SimpleNamespace(
                source_transport_task_id="in", position_json={"kind": "RACK_POSITION", "location_code": "FIVE"}
            )
            assert await module.DrainRepository().arrival_matches(db, transport, projection, "DR1", "90") is (
                case == "valid"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_completed_chain_allows_new_root_without_reopening_old_identity():
    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            _, e = await add_decision(db)
            await add_departure(db, e, "ACCEPTED", accepted=True)
            await add_decision(db, 2, status="PENDING", published=False)
            current = await module.DrainRepository().current(db, 7)
            assert current.intent.operation_id == op(2) and current.intent.previous_operation_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_source_cycle_blocks_drain_but_independent_transfer_does_not():
    from sqlalchemy import select

    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            _, e = await add_decision(db)
            await add_departure(db, e, "RECONCILING")
            repo = module.DrainRepository()
            assert await repo.has_unclosed_rack_action(db, 7)
            binding = await db.scalar(select(TransportDecisionBinding))
            binding.step = "MANUAL_PICKING_TRANSFER_RACK_OUT"
            await db.flush()
            assert not await repo.has_unclosed_rack_action(db, 7)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_drain_trigger_reads_only_completed_tasks_bound_to_this_line():
    from src.app.wms_integration.outbound_picking.models import PickingTask

    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(PickingTask.__table__.create)
        async with sessions.begin() as db:
            task = PickingTask(
                task_id="PICK-1",
                task_type="MANUAL",
                status="EXECUTING",
                workline_id=7,
                queue_revision=1,
                dispatch_sequence=1,
                issued_at_ms=1,
                issued_evidence_id=51,
            )
            db.add(task)
            await db.flush()
            repo = module.DrainRepository()
            assert not await repo.has_completed_task(db, 7)
            task.status = "EXECUTION_COMPLETED"
            await db.flush()
            assert await repo.has_completed_task(db, 7)
            assert not await repo.has_completed_task(db, 8)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_drain_current_under_workline_lock_uses_nonlocking_snapshot():
    from types import SimpleNamespace

    from sqlalchemy.dialects.postgresql import dialect

    module = import_module("manual_picking.application.drain_repository")

    class WorkLineLockedSession:
        async def execute(self, statement):
            sql = str(statement.compile(dialect=dialect()))
            assert "FOR UPDATE" not in sql
            assert "SKIP LOCKED" not in sql
            return SimpleNamespace(all=list, one_or_none=lambda: None)

    assert await module.DrainRepository().current(WorkLineLockedSession(), 7) is None


@pytest.mark.asyncio
async def test_current_consumes_only_immutable_typed_history():
    from unittest.mock import AsyncMock

    from src.app.wms_integration.return_buffer_drain import ReturnBufferDrainRecord

    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    try:
        reader = AsyncMock()
        typed = sdk.wms_operations.workline_return_buffer_drain_rack_decide(
            operation_id=op(1),
            workline_code="LINE-1",
            plugin_key="manual-picking",
            drain_reason="PICKING_TASK_COMPLETED",
            return_candidates=(sdk.BinReturnCandidate(1, "B1", "OUTLET"),),
        )
        record = ReturnBufferDrainRecord(
            workline_id=7,
            status="PENDING",
            created_at=NOW,
            completed_at=None,
            intent=typed,
            result=None,
            evidence_id=None,
        )
        reader.history.return_value = (record,)
        async with sessions.begin() as db:
            assert await module.DrainRepository(reader).current(db, 7) is record
            reader.history.assert_awaited_once_with(db, workline_id=7, after_operation_id=None)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_closed_history_is_not_revalidated_and_query_count_is_constant():
    from sqlalchemy import event

    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    statements = []
    try:
        async with sessions.begin() as db:
            for n in range(1, 31):
                c, e = await add_decision(db, n)
                await add_departure(db, e, "ACCEPTED", accepted=True, identity=f"out-{n}")
                if n == 1:
                    # 已闭合旧历史不再作为每次 current() 的重新解析输入。
                    c.request_digest = "f" * 64
            await add_decision(db, 31, status="PENDING", published=False)
        event.listen(
            engine.sync_engine, "before_cursor_execute", lambda _c, _s, statement, *_a: statements.append(statement)
        )
        async with sessions.begin() as db:
            current = await module.DrainRepository().current(db, 7)
            assert current.intent.operation_id == op(31)
        assert len(statements) <= 3
        confirmation_queries = [sql for sql in statements if "wms_confirmations" in sql.lower()]
        assert len(confirmation_queries) == 1
        assert "ORDER BY wes_biz.wms_confirmations.operation_id DESC" in confirmation_queries[0]
        assert "ORDER BY wes_biz.wms_confirmations.created_at" not in confirmation_queries[0]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["fork", "second_root", "broken", "premature_successor"])
async def test_checkpoint_suffix_keeps_chain_validation(case):
    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    try:
        async with sessions.begin() as db:
            _, closed = await add_decision(db)
            await add_departure(db, closed, "ACCEPTED", accepted=True)
            prior, _ = await add_decision(db, 2, result="WAIT")
            if case == "fork":
                await add_decision(db, 3, predecessor=2, result="WAIT")
                await add_decision(db, 4, predecessor=2)
            elif case == "second_root":
                await add_decision(db, 3)
            elif case == "broken":
                await add_decision(db, 3, predecessor=99)
            else:
                prior.completed_at = NOW + timedelta(seconds=10)
                await add_decision(db, 3, predecessor=2)
            await db.flush()
            with pytest.raises(ValueError):
                await module.DrainRepository().current(db, 7)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [5, 100])
@pytest.mark.parametrize("checkpoint", [False, True])
async def test_active_wait_chain_reads_completed_records_without_n_plus_one(length, checkpoint):
    from sqlalchemy import event

    module = import_module("manual_picking.application.drain_repository")
    engine, sessions = await _new_sessions()
    statements = []
    reader = module.ReturnBufferDrainResultReader()
    from unittest.mock import Mock

    reader._request = Mock(wraps=reader._request)
    try:
        async with sessions.begin() as db:
            if checkpoint:
                _, evidence = await add_decision(db, 1)
                await add_departure(db, evidence, "ACCEPTED")
            for n in range(2, length + 2):
                await add_decision(
                    db, n, predecessor=n - 1 if n > 2 else None, result="READY" if n == length + 1 else "WAIT"
                )
        event.listen(
            engine.sync_engine, "before_cursor_execute", lambda _c, _s, statement, *_a: statements.append(statement)
        )
        async with sessions.begin() as db:
            current = await module.DrainRepository(reader=reader).current(db, 7)
            assert current.intent.operation_id == op(length + 1)
            assert reader._request.call_count == (3 if checkpoint else 2)
        assert len(statements) == 3
        confirmation_queries = [sql for sql in statements if "wms_confirmations" in sql.lower()]
        assert len(confirmation_queries) == 1
        assert "ORDER BY wes_biz.wms_confirmations.operation_id DESC" in confirmation_queries[0]
        assert "ORDER BY wes_biz.wms_confirmations.created_at" not in confirmation_queries[0]
    finally:
        await engine.dispose()
