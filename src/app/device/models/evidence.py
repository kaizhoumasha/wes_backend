"""设备状态观察。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, ClassVar

from sqlalchemy import JSON, BigInteger, Column, Index
from sqlmodel import Field

from src.core.mixins import DataTableMixin, EnterpriseMixin
from src.database.schema_conf import SchemaType


class DeviceStatusObservation(EnterpriseMixin, DataTableMixin, table=True):
    """每次派发准入实际使用的不可变 ECS 状态观察。"""

    __tablename__: ClassVar[str] = "device_status_observations"  # pyright: ignore[reportIncompatibleVariableOverride]
    __schema__ = SchemaType.BIZ.value
    __table_args__ = (
        Index("ix_device_status_observations_device_received", "device_code", "received_at", "id"),
        {"schema": SchemaType.BIZ.value},
    )

    device_code: str = Field(min_length=1, max_length=100)
    command_code: str | None = Field(default=None, max_length=100)
    contract_key: str = Field(min_length=1, max_length=100)
    contract_version: str = Field(min_length=1, max_length=50)
    mode: str = Field(min_length=1, max_length=20)
    status: str = Field(min_length=1, max_length=20)
    current_command_code: str | None = Field(default=None, max_length=160)
    device_timestamp: int = Field(sa_type=BigInteger)
    received_at: datetime
    payload_digest: str = Field(min_length=64, max_length=64)
    raw_payload: dict[str, Any] = Field(sa_column=Column(JSON))


__all__ = ["DeviceStatusObservation"]
