"""工作线 Trace 查询 API。"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, status

from src.app.workline.models.runtime import (
    RuntimeTraceListResponse,
    TraceCallbackLogItem,
    TraceCommandItem,
    TraceContextResponse,
    TraceDetailResponse,
    TraceDiagnosticItem,
    TraceInboxItem,
    TraceOutboxItem,
    TraceOverviewSummary,
    TraceQueryRequest,
    TraceSessionItem,
    TraceTimelineItem,
)
from src.app.workline.services import runtime_query_service, trace_query_service
from src.core.rbac import RequirePermission
from src.core.response import ResponseSchemaModel, response_builder
from src.database.dependencies import AsyncSessionDep

router = APIRouter(tags=["工作线 Trace"])


def _enum_str(value: Any) -> str | None:
    return getattr(value, "value", value) if value is not None else None


def _build_trace_response(result: Any) -> TraceDetailResponse:
    session = result.session
    latest_timeline = result.timelines[-1] if result.timelines else None

    summary = TraceOverviewSummary(
        callback_logs=len(result.callback_logs),
        inboxes=len(result.inboxes),
        commands=len(result.commands),
        outboxes=len(result.outboxes),
        timelines=len(result.timelines),
        diagnostics=len(result.diagnostics),
        session_status=_enum_str(getattr(session, "status", None)) if session is not None else None,
        step_code=getattr(session, "step_code", None) if session is not None else None,
        current_wait_type=getattr(session, "current_wait_type", None) if session is not None else None,
        latest_timeline_action=_enum_str(getattr(latest_timeline, "action_type", None)) if latest_timeline else None,
        latest_timeline_status=_enum_str(getattr(latest_timeline, "status", None)) if latest_timeline else None,
        latest_timeline_message=getattr(latest_timeline, "message", None) if latest_timeline else None,
    )

    session_item = None
    if session is not None:
        session_item = TraceSessionItem(
            id=session.id,
            session_code=session.session_code,
            workline_id=session.workline_id,
            plugin_key=session.plugin_key,
            run_mode=_enum_str(session.run_mode) or "UNKNOWN",
            business_key=session.business_key,
            barcode=session.barcode,
            status=_enum_str(session.status) or "UNKNOWN",
            step_code=session.step_code,
            correlation_id=session.correlation_id,
            started_at=session.started_at,
            ended_at=session.ended_at,
            current_wait_type=session.current_wait_type,
            current_wait_token=session.current_wait_token,
            waiting_since=session.waiting_since,
            deadline_at=session.deadline_at,
            awaiting_command_id=session.awaiting_command_id,
            failure_domain=session.failure_domain,
            failure_code=session.failure_code,
            failure_message=session.failure_message,
            ingress_count=session.ingress_count,
            last_request_id=session.last_request_id,
            last_ingress_at=session.last_ingress_at,
            last_inbox_id=session.last_inbox_id,
            context_json=session.context_json,
        )

    return TraceDetailResponse(
        trace=TraceContextResponse(**result.trace.as_dict()),
        summary=summary,
        session=session_item,
        callback_logs=[
            TraceCallbackLogItem(
                id=item.id,
                callback_type=item.callback_type,
                device_id=item.device_id,
                request_id=item.request_id,
                correlation_id=item.correlation_id,
                response_status=item.response_status,
                response_time_ms=item.response_time_ms,
                error_message=item.error_message,
                ingress_outcome=item.ingress_outcome,
                failure_stage=item.failure_stage,
                request_body=item.request_body,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in result.callback_logs
        ],
        inboxes=[
            TraceInboxItem(
                id=item.id,
                kind=_enum_str(item.kind) or "UNKNOWN",
                source_system=_enum_str(item.source_system) or "UNKNOWN",
                source_message_id=item.source_message_id,
                workline_id=item.workline_id,
                device_id=item.device_id,
                command_id=item.command_id,
                session_id=item.session_id,
                correlation_id=item.correlation_id,
                status=_enum_str(item.status) or "UNKNOWN",
                received_at=item.received_at,
                processed_at=item.processed_at,
                attempt_count=item.attempt_count,
                max_attempts=item.max_attempts,
                next_retry_at=item.next_retry_at,
                error_message=item.error_message,
                payload_json=item.payload_json,
            )
            for item in result.inboxes
        ],
        commands=[
            TraceCommandItem(
                id=item.id,
                device_id=item.device_id,
                command_code=item.command_code,
                correlation_id=item.correlation_id,
                workline_id=item.workline_id,
                session_id=item.session_id,
                task_type=_enum_str(item.task_type) or "UNKNOWN",
                status=_enum_str(item.status) or "UNKNOWN",
                result=_enum_str(item.result),
                retry_count=item.retry_count,
                sent_at=item.sent_at,
                ack_received_at=item.ack_received_at,
                completed_at=item.completed_at,
                ack_code=item.ack_code,
                ack_message=item.ack_message,
                ack_trace_id=item.ack_trace_id,
                step_code=item.step_code,
                params=item.params,
                result_data=item.result_data,
                error_detail=item.error_detail,
                duration_ms=item.get_duration_ms(),
            )
            for item in result.commands
        ],
        outboxes=[
            TraceOutboxItem(
                id=item.id,
                session_id=item.session_id,
                workline_id=item.workline_id,
                dispatch_type=_enum_str(item.dispatch_type) or "UNKNOWN",
                dispatch_key=item.dispatch_key,
                target_type=_enum_str(item.target_type) or "UNKNOWN",
                target_code=item.target_code,
                status=_enum_str(item.status) or "UNKNOWN",
                attempt_count=item.attempt_count,
                next_retry_at=item.next_retry_at,
                last_error=item.last_error,
                created_at=item.created_at,
                sent_at=item.sent_at,
                finished_at=item.finished_at,
                payload_json=item.payload_json,
            )
            for item in result.outboxes
        ],
        timelines=[
            TraceTimelineItem(
                id=item.id,
                session_id=item.session_id,
                workline_id=item.workline_id,
                correlation_id=item.correlation_id,
                seq_no=item.seq_no,
                occurred_at=item.occurred_at,
                stage=_enum_str(item.stage) or "UNKNOWN",
                action_type=_enum_str(item.action_type) or "UNKNOWN",
                actor_type=_enum_str(item.actor_type) or "UNKNOWN",
                actor_code=item.actor_code,
                from_status=item.from_status,
                to_status=item.to_status,
                status=_enum_str(item.status) or "UNKNOWN",
                failure_domain=item.failure_domain,
                message=item.message,
                payload_json=cast("dict[str, Any] | None", item.payload_json),
                related_inbox_id=item.related_inbox_id,
                related_command_id=item.related_command_id,
            )
            for item in result.timelines
        ],
        diagnostics=[
            TraceDiagnosticItem(
                request_id=item.request_id,
                correlation_id=item.correlation_id,
                session_id=item.session_id,
                inbox_id=item.inbox_id,
                outbox_id=item.outbox_id,
                command_code=item.command_code,
                device_code=item.device_code,
                workline_id=item.workline_id,
                workline_code=item.workline_code,
                plugin_key=item.plugin_key,
                canonical_event_type=item.canonical_event_type,
                transition=item.transition,
                extra=item.extra,
            )
            for item in result.diagnostics
        ],
    )


@router.get(
    "/request/{request_id}",
    summary="[biz:workline:list] 根据 request_id 查询 Trace",
    response_model=ResponseSchemaModel[TraceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_by_request_id(request_id: str, db: AsyncSessionDep) -> ResponseSchemaModel[TraceDetailResponse]:
    result = await trace_query_service.by_request_id(db, request_id)
    return cast("ResponseSchemaModel[TraceDetailResponse]", response_builder.success(data=_build_trace_response(result)))


@router.get(
    "/correlation/{correlation_id}",
    summary="[biz:workline:list] 根据 correlation_id 查询 Trace",
    response_model=ResponseSchemaModel[TraceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_by_correlation_id(
    correlation_id: str,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[TraceDetailResponse]:
    result = await trace_query_service.by_correlation_id(db, correlation_id)
    return cast("ResponseSchemaModel[TraceDetailResponse]", response_builder.success(data=_build_trace_response(result)))


@router.get(
    "/session/{session_id}",
    summary="[biz:workline:list] 根据 session_id 查询 Trace",
    response_model=ResponseSchemaModel[TraceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_by_session_id(session_id: int, db: AsyncSessionDep) -> ResponseSchemaModel[TraceDetailResponse]:
    result = await trace_query_service.by_session_id(db, session_id)
    return cast("ResponseSchemaModel[TraceDetailResponse]", response_builder.success(data=_build_trace_response(result)))


@router.get(
    "/command/{command_code}",
    summary="[biz:workline:list] 根据 command_code 查询 Trace",
    response_model=ResponseSchemaModel[TraceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_by_command_code(
    command_code: str,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[TraceDetailResponse]:
    result = await trace_query_service.by_command_code(db, command_code)
    return cast("ResponseSchemaModel[TraceDetailResponse]", response_builder.success(data=_build_trace_response(result)))


@router.get(
    "/dispatch/{dispatch_key}",
    summary="[biz:workline:list] 根据 dispatch_key 查询 Trace",
    response_model=ResponseSchemaModel[TraceDetailResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def get_trace_by_dispatch_key(
    dispatch_key: str,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[TraceDetailResponse]:
    result = await trace_query_service.by_dispatch_key(db, dispatch_key)
    return cast("ResponseSchemaModel[TraceDetailResponse]", response_builder.success(data=_build_trace_response(result)))


@router.post(
    "/query",
    summary="[biz:workline:list] Trace 列表查询",
    response_model=ResponseSchemaModel[RuntimeTraceListResponse],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequirePermission("biz:workline:list"))],
)
async def query_trace_list(
    payload: TraceQueryRequest,
    db: AsyncSessionDep,
) -> ResponseSchemaModel[RuntimeTraceListResponse]:
    result = await runtime_query_service.get_trace_list(db, payload)
    return cast("ResponseSchemaModel[RuntimeTraceListResponse]", response_builder.success(data=result))


__all__ = ["router"]
