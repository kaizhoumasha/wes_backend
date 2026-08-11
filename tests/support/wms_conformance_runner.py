"""T8 test-only 通用 conformance runner 与真实 TCP scenario 合同。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.app.runtime.system_capabilities.wms.provider_conformance import (
    OperationConformanceExpectation,
    OperationConformanceObservation,
)
from src.app.wms_integration.ports.effect_status import (
    WmsEffectStatus,
    WmsEffectStatusRequest,
    parse_wms_effect_status_snapshot,
)
from src.app.wms_integration.provider_manifest import conformance_cases_for_operation
from src.app.wms_integration.services.http_transport import (
    send_bounded_wms_request,
    sign_wms_hmac_request,
)
from tests.mock.wms_fixture_matrix import (
    IdentityMismatchFixture,
    OperationFixture,
    RejectFixture,
    build_operation_fixture_matrix,
)

if TYPE_CHECKING:
    from src.app.sys.external_http_credentials import VersionedCredentialProvider
    from src.app.wms_integration.endpoint_compiler import CompiledWmsProviderProfile


class GenericConformanceRunner:
    """共享 mode-family 题库 runner；31 项 operation 不生成专用 runner。"""

    def __init__(self, fixtures: tuple[OperationFixture, ...]) -> None:
        self._fixtures = {item.operation.identity: item for item in fixtures}

    async def execute(self, case: OperationConformanceExpectation) -> OperationConformanceObservation:
        fixture = self._fixtures[case.operation_identity]
        if case.case_id not in conformance_cases_for_operation(fixture.operation):
            raise ValueError("conformance case does not belong to the operation mode family")
        fixture.operation.request_model.model_validate(fixture.request)
        if case.outcome_kind == "SUCCESS":
            fixture.operation.result_model.model_validate(fixture.result)
        elif case.case_id in {"business_reject", "reject"} and fixture.reject.reason_code != case.reason_code:
            raise ValueError("business reject fixture does not match operation contract")
        if case.case_id == "idempotency_conflict" and (
            case.reason_code != "IDEMPOTENCY_CONFLICT" or case.retryable is not False
        ):
            raise ValueError("idempotency conflict verdict differs from the frozen contract")
        if case.case_id == "in_progress" and (
            case.reason_code != "IDEMPOTENCY_REQUEST_IN_PROGRESS" or case.retryable is not True
        ):
            raise ValueError("in-progress verdict differs from the frozen contract")
        return OperationConformanceObservation.model_validate(case.model_dump(mode="json"))


class RealTcpScenario(BaseModel):
    """WMS 方提供的单 case 脱敏预置场景；只描述生产 wire 输入与期望。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_identity: str
    case_id: str
    wire_kind: Literal["OPERATION", "STATUS_QUERY"] = "OPERATION"
    request_payload: dict[str, Any]
    idempotency_key: str | None = None
    expected_transport: Literal["RESPONSE", "TIMEOUT", "TRANSPORT_ERROR"] = "RESPONSE"
    expected_http_status: int | None = Field(default=None, ge=100, le=599)
    expected_response_payload: dict[str, Any] | None = None
    observation: OperationConformanceObservation

    @model_validator(mode="after")
    def validate_scenario(self) -> RealTcpScenario:
        if self.operation_identity != self.observation.operation_identity or self.case_id != self.observation.case_id:
            raise ValueError("REAL_TCP scenario observation identity mismatch")
        if self.wire_kind == "STATUS_QUERY" and self.idempotency_key is None:
            raise ValueError("status query scenario requires idempotency_key")
        if self.case_id in {"status_query", "partial_failure"} and self.wire_kind != "STATUS_QUERY":
            raise ValueError("async status/partial cases require the production STATUS_QUERY wire")
        if self.expected_transport == "RESPONSE":
            if self.expected_http_status is None or self.expected_response_payload is None:
                raise ValueError("response scenario requires expected status and payload")
        elif self.expected_http_status is not None or self.expected_response_payload is not None:
            raise ValueError("transport failure scenario cannot carry HTTP response expectation")
        if self.expected_transport != "RESPONSE" and self.observation.outcome_kind != "TECHNICAL_FAILURE":
            raise ValueError("transport failure scenario requires a technical-failure observation")
        if (self.expected_transport == "TIMEOUT") is not (self.observation.reason_code == "WMS_PROVIDER_TIMEOUT"):
            raise ValueError("timeout scenario must match the frozen timeout verdict")
        if self.expected_transport == "TRANSPORT_ERROR" and self.observation.reason_code != "WMS_UNAVAILABLE":
            raise ValueError("transport-error scenario must match the frozen unavailable verdict")
        if self.expected_transport == "RESPONSE":
            required_status = {
                "accepted": 202,
                "business_reject": 422,
                "reject": 422,
                "idempotency_conflict": 422,
                "in_progress": 409,
                "rate_limit": 429,
                "unavailable": 503,
            }.get(self.case_id, 200)
            if self.expected_http_status != required_status:
                raise ValueError("REAL_TCP scenario status differs from the frozen wire contract")
            protocol_reason_cases = {
                "business_reject",
                "reject",
                "idempotency_conflict",
                "in_progress",
                "rate_limit",
                "unavailable",
            }
            if self.case_id in protocol_reason_cases:
                response_reason = (self.expected_response_payload or {}).get(
                    "reason_code",
                    (self.expected_response_payload or {}).get("code"),
                )
                if response_reason != self.observation.reason_code:
                    raise ValueError("REAL_TCP scenario reason differs from the frozen verdict")
            terminal_payload = (self.expected_response_payload or {}).get("result_payload")
            if self.case_id == "partial_failure" and (
                not isinstance(terminal_payload, dict)
                or terminal_payload.get("task_outcome") not in {"PARTIAL_FAILURE", "FAILED_AFTER_EXECUTION"}
            ):
                raise ValueError("partial-failure scenario requires the frozen terminal marker")
        return self


