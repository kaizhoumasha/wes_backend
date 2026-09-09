"""WMS wire DTO 共用的最小类型与信封身份校验。"""

from __future__ import annotations

from typing import Annotated, Any, TypeGuard, cast

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints
from wes_plugin_sdk.validation import is_persistable_text as _is_persistable_text
from wes_plugin_sdk.validation import validate_opaque_face

from src.app.wms_adapter.strict_json import StrictJsonError, loads_transport_json
from src.core.uuid7 import is_uuid7

DECISION_PATH = "/api/v1/wes/decisions"
FACT_PATH = "/api/v1/wes/facts"
MAX_WMS_EVENT_BODY_BYTES = 256 * 1024
UUIDV7_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"

OperationId = Annotated[str, StringConstraints(pattern=UUIDV7_PATTERN)]
PositiveMilliseconds = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
NonnegativeMilliseconds = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
PositiveInteger = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]


def _rack_face(value: str) -> str:
    validate_opaque_face(value, "rack_face")
    return value


RackFaceText = Annotated[str, StringConstraints(min_length=1, max_length=10), AfterValidator(_rack_face)]


class StrictWireModel(BaseModel):
    """严格校验已定义字段；冗余字段不进入业务模型或规范化摘要。"""

    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


def parse_wms_event_envelope(raw_body: bytes) -> dict[str, Any] | None:
    """消费 ingress 已有界读取的正文；保留完整信封供静态分派与领域校验。"""
    try:
        value = loads_transport_json(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, StrictJsonError):
        return None
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def is_wire_operation_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and value == value.lower() and is_uuid7(value)


def is_wire_operation(value: object) -> TypeGuard[str]:
    return _is_persistable_text(value, 80)


__all__ = [
    "DECISION_PATH",
    "FACT_PATH",
    "MAX_WMS_EVENT_BODY_BYTES",
    "UUIDV7_PATTERN",
    "NonnegativeMilliseconds",
    "OperationId",
    "PositiveInteger",
    "PositiveMilliseconds",
    "RackFaceText",
    "StrictWireModel",
    "is_wire_operation",
    "is_wire_operation_id",
    "parse_wms_event_envelope",
]
