"""T8 通用 runner、真实 scenario asset 与生产 CLI 合同。"""

from __future__ import annotations

import argparse
import json
import runpy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from scripts import run_wms_conformance as wms_conformance_cli
from src.app.runtime.system_capabilities.wms.provider_conformance import (
    WMS_PROVIDER_CONFORMANCE_CASES,
    OperationConformanceObservation,
)
from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY, WMS_OPERATIONS
from tests.contracts.wms_integration.provider_profile_support import (
    build_compiled_provider_profile,
    build_hmac_provider_profile_payload,
    build_provider_profile_payload,
)
from tests.support.wms_conformance_runner import (
    GenericConformanceRunner,
    IdentityMismatchFixture,
    RealTcpConformanceRunner,
    RealTcpScenario,
    RealTcpScenarioAsset,
    RejectFixture,
    build_operation_fixture_matrix,
    build_real_tcp_scenario_matrix,
    scenario_asset_digest,
)
from tests.support.wms_integration.operation_fixtures import (
    IDENTITY_MISMATCH_FIXTURES,
    REJECT_FIXTURES,
    REQUEST_FIXTURES,
    RESULT_FIXTURES,
    WMS_OPERATION_FIXTURE_MATRIX,
)


def _cases(operation_identity: str):
    return tuple(case for case in WMS_PROVIDER_CONFORMANCE_CASES if case.operation_identity == operation_identity)


def _response_scenario(case, *, wire_kind=None, idempotency_key=None):
    observation = OperationConformanceObservation.model_validate(case.model_dump(mode="json"))
    resolved_wire_kind = (
        "STATUS_QUERY" if case.case_id in {"status_query", "partial_failure"} else wire_kind or "OPERATION"
    )
    resolved_idempotency_key = (
        idempotency_key or "idem-conformance-001" if resolved_wire_kind == "STATUS_QUERY" else idempotency_key
    )
    if case.reason_code == "WMS_PROVIDER_TIMEOUT":
        return RealTcpScenario(
            operation_identity=case.operation_identity,
            case_id=case.case_id,
            wire_kind=resolved_wire_kind,
            request_payload=REQUEST_FIXTURES[case.operation_identity],
            idempotency_key=resolved_idempotency_key,
            expected_transport="TIMEOUT",
            observation=observation,
        )
    if case.reason_code == "WMS_UNAVAILABLE":
        return RealTcpScenario(
            operation_identity=case.operation_identity,
            case_id=case.case_id,
            wire_kind=resolved_wire_kind,
            request_payload=REQUEST_FIXTURES[case.operation_identity],
            idempotency_key=resolved_idempotency_key,
            expected_transport="TRANSPORT_ERROR",
            observation=observation,
        )
    status = {
        "accepted": 202,
        "business_reject": 422,
        "reject": 422,
        "idempotency_conflict": 422,
        "in_progress": 409,
        "rate_limit": 429,
    }.get(case.case_id, 200)
    if case.case_id in {
        "business_reject",
        "reject",
        "idempotency_conflict",
        "in_progress",
        "rate_limit",
    }:
        response_payload = {"reason_code": case.reason_code}
    else:
        response_payload = dict(RESULT_FIXTURES[case.operation_identity])
    if case.case_id == "status_query":
        response_payload = {
            "state": "PROCESSING",
            "provider_reference": response_payload["provider_reference"],
            "reason_code": None,
            "updated_at": "2026-07-30T00:00:00+00:00",
            "source_version": 2,
            "result_payload": None,
        }
    elif case.case_id == "partial_failure":
        response_payload["task_outcome"] = "PARTIAL_FAILURE"
        operation = WMS_OPERATION_BY_IDENTITY[case.operation_identity]
        try:
            operation.result_model.model_validate(response_payload)
        except ValidationError:
            response_payload["task_outcome"] = "FAILED_AFTER_EXECUTION"
        response_payload = {
            "state": "COMPLETED",
            "provider_reference": response_payload["provider_reference"],
            "reason_code": None,
            "updated_at": "2026-07-30T00:00:00+00:00",
            "source_version": int(response_payload["source_version"]),
            "result_payload": response_payload,
        }
    return RealTcpScenario(
        operation_identity=case.operation_identity,
        case_id=case.case_id,
        wire_kind=resolved_wire_kind,
        request_payload=REQUEST_FIXTURES[case.operation_identity],
        idempotency_key=resolved_idempotency_key,
        expected_transport="RESPONSE",
        expected_http_status=status,
        expected_response_payload=response_payload,
        observation=observation,
    )


