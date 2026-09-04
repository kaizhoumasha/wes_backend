"""WMS wire DTO 共用的最小类型与信封身份校验。"""

from __future__ import annotations

from typing import Annotated, TypeGuard

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from wes_plugin_sdk.validation import is_persistable_text as _is_persistable_text

from src.core.uuid7 import is_uuid7

MAX_WMS_EVENT_BODY_BYTES = 256 * 1024
UUIDV7_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"

OperationId = Annotated[str, StringConstraints(pattern=UUIDV7_PATTERN)]
PositiveMilliseconds = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
NonnegativeMilliseconds = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
PositiveInteger = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]


class StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def is_wire_operation_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value == value.lower() and is_uuid7(value)


def is_wire_operation(value: object) -> TypeGuard[str]:
    return _is_persistable_text(value, 80)


__all__ = [
    "MAX_WMS_EVENT_BODY_BYTES",
    "UUIDV7_PATTERN",
    "NonnegativeMilliseconds",
    "OperationId",
    "PositiveInteger",
    "PositiveMilliseconds",
    "StrictWireModel",
    "is_wire_operation",
    "is_wire_operation_id",
]
