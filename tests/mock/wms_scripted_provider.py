"""仅供 conformance 使用的进程内最小 WMS scripted provider。"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from tests.support.wms_provider_conformance import ScriptedQueryCase


async def scripted_query_inventory_response(
    case: ScriptedQueryCase,
    request: httpx.Request,
) -> httpx.Response:
    """按命名故障点同步返回确定性响应，不启动服务或第二 transport。"""

    scenario = case.scenario.value
    if scenario == "timeout":
        raise httpx.ReadTimeout("scripted provider timeout", request=request)
    if scenario == "rate_limit":
        return httpx.Response(429, headers={"retry-after": "3"})
    if scenario == "unavailable":
        return httpx.Response(503)
    if scenario == "reject":
        return httpx.Response(
            409,
            json={
                "classification": "BUSINESS_REJECT",
                "reason_code": "INSUFFICIENT_STOCK",
                "message": "scripted business reject",
            },
        )
    if scenario == "malformed":
        return httpx.Response(200, content=b"not-json")
    if scenario == "missing_field":
        return httpx.Response(200, json={"source_version": "SCRIPTED-V1"})
    if scenario == "invalid_decimal":
        return httpx.Response(200, json={"items": [{"sku": "MAT-001", "available_qty": "not-decimal"}]})
    if scenario == "pagination":
        if request.url.params.get("cursor") is None:
            return httpx.Response(
                200,
                json={"items": [{"sku": "MAT-001", "available_qty": "1"}], "next_cursor": "page-2"},
            )
        return httpx.Response(200, json={"items": [{"sku": "MAT-002", "available_qty": "2"}]})
    if scenario == "precision":
        return httpx.Response(
            200,
            json={"items": [{"sku": "MAT-001", "available_qty": "9007199254740993.125"}]},
        )
    if scenario == "budget":
        return httpx.Response(200, content=b'{"items":[],"padding":"' + b"x" * 80 + b'"}')
    if scenario in {"empty", "evidence_failure"}:
        return httpx.Response(200, json={"items": []})
    if scenario == "success":
        return httpx.Response(
            200,
            json={
                "items": [{"sku": "MAT-001", "available_qty": "7.25"}],
                "source_version": "SCRIPTED-V1",
            },
        )
    raise AssertionError(f"unknown scripted conformance case: {case.case_id}")


class ScriptedWmsQueryInventoryProvider:
    """只持有单个冻结题目；无 endpoint、credential、服务或容器能力。"""

    __slots__ = ("_case",)

    def __init__(self, case: ScriptedQueryCase) -> None:
        self._case = case

    async def handle(self, request: httpx.Request) -> httpx.Response:
        return await scripted_query_inventory_response(self._case, request)


__all__ = ["ScriptedWmsQueryInventoryProvider", "scripted_query_inventory_response"]