def _complete_asset() -> RealTcpScenarioAsset:
    scenarios = tuple(_response_scenario(case) for case in WMS_PROVIDER_CONFORMANCE_CASES)
    return RealTcpScenarioAsset(
        schema_version="wms-real-tcp-conformance-scenarios.v1",
        scenarios=scenarios,
        digest=scenario_asset_digest(scenarios),
    )


@pytest.mark.asyncio
async def test_generic_runner_executes_mode_family_cases_and_rejects_fixture_drift() -> None:
    fixture = WMS_OPERATION_FIXTURE_MATRIX[0]
    cases = _cases(fixture.operation.identity)
    runner = GenericConformanceRunner(WMS_OPERATION_FIXTURE_MATRIX)

    observations = tuple([await runner.execute(case) for case in cases])
    assert tuple(item.model_dump(mode="json") for item in observations) == tuple(
        item.model_dump(mode="json") for item in cases
    )

    reject_case = next(case for case in cases if case.outcome_kind == "BUSINESS_REJECT")
    wrong_reject = replace(
        fixture,
        reject=RejectFixture(operation_identity=fixture.operation.identity, reason_code="WRONG_REASON"),
    )
    with pytest.raises(ValueError, match="business reject"):
        await GenericConformanceRunner((wrong_reject,)).execute(reject_case)

    with pytest.raises(ValueError, match="mode family"):
        await runner.execute(cases[0].model_copy(update={"case_id": "not_in_family"}))
    conflict = next(case for case in WMS_PROVIDER_CONFORMANCE_CASES if case.case_id == "idempotency_conflict")
    with pytest.raises(ValueError, match="idempotency conflict"):
        await runner.execute(conflict.model_copy(update={"retryable": True}))
    in_progress = next(case for case in WMS_PROVIDER_CONFORMANCE_CASES if case.case_id == "in_progress")
    with pytest.raises(ValueError, match="in-progress"):
        await runner.execute(in_progress.model_copy(update={"retryable": False}))


def test_fixture_matrix_rejects_registry_and_fixture_semantic_drift() -> None:
    fixture = WMS_OPERATION_FIXTURE_MATRIX[0]
    duplicate_operation = (fixture.operation, fixture.operation)
    pairs = ((fixture.operation.identity, REQUEST_FIXTURES[fixture.operation.identity]),) * 2
    with pytest.raises(ValueError, match="duplicate operation"):
        build_operation_fixture_matrix(
            operations=duplicate_operation,
            request_fixtures=pairs,
            result_fixtures=(),
            reject_fixtures=(),
            identity_mismatch_fixtures=(),
        )

    invalid_rejects = dict(REJECT_FIXTURES)
    invalid_rejects[fixture.operation.identity] = {
        "operation_identity": fixture.operation.identity,
        "reason_code": "WRONG_REASON",
    }
    with pytest.raises(ValueError, match="reject fixture"):
        build_operation_fixture_matrix(
            operations=WMS_OPERATIONS,
            request_fixtures=tuple(REQUEST_FIXTURES.items()),
            result_fixtures=tuple(RESULT_FIXTURES.items()),
            reject_fixtures=tuple(invalid_rejects.items()),
            identity_mismatch_fixtures=tuple(IDENTITY_MISMATCH_FIXTURES.items()),
        )

    invalid_mismatches = dict(IDENTITY_MISMATCH_FIXTURES)
    identity = fixture.operation.identity
    invalid_mismatches[identity] = {
        "expected_operation_identity": identity,
        "actual_operation_identity": identity,
    }
    with pytest.raises(ValueError, match="is not mismatched"):
        build_operation_fixture_matrix(
            operations=WMS_OPERATIONS,
            request_fixtures=tuple(REQUEST_FIXTURES.items()),
            result_fixtures=tuple(RESULT_FIXTURES.items()),
            reject_fixtures=tuple(REJECT_FIXTURES.items()),
            identity_mismatch_fixtures=tuple(invalid_mismatches.items()),
        )


