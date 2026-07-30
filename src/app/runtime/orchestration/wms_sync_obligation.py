"""E03/E07 同步义务的 typed reconciliation resolution 合同。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

E03_CONFIRM_INBOUND = "wms.inventory.confirm_inbound@v1"
E07_NOTIFY_PKG_BINDING = "wms.fulfillment.notify_pkg_binding@v1"
WMS_SYNC_OBLIGATION_OPERATION_IDENTITIES = frozenset(
    {
        E03_CONFIRM_INBOUND,
        E07_NOTIFY_PKG_BINDING,
    }
)


class WmsSyncObligationResolution(BaseModel):
    """明确满足单项 E03/E07 同步义务的已关闭对账裁决。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resolved_operation_identity: Literal[
        "wms.inventory.confirm_inbound@v1",
        "wms.fulfillment.notify_pkg_binding@v1",
    ]
    resolved_fact_version: str = Field(min_length=1, max_length=120)
    resolution: Literal["OBLIGATION_SATISFIED"]
    source_event_id: str = Field(min_length=1, max_length=240)
    evidence_reference: str = Field(min_length=1, max_length=500)

    @field_validator(
        "resolved_fact_version",
        "source_event_id",
        "evidence_reference",
        mode="before",
    )
    @classmethod
    def normalize_stable_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


__all__ = [
    "E03_CONFIRM_INBOUND",
    "E07_NOTIFY_PKG_BINDING",
    "WMS_SYNC_OBLIGATION_OPERATION_IDENTITIES",
    "WmsSyncObligationResolution",
]
