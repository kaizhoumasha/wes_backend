"""Callback API 单元测试。"""

import importlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.callback.models import (
    CallbackEventIngressResponse,
    CallbackExternalIngressResponse,
    CallbackResultIngressResponse,
)
from src.app.callback.v1 import callback as callback_module

JsonDict = dict[str, object]
RequestFactory = Callable[..., Request]


def _await_kwargs(mock: AsyncMock) -> JsonDict:
    await_args = mock.await_args
    assert await_args is not None
    return cast("JsonDict", await_args.kwargs)


def _response_data(response: JsonDict) -> JsonDict:
    data = response["data"]
    if hasattr(data, "model_dump"):
        return cast("JsonDict", data.model_dump())
    return cast("JsonDict", data)


def _get_route(path: str, method: str) -> APIRoute:
    for route in callback_module.router.routes:
        if isinstance(route, APIRoute) and method in route.methods and route.path == path:
            return route
    raise AssertionError(f"{method} {path} route not found")


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
            patch("src.app.callback.services.callback_ingress_service.device_context_service.resolve") as ctx_mock,
            patch(
                "src.app.callback.services.callback_ingress_service.workline_diagnostic_service.record_event",
                new_callable=AsyncMock,
            ),
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


def create_wms_external_payload(**overrides: object) -> JsonDict:
    payload: JsonDict = {
        "callback_type": "WMS_RACK_ARRIVED",
        "trace_id": "trace-wms-001",
        "dispatch_key": "external:smt_classifier:trace-wms-001:RACK_EXCHANGE_AND_SUPPLY",
        "source_system": "WMS",
        "source_event_id": "wms-event-001",
        "source_version": "1",
        "occurred_at": "2026-05-16T08:00:00Z",
        "request_id": "REQ-WMS-001",
        "timestamp": "2026-05-16T08:00:01Z",
        "signature": "test-signature",
        "active_bin_rack": {"rack_id": "RACK-001", "cells": []},
    }
    payload.update(overrides)
    return payload


class TestCallbackIngressRouteContracts:
    @pytest.mark.parametrize(
        ("path", "response_model"),
        [
            ("/result", CallbackResultIngressResponse),
            ("/event", CallbackEventIngressResponse),
            ("/external", CallbackExternalIngressResponse),
        ],
    )
    def test_ingress_routes_declare_named_response_models(self, path: str, response_model: object) -> None:
        route = _get_route(path, "POST")

        assert route.response_model == response_model

    def test_ingress_routes_do_not_fast_fail_on_celery_control_plane(self) -> None:
        for path in ("/result", "/event", "/external"):
            route = _get_route(path, "POST")
            route_dependency_names = [
                getattr(dependency.call, "__name__", type(dependency.call).__name__)
                for dependency in route.dependant.dependencies
            ]

            assert "fast_fail_check" not in route_dependency_names


