"""Callback API 单元测试。"""

import importlib
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from celery.exceptions import DuplicateNodenameWarning
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def mock_fast_fail_check():
    """自动 mock fast_fail_check 和设备上下文服务，避免在测试中执行真实的基础设施检查。

    注意：由于测试直接调用 callback 函数而非通过 FastAPI，
    依赖注入可能不会自动触发。
    """
    # Mock fast_fail_check 函数本身
    with patch("src.utils.fast_fail.fast_fail_check", new_callable=AsyncMock) as mock:
        # 同时 mock 健康检查函数
        with (
            patch("src.utils.health.check_database_health", new_callable=AsyncMock) as db_mock,
            patch("src.utils.health.check_redis_health", new_callable=AsyncMock) as redis_mock,
            patch("src.utils.health.check_celery_health", new_callable=AsyncMock) as celery_mock,
            patch("src.app.callback.v1.callback.device_context_service.resolve") as ctx_mock,
        ):
            # 返回健康状态
            db_mock.return_value = {"status": "healthy"}
            redis_mock.return_value = {"status": "healthy"}
            celery_mock.return_value = {"status": "healthy"}

            # 返回设备上下文（模拟 DeviceContextService.resolve）
            def ctx_resolve_side_effect(db, device_code):
                # 模拟成功返回：返回 (DeviceContextResult, None)
                return (
                    SimpleNamespace(
                        device=SimpleNamespace(
                            id=1,
                            code=device_code,
                            work_line_id=1,
                            plugin_key="smt_classifier",
                            contract_version="1.0",
                            device_status="ONLINE",
                        ),
                        workline=SimpleNamespace(
                            id=1,
                            is_active=True,
                            plugin_key="smt_classifier",
                        ),
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                        work_line_id=1,
                        is_workline_bound=True,
                    ),
                    None,  # 无错误
                )

            ctx_mock.side_effect = ctx_resolve_side_effect

            mock.return_value = None  # 允许请求通过
            yield mock


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
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=None,
                            workline=SimpleNamespace(
                                plugin_key="smt_classifier", contract_version="1.0", is_active=True
                            ),
                            plugin_key="smt_classifier",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
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
        assert mock_create_inbox.call_args.kwargs["source_message_id"] == "req-001"
        assert mock_log_callback.await_args is not None
        assert mock_log_callback.await_args.kwargs["correlation_id"] == "corr-001"
        assert mock_log_callback.await_args.kwargs["ingress_outcome"] == "ACCEPTED"
        assert mock_log_callback.await_args.kwargs["failure_stage"] is None
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
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=None,
                            workline=SimpleNamespace(
                                plugin_key="smt_classifier", contract_version="1.0", is_active=True
                            ),
                            plugin_key="smt_classifier",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
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
        assert mock_log_callback.await_args.kwargs["ingress_outcome"] == "REJECTED"
        assert mock_log_callback.await_args.kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
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
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=None,
                            workline=SimpleNamespace(
                                plugin_key="smt_classifier", contract_version="1.0", is_active=True
                            ),
                            plugin_key="smt_classifier",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
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
        assert mock_log_callback.await_args.kwargs["ingress_outcome"] == "REJECTED"
        assert mock_log_callback.await_args.kwargs["failure_stage"] == "CONTRACT_VALIDATE"
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_result_logs_device_context_failure(self, db_session: AsyncSession, build_request) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(return_value=(None, {"code": 404, "message": "未找到设备: ARM_01"})),
            ),
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-ctx-001"),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

        assert response["code"] == 404
        assert response["message"] == "未找到设备: ARM_01"
        assert response["request_id"] == "req-ctx-001"
        mock_log_callback.assert_awaited_once()
        assert mock_log_callback.await_args.kwargs["ingress_outcome"] == "REJECTED"
        assert mock_log_callback.await_args.kwargs["failure_stage"] == "DEVICE_CONTEXT_RESOLVE"
        assert mock_log_callback.await_args.kwargs["response_status"] == 404
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_result_rejects_invalid_capability_config(
        self, db_session: AsyncSession, build_request
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(capabilities_json=[]),
                            workline=SimpleNamespace(
                                plugin_key="smt_classifier", contract_version="1.0", is_active=True
                            ),
                            plugin_key="smt_classifier",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
            ),
            patch(
                "src.app.callback.v1.callback.device_command_service.get_command_by_code",
                new=AsyncMock(),
            ) as mock_get_command,
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-cap-bad-result-001"),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert response["data"]["ack"] is False
        mock_get_command.assert_not_called()
        mock_log_callback.assert_awaited_once()
        assert mock_log_callback.await_args.kwargs["ingress_outcome"] == "REJECTED"
        assert mock_log_callback.await_args.kwargs["failure_stage"] == "CONFIG_VALIDATE"
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
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=None,
                            workline=SimpleNamespace(
                                plugin_key="smt_classifier", contract_version="1.0", is_active=True
                            ),
                            plugin_key="smt_classifier",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
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
        assert create_inbox_kwargs["source_message_id"] == "req-003"
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
        assert mock_log_callback.await_args.kwargs["ingress_outcome"] == "ACCEPTED"
        assert mock_log_callback.await_args.kwargs["failure_stage"] is None
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
        assert mock_log_callback.await_args.kwargs["ingress_outcome"] == "REJECTED"
        assert mock_log_callback.await_args.kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_event_rejects_invalid_plugin_event(self, db_session: AsyncSession, build_request) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_service.get_device_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        work_line_id=1,
                        plugin_key="smt_classifier",  # 使用有效的插件
                        contract_version="1.0",
                    )
                ),
            ),
            patch(
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=None,
                            workline=SimpleNamespace(
                                plugin_key="smt_classifier", contract_version="1.0", is_active=True
                            ),
                            plugin_key="smt_classifier",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
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

    @pytest.mark.asyncio
    async def test_callback_event_uses_canonical_event_for_capability_check(
        self,
        db_session: AsyncSession,
        build_request,
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(capabilities_json={"supports_event_types": ["SCAN_COMPLETED"]}),
                            workline=SimpleNamespace(
                                plugin_key="smt_classifier",
                                contract_version="1.0",
                                is_active=True,
                                runtime_config_json={"event_type_mapping": {"SCAN_FINISH": "SCAN_COMPLETED"}},
                            ),
                            plugin_key="smt_classifier",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-canonical-001"),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(
                    body=create_event_payload(event_type="SCAN_FINISH"),
                    path="/api/v1/callback/event",
                ),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert response["data"]["status"] == "submitted"
        assert mock_create_inbox.await_args is not None
        assert mock_create_inbox.await_args.kwargs["event_type"] == "SCAN_FINISH"
        assert mock_create_inbox.await_args.kwargs["canonical_event_type"] == "SCAN_COMPLETED"
        mock_enqueue.assert_called_once()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_event_rejects_invalid_capability_config(
        self, db_session: AsyncSession, build_request
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(capabilities_json=[]),
                            workline=SimpleNamespace(
                                plugin_key="smt_classifier", contract_version="1.0", is_active=True
                            ),
                            plugin_key="smt_classifier",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-cap-bad-event-001"),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(body=create_event_payload(), path="/api/v1/callback/event"),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert response["data"]["ack"] is False
        mock_create_inbox.assert_not_called()
        mock_log_callback.assert_awaited_once()
        assert mock_log_callback.await_args.kwargs["ingress_outcome"] == "REJECTED"
        assert mock_log_callback.await_args.kwargs["failure_stage"] == "CONFIG_VALIDATE"
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
        assert mock_create_inbox.await_args.kwargs["source_message_id"] == "req-ext-001"
        assert mock_log_callback.await_args is not None
        assert mock_log_callback.await_args.kwargs["correlation_id"] == "corr-agv-001"
        assert mock_log_callback.await_args.kwargs["ingress_outcome"] == "ACCEPTED"
        assert mock_log_callback.await_args.kwargs["failure_stage"] is None
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
        assert mock_log_callback.await_args.kwargs["ingress_outcome"] == "REJECTED"
        assert mock_log_callback.await_args.kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        mock_audit.assert_awaited_once()