def test_scenario_asset_is_complete_ordered_deterministic_and_fail_closed() -> None:
    asset = _complete_asset()

    assert (
        build_real_tcp_scenario_matrix(
            cases=WMS_PROVIDER_CONFORMANCE_CASES,
            scenarios=asset.scenarios,
        )
        == asset.scenarios
    )
    for invalid in (asset.scenarios[:-1], (*asset.scenarios, asset.scenarios[0])):
        with pytest.raises(ValueError, match="exactly match"):
            build_real_tcp_scenario_matrix(
                cases=WMS_PROVIDER_CONFORMANCE_CASES,
                scenarios=invalid,
            )
    wrong_verdict = asset.scenarios[0].model_copy(
        update={"observation": asset.scenarios[0].observation.model_copy(update={"semantic_marker": "WRONG"})}
    )
    with pytest.raises(ValueError, match="verdicts"):
        build_real_tcp_scenario_matrix(
            cases=WMS_PROVIDER_CONFORMANCE_CASES,
            scenarios=(wrong_verdict, *asset.scenarios[1:]),
        )

    with pytest.raises(ValidationError, match="digest"):
        RealTcpScenarioAsset.model_validate({**asset.model_dump(mode="json"), "digest": "f" * 64})


@pytest.mark.parametrize(
    "changes",
    (
        {"operation_identity": "wrong"},
        {"wire_kind": "STATUS_QUERY", "idempotency_key": None},
        {"expected_http_status": None},
        {"expected_transport": "TIMEOUT", "expected_http_status": 200},
        {
            "expected_transport": "TIMEOUT",
            "expected_http_status": None,
            "expected_response_payload": None,
        },
    ),
)
def test_scenario_contract_rejects_incomplete_or_inconsistent_input(changes) -> None:
    case = WMS_PROVIDER_CONFORMANCE_CASES[0]
    payload = _response_scenario(case).model_dump(mode="python")
    payload.update(changes)

    with pytest.raises(ValidationError):
        RealTcpScenario.model_validate(payload)


def test_scenario_transport_kind_must_match_the_frozen_technical_reason() -> None:
    timeout_case = next(case for case in WMS_PROVIDER_CONFORMANCE_CASES if case.case_id == "timeout")
    timeout_payload = _response_scenario(timeout_case).model_dump(mode="python")
    timeout_payload.update(
        {
            "expected_transport": "RESPONSE",
            "expected_http_status": 504,
            "expected_response_payload": {"reason_code": "WMS_PROVIDER_TIMEOUT"},
        }
    )
    with pytest.raises(ValidationError, match="frozen timeout"):
        RealTcpScenario.model_validate(timeout_payload)

    rate_limit_case = next(case for case in WMS_PROVIDER_CONFORMANCE_CASES if case.case_id == "rate_limit")
    with pytest.raises(ValidationError, match="frozen unavailable"):
        RealTcpScenario(
            operation_identity=rate_limit_case.operation_identity,
            case_id=rate_limit_case.case_id,
            request_payload=REQUEST_FIXTURES[rate_limit_case.operation_identity],
            expected_transport="TRANSPORT_ERROR",
            observation=OperationConformanceObservation.model_validate(rate_limit_case.model_dump(mode="json")),
        )


