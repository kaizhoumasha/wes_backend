"""WMS Provider conformance 的 test-only fixture 与三个执行工厂。"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Annotated

import httpx
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from src.app.runtime.system_capabilities.wms.inventory.query_inventory.contract import CONTRACT
from src.app.runtime.system_capabilities.wms.provider_catalog import resolve_wms_operation_binding
from src.app.runtime.system_capabilities.wms.provider_conformance import (
    QUERY_INVENTORY_CONFORMANCE_CASES,
    ConformanceObservation,
    ConformanceOutcomeKind,
    ConformanceTarget,
)
from src.app.wms_integration.adapters.query_inventory_operation_adapter import InventoryQueryOperationAdapter
from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryAuthorityItem,
    InventoryQueryOperationRequest,
    InventoryQueryOperationResult,
)
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)
from src.app.wms_integration.services.query_transport import (
    WmsBoundQueryEndpoint,
    WmsQueryCallPermit,
    WmsQueryTransportExecutor,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.app.wms_integration.ports.query_outcome import WmsQueryOutcome

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class QueryConformanceScenario(str, Enum):
    """固定命名故障点；不是可扩展规则 DSL。"""

    SUCCESS = "success"
    EMPTY = "empty"
    MISSING_FIELD = "missing_field"
    INVALID_DECIMAL = "invalid_decimal"
    REJECT = "reject"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    PAGINATION = "pagination"
    PRECISION = "precision"
    BUDGET = "budget"
    EVIDENCE_FAILURE = "evidence_failure"


class ScriptedQueryCase(BaseModel):
    """单道不可变脚本题；schema 不允许 endpoint/credential/header。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    scenario: QueryConformanceScenario
    recorded_observation: ConformanceObservation

    @model_validator(mode="after")
    def require_scenario_identity(self) -> ScriptedQueryCase:
        if self.case_id != self.scenario.value or self.case_id != self.recorded_observation.case_id:
            raise ValueError("scripted case identity mismatch")
        return self


