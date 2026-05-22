import json
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.responses import JSONResponse

from src.app.workline.models import LineType, WorkLine
from src.app.workline.models.runtime_hold import (
    MaterialDisposition,
    NgReasonSource,
    NgReturnItem,
    NgReturnItemStatus,
    RuntimeHold,
    RuntimeHoldStatus,
    RuntimeHoldType,
)
from src.app.workline.models.runtime_hold_api import ResolveRuntimeHoldRequest
from src.app.workline.models.safety import WorkLineRuntimeStatus
from src.app.workline.models.session import SessionStatus, WorklineSession
from src.app.workline.services.runtime_hold_query_service import runtime_hold_query_service
from src.app.workline.v1 import runtime_hold as runtime_hold_api

pytestmark = pytest.mark.asyncio


def _get_route(path: str, method: str):
    for route in runtime_hold_api.router.routes:
        if method in route.methods and route.path == path:
            return route
    raise AssertionError(f"{method} {path} route not found")


def _permission_names(path: str, method: str) -> list[str]:
    route = _get_route(path, method)
    return [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies]


def _json_response_body(response: JSONResponse) -> dict:
    return cast("dict", json.loads(response.body.decode("utf-8")))


async def _create_workline(db_session, *, code: str) -> WorkLine:
    workline = WorkLine(
        line_code=code,
        line_name=code,
        line_type=LineType.AUTO,
        plugin_key="smt_classifier",
        contract_version="1.0",
        runtime_config_json={"runtime_hold": {"ng_locations": [{"code": "NG-01", "label": "NG 暂存位 01"}]}},
        runtime_status=WorkLineRuntimeStatus.RECONCILING,
        stopped_reason="RUNTIME_HOLD",
    )
    db_session.add(workline)
    await db_session.flush()
    return workline


async def _create_session(db_session, workline: WorkLine, *, code: str) -> WorklineSession:
    session = WorklineSession(
        session_code=code,
        workline_id=cast("int", workline.id),
        plugin_key="smt_classifier",
        contract_version="1.0",
        status=SessionStatus.MANUAL_HOLD,
        context_json={},
    )
    db_session.add(session)
    await db_session.flush()
    return session


async def _create_hold(
    db_session,
    workline: WorkLine,
    session: WorklineSession,
    *,
    key: str,
    evidence: dict | None = None,
) -> RuntimeHold:
    hold = RuntimeHold(
        hold_type=RuntimeHoldType.RUNTIME_RECONCILIATION,
        workline_id=cast("int", workline.id),
        session_id=cast("int", session.id),
        plugin_key="smt_classifier",
        contract_version="1.0",
        source_kind="TIMER_TIMEOUT",
        source_reason="CALLBACK_DEADLINE_EXPIRED",
        source_idempotency_key=key,
        evidence_snapshot_json=evidence or {"inbox_payload": {"data": {"PkgID": "PKG-API-001"}}},
    )
    db_session.add(hold)
    await db_session.flush()
    return hold


async def _continue_request(db_session, hold: RuntimeHold) -> ResolveRuntimeHoldRequest:
    detail = await runtime_hold_query_service.get_detail(db_session, cast("int", hold.id))
    assert detail is not None
    return ResolveRuntimeHoldRequest(
        resolution="COMPLETED",
        checks=dict.fromkeys(detail.release_eligibility.required_checks, True),
        operator_note="现场确认继续",
        material_disposition="CONTINUE",
        hold_version=detail.summary.version,
        latest_evidence_hash=detail.release_eligibility.latest_evidence_hash,
    )