@pytest.mark.asyncio
async def test_real_tcp_runner_uses_production_get_post_wire_without_control_headers() -> None:
    compiled_profile = build_compiled_provider_profile()
    get_operation = next(operation for operation in WMS_OPERATIONS if operation.http_method.value == "GET")
    post_operation = next(operation for operation in WMS_OPERATIONS if operation.http_method.value == "POST")
    async_operation = next(operation for operation in WMS_OPERATIONS if operation.supports_status_query)
    accepted = next(case for case in _cases(async_operation.identity) if case.case_id == "accepted")
    cases = (_cases(get_operation.identity)[0], _cases(post_operation.identity)[0], accepted)
    scenarios = tuple(_response_scenario(case) for case in cases)
    seen_methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert "X-WES-Conformance-Case" not in request.headers
        assert "X-WES-Operation-Identity" not in request.headers
        assert "X-WMS-Operation-Identity" not in request.headers
        assert "X-WMS-Signature" not in request.headers
        seen_methods.append(request.method)
        scenario = scenarios[len(seen_methods) - 1]
        return httpx.Response(scenario.expected_http_status, json=scenario.expected_response_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runner = RealTcpConformanceRunner(
            scenarios,
            compiled_profile=compiled_profile,
            client=client,
        )
        observations = tuple([await runner.execute(case) for case in cases])

    assert observations == tuple(scenario.observation for scenario in scenarios)
    assert seen_methods == ["GET", "POST", "POST"]


@pytest.mark.asyncio
async def test_real_tcp_runner_uses_reviewed_status_query_wire() -> None:
    compiled_profile = build_compiled_provider_profile()
    operation = next(operation for operation in WMS_OPERATIONS if operation.supports_status_query)
    case = next(case for case in _cases(operation.identity) if case.case_id == "status_query")
    scenario = _response_scenario(case, wire_kind="STATUS_QUERY", idempotency_key="idem-001")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert dict(request.url.params) == {
            "operation_identity": operation.identity,
            "idempotency_key": "idem-001",
        }
        return httpx.Response(200, json=scenario.expected_response_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await RealTcpConformanceRunner(
            (scenario,),
            compiled_profile=compiled_profile,
            client=client,
        ).execute(case)

    assert observation == scenario.observation


@pytest.mark.parametrize("case_id", ("status_query", "partial_failure"))
def test_async_status_and_partial_cases_reject_submit_wire(case_id: str) -> None:
    case = next(case for case in WMS_PROVIDER_CONFORMANCE_CASES if case.case_id == case_id)
    payload = _response_scenario(case).model_dump(mode="python")
    payload["wire_kind"] = "OPERATION"

    with pytest.raises(ValidationError, match="production STATUS_QUERY"):
        RealTcpScenario.model_validate(payload)


@pytest.mark.asyncio
async def test_status_query_rejects_submit_terminal_payload_without_formal_envelope() -> None:
    compiled_profile = build_compiled_provider_profile()
    operation = next(operation for operation in WMS_OPERATIONS if operation.supports_status_query)
    case = next(case for case in _cases(operation.identity) if case.case_id == "status_query")
    scenario = _response_scenario(case)
    malformed = scenario.model_copy(update={"expected_response_payload": RESULT_FIXTURES[operation.identity]})

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=malformed.expected_response_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValidationError):
            await RealTcpConformanceRunner(
                (malformed,),
                compiled_profile=compiled_profile,
                client=client,
            ).execute(case)


@pytest.mark.asyncio
async def test_partial_failure_requires_typed_completed_status_terminal() -> None:
    compiled_profile = build_compiled_provider_profile()
    operation = next(operation for operation in WMS_OPERATIONS if operation.supports_status_query)
    case = next(case for case in _cases(operation.identity) if case.case_id == "partial_failure")
    scenario = _response_scenario(case)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=scenario.expected_response_payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await RealTcpConformanceRunner(
            (scenario,),
            compiled_profile=compiled_profile,
            client=client,
        ).execute(case)

    assert observation == scenario.observation

    non_terminal_payload = {
        **scenario.expected_response_payload,
        "state": "PROCESSING",
    }
    non_terminal = scenario.model_copy(update={"expected_response_payload": non_terminal_payload})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=non_terminal_payload))
    ) as client:
        with pytest.raises(ValueError, match="only COMPLETED"):
            await RealTcpConformanceRunner(
                (non_terminal,),
                compiled_profile=compiled_profile,
                client=client,
            ).execute(case)

    successful_terminal_payload = {
        **scenario.expected_response_payload,
        "result_payload": RESULT_FIXTURES[operation.identity],
    }
    successful_terminal = scenario.model_copy(update={"expected_response_payload": successful_terminal_payload})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=successful_terminal_payload))
    ) as client:
        with pytest.raises(ValueError, match="typed terminal status snapshot"):
            await RealTcpConformanceRunner(
                (successful_terminal,),
                compiled_profile=compiled_profile,
                client=client,
            ).execute(case)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expected_transport", "error_type"),
    (("TIMEOUT", httpx.ReadTimeout), ("TRANSPORT_ERROR", httpx.ConnectError)),
)
async def test_real_tcp_runner_observes_approved_transport_failure(expected_transport, error_type) -> None:
    compiled_profile = build_compiled_provider_profile()
    expected_case_id = "timeout" if expected_transport == "TIMEOUT" else "unavailable"
    case = next(case for case in WMS_PROVIDER_CONFORMANCE_CASES if case.case_id == expected_case_id)
    scenario = RealTcpScenario(
        operation_identity=case.operation_identity,
        case_id=case.case_id,
        request_payload=REQUEST_FIXTURES[case.operation_identity],
        expected_transport=expected_transport,
        observation=OperationConformanceObservation.model_validate(case.model_dump(mode="json")),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("planned", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await RealTcpConformanceRunner(
            (scenario,),
            compiled_profile=compiled_profile,
            client=client,
        ).execute(case)

    assert observation == scenario.observation


@pytest.mark.asyncio
async def test_real_tcp_runner_reuses_existing_hmac_signing_primitive(monkeypatch) -> None:
    from tests.support import wms_conformance_runner

    case = WMS_PROVIDER_CONFORMANCE_CASES[0]
    scenario = _response_scenario(case)
    compiled_profile = build_compiled_provider_profile(build_hmac_provider_profile_payload())
    real_sign = wms_conformance_runner.sign_wms_hmac_request
    signing_calls: list[str] = []

    def recording_sign(request, **kwargs):
        signing_calls.append(kwargs["credential_reference"])
        return real_sign(request, **kwargs)

    class CredentialProvider:
        def resolve(self, _credential_reference: str) -> bytes:
            return b"test-only-secret"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-WMS-Signature-Algorithm"] == "HMAC_SHA256"
        assert request.headers["X-WMS-Nonce"] == "fixed-nonce"
        assert len(request.headers["X-WMS-Signature"]) == 64
        return httpx.Response(200, json=scenario.expected_response_payload)

    monkeypatch.setattr(wms_conformance_runner, "sign_wms_hmac_request", recording_sign)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await RealTcpConformanceRunner(
            (scenario,),
            compiled_profile=compiled_profile,
            client=client,
            credential_provider=CredentialProvider(),
            now=lambda: datetime(2026, 7, 30, tzinfo=UTC),
            nonce_factory=lambda: "fixed-nonce",
        ).execute(case)

    assert observation == scenario.observation
    assert signing_calls == [compiled_profile.profile.outbound_auth.credential_reference]


@pytest.mark.asyncio
async def test_real_tcp_runner_rejects_unapproved_response_and_missing_hmac_resolver() -> None:
    case = WMS_PROVIDER_CONFORMANCE_CASES[0]
    scenario = _response_scenario(case)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500, json={}))
    ) as client:
        hmac_runner = RealTcpConformanceRunner(
            (scenario,),
            compiled_profile=build_compiled_provider_profile(build_hmac_provider_profile_payload()),
            client=client,
        )
        with pytest.raises(ValueError, match="credential resolver"):
            await hmac_runner.execute(case)
        runner = RealTcpConformanceRunner(
            (scenario,),
            compiled_profile=build_compiled_provider_profile(),
            client=client,
        )
        with pytest.raises(ValueError, match="approved scenario"):
            await runner.execute(case)


