from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest


def _module():
    return importlib.import_module("src.app.workline.v1.inbound_handoff")


def _response_data(response: object) -> Any:
    if isinstance(response, Mapping):
        return cast("Mapping[str, Any]", response)["data"]
    return cast("Any", response).data


def _response_code(response: object) -> str:
    if isinstance(response, Mapping):
        return str(cast("Mapping[str, Any]", response)["code"])
    return str(cast("Any", response).code)


def _get_route(module: Any, path: str, method: str):
    for route in module.router.routes:
        if method in route.methods and route.path == path:
            return route
    raise AssertionError(f"{method} {path} route not found")


def _permission_names(module: Any, path: str, method: str) -> list[str]:
    route = _get_route(module, path, method)
    return [getattr(dep.dependency, "permission_required", "") for dep in route.dependencies]


def test_inbound_handoff_routes_require_expected_permissions() -> None:
    module = _module()

    assert _permission_names(module, "/demands", "GET") == ["biz:workline:list"]
    assert _permission_names(module, "/demands/{demand_id}", "GET") == ["biz:workline:list"]
    assert _permission_names(module, "/source-items/{source_item_id}/actions/retry-source-pick", "POST") == [
        "biz:workline:update"
    ]


def test_inbound_handoff_debug_routes_are_not_registered() -> None:
    module = _module()

    assert all("/debug" not in route.path for route in module.router.routes)


@pytest.mark.asyncio
async def test_list_inbound_handoff_demands_uses_service(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    route = _get_route(module, "/demands", "GET")
    expected = {
        "total": 1,
        "items": [
            {
                "id": 11,
                "demand_key": "smt-inbound-handoff:release-001",
                "status": "MANUAL_HOLD",
                "decision_status": "DIRECT_SORTING",
                "failure_code": "SOURCE_PICK_INBOX_DEAD_LETTER",
                "item_status_counts": {"MANUAL_HOLD": 1},
                "handling_trace_summary": {"handling_operation_key": None},
                "claim_recovery_summary": {"dead_letter": 1},
                "available_actions": ["RETRY_SOURCE_PICK", "RELEASE_HOLD"],
            }
        ],
    }
    list_summaries = AsyncMock(return_value=expected)
    monkeypatch.setattr(module.smt_inbound_handoff_service, "list_handoff_demand_summaries", list_summaries)
    db = object()

    response = await route.endpoint(db=db, limit=20, offset=0, status=None)

    list_summaries.assert_awaited_once_with(db, limit=20, offset=0, status=None)
    assert _response_data(response)["items"][0]["available_actions"] == ["RETRY_SOURCE_PICK", "RELEASE_HOLD"]
    assert _response_data(response)["items"][0]["claim_recovery_summary"] == {"dead_letter": 1}


@pytest.mark.asyncio
async def test_get_inbound_handoff_detail_returns_source_item_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    route = _get_route(module, "/demands/{demand_id}", "GET")
    expected = {
        "id": 11,
        "demand_key": "smt-inbound-handoff:release-001",
        "status": "CLAIMED_BY_SORTING",
        "available_actions": ["SCAN_RECOVERY"],
        "release_snapshot": {"bins": []},
        "source_items": [
            {
                "id": 22,
                "status": "CLAIMED_BY_SORTING",
                "source_pick_inbox_id": 2101,
                "source_pick_command_id": 88,
                "source_pick_command_code": "CMD-SOURCE-PICK-001",
                "source_pick_dispatch_key": "device-command:CMD-SOURCE-PICK-001",
                "source_pick_inbox": {"status": "PROCESSED", "error_message": None},
                "source_pick_command": {"status": "PENDING", "result": None},
            }
        ],
    }
    get_detail = AsyncMock(return_value=expected)
    monkeypatch.setattr(module.smt_inbound_handoff_service, "get_handoff_demand_detail", get_detail)
    db = object()

    response = await route.endpoint(demand_id=11, db=db)

    get_detail.assert_awaited_once_with(db, 11)
    assert _response_data(response)["source_items"][0]["source_pick_inbox_id"] == 2101
    assert _response_data(response)["source_items"][0]["source_pick_command_code"] == "CMD-SOURCE-PICK-001"


@pytest.mark.asyncio
async def test_get_inbound_handoff_detail_maps_missing_to_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    route = _get_route(module, "/demands/{demand_id}", "GET")
    monkeypatch.setattr(module.smt_inbound_handoff_service, "get_handoff_demand_detail", AsyncMock(return_value=None))

    response = await route.endpoint(demand_id=404, db=object())

    assert _response_code(response) == "3000"


@pytest.mark.asyncio
async def test_retry_source_pick_action_uses_service_and_state_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    route = _get_route(module, "/source-items/{source_item_id}/actions/retry-source-pick", "POST")
    retry = AsyncMock(return_value={"id": 22, "status": "READY", "available_actions": []})
    monkeypatch.setattr(module.smt_inbound_handoff_service, "retry_source_pick_action", retry)
    db = object()

    response = await route.endpoint(source_item_id=22, db=db)

    retry.assert_awaited_once_with(db, source_item_id=22)
    assert _response_data(response)["status"] == "READY"


@pytest.mark.asyncio
async def test_retry_source_pick_action_maps_invalid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    route = _get_route(module, "/source-items/{source_item_id}/actions/retry-source-pick", "POST")
    monkeypatch.setattr(
        module.smt_inbound_handoff_service,
        "retry_source_pick_action",
        AsyncMock(side_effect=ValueError("当前状态不可重试 source pick")),
    )

    response = await route.endpoint(source_item_id=22, db=object())

    assert _response_code(response) == "4001"
