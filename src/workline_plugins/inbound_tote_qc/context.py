"""入库料箱称重复核插件上下文模型。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from pydantic import BaseModel, ConfigDict


class InboundToteQcContext(BaseModel):
    """入库料箱复核插件上下文快照。"""

    tote_id: str | None = None
    station_code: str | None = None
    expected_weight_kg: float | None = None
    tolerance_kg: float | None = None
    actual_weight_kg: float | None = None
    destination_lane: str | None = None
    reason_code: str | None = None

    model_config = ConfigDict(extra="allow")

    @classmethod
    def from_mapping(cls, value: Any) -> InboundToteQcContext:
        return cls.model_validate(dict(cast("Mapping[str, Any]", value)) if isinstance(value, Mapping) else {})

    @classmethod
    def from_session(cls, session: Any) -> InboundToteQcContext:
        return cls.from_mapping(getattr(session, "context_json", None))

    def to_patch(self, *, exclude_none: bool = True) -> dict[str, Any]:
        return self.model_dump(exclude_none=exclude_none)


def parse_inbound_tote_qc_context(ctx: Any) -> InboundToteQcContext:
    return InboundToteQcContext.from_session(getattr(ctx, "session", None))


__all__ = ["InboundToteQcContext", "parse_inbound_tote_qc_context"]
