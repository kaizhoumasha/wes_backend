"""ActiveObjectRegistry read-model conflict policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class ActiveObjectFact:
    """单个投影来源报告的 active object fact。"""

    object_code: str
    owner_kind: str
    owner_code: str
    evidence_ref: str
    object_type: str = "bin"
    presence_type: str | None = None
    transient_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class ActiveObjectResolution:
    """ActiveObjectRegistry 归属仲裁结果。"""

    object_code: str
    status: str
    owner_kind: str | None
    owner_code: str | None
    reconciliation_required: bool
    conflict_policy: str | None
    evidence_refs: list[str]


class ActiveObjectRegistry:
    """3 路 active projection UNION 后的唯一归属仲裁。"""

    def resolve(self, facts: list[ActiveObjectFact], *, now: datetime | None = None) -> ActiveObjectResolution:
        if not facts:
            return ActiveObjectResolution(
                object_code="",
                status="INACTIVE",
                owner_kind=None,
                owner_code=None,
                reconciliation_required=False,
                conflict_policy=None,
                evidence_refs=[],
            )

        object_code = facts[0].object_code
        owners = {(fact.owner_kind, fact.owner_code) for fact in facts}
        evidence_refs = [fact.evidence_ref for fact in facts]
        if len(owners) == 1:
            owner_kind, owner_code = next(iter(owners))
            return ActiveObjectResolution(
                object_code=object_code,
                status="ACTIVE",
                owner_kind=owner_kind,
                owner_code=owner_code,
                reconciliation_required=False,
                conflict_policy=None,
                evidence_refs=evidence_refs,
            )

        transient_policy = self._resolve_transient_policy(facts, now=now)
        if transient_policy == "ACTIVE":
            return ActiveObjectResolution(
                object_code=object_code,
                status="TRANSIENT",
                owner_kind=None,
                owner_code=None,
                reconciliation_required=False,
                conflict_policy="TRANSIENT_TRANSFER_HANDOFF",
                evidence_refs=evidence_refs,
            )
        if transient_policy == "EXPIRED":
            return ActiveObjectResolution(
                object_code=object_code,
                status="RECONCILING",
                owner_kind=None,
                owner_code=None,
                reconciliation_required=True,
                conflict_policy="TRANSIENT_WINDOW_EXPIRED",
                evidence_refs=evidence_refs,
            )
        return ActiveObjectResolution(
            object_code=object_code,
            status="RECONCILING",
            owner_kind=None,
            owner_code=None,
            reconciliation_required=True,
            conflict_policy="MULTI_ACTIVE_OWNER",
            evidence_refs=evidence_refs,
        )

    @staticmethod
    def _resolve_transient_policy(facts: list[ActiveObjectFact], *, now: datetime | None) -> str | None:
        presence_types = {fact.presence_type or fact.owner_kind.upper() for fact in facts}
        if presence_types != {"IN_TRANSFER", "ON_CONVEYOR"}:
            return None
        if now is None:
            return "ACTIVE"
        transient_until_values = [fact.transient_until for fact in facts if fact.transient_until is not None]
        if not transient_until_values:
            return "ACTIVE"
        return "ACTIVE" if now <= max(transient_until_values) else "EXPIRED"


__all__ = ["ActiveObjectFact", "ActiveObjectRegistry", "ActiveObjectResolution"]
