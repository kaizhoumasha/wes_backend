"""Callback API 幂等性专项测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def db_session():
    mock = AsyncMock(spec=AsyncSession)
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    return mock


@pytest.fixture
def build_request():
    def _build_request(*, body: dict, path: str):
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "192.168.1.100"
        request.url = MagicMock()
        request.url.path = path
        request.headers = {"User-Agent": "TestClient"}
        request.method = "POST"
        request.json = AsyncMock(return_value=body)
        return request

    return _build_request


def create_result_payload() -> dict:
    return {
        "command_code": "CMD-20250317-001",
        "device_code": "ARM_01",
        "result": "SUCCESS",
        "finish_time": 1702627250000,
        "data": {"task_type": "PICK_AND_PUT"},
    }


def create_event_payload() -> dict:
    return {
        "device_code": "ARM_01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1702627300000,
        "data": {
            "location": "STATION_INPUT1",
            "barcode1": "PKG1_12345678",
        },
    }


def create_external_payload() -> dict:
    return {
        "callback_type": "AGV_TASK_RESULT",
        "correlation_id": "corr-agv-001",
        "command_code": "AGV-REQ-001",
        "result": "SUCCESS",
        "data": {"to_location": "STATION_OUTPUT1"},
    }


class TestCallbackResultIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_result_skips_business_side_effects(self, db_session: AsyncSession, build_request) -> None:
        existing_command = SimpleNamespace(
            correlation_id="corr-001",
            params={"action": "PICK_AND_PUT"},
            workline_id=1,
            plugin_key="smt_classifier",
            contract_version="1.0",
        )
        handled_command = MagicMock()
        handled_command.status = MagicMock()
        handled_command.status.value = "SUCCESS"
        handled_command.get_duration_ms = MagicMock(return_value=100)
        handled_command.correlation_id = "corr-001"

        with (
            patch(
                "src.app.callback.v1.callback.device_command_service.get_command_by_code",
                new=AsyncMock(return_value=existing_command),
            ),
            patch(
                "src.app.callback.v1.callback.device_service.get_device_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        work_line_id=1,
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                    )
                ),
            ),
            patch(
                "src.app.callback.v1.callback.workline_service.get_by_id",
                new=AsyncMock(return_value=SimpleNamespace(plugin_key="smt_classifier")),
            ),
            patch(
                "src.app.callback.v1.callback.inbox_service.create_command_result_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.v1.callback.device_command_service.handle_callback_result",
                new=AsyncMock(return_value=handled_command),
            ) as mock_handle,
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback._enqueue_workline_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", side_effect=["req-001", "req-002"]),
        ):
            from src.app.callback.v1.callback import callback_result

            response1 = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

            mock_create_inbox.side_effect = ValueError("指令结果已存在（幂等键重复）: duplicate")

            response2 = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

        assert response1["code"] == "1000"
        assert response2["code"] == "1000"
        assert response2["data"]["ack"] is True
        assert mock_handle.await_count == 1
        assert mock_enqueue.call_count == 2
        assert mock_log_callback.await_count == 2
        assert mock_log_callback.await_args.kwargs["error_message"] == "幂等重复: 已存在相同事件"
        assert mock_audit.await_count == 1


class TestCallbackEventIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_event_requeues_processing_but_skips_audit(
        self,
        db_session: AsyncSession,
        build_request,
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_service.get_device_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        work_line_id=1,
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                    )
                ),
            ),
            patch(
                "src.app.callback.v1.callback.workline_service.get_by_id",
                new=AsyncMock(return_value=SimpleNamespace(plugin_key="smt_classifier")),
            ),
            patch(
                "src.app.callback.v1.callback.inbox_service.create_device_event_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback._enqueue_workline_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", side_effect=["req-101", "req-102"]),
        ):
            from src.app.callback.v1.callback import callback_event

            response1 = await callback_event(
                request=build_request(body=create_event_payload(), path="/api/v1/callback/event"),
                db=db_session,
            )

            mock_create_inbox.side_effect = ValueError("设备事件已存在（幂等键重复）: duplicate")

            response2 = await callback_event(
                request=build_request(body=create_event_payload(), path="/api/v1/callback/event"),
                db=db_session,
            )

        assert response1["code"] == "1000"
        assert response2["code"] == "1000"
        assert response2["data"]["status"] == "duplicate"
        assert mock_enqueue.call_count == 2
        assert mock_create_inbox.await_count == 2
        assert mock_log_callback.await_count == 2
        assert mock_log_callback.await_args.kwargs["error_message"] == "幂等重复: 已存在相同事件"
        assert mock_audit.await_count == 1


class TestCallbackExternalIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_external_callback_requeues_processing_but_skips_audit(
        self,
        db_session: AsyncSession,
        build_request,
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.inbox_service.create_external_http_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback._enqueue_workline_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", side_effect=["req-ext-101", "req-ext-102"]),
        ):
            from src.app.callback.v1.callback import callback_external

            response1 = await callback_external(
                request=build_request(body=create_external_payload(), path="/api/v1/callback/external"),
                db=db_session,
            )

            mock_create_inbox.side_effect = ValueError("外部 HTTP 回调已存在（幂等键重复）: duplicate")

            response2 = await callback_external(
                request=build_request(body=create_external_payload(), path="/api/v1/callback/external"),
                db=db_session,
            )

        assert response1["code"] == "1000"
        assert response2["code"] == "1000"
        assert response2["data"]["status"] == "duplicate"
        assert mock_enqueue.call_count == 2
        assert mock_log_callback.await_count == 2
        assert mock_log_callback.await_args.kwargs["error_message"] == "幂等重复: 已存在相同事件"
        assert mock_audit.await_count == 1
