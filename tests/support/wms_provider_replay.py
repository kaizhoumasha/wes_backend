"""WMS Provider conformance 的纯 replay asset、固定 DTO 与无状态 factory。"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CaseId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]*$")]
ConformanceCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z][A-Z0-9_]*$")]
QUERY_INVENTORY_REPLAY_ASSET_DIGEST = "d396dce29e343138317e00ba8389a5d869c16c1262373e575a84cc5e5375bca5"
QUERY_INVENTORY_REPLAY_CASE_IDS = (
    "success",
    "empty",
    "missing_field",
    "invalid_decimal",
    "reject",
    "timeout",
    "rate_limit",
    "unavailable",
    "malformed",
    "pagination",
    "precision",
    "budget",
    "evidence_failure",
)
_DEFAULT_ASSET_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures/wms_provider_conformance/query_inventory_replay.v1.json"
)


class ReplayOutcomeKind(str, Enum):
    """冻结 asset 使用的封闭 outcome 分类。"""

    SUCCESS = "SUCCESS"
    BUSINESS_REJECT = "BUSINESS_REJECT"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    CONTRACT_FAILURE = "CONTRACT_FAILURE"


class ReplayCaseIdentity(Protocol):
    """replay factory 只读取 case identity，不依赖 scripted adapter fixture。"""

    @property
    def case_id(self) -> str: ...


class ReplayInventoryItem(BaseModel):
    """固定 replay asset 中重建 success outcome 所需的最小领域事实。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    material_code: str
    available_quantity: Decimal


class QueryInventoryReplayRecord(BaseModel):
    """独立 replay asset 的冻结 outcome record，不引用当前题库 expectation。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: CaseId
    outcome_kind: ReplayOutcomeKind
    reason_code: ConformanceCode | None
    retryable: bool | None
    retry_after_seconds: float | None
    evidence_recorded: bool
    items: tuple[ReplayInventoryItem, ...]

    @model_validator(mode="after")
    def validate_recorded_outcome(self) -> QueryInventoryReplayRecord:
        if self.outcome_kind is ReplayOutcomeKind.SUCCESS:
            if self.reason_code is not None or self.retryable is not None:
                raise ValueError("replay success cannot carry failure classification")
        elif self.outcome_kind is ReplayOutcomeKind.TECHNICAL_FAILURE:
            if self.reason_code is None or self.retryable is None or self.items:
                raise ValueError("replay technical failure requires classification without items")
        elif self.reason_code is None or self.retryable is not None or self.items:
            raise ValueError("replay business/contract failure requires reason without items")
        if self.retry_after_seconds is not None and not (
            self.outcome_kind is ReplayOutcomeKind.TECHNICAL_FAILURE and self.retryable
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
        actual_ids = tuple(record.case_id for record in self.records)
        if actual_ids != QUERY_INVENTORY_REPLAY_CASE_IDS:
            raise ValueError("query inventory replay record identity order mismatch")
        if not self.verify():
            raise ValueError("query inventory replay asset digest mismatch")
        return self

    def verify(self) -> bool:
        return hmac.compare_digest(self.digest, _replay_fixture_digest(self))


class ReplayQueryInventoryOutcome(BaseModel):
    """由固定 record 重建的纯 outcome DTO。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome_kind: ReplayOutcomeKind
    reason_code: ConformanceCode | None
    retryable: bool | None
    retry_after_seconds: float | None
    evidence_recorded: bool
    items: tuple[ReplayInventoryItem, ...]


class ReplayConformanceObservation(BaseModel):
    """交给外层 conformance adapter 的冻结、脱敏 observation DTO。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: CaseId
    outcome_kind: ReplayOutcomeKind
    reason_code: ConformanceCode | None = None
    retryable: bool | None = None
    retry_after_seconds: float | None = None
    evidence_recorded: bool
    semantic_marker: ConformanceCode


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
    """仅从已 pin 的 replay record 重建固定 DTO；没有外部 effect 能力。"""

    name = "canonical-replay"
    target = "REPLAY"
    asset_digest = QUERY_INVENTORY_REPLAY_ASSET_DIGEST
    __slots__ = ()

    async def execute(self, case: ReplayCaseIdentity) -> ReplayConformanceObservation:
        record = next(
            (record for record in QUERY_INVENTORY_REPLAY_FIXTURE.records if record.case_id == case.case_id),
            None,
        )
        if record is None:
            raise AssertionError(f"missing canonical replay record: {case.case_id}")
        outcome = reconstruct_query_inventory_outcome(record)
        return observe_query_inventory_outcome(outcome, case_id=record.case_id)


def reconstruct_query_inventory_outcome(record: QueryInventoryReplayRecord) -> ReplayQueryInventoryOutcome:
    """从冻结领域事实重建纯 outcome DTO。"""

    return ReplayQueryInventoryOutcome(
        outcome_kind=record.outcome_kind,
        reason_code=record.reason_code,
        retryable=record.retryable,
        retry_after_seconds=record.retry_after_seconds,
        evidence_recorded=record.evidence_recorded,
        items=record.items,
    )


def observe_query_inventory_outcome(
    outcome: ReplayQueryInventoryOutcome,
    *,
    case_id: str,
) -> ReplayConformanceObservation:
    """把纯 outcome DTO 投影为固定、脱敏 observation DTO。"""

    if outcome.outcome_kind is ReplayOutcomeKind.SUCCESS:
        semantic_marker = _success_marker(outcome.items)
    else:
        semantic_marker = outcome.outcome_kind.value
    return ReplayConformanceObservation(
        case_id=case_id,
        outcome_kind=outcome.outcome_kind,
        reason_code=outcome.reason_code,
        retryable=outcome.retryable,
        retry_after_seconds=outcome.retry_after_seconds,
        evidence_recorded=outcome.evidence_recorded,
        semantic_marker=semantic_marker,
    )


def _success_marker(items: tuple[ReplayInventoryItem, ...]) -> str:
    if not items:
        return "EMPTY"
    if len(items) == 2:
        return "TWO_ITEMS"
    if items[0].available_quantity == Decimal("9007199254740993.125"):
        return "DECIMAL_EXACT"
    return "ONE_ITEM"


__all__ = [
    "QUERY_INVENTORY_REPLAY_ASSET_DIGEST",
    "QUERY_INVENTORY_REPLAY_CASE_IDS",
    "QUERY_INVENTORY_REPLAY_FIXTURE",
    "QueryInventoryReplayFactory",
    "QueryInventoryReplayFixture",
    "QueryInventoryReplayRecord",
    "ReplayConformanceObservation",
    "ReplayInventoryItem",
    "ReplayOutcomeKind",
    "ReplayQueryInventoryOutcome",
    "load_query_inventory_replay_fixture",
    "observe_query_inventory_outcome",
    "reconstruct_query_inventory_outcome",
]