class QueryInventoryScriptFixture(BaseModel):
    """带内容摘要的冻结 fixture 集。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: tuple[ScriptedQueryCase, ...]
    digest: Sha256Digest

    @model_validator(mode="after")
    def verify_digest_on_load(self) -> QueryInventoryScriptFixture:
        if not self.verify():
            raise ValueError("query inventory conformance fixture digest mismatch")
        return self

    def verify(self) -> bool:
        return hmac.compare_digest(self.digest, _fixture_digest(self.cases))


def _observation_from_expectation(case) -> ConformanceObservation:
    return ConformanceObservation.model_validate(case.model_dump(mode="json"))


def _fixture_digest(cases: tuple[ScriptedQueryCase, ...]) -> str:
    canonical = json.dumps(
        [case.model_dump(mode="json") for case in cases],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_SCRIPTED_CASES = tuple(
    ScriptedQueryCase(
        case_id=case.case_id,
        scenario=QueryConformanceScenario(case.case_id),
        recorded_observation=_observation_from_expectation(case),
    )
    for case in QUERY_INVENTORY_CONFORMANCE_CASES
)
QUERY_INVENTORY_SCRIPT_FIXTURE = QueryInventoryScriptFixture(
    cases=_SCRIPTED_CASES,
    digest=_fixture_digest(_SCRIPTED_CASES),
)


class _RecordingEvidenceWriter:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def before_call(self, *, operation_identity: str, target_code: str) -> WmsQueryCallPermit:
        return WmsQueryCallPermit(allowed=True)

    async def record(
        self,
        *,
        operation_identity: str,
        target_code: str,
        request_snapshot: Mapping[str, object],
        outcome: object,
        permit: WmsQueryCallPermit,
    ) -> str:
        if self._fail:
            raise RuntimeError("scripted evidence failure")
        return f"evidence:{operation_identity}:conformance"


class _TestCredentialProvider:
    def resolve(self, _credential_reference: str) -> bytes:
        return b"test-only-conformance-key"


class QueryInventoryAdapterFactory:
    """真实 adapter + T3 executor 对 canonical scripted HTTP response 执行。"""

    name = "real-adapter"
    target = ConformanceTarget.CI_ADAPTER

    def __init__(self) -> None:
        self._executed_case_ids: list[str] = []

    @property
    def executed_case_ids(self) -> tuple[str, ...]:
        return tuple(self._executed_case_ids)

    async def execute(self, case: ScriptedQueryCase) -> ConformanceObservation:
        from tests.mock.wms_scripted_provider import scripted_query_inventory_response

        self._executed_case_ids.append(case.case_id)
        return await _execute_adapter_case(
            case,
            handler=lambda request: scripted_query_inventory_response(case, request),
        )


class QueryInventorySimulatorFactory:
    """真实 adapter 连接进程内最小 simulator；仍复用唯一 T3 transport 生命周期。"""

    name = "in-process-simulator"
    target = ConformanceTarget.SIMULATOR

    def __init__(self) -> None:
        self._executed_case_ids: list[str] = []

    @property
    def executed_case_ids(self) -> tuple[str, ...]:
        return tuple(self._executed_case_ids)

    async def execute(self, case: ScriptedQueryCase) -> ConformanceObservation:
        from tests.mock.wms_scripted_provider import ScriptedWmsQueryInventoryProvider

        self._executed_case_ids.append(case.case_id)
        provider = ScriptedWmsQueryInventoryProvider(case)
        return await _execute_adapter_case(case, handler=provider.handle)


class QueryInventoryReplayFactory:
    """仅从冻结 replay record 重建 T3 outcome；没有网络或密钥依赖。"""

    name = "canonical-replay"
    target = ConformanceTarget.REPLAY

    def __init__(self) -> None:
        self._executed_case_ids: list[str] = []

    @property
    def executed_case_ids(self) -> tuple[str, ...]:
        return tuple(self._executed_case_ids)

    async def execute(self, case: ScriptedQueryCase) -> ConformanceObservation:
        self._executed_case_ids.append(case.case_id)
        return _observe_query_outcome(_recorded_outcome(case), case_id=case.case_id)


def build_query_inventory_conformance_targets():
    """返回固定三个执行面；Provider 不可删题或替换题库。"""

    return (
        QueryInventoryAdapterFactory(),
        QueryInventorySimulatorFactory(),
        QueryInventoryReplayFactory(),
    )


async def _execute_adapter_case(case: ScriptedQueryCase, *, handler) -> ConformanceObservation:
    contract = CONTRACT
    if case.scenario is QueryConformanceScenario.BUDGET:
        contract = contract.model_copy(
            update={
                "budget": contract.budget.model_copy(
                    update={"max_wire_bytes": 64, "max_decoded_bytes": 128, "max_chunk_bytes": 64}
                )
            }
        )
    binding = resolve_wms_operation_binding(
        profile_identity="wms.2026-07-06.material-flow.sandbox",
        operation_identity=CONTRACT.identity,
    ).model_copy(update={"operation": contract})
    executor = WmsQueryTransportExecutor(
        endpoint=WmsBoundQueryEndpoint(binding=binding, base_url="https://conformance.invalid"),
        transport=httpx.MockTransport(handler),
        evidence_writer=_RecordingEvidenceWriter(fail=case.scenario is QueryConformanceScenario.EVIDENCE_FAILURE),
        credential_provider=_TestCredentialProvider(),
    )
    outcome = await InventoryQueryOperationAdapter(executor=executor).execute(
        InventoryQueryOperationRequest(material_code="MAT-001")
    )
    return _observe_query_outcome(outcome, case_id=case.case_id)


def _recorded_outcome(case: ScriptedQueryCase) -> WmsQueryOutcome[InventoryQueryOperationResult]:
    observation = case.recorded_observation
    evidence_key = "replay:evidence" if observation.evidence_recorded else None
    if observation.outcome_kind is ConformanceOutcomeKind.SUCCESS:
        return QuerySuccess(value=_recorded_success(case), evidence_key=evidence_key)
    if observation.outcome_kind is ConformanceOutcomeKind.BUSINESS_REJECT:
        return QueryBusinessReject(
            reason_code=observation.reason_code or "",
            message="recorded business reject",
            evidence_key=evidence_key,
        )
    if observation.outcome_kind is ConformanceOutcomeKind.TECHNICAL_FAILURE:
        return QueryTechnicalFailure(
            reason_code=observation.reason_code or "",
            message="recorded technical failure",
            retryable=bool(observation.retryable),
            retry_after_seconds=observation.retry_after_seconds,
            evidence_key=evidence_key,
        )
    return QueryContractFailure(
        reason_code=observation.reason_code or "",
        message="recorded contract failure",
        evidence_key=evidence_key,
    )


def _recorded_success(case: ScriptedQueryCase) -> InventoryQueryOperationResult:
    if case.scenario is QueryConformanceScenario.EMPTY:
        return InventoryQueryOperationResult(items=())
    if case.scenario is QueryConformanceScenario.PAGINATION:
        return InventoryQueryOperationResult(
            items=(
                InventoryAuthorityItem(material_code="MAT-001", available_quantity=Decimal("1")),
                InventoryAuthorityItem(material_code="MAT-002", available_quantity=Decimal("2")),
            )
        )
    quantity = (
        Decimal("9007199254740993.125") if case.scenario is QueryConformanceScenario.PRECISION else Decimal("7.25")
    )
    return InventoryQueryOperationResult(
        items=(InventoryAuthorityItem(material_code="MAT-001", available_quantity=quantity),)
    )


def _observe_query_outcome(
    outcome: WmsQueryOutcome[InventoryQueryOperationResult],
    *,
    case_id: str,
) -> ConformanceObservation:
    if isinstance(outcome, QuerySuccess):
        marker = _success_marker(outcome.value)
        return ConformanceObservation(
            case_id=case_id,
            outcome_kind=ConformanceOutcomeKind.SUCCESS,
            evidence_recorded=outcome.evidence_key is not None,
            semantic_marker=marker,
        )
    if isinstance(outcome, QueryBusinessReject):
        return ConformanceObservation(
            case_id=case_id,
            outcome_kind=ConformanceOutcomeKind.BUSINESS_REJECT,
            reason_code=outcome.reason_code,
            evidence_recorded=outcome.evidence_key is not None,
            semantic_marker="BUSINESS_REJECT",
        )
    if isinstance(outcome, QueryTechnicalFailure):
        return ConformanceObservation(
            case_id=case_id,
            outcome_kind=ConformanceOutcomeKind.TECHNICAL_FAILURE,
            reason_code=outcome.reason_code,
            retryable=outcome.retryable,
            retry_after_seconds=outcome.retry_after_seconds,
            evidence_recorded=outcome.evidence_key is not None,
            semantic_marker="TECHNICAL_FAILURE",
        )
    return ConformanceObservation(
        case_id=case_id,
        outcome_kind=ConformanceOutcomeKind.CONTRACT_FAILURE,
        reason_code=outcome.reason_code,
        evidence_recorded=outcome.evidence_key is not None,
        semantic_marker="CONTRACT_FAILURE",
    )


def _success_marker(result: InventoryQueryOperationResult) -> str:
    if not result.items:
        return "EMPTY"
    if len(result.items) == 2:
        return "TWO_ITEMS"
    if result.items[0].available_quantity == Decimal("9007199254740993.125"):
        return "DECIMAL_EXACT"
    return "ONE_ITEM"


__all__ = [
    "QUERY_INVENTORY_SCRIPT_FIXTURE",
    "QueryInventoryAdapterFactory",
    "QueryInventoryReplayFactory",
    "QueryInventoryScriptFixture",
    "QueryInventorySimulatorFactory",
    "ScriptedQueryCase",
    "build_query_inventory_conformance_targets",
]