@pytest.mark.asyncio
async def test_real_tcp_runner_rejects_invalid_status_family_and_unexpected_transport_outcome() -> None:
    compiled_profile = build_compiled_provider_profile()
    case = WMS_PROVIDER_CONFORMANCE_CASES[0]
    invalid_status = _response_scenario(case, wire_kind="STATUS_QUERY", idempotency_key="idem-001")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={}))
    ) as client:
        with pytest.raises(ValueError, match="async EFFECT"):
            await RealTcpConformanceRunner(
                (invalid_status,),
                compiled_profile=compiled_profile,
                client=client,
            ).execute(case)

    timeout_case = next(item for item in WMS_PROVIDER_CONFORMANCE_CASES if item.case_id == "timeout")
    expected_timeout = RealTcpScenario(
        operation_identity=timeout_case.operation_identity,
        case_id=timeout_case.case_id,
        request_payload=REQUEST_FIXTURES[timeout_case.operation_identity],
        expected_transport="TIMEOUT",
        observation=OperationConformanceObservation.model_validate(timeout_case.model_dump(mode="json")),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=RESULT_FIXTURES[case.operation_identity])
        )
    ) as client:
        with pytest.raises(ValueError, match="expected a transport failure"):
            await RealTcpConformanceRunner(
                (expected_timeout,),
                compiled_profile=compiled_profile,
                client=client,
            ).execute(timeout_case)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    (httpx.ReadTimeout, httpx.ConnectError, TimeoutError),
)
async def test_real_tcp_runner_propagates_unapproved_transport_failure(error_type) -> None:
    compiled_profile = build_compiled_provider_profile()
    case = WMS_PROVIDER_CONFORMANCE_CASES[0]
    scenario = _response_scenario(case)

    async def handler(request: httpx.Request) -> httpx.Response:
        if issubclass(error_type, httpx.HTTPError):
            raise error_type("unexpected", request=request)
        raise error_type("unexpected")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(error_type):
            await RealTcpConformanceRunner(
                (scenario,),
                compiled_profile=compiled_profile,
                client=client,
            ).execute(case)


