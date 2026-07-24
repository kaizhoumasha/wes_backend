"""Workline 插件不可变运行绑定。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - SQLModel table fields need runtime types.
from typing import Any, ClassVar, Literal

from sqlalchemy import JSON, Column, UniqueConstraint, text
from sqlmodel import Field

from src.core.mixins import BaseMixin
from src.database.schema_conf import SchemaType


class WorklinePluginBinding(BaseMixin, table=True):
    """一次激活审批生成一行；历史行只允许追加停用证据。"""

    __tablename__: ClassVar[Literal["workline_plugin_bindings"]] = "workline_plugin_bindings"  # pyright: ignore[reportIncompatibleVariableOverride]  # SQLModel runtime metadata.
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        UniqueConstraint(
            "workline_id",
            "plugin_key",
            "contract_version",
            "binding_version",
            name="uq_workline_plugin_binding_identity",
        ),
        {"schema": SchemaType.BIZ.value},
    )

    id: int | None = Field(default=None, primary_key=True)
    workline_id: int = Field(foreign_key="wes_biz.work_lines.id", index=True)
    plugin_key: str = Field(max_length=100, index=True)
    contract_version: str = Field(max_length=60)
    binding_version: int = Field(ge=1)
    typed_config_json: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    typed_config_hash: str = Field(min_length=64, max_length=64, index=True)
    provider_profile_snapshot_json: list[dict[str, Any]] = Field(sa_column=Column(JSON), default_factory=list)
    device_snapshot_json: list[dict[str, Any]] = Field(sa_column=Column(JSON), default_factory=list)
    generated_index_digest: str = Field(min_length=64, max_length=64)
    environment: str = Field(max_length=30)
    activated_at: datetime
    activated_by: str = Field(max_length=100)
    activated_reason: str = Field(max_length=500)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    is_enabled: bool = Field(default=True, sa_column_kwargs={"server_default": text("true")})
    disabled_at: datetime | None = None
    disabled_by: str | None = Field(default=None, max_length=100)
    disabled_reason: str | None = Field(default=None, max_length=500)
    is_revoked: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    revoked_at: datetime | None = None
    revoked_by: str | None = Field(default=None, max_length=100)
    revoked_reason: str | None = Field(default=None, max_length=500)


__all__ = ["WorklinePluginBinding"]
