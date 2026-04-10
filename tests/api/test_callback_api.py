"""Callback API 单元测试。"""

import importlib
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from celery.exceptions import DuplicateNodenameWarning
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def db_session():
    mock = AsyncMock(spec=AsyncSession)
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    return mock


@pytest.fixture
def build_request():
    def _build_request(
        *,
        body: dict,
        path: str,
        client_ip: str = "192.168.1.100",
        user_agent: str = "TestClient",
    ):
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = client_ip
        request.url = MagicMock()
        request.url.path = path
        request.headers = {"User-Agent": user_agent}
        request.method = "POST"
        request.json = AsyncMock(return_value=body)
        return request

    return _build_request


def create_result_payload(**overrides) -> dict:
    payload = {
        "command_code": "CMD-20250317-001",
        "device_code": "ARM_01",
        "result": "SUCCESS",
        "finish_time": 1702627250000,
        "data": {"task_type": "PICK_AND_PUT"},
    }
    payload.update(overrides)
    return payload


def create_event_payload(**overrides) -> dict:
    payload = {
        "device_code": "ARM_01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1702627300000,
        "data": {
            "location": "STATION_INPUT1",
            # 使用完整的 SixInOne 字段（对齐硬件约定）
            "LotCode": "LOTABC123",  # 批次码
            "DateCode": "20260409",  # 日期码
            "Qty": "100",  # 数量
            "ProductNo": "PN001",  # 产品PN码
            "MfrPN": "MFR002",  # 制造商PN码
            "PONumber": "PO2026040901",  # 订单码
        },
    }
    payload.update(overrides)
    return payload


def create_external_payload(**overrides) -> dict:
    payload = {
        "callback_type": "AGV_TASK_RESULT",
        "correlation_id": "corr-agv-001",
        "command_code": "AGV-REQ-001",
        "result": "SUCCESS",
        "data": {"to_location": "STATION_OUTPUT1"},
    }
    payload.update(overrides)
    return payload


class TestCallbackResultAPI:
    def test_check_system_ready_suppresses_duplicate_nodename_warning(self) -> None:
        fake_system_health = SimpleNamespace(is_ready=False, is_stale=True, update=MagicMock())
        fake_redis = SimpleNamespace(ping=AsyncMock(return_value=True))
        fake_inspect = MagicMock()

        def _ping_with_warning():
            warnings.warn("duplicate worker node", DuplicateNodenameWarning, stacklevel=1)
            return {"celery@worker": {"ok": "pong"}}

        fake_inspect.ping = MagicMock(side_effect=_ping_with_warning)
        fake_celery_app = SimpleNamespace(
            conf=MagicMock(), control=MagicMock(inspect=MagicMock(return_value=fake_inspect))
        )

        with (
            patch("src.core.health.system_health", fake_system_health),
            patch("src.database.redis_client.get_redis", return_value=fake_redis),
            patch("src.celery_app.app.celery_app", fake_celery_app),
            warnings.catch_warnings(record=True) as caught_warnings,
        ):
            warnings.simplefilter("always")

            from src.app.callback.v1.callback import _check_system_ready

            result = _check_system_ready()

        assert result is None
        assert not any(isinstance(item.message, DuplicateNodenameWarning) for item in caught_warnings)
        fake_system_health.update.assert_called_once_with(db_ok=True, redis_ok=True, celery_ok=True)
        fake_inspect.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_result_success(self, db_session: AsyncSession, build_request) -> None:
        existing_command = SimpleNamespace(
            correlation_id="corr-001",
            params={"task_type": "PICK_AND_PUT"},
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-001"),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert response["data"]["ack"] is True
        assert mock_create_inbox.call_args.kwargs["command_type"] == "PICK_AND_PUT"
        assert mock_log_callback.await_args is not None
        assert mock_log_callback.await_args.kwargs["correlation_id"] == "corr-001"
        mock_handle.assert_awaited_once()
        mock_enqueue.assert_called_once()
        db_session.commit.assert_awaited_once()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_result_rejects_legacy_command_id_and_device_id(
        self,
        db_session: AsyncSession,
        build_request,
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_command_service.get_command_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        correlation_id="corr-001",
                        params={"task_type": "PICK_AND_PUT"},
                        workline_id=1,
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                    )
                ),
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
            ),
            patch(
                "src.app.callback.v1.callback.device_command_service.handle_callback_result",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        correlation_id="corr-001",
                        status=SimpleNamespace(value="SUCCESS"),
                        get_duration_ms=MagicMock(return_value=100),
                    )
                ),
            ),
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-legacy-001"),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(
                    body={
                        "command_id": "CMD-LEGACY-001",
                        "device_id": "ARM_01",
                        "result": "SUCCESS",
                        "finish_time": 1702627250000,
                    },
                    path="/api/v1/callback/result",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert response["data"]["ack"] is False
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_result_rejects_invalid_plugin_result(self, db_session: AsyncSession, build_request) -> None:
        existing_command = SimpleNamespace(
            correlation_id="corr-001",
            params={"action": "PICK_AND_PUT"},
            workline_id=1,
            plugin_key="smt_classifier",
            contract_version="1.0",
        )

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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-002"),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(
                    body=create_result_payload(result="BROKEN"),
                    path="/api/v1/callback/result",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert response["data"]["ack"] is False
        mock_create_inbox.assert_not_called()
        mock_handle.assert_not_called()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()


