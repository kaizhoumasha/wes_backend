"""插件 Transport Decision 到稳定 client identity 的中立映射。"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import ForeignKeyConstraint, Index, UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.core.mixins.primary_key import SQL_COMPAT_BIGINT
from src.database.schema_conf import SchemaType


class TransportDecisionBinding(EnterpriseMixin, DataTableMixin, table=True):
    """冻结一个插件 Transport Decision 对应的全局 client identity。"""

    __tablename__: ClassVar[str] = "transport_decision_bindings"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        ForeignKeyConstraint(
            ["line_run_epoch_id"],
            ["wes_biz.line_run_epochs.id"],
            name="fk_transport_decision_bindings_epoch",
        ),
        UniqueConstraint(
            "line_run_epoch_id",
            "correlation_id",
            "step",
            name="ux_transport_decision_bindings_decision_identity",
        ),
        UniqueConstraint(
            "client_request_id",
            name="ux_transport_decision_bindings_client_request_id",
        ),
        Index(
            "ix_wes_biz_transport_decision_bindings_epoch_resource",
            "line_run_epoch_id",
            "resource_fence_id",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    correlation_id: str = Field(min_length=1, max_length=160)
    step: str = Field(min_length=1, max_length=80)
    line_run_epoch_id: int = Field(sa_type=SQL_COMPAT_BIGINT)
    resource_fence_id: str = Field(min_length=1, max_length=160)
    client_request_id: str = Field(min_length=1, max_length=120)
    source_evidence_id: int = Field(
        foreign_key="wes_biz.inbound_evidences.id",
        index=True,
        sa_type=SQL_COMPAT_BIGINT,
    )

    @property
    def decision_identity(self) -> tuple[int, str, str]:
        return self.line_run_epoch_id, self.correlation_id, self.step

    @property
    def resource_fence_identity(self) -> tuple[int, str]:
        return self.line_run_epoch_id, self.resource_fence_id


__all__ = ["TransportDecisionBinding"]
