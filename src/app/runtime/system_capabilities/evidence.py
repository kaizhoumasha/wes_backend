"""System Capability QUERY 的可审计、可重放 evidence 合同。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  # Pydantic runtime validation 需要具体类型
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from src.app.runtime.orchestration.models.timeline import TimelineActionType
from src.app.runtime.system_capabilities.shadow_readiness import (  # noqa: TC001  # Pydantic runtime schema
    QueryShadowExpected,
)


class QueryEvidence(BaseModel):
    """写入既有 DECISION_MADE timeline payload 的脱敏查询证据。"""

    model_config = ConfigDict(frozen=True)

    capability_key: str = Field(min_length=1)
    contract_version: str = Field(min_length=1)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority: str = Field(min_length=1)
    source: str = Field(min_length=1)
    evidence_at: datetime
    source_version: str = Field(min_length=1)
    admission_snapshot: dict[str, JsonValue]
    summary: dict[str, JsonValue]
    shadow_expected: QueryShadowExpected | None = None

    @field_validator("evidence_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        """证据时间必须带时区，避免 replay 时发生本地时区漂移。"""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evidence_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def bind_shadow_expected_to_evidence(self) -> QueryEvidence:
        """expected 必须与同一 evidence 的 hash/时间完全一致，禁止旁路样本。"""

        expected = self.shadow_expected
        if expected is None:
            return self
        if expected.input_hash != self.input_hash or expected.output_hash != self.output_hash:
            raise ValueError("shadow expected hashes must match QUERY evidence")
        if expected.observed_at != self.evidence_at:
            raise ValueError("shadow expected timestamp must match QUERY evidence")
        return self

    def payload(self) -> dict[str, Any]:
        """返回可直接持久化的 canonical timeline payload。"""

        return self.model_dump(mode="json", exclude_none=True)

    def to_timeline_record(self) -> dict[str, Any]:
        """映射到既有 DECISION_MADE，而不新增 timeline action 类型。"""

        return {
            "action_type": TimelineActionType.DECISION_MADE,
            "payload_json": self.payload(),
        }


__all__ = ["QueryEvidence"]
