"""System Capability QUERY 的可审计、可重放 evidence 合同。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003  # Pydantic runtime validation 需要具体类型
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from src.app.runtime.orchestration.models.timeline import TimelineActionType


class QueryEvidence(BaseModel):
    """写入既有 DECISION_MADE timeline payload 的脱敏查询证据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

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

    @field_validator("evidence_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        """证据时间必须带时区，避免 replay 时发生本地时区漂移。"""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evidence_at must be timezone-aware")
        return value

    def payload(self) -> dict[str, Any]:
        """返回可直接持久化的 canonical timeline payload。"""

        return self.model_dump(mode="json")

    def to_timeline_record(self) -> dict[str, Any]:
        """映射到既有 DECISION_MADE，而不新增 timeline action 类型。"""

        return {
            "action_type": TimelineActionType.DECISION_MADE,
            "payload_json": self.payload(),
        }


__all__ = ["QueryEvidence"]
