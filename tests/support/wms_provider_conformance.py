"""WMS Provider conformance 的 test-only fixture 与三个执行工厂。"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

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


class ReplayInventoryItem(BaseModel):
    """固定 replay asset 中重建 T3 success outcome 所需的最小领域事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    material_code: str
    available_quantity: Decimal


class QueryInventoryReplayRecord(BaseModel):
    """独立 replay asset 的冻结 outcome record，不引用当前题库 expectation。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    outcome_kind: ConformanceOutcomeKind
    reason_code: str | None
    retryable: bool | None
    retry_after_seconds: float | None
    evidence_recorded: bool
    items: tuple[ReplayInventoryItem, ...]

    @model_validator(mode="after")
    def validate_recorded_outcome(self) -> QueryInventoryReplayRecord:
        if self.outcome_kind is ConformanceOutcomeKind.SUCCESS:
            if self.reason_code is not None or self.retryable is not None:
                raise ValueError("replay success cannot carry failure classification")
        elif self.outcome_kind is ConformanceOutcomeKind.TECHNICAL_FAILURE:
            if self.reason_code is None or self.retryable is None or self.items:
                raise ValueError("replay technical failure requires classification without items")
        elif self.reason_code is None or self.retryable is not None or self.items:
            raise ValueError("replay business/contract failure requires reason without items")
        if self.retry_after_seconds is not None and not (
            self.outcome_kind is ConformanceOutcomeKind.TECHNICAL_FAILURE and self.retryable
        ):
            raise ValueError("replay retry_after_seconds requires retryable technical failure")
        return self


class QueryInventoryReplayFixture(BaseModel):
    """独立于当前 conformance 题库的固定 replay asset 与内容摘要。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["wms-query-inventory-replay.v1"]
    records: tuple[QueryInventoryReplayRecord, ...]
    digest: Sha256Digest

    @model_validator(mode="after")
    def verify_asset(self) -> QueryInventoryReplayFixture:
        case_ids = tuple(record.case_id for record in self.records)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("query inventory replay asset contains duplicate case identity")
        if not self.verify():
            raise ValueError("query inventory replay asset digest mismatch")
        return self

    def verify(self) -> bool:
        return hmac.compare_digest(self.digest, _replay_fixture_digest(self))


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


def _replay_fixture_digest(fixture: QueryInventoryReplayFixture) -> str:
    canonical = json.dumps(
        {
            "schema_version": fixture.schema_version,
            "records": [record.model_dump(mode="json") for record in fixture.records],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_replay_fixture() -> QueryInventoryReplayFixture:
    asset_path = (
        Path(__file__).resolve().parents[1] / "fixtures/wms_provider_conformance/query_inventory_replay.v1.json"
    )
    return QueryInventoryReplayFixture.model_validate_json(asset_path.read_text(encoding="utf-8"))


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
QUERY_INVENTORY_REPLAY_FIXTURE = _load_replay_fixture()


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
    __slots__ = ()

    async def execute(self, case: ScriptedQueryCase) -> ConformanceObservation:
        from tests.mock.wms_scripted_provider import ScriptedWmsQueryInventoryProvider

        provider = ScriptedWmsQueryInventoryProvider(case)
        return await _execute_adapter_case(case, handler=provider.handle)


class QueryInventoryReplayFactory:
    """仅从冻结 replay record 重建 T3 outcome；没有网络或密钥依赖。"""

    name = "canonical-replay"
    target = ConformanceTarget.REPLAY
    __slots__ = ()

    async def execute(self, case: ScriptedQueryCase) -> ConformanceObservation:
        record = next(
            (record for record in QUERY_INVENTORY_REPLAY_FIXTURE.records if record.case_id == case.case_id),
            None,
        )
        if record is None:
            raise AssertionError(f"missing canonical replay record: {case.case_id}")
        return _observe_query_outcome(_recorded_outcome(record), case_id=record.case_id)


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
        RecordingConformanceTarget(QueryInventoryReplayFactory()),
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


def _recorded_outcome(record: QueryInventoryReplayRecord) -> WmsQueryOutcome[InventoryQueryOperationResult]:
    evidence_key = "replay:evidence" if record.evidence_recorded else None
    if record.outcome_kind is ConformanceOutcomeKind.SUCCESS:
        return QuerySuccess(value=_recorded_success(record), evidence_key=evidence_key)
    if record.outcome_kind is ConformanceOutcomeKind.BUSINESS_REJECT:
        return QueryBusinessReject(
            reason_code=record.reason_code or "",
            message="recorded business reject",
            evidence_key=evidence_key,
        )
    if record.outcome_kind is ConformanceOutcomeKind.TECHNICAL_FAILURE:
        return QueryTechnicalFailure(
            reason_code=record.reason_code or "",
            message="recorded technical failure",
            retryable=bool(record.retryable),
            retry_after_seconds=record.retry_after_seconds,
            evidence_key=evidence_key,
        )
    return QueryContractFailure(
        reason_code=record.reason_code or "",
        message="recorded contract failure",
        evidence_key=evidence_key,
    )


def _recorded_success(record: QueryInventoryReplayRecord) -> InventoryQueryOperationResult:
    return InventoryQueryOperationResult(
        items=tuple(
            InventoryAuthorityItem(
                material_code=item.material_code,
                available_quantity=item.available_quantity,
            )
            for item in record.items
        )
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
    "QUERY_INVENTORY_REPLAY_FIXTURE",
    "QUERY_INVENTORY_SCRIPT_FIXTURE",
    "QueryInventoryAdapterFactory",
    "QueryInventoryReplayFactory",
    "QueryInventoryReplayFixture",
    "QueryInventoryReplayRecord",
    "QueryInventoryScriptFixture",
    "QueryInventorySimulatorFactory",
    "RecordingConformanceTarget",
    "ReplayInventoryItem",
    "ScriptedQueryCase",
    "build_query_inventory_conformance_targets",
]
