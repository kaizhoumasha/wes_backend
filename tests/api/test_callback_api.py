"""
Callback API 单元测试

测试设备回调 API 的功能：
- POST /api/v1/callback/result - 指令结果回传
- POST /api/v1/callback/event - 设备事件上报

相关文档:
- 白皮书 3.2 节: 第三方设备接入规范
- claudedocs/idempotency_fix_20260317.md: 幂等性控制逻辑修复

注意: 由于循环导入问题（api_security.py ↔ api_auth/__init__.py），
部分测试需要特殊处理。核心功能已在 test_callback_idempotency.py 中验证。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.device.models.command import CommandCallbackResult, CommandResult
from src.app.device.models.event_log import EventRequest, EventType

# ==================== 测试 Fixtures ====================


@pytest.fixture
def db_session():
    """创建 mock 数据库会话（支持 commit 方法）"""
    mock = AsyncMock(spec=AsyncSession)
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    return mock


@pytest.fixture
def mock_request():
    """创建 mock Request 对象"""

    def _create_mock_request(
        client_ip: str = "192.168.1.100",
        path: str = "/api/v1/callback/result",
        user_agent: str = "TestClient",
    ):
        mock_req = MagicMock()
        mock_req.client = MagicMock()
        mock_req.client.host = client_ip
        mock_req.url = MagicMock()
        mock_req.url.path = path
        mock_req.headers = {"User-Agent": user_agent}
        return mock_req

    return _create_mock_request


# ==================== 辅助函数 ====================


def create_callback_result_request(
    command_code: str = "CMD-20250317-001",
    device_code: str = "ARM_01",
    result: CommandResult = CommandResult.SUCCESS,
    finish_time: int = 1702627250000,
    data: dict | None = None,
) -> dict:
    """创建指令结果回调请求"""
    return {
        "command_code": command_code,
        "device_code": device_code,
        "result": result.value,
        "finish_time": finish_time,
        "data": data or {},
    }


def create_event_request(
    device_code: str = "CONVEYOR_01",
    event_type: EventType = EventType.MATERIAL_ARRIVED,
    timestamp: int = 1702627300000,
    data: dict | None = None,
) -> dict:
    """创建设备事件请求"""
    return {
        "device_code": device_code,
        "event_type": event_type.value,
        "timestamp": timestamp,
        "data": data or {},
    }


# ==================== callback/result 测试 ====================


class TestCallbackResultAPI:
    """测试指令结果回调 API"""

    @pytest.mark.asyncio
    async def test_callback_result_success(
        self,
        db_session: AsyncSession,
        mock_request,
    ) -> None:
        """
        测试成功的指令结果回调

        验证:
        - ✅ 返回成功
        - ✅ 响应包含 ack: true
        - ✅ 调用了 handle_callback_result
        - ✅ 记录了 callback_log
        - ✅ 记录了 audit_log
        """
        mock_command = MagicMock()
        mock_command.status = MagicMock()
        mock_command.status.value = "SUCCESS"
        mock_command.get_duration_ms = MagicMock(return_value=100)

        with (
            patch(
                "src.app.callback.v1.callback.inbox_service.create_command_result_inbox",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.v1.callback.device_command_service.handle_callback_result",
                new=AsyncMock(return_value=mock_command),
            ) as mock_handle,
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="test-req-001"),
        ):
            # 在 patch 块内导入（patch 阻止循环导入解析）
            from src.app.callback.v1.callback import callback_result

            request_data = create_callback_result_request()

            response = await callback_result(
                callback=CommandCallbackResult(**request_data),
                request=mock_request(),
                db=db_session,
            )

            assert response["code"] == "1000"
            assert response["data"]["ack"] is True

            mock_handle.assert_called_once()

            mock_log_callback.assert_called_once()
            log_call_kwargs = mock_log_callback.call_args[1]
            assert log_call_kwargs["callback_type"] == "result"
            assert log_call_kwargs["device_id"] == "ARM_01"
            assert log_call_kwargs["response_status"] == 200
            assert log_call_kwargs["error_message"] is None

            mock_audit.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_result_idempotent_duplicate(
        self,
        db_session: AsyncSession,
        mock_request,
    ) -> None:
        """
        测试幂等重复的指令结果回调

        验证:
        - ✅ 返回 200 OK（幂等重复也应返回成功）
        - ✅ 调用了 create_command_result_inbox
        - ✅ **未调用** handle_callback_result（跳过业务处理）
        - ✅ 记录了 callback_log（error_message 标记为幂等重复）
        - ✅ **未记录** audit_log（跳过审计）
        """
        with (
            patch(
                "src.app.callback.v1.callback.inbox_service.create_command_result_inbox",
                new=AsyncMock(side_effect=ValueError("指令结果已存在（幂等键重复）: test-key, 原消息 ID: 123")),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.v1.callback.device_command_service.handle_callback_result",
                new=AsyncMock(),
            ) as mock_handle,
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="test-req-002"),
        ):
            from src.app.callback.v1.callback import callback_result

            request_data = create_callback_result_request()

            response = await callback_result(
                callback=CommandCallbackResult(**request_data),
                request=mock_request(),
                db=db_session,
            )

            assert response["code"] == "1000"
            assert response["data"]["ack"] is True

            mock_create_inbox.assert_called_once()

            # ✅ 关键验证：业务处理**未被调用**
            mock_handle.assert_not_called()

            # ✅ 验证 callback_log 仍然被记录
            mock_log_callback.assert_called_once()
            log_call_kwargs = mock_log_callback.call_args[1]
            assert log_call_kwargs["callback_type"] == "result"
            assert log_call_kwargs["device_id"] == "ARM_01"
            assert log_call_kwargs["response_status"] == 200
            # ✅ 关键验证：error_message 标记为幂等重复
            assert log_call_kwargs["error_message"] == "幂等重复: 已存在相同事件"

            # ✅ 关键验证：audit_log **未被记录**
            mock_audit.assert_not_called()


# ==================== callback/event 测试 ====================


class TestCallbackEventAPI:
    """测试设备事件上报 API"""

    @pytest.mark.asyncio
    async def test_callback_event_success(
        self,
        db_session: AsyncSession,
        mock_request,
    ) -> None:
        """
        测试成功的设备事件上报

        验证:
        - ✅ 返回 200 OK
        - ✅ 响应包含 status: submitted
        - ✅ 调用了 Celery send_task
        - ✅ 记录了 callback_log
        - ✅ 记录了 audit_log
        """
        mock_celery_app = MagicMock()
        mock_celery_app.send_task = MagicMock()

        with (
            patch(
                "src.app.callback.v1.callback.inbox_service.create_device_event_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.celery_app.app.celery_app",
                mock_celery_app,
            ),
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="test-req-003"),
        ):
            from src.app.callback.v1.callback import callback_event

            request_data = create_event_request()

            response = await callback_event(
                event_request=EventRequest(**request_data),
                request=mock_request(path="/api/v1/callback/event"),
                db=db_session,
            )

            assert response["code"] == "1000"
            assert response["data"]["status"] == "submitted"
            assert response["data"]["device_code"] == "CONVEYOR_01"

            mock_create_inbox.assert_called_once()

            mock_celery_app.send_task.assert_called_once()
            assert mock_celery_app.send_task.call_args[0][0] == "src.celery_app.tasks.device.process_device_event"

            mock_log_callback.assert_called_once()
            log_call_kwargs = mock_log_callback.call_args[1]
            assert log_call_kwargs["callback_type"] == "event"
            assert log_call_kwargs["device_id"] == "CONVEYOR_01"
            assert log_call_kwargs["error_message"] is None

            mock_audit.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_event_idempotent_duplicate(
        self,
        db_session: AsyncSession,
        mock_request,
    ) -> None:
        """
        测试幂等重复的设备事件上报

        验证:
        - ✅ 返回 200 OK
        - ✅ 响应包含 status: duplicate（区分正常和重复）
        - ✅ **未调用** Celery send_task（跳过异步处理）
        - ✅ 记录了 callback_log（error_message 标记为幂等重复）
        - ✅ **未记录** audit_log（跳过审计）
        """
        mock_celery_app = MagicMock()
        mock_celery_app.send_task = MagicMock()

        with (
            patch(
                "src.app.callback.v1.callback.inbox_service.create_device_event_inbox",
                new=AsyncMock(side_effect=ValueError("设备事件已存在（幂等键重复）: test-key, 原消息 ID: 456")),
            ) as mock_create_inbox,
            patch(
                "src.celery_app.app.celery_app",
                mock_celery_app,
            ),
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="test-req-004"),
        ):
            from src.app.callback.v1.callback import callback_event

            request_data = create_event_request()

            response = await callback_event(
                event_request=EventRequest(**request_data),
                request=mock_request(path="/api/v1/callback/event"),
                db=db_session,
            )

            assert response["code"] == "1000"
            # ✅ 关键验证：响应中标记为 duplicate
            assert response["data"]["status"] == "duplicate"
            assert response["data"]["device_code"] == "CONVEYOR_01"

            mock_create_inbox.assert_called_once()

            # ✅ 关键验证：Celery 任务**未被提交**
            mock_celery_app.send_task.assert_not_called()

            # ✅ 验证 callback_log 仍然被记录
            mock_log_callback.assert_called_once()
            log_call_kwargs = mock_log_callback.call_args[1]
            assert log_call_kwargs["callback_type"] == "event"
            assert log_call_kwargs["device_id"] == "CONVEYOR_01"
            # ✅ 关键验证：error_message 标记为幂等重复
            assert log_call_kwargs["error_message"] == "幂等重复: 已存在相同事件"

            # ✅ 关键验证：audit_log **未被记录**
            mock_audit.assert_not_called()


# ==================== 错误处理测试 ====================


class TestCallbackErrorHandling:
    """测试错误处理"""

    @pytest.mark.asyncio
    async def test_callback_result_service_error(
        self,
        db_session: AsyncSession,
        mock_request,
    ) -> None:
        """
        测试指令结果回调时服务层抛出异常

        验证:
        - ✅ 异常被正确抛出
        - ✅ 记录了失败的 callback_log
        - ✅ 记录了失败的 audit_log
        """
        with (
            patch(
                "src.app.callback.v1.callback.inbox_service.create_command_result_inbox",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.v1.callback.device_command_service.handle_callback_result",
                new=AsyncMock(side_effect=Exception("Database connection failed")),
            ),
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="test-req-error"),
        ):
            from src.app.callback.v1.callback import callback_result

            request_data = create_callback_result_request()

            # 调用 API，预期抛出异常
            with pytest.raises(Exception, match="Database connection failed"):
                await callback_result(
                    callback=CommandCallbackResult(**request_data),
                    request=mock_request(),
                    db=db_session,
                )

            # 验证失败的 callback_log 被记录
            mock_log_callback.assert_called_once()
            log_call_kwargs = mock_log_callback.call_args[1]
            assert log_call_kwargs["response_status"] == 500
            assert log_call_kwargs["error_message"] == "Database connection failed"
            mock_audit.assert_called_once()
