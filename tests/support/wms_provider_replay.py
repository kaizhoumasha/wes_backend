"""WMS Provider conformance 的纯 replay asset、重建逻辑与无状态 factory。"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from src.app.runtime.system_capabilities.wms.provider_conformance import (
    QUERY_INVENTORY_CONFORMANCE_CASES,
    ConformanceObservation,
    ConformanceOutcomeKind,
    ConformanceTarget,
    WmsConformanceReport,
    verify_wms_conformance_report,
)
from src.app.wms_integration.ports.query_inventory_operation import (
    InventoryAuthorityItem,
    InventoryQueryOperationResult,
)
from src.app.wms_integration.ports.query_outcome import (
    QueryBusinessReject,
    QueryContractFailure,
    QuerySuccess,
    QueryTechnicalFailure,
)

if TYPE_CHECKING:
    from src.app.wms_integration.ports.query_outcome import WmsQueryOutcome

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
QUERY_INVENTORY_REPLAY_ASSET_DIGEST = "4584ece449cdcfa69f6a46ac4315b3f11a285f3f832a82bc04685c21ac22bf52"
_DEFAULT_ASSET_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures/wms_provider_conformance/query_inventory_replay.v1.json"
)


class ReplayCaseIdentity(Protocol):
    """replay factory 只读取 case identity，不依赖 scripted adapter fixture。"""

    @property
    def case_id(self) -> str: ...


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
    """独立于 scripted fixture 的固定 replay asset。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["wms-query-inventory-replay.v1"]
    records: tuple[QueryInventoryReplayRecord, ...]
    digest: Sha256Digest

    @model_validator(mode="after")
    def verify_asset(self) -> QueryInventoryReplayFixture:
        expected_ids = tuple(case.case_id for case in QUERY_INVENTORY_CONFORMANCE_CASES)
        actual_ids = tuple(record.case_id for record in self.records)
        if actual_ids != expected_ids:
            raise ValueError("query inventory replay record identity order mismatch")
        if not self.verify():
            raise ValueError("query inventory replay asset digest mismatch")
        return self

    def verify(self) -> bool:
        return hmac.compare_digest(self.digest, _replay_fixture_digest(self))


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


def load_query_inventory_replay_fixture(asset_path: Path | None = None) -> QueryInventoryReplayFixture:
    """加载 replay asset，并同时校验 record 顺序、asset 自摘要和代码侧独立 pin。"""

    loaded = QueryInventoryReplayFixture.model_validate_json(
        (asset_path or _DEFAULT_ASSET_PATH).read_text(encoding="utf-8")
    )
    if not hmac.compare_digest(loaded.digest, QUERY_INVENTORY_REPLAY_ASSET_DIGEST):
        raise ValueError("query inventory replay asset does not match the code-pinned digest")
    return loaded


QUERY_INVENTORY_REPLAY_FIXTURE = load_query_inventory_replay_fixture()


class QueryInventoryReplayFactory:
    """仅从已 pin 的 replay record 重建 T3 outcome；没有外部 effect 能力。"""

    name = "canonical-replay"
    target = ConformanceTarget.REPLAY
    asset_digest = QUERY_INVENTORY_REPLAY_ASSET_DIGEST
    __slots__ = ()

    async def execute(self, case: ReplayCaseIdentity) -> ConformanceObservation:
        record = next(
            (record for record in QUERY_INVENTORY_REPLAY_FIXTURE.records if record.case_id == case.case_id),
            None,
        )
        if record is None:
            raise AssertionError(f"missing canonical replay record: {case.case_id}")
        outcome = reconstruct_query_inventory_outcome(record)
        return observe_query_inventory_outcome(outcome, case_id=record.case_id)


def reconstruct_query_inventory_outcome(
    record: QueryInventoryReplayRecord,
) -> WmsQueryOutcome[InventoryQueryOperationResult]:
    """从冻结领域事实重建 T3 四分支 outcome。"""

    evidence_key = "replay:evidence" if record.evidence_recorded else None
    if record.outcome_kind is ConformanceOutcomeKind.SUCCESS:
        result = InventoryQueryOperationResult(
            items=tuple(
                InventoryAuthorityItem(
                    material_code=item.material_code,
                    available_quantity=item.available_quantity,
                )
                for item in record.items
            )
        )
        return QuerySuccess(value=result, evidence_key=evidence_key)
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


def observe_query_inventory_outcome(
    outcome: WmsQueryOutcome[InventoryQueryOperationResult],
    *,
    case_id: str,
) -> ConformanceObservation:
    """把 T3 outcome 投影为固定、脱敏 conformance observation。"""

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
    return ConformanceObservation(
        case_id=case_id,
        outcome_kind=ConformanceOutcomeKind.CONTRACT_FAILURE,
        reason_code=outcome.reason_code,
        evidence_recorded=outcome.evidence_key is not None,
        semantic_marker="CONTRACT_FAILURE",
    )


def verify_query_inventory_replay_report(payload: dict[str, object]) -> WmsConformanceReport:
    """验证 replay 报告来自当前代码 pin 的实际 asset。"""

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
    "QUERY_INVENTORY_REPLAY_ASSET_DIGEST",
    "QUERY_INVENTORY_REPLAY_FIXTURE",
    "QueryInventoryReplayFactory",
    "QueryInventoryReplayFixture",
    "QueryInventoryReplayRecord",
    "ReplayInventoryItem",
    "load_query_inventory_replay_fixture",
    "observe_query_inventory_outcome",
    "reconstruct_query_inventory_outcome",
    "verify_query_inventory_replay_report",
]
