from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.app.execution.models import InboundEvidence
from src.app.transport.debug_run_contracts import TransportDebugRunPhase, TransportDebugRunStatus
from src.app.transport.models import TransportCallbackReceipt, TransportDebugRun, TransportDebugRunStep
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


def _run(run_id: str, *, active_scope: str | None = "GLOBAL", status: str = "RUNNING") -> TransportDebugRun:
    now = timezone.now_for_db()
    return TransportDebugRun(
        run_id=run_id,
        status=status,
        active_scope=active_scope,
        rack_id="510056",
        configuration_json={"rack_id": "510056", "face_groups": []},
        current_phase=TransportDebugRunPhase.RACK_TO_STATION.value,
        created_by_user_id=1,
        created_at=now,
        updated_at=now,
    )


def _step(run_id: str, ordinal: int, client_request_id: str | None = None) -> TransportDebugRunStep:
    now = timezone.now_for_db()
    return TransportDebugRunStep(
        run_id=run_id,
        ordinal=ordinal,
        phase=TransportDebugRunPhase.RACK_TO_STATION.value,
        status="PENDING",
        client_request_id=client_request_id,
        created_at=now,
        updated_at=now,
    )


async def test_debug_run_model_metadata_declares_runtime_schema_and_fences() -> None:
    assert TransportDebugRun.__table__.schema == "wes_runtime"
    assert TransportDebugRunStep.__table__.schema == "wes_runtime"
    run_constraints = {constraint.name for constraint in TransportDebugRun.__table__.constraints}
    step_constraints = {constraint.name for constraint in TransportDebugRunStep.__table__.constraints}
    assert {
        "ux_transport_debug_runs_run_id",
        "ux_transport_debug_runs_active_scope",
        "ck_transport_debug_runs_transport_debug_run_status_valid",
        "ck_transport_debug_runs_transport_debug_run_status_scope_consistent",
        "ck_transport_debug_runs_transport_debug_run_claim_complete",
    } <= run_constraints
    assert {
        "ux_transport_debug_run_steps_run_ordinal",
        "ux_transport_debug_run_steps_client_request_id",
        "ck_transport_debug_run_steps_transport_debug_run_step_status_valid",
        "ck_transport_debug_run_steps_transport_debug_run_step_phase_valid",
    } <= step_constraints
    run_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in TransportDebugRun.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    assert (
        "active_scope IS NOT NULL" in run_checks["ck_transport_debug_runs_transport_debug_run_status_scope_consistent"]
    )
    assert (
        "active_scope IS NOT NULL"
        in run_checks["ck_transport_debug_runs_transport_debug_run_claim_requires_active_scope"]
    )
    recent_index = next(
        index for index in TransportDebugRun.__table__.indexes if index.name == "ix_transport_debug_runs_recent"
    )
    assert [column.name for column in recent_index.columns] == ["created_at", "id"]
    evidence_index = next(
        index for index in InboundEvidence.__table__.indexes if index.name == "ix_inbound_evidences_device_event_range"
    )
    assert [column.name for column in evidence_index.columns] == ["received_at", "id"]
    assert str(evidence_index.dialect_options["postgresql"]["where"]) == "kind = 'DEVICE_EVENT'"
    receipt_constraints = {constraint.name for constraint in TransportCallbackReceipt.__table__.constraints}
    assert "ck_transport_callback_receipts_transport_callback_receipt_conflict_complete" in receipt_constraints


async def test_debug_run_migration_declares_stable_recent_index(
    integration_db_session: AsyncSession,
) -> None:
    definition = await integration_db_session.scalar(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'wes_runtime' AND indexname = 'ix_transport_debug_runs_recent'"
        )
    )

    assert isinstance(definition, str)
    assert definition.endswith("USING btree (created_at, id)")


async def test_scan12_evidence_range_index_matches_the_periodic_query(
    integration_db_session: AsyncSession,
) -> None:
    definition = await integration_db_session.scalar(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = 'wes_biz' AND indexname = 'ix_inbound_evidences_device_event_range'"
        )
    )

    assert isinstance(definition, str)
    assert definition.endswith("USING btree (received_at, id) WHERE ((kind)::text = 'DEVICE_EVENT'::text)")


async def test_callback_receipt_conflict_marker_is_persisted_by_the_migration(
    integration_db_session: AsyncSession,
) -> None:
    rows = await integration_db_session.execute(
        text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'wes_runtime' AND table_name = 'transport_callback_receipts' "
            "AND column_name IN ('conflict_code', 'conflict_detected_at')"
        )
    )

    assert dict(rows.all()) == {"conflict_code": "YES", "conflict_detected_at": "YES"}


async def test_debug_run_operator_ids_are_postgresql_bigint(
    integration_db_session: AsyncSession,
) -> None:
    rows = await integration_db_session.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'wes_runtime' AND table_name = 'transport_debug_runs' "
            "AND column_name IN ('created_by_user_id', 'aborted_by_user_id')"
        )
    )

    assert dict(rows.all()) == {
        "aborted_by_user_id": "bigint",
        "created_by_user_id": "bigint",
    }


async def test_debug_run_schema_allows_only_one_global_active_run(
    integration_db_session: AsyncSession,
) -> None:
    suffix = uuid.uuid4().hex
    integration_db_session.add(_run(f"run-one-{suffix}"))
    await integration_db_session.flush()
    integration_db_session.add(_run(f"run-two-{suffix}"))

    with pytest.raises(IntegrityError):
        await integration_db_session.flush()
    await integration_db_session.rollback()


async def test_debug_run_schema_requires_terminal_runs_to_release_active_scope(
    integration_db_session: AsyncSession,
) -> None:
    run = _run(
        f"run-terminal-{uuid.uuid4().hex}",
        active_scope="GLOBAL",
        status=TransportDebugRunStatus.COMPLETED.value,
    )
    integration_db_session.add(run)

    with pytest.raises(IntegrityError):
        await integration_db_session.flush()
    await integration_db_session.rollback()


async def test_debug_run_schema_requires_active_runs_to_claim_global_scope(
    integration_db_session: AsyncSession,
) -> None:
    integration_db_session.add(_run(f"run-active-null-{uuid.uuid4().hex}", active_scope=None))

    with pytest.raises(IntegrityError):
        await integration_db_session.flush()
    await integration_db_session.rollback()


async def test_debug_run_step_schema_fences_ordinal_and_client_request_identity(
    integration_db_session: AsyncSession,
) -> None:
    run_id = f"run-step-{uuid.uuid4().hex}"
    request_id = new_uuid7()
    integration_db_session.add(_run(run_id))
    await integration_db_session.flush()
    integration_db_session.add(_step(run_id, 0, request_id))
    await integration_db_session.flush()
    integration_db_session.add(_step(run_id, 0, new_uuid7()))

    with pytest.raises(IntegrityError):
        await integration_db_session.flush()
    await integration_db_session.rollback()

    other_run_id = f"run-request-{uuid.uuid4().hex}"
    integration_db_session.add(_run(other_run_id))
    await integration_db_session.flush()
    integration_db_session.add(_step(other_run_id, 0, request_id))
    await integration_db_session.flush()
    another_run_id = f"run-request-other-{uuid.uuid4().hex}"
    integration_db_session.add(_run(another_run_id, active_scope=None, status="COMPLETED"))
    await integration_db_session.flush()
    integration_db_session.add(_step(another_run_id, 0, request_id))

    with pytest.raises(IntegrityError):
        await integration_db_session.flush()
    await integration_db_session.rollback()