@pytest.mark.asyncio
async def test_real_tcp_runner_accepts_builtin_timeout_when_approved() -> None:
    compiled_profile = build_compiled_provider_profile()
    case = next(case for case in WMS_PROVIDER_CONFORMANCE_CASES if case.case_id == "timeout")
    scenario = RealTcpScenario(
        operation_identity=case.operation_identity,
        case_id=case.case_id,
        request_payload=REQUEST_FIXTURES[case.operation_identity],
        expected_transport="TIMEOUT",
        observation=OperationConformanceObservation.model_validate(case.model_dump(mode="json")),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        raise TimeoutError("planned")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        observation = await RealTcpConformanceRunner(
            (scenario,),
            compiled_profile=compiled_profile,
            client=client,
        ).execute(case)

    assert observation == scenario.observation


def test_cli_parser_requires_explicit_scenario_and_release_metadata() -> None:
    args = wms_conformance_cli.build_parser().parse_args(
        [
            "--profile",
            "provider.json",
            "--scenario-asset",
            "scenario.json",
            "--wms-build-version",
            "wms-build-1",
            "--responsible-person",
            "WMS-OWNER-001",
            "--confirm-execution-safety",
        ]
    )

    assert args.profile == Path("provider.json")
    assert args.scenario_asset == Path("scenario.json")
    assert args.confirm_execution_safety is True


@pytest.mark.asyncio
async def test_cli_fails_closed_without_safety_or_real_scenario_asset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="safety confirmation"):
        await wms_conformance_cli.run(
            argparse.Namespace(
                profile=Path("unused.json"),
                scenario_asset=Path("unused-scenario.json"),
                wms_build_version="wms-build-1",
                responsible_person="WMS-OWNER-001",
                confirm_execution_safety=False,
            )
        )
    profile_path = tmp_path / "provider.json"
    profile_path.write_text(json.dumps(build_provider_profile_payload()), encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        await wms_conformance_cli.run(
            argparse.Namespace(
                profile=profile_path,
                scenario_asset=tmp_path / "missing-scenario.json",
                wms_build_version="wms-build-1",
                responsible_person="WMS-OWNER-001",
                confirm_execution_safety=True,
            )
        )


@pytest.mark.asyncio
async def test_cli_builds_release_report_from_explicit_asset(tmp_path: Path, monkeypatch) -> None:
    profile_path = tmp_path / "provider.json"
    profile_path.write_text(json.dumps(build_provider_profile_payload()), encoding="utf-8")
    asset = _complete_asset()
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(asset.model_dump_json(), encoding="utf-8")

    async def execute(_self, case):
        return case

    monkeypatch.setattr(RealTcpConformanceRunner, "execute", execute)
    payload = await wms_conformance_cli.run(
        argparse.Namespace(
            profile=profile_path,
            scenario_asset=scenario_path,
            wms_build_version="wms-build-1",
            responsible_person="WMS-OWNER-001",
            confirm_execution_safety=True,
        )
    )

    assert json.loads(payload)["provenance"] == "REAL_TCP"


def test_cli_main_and_module_entrypoint_print_runner_result(monkeypatch, capsys) -> None:
    async def result(_args):
        return '{"passed": true}'

    monkeypatch.setattr(wms_conformance_cli, "run", result)
    monkeypatch.setattr(
        "sys.argv",
        [
            "wms-conformance",
            "--profile",
            "provider.json",
            "--scenario-asset",
            "scenario.json",
            "--wms-build-version",
            "wms-build-1",
            "--responsible-person",
            "WMS-OWNER-001",
            "--confirm-execution-safety",
        ],
    )
    wms_conformance_cli.main()
    assert capsys.readouterr().out.strip() == '{"passed": true}'

    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda _self: argparse.Namespace(
            profile=Path("provider.json"),
            scenario_asset=Path("scenario.json"),
            wms_build_version="wms-build-1",
            responsible_person="WMS-OWNER-001",
            confirm_execution_safety=True,
        ),
    )

    def close_coroutine(coroutine):
        coroutine.close()
        return '{"passed": true}'

    monkeypatch.setattr("asyncio.run", close_coroutine)
    runpy.run_path(wms_conformance_cli.__file__, run_name="__main__")
    assert capsys.readouterr().out.strip() == '{"passed": true}'


