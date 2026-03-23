"""
Callback API 幂等性专项测试

专门测试幂等性控制逻辑，确保修复后的代码符合预期：

测试覆盖（基于 claudedocs/idempotency_fix_20260317.md）:
- ✅ 相同 command_code 第二次调用 → 业务处理只执行一次
- ✅ 相同事件（device_code + event_type + timestamp + data）第二次调用 → Celery 任务只提交一次
- ✅ callback_log 中正确记录了两次请求
- ✅ 第二次的 callback_log.error_message 包含"幂等重复"
- ✅ audit_log 只记录一次（非重复的那次）

相关文档:
- 白皮书 6.3.1 节: 幂等性设计
- claudedocs/idempotency_fix_20260317.md: 幂等性控制逻辑修复

注意:
- 由于循环导入问题（callback.v1.__init__.py → callback.py → api_security.py ↔
  api_auth/__init__.py）
- 测试使用延迟导入模式：在 patch 块内部导入 callback 模块
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


# ==================== 幂等性核心测试 ====================


class TestCallbackResultIdempotency:
    """
    测试指令结果回调的幂等性

    场景：相同 command_code 的指令结果重复发送
    期望：
    1. 第一次调用：写入 Inbox → 执行业务处理 → 记录 callback_log
       （无 error_message）→ 记录 audit_log
    2. 第二次调用：写入 Inbox 失败（幂等键重复）→ **跳过**业务处理 →
       记录 callback_log（标记重复）→ **不记录** audit_log
    """

    @pytest.mark.asyncio
    async def test_idempotency_duplicate_result_single_execution(
        self,
        db_session: AsyncSession,
        mock_request,
    ) -> None:
        """
        测试相同指令结果重复发送时，业务处理只执行一次

        这是幂等性的核心验证：确保重复请求不会导致重复的业务处理。
        """
        mock_command = MagicMock()
        mock_command.status = MagicMock()
        mock_command.status.value = "SUCCESS"
        mock_command.get_duration_ms = MagicMock(return_value=100)

        request_data = {
            "command_code": "CMD-20250317-001",
            "device_code": "ARM_01",
            "result": "SUCCESS",
            "finish_time": 1702627250000,
            "data": {"actual_qty": 10},
        }

        # 使用 patch 实例方法的方式（inbox_service 是 WorklineInboxService 的实例）
        with (
            patch(
                "src.app.callback.v1.callback.inbox_service.create_command_result_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-001"),
        ):
            # 在 patch 块内部导入，避免循环导入
            from src.app.callback.v1.callback import callback_result

            # ===== 第一次调用：正常处理 =====
            response1 = await callback_result(
                callback=CommandCallbackResult(**request_data),
                request=mock_request(),
                db=db_session,
            )

            # 验证第一次调用成功
            assert response1["code"] == "1000"
            assert response1["data"]["ack"] is True

            # 验证 Inbox 创建被调用
            assert mock_create_inbox.call_count == 1

            # 验证业务处理被调用
            assert mock_handle.call_count == 1

            # 验证 callback_log 被记录（无错误消息）
            assert mock_log_callback.call_count == 1
            first_log_kwargs = mock_log_callback.call_args[1]
            assert first_log_kwargs["error_message"] is None

            # 验证 audit_log 被记录
            assert mock_audit.call_count == 1

            # ===== 第二次调用：幂等重复 =====
            # Mock Inbox 创建抛出幂等重复异常
            mock_create_inbox.side_effect = ValueError(
                "指令结果已存在（幂等键重复）: cmd-result:CMD-20250317-001:SUCCESS:1702627250000:a1b2c3d4, 原消息 ID: 123"
            )

            with patch("src.app.callback.v1.callback.get_request_id", return_value="req-002"):
                response2 = await callback_result(
                    callback=CommandCallbackResult(**request_data),
                    request=mock_request(),
                    db=db_session,
                )

            # 验证第二次调用也返回成功
            assert response2["code"] == "1000"
            assert response2["data"]["ack"] is True

            # ===== 关键验证：业务处理只执行一次 =====
            assert mock_handle.call_count == 1, "业务处理应该只执行一次（第二次调用时未执行）"

            # ===== 验证 callback_log 记录了两次 =====
            assert mock_log_callback.call_count == 2, "callback_log 应该记录两次（包括幂等重复）"

            # 验证第二次 callback_log 标记了幂等重复
            second_log_kwargs = mock_log_callback.call_args[1]
            assert second_log_kwargs["error_message"] == "幂等重复: 已存在相同事件"
            assert second_log_kwargs["request_id"] == "req-002"

            # ===== 关键验证：audit_log 只记录一次 =====
            assert mock_audit.call_count == 1, "audit_log 应该只记录一次（幂等重复时不记录）"


class TestCallbackEventIdempotency:
    """
    测试设备事件上报的幂等性

    场景：相同事件（device_code + event_type + timestamp + data）重复发送
    期望：
    1. 第一次调用：写入 Inbox → 提交 Celery 任务 → 记录 callback_log
       （无 error_message）→ 记录 audit_log
    2. 第二次调用：写入 Inbox 失败（幂等键重复）→ **不提交** Celery 任务 →
       记录 callback_log（标记重复）→ **不记录** audit_log
    """

    @pytest.mark.asyncio
    async def test_idempotency_duplicate_event_single_celery_task(
        self,
        db_session: AsyncSession,
        mock_request,
    ) -> None:
        """
        测试相同事件重复发送时，Celery 任务只提交一次

        这是事件幂等性的核心验证：确保重复请求不会导致重复的异步处理。
        """
        mock_celery_app = MagicMock()
        mock_celery_app.send_task = MagicMock()

        request_data = {
            "device_code": "CONVEYOR_01",
            "event_type": "MATERIAL_ARRIVED",
            "timestamp": 1702627300000,
            "data": {"barcode": "PKG12345678", "location": "STATION_04"},
        }

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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-001"),
        ):
            # 在 patch 块内部导入
            from src.app.callback.v1.callback import callback_event

            # ===== 第一次调用：正常处理 =====
            response1 = await callback_event(
                event_request=EventRequest(**request_data),
                request=mock_request(path="/api/v1/callback/event"),
                db=db_session,
            )

            # 验证第一次调用成功
            assert response1["code"] == "1000"
            assert response1["data"]["status"] == "submitted"

            # 验证 Celery 任务被提交
            assert mock_celery_app.send_task.call_count == 1
            assert mock_create_inbox.call_count == 1

            # 验证 callback_log 被记录（无错误消息）
            assert mock_log_callback.call_count == 1
            first_log_kwargs = mock_log_callback.call_args[1]
            assert first_log_kwargs["error_message"] is None

            # 验证 audit_log 被记录
            assert mock_audit.call_count == 1

            # ===== 第二次调用：幂等重复 =====
            # Mock Inbox 创建抛出幂等重复异常
            mock_create_inbox.side_effect = ValueError(
                "设备事件已存在（幂等键重复）: device_event:CONVEYOR_01:MATERIAL_ARRIVED:1702627300000:e5f6g7h8, 原消息 ID: 456"
            )

            with patch("src.app.callback.v1.callback.get_request_id", return_value="req-002"):
                response2 = await callback_event(
                    event_request=EventRequest(**request_data),
                    request=mock_request(path="/api/v1/callback/event"),
                    db=db_session,
                )

            # 验证第二次调用也返回成功
            assert response2["code"] == "1000"
            # ✅ 关键验证：响应中标记为 duplicate
            assert response2["data"]["status"] == "duplicate"

            # ===== 关键验证：Celery 任务只提交一次 =====
            assert (
                mock_celery_app.send_task.call_count == 1
            ), "Celery 任务应该只提交一次（第二次调用时未提交）"

            # ===== 验证 callback_log 记录了两次 =====
            assert mock_log_callback.call_count == 2, "callback_log 应该记录两次"

            # 验证第二次 callback_log 标记了幂等重复
            second_log_kwargs = mock_log_callback.call_args[1]
            assert second_log_kwargs["error_message"] == "幂等重复: 已存在相同事件"
            assert second_log_kwargs["request_id"] == "req-002"

            # ===== 关键验证：audit_log 只记录一次 =====
            assert mock_audit.call_count == 1, "audit_log 应该只记录一次"


# ==================== 边界条件测试 ====================


class TestIdempotencyEdgeCases:
    """测试幂等性的边界条件"""

    @pytest.mark.asyncio
    async def test_different_results_should_not_be_duplicate(
        self,
        db_session: AsyncSession,
        mock_request,
    ) -> None:
        """
        测试不同结果的指令回调不视为幂等重复

        场景：相同 command_code，但不同的 result（SUCCESS vs FAILED）
        期望：两次都正常处理，不触发幂等逻辑
        """
        mock_command = MagicMock()
        mock_command.status = MagicMock()
        mock_command.status.value = "SUCCESS"
        mock_command.get_duration_ms = MagicMock(return_value=100)

        with (
            patch(
                "src.app.callback.v1.callback.inbox_service.create_command_result_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.v1.callback.device_command_service.handle_callback_result",
                new=AsyncMock(return_value=mock_command),
            ) as mock_handle,
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
        ):
            from src.app.callback.v1.callback import callback_result

            # 第一次调用：SUCCESS
            request_data_1 = {
                "command_code": "CMD-001",
                "device_code": "ARM_01",
                "result": "SUCCESS",
                "finish_time": 1702627250000,
                "data": {},
            }

            with patch("src.app.callback.v1.callback.get_request_id", return_value="req-001"):
                await callback_result(
                    callback=CommandCallbackResult(**request_data_1),
                    request=mock_request(),
                    db=db_session,
                )

            # 第二次调用：FAILED（不同的结果）
            request_data_2 = {
                "command_code": "CMD-001",  # 相同的 command_code
                "device_code": "ARM_01",
                "result": "FAILED",  # 不同的结果
                "finish_time": 1702627260000,  # 不同的完成时间
                "data": {},
            }

            with patch("src.app.callback.v1.callback.get_request_id", return_value="req-002"):
                await callback_result(
                    callback=CommandCallbackResult(**request_data_2),
                    request=mock_request(),
                    db=db_session,
                )

            # 验证：两次都应该执行业务处理（因为幂等键不同）
            assert mock_handle.call_count == 2, "不同结果的指令应该被分别处理"
            assert mock_create_inbox.call_count == 2

    @pytest.mark.asyncio
    async def test_different_events_should_not_be_duplicate(
        self,
        db_session: AsyncSession,
        mock_request,
    ) -> None:
        """
        测试不同事件不视为幂等重复

        场景：相同 device_code，但不同的 event_type
        期望：两次都正常处理，不触发幂等逻辑
        """
        mock_celery_app = MagicMock()
        mock_celery_app.send_task = MagicMock()

        with (
            patch(
                "src.app.callback.v1.callback.inbox_service.create_device_event_inbox",
                new=AsyncMock(),
            ),
            patch(
                "src.celery_app.app.celery_app",
                mock_celery_app,
            ),
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
        ):
            from src.app.callback.v1.callback import callback_event

            # 第一次调用：MATERIAL_ARRIVED
            request_data_1 = {
                "device_code": "CONVEYOR_01",
                "event_type": "MATERIAL_ARRIVED",
                "timestamp": 1702627300000,
                "data": {"barcode": "PKG001"},
            }

            with patch("src.app.callback.v1.callback.get_request_id", return_value="req-001"):
                await callback_event(
                    event_request=EventRequest(**request_data_1),
                    request=mock_request(path="/api/v1/callback/event"),
                    db=db_session,
                )

            # 第二次调用：SCAN_COMPLETED（不同的事件类型）
            request_data_2 = {
                "device_code": "CONVEYOR_01",  # 相同的 device_code
                "event_type": "SCAN_COMPLETED",  # 不同的事件类型
                "timestamp": 1702627310000,  # 不同的时间戳
                "data": {"barcode": "PKG001"},
            }

            with patch("src.app.callback.v1.callback.get_request_id", return_value="req-002"):
                await callback_event(
                    event_request=EventRequest(**request_data_2),
                    request=mock_request(path="/api/v1/callback/event"),
                    db=db_session,
                )

            # 验证：两次都应该提交 Celery 任务（因为幂等键不同）
            assert (
                mock_celery_app.send_task.call_count == 2
            ), "不同的事件应该被分别处理并提交 Celery 任务"
