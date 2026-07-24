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
    WmsConformanceReport,
    verify_wms_conformance_report,
)
from src.app.wms_integration.adapters.query_inventory_operation_adapter import InventoryQueryOperationAdapter
from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryQueryOperationRequest,
    InventoryQueryOperationResult,
)
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
    WmsQueryOutcome,
)
from src.app.wms_integration.services.query_transport import (
    WmsBoundQueryEndpoint,
    WmsQueryCallPermit,
    WmsQueryTransportExecutor,
)
from tests.support.wms_provider_replay import (
    QUERY_INVENTORY_REPLAY_ASSET_DIGEST,
    QueryInventoryReplayFactory,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

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
    asset_digest = QUERY_INVENTORY_SCRIPT_FIXTURE.digest
    __slots__ = ()

    async def execute(self, case: ScriptedQueryCase) -> ConformanceObservation:
        from tests.mock.wms_scripted_provider import scripted_query_inventory_response

        return await _execute_adapter_case(
            case,
            handler=lambda request: scripted_query_inventory_response(case, request),
        )


class QueryInventorySimulatorFactory:
    """真实 adapter 连接进程内最小 simulator；仍复用唯一 T3 transport 生命周期。"""

    name = "in-process-simulator"
    target = ConformanceTarget.SIMULATOR
    asset_digest = QUERY_INVENTORY_SCRIPT_FIXTURE.digest
    __slots__ = ()

    async def execute(self, case: ScriptedQueryCase) -> ConformanceObservation:
        from tests.mock.wms_scripted_provider import ScriptedWmsQueryInventoryProvider

        provider = ScriptedWmsQueryInventoryProvider(case)
        return await _execute_adapter_case(case, handler=provider.handle)


class QueryInventoryReplayConformanceFactory:
    """把纯 replay DTO 投影到外层 runtime conformance contract。"""

    name = "canonical-replay"
    target = ConformanceTarget.REPLAY
    asset_digest = QUERY_INVENTORY_REPLAY_ASSET_DIGEST
    __slots__ = ()

    async def execute(self, case: ScriptedQueryCase) -> ConformanceObservation:
        replay_observation = await QueryInventoryReplayFactory().execute(case)
        return ConformanceObservation.model_validate(replay_observation.model_dump(mode="json"))


class RecordingConformanceTarget:
    """仅供共同试卷记录执行顺序；被测 factory 保持无状态。"""

    __slots__ = ("_delegate", "_executed_case_ids")

    def __init__(self, delegate) -> None:
        self._delegate = delegate
        self._executed_case_ids: list[str] = []

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def target(self) -> ConformanceTarget:
        return self._delegate.target

    @property
    def asset_digest(self) -> str:
        return self._delegate.asset_digest

    @property
    def executed_case_ids(self) -> tuple[str, ...]:
        return tuple(self._executed_case_ids)

    async def execute(self, case: ScriptedQueryCase) -> ConformanceObservation:
        self._executed_case_ids.append(case.case_id)
        return await self._delegate.execute(case)


def build_query_inventory_conformance_targets():
    """返回固定三个执行面；Provider 不可删题或替换题库。"""

    return (
        RecordingConformanceTarget(QueryInventoryAdapterFactory()),
        RecordingConformanceTarget(QueryInventorySimulatorFactory()),
        RecordingConformanceTarget(QueryInventoryReplayConformanceFactory()),
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
    return observe_query_inventory_outcome(outcome, case_id=case.case_id)


def observe_query_inventory_outcome(
    outcome: WmsQueryOutcome[InventoryQueryOperationResult],
    *,
    case_id: str,
) -> ConformanceObservation:
    """外层 adapter 把 T3 outcome 投影为 runtime conformance observation。"""

    if isinstance(outcome, QuerySuccess):
        return ConformanceObservation(
            case_id=case_id,
            outcome_kind=ConformanceOutcomeKind.SUCCESS,
            evidence_recorded=outcome.evidence_key is not None,
            semantic_marker=_success_marker(outcome.value),
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
    if not isinstance(outcome, QueryContractFailure):
        raise TypeError("query inventory adapter returned an unsupported outcome")
    return ConformanceObservation(
        case_id=case_id,
        outcome_kind=ConformanceOutcomeKind.CONTRACT_FAILURE,
        reason_code=outcome.reason_code,
        evidence_recorded=outcome.evidence_key is not None,
        semantic_marker="CONTRACT_FAILURE",
    )


def verify_query_inventory_replay_report(payload: dict[str, object]) -> WmsConformanceReport:
    """在外层验证 runtime 报告来自当前代码 pin 的 replay asset。"""

    report = verify_wms_conformance_report(payload)
    if report.target is not ConformanceTarget.REPLAY:
        raise ValueError("query inventory replay verifier requires a REPLAY report")
    if not hmac.compare_digest(report.fixture_digest, QUERY_INVENTORY_REPLAY_ASSET_DIGEST):
        raise ValueError("query inventory replay report asset digest mismatch")
    return report


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
    "QueryInventoryReplayConformanceFactory",
    "QueryInventoryScriptFixture",
    "QueryInventorySimulatorFactory",
    "RecordingConformanceTarget",
    "ScriptedQueryCase",
    "build_query_inventory_conformance_targets",
    "observe_query_inventory_outcome",
    "verify_query_inventory_replay_report",
]