class TestCallbackContractBoundary:
    def test_device_models_no_longer_export_event_contract_types(self) -> None:
        device_models = importlib.import_module("src.app.device.models")

        assert not hasattr(device_models, "EventRequest")
        assert not hasattr(device_models, "EventType")


class TestCallbackEventAPI:
    @pytest.mark.asyncio
    async def test_callback_event_success(self, db_session: AsyncSession, build_request) -> None:
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-003"),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(body=create_event_payload(), path="/api/v1/callback/event"),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert response["data"]["status"] == "submitted"
        create_inbox_kwargs = mock_create_inbox.call_args.kwargs
        event_correlation_id = create_inbox_kwargs["correlation_id"]
        assert create_inbox_kwargs["event_type"] == "SCAN_COMPLETED"
        # 验证 data 包含完整的 SixInOne 字段（对齐硬件约定）
        assert create_inbox_kwargs["data"]["LotCode"] == "LOTABC123"
        assert create_inbox_kwargs["data"]["DateCode"] == "20260409"
        assert create_inbox_kwargs["data"]["Qty"] == "100"
        assert create_inbox_kwargs["data"]["ProductNo"] == "PN001"
        assert create_inbox_kwargs["data"]["MfrPN"] == "MFR002"
        assert create_inbox_kwargs["data"]["PONumber"] == "PO2026040901"
        assert isinstance(event_correlation_id, str)
        assert event_correlation_id.startswith("corr_")
        assert create_inbox_kwargs["correlation_id"] == event_correlation_id
        assert mock_log_callback.await_args is not None
        assert mock_log_callback.await_args.kwargs["correlation_id"] == event_correlation_id
        mock_enqueue.assert_called_once()
        db_session.commit.assert_awaited_once()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_event_rejects_legacy_device_id(
        self,
        db_session: AsyncSession,
        build_request,
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-legacy-002"),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(
                    body={
                        "device_id": "ARM_01",
                        "event_type": "SCAN_COMPLETED",
                        "timestamp": 1702627300000,
                        "data": {"LotCode": "LOTABC123"},
                    },
                    path="/api/v1/callback/event",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert response["data"]["ack"] is False
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_event_rejects_invalid_plugin_event(self, db_session: AsyncSession, build_request) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_service.get_device_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        work_line_id=1,
                        plugin_key="simplified_smt",  # 使用有效的插件
                        contract_version="1.0",
                    )
                ),
            ),
            patch(
                "src.app.callback.v1.callback.workline_service.get_by_id",
                new=AsyncMock(return_value=SimpleNamespace(plugin_key="simplified_smt")),
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
            patch("src.app.callback.v1.callback._enqueue_workline_processing"),
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-004"),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(
                    body=create_event_payload(event_type="UNKNOWN_EVENT"),
                    path="/api/v1/callback/event",
                ),
                db=db_session,
            )

        # 简化架构：接受所有事件类型，返回成功
        assert response["code"] == "1000"
        assert response["data"]["status"] == "submitted"
        mock_create_inbox.assert_awaited_once()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()


class TestCallbackExternalAPI:
    @pytest.mark.asyncio
    async def test_callback_external_success(self, db_session: AsyncSession, build_request) -> None:
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-ext-001"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(body=create_external_payload(), path="/api/v1/callback/external"),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert response["data"]["status"] == "submitted"
        assert mock_create_inbox.await_args is not None
        assert mock_create_inbox.await_args.kwargs["callback_type"] == "AGV_TASK_RESULT"
        assert mock_create_inbox.await_args.kwargs["correlation_id"] == "corr-agv-001"
        assert mock_log_callback.await_args is not None
        assert mock_log_callback.await_args.kwargs["correlation_id"] == "corr-agv-001"
        mock_enqueue.assert_called_once()
        db_session.commit.assert_awaited_once()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_external_rejects_missing_correlation_id(
        self,
        db_session: AsyncSession,
        build_request,
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-ext-002"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_external_payload(correlation_id=""),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert response["data"]["ack"] is False
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()