async def _return_to_ng_request(db_session, hold: RuntimeHold) -> ResolveRuntimeHoldRequest:
    detail = await runtime_hold_query_service.get_detail(db_session, cast("int", hold.id))
    assert detail is not None
    return ResolveRuntimeHoldRequest(
        resolution="FAILED",
        checks=dict.fromkeys(detail.release_eligibility.required_checks, True),
        operator_note="物料放入 NG 暂存区",
        material_disposition="RETURN_TO_NG",
        ng_reason={"source": "PLUGIN", "code": "SCAN_NG", "label": "扫码异常"},
        physical_handoff_evidence={
            "ng_location_code": "NG-01",
            "ng_location_scan": "NG-01",
            "material_scan_payload": {"PkgID": "PKG-API-001"},
            "line_clear_checked": True,
            "late_callback_reviewed": True,
        },
        hold_version=detail.summary.version,
        latest_evidence_hash=detail.release_eligibility.latest_evidence_hash,
    )


class TestRuntimeHoldRoutePermissions:
    async def test_runtime_hold_routes_require_expected_permissions(self) -> None:
        assert _permission_names("/runtime-holds", "GET") == ["biz:workline:view-runtime-hold"]
        assert _permission_names("/runtime-holds/{hold_id}", "GET") == ["biz:workline:view-runtime-hold"]
        assert _permission_names("/runtime-holds/{hold_id}/resolve", "POST") == ["biz:workline:resolve-runtime-hold"]
        assert _permission_names("/runtime-holds/ng-reasons", "GET") == ["biz:workline:view-runtime-hold"]
        assert _permission_names("/ng-return-items", "GET") == ["biz:workline:list-ng-return-item"]


