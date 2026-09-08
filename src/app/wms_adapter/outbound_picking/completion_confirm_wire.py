"""PickingTask 完成确认的严格请求与封闭决定。"""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, TypeAdapter

from src.app.wms_adapter.outbound_picking.response_wire import ConflictResponse, RejectedResponse, UnavailableResponse
from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN
from src.app.wms_adapter.wire_common import NonnegativeMilliseconds, OperationId, StrictWireModel
from src.app.wms_diagnostics.observation import WmsCallObservation, observed_contract_error, validate_observed

COMPLETION_CONFIRM_OPERATION = "outbound.picking_task.completion_confirm@v1"


class CompletionConfirmData(StrictWireModel):
    task_id: Annotated[str, StringConstraints(pattern=BUSINESS_IDENTIFIER_PATTERN)]
    last_applied_plan_revision: Annotated[int, Field(ge=0, le=2**63 - 1)]


class CompletionConfirmRequest(StrictWireModel):
    operation_id: OperationId
    operation: Literal["outbound.picking_task.completion_confirm@v1"]
    timestamp: NonnegativeMilliseconds
    data: CompletionConfirmData


class PickingTaskPlanRevisionStale(StrictWireModel):
    result: Literal["PLAN_REVISION_STALE"]
    current_plan_revision: Annotated[int, Field(ge=1, le=2**63 - 1)]


class PickingTaskCompleted(StrictWireModel):
    result: Literal["COMPLETED"]


class PickingTaskBusinessInProgress(StrictWireModel):
    result: Literal["BUSINESS_IN_PROGRESS"]
    retry_after_ms: Annotated[int, Field(ge=1, le=60000)]


class CompletionConfirmDecidedResponse(StrictWireModel):
    operation_id: OperationId
    code: Literal["DECIDED"]
    timestamp: NonnegativeMilliseconds
    data: Annotated[
        PickingTaskPlanRevisionStale | PickingTaskCompleted | PickingTaskBusinessInProgress,
        Field(discriminator="result"),
    ]


type CompletionConfirmResponse = (
    CompletionConfirmDecidedResponse | UnavailableResponse | ConflictResponse | RejectedResponse
)

_RESPONSE_ADAPTERS = {
    (200, "DECIDED"): TypeAdapter(CompletionConfirmDecidedResponse),
    (503, "UNAVAILABLE"): TypeAdapter(UnavailableResponse),
    (409, "CONFLICT"): TypeAdapter(ConflictResponse),
    (422, "REJECTED"): TypeAdapter(RejectedResponse),
}


def parse_completion_confirm_request(
    value: object, *, observation: WmsCallObservation | None = None
) -> CompletionConfirmRequest:
    return validate_observed(CompletionConfirmRequest, value, observation=observation, side="request")


def parse_completion_confirm_response(
    status_code: int,
    value: object,
    *,
    request: CompletionConfirmRequest | None = None,
    observation: WmsCallObservation | None = None,
) -> CompletionConfirmResponse:
    code = value.get("code") if isinstance(value, dict) else None
    adapter = _RESPONSE_ADAPTERS.get((status_code, code)) if isinstance(code, str) else None
    if adapter is None:
        raise observed_contract_error(observation, "HTTP status 与 completion confirm response code 不匹配")
    response = validate_observed(adapter, value, observation=observation, side="response")
    if request is not None and response.operation_id != request.operation_id:
        raise observed_contract_error(
            observation, "响应 operation_id 必须匹配请求", path=("operation_id",), expected_value=request.operation_id
        )
    if (
        request is not None
        and isinstance(response, CompletionConfirmDecidedResponse)
        and isinstance(response.data, PickingTaskPlanRevisionStale)
        and response.data.current_plan_revision <= request.data.last_applied_plan_revision
    ):
        raise observed_contract_error(observation, "current_plan_revision 必须高于请求版本")
    return response
