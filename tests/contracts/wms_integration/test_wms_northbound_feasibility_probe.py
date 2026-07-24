"""北向 WMS 可行性探针必须只依赖 HTTP 合同，而不是 WES adapter 或 mock 内部状态。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from scripts.verify_wms_northbound_feasibility import run_probe

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

OPERATION_IDENTITY = "wms.fulfillment.notify_pkg_binding@v1"


def _snapshot(
    state: str,
    *,
    source_version: int | None,
    reason_code: str | None = None,
    result_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造模拟 WMS 通过 HTTP 暴露的状态快照，不暴露其内部记录。"""

    return {
        "state": state,
        "provider_reference": "mock-provider-ref-001" if state != "NOT_FOUND" else None,
        "reason_code": reason_code,
        "updated_at": datetime(2026, 7, 24, 8, 0, tzinfo=UTC).isoformat() if state != "NOT_FOUND" else None,
        "source_version": source_version,
        "result_payload": result_payload,
    }


@pytest.fixture
async def northbound_stub_client() -> AsyncIterator[httpx.AsyncClient]:
    """最小 WMS 联调 stub；探针仅能通过它的公开 HTTP 面观察行为。"""

    app = FastAPI()
    records: dict[tuple[str, str], dict[str, Any]] = {}

    @app.get("/northbound/contract")
    async def contract() -> dict[str, int]:
        return {
            "retention_seconds": 900,
            "wes_max_confirmation_age_seconds": 600,
            "safety_margin_seconds": 300,
            "visibility_sla_seconds": 2,
            "not_found_grace_period_seconds": 3,
            "max_response_bytes": 8192,
        }

    @app.post("/northbound/operations")
    async def submit(
        payload: dict[str, Any], x_first_attempt_dropped: str | None = Header(default=None)
    ) -> JSONResponse:
        operation_identity = str(payload["operation_identity"])
        idempotency_key = str(payload["idempotency_key"])
        canonical_payload = payload["canonical_payload"]
        key = (operation_identity, idempotency_key)
        if x_first_attempt_dropped == "true":
            raise HTTPException(status_code=504, detail={"error_code": "UPSTREAM_TIMEOUT"})

        record = records.get(key)
        if record is not None:
            if record["canonical_payload"] != canonical_payload:
                raise HTTPException(status_code=422, detail={"error_code": "IDEMPOTENCY_CONFLICT"})
            if record["scenario"] == "rejected" or record["status_reads"] >= 3:
                return JSONResponse(status_code=200, content=_completed_payload(record))
            raise HTTPException(status_code=409, detail={"error_code": "IDEMPOTENCY_REQUEST_IN_PROGRESS"})

        records[key] = {
            "canonical_payload": canonical_payload,
            "scenario": canonical_payload.get("scenario", "success"),
            "status_reads": 0,
        }
        return JSONResponse(status_code=202, content=_snapshot("ACCEPTED", source_version=0))

    @app.get("/northbound/operations/status")
    async def status(
        operation_identity: str = Query(),
        idempotency_key: str = Query(),
        x_probe_fault: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if x_probe_fault == "rate_limit":
            raise HTTPException(status_code=429, detail={"error_code": "RATE_LIMITED"}, headers={"Retry-After": "2"})
        if x_probe_fault == "unavailable":
            raise HTTPException(status_code=503, detail={"error_code": "TEMPORARILY_UNAVAILABLE"})
        if x_probe_fault == "timeout":
            await asyncio.sleep(0.05)

        record = records.get((operation_identity, idempotency_key))
        if record is None:
            return _snapshot("NOT_FOUND", source_version=None)
        if record["scenario"] == "rejected":
            return _snapshot("REJECTED", source_version=0, reason_code="WMS_BUSINESS_REJECTED")
        if record["scenario"] == "not_visible" and record["status_reads"] == 0:
            record["status_reads"] += 1
            return _snapshot("NOT_FOUND", source_version=None)

        record["status_reads"] += 1
        if record["status_reads"] == 1:
            return _snapshot("ACCEPTED", source_version=0)
        if record["status_reads"] == 2:
            return _snapshot("PROCESSING", source_version=1)
        return _completed_payload(record)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mock-wms.test",
        timeout=httpx.Timeout(0.01),
    ) as client:
        yield client


def _completed_payload(record: dict[str, Any]) -> dict[str, Any]:
    if record["scenario"] == "rejected":
        return _snapshot("REJECTED", source_version=0, reason_code="WMS_BUSINESS_REJECTED")
    return _snapshot(
        "COMPLETED",
        source_version=2,
        result_payload={
            "accepted": True,
            "dispatch_key": "dispatch-001",
            "correlation_id": "correlation-001",
            "source_version": 2,
        },
    )


@pytest.mark.asyncio
async def test_feasibility_probe_verifies_minimal_wms_contract_over_http(
    northbound_stub_client: httpx.AsyncClient,
) -> None:
    report = await run_probe(
        northbound_stub_client,
        operation_identity=OPERATION_IDENTITY,
        request_timeout_seconds=0.01,
    )

    assert report.passed is True, report.cases
    assert {case.case_id for case in report.cases} == {
        "first_submit",
        "in_progress_replay",
        "completed_replay",
        "idempotency_conflict",
        "source_version_and_typed_result",
        "rejected_reason_code",
        "not_found_empty_version",
        "first_submit_not_arrived_retry_creates",
        "accepted_not_visible_replay_has_no_second_effect",
        "retention_and_visibility_commitments",
        "rate_limit_retry_after",
        "wms_5xx_shape",
        "status_query_timeout",
    }
    assert all(case.passed for case in report.cases)
    assert all("secret" not in case.detail.lower() for case in report.cases)


@pytest.mark.asyncio
async def test_feasibility_probe_reports_protocol_failure_without_response_body(
    northbound_stub_client: httpx.AsyncClient,
) -> None:
    response = await northbound_stub_client.get(
        "/northbound/operations/status",
        params={"operation_identity": OPERATION_IDENTITY, "idempotency_key": "missing"},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "NOT_FOUND"
    assert response.json()["source_version"] is None