async def test_list_runtime_holds_includes_session_level_timeout_hold(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-HOLD-LIST")
    session = await _create_session(db_session, workline, code="S-API-HOLD-LIST")
    hold = await _create_hold(db_session, workline, session, key="api:hold-list")

    response = await runtime_hold_api.list_runtime_holds(
        db_session,
        workline_id=cast("int", workline.id),
        session_id=cast("int", session.id),
        status="OPEN",
    )

    items = response["data"]
    assert [item.id for item in items] == [hold.id]
    assert items[0].source_reason == "CALLBACK_DEADLINE_EXPIRED"


async def test_get_runtime_hold_detail_is_read_only(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-DETAIL")
    session = await _create_session(db_session, workline, code="S-API-DETAIL")
    hold = await _create_hold(db_session, workline, session, key="api:detail")

    response = await runtime_hold_api.get_runtime_hold_detail(cast("int", hold.id), db_session)

    await db_session.refresh(hold)
    assert hold.status == RuntimeHoldStatus.OPEN
    assert hold.version == 0
    assert response["data"].summary.id == hold.id
    assert response["data"].release_eligibility.can_resolve is True


async def test_resolve_runtime_hold_continue(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-CONTINUE")
    session = await _create_session(db_session, workline, code="S-API-CONTINUE")
    hold = await _create_hold(db_session, workline, session, key="api:continue")
    request = await _continue_request(db_session, hold)

    with patch(
        "src.app.workline.v1.runtime_hold.publish_deferred_sse_events",
        new=AsyncMock(),
    ):
        response = await runtime_hold_api.resolve_runtime_hold(
            cast("int", hold.id),
            request,
            db_session,
            current_user_id=42,
        )

    await db_session.refresh(hold)
    assert response["data"]["status"] == RuntimeHoldStatus.RESOLVED.value
    assert hold.status == RuntimeHoldStatus.RESOLVED


async def test_resolve_runtime_hold_rejects_safety_estop_hold(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-SAFETY")
    workline.runtime_status = WorkLineRuntimeStatus.ESTOPPED
    hold = RuntimeHold(
        hold_type=RuntimeHoldType.SAFETY_ESTOP,
        workline_id=cast("int", workline.id),
        plugin_key="smt_classifier",
        contract_version="1.0",
        source_kind="SAFETY_ESTOP",
        source_reason="ESTOP_PRESSED",
        source_idempotency_key="api:safety-estop",
        evidence_snapshot_json={"event_type": "ESTOP_PRESSED"},
    )
    db_session.add(hold)
    await db_session.flush()
    request = await _continue_request(db_session, hold)

    response = await runtime_hold_api.resolve_runtime_hold(
        cast("int", hold.id),
        request,
        db_session,
        current_user_id=42,
    )

    await db_session.refresh(hold)
    assert isinstance(response, JSONResponse)
    assert response.status_code == 400
    body = _json_response_body(response)
    assert body["code"] == "RUNTIME_HOLD_SAFETY_ESTOP_REQUIRES_CLEAR_ESTOP"
    assert "clear-estop" in body["message"]
    assert hold.status == RuntimeHoldStatus.OPEN


async def test_resolve_runtime_hold_return_to_ng(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-NG")
    session = await _create_session(db_session, workline, code="S-API-NG")
    hold = await _create_hold(db_session, workline, session, key="api:ng")
    request = await _return_to_ng_request(db_session, hold)

    with patch(
        "src.app.workline.v1.runtime_hold.publish_deferred_sse_events",
        new=AsyncMock(),
    ):
        response = await runtime_hold_api.resolve_runtime_hold(
            cast("int", hold.id),
            request,
            db_session,
            current_user_id=42,
        )

    assert response["data"]["ng_return_item_id"] is not None


async def test_resolve_runtime_hold_missing_evidence_returns_422(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-MISSING")
    session = await _create_session(db_session, workline, code="S-API-MISSING")
    hold = await _create_hold(db_session, workline, session, key="api:missing")
    request = (await _return_to_ng_request(db_session, hold)).model_copy(update={"physical_handoff_evidence": None})

    response = await runtime_hold_api.resolve_runtime_hold(
        cast("int", hold.id),
        request,
        db_session,
        current_user_id=42,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 422
    assert _json_response_body(response)["code"] == "RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE"


async def test_resolve_runtime_hold_unmapped_ng_reason_returns_422(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-BAD-REASON")
    session = await _create_session(db_session, workline, code="S-API-BAD-REASON")
    hold = await _create_hold(db_session, workline, session, key="api:bad-reason")
    request_payload = (await _return_to_ng_request(db_session, hold)).model_dump(mode="json")
    request_payload["ng_reason"] = {
        "source": "PLUGIN",
        "code": "FREE_TEXT_REASON",
        "label": "随便填的原因",
    }
    request = ResolveRuntimeHoldRequest.model_validate(request_payload)

    response = await runtime_hold_api.resolve_runtime_hold(
        cast("int", hold.id),
        request,
        db_session,
        current_user_id=42,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 422
    assert _json_response_body(response)["code"] == "RUNTIME_HOLD_REASON_UNMAPPED"


async def test_resolve_runtime_hold_version_conflict_returns_decision_model(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-CONFLICT")
    session = await _create_session(db_session, workline, code="S-API-CONFLICT")
    hold = await _create_hold(db_session, workline, session, key="api:conflict")
    request = (await _continue_request(db_session, hold)).model_copy(update={"hold_version": hold.version + 1})

    response = await runtime_hold_api.resolve_runtime_hold(
        cast("int", hold.id),
        request,
        db_session,
        current_user_id=42,
    )

    assert isinstance(response, JSONResponse)
    body = _json_response_body(response)
    assert response.status_code == 409
    assert body["code"] == "RUNTIME_HOLD_VERSION_CONFLICT"
    assert body["data"]["current_hold_version"] == hold.version
    assert body["data"]["release_eligibility"]["can_resolve"] is True


async def test_resolve_runtime_hold_evidence_changed_returns_409(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-EVIDENCE")
    session = await _create_session(db_session, workline, code="S-API-EVIDENCE")
    hold = await _create_hold(db_session, workline, session, key="api:evidence")
    request = await _continue_request(db_session, hold)
    session.context_json = {"runtime_reconciliation_late_callback_evidence": [{"evidence_key": "late"}]}
    await db_session.flush()

    response = await runtime_hold_api.resolve_runtime_hold(
        cast("int", hold.id),
        request,
        db_session,
        current_user_id=42,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    assert _json_response_body(response)["code"] == "RUNTIME_HOLD_EVIDENCE_CHANGED"


async def test_resolve_runtime_hold_material_conflict_returns_decision_model(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-MATERIAL-CONFLICT")
    session = await _create_session(db_session, workline, code="S-API-MATERIAL-CONFLICT")
    hold = await _create_hold(db_session, workline, session, key="api:material-conflict")
    other_hold = await _create_hold(db_session, workline, session, key="api:material-conflict:other")
    existing_item = NgReturnItem(
        source_workline_id=cast("int", workline.id),
        source_session_id=cast("int", session.id),
        material_identity_key="smt:PKG-API-001",
        material_identity_json={"idempotency_key": "smt:PKG-API-001"},
        physical_handoff_evidence_json={"ng_location_code": "NG-01"},
        disposition=MaterialDisposition.RETURN_TO_NG,
        ng_reason_source=NgReasonSource.PLUGIN,
        ng_reason_code="SCAN_NG",
        ng_reason_label="扫码异常",
        created_from_runtime_hold_id=cast("int", other_hold.id),
        status=NgReturnItemStatus.WAITING_REWORK,
    )
    db_session.add(existing_item)
    await db_session.flush()
    request = await _return_to_ng_request(db_session, hold)

    response = await runtime_hold_api.resolve_runtime_hold(
        cast("int", hold.id),
        request,
        db_session,
        current_user_id=42,
    )

    assert isinstance(response, JSONResponse)
    body = _json_response_body(response)
    assert response.status_code == 409
    assert body["code"] == "RUNTIME_HOLD_MATERIAL_CONFLICT"
    assert body["data"]["material_identity_key"] == "smt:PKG-API-001"
    assert body["data"]["existing_ng_return_item_id"] == existing_item.id
    assert body["data"]["existing_runtime_hold_id"] == other_hold.id
    assert body["data"]["current_hold_version"] == hold.version
    assert body["data"]["current_status"] == RuntimeHoldStatus.OPEN.value
    assert body["data"]["release_eligibility"]["can_resolve"] is True
    assert body["data"]["refresh_url"] == f"/api/v1/workline/runtime-holds/{hold.id}"


async def test_resolve_runtime_hold_already_resolved_returns_409(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-RESOLVED")
    session = await _create_session(db_session, workline, code="S-API-RESOLVED")
    hold = await _create_hold(db_session, workline, session, key="api:resolved")
    request = await _continue_request(db_session, hold)

    with patch(
        "src.app.workline.v1.runtime_hold.publish_deferred_sse_events",
        new=AsyncMock(),
    ):
        await runtime_hold_api.resolve_runtime_hold(cast("int", hold.id), request, db_session, current_user_id=42)
    response = await runtime_hold_api.resolve_runtime_hold(
        cast("int", hold.id),
        request,
        db_session,
        current_user_id=42,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    assert _json_response_body(response)["code"] == "RUNTIME_HOLD_ALREADY_RESOLVED"


async def test_get_runtime_hold_ng_reasons_returns_plugin_and_fallback(db_session) -> None:
    response = await runtime_hold_api.get_runtime_hold_ng_reasons(db_session, plugin_key="smt_classifier")

    codes = {item.code for item in response["data"]}
    assert "SCAN_NG" in codes
    assert "UNKNOWN_PHYSICAL_STATE" in codes


async def test_list_ng_return_items_filters_by_hold(db_session) -> None:
    workline = await _create_workline(db_session, code="WL-API-NG-LIST")
    session = await _create_session(db_session, workline, code="S-API-NG-LIST")
    hold = await _create_hold(db_session, workline, session, key="api:ng-list")
    request = await _return_to_ng_request(db_session, hold)
    with patch(
        "src.app.workline.v1.runtime_hold.publish_deferred_sse_events",
        new=AsyncMock(),
    ):
        await runtime_hold_api.resolve_runtime_hold(cast("int", hold.id), request, db_session, current_user_id=42)

    response = await runtime_hold_api.list_ng_return_items(
        db_session,
        runtime_hold_id=cast("int", hold.id),
        status="WAITING_REWORK",
    )

    assert len(response["data"]) == 1
    assert response["data"][0].created_from_runtime_hold_id == hold.id
