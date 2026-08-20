"""
回调日志查询 API 路由 (Callback Log Query API Routes)

提供回调日志资源查询接口，用于监控和问题排查。
"""

from typing import Any, cast

from fastapi import APIRouter, Depends, Query, status

from src.app.callback.models import (
    CallbackLog,
    CallbackLogResponse,
    CallbackLogSubjectResponse,
    CallbackLogTraceResponse,
    build_callback_log_response,
)
from src.app.callback.services import callback_log_service
from src.core.base_api import BaseAPI
from src.core.rbac import RequirePermission
from src.core.response import DEFAULT_NOT_FOUND, ResponseSchemaModel, response_builder
from src.database.dependencies import AsyncSessionDep


def register_callback_log_routes(router: APIRouter, api: BaseAPI[Any, Any, Any]) -> None:
    """注册回调日志专用查询入口。"""

    request_detail_permission = api.get_permission_code("detail-by-request-id")
    trace_list_permission = api.get_permission_code("list-by-trace-id")
    subject_list_permission = api.get_permission_code("list-by-subject-code")

    @router.get(
        "/request/{request_id}",
        response_model=ResponseSchemaModel[CallbackLogResponse],
        status_code=status.HTTP_200_OK,
        summary=f"[{request_detail_permission}] 根据请求 ID 查询回调日志",
        dependencies=[Depends(RequirePermission(request_detail_permission))] if request_detail_permission else None,
        description="根据 request_id 查询单条回调日志记录",
    )
    async def get_by_request_id(
        request_id: str,
        db: AsyncSessionDep,
    ) -> ResponseSchemaModel[CallbackLogResponse]:
        """
        根据 request_id 查询单条回调日志

        用于追踪特定请求的回调记录。
        """
        log = await callback_log_service.get_by_request_id(db, request_id)
        if not log:
            return cast(
                "ResponseSchemaModel[CallbackLogResponse]",
                response_builder.fail(
                    code=DEFAULT_NOT_FOUND,
                    message=f"回调日志不存在: request_id={request_id}",
                ),
            )
        return cast(
            "ResponseSchemaModel[CallbackLogResponse]",
            response_builder.success(data=build_callback_log_response(log)),
        )

    @router.get(
        "/trace/{trace_id}",
        response_model=ResponseSchemaModel[CallbackLogTraceResponse],
        status_code=status.HTTP_200_OK,
        summary=f"[{trace_list_permission}] 根据 Trace ID 查询回调日志",
        dependencies=[Depends(RequirePermission(trace_list_permission))] if trace_list_permission else None,
        description="根据 trace_id 查询所有相关的回调日志（用于串联整个流程）",
    )
    async def get_by_trace_id(
        trace_id: str,
        db: AsyncSessionDep,
    ) -> ResponseSchemaModel[CallbackLogTraceResponse]:
        """
        根据 trace_id 查询所有相关的回调日志

        用于追踪整个业务流程的回调链路。
        """
        logs = await callback_log_service.get_by_trace_id(db, trace_id)
        return cast(
            "ResponseSchemaModel[CallbackLogTraceResponse]",
            response_builder.success(data=CallbackLogTraceResponse.build(trace_id, logs)),
        )

    @router.get(
        "/subject/{subject_code}",
        response_model=ResponseSchemaModel[CallbackLogSubjectResponse],
        status_code=status.HTTP_200_OK,
        summary=f"[{subject_list_permission}] 根据回调主体编码查询回调日志",
        dependencies=[Depends(RequirePermission(subject_list_permission))] if subject_list_permission else None,
        description="查询指定回调主体最近的回调记录。设备回调主体通常是 device_code。",
    )
    async def get_by_subject_code(
        subject_code: str,
        db: AsyncSessionDep,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> ResponseSchemaModel[CallbackLogSubjectResponse]:
        """
        根据回调主体编码查询最近的回调日志

        用于监控设备/外部系统回调历史和排查问题。
        """
        logs = await callback_log_service.get_by_subject_code(db, subject_code, limit)
        return cast(
            "ResponseSchemaModel[CallbackLogSubjectResponse]",
            response_builder.success(data=CallbackLogSubjectResponse.build(subject_code, logs)),
        )


# ==================== 资源 API ====================

callback_log_api = BaseAPI(
    module_name="callback",
    model=CallbackLog,
    service=callback_log_service,
    response_schema=CallbackLogResponse,
    prefix="/logs",
    tags=["Callback"],
    gen_create=False,
    gen_update=False,
    gen_delete=False,
    permission_resource="callback_log",
    custom_routes=[register_callback_log_routes],
)

router = callback_log_api.router


# ==================== 导出 ====================


__all__ = ["router"]
