"""Seed non-prod runtime monitor smoke scenarios."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.data.sync_test_workline_devices import (
    TEST_SMT_SORTING_INBOUND_LINE_CODE,
    sync_test_workline_devices,
)
from src.app.runtime.orchestration.models.session import RunMode, SessionStatus, WorklineSession
from src.app.runtime.orchestration.repositories.runtime_hold_repository import runtime_hold_repository
from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    workline_runtime_status_projection_service,
)
from src.app.workline.models import LineType, WorkLine, WorkLineRunMode
from src.core.conf import settings
from src.utils.timezone import timezone

FALLBACK_LINE_CODE = "WL-RUNTIME-MONITOR-FALLBACK-SMOKE"
FALLBACK_PLUGIN_KEY = "runtime_monitor_smoke_missing_manifest"
SMOKE_CONTRACT_VERSION = "runtime-monitor-smoke-v1"
SINGLE_LAYER_SMOKE_POSITION_CODE = "SOURCE_STATION_A"


async def seed_runtime_monitor_smoke(db: AsyncSession, *, commit: bool = True) -> dict[str, Any]:
    """Create deterministic runtime monitor scenarios for browser smoke tests."""

    await sync_test_workline_devices(db, commit=False)

    single_layer_workline = await _require_workline(db, TEST_SMT_SORTING_INBOUND_LINE_CODE)
    fallback_workline = await _upsert_fallback_workline(db)
    await _assert_seed_workline_safe(db, single_layer_workline)
    await _assert_seed_workline_safe(db, fallback_workline)
    await _mark_ready(db, single_layer_workline)
    await _mark_ready(db, fallback_workline)

    single_layer_sessions = await _seed_single_layer_sessions(db, single_layer_workline)
    fallback_session = await _upsert_session(
        db,
        fallback_workline,
        session_code="runtime-monitor-smoke:fallback:generic",
        business_key="runtime-monitor-smoke:fallback:generic",
        trace_id="runtime-monitor-smoke-fallback-generic",
        context_json={
            "resource_evidence": {
                "resource_kind": "RACK",
                "resource_code": "GENERIC-FALLBACK-001",
                "display_label": "Generic evidence GENERIC-FALLBACK-001",
                "evidence_kind": "GENERIC_EVIDENCE",
                "position_code": "FALLBACK_POSITION",
                "rack_code": "GENERIC-FALLBACK-001",
                "source_system": "LOCAL_DEBUG",
                "trace_id": "runtime-monitor-smoke-fallback-generic",
                "occurred_at": timezone.now_for_db().isoformat(),
            }
        },
    )

    if commit:
        await db.commit()
    else:
        await db.flush()

    return {
        "single_layer_workline": _workline_result(single_layer_workline),
        "fallback_workline": _workline_result(fallback_workline),
        "sessions": {
            "single_layer": [session.session_code for session in single_layer_sessions],
            "fallback": [fallback_session.session_code],
        },
    }


async def _seed_single_layer_sessions(db: AsyncSession, workline: WorkLine) -> list[WorklineSession]:
    now = timezone.now_for_db()
    waiting_session = await _upsert_session(
        db,
        workline,
        session_code="runtime-monitor-smoke:single-layer:waiting-wms",
        business_key="runtime-monitor-smoke:single-layer:waiting-wms",
        trace_id="runtime-monitor-smoke-waiting-wms",
        context_json={
            "waiting_rack_operation_key": "runtime-monitor-smoke:rack-op-waiting",
            "rack_operation": {
                "operation_key": "runtime-monitor-smoke:rack-op-waiting",
                "status": "PENDING",
                "rack_kind": "SINGLE_LAYER",
                "rack_code": "RACK-SMOKE-WAITING",
                "target_position_code": SINGLE_LAYER_SMOKE_POSITION_CODE,
                "work_position_code": SINGLE_LAYER_SMOKE_POSITION_CODE,
                "source_system": "WMS",
                "occurred_at": now.isoformat(),
            },
        },
    )
    callback_session = await _upsert_session(
        db,
        workline,
        session_code="runtime-monitor-smoke:single-layer:wms-callback",
        business_key="runtime-monitor-smoke:single-layer:wms-callback",
        trace_id="runtime-monitor-smoke-wms-callback",
        context_json={
            "rack_operation": {
                "operation_key": "runtime-monitor-smoke:rack-op-callback",
                "status": "ARRIVED",
                "source_system": "WMS",
                "callback_type": "WMS_RACK_ARRIVED",
                "rack_kind": "SINGLE_LAYER",
                "rack_code": "RACK-SMOKE-CALLBACK",
                "bin_code": "BIN-SMOKE-CALLBACK",
                "target_position_code": SINGLE_LAYER_SMOKE_POSITION_CODE,
                "work_position_code": SINGLE_LAYER_SMOKE_POSITION_CODE,
                "occurred_at": now.isoformat(),
            },
            "resource_state_events": _trace_resource_events(now),
        },
    )
    return [waiting_session, callback_session]


def _trace_resource_events(now: Any, *, count: int = 55) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        cell_index = ((index - 1) % 4) + 1
        events.append(
            {
                "resource_kind": "PKG",
                "resource_code": f"PKG-SMOKE-{index:03d}",
                "display_label": f"PKG PKG-SMOKE-{index:03d}",
                "pkg_code": f"PKG-SMOKE-{index:03d}",
                "rack_code": "RACK-SMOKE-CALLBACK",
                "bin_code": "BIN-SMOKE-CALLBACK",
                "rack_slot_code": "A",
                "bin_cell_code": f"CELL-SMOKE-{cell_index}",
                "material_code": "620100L00-011-G",
                "date_code": "2401",
                "lot_code": "LOT-A",
                "reel_count": 1,
                "reel_code": f"REEL-SMOKE-{index:03d}",
                "position_index": index,
                "evidence_kind": "WMS_CALLBACK_EVIDENCE",
                "station_code": SINGLE_LAYER_SMOKE_POSITION_CODE,
                "position_code": SINGLE_LAYER_SMOKE_POSITION_CODE,
                "source_system": "WMS",
                "callback_type": "WMS_RACK_ARRIVED",
                "trace_id": "runtime-monitor-smoke-wms-callback",
                "occurred_at": now.isoformat(),
            }
        )
    return events


async def _upsert_fallback_workline(db: AsyncSession) -> WorkLine:
    result = await db.execute(
        select(WorkLine).where(
            WorkLine.line_code == FALLBACK_LINE_CODE,  # type: ignore[arg-type]
            WorkLine.is_deleted.is_(False),  # type: ignore[arg-type]
        )
    )
    workline = result.scalar_one_or_none()
    values: dict[str, Any] = {
        "line_code": FALLBACK_LINE_CODE,
        "line_name": "Runtime monitor fallback smoke line",
        "line_type": LineType.AUTO,
        "zone_name": "开发库",
        "plugin_key": FALLBACK_PLUGIN_KEY,
        "contract_version": SMOKE_CONTRACT_VERSION,
        "config": {"seed_source": "runtime-monitor-smoke"},
        "runtime_config_json": {
            "run_mode": WorkLineRunMode.SIMULATION.value,
            "sandbox_enabled": True,
        },
        "run_mode": WorkLineRunMode.SIMULATION,
        "diagnostic_profile": {"seed_source": "runtime-monitor-smoke"},
        "description": "Runtime monitor smoke fallback line without plugin manifest.",
        "is_active": True,
    }
    if workline is None:
        workline = WorkLine(**values)
        db.add(workline)
    else:
        for key, value in values.items():
            setattr(workline, key, value)
    await db.flush()
    return workline


async def _upsert_session(
    db: AsyncSession,
    workline: WorkLine,
    *,
    session_code: str,
    business_key: str,
    trace_id: str,
    context_json: dict[str, Any],
) -> WorklineSession:
    result = await db.execute(select(WorklineSession).where(WorklineSession.session_code == session_code))
    session = result.scalar_one_or_none()
    now = timezone.now_for_db()
    values: dict[str, Any] = {
        "session_code": session_code,
        "workline_id": workline.id,
        "plugin_key": workline.plugin_key,
        "run_mode": RunMode.SIMULATION,
        "business_key": business_key,
        "barcode": None,
        "status": SessionStatus.WAITING_EXTERNAL,
        "context_json": context_json,
        "context_schema_version": SMOKE_CONTRACT_VERSION,
        "contract_version": workline.contract_version,
        "started_at": now,
        "ended_at": None,
        "trace_id": trace_id,
        "current_wait_type": "EXTERNAL_API",
        "waiting_since": now,
        "deadline_at": now + timedelta(hours=1),
        "current_wait_timeout_seconds": 3600,
        "awaiting_device_command_code": None,
        "failure_domain": None,
        "failure_code": None,
        "failure_message": None,
        "ingress_count": 1,
        "last_request_id": trace_id,
        "last_ingress_at": now,
        "last_inbox_id": None,
        "reconciliation_state": None,
        "reconciliation_reason": None,
        "reconciliation_source_kind": None,
        "reconciliation_source_inbox_id": None,
        "reconciliation_source_outbox_id": None,
        "reconciliation_command_id": None,
        "reconciliation_device_id": None,
        "reconciliation_wait_token": None,
        "reconciliation_ack_received_at": None,
        "reconciliation_deadline_at": None,
        "reconciliation_occurred_at": None,
        "reconciliation_late_evidence_received": False,
        "reconciliation_resolution": None,
        "reconciliation_resolved_at": None,
    }
    if session is None:
        session = WorklineSession(**values)
        db.add(session)
    else:
        for key, value in values.items():
            setattr(session, key, value)
    await db.flush()
    return session


async def _require_workline(db: AsyncSession, line_code: str) -> WorkLine:
    result = await db.execute(
        select(WorkLine).where(
            WorkLine.line_code == line_code,  # type: ignore[arg-type]
            WorkLine.is_deleted.is_(False),  # type: ignore[arg-type]
        )
    )
    workline = result.scalar_one_or_none()
    if workline is None:
        raise RuntimeError(f"workline not found after base seed: {line_code}")
    return workline


async def _assert_seed_workline_safe(db: AsyncSession, workline: WorkLine) -> None:
    workline_id = workline.id
    if workline_id is None:
        raise RuntimeError(f"workline id missing for runtime monitor smoke seed: {workline.line_code}")
    runtime_snapshot = await workline_runtime_status_projection_service.runtime_status_snapshot(
        db,
        workline_id=workline_id,
    )
    if runtime_snapshot.active_safety_incident_id is not None:
        raise RuntimeError(
            f"workline has active safety incident; resolve it before runtime monitor smoke seed: {workline.line_code}"
        )
    if await runtime_hold_repository.count_active_by_workline(db, workline_id):
        raise RuntimeError(
            f"workline has active runtime hold; resolve it before runtime monitor smoke seed: {workline.line_code}"
        )


async def _mark_ready(db: AsyncSession, workline: WorkLine) -> None:
    workline_id = workline.id
    if workline_id is None:
        raise RuntimeError(f"workline id missing for runtime monitor smoke seed: {workline.line_code}")
    await workline_runtime_status_projection_service.project_ready_after_start(
        db,
        workline_id=workline_id,
        occurred_at=timezone.now_for_db(),
        evidence_json={"source": "scripts/data/seed_runtime_monitor_smoke"},
    )
    workline.is_active = True


def _workline_result(workline: WorkLine) -> dict[str, Any]:
    return {
        "id": workline.id,
        "line_code": workline.line_code,
        "plugin_key": workline.plugin_key,
        "contract_version": workline.contract_version,
    }


async def _run(*, dry_run: bool = False) -> dict[str, Any]:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session_maker = async_sessionmaker(engine, expire_on_commit=False, autocommit=False, autoflush=False)
    try:
        async with async_session_maker() as db:
            result = await seed_runtime_monitor_smoke(db, commit=not dry_run)
            if dry_run:
                await db.rollback()
            return result
    finally:
        await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed non-prod runtime monitor browser smoke scenarios")
    parser.add_argument("--dry-run", action="store_true", help="Run seed logic but roll back the transaction")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = asyncio.run(_run(dry_run=args.dry_run))
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return
    dry_run_label = " (dry-run)" if args.dry_run else ""
    print(f"runtime monitor smoke scenarios seeded{dry_run_label}: {json.dumps(result, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