def test_scenario_response_must_match_frozen_status_reason_and_partial_marker() -> None:
    success = WMS_PROVIDER_CONFORMANCE_CASES[0]
    with pytest.raises(ValidationError, match="status"):
        RealTcpScenario.model_validate(
            {**_response_scenario(success).model_dump(mode="python"), "expected_http_status": 201}
        )

    business_reject = next(case for case in WMS_PROVIDER_CONFORMANCE_CASES if case.case_id == "business_reject")
    with pytest.raises(ValidationError, match="reason"):
        RealTcpScenario.model_validate(
            {
                **_response_scenario(business_reject).model_dump(mode="python"),
                "expected_response_payload": {"reason_code": "WRONG"},
            }
        )

    partial_failure = next(case for case in WMS_PROVIDER_CONFORMANCE_CASES if case.case_id == "partial_failure")
    with pytest.raises(ValidationError, match="terminal marker"):
        RealTcpScenario.model_validate(
            {
                **_response_scenario(partial_failure).model_dump(mode="python"),
                "expected_response_payload": {"task_outcome": "SUCCESS"},
            }
        )


def test_real_tcp_runner_source_has_no_test_control_headers() -> None:
    source = Path("tests/support/wms_conformance_runner.py").read_text(encoding="utf-8")

    assert "X-WES-Conformance-Case" not in source
    assert "X-WES-Operation-Identity" not in source
    assert "X-WMS-Operation-Identity" not in source
