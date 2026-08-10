"""T8d EFFECT reducer 与 ReconciliationCase 的真实 PostgreSQL 验证。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select, text

from src.app.runtime.orchestration.effect_bridges import (
    EffectCallbackBridge,
    EffectCallbackOutcome,
    EffectReconciliationBridge,
)
from src.app.runtime.orchestration.execution_correlation import ExecutionCorrelation
from src.app.runtime.orchestration.execution_session import ExecutionSession
from src.app.runtime.orchestration.reconciliation_case import ReconciliationCase, ReconciliationCaseStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.workline.models.workline import LineType, WorkLine
from tests.support.runtime_inbox_processing_postgresql import with_temporary_runtime_database


def _intent(*, execution_session_id: int, correlation_id: str) -> RuntimeIntentLog:
    return RuntimeIntentLog(
        execution_session_id=execution_session_id,
        correlation_id=correlation_id,
        provider_code="WMS",
        operation_kind="system_capability_effect",
        target_domain="wms_integration",
        target_action="confirm_effect",
        idempotency_key="effect-pg-1",
        request_hash="a" * 64,
        dispatch_key="effect-pg-dispatch-1",
    )


def test_effect_reducer_and_reconciliation_constraints_on_postgresql() -> None:
    async def scenario(session_factory, _queue_gateway) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as db:
            constraint_names = set(
                (
                    await db.execute(
                        text(
                            "SELECT c.conname "
                            "FROM pg_constraint c "
                            "JOIN pg_class t ON t.oid = c.conrelid "
                            "JOIN pg_namespace n ON n.oid = t.relnamespace "
                            "WHERE n.nspname = 'wes_runtime' AND t.relname = 'reconciliation_cases'"
                        )
                    )
                ).scalars()
            )
            assert constraint_names == {
                "ck_reconciliation_cases_reconciliation_case_status",
                "ck_reconciliation_cases_resolution_state",
                "fk_reconciliation_case_intent",
                "pk_reconciliation_cases",
            }

            workline = WorkLine(
                line_code="EFFECT-REDUCER-POSTGRESQL",
                line_name="Effect reducer PostgreSQL",
                line_type=LineType.AUTO,
                is_active=True,
            )
            db.add(workline)
            await db.flush()
            execution_session = ExecutionSession(
                workline_id=workline.id,
                state="RUNNING",
            )
            db.add(execution_session)
            await db.flush()
            assert execution_session.id is not None
            correlation = ExecutionCorrelation(
                correlation_id="effect-pg-corr-1",
                execution_session_id=execution_session.id,
                trace_id="effect-pg-trace-1",
            )
            db.add(correlation)
            await db.flush()
            intent = _intent(
                execution_session_id=execution_session.id,
                correlation_id=correlation.correlation_id,
            )
            db.add(intent)
            await db.commit()

            callback = EffectCallbackBridge()
            reconciliation = EffectReconciliationBridge()
            await callback.record(
                db,
                dispatch_key=intent.dispatch_key,
                outcome=EffectCallbackOutcome.COMPLETED,
                occurred_at_ms=1000,
                source_event_id="callback-completed-1",
                evidence_json={"remote_status": "DONE"},
            )
            await callback.record(
                db,
                dispatch_key=intent.dispatch_key,
                outcome=EffectCallbackOutcome.REJECTED,
                occurred_at_ms=1100,
                source_event_id="callback-rejected-1",
                evidence_json={"remote_status": "REJECTED"},
            )
            await callback.record(
                db,
                dispatch_key=intent.dispatch_key,
                outcome=EffectCallbackOutcome.ACCEPTED,
                occurred_at_ms=1200,
                source_event_id="callback-accepted-late",
                evidence_json={"remote_status": "ACCEPTED"},
            )
            await db.commit()

            persisted_intent = await db.scalar(
                select(RuntimeIntentLog).where(RuntimeIntentLog.dispatch_key == intent.dispatch_key)
            )
            open_case = await db.scalar(
                select(ReconciliationCase).where(ReconciliationCase.dispatch_key == intent.dispatch_key)
            )
            assert persisted_intent is not None
            assert persisted_intent.effect_status is RuntimeIntentStatus.COMPLETED
            assert open_case is not None and open_case.status is ReconciliationCaseStatus.OPEN
            assert len(open_case.evidence_history_json) == 2

            await reconciliation.resolve(
                db,
                dispatch_key=intent.dispatch_key,
                occurred_at_ms=1300,
                resolution=RuntimeIntentStatus.REJECTED,
                reason_code="REMOTE_REJECTED",
                evidence_json={"ticket": "PG-CASE-1"},
                source_event_id="resolution-pg-case-1",
            )
            await db.commit()
            await db.refresh(persisted_intent)
            await db.refresh(open_case)
            assert persisted_intent.effect_status is RuntimeIntentStatus.COMPLETED
            assert open_case.status is ReconciliationCaseStatus.RESOLVED
            assert open_case.resolved_at_ms == 1300

            await callback.record(
                db,
                dispatch_key=intent.dispatch_key,
                outcome=EffectCallbackOutcome.REJECTED,
                occurred_at_ms=1400,
                source_event_id="callback-rejected-1",
                evidence_json={"remote_status": "REJECTED"},
            )
            await db.commit()
            persisted_cases = list(
                (
                    await db.execute(
                        select(ReconciliationCase).where(ReconciliationCase.dispatch_key == intent.dispatch_key)
                    )
                )
                .scalars()
                .all()
            )
            assert len(persisted_cases) == 1
            assert persisted_cases[0].status is ReconciliationCaseStatus.RESOLVED

            duplicate_open = ReconciliationCase(
                runtime_intent_log_id=intent.id,
                dispatch_key=intent.dispatch_key,
                status=ReconciliationCaseStatus.OPEN,
                reason_code="FIRST_OPEN",
                opened_at_ms=1500,
            )
            db.add(duplicate_open)
            await db.commit()
            db.add(
                ReconciliationCase(
                    runtime_intent_log_id=intent.id,
                    dispatch_key=intent.dispatch_key,
                    status=ReconciliationCaseStatus.OPEN,
                    reason_code="SECOND_OPEN",
                    opened_at_ms=1600,
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()

    asyncio.run(with_temporary_runtime_database(scenario))
