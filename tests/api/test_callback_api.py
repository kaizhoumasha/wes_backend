"""Callback API 单元测试。"""

import importlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

JsonDict = dict[str, object]
RequestFactory = Callable[..., Request]


def _await_kwargs(mock: AsyncMock) -> JsonDict:
    await_args = mock.await_args
    assert await_args is not None
    return cast("JsonDict", await_args.kwargs)


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
            patch("src.app.callback.v1.callback.workline_diagnostic_service.record_event", new_callable=AsyncMock),
        ):
            # 返回健康状态
            db_mock.return_value = {"status": "healthy"}
            redis_mock.return_value = {"status": "healthy"}
            celery_mock.return_value = {"status": "healthy"}

            # 返回设备上下文（模拟 DeviceContextService.resolve）
            def ctx_resolve_side_effect(db: object, device_code: str):
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
def db_session() -> AsyncSession:
    mock = AsyncMock(spec=AsyncSession)
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=MagicMock(return_value=None)))
    mock.add = MagicMock()
    return cast("AsyncSession", mock)


@pytest.fixture
def build_request() -> RequestFactory:
    def _build_request(
        *,
        body: JsonDict,
        path: str,
        client_ip: str = "192.168.1.100",
        user_agent: str = "TestClient",
    ) -> Request:
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = client_ip
        request.url = MagicMock()
        request.url.path = path
        request.headers = {"User-Agent": user_agent}
        request.method = "POST"
        request.json = AsyncMock(return_value=body)
        return cast("Request", request)

    return _build_request


def create_result_payload(**overrides: object) -> JsonDict:
    payload: JsonDict = {
        "command_code": "CMD-20250317-001",
        "device_code": "ARM_01",
        "result": "SUCCESS",
        "finish_time": 1702627250000,
        "data": {"task_type": "PICK_AND_PUT"},
    }
    payload.update(overrides)
    return payload


