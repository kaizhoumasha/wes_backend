"""批量人工对账 evidence 到受影响执行的专用冻结关联。"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.database.schema_conf import SchemaType


class InboundEvidenceExecutionBinding(EnterpriseMixin, DataTableMixin, table=True):
    """只表达一条 WMS 对账 evidence 的有序 execution 成员。"""

    __tablename__: ClassVar[str] = "inbound_evidence_execution_bindings"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="inbound_evidence_execution_binding_ordinal_nonnegative"),
        UniqueConstraint(
            "inbound_evidence_id",
            "material_execution_id",
            name="ux_inbound_evidence_execution_bindings_evidence_execution",
        ),
        UniqueConstraint(
            "inbound_evidence_id",
            "ordinal",
            name="ux_inbound_evidence_execution_bindings_evidence_ordinal",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    inbound_evidence_id: int = Field(foreign_key="wes_biz.inbound_evidences.id", index=True)
    material_execution_id: int = Field(foreign_key="wes_biz.material_executions.id", index=True)
    ordinal: int = Field(ge=0)


__all__ = ["InboundEvidenceExecutionBinding"]
