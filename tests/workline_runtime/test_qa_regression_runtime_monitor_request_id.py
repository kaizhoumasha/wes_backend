from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.app.workline.models.runtime import RuntimeResourceEvidenceKind
from src.app.workline.services.runtime_query_service import RuntimeQueryService
from src.utils.timezone import timezone

# Regression: ISSUE-002 - SMT runtime monitor crashed when a pending reconciliation
# WorklineSession had last_request_id but no request_id attribute.
# Found by /qa on 2026-06-14.
# Report: .gstack/qa-reports/qa-report-localhost-2026-06-14.md


class _ExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


@pytest.mark.asyncio
async def test_monitor_projection_uses_last_request_id_for_pending_reconciliation() -> None:
    service = RuntimeQueryService()
    db = AsyncMock()
    db_time = timezone.now_for_db()

    workline = SimpleNamespace(
        id=2,
        is_deleted=False,
        line_code="WL-SMT-SORTING-INBOUND-TEST",
        line_name="SMT runtime monitor regression line",
        line_type="SMT",
        zone_name=None,
        plugin_key="SMT_SORTING_INBOUND",
        contract_version="1.0",
        is_active=True,
        run_mode="SIMULATION",
        runtime_status="READY",
        active_safety_incident_id=None,
        stopped_at=None,
        stopped_reason=None,
        resumed_at=None,
    )
    pending_session = SimpleNamespace(
        id=262,
        session_code="runtime-monitor-smoke:single-layer:wms-callback",
        trace_id="runtime-monitor-smoke-wms-callback",
        last_request_id="req-runtime-monitor-262",
        reconciliation_state="PENDING",
        reconciliation_reason="CALLBACK_DEADLINE_EXPIRED",
        reconciliation_source_kind="DEVICE",
        reconciliation_device_id=None,
        reconciliation_command_id=None,
        reconciliation_wait_token=None,
        reconciliation_occurred_at=db_time,
        reconciliation_deadline_at=db_time,
        reconciliation_late_evidence_received=False,
        updated_at=db_time,
    )
    db.execute = AsyncMock(
        side_effect=[
            _ExecuteResult(workline),
            _ExecuteResult(0),
            _ExecuteResult(pending_session),
        ]
    )

    with (
        patch(
            "src.app.workline.services.runtime_query_service.device_repository.get_by_work_line_id",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(service, "_load_workline_session_summary_counts", new=AsyncMock(return_value=({}, 0, 0))),
        patch.object(service, "_load_active_sessions_for_workline", new=AsyncMock(return_value=[])),
        patch.object(service, "_load_recent_failed_sessions_for_workline", new=AsyncMock(return_value=[])),
        patch.object(service, "_load_recent_completed_sessions_for_workline", new=AsyncMock(return_value=[])),
        patch.object(
            service,
            "_load_blocked_outbox_projection",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    command_codes_by_device_id={},
                    count_by_device_id={},
                    summary_by_device_id={},
                )
            ),
        ),
        patch.object(service, "_load_open_command_count_map", new=AsyncMock(return_value={})),
        patch.object(service, "_load_active_runtime_hold_ids_map", new=AsyncMock(return_value={})),
        patch.object(service, "_build_trace_list_items", new=AsyncMock(return_value=[])),
        patch.object(
            service,
            "_build_workline_runtime_boundary",
            new=AsyncMock(
                return_value={
                    "workline_readiness": "READY",
                    "station_lease": "UNKNOWN",
                    "single_layer_rack_snapshot": "UNKNOWN",
                    "rack_operation_wait": "NONE",
                    "resource_evidence_kind": RuntimeResourceEvidenceKind.UNKNOWN,
                    "resource_evidence_items": [],
                    "resource_evidence_total_count": 0,
                    "resource_evidence_truncated": False,
                }
            ),
        ),
    ):
        result = await service.get_workline_monitor_projection(db, 2)

    assert result is not None
    assert result.action_candidates.pending_reconciliation is not None
    assert result.action_candidates.pending_reconciliation.request_id == "req-runtime-monitor-262"
