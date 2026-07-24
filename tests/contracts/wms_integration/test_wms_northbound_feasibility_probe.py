"""北向 WMS 可行性探针必须只依赖 HTTP 合同，而不是 WES adapter 或 mock 内部状态。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response

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
    """最小 WMS 联调 stub；探针仅通过公开 HTTP 面观察状态和业务效果计数。"""

    app = FastAPI()
    records: dict[tuple[str, str], dict[str, Any]] = {}
    effects: dict[tuple[str, str], int] = {}
    clock = {"seconds": 0}
    contract_values = {
        "retention_seconds": 9,
        "wes_max_confirmation_age_seconds": 6,
        "safety_margin_seconds": 3,
        "visibility_sla_seconds": 2,
        "not_found_grace_period_seconds": 3,
        "max_response_bytes": 4096,
    }

    def record_key(operation_identity: str, idempotency_key: str) -> tuple[str, str]:
        return operation_identity, idempotency_key

    def visible_snapshot(state: str, source_version: int, *, reason_code: str | None = None) -> dict[str, Any]:
        result_payload = None
        if state == "COMPLETED":
            result_payload = {
                "accepted": True,
                "dispatch_key": "dispatch-001",
                "correlation_id": "correlation-001",
                "source_version": source_version,
            }
        return _snapshot(state, source_version=source_version, reason_code=reason_code, result_payload=result_payload)

    @app.get("/northbound/contract")
    async def contract() -> dict[str, int]:
        return contract_values

    @app.post("/northbound/test-clock/advance")
    async def advance_clock(payload: dict[str, int]) -> dict[str, int]:
        clock["seconds"] += payload["seconds"]
        return {"now": clock["seconds"]}

    @app.get("/northbound/operations/effects")
    async def effect_count(operation_identity: str = Query(), idempotency_key: str = Query()) -> dict[str, int]:
        return {"effect_count": effects.get(record_key(operation_identity, idempotency_key), 0)}

    @app.get("/northbound/test/oversized-response")
    async def oversized_response() -> Response:
        return Response(content="x" * (contract_values["max_response_bytes"] + 1), media_type="application/json")

    @app.post("/northbound/operations")
    async def submit(
        payload: dict[str, Any], x_first_attempt_dropped: str | None = Header(default=None)
    ) -> JSONResponse:
        operation_identity = str(payload["operation_identity"])
        idempotency_key = str(payload["idempotency_key"])
        canonical_payload = payload["canonical_payload"]
        frozen_binding = payload["frozen_binding"]
        key = record_key(operation_identity, idempotency_key)
        if x_first_attempt_dropped == "true":
            raise HTTPException(status_code=504, detail={"error_code": "UPSTREAM_TIMEOUT"})

        record = records.get(key)
        if record is not None and clock["seconds"] - record["created_at"] >= contract_values["retention_seconds"]:
            records.pop(key)
            record = None
        if record is not None:
            if record["canonical_payload"] != canonical_payload or record["frozen_binding"] != frozen_binding:
                raise HTTPException(status_code=422, detail={"error_code": "IDEMPOTENCY_CONFLICT"})
            if record["scenario"] == "rejected":
                return JSONResponse(
                    status_code=200, content=visible_snapshot("REJECTED", 0, reason_code="WMS_BUSINESS_REJECTED")
                )
            if record["status_reads"] >= 3:
                return JSONResponse(status_code=200, content=visible_snapshot("COMPLETED", 2))
            raise HTTPException(status_code=409, detail={"error_code": "IDEMPOTENCY_REQUEST_IN_PROGRESS"})

        records[key] = {
            "canonical_payload": canonical_payload,
            "frozen_binding": frozen_binding,
            "scenario": canonical_payload.get("scenario", "success"),
            "status_reads": 0,
            "created_at": clock["seconds"],
        }
        effects[key] = effects.get(key, 0) + 1
        return JSONResponse(status_code=202, content=visible_snapshot("ACCEPTED", 0))

    @app.get("/northbound/operations/status")
    async def status(
        operation_identity: str = Query(),
        idempotency_key: str = Query(),
        x_probe_fault: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if x_probe_fault == "rate_limit_delta":
            raise HTTPException(status_code=429, detail={"error_code": "RATE_LIMITED"}, headers={"Retry-After": "2"})
        if x_probe_fault == "rate_limit_date":
            retry_at = datetime.now(UTC) + timedelta(seconds=2)
            raise HTTPException(
                status_code=429,
                detail={"error_code": "RATE_LIMITED"},
                headers={"Retry-After": format_datetime(retry_at, usegmt=True)},
            )
        if x_probe_fault == "unavailable":
            raise HTTPException(status_code=503, detail={"error_code": "TEMPORARILY_UNAVAILABLE"})
        if x_probe_fault == "timeout":
            await asyncio.sleep(0.05)

        record = records.get(record_key(operation_identity, idempotency_key))
        if record is None:
            return _snapshot("NOT_FOUND", source_version=None)
        if record["scenario"] == "rejected":
            return visible_snapshot("REJECTED", 0, reason_code="WMS_BUSINESS_REJECTED")
        if record["scenario"] == "recoverable_not_found":
            return _snapshot("NOT_FOUND", source_version=None)
        if (
            record["scenario"] == "not_visible"
            and clock["seconds"] - record["created_at"] < contract_values["visibility_sla_seconds"]
        ):
            return _snapshot("NOT_FOUND", source_version=None)
        if record["scenario"] == "visible_then_missing" and record["status_reads"] > 0:
            return _snapshot("NOT_FOUND", source_version=None)

        record["status_reads"] += 1
        if record["status_reads"] == 1:
            return visible_snapshot("ACCEPTED", 0)
        if record["status_reads"] == 2:
            return visible_snapshot("PROCESSING", 1)
        return visible_snapshot("COMPLETED", 2)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mock-wms.test",
        timeout=httpx.Timeout(0.01),
    ) as client:
        yield client


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
        "controlled_recovery_replay_preserves_frozen_request",
        "visible_then_not_found_requires_reconciliation",
        "accepted_not_visible_replay_has_one_effect",
        "retention_and_visibility_boundaries",
        "retention_boundary_observed",
        "rate_limit_retry_after",
        "wms_5xx_shape",
        "status_query_timeout",
        "maximum_response_body",
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


@pytest.mark.asyncio
async def test_malicious_remote_body_never_reaches_probe_output(capsys: pytest.CaptureFixture[str]) -> None:
    """远端错误文本可含 secret/PII，探针只能输出本地 case 枚举和布尔结果。"""

    app = FastAPI()
    remote_secret = "credential=top-secret;customer=Alice Example;payload=untrusted-body"

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def malicious_response(path: str) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": {"error_code": remote_secret}})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://malicious-wms.test",
    ) as client:
        report = await run_probe(client, operation_identity=OPERATION_IDENTITY, request_timeout_seconds=0.01)

    captured = capsys.readouterr()
    serialized = json.dumps([asdict(case) for case in report.cases])
    assert report.passed is False
    assert remote_secret not in captured.out
    assert remote_secret not in captured.err
    assert remote_secret not in serialized
    assert {case.detail for case in report.cases} == {"CONTRACT_ASSERTION"}
