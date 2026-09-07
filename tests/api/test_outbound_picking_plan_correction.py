"""计划阻塞对账的授权和严格管理 HTTP 合同。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from src.app.wms_integration.outbound_picking.v1.plan_correction import router
from src.core.rbac import require_auth, require_superuser
from src.register import register_exception


async def allow_admin(request: Request):
    request.state.user_id = 42


async def non_admin(request: Request):
    request.state.user_id = 42
    request.state.is_superuser = False
    return 42


def app_for(service, *, admin=True):
    app = FastAPI()
    register_exception(app)
    app.include_router(router, prefix="/api")
    app.state.outbound_picking_runtime = SimpleNamespace(plan_delta_service=service)
    app.dependency_overrides[require_superuser if admin else require_auth] = allow_admin if admin else non_admin
    return app


URL = "/api/v1/outbound-picking/tasks/PICK-1/plan-blockers/51/apply-correction"
PAYLOAD = {"correction_evidence_id": 52, "expected_version": 3, "reason": "核验 WMS 修正计划"}


def test_correction_requires_superuser():
    route = next(r for r in router.routes if isinstance(r, APIRoute))
    assert route.dependencies[0].dependency is require_superuser


async def test_non_admin_cannot_call_service():
    service = SimpleNamespace(apply_correction=AsyncMock())
    async with AsyncClient(
        transport=ASGITransport(app=app_for(service, admin=False)), base_url="http://test"
    ) as client:
        response = await client.post(URL, json=PAYLOAD)
    assert response.status_code == 403
    service.apply_correction.assert_not_awaited()


@pytest.mark.parametrize(
    "patch",
    [
        {"correction_evidence_id": True},
        {"correction_evidence_id": "52"},
        {"correction_evidence_id": 0},
        {"expected_version": True},
        {"expected_version": -1},
        {"expected_version": "3"},
        {"reason": " "},
        {"reason": "x" * 501},
        {"unapproved": True},
    ],
)
async def test_strict_payload_rejects_before_service(patch):
    service = SimpleNamespace(apply_correction=AsyncMock())
    async with AsyncClient(transport=ASGITransport(app=app_for(service)), base_url="http://test") as client:
        response = await client.post(URL, json={**PAYLOAD, **patch})
    assert response.status_code == 422
    service.apply_correction.assert_not_awaited()


async def test_missing_runtime_is_unavailable():
    app = app_for(SimpleNamespace())
    del app.state.outbound_picking_runtime
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(URL, json=PAYLOAD)
    assert response.status_code == 503


@pytest.mark.parametrize("task_path", ["PICK-1", "PICK/001", "PICK%2F001"])
async def test_success_passes_frozen_references_actor_and_timestamp(task_path):
    from datetime import datetime
    from unittest.mock import ANY

    task_id = task_path.replace("%2F", "/")
    service = SimpleNamespace(
        apply_correction=AsyncMock(
            return_value=SimpleNamespace(task_id=task_id, plan_revision=2, version=4, correction_evidence_id=52)
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app_for(service)), base_url="http://test") as client:
        response = await client.post(
            URL.replace("PICK-1", task_path), json={**PAYLOAD, "reason": "  核验 WMS 修正计划  "}
        )
    assert response.status_code == 200
    assert response.json()["data"] == {
        "task_id": task_id,
        "plan_revision": 2,
        "version": 4,
        "correction_evidence_id": 52,
    }
    service.apply_correction.assert_awaited_once_with(
        task_id=task_id,
        blocked_evidence_id=51,
        correction_evidence_id=52,
        expected_version=3,
        reason="核验 WMS 修正计划",
        actor_id=42,
        received_at=ANY,
    )
    received = service.apply_correction.call_args.kwargs["received_at"]
    assert isinstance(received, datetime)
    assert received.tzinfo is None


async def test_conflict_preserves_service_rejection():
    from src.app.wms_integration.outbound_picking.services.picking_task_plan_delta import PlanCorrectionConflictError

    service = SimpleNamespace(apply_correction=AsyncMock(side_effect=PlanCorrectionConflictError("证据已变化")))
    async with AsyncClient(transport=ASGITransport(app=app_for(service)), base_url="http://test") as client:
        response = await client.post(URL, json=PAYLOAD)
    assert response.status_code == 409
