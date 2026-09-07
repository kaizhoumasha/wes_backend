"""Picking operation 共同的封闭错误响应；成功响应由各 operation 定义。"""

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from src.app.wms_adapter.wire_common import NonnegativeMilliseconds, OperationId, StrictWireModel


class EmptyResponseData(StrictWireModel):
    pass


class UnavailableResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["UNAVAILABLE"]
    timestamp: NonnegativeMilliseconds
    data: EmptyResponseData


class ConflictData(StrictWireModel):
    reason_code: Literal["IDEMPOTENCY_CONFLICT", "REVISION_CONFLICT", "STATE_CONFLICT", "REFERENCE_CONFLICT"]


class ConflictResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["CONFLICT"]
    timestamp: NonnegativeMilliseconds
    data: ConflictData


class RejectedData(StrictWireModel):
    reason_code: Literal["INVALID_ENVELOPE", "UNSUPPORTED_OPERATION", "INVALID_DATA"]
    field_path: (
        Annotated[str, StringConstraints(min_length=1, max_length=256, pattern=r"^/(?:[^~]|~[01])*$")] | None
    ) = None

    @model_validator(mode="before")
    @classmethod
    def validate_field_path(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and "field_path" in value
            and (value["field_path"] is None or value.get("reason_code") != "INVALID_DATA")
        ):
            raise ValueError("field_path 仅允许 INVALID_DATA，且不得为 null")
        return value


class RejectedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["REJECTED"]
    timestamp: NonnegativeMilliseconds
    data: RejectedData


class BinBatchNoBatch(StrictWireModel):
    result: Literal["NO_BATCH"]
    retry_after_ms: Annotated[int, Field(ge=1, le=60000)]