class RealTcpScenarioAsset(BaseModel):
    """外部提供的完整 193-case scenario asset，导入时校验自身摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["wms-real-tcp-conformance-scenarios.v1"]
    scenarios: tuple[RealTcpScenario, ...]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_digest(self) -> RealTcpScenarioAsset:
        actual = scenario_asset_digest(self.scenarios)
        if not hmac.compare_digest(self.digest, actual):
            raise ValueError("REAL_TCP scenario asset digest mismatch")
        return self


def scenario_asset_digest(scenarios: tuple[RealTcpScenario, ...]) -> str:
    payload = [scenario.model_dump(mode="json") for scenario in scenarios]
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_real_tcp_scenario_matrix(
    *,
    cases: tuple[OperationConformanceExpectation, ...],
    scenarios: tuple[RealTcpScenario, ...],
) -> tuple[RealTcpScenario, ...]:
    """真实 TCP asset 必须与完整 mode-family 矩阵逐项、同序匹配。"""

    expected = tuple((case.operation_identity, case.case_id) for case in cases)
    actual = tuple((scenario.operation_identity, scenario.case_id) for scenario in scenarios)
    if len(set(actual)) != len(actual) or actual != expected:
        raise ValueError("REAL_TCP scenario identities must exactly match the conformance matrix")
    expected_observations = tuple(
        OperationConformanceObservation.model_validate(case.model_dump(mode="json")) for case in cases
    )
    if tuple(scenario.observation for scenario in scenarios) != expected_observations:
        raise ValueError("REAL_TCP scenario verdicts must exactly match the conformance matrix")
    return scenarios


class RealTcpConformanceRunner:
    """按显式 scenario 执行生产 method/path/body；不发送任何测试控制字段。"""

    def __init__(
        self,
        scenarios: tuple[RealTcpScenario, ...],
        *,
        compiled_profile: CompiledWmsProviderProfile,
        client: httpx.AsyncClient,
        credential_provider: VersionedCredentialProvider | None = None,
        now=None,
        nonce_factory=None,
    ) -> None:
        self._scenarios = {(scenario.operation_identity, scenario.case_id): scenario for scenario in scenarios}
        self._compiled_profile = compiled_profile
        self._client = client
        self._credential_provider = credential_provider
        self._now = now or (lambda: datetime.now(UTC))
        self._nonce_factory = nonce_factory or (lambda: uuid4().hex)

    async def execute(self, case: OperationConformanceExpectation) -> OperationConformanceObservation:
        scenario = self._scenarios[(case.operation_identity, case.case_id)]
        endpoint = self._compiled_profile.operations[case.operation_identity]
        request = endpoint.request_model.model_validate(scenario.request_payload)
        if scenario.wire_kind == "STATUS_QUERY":
            if endpoint.status_endpoint is None:
                raise ValueError("status query scenario requires an async EFFECT operation")
            method = "GET"
            url = endpoint.status_endpoint
            json_body = None
            query_params = {
                "operation_identity": case.operation_identity,
                "idempotency_key": scenario.idempotency_key,
            }
        else:
            method = endpoint.http_method.value
            url = endpoint.render_endpoint(request)
            json_body = request.model_dump(mode="json") if method == "POST" else None
            query_params = request.model_dump(mode="json") if method == "GET" else None
        request_message = self._client.build_request(
            method,
            url,
            json=json_body,
            params=query_params,
        )
        auth = self._compiled_profile.profile.outbound_auth

        def authenticate(message: httpx.Request) -> None:
            if auth.scheme.value == "NONE":
                return
            if self._credential_provider is None or auth.credential_reference is None:
                raise ValueError("HMAC REAL_TCP conformance requires the existing credential resolver")
            sign_wms_hmac_request(
                message,
                credential_reference=auth.credential_reference,
                auth_scheme=auth.scheme.value,
                secret=self._credential_provider.resolve(auth.credential_reference),
                now=self._now,
                nonce_factory=self._nonce_factory,
            )

        deadline = asyncio.get_running_loop().time() + endpoint.budget.deadline_seconds
        try:
            response, _ = await send_bounded_wms_request(
                client=self._client,
                request=request_message,
                authenticate=authenticate,
                deadline=deadline,
                max_chunk_bytes=endpoint.budget.max_chunk_bytes,
                max_wire_bytes=endpoint.budget.max_wire_bytes,
            )
        except httpx.TimeoutException:
            if scenario.expected_transport != "TIMEOUT":
                raise
        except TimeoutError:
            if scenario.expected_transport != "TIMEOUT":
                raise
        except httpx.TransportError:
            if scenario.expected_transport != "TRANSPORT_ERROR":
                raise
        else:
            if scenario.expected_transport != "RESPONSE":
                raise ValueError("REAL_TCP scenario expected a transport failure")
            if (
                response.status_code != scenario.expected_http_status
                or json.loads(response.body) != scenario.expected_response_payload
            ):
                raise ValueError("REAL_TCP response does not match the approved scenario asset")
            validates_result = case.case_id not in {
                "accepted",
                "idempotent_replay",
                "in_progress",
                "status_query",
            }
            if case.outcome_kind == "SUCCESS" and scenario.wire_kind == "OPERATION" and validates_result:
                endpoint.result_model.model_validate(json.loads(response.body))
            if scenario.wire_kind == "STATUS_QUERY":
                status_request = WmsEffectStatusRequest(
                    operation_identity=case.operation_identity,
                    idempotency_key=scenario.idempotency_key,
                    request_payload=scenario.request_payload,
                )
                snapshot = parse_wms_effect_status_snapshot(
                    request=status_request,
                    raw_response=json.loads(response.body),
                    max_result_payload_bytes=endpoint.budget.max_decoded_bytes,
                )
                if case.case_id == "partial_failure" and (
                    snapshot.state is not WmsEffectStatus.COMPLETED
                    or snapshot.result is None
                    or getattr(snapshot.result, "task_outcome", None)
                    not in {"PARTIAL_FAILURE", "FAILED_AFTER_EXECUTION"}
                ):
                    raise ValueError("partial-failure scenario requires a typed terminal status snapshot")
        return scenario.observation


__all__ = [
    "GenericConformanceRunner",
    "IdentityMismatchFixture",
    "OperationFixture",
    "RealTcpConformanceRunner",
    "RealTcpScenario",
    "RealTcpScenarioAsset",
    "RejectFixture",
    "build_operation_fixture_matrix",
    "build_real_tcp_scenario_matrix",
    "scenario_asset_digest",
]
