"""北向 WMS 可行性探针必须只依赖 HTTP 合同，而不是 WES adapter 或 mock 内部状态。"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient

from scripts import verify_wms_northbound_feasibility as probe_module
from scripts.verify_wms_northbound_feasibility import (
    _contract_values,
    _is_ack,
    _is_snapshot,
    _status_headers,
    _submit_headers,
    run_probe,
)
from src.app.wms_integration.operation_contract import WmsCompletionMode
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS
from src.app.wms_integration.ports.fulfillment_operations import (
    ConveyorExitCandidate,
    WmsAcceptedScope,
    WmsEffectAck,
    accepted_scope_digest,
    frozen_candidate_digest,
)
from src.core.conf import settings
from tests.contracts.wms_integration.provider_profile_support import (
    build_compiled_provider_profile,
    build_hmac_provider_profile_payload,
)
from tests.mock import wms_mock_server
from tests.mock.wms_northbound_contract import (
    ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
    MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V1,
    MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2,
)
from tests.mock.wms_operation_fixtures import REQUEST_FIXTURES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ASYNC_OPERATIONS = tuple(
    operation for operation in WMS_OPERATIONS if operation.completion_mode is WmsCompletionMode.ASYNC_TASK
)
OPERATION_IDENTITIES = frozenset(operation.identity for operation in ASYNC_OPERATIONS)
SAMPLE_ASYNC_OPERATION = ASYNC_OPERATIONS[0]
PROBE_PROFILE_PAYLOAD = build_hmac_provider_profile_payload()
PROBE_PROFILE_PAYLOAD["outbound_auth"]["credential_reference"] = ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE
PROBE_PROFILE_PAYLOAD["effect_status_path"] = "/northbound/operations/status"
PROBE_COMPILED_PROFILE = build_compiled_provider_profile(PROBE_PROFILE_PAYLOAD)
E12 = "wms.fulfillment.move_bins_to_conveyor_entry@v1"
E13 = "wms.fulfillment.move_bins_from_conveyor_exit@v1"


def _batch_request(operation_identity: str) -> Any:
    operation = WMS_OPERATION_BY_IDENTITY[operation_identity]
    payload = deepcopy(REQUEST_FIXTURES[operation_identity])
    if operation_identity == E12:
        first = payload["items"][0]
        payload["items"] = [
            first,
            {
                **first,
                "sequence_no": 2,
                "route_instance_id": "ROUTE-002",
                "bin_id": "BIN-002",
                "source_slot_id": "SLOT-002",
                "reserved_queue_position": 2,
            },
        ]
    else:
        first = payload["candidate_items"][0]
        payload["candidate_items"] = [
            first,
            {
                **first,
                "sequence_no": 2,
                "route_instance_id": "ROUTE-002",
                "bin_id": "BIN-002",
                "scan3_enqueued_at": "2026-07-29T00:00:01+00:00",
                "queue_position": 2,
            },
            {
                **first,
                "sequence_no": 3,
                "route_instance_id": "ROUTE-003",
                "bin_id": "BIN-003",
                "scan3_enqueued_at": "2026-07-29T00:00:02+00:00",
                "queue_position": 3,
            },
        ]
        candidate_items = tuple(ConveyorExitCandidate.model_validate(item) for item in payload["candidate_items"])
        payload["candidate_digest"] = frozen_candidate_digest(
            workline_id=payload["workline_id"],
            queue_code=payload["queue_code"],
            candidate_items=candidate_items,
        )
    return operation.request_model.model_validate(payload)


def _batch_ack(operation_identity: str, object_keys: tuple[str, ...]) -> WmsEffectAck:
    return WmsEffectAck(
        operation_identity=operation_identity,
        idempotency_key=f"probe-{operation_identity}",
        provider_reference=f"provider-{operation_identity}",
        submission_state="ACCEPTED",
        accepted_scope=WmsAcceptedScope(
            object_keys=object_keys,
            scope_digest=accepted_scope_digest(object_keys),
        ),
    )


def test_probe_ack_rejects_forged_batch_member() -> None:
    request = _batch_request(E13)
    forged_keys = (request.candidate_items[0].bin_id, "FORGED-BIN")
    ack = _batch_ack(E13, forged_keys)

    assert (
        _is_ack(
            ack.model_dump(mode="json"),
            operation_identity=E13,
            idempotency_key=ack.idempotency_key,
            request=request,
        )
        is False
    )


def test_probe_ack_rejects_e13_non_prefix_members() -> None:
    request = _batch_request(E13)
    non_prefix_keys = (request.candidate_items[1].bin_id,)
    ack = _batch_ack(E13, non_prefix_keys)

    assert (
        _is_ack(
            ack.model_dump(mode="json"),
            operation_identity=E13,
            idempotency_key=ack.idempotency_key,
            request=request,
        )
        is False
    )


def test_probe_ack_rejects_e12_partial_batch() -> None:
    request = _batch_request(E12)
    partial_keys = tuple(item.bin_id for item in request.items[:-1])
    ack = _batch_ack(E12, partial_keys)

    assert (
        _is_ack(
            ack.model_dump(mode="json"),
            operation_identity=E12,
            idempotency_key=ack.idempotency_key,
            request=request,
        )
        is False
    )


@pytest.mark.parametrize("drift_field", ["provider_reference", "accepted_scope"])
def test_probe_snapshot_rejects_cross_state_ack_drift(drift_field: str) -> None:
    request = _batch_request(E13)
    accepted_keys = tuple(item.bin_id for item in request.candidate_items[:2])
    ack = _batch_ack(E13, accepted_keys)
    snapshot = {
        "state": "PROCESSING",
        "provider_reference": ack.provider_reference,
        "accepted_scope": ack.accepted_scope.model_dump(mode="json"),
        "reason_code": None,
        "updated_at": datetime.now(UTC).isoformat(),
        "source_version": 1,
        "result_payload": None,
    }
    if drift_field == "provider_reference":
        snapshot[drift_field] = "provider-drift"
    else:
        drift_keys = (request.candidate_items[0].bin_id,)
        snapshot[drift_field] = WmsAcceptedScope(
            object_keys=drift_keys,
            scope_digest=accepted_scope_digest(drift_keys),
        ).model_dump(mode="json")

    assert (
        _is_snapshot(
            snapshot,
            operation_identity=E13,
            payload=REQUEST_FIXTURES[E13],
            frozen_ack=ack,
        )
        is False
    )


def test_probe_rejected_snapshot_rejects_self_consistent_forged_scope_without_first_ack() -> None:
    request = _batch_request(E13)
    forged_keys = (request.candidate_items[0].bin_id, "FORGED-BIN")
    forged_scope = WmsAcceptedScope(
        object_keys=forged_keys,
        scope_digest=accepted_scope_digest(forged_keys),
    )
    snapshot = {
        "state": "REJECTED",
        "provider_reference": "provider-status-first",
        "accepted_scope": forged_scope.model_dump(mode="json"),
        "reason_code": "NO_STORAGE_SLOT",
        "updated_at": datetime.now(UTC).isoformat(),
        "source_version": 1,
        "result_payload": None,
    }

    assert (
        _is_snapshot(
            snapshot,
            operation_identity=E13,
            payload=request.model_dump(mode="json"),
            status_first_request=request,
            idempotency_key="status-first-rejected",
        )
        is False
    )


def test_probe_status_first_snapshot_validates_recovered_ack_against_original_request() -> None:
    request = _batch_request(E13)
    accepted_keys = tuple(item.bin_id for item in request.candidate_items[:2])
    accepted_scope = WmsAcceptedScope(
        object_keys=accepted_keys,
        scope_digest=accepted_scope_digest(accepted_keys),
    )
    snapshot = {
        "state": "ACCEPTED",
        "provider_reference": "provider-status-first",
        "accepted_scope": accepted_scope.model_dump(mode="json"),
        "reason_code": None,
        "updated_at": datetime.now(UTC).isoformat(),
        "source_version": 0,
        "result_payload": None,
    }

    assert (
        _is_snapshot(
            snapshot,
            operation_identity=E13,
            payload=request.model_dump(mode="json"),
            status_first_request=request,
            idempotency_key="status-first-accepted",
        )
        is True
    )


def test_contract_values_reject_retention_shorter_than_wes_confirmation_window() -> None:
    contract = {
        "credential_reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "idempotency_retention_seconds": 5,
        "status_visibility_sla_seconds": 2,
        "max_response_bytes": 4096,
        "submit_deadline_seconds": 2,
        "status_deadline_seconds": 2,
    }

    assert _contract_values(contract, compiled_profile=PROBE_COMPILED_PROFILE) is None


def test_contract_values_reject_visibility_slower_than_wes_not_found_grace() -> None:
    contract = {
        "credential_reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "idempotency_retention_seconds": 9,
        "status_visibility_sla_seconds": 4,
        "max_response_bytes": 4096,
        "submit_deadline_seconds": 2,
        "status_deadline_seconds": 2,
    }

    assert _contract_values(contract, compiled_profile=PROBE_COMPILED_PROFILE) is None


def test_contract_values_accept_finite_fractional_time_contract() -> None:
    contract = {
        "credential_reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "idempotency_retention_seconds": 9,
        "status_visibility_sla_seconds": 2.5,
        "max_response_bytes": 4096,
        "submit_deadline_seconds": 30.5,
        "status_deadline_seconds": 2.5,
    }

    assert _contract_values(contract, compiled_profile=PROBE_COMPILED_PROFILE) == contract


@pytest.mark.parametrize("invalid_value", [float("inf"), float("-inf"), float("nan")])
def test_contract_values_reject_non_finite_time_contract(invalid_value: float) -> None:
    contract = {
        "credential_reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        "idempotency_retention_seconds": 9,
        "status_visibility_sla_seconds": 2,
        "max_response_bytes": 4096,
        "submit_deadline_seconds": invalid_value,
        "status_deadline_seconds": 2,
    }

    assert _contract_values(contract, compiled_profile=PROBE_COMPILED_PROFILE) is None


def test_mock_contract_deadlines_match_wes_transport_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    submit_deadlines = {operation.budget.deadline_seconds for operation in ASYNC_OPERATIONS}
    assert len(submit_deadlines) == 1
    expected_submit_deadline = float(submit_deadlines.pop())
    monkeypatch.setenv("WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS", str(expected_submit_deadline))
    monkeypatch.setenv("WMS_EFFECT_STATUS_TIMEOUT_SECONDS", str(settings.WMS_EFFECT_STATUS_TIMEOUT_SECONDS))

    with TestClient(wms_mock_server.app) as client:
        contract = client.get("/northbound/contract")

    assert contract.status_code == 200
    assert contract.json()["submit_deadline_seconds"] == expected_submit_deadline
    assert contract.json()["status_deadline_seconds"] == settings.WMS_EFFECT_STATUS_TIMEOUT_SECONDS


def test_mock_contract_accepts_fractional_time_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS", "2.5")
    monkeypatch.setenv("WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS", "30.5")
    monkeypatch.setenv("WMS_EFFECT_STATUS_TIMEOUT_SECONDS", "2.5")

    with TestClient(wms_mock_server.app) as client:
        contract = client.get("/northbound/contract")

    assert contract.status_code == 200
    assert contract.json()["status_visibility_sla_seconds"] == 2.5
    assert contract.json()["submit_deadline_seconds"] == 30.5
    assert contract.json()["status_deadline_seconds"] == 2.5


@pytest.mark.asyncio
async def test_probe_reports_public_deadline_alignment(northbound_mock_client: httpx.AsyncClient) -> None:
    report = await run_probe(
        northbound_mock_client,
        compiled_profile=PROBE_COMPILED_PROFILE,
        request_timeout_seconds=1.0,
    )

    case = next((case for case in report.cases if case.case_id == "public_contract_deadline_alignment"), None)
    assert case is not None
    assert case.passed is True


def test_feasibility_probe_script_is_directly_executable() -> None:
    script = Path(__file__).resolve().parents[3] / "scripts/verify_wms_northbound_feasibility.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        check=False,
        capture_output=True,
        cwd=script.parent,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--base-url" in completed.stdout


@pytest.mark.asyncio
async def test_feasibility_probe_cli_ignores_host_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        probe_module,
        "_parse_args",
        lambda: SimpleNamespace(
            base_url="http://127.0.0.1:8011",
            operation_identity=None,
            timeout_seconds=0.25,
            submit_timeout_seconds=0.25,
            status_timeout_seconds=0.25,
        ),
    )
    monkeypatch.setattr(probe_module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        probe_module,
        "validate_wms_transport_configuration",
        lambda *, settings_source: SimpleNamespace(compiled_profile=PROBE_COMPILED_PROFILE),
    )
    monkeypatch.setattr(
        probe_module,
        "run_probe",
        AsyncMock(return_value=probe_module.FeasibilityReport(cases=())),
    )

    assert await probe_module._main() == 0
    assert captured["trust_env"] is False


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
async def northbound_mock_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    """验收探针连接实际 Mock app，仅通过其公开 HTTP 路由复位和观察。"""

    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V1, "probe-material-flow-v1")
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, "probe-material-flow-v2")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wms_mock_server.app),
        base_url="http://mock-wms.test",
        timeout=httpx.Timeout(0.05),
    ) as client:
        reset = await client.post("/debug/reset")
        assert reset.status_code == 200
        yield client


@pytest.mark.asyncio
async def test_feasibility_probe_verifies_minimal_wms_contract_over_http(
    northbound_mock_client: httpx.AsyncClient,
) -> None:
    report = await run_probe(
        northbound_mock_client,
        compiled_profile=PROBE_COMPILED_PROFILE,
        request_timeout_seconds=1.0,
    )

    failed_cases = tuple(case for case in report.cases if not case.passed)
    assert report.passed is True, failed_cases
    case_ids = {case.case_id for case in report.cases}
    assert {"public_contract_parameters", "active_v2_hmac_secret_available"} <= case_ids
    for operation_identity in OPERATION_IDENTITIES:
        assert {
            f"{operation_identity}:first_submit",
            f"{operation_identity}:in_progress_replay",
            f"{operation_identity}:five_state_progression_and_typed_result",
            f"{operation_identity}:completed_replay",
            f"{operation_identity}:idempotency_conflict",
            f"{operation_identity}:rejected_stable_reason",
            f"{operation_identity}:not_found",
            f"{operation_identity}:visibility_sla_and_retention_boundaries",
            f"{operation_identity}:visible_then_lost_is_independent_fault",
            f"{operation_identity}:typed_request_validation",
            f"{operation_identity}:callback_hint_evidence_and_status_authority",
            f"{operation_identity}:submit_hmac_signature_tamper",
        } <= case_ids
    assert {
        "fault_matrix_rate_limit_and_fixed_5xx",
        "northbound_fault_scope_excludes_health_inventory_and_unregistered_paths",
        "current_master_data_route_is_live_and_legacy_route_is_removed",
        "submit_deadline_ambiguous_retry_one_effect",
        "status_deadline",
        "response_body_budget_exceeded_without_remote_echo",
        "status_nonce_replay_rejected_without_remote_echo",
        "status_hmac_tamper_rejected_without_remote_echo",
        "submit_stale_timestamp_rejected_without_remote_echo",
        "public_reset_clears_observable_operation",
    } <= case_ids
    assert all(case.passed for case in report.cases)
    assert all("secret" not in case.detail.lower() for case in report.cases)


@pytest.mark.asyncio
async def test_batch_probe_binds_rejected_visible_and_deadline_status_to_first_ack(
    northbound_mock_client: httpx.AsyncClient,
) -> None:
    report = await run_probe(
        northbound_mock_client,
        compiled_profile=PROBE_COMPILED_PROFILE,
        operation_identity=E13,
        request_timeout_seconds=1.0,
    )

    required_cases = {
        f"{E13}:rejected_stable_reason",
        f"{E13}:visibility_sla_and_retention_boundaries",
        "status_deadline",
    }
    assert all(case.passed for case in report.cases if case.case_id in required_cases)
    assert required_cases <= {case.case_id for case in report.cases}


@pytest.mark.asyncio
async def test_mock_rejects_raw_body_hash_tampering(
    northbound_mock_client: httpx.AsyncClient,
) -> None:
    operation_identity = SAMPLE_ASYNC_OPERATION.identity
    raw_body = json.dumps(REQUEST_FIXTURES[operation_identity], separators=(",", ":")).encode()
    path = f"/api/wms{SAMPLE_ASYNC_OPERATION.path_template}"
    headers = _submit_headers(
        secret=b"probe-material-flow-v2",
        credential_reference=ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
        path=path,
        body=raw_body,
        operation_identity=operation_identity,
        key="tampered-content-hash",
    )
    headers["X-WES-Content-SHA256"] = "0" * 64

    response = await northbound_mock_client.post(
        path,
        content=raw_body,
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json() == {"code": "CONTENT_HASH_MISMATCH"}


@pytest.mark.asyncio
async def test_feasibility_probe_reports_protocol_failure_without_response_body(
    northbound_mock_client: httpx.AsyncClient,
) -> None:
    operation_identity = SAMPLE_ASYNC_OPERATION.identity
    raw_path = "/northbound/operations/status?" + urlencode(
        (("operation_identity", operation_identity), ("idempotency_key", "missing"))
    )
    response = await northbound_mock_client.get(
        raw_path,
        headers=_status_headers(
            secret=b"probe-material-flow-v2",
            credential_reference=ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
            raw_path=raw_path,
        ),
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
        report = await run_probe(
            client,
            compiled_profile=PROBE_COMPILED_PROFILE,
            operation_identity=SAMPLE_ASYNC_OPERATION.identity,
            request_timeout_seconds=0.01,
        )

    captured = capsys.readouterr()
    serialized = json.dumps([asdict(case) for case in report.cases])
    assert report.passed is False
    assert remote_secret not in captured.out
    assert remote_secret not in captured.err
    assert remote_secret not in serialized
    assert {case.detail for case in report.cases} == {"CONTRACT_ASSERTION"}


@pytest.mark.asyncio
async def test_malicious_200_contract_values_fail_without_traceback_or_remote_echo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """字符串、bool 和空值合同参数只能变成固定失败摘要，不能进入算术或输出。"""

    app = FastAPI()
    remote_secret = "retention=secret-customer-Alice"

    @app.get("/northbound/contract")
    async def invalid_contract() -> dict[str, object]:
        return {
            "credential_reference": remote_secret,
            "idempotency_retention_seconds": True,
            "status_visibility_sla_seconds": None,
            "max_response_bytes": "4096",
            "submit_deadline_seconds": 2,
            "status_deadline_seconds": False,
        }

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def other_responses(path: str) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": {"error_code": remote_secret}})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://invalid-contract-wms.test",
    ) as client:
        report = await run_probe(
            client,
            compiled_profile=PROBE_COMPILED_PROFILE,
            operation_identity=SAMPLE_ASYNC_OPERATION.identity,
            request_timeout_seconds=0.01,
        )

    captured = capsys.readouterr()
    serialized = json.dumps([asdict(case) for case in report.cases])
    assert report.passed is False
    assert remote_secret not in captured.out + captured.err + serialized
    assert {case.detail for case in report.cases} == {"CONTRACT_ASSERTION"}


@pytest.mark.asyncio
async def test_total_deadline_includes_slow_streamed_response_body(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """即使 headers 已返回，慢速分块 body 也必须消耗同一个客户端总 deadline。"""

    app = FastAPI()
    monkeypatch.setenv(MATERIAL_FLOW_SANDBOX_HMAC_SECRET_ENV_V2, "probe-material-flow-v2")

    @app.get("/northbound/contract")
    async def contract() -> dict[str, object]:
        return {
            "credential_reference": ACTIVE_MATERIAL_FLOW_SANDBOX_CREDENTIAL_REFERENCE,
            "idempotency_retention_seconds": 9,
            "status_visibility_sla_seconds": 2,
            "max_response_bytes": 4096,
            "submit_deadline_seconds": 2,
            "status_deadline_seconds": 2,
        }

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def slow_body(path: str) -> StreamingResponse:
        async def body():
            await asyncio.sleep(0.05)
            yield json.dumps(_snapshot("ACCEPTED", source_version=10)).encode()

        return StreamingResponse(body(), media_type="application/json")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://slow-body-wms.test",
    ) as client:
        report = await run_probe(
            client,
            compiled_profile=PROBE_COMPILED_PROFILE,
            operation_identity=SAMPLE_ASYNC_OPERATION.identity,
            request_timeout_seconds=0.01,
        )

    captured = capsys.readouterr()
    first_submit = next(
        case for case in report.cases if case.case_id == f"{SAMPLE_ASYNC_OPERATION.identity}:first_submit"
    )
    assert first_submit.passed is False
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.asyncio
async def test_total_deadline_timeout_still_closes_streamed_response() -> None:
    class TrackingSlowStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.close_started = 0
            self.close_finished = 0

        async def __aiter__(self):
            await asyncio.sleep(0.05)
            yield b"{}"

        async def aclose(self) -> None:
            self.close_started += 1
            await asyncio.sleep(0)
            self.close_finished += 1

    stream = TrackingSlowStream()

    async def slow_response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(slow_response),
        base_url="http://slow-body-wms.test",
    ) as client:
        response = await probe_module._request(
            client,
            "GET",
            "/slow",
            request_timeout_seconds=0.001,
        )

    assert response is None
    assert stream.close_started == 1
    assert stream.close_finished == 1
