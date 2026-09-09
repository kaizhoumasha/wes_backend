"""人工出库联调台 API 与 SSE。"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.responses import Response, StreamingResponse

from src.app.sys.services.event_stream_service import event_stream_service
from src.app.wms_adapter.outbound_picking.completion_confirm_wire import CompletionConfirmData  # noqa: TC001
from src.app.wms_adapter.outbound_picking.departure_wire import RackDepartureData  # noqa: TC001
from src.app.wms_adapter.outbound_picking.inbound_batch_wire import BinInboundBatchData  # noqa: TC001
from src.app.wms_adapter.outbound_picking.manual_bin_admission_wire import ManualBinAdmissionData  # noqa: TC001
from src.app.wms_adapter.outbound_picking.manual_bin_apply_report_wire import (
    ManualBinApplied,  # noqa: TC001
    ManualBinReconciling,  # noqa: TC001
)
from src.app.wms_adapter.outbound_picking.return_batch_wire import BinReturnBatchData  # noqa: TC001
from src.app.wms_adapter.outbound_picking.wire import BUSINESS_IDENTIFIER_PATTERN, PickingTaskPrepareData
from src.app.workline_integration_debug.contracts import (
    IntegrationDebugProfile,
    IntegrationTransportAction,
    IntegrationTransportActionKind,
    wms_team_guidance,
)
from src.app.workline_integration_debug.service import (
    INTEGRATION_DEBUG_STREAM_CHANNEL,
    CreateIntegrationRun,
    IntegrationDebugConflict,
    IntegrationDebugContractError,
    IntegrationDebugNotFound,
)
from src.core.exceptions import ConflictException, NotFoundException, ServiceUnavailableException, ValidationException
from src.core.rbac import RequirePermission, require_superuser
from src.core.response import ResponseSchemaModel, SuccessCode, response_builder

router = APIRouter(
    prefix="/v1/workline-integration-debug",
    tags=["人工出库联调"],
    dependencies=[Depends(require_superuser)],
)
_RUN_ID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
_TEXT = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
_WORKLINE_CODE = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
_ENVIRONMENT_LABEL = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
_RESOURCE_CODE = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
_IDENTIFIER = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=BUSINESS_IDENTIFIER_PATTERN),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRunRequest(_StrictModel):
    workline_code: _WORKLINE_CODE
    profile: IntegrationDebugProfile
    environment_label: _ENVIRONMENT_LABEL
    device_code: _RESOURCE_CODE
    rack_id: _RESOURCE_CODE | None = None


class VersionRequest(_StrictModel):
    expected_version: int = Field(ge=0)


class BindTaskRequest(VersionRequest):
    task_id: _TEXT


class Point2ScanRequest(VersionRequest):
    bin_code: _IDENTIFIER
    scanned_at: int = Field(gt=0)


class ClientActionRequest(VersionRequest):
    client_request_id: _TEXT


class RefreshWmsActionRequest(ClientActionRequest):
    pass


class PrepareTaskRequest(ClientActionRequest):
    data: PickingTaskPrepareData


class RetryWmsActionRequest(ClientActionRequest):
    wms_non_receipt_confirmed: Literal[True]
    data: PickingTaskPrepareData


class RefreshTransportActionRequest(ClientActionRequest):
    pass


class BinInboundBatchRequest(ClientActionRequest):
    data: BinInboundBatchData


class BinReturnBatchRequest(ClientActionRequest):
    data: BinReturnBatchData


class RackDepartureRequest(ClientActionRequest):
    data: RackDepartureData


class WorkAdmissionRequest(ClientActionRequest):
    data: ManualBinAdmissionData


class TaskCompletionRequest(ClientActionRequest):
    data: CompletionConfirmData


class BindCompletionRequest(VersionRequest):
    operation_id: _TEXT


class RackMovePosition(_StrictModel):
    kind: Literal["RACK", "ZONE", "RACK_POSITION"]
    location_code: _TEXT


class BinMovePosition(_StrictModel):
    kind: Literal["HANDOFF_POSITION", "RACK_BIN_SLOT"]
    location_code: _TEXT | None = None
    rack_id: _TEXT | None = None
    rack_face: _TEXT | None = None
    slot_id: _TEXT | None = None


class TransportActionRequest(ClientActionRequest):
    kind: IntegrationTransportActionKind
    rack_id: _TEXT
    source: RackMovePosition | BinMovePosition
    target: RackMovePosition | BinMovePosition | None = None
    rcs_template_id: Literal["CTU01", "CTU02", "CTU03", "F01"]
    target_face: _TEXT | None = None
    bin_code: _TEXT | None = None


class DeviceActionRequest(ClientActionRequest):
    device_code: _TEXT
    task_type: _TEXT
    params: dict[str, object]
    timeout_ms: int = Field(ge=100, le=600_000)
    reason: _TEXT


class CompletionApplyReportRequest(ClientActionRequest):
    data: Annotated[ManualBinApplied | ManualBinReconciling, Field(discriminator="apply_result")]


class ConfirmPhaseRequest(VersionRequest):
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class CloseRunRequest(VersionRequest):
    wms_cleanup_confirmed: bool
    site_cleanup_confirmed: bool


class IntegrationRunStepResponse(_StrictModel):
    ordinal: int
    phase: str
    status: str
    client_request_id: str | None
    operation: str | None
    operation_id: str | None
    wms_confirmation_id: int | None
    transport_task_id: str | None
    device_command_code: str | None
    request: dict[str, Any]
    result: dict[str, Any]
    reason_code: str | None
    created_at: str


class IntegrationRunResponse(_StrictModel):
    run_id: str
    workline_id: int
    workline_code: str
    scenario_key: Literal["manual_outbound_picking@v1"]
    expected_plugin_key: Literal["manual_bin_processing"]
    profile: IntegrationDebugProfile
    environment_label: str
    operator_user_id: int
    status: str
    current_phase: str
    version: int
    task_id: str | None
    issued_operation_id: str | None
    bin_code: str | None
    device_code: str | None
    rack_id: str | None
    plan_resources: dict[str, Any] | None
    site_configuration: dict[str, Any]
    operation_context: dict[str, Any]
    attention_code: str | None
    attention_detail: str | None
    wms_cleanup_confirmed: bool
    site_cleanup_confirmed: bool
    created_at: str
    updated_at: str
    steps: list[IntegrationRunStepResponse]


def _service(request: Request):  # type: ignore[no-untyped-def]
    runtime = getattr(request.app.state, "workline_integration_debug_runtime", None)
    if runtime is None:
        raise ServiceUnavailableException("人工出库联调 runtime 不可用")
    return runtime.service


def _success(data: Any, *, accepted: bool = False) -> ResponseSchemaModel[Any]:
    return cast(
        "ResponseSchemaModel[Any]",
        response_builder.success(data=data, code=SuccessCode.ACCEPTED if accepted else SuccessCode.SUCCESS),
    )


async def _domain_call(call):  # type: ignore[no-untyped-def]
    try:
        return await call
    except IntegrationDebugNotFound as error:
        raise NotFoundException(resource_type="IntegrationRun", resource_id=str(error)) from error
    except IntegrationDebugConflict as error:
        raise ConflictException(str(error)) from error
    except (IntegrationDebugContractError, ValueError) as error:
        raise ValidationException(str(error), code="2004", status_code=400) from error


@router.post(
    "/runs",
    summary="[ops:workline-integration-debug:operate] 创建人工出库联调 run",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:create"))],
)
async def create_run(request: Request, payload: CreateRunRequest) -> ResponseSchemaModel[IntegrationRunResponse]:
    result = await _domain_call(
        _service(request).create_run(
            CreateIntegrationRun(
                workline_code=payload.workline_code,
                profile=payload.profile,
                environment_label=payload.environment_label,
                device_code=payload.device_code,
                rack_id=payload.rack_id,
            ),
            actor_id=request.state.user_id,
        )
    )
    return _success(result, accepted=True)


@router.get(
    "/runs",
    summary="[ops:workline-integration-debug:read] 查询人工出库联调 run",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:list"))],
)
async def list_runs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseSchemaModel[list[IntegrationRunResponse]]:
    return _success(await _domain_call(_service(request).list_runs(limit=limit)))


@router.get(
    "/runs/stream",
    summary="[ops:workline-integration-debug:read] 实时订阅人工出库联调状态",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:stream"))],
)
async def stream_runs(request: Request) -> StreamingResponse:
    publisher = getattr(request.app.state, "workline_integration_debug_event_stream", event_stream_service)

    async def events():  # type: ignore[no-untyped-def]
        async for envelope in publisher.subscribe(INTEGRATION_DEBUG_STREAM_CHANNEL, timeout_seconds=25.0):
            if envelope is None:
                yield ": heartbeat\n\n"
                continue
            if envelope.get("type") != "workline_integration_debug.updated":
                continue
            yield f"event: workline_integration_debug.updated\ndata: {json.dumps(envelope['payload'], ensure_ascii=False)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/runs/{run_id}",
    summary="[ops:workline-integration-debug:read] 查看人工出库联调 run",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:read"))],
)
async def get_run(
    request: Request,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(await _domain_call(_service(request).get_run(run_id)))


@router.post(
    "/runs/{run_id}/bind-task",
    summary="[ops:workline-integration-debug:operate] 绑定 WMS MANUAL PickingTask",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:bind-task"))],
)
async def bind_task(
    request: Request,
    payload: BindTaskRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).bind_task(
                run_id,
                task_id=payload.task_id,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        )
    )


@router.post(
    "/runs/{run_id}/point2-scan",
    summary="[ops:workline-integration-debug:operate] 记录 point2 实际扫码",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:point2-scan"))],
)
async def point2_scan(
    request: Request,
    payload: Point2ScanRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).record_point2_scan(
                run_id,
                bin_code=payload.bin_code,
                scanned_at=payload.scanned_at,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        )
    )


@router.post(
    "/runs/{run_id}/wms/prepare",
    summary="[ops:workline-integration-debug:operate] 为所选任务发送 PickingTask prepare Operation",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:prepare-task"))],
)
async def send_task_prepare(
    request: Request,
    payload: PrepareTaskRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).send_task_prepare(
                run_id,
                client_request_id=payload.client_request_id,
                request_data=payload.data,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        ),
        accepted=True,
    )


@router.post(
    "/runs/{run_id}/plan/refresh",
    summary="[ops:workline-integration-debug:operate] 读取已应用的 plan_delta 资源并进入货架搬运",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:refresh-plan"))],
)
async def refresh_plan(
    request: Request,
    payload: VersionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).refresh_plan_resources(
                run_id,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        )
    )


@router.post(
    "/runs/{run_id}/wms/work-admission",
    summary="[ops:workline-integration-debug:operate] 发送人工 Bin 任务准入 Operation",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:work-admission"))],
)
async def send_work_admission(
    request: Request,
    payload: WorkAdmissionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).send_work_admission(
                run_id,
                client_request_id=payload.client_request_id,
                request_data=payload.data,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        ),
        accepted=True,
    )


@router.post(
    "/runs/{run_id}/wms/bin-inbound-batch",
    summary="[ops:workline-integration-debug:operate] 请求五层货架入站料箱批次",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:bin-inbound-batch"))],
)
async def send_bin_inbound_batch(
    request: Request,
    payload: BinInboundBatchRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).send_bin_inbound_batch(
                run_id,
                client_request_id=payload.client_request_id,
                request_data=payload.data,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        ),
        accepted=True,
    )


@router.post(
    "/runs/{run_id}/wms/bin-return-batch",
    summary="[ops:workline-integration-debug:operate] 请求回流 Bin 的目标槽位",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:bin-return-batch"))],
)
async def send_bin_return_batch(
    request: Request,
    payload: BinReturnBatchRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).send_bin_return_batch(
                run_id,
                client_request_id=payload.client_request_id,
                request_data=payload.data,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        ),
        accepted=True,
    )


@router.post(
    "/runs/{run_id}/wms/rack-departure",
    summary="[ops:workline-integration-debug:operate] 请求货架离场目的地",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:rack-departure"))],
)
async def send_rack_departure(
    request: Request,
    payload: RackDepartureRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).send_rack_departure(
                run_id,
                client_request_id=payload.client_request_id,
                request_data=payload.data,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        ),
        accepted=True,
    )


@router.post(
    "/runs/{run_id}/wms/task-completion",
    summary="[ops:workline-integration-debug:operate] 请求 PickingTask 完成确认",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:task-completion"))],
)
async def send_task_completion(
    request: Request,
    payload: TaskCompletionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).send_task_completion_confirm(
                run_id,
                client_request_id=payload.client_request_id,
                request_data=payload.data,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        ),
        accepted=True,
    )


@router.post(
    "/runs/{run_id}/wms/refresh",
    summary="[ops:workline-integration-debug:operate] 按持久化 WMS 响应推进联调状态",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:refresh-wms"))],
)
async def refresh_wms_action(
    request: Request,
    payload: RefreshWmsActionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).refresh_wms_action(
                run_id,
                client_request_id=payload.client_request_id,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        )
    )


@router.post(
    "/runs/{run_id}/wms/retry",
    summary="[ops:workline-integration-debug:operate] 确认 WMS 未接收并重发 prepare；参数变更时使用新身份",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:retry-wms"))],
)
async def retry_wms_action(
    request: Request,
    payload: RetryWmsActionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).retry_wms_action(
                run_id,
                client_request_id=payload.client_request_id,
                wms_non_receipt_confirmed=payload.wms_non_receipt_confirmed,
                request_data=payload.data,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        ),
        accepted=True,
    )


@router.post(
    "/runs/{run_id}/wms/bind-completion",
    summary="[ops:workline-integration-debug:operate] 绑定已接收的人工 Bin 完成决定",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:bind-completion"))],
)
async def bind_completion(
    request: Request,
    payload: BindCompletionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).bind_work_completion(
                run_id,
                operation_id=payload.operation_id,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        )
    )


@router.post(
    "/runs/{run_id}/wms/completion-apply-report",
    summary="[ops:workline-integration-debug:operate] 发送完成决定应用结果 Operation",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:completion-apply-report"))],
)
async def send_apply_report(
    request: Request,
    payload: CompletionApplyReportRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).send_completion_apply_report(
                run_id,
                client_request_id=payload.client_request_id,
                request_data=payload.data,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        ),
        accepted=True,
    )


@router.post(
    "/runs/{run_id}/transport",
    summary="[ops:workline-integration-debug:operate] 模拟或创建真实 Transport 调试动作",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:transport"))],
)
async def create_transport(
    request: Request,
    payload: TransportActionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    action = IntegrationTransportAction(
        kind=payload.kind,
        client_request_id=payload.client_request_id,
        rack_id=payload.rack_id,
        source=payload.source.model_dump(exclude_none=True),
        target=payload.target.model_dump(exclude_none=True) if payload.target is not None else {},
        rcs_template_id=payload.rcs_template_id,
        target_face=payload.target_face,
        bin_code=payload.bin_code,
    )
    return _success(
        await _domain_call(
            _service(request).create_transport_action(
                run_id,
                action=action,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        ),
        accepted=True,
    )


@router.post(
    "/runs/{run_id}/device-command",
    summary="[ops:workline-integration-debug:operate] 使用 Run 冻结设备创建 ECS 调试命令",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:device-command"))],
)
async def create_device_command(
    request: Request,
    payload: DeviceActionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).create_device_action(
                run_id,
                client_request_id=payload.client_request_id,
                device_code=payload.device_code,
                task_type=payload.task_type,
                params=payload.params,
                timeout_ms=payload.timeout_ms,
                reason=payload.reason,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        ),
        accepted=True,
    )


@router.post(
    "/runs/{run_id}/device-command/refresh",
    summary="[ops:workline-integration-debug:operate] 刷新 DeviceCommand 权威终态",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:refresh-device"))],
)
async def refresh_device_command(
    request: Request,
    payload: RefreshTransportActionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).refresh_device_action(
                run_id,
                client_request_id=payload.client_request_id,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        )
    )


@router.post(
    "/runs/{run_id}/transport/refresh",
    summary="[ops:workline-integration-debug:operate] 刷新 Transport 权威终态",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:refresh-transport"))],
)
async def refresh_transport(
    request: Request,
    payload: RefreshTransportActionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).refresh_transport_action(
                run_id,
                client_request_id=payload.client_request_id,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        )
    )


@router.post(
    "/runs/{run_id}/confirm-phase",
    summary="[ops:workline-integration-debug:operate] 记录现场步骤人工确认并推进",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:confirm-phase"))],
)
async def confirm_phase(
    request: Request,
    payload: ConfirmPhaseRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).confirm_current_phase(
                run_id,
                note=payload.note,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        )
    )


@router.post(
    "/runs/{run_id}/complete",
    summary="[ops:workline-integration-debug:operate] 标记本轮联调完成",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:complete"))],
)
async def complete_run(
    request: Request,
    payload: VersionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).mark_completed(
                run_id,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        )
    )


@router.post(
    "/runs/{run_id}/takeover",
    summary="[ops:workline-integration-debug:operate] 接管联调 run 操作权",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:takeover"))],
)
async def takeover_run(
    request: Request,
    payload: VersionRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).takeover(
                run_id,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        )
    )


@router.post(
    "/runs/{run_id}/close",
    summary="[ops:workline-integration-debug:operate] 记录双方人工清理并关闭 run",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:close"))],
)
async def close_run(
    request: Request,
    payload: CloseRunRequest,
    run_id: Annotated[_RUN_ID, Path()],
) -> ResponseSchemaModel[IntegrationRunResponse]:
    return _success(
        await _domain_call(
            _service(request).close_run(
                run_id,
                wms_cleanup_confirmed=payload.wms_cleanup_confirmed,
                site_cleanup_confirmed=payload.site_cleanup_confirmed,
                expected_version=payload.expected_version,
                actor_id=request.state.user_id,
            )
        )
    )


@router.get(
    "/runs/{run_id}/export",
    summary="[ops:workline-integration-debug:read] 导出 WMS C# 联调证据包",
    dependencies=[Depends(RequirePermission("ops:workline-integration-debug:export"))],
)
async def export_run(request: Request, run_id: Annotated[_RUN_ID, Path()]) -> Response:
    snapshot = await _domain_call(_service(request).get_run(run_id))
    content = json.dumps(
        {
            "run": snapshot,
            "wms_operations": [
                {
                    "direction": "WMS_TO_WES",
                    "method": "POST",
                    "path": "/api/v1/wms/events",
                    "operation": "outbound.picking_task.issued@v1",
                    "wms_action": "先发送任务；HTTP 202 RECEIVED 只证明 WES 已可靠接收，等待操作员选择该 task_id。",
                },
                {
                    "direction": "WES_TO_WMS",
                    "method": "POST",
                    "path": "/api/v1/wes/decisions",
                    "operation": "outbound.picking_task.prepare@v1",
                    "wms_action": "返回 PREPARE_ACCEPTED 后，才能发送该 task_id 的初始 plan_delta。",
                },
                {
                    "direction": "WMS_TO_WES",
                    "method": "POST",
                    "path": "/api/v1/wms/events",
                    "operation": "outbound.picking_task.plan_delta@v1",
                    "wms_action": "初始 plan_revision=1，必须包含 target_rack；后续 revision 严格连续且 identity/body 不漂移。",
                },
                {
                    "direction": "WES_TO_WMS",
                    "method": "POST",
                    "path": "/api/v1/wes/decisions",
                    "operation": "outbound.bin.inbound_batch@v1",
                    "wms_action": "只对 plan_delta 中已到位的五层料箱架返回 READY bins、NO_BATCH 或 RACK_FACE_DONE。",
                },
                {
                    "direction": "WES_TO_WMS",
                    "method": "POST",
                    "path": "/api/v1/wes/decisions",
                    "operation": "outbound.manual_bin.work_admission_decide@v1",
                    "wms_action": "按 operation_id 幂等保存首次完整 DECIDED 响应；WAIT 重求值使用新 identity。",
                },
                {
                    "direction": "WMS_TO_WES",
                    "method": "POST",
                    "path": "/api/v1/wms/events",
                    "operation": "outbound.manual_bin.work_completed@v1",
                    "wms_action": "PDA 子任务与 Bin 最终结果同事务提交后发送；503 使用原 identity 和原 body 重试。",
                },
                {
                    "direction": "WES_TO_WMS",
                    "method": "POST",
                    "path": "/api/v1/wes/facts",
                    "operation": "outbound.manual_bin.completion_apply_report@v1",
                    "wms_action": "按 completion_operation_id + apply_revision 保存应用结果；同报告 identity 内容不得漂移。",
                },
                {
                    "direction": "WES_TO_WMS",
                    "method": "POST",
                    "path": "/api/v1/wes/decisions",
                    "operation": "outbound.bin.return_batch@v1",
                    "wms_action": "按 FIFO 候选顺序返回 READY moves 或 NO_BATCH；不得重排 sequence_no。",
                },
                {
                    "direction": "WES_TO_WMS",
                    "method": "POST",
                    "path": "/api/v1/wes/decisions",
                    "operation": "outbound.rack.departure_decide@v1",
                    "wms_action": "READY 必须返回 rack_destination；WAIT 按 retry_after_ms 后用新 operation_id 重求值。",
                },
                {
                    "direction": "WES_TO_WMS",
                    "method": "POST",
                    "path": "/api/v1/wes/decisions",
                    "operation": "outbound.picking_task.completion_confirm@v1",
                    "wms_action": "按 last_applied_plan_revision 返回 COMPLETED、BUSINESS_IN_PROGRESS 或 PLAN_REVISION_STALE。",
                },
            ],
            "wms_csharp_guidance": {
                code: wms_team_guidance(code) for code in ("WAIT", "UNAVAILABLE", "CONFLICT", "RECEIVED")
            },
            "csharp6_rules": [
                "operation_id 使用 UUID 字符串；UTC Unix 毫秒时间使用 long，禁止 int。",
                "Json.NET DTO 明确 JsonProperty；缺字段、null、未知字段、数字字符串和枚举大小写漂移都视为合同错误。",
                "技术重试缓存首次序列化 JSON 字符串；重新创建 StringContent，但不得重新生成 operation_id、timestamp 或 body。",
            ],
            "csharp6_httpclient_example": (
                "var frozenJson = JsonConvert.SerializeObject(envelope, Formatting.None);\n"
                'using (var content = new StringContent(frozenJson, Encoding.UTF8, "application/json"))\n'
                "using (var response = await httpClient.PostAsync(path, content))\n"
                "{\n"
                "    var responseJson = await response.Content.ReadAsStringAsync();\n"
                "    // 503: 用 frozenJson 和原 identity 重试；409: 停止并核对首次请求。\n"
                "}"
            ),
            "acceptance_boundary": "HTTP/ACK、模拟成功或数据库状态均不代表真实设备物理完成或现场验收。",
        },
        ensure_ascii=False,
        indent=2,
    ).encode()
    return Response(
        content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="manual-outbound-{run_id}.json"'},
    )


__all__ = ["router"]
