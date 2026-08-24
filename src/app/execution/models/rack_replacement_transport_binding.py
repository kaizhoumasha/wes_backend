"""换架业务身份到 Transport client identity 的最窄映射。"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class RackReplacementTransportBinding(EnterpriseMixin, DataTableMixin, table=True):
    """冻结一个换架腿对应的全局 Transport client identity。"""

    __tablename__: ClassVar[str] = "rack_replacement_transport_bindings"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint("leg IN ('OLD_OUT', 'NEW_IN')", name="rack_replacement_transport_binding_leg_valid"),
        ForeignKeyConstraint(
            ["line_run_epoch_id"],
            ["wes_biz.line_run_epochs.id"],
            name="fk_rack_replacement_transport_bindings_epoch",
        ),
        UniqueConstraint(
            "rack_replacement_id",
            "leg",
            name="ux_rack_replacement_transport_bindings_business_identity",
        ),
        UniqueConstraint(
            "client_request_id",
            name="ux_rack_replacement_transport_bindings_client_request_id",
        ),
        UniqueConstraint(
            "line_run_epoch_id",
            "current_rack_id",
            "leg",
            name="ux_rack_replacement_transport_bindings_epoch_rack_leg",
        ),
        Index(
            "ix_wes_biz_rack_replacement_transport_bindings_epoch_rack",
            "line_run_epoch_id",
            "current_rack_id",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    rack_replacement_id: str = Field(min_length=1, max_length=160)
    leg: str = Field(min_length=1, max_length=20)
    line_run_epoch_id: int
    current_rack_id: str = Field(min_length=1, max_length=80)
    client_request_id: str = Field(min_length=1, max_length=120)
    source_evidence_id: int = Field(
        foreign_key="wes_biz.inbound_evidences.id",
        index=True,
        sa_type=SQL_COMPAT_BIGINT,
    )

    @property
    def business_identity(self) -> tuple[str, str]:
        return self.rack_replacement_id, self.leg

    @property
    def rack_fence_identity(self) -> tuple[int, str]:
        return self.line_run_epoch_id, self.current_rack_id


__all__ = ["RackReplacementTransportBinding"]