class TestCallbackEnqueueFallback:
    @pytest.mark.asyncio
    async def test_commit_succeeds_when_enqueue_is_unavailable(self, db_session: AsyncSession) -> None:
        from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

        service = CallbackOrchestrationService()

        with patch(
            "src.app.callback.services.callback_orchestration_service.publish_deferred_sse_events",
            new=AsyncMock(),
        ):
            await service._commit_and_enqueue_workline_processing(
                db_session,
                enqueue_processing=MagicMock(side_effect=RuntimeError("celery down")),
            )

        db_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_external_records_full_box_exchange_callback(self) -> None:
        from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

        calls: list[dict[str, Any]] = []

        class FakeInboxService:
            async def create_external_http_inbox(self, **kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(id=321, trace_id=kwargs["trace_id"])

        class RecordingFullBoxExchangeTaskService:
            async def record_callback_from_external_http(self, **kwargs: Any) -> None:
                calls.append(kwargs)

        service = CallbackOrchestrationService(full_box_exchange_task_service=RecordingFullBoxExchangeTaskService())
        service._commit_and_enqueue_workline_processing = AsyncMock()  # type: ignore[method-assign]
        db = SimpleNamespace()
        payload = create_wms_external_payload(
            callback_type="WMS_FULL_BOX_EXCHANGE_RESULT",
            exchange_request_code="external:smt:release-001:FULL_BIN_EXCHANGE",
            rack_release_id="release-001",
            wms_rcs_task_id="wms-task-001",
            exchange_status="PHYSICAL_COMPLETED",
            post_exchange_relations={"rack_code": "RACK-002"},
        )

        outcome = await service.process_external(
            db,  # type: ignore[arg-type]
            callback_type="WMS_FULL_BOX_EXCHANGE_RESULT",
            payload=payload,
            request_id="req-wms-physical",
            trace_id="trace-wms-001",
            inbox_service=FakeInboxService(),  # type: ignore[arg-type]
            enqueue_processing=lambda: None,
        )

        assert outcome.trace_id == "trace-wms-001"
        assert len(calls) == 1
        assert calls[0]["db"] is db
        assert calls[0]["payload_json"]["exchange_status"] == "PHYSICAL_COMPLETED"
        assert calls[0]["trace_id"] == "trace-wms-001"


class TestCallbackResultAPI:
    @pytest.mark.asyncio
    async def test_callback_result_success(self, db_session: AsyncSession, build_request: RequestFactory) -> None:
        existing_command = SimpleNamespace(
            trace_id="trace-001",
            params={"task_type": "PICK_AND_PUT"},
            workline_id=1,
            plugin_key="smt_classifier",
            contract_version="1.0",
            device_id=7,
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
                "src.app.callback.services.callback_ingress_service.device_command_service.get_command_by_code",
                new=AsyncMock(return_value=existing_command),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_service.get_device_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        work_line_id=1,
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                    )
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(
                                id=7,
                                device_code="ARM_01",
                                capabilities_json={"supports_result_callback": True},
                            ),
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
                "src.app.callback.services.callback_ingress_service.inbox_service.create_command_result_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.device_command_service.handle_callback_result",
                new=AsyncMock(return_value=handled_command),
            ) as mock_handle,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback._enqueue_workline_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-001"),
            patch(
                "src.app.callback.services.callback_ingress_service.device_service.mark_command_finished",
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
        assert _response_data(response)["ack"] is True
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
                "src.app.callback.services.callback_ingress_service.device_command_service.get_command_by_code",
                new=AsyncMock(side_effect=get_command_by_code),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(side_effect=resolve_device_context),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_command_result_inbox",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_command_service.handle_callback_result",
                new=AsyncMock(return_value=handled_command),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
            patch("src.app.callback.v1.callback._enqueue_workline_processing"),
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-trace-001"),
            patch(
                "src.app.callback.services.callback_ingress_service.device_service.mark_command_finished",
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
        assert _response_data(response)["ack"] is True
        assert _response_data(response)["request_id"] == "req-trace-001"
        assert _response_data(response)["trace_id"] == "trace-vendor-001"
        assert _response_data(response)["event_id"] == "evt-result-001"
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["trace_id"] == "trace-vendor-001"

    @pytest.mark.asyncio
    async def test_callback_result_rejects_command_device_mismatch(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        existing_command = SimpleNamespace(
            id=1001,
            command_code="CMD-20250317-MISMATCH",
            trace_id="trace-mismatch-001",
            params={"task_type": "PICK_AND_PUT"},
            workline_id=1,
            plugin_key="smt_classifier",
            contract_version="1.0",
            session_id=None,
            device_id=8,
        )

        with (
            patch(
                "src.app.callback.services.callback_ingress_service.device_command_service.get_command_by_code",
                new=AsyncMock(return_value=existing_command),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(
                                id=7,
                                device_code="ARM_01",
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
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_command_service.handle_callback_result",
                new=AsyncMock(),
            ) as mock_handle,
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_command_result_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-mismatch-001"),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(
                    body=create_result_payload(command_code="CMD-20250317-MISMATCH"),
                    path="/api/v1/callback/result",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert _response_data(response)["ack"] is False
        assert "不匹配" in response["message"]
        mock_handle.assert_not_awaited()
        mock_create_inbox.assert_not_awaited()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "CONTRACT_VALIDATE"
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_result_rejects_legacy_command_id_and_device_id(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.device_command_service.get_command_by_code",
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
                "src.app.callback.services.callback_ingress_service.device_service.get_device_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        work_line_id=1,
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                    )
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(
                                id=7,
                                device_code="ARM_01",
                                capabilities_json={"supports_result_callback": True},
                            ),
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
                "src.app.callback.services.callback_ingress_service.inbox_service.create_command_result_inbox",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_command_service.handle_callback_result",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        trace_id="trace-001",
                        status=SimpleNamespace(value="SUCCESS"),
                        get_duration_ms=MagicMock(return_value=100),
                    )
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["ack"] is False
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
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(),
            ) as mock_resolve,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["ack"] is False
        assert "业务字段必须放在 data 中" in response["message"]
        mock_resolve.assert_not_awaited()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_result_rejects_invalid_runtime_result(
        self, db_session: AsyncSession, build_request: RequestFactory
    ) -> None:
        existing_command = SimpleNamespace(
            trace_id="trace-001",
            params={"action": "PICK_AND_PUT"},
            workline_id=1,
            plugin_key="smt_classifier",
            contract_version="1.0",
            device_id=7,
        )

        with (
            patch(
                "src.app.callback.services.callback_ingress_service.device_command_service.get_command_by_code",
                new=AsyncMock(return_value=existing_command),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_service.get_device_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        work_line_id=1,
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                    )
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(
                                id=7,
                                device_code="ARM_01",
                                capabilities_json={"supports_result_callback": True},
                            ),
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
                "src.app.callback.services.callback_ingress_service.inbox_service.create_command_result_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.device_command_service.handle_callback_result",
                new=AsyncMock(),
            ) as mock_handle,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["ack"] is False
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
                "src.app.callback.services.callback_ingress_service.device_command_service.get_command_by_code",
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
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(return_value=(None, {"code": 404, "message": "未找到设备: ARM_01"})),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(id=7, device_code="ARM_01", capabilities_json=[]),
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
                "src.app.callback.services.callback_ingress_service.device_command_service.get_command_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        trace_id="trace-cap-bad-001",
                        params={"task_type": "PICK_AND_PUT"},
                        workline_id=1,
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                        device_id=7,
                    )
                ),
            ) as mock_get_command,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["ack"] is False
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
                "src.app.callback.services.callback_ingress_service.device_service.get_device_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        work_line_id=1,
                        plugin_key="smt_classifier",
                        contract_version="1.0",
                    )
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
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
                "src.app.callback.services.callback_ingress_service.inbox_service.create_device_event_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["status"] == "submitted"
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
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(),
            ) as mock_resolve,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["ack"] is False
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
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["ack"] is False
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
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(),
            ) as mock_resolve,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["ack"] is False
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
                "src.app.callback.services.callback_ingress_service.device_service.get_device_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        work_line_id=1,
                        plugin_key="smt_classifier",  # 使用有效的插件
                        contract_version="1.0",
                    )
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
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
                "src.app.callback.services.callback_ingress_service.inbox_service.create_device_event_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["status"] == "submitted"
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
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
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
                "src.app.callback.services.callback_ingress_service.inbox_service.create_device_event_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["status"] == "submitted"
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
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
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
                "src.app.callback.services.callback_ingress_service.inbox_service.create_device_event_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["ack"] is False
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
                "src.app.callback.services.callback_ingress_service.inbox_service.create_external_http_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["status"] == "submitted"
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
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
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
        assert _response_data(response)["ack"] is False
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "missing_field",
        [
            "dispatch_key",
            "source_system",
            "source_event_id",
            "source_version",
            "occurred_at",
            "request_id",
            "timestamp",
            "signature",
        ],
    )
    async def test_callback_external_rejects_wms_rcs_execution_missing_required_envelope_field(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
        missing_field: str,
    ) -> None:
        payload = create_wms_external_payload()
        payload.pop(missing_field)
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_external_http_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-missing-field"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(body=payload, path="/api/v1/callback/external"),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert _response_data(response)["ack"] is False
        mock_create_inbox.assert_not_awaited()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        assert missing_field in str(log_kwargs["error_message"])
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_external_rejects_full_box_physical_completed_without_post_exchange_relations(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_external_http_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-physical"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_wms_external_payload(
                        callback_type="WMS_FULL_BOX_EXCHANGE_RESULT",
                        exchange_request_code="external:smt:release-001:FULL_BIN_EXCHANGE",
                        rack_release_id="release-001",
                        wms_rcs_task_id="wms-task-001",
                        exchange_status="PHYSICAL_COMPLETED",
                    ),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert _response_data(response)["ack"] is False
        mock_create_inbox.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_callback_external_rejects_full_box_wms_confirmed_without_confirmation(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_external_http_inbox",
                new=AsyncMock(),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-confirmed"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_wms_external_payload(
                        callback_type="WMS_FULL_BOX_EXCHANGE_RESULT",
                        exchange_request_code="external:smt:release-001:FULL_BIN_EXCHANGE",
                        rack_release_id="release-001",
                        wms_rcs_task_id="wms-task-001",
                        exchange_status="WMS_CONFIRMED",
                    ),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert _response_data(response)["ack"] is False
        mock_create_inbox.assert_not_awaited()
