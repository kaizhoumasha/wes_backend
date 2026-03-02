"""
设备管理 API

用于管理接入的第三方设备和指令下发

遵循项目零代码架构：
- 使用 BaseAPI 自动生成标准 CRUD 路由
- 使用自定义路由添加特殊业务逻辑
"""

from fastapi import APIRouter, BackgroundTasks, Depends

from src.app.device.models.device import (
    CommandRequest,
    CommandResponse,
    Device,
    DeviceCreate,
    DeviceResponse,
    DeviceUpdate,
)
from src.app.device.services.device_service import DeviceCommandService, device_command_service, device_service
from src.core.base_api import BaseAPI
from src.core.rbac import RequirePermission
from src.core.response import response_builder
from src.database.dependencies import AsyncSessionDep


# ============================================================================
# 设备 CRUD API（使用 BaseAPI 零代码生成）
# ============================================================================

# 使用 BaseAPI 自动生成标准 CRUD 路由
device_base_api = BaseAPI(
    module_name="device",
    model=Device,
    service=device_service,
    create_schema=DeviceCreate,
    update_schema=DeviceUpdate,
    response_schema=DeviceResponse,
    prefix="/devices",
    tags=["设备管理"],
)


def register_device_custom_routes(router: APIRouter, api: BaseAPI) -> None:
    """
    注册设备相关的自定义路由

    Args:
        router: APIRouter 实例
        api: BaseAPI 实例
    """

    # 自定义路由：设备状态查询
    @router.get(
        "/{device_id}/status",
        summary="查询设备状态",
        description="主动查询设备当前状态（调用供应商设备接口）",
        dependencies=[Depends(RequirePermission(api.get_permission_code("status")))],  # type: ignore[arg-type]
    )
    async def get_device_status(
        device_id: str,
        db: AsyncSessionDep,
    ):
        """查询设备状态（调用供应商的 GET /api/v1/device/status 接口）"""
        # 先验证设备存在
        await device_service.get_by_device_id(db, device_id)

        # 调用设备状态接口（供应商实现）
        status_response = await device_command_service.query_device_status(db, device_id)

        if status_response:
            return response_builder.success(data=status_response)
        return response_builder.fail(msg="查询设备状态失败")

    # 自定义路由：设备心跳
    @router.post(
        "/{device_id}/heartbeat",
        summary="设备心跳",
        description="设备主动心跳接口（设备定期调用）",
        dependencies=[Depends(RequirePermission(api.get_permission_code("heartbeat")))],  # type: ignore[arg-type]
    )
    async def device_heartbeat(
        device_id: str,
        db: AsyncSessionDep,
    ):
        """设备心跳"""
        await device_service.heartbeat(db, device_id)
        return response_builder.success(message="Heartbeat ACK")

    # 自定义路由：下发指令
    @router.post(
        "/commands",
        summary="下发指令",
        description="创建并下发指令到设备",
        dependencies=[Depends(RequirePermission(api.get_permission_code("command")))],  # type: ignore[arg-type]
    )
    async def send_command(
        request: CommandRequest,
        background_tasks: BackgroundTasks,
        db: AsyncSessionDep,
    ):
        """下发指令到设备

        **流程**：
        1. 创建指令记录（状态：PENDING）
        2. 后台异步发送指令到设备（调用供应商的 POST /api/v1/device/command）
        3. 设备返回 ACK（状态：ACKED）
        4. 设备完成后回调结果（状态：COMPLETED/FAILED）

        **幂等性**：重复发送相同 command_id 不会重复执行
        """
        # 创建指令记录
        command = await device_command_service.create_command(db, request)

        # 后台发送指令
        async def send_task():
            try:
                await device_command_service.send_command(db, command.command_id)
            except Exception as e:
                from src.core.logger import logger
                logger.error(f"发送指令失败: {command.command_id} -> {e}")

        background_tasks.add_task(send_task)

        return response_builder.success(
            data={
                "command_id": command.command_id,
                "status": command.status,
                "message": "指令已创建，正在发送...",
            }
        )

    # 自定义路由：查询指令状态
    @router.get(
        "/commands/{command_id}",
        summary="查询指令状态",
        description="查询指令执行状态",
        dependencies=[Depends(RequirePermission(api.get_permission_code("command")))],  # type: ignore[arg-type]
    )
    async def get_command_status(
        command_id: str,
        db: AsyncSessionDep,
    ):
        """查询指令状态"""
        command = await device_command_service.repo.get_by_command_id(db, command_id)
        if not command:
            return response_builder.fail(msg="指令不存在")

        return response_builder.success(
            data=CommandResponse(
                id=command.id,
                command_id=command.command_id,
                device_id=command.device_id,
                task_type=command.task_type,
                priority=command.priority,
                timeout_ms=command.timeout_ms,
                params=command.params or {},
                status=command.status,
                sent_at=command.sent_at,
                acked_at=command.acked_at,
                completed_at=command.completed_at,
                result=command.result,
                result_data=command.result_data,
                error_message=command.error_message,
                created_at=command.created_at,
            )
        )

    # 自定义路由：取消指令
    @router.post(
        "/commands/{command_id}/cancel",
        summary="取消指令",
        description="取消正在执行或排队的指令",
        dependencies=[Depends(RequirePermission(api.get_permission_code("command")))],  # type: ignore[arg-type]
    )
    async def cancel_command(
        command_id: str,
        db: AsyncSessionDep,
    ):
        """取消指令（调用供应商的 POST /api/v1/device/cancel）"""
        success = await device_command_service.cancel_command(db, command_id)
        if success:
            return response_builder.success(message="指令已取消")
        return response_builder.fail(msg="指令不存在或无法取消")

    # 自定义路由：重试指令
    @router.post(
        "/commands/{command_id}/retry",
        summary="重试指令",
        description="重试失败的指令",
        dependencies=[Depends(RequirePermission(api.get_permission_code("command")))],  # type: ignore[arg-type]
    )
    async def retry_command(
        command_id: str,
        background_tasks: BackgroundTasks,
        db: AsyncSessionDep,
    ):
        """重试指令"""

        async def retry_task():
            try:
                await device_command_service.retry_command(db, command_id)
            except Exception as e:
                from src.core.logger import logger
                logger.error(f"重试指令失败: {command_id} -> {e}")

        background_tasks.add_task(retry_task)

        return response_builder.success(message="指令正在重试")


# 注册自定义路由到 BaseAPI
device_base_api.add_custom_route(register_device_custom_routes)

# 导出路由器
router = device_base_api.router