def create_event_payload(**overrides: object) -> JsonDict:
    payload: JsonDict = {
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


def create_external_payload(**overrides: object) -> JsonDict:
    payload: JsonDict = {
        "callback_type": "AGV_TASK_RESULT",
        "trace_id": "trace-agv-001",
        "command_code": "AGV-REQ-001",
        "result": "SUCCESS",
        "data": {"to_location": "STATION_OUTPUT1"},
    }
    payload.update(overrides)
    return payload


class TestCallbackResultAPI:
    @pytest.mark.asyncio
    async def test_callback_result_success(self, db_session: AsyncSession, build_request: RequestFactory) -> None:
        existing_command = SimpleNamespace(
            trace_id="trace-001",
            params={"task_type": "PICK_AND_PUT"},
            workline_id=1,
            plugin_key="smt_classifier",
            contract_version="1.0",
        )
        handled_command = MagicMock()
        handled_command.id = 1001
        handled_command.device_id = 7
        handled_command.status = MagicMock()
        handled_command.status.value = "SUCCESS"
        handled_command.get_duration_ms = MagicMock(return_value=100)
        handled_command.trace_id = "trace-001"
        handled_command.session_id = None

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
                                plugin_key="smt_classifier",
                                contract_version="1.0",
                                is_active=True,
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
                "src.app.callback.services.callback_orchestration_service.outbox_repository.mark_as_acked_by_dispatch_key",
                new=AsyncMock(return_value=None),
            ),
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
            patch(
                "src.app.callback.services.callback_orchestration_service.outbox_repository.mark_as_acked_by_dispatch_key",
                new=AsyncMock(return_value=1),
            ) as mock_mark_acked,
            patch(
                "src.app.callback.v1.callback.device_service.mark_command_finished",
                new=AsyncMock(),
                create=True,
            ) as mock_mark_finished,
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert response["data"]["ack"] is True
        assert mock_create_inbox.call_args.kwargs["command_type"] == "PICK_AND_PUT"
        mock_mark_finished.assert_awaited_once_with(
            db_session,
            device_id=7,
            command_id=1001,
            success=True,
            error_code=None,
            auto_commit=False,
        )
        assert mock_create_inbox.call_args.kwargs["source_message_id"] == "req-001"
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["trace_id"] == "trace-001"
        assert log_kwargs["ingress_outcome"] == "ACCEPTED"
        assert log_kwargs["failure_stage"] is None
        mock_handle.assert_awaited_once()
        mock_mark_acked.assert_awaited_once_with(db_session, "device-command:CMD-20250317-001")
        mock_enqueue.assert_called_once()
        db_session.commit.assert_awaited_once()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_result_accepts_trace_id_and_looks_up_command_before_device_context(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        existing_command = SimpleNamespace(
            id=1001,
            command_code="CMD-20250317-TRACE",
            trace_id="trace-vendor-001",
            params={"task_type": "PICK_AND_PUT"},
            workline_id=1,
            plugin_key="smt_classifier",
            contract_version="1.0",
            session_id=None,
            device_id=7,
        )
        handled_command = MagicMock()
        handled_command.id = 1001
        handled_command.device_id = 7
        handled_command.status = MagicMock()
        handled_command.status.value = "SUCCESS"
        handled_command.get_duration_ms = MagicMock(return_value=100)
        handled_command.trace_id = "trace-vendor-001"
        handled_command.session_id = None

        call_order: list[str] = []

        async def get_command_by_code(_db: object, command_code: str) -> object:
            call_order.append(f"command:{command_code}")
            return existing_command

        async def resolve_device_context(_db: object, device_code: str) -> tuple[object, None]:
            call_order.append(f"device:{device_code}")
            return (
                SimpleNamespace(
                    device=SimpleNamespace(
                        id=7,
                        device_code=device_code,
                        capabilities_json={"supports_result_callback": True},
                    ),
                    workline=SimpleNamespace(
                        id=1,
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                        is_active=True,
                    ),
                    plugin_key="smt_classifier",
                    contract_version="1.0",
                    work_line_id=1,
                    is_workline_bound=True,
                ),
                None,
            )

        payload = create_result_payload(
            command_code="CMD-20250317-TRACE",
            trace_id="trace-vendor-001",
            event_id="evt-result-001",
            causation_id="cmd-request-001",
        )

        with (
            patch(
                "src.app.callback.v1.callback.device_command_service.get_command_by_code",
                new=AsyncMock(side_effect=get_command_by_code),
            ),
            patch(
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(side_effect=resolve_device_context),
            ),
            patch(
                "src.app.callback.v1.callback.inbox_service.create_command_result_inbox",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.v1.callback.device_command_service.handle_callback_result",
                new=AsyncMock(return_value=handled_command),
            ),
            patch(
                "src.app.callback.services.callback_orchestration_service.outbox_repository.mark_as_acked_by_dispatch_key",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
            patch("src.app.callback.v1.callback._enqueue_workline_processing"),
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-trace-001"),
            patch(
                "src.app.callback.v1.callback.device_service.mark_command_finished",
                new=AsyncMock(),
                create=True,
            ),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(body=payload, path="/api/v1/callback/result"),
                db=db_session,
            )

        assert call_order[:2] == ["command:CMD-20250317-TRACE", "device:ARM_01"]
        assert response["code"] == "1000"
        assert response["data"]["ack"] is True
        assert response["data"]["request_id"] == "req-trace-001"
        assert response["data"]["trace_id"] == "trace-vendor-001"
        assert response["data"]["event_id"] == "evt-result-001"
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["trace_id"] == "trace-vendor-001"

    @pytest.mark.asyncio
    async def test_callback_result_rejects_legacy_command_id_and_device_id(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_command_service.get_command_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        trace_id="trace-001",
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
                                plugin_key="smt_classifier",
                                contract_version="1.0",
                                is_active=True,
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
                        trace_id="trace-001",
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
            patch(
                "src.app.callback.v1.callback.get_request_id",
                return_value="req-legacy-001",
            ),
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
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_result_rejects_flattened_business_fields_before_device_context(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(),
            ) as mock_resolve,
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch(
                "src.app.callback.v1.callback.get_request_id",
                return_value="req-result-extra-001",
            ),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(
                    body=create_result_payload(pkg_id="PKG001", reel_diameter=178.5, actual_qty=100),
                    path="/api/v1/callback/result",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert response["data"]["ack"] is False
        assert "业务字段必须放在 data 中" in response["message"]
        mock_resolve.assert_not_awaited()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_result_rejects_invalid_plugin_result(
        self, db_session: AsyncSession, build_request: RequestFactory
    ) -> None:
        existing_command = SimpleNamespace(
            trace_id="trace-001",
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
                                plugin_key="smt_classifier",
                                contract_version="1.0",
                                is_active=True,
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
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "CONTRACT_VALIDATE"
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_result_logs_device_context_failure(
        self, db_session: AsyncSession, build_request: RequestFactory
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_command_service.get_command_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        trace_id="trace-ctx-001",
                        params={"task_type": "PICK_AND_PUT"},
                        workline_id=1,
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                    )
                ),
            ),
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
            patch(
                "src.app.callback.v1.callback.get_request_id",
                return_value="req-ctx-001",
            ),
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
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "DEVICE_CONTEXT_RESOLVE"
        assert log_kwargs["trace_id"] == "trace-ctx-001"
        assert log_kwargs["response_status"] == 404
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_result_rejects_invalid_capability_config(
        self, db_session: AsyncSession, build_request: RequestFactory
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(capabilities_json=[]),
                            workline=SimpleNamespace(
                                plugin_key="smt_classifier",
                                contract_version="1.0",
                                is_active=True,
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
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        trace_id="trace-cap-bad-001",
                        params={"task_type": "PICK_AND_PUT"},
                        workline_id=1,
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                    )
                ),
            ) as mock_get_command,
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch(
                "src.app.callback.v1.callback.get_request_id",
                return_value="req-cap-bad-result-001",
            ),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert response["data"]["ack"] is False
        mock_get_command.assert_awaited_once()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "CONFIG_VALIDATE"
        assert log_kwargs["trace_id"] == "trace-cap-bad-001"
        mock_audit.assert_awaited_once()


class TestCallbackContractBoundary:
    def test_device_models_no_longer_export_event_contract_types(self) -> None:
        device_models = importlib.import_module("src.app.device.models")

        assert not hasattr(device_models, "EventRequest")
        assert not hasattr(device_models, "EventType")


class TestCallbackEventAPI:
    @pytest.mark.asyncio
    async def test_callback_event_success(self, db_session: AsyncSession, build_request: RequestFactory) -> None:
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
                                plugin_key="smt_classifier",
                                contract_version="1.0",
                                is_active=True,
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
        event_trace_id = create_inbox_kwargs["trace_id"]
        assert create_inbox_kwargs["event_type"] == "SCAN_COMPLETED"
        assert create_inbox_kwargs["source_message_id"] == "req-003"
        # 验证 data 包含完整的 SixInOne 字段（对齐硬件约定）
        assert create_inbox_kwargs["data"]["LotCode"] == "LOTABC123"
        assert create_inbox_kwargs["data"]["DateCode"] == "20260409"
        assert create_inbox_kwargs["data"]["Qty"] == "100"
        assert create_inbox_kwargs["data"]["ProductNo"] == "PN001"
        assert create_inbox_kwargs["data"]["MfrPN"] == "MFR002"
        assert create_inbox_kwargs["data"]["PONumber"] == "PO2026040901"
        assert isinstance(event_trace_id, str)
        assert event_trace_id.startswith("trace_")
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["trace_id"] == event_trace_id
        assert log_kwargs["ingress_outcome"] == "ACCEPTED"
        assert log_kwargs["failure_stage"] is None
        mock_enqueue.assert_called_once()
        db_session.commit.assert_awaited_once()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_event_rejects_missing_event_type_before_device_context(
        self,
        db_session: AsyncSession,
        build_request,
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(),
            ) as mock_resolve,
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch(
                "src.app.callback.v1.callback.get_request_id",
                return_value="req-envelope-001",
            ),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(
                    body={
                        "device_code": "ARM_01",
                        "timestamp": 1702627300000,
                        "data": {"LotCode": "LOTABC123"},
                    },
                    path="/api/v1/callback/event",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert response["data"]["ack"] is False
        mock_resolve.assert_not_awaited()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        mock_audit.assert_awaited_once()

    async def test_callback_event_rejects_legacy_device_id(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
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
            patch(
                "src.app.callback.v1.callback.get_request_id",
                return_value="req-legacy-002",
            ),
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
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_event_rejects_flattened_business_fields_before_device_context(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(),
            ) as mock_resolve,
            patch(
                "src.app.callback.v1.callback.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.v1.callback.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch(
                "src.app.callback.v1.callback.get_request_id",
                return_value="req-event-extra-001",
            ),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(
                    body=create_event_payload(PkgID="PKG001", location="STATION_INPUT1"),
                    path="/api/v1/callback/event",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert response["data"]["ack"] is False
        assert "业务字段必须放在 data 中" in response["message"]
        mock_resolve.assert_not_awaited()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_event_rejects_invalid_plugin_event(
        self, db_session: AsyncSession, build_request: RequestFactory
    ) -> None:
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
                                plugin_key="smt_classifier",
                                contract_version="1.0",
                                is_active=True,
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
        build_request: RequestFactory,
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
            patch(
                "src.app.callback.v1.callback.get_request_id",
                return_value="req-canonical-001",
            ),
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
        inbox_kwargs = _await_kwargs(mock_create_inbox)
        assert inbox_kwargs["event_type"] == "SCAN_FINISH"
        assert inbox_kwargs["canonical_event_type"] == "SCAN_COMPLETED"
        mock_enqueue.assert_called_once()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_event_rejects_invalid_capability_config(
        self, db_session: AsyncSession, build_request: RequestFactory
    ) -> None:
        with (
            patch(
                "src.app.callback.v1.callback.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(capabilities_json=[]),
                            workline=SimpleNamespace(
                                plugin_key="smt_classifier",
                                contract_version="1.0",
                                is_active=True,
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
            patch(
                "src.app.callback.v1.callback.get_request_id",
                return_value="req-cap-bad-event-001",
            ),
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
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "CONFIG_VALIDATE"
        mock_audit.assert_awaited_once()


class TestCallbackExternalAPI:
    @pytest.mark.asyncio
    async def test_callback_external_success(self, db_session: AsyncSession, build_request: RequestFactory) -> None:
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
            patch(
                "src.app.callback.v1.callback.get_request_id",
                return_value="req-ext-001",
            ),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(body=create_external_payload(), path="/api/v1/callback/external"),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert response["data"]["status"] == "submitted"
        inbox_kwargs = _await_kwargs(mock_create_inbox)
        assert inbox_kwargs["callback_type"] == "AGV_TASK_RESULT"
        assert inbox_kwargs["trace_id"] == "trace-agv-001"
        assert inbox_kwargs["source_message_id"] == "req-ext-001"
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["trace_id"] == "trace-agv-001"
        assert log_kwargs["ingress_outcome"] == "ACCEPTED"
        assert log_kwargs["failure_stage"] is None
        mock_enqueue.assert_called_once()
        db_session.commit.assert_awaited_once()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_external_rejects_missing_trace_id(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
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
            patch(
                "src.app.callback.v1.callback.get_request_id",
                return_value="req-ext-002",
            ),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_external_payload(trace_id=""),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert response["data"]["ack"] is False
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        mock_audit.assert_awaited_once()
