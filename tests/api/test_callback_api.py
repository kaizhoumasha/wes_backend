"""Callback API 单元测试。"""

import importlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request, Response
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.callback.models import (
    CallbackEventIngressResponse,
    CallbackExternalIngressResponse,
    CallbackResultIngressResponse,
)
from src.app.callback.v1 import callback as callback_module
from src.core.conf import settings
from src.core.response.response_code import ResourceErrorCode
from src.workline_runtime.trace_context import TraceContext

JsonDict = dict[str, object]
RequestFactory = Callable[..., Request]
callback_ingress_module = importlib.import_module("src.app.callback.services.callback_ingress_service")


def _await_kwargs(mock: AsyncMock) -> JsonDict:
    await_args = mock.await_args
    assert await_args is not None
    return cast("JsonDict", await_args.kwargs)


def _response_data(response: JsonDict) -> JsonDict:
    data = response["data"]
    if hasattr(data, "model_dump"):
        return cast("JsonDict", data.model_dump())
    return cast("JsonDict", data)


def _response_model_data(response: JsonDict) -> JsonDict:
    validated = TypeAdapter(CallbackEventIngressResponse).validate_python(response)
    serialized = TypeAdapter(CallbackEventIngressResponse).dump_python(validated, mode="json")
    return _response_data(cast("JsonDict", serialized))


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
                            plugin_key="test_workline_plugin",
                            contract_version="1.0",
                            device_status="ONLINE",
                        ),
                        workline=SimpleNamespace(
                            id=1,
                            is_active=True,
                            plugin_key="test_workline_plugin",
                        ),
                        plugin_key="test_workline_plugin",
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


def test_callback_log_payload_uses_trusted_proxy_client_ip(
    build_request: RequestFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["10.0.0.10"])
    request = build_request(
        body=create_result_payload(),
        path="/api/v1/callback/result",
        client_ip="10.0.0.10",
    )
    request.headers = {
        "User-Agent": "TestClient",
        "X-Real-IP": "203.0.113.10",
    }

    payload = callback_ingress_module._build_callback_log_payload(
        request,
        trace=TraceContext(request_id="req-1", trace_id="trace-1"),
        callback_type="RESULT",
        subject_code="CMD-20250317-001",
        request_body=create_result_payload(),
        response_status=200,
        response_time_ms=12,
    )

    assert payload["client_ip"] == "203.0.113.10"


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
        "dispatch_key": "external:test_workline_plugin:trace-wms-001:RACK_EXCHANGE_AND_SUPPLY",
        "status": "SUCCEEDED",
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


def create_full_box_exchange_external_payload(**overrides: object) -> JsonDict:
    payload: JsonDict = {
        "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
        "trace_id": "trace-full-box-001",
        "dispatch_key": "handling:full-box:release-001:move:1",
        "exchange_request_code": "handling:full-box:release-001:move:1",
        "rack_release_id": "rack-release-001",
        "wms_rcs_task_id": "RCS-TASK-FULL-001",
        "source_system": "WMS",
        "source_event_id": "wms-full-box-event-001",
        "source_version": "1",
        "occurred_at": "2026-05-22T08:00:00Z",
        "request_id": "REQ-FULL-BOX-001",
        "timestamp": "2026-05-22T08:00:01Z",
        "signature": "test-signature",
        "exchange_status": "BUSINESS_COMPLETED",
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
    async def test_load_command_session_does_not_fallback_to_trace_id(self) -> None:
        from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

        class RepoStub:
            instances: ClassVar[list["RepoStub"]] = []

            def __init__(self) -> None:
                self.get_by_trace_id = AsyncMock(return_value=SimpleNamespace(id=99))
                self.get_open_session_by_awaiting_device_command_code = AsyncMock(return_value=None)
                self.__class__.instances.append(self)

        service = CallbackOrchestrationService()
        command = SimpleNamespace(command_code="CMD-404", trace_id="trace-same-but-wrong-session")
        db = SimpleNamespace()

        with patch("src.app.workline.repositories.session_repository.WorklineSessionRepository", RepoStub):
            session = await service._load_command_session(db, command)  # type: ignore[arg-type]

        assert session is None
        repo = RepoStub.instances[0]
        repo.get_open_session_by_awaiting_device_command_code.assert_awaited_once_with(db, "CMD-404")
        repo.get_by_trace_id.assert_not_awaited()

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
    async def test_process_external_records_rack_task_callback(self) -> None:
        from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

        calls: list[dict[str, Any]] = []

        class FakeInboxService:
            async def mark_as_processed(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(id=321)

            async def create_external_http_inbox(self, **kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(id=321, trace_id=kwargs["trace_id"])

        class RecordingRackTaskService:
            async def record_callback_from_external_http(self, **kwargs: Any) -> None:
                calls.append(kwargs)

        service = CallbackOrchestrationService(rack_task_service=RecordingRackTaskService())
        service._commit_and_enqueue_workline_processing = AsyncMock()  # type: ignore[method-assign]
        db = SimpleNamespace()
        payload = create_wms_external_payload(
            callback_type="WMS_RACK_TASK_RESULT",
            dispatch_key="external:smt:release-001:RACK_OPERATION:1",
            status="SUCCEEDED",
        )

        outcome = await service.process_external(
            db,  # type: ignore[arg-type]
            callback_type="WMS_RACK_TASK_RESULT",
            payload=payload,
            request_id="req-wms-physical",
            trace_id="trace-wms-001",
            inbox_service=FakeInboxService(),  # type: ignore[arg-type]
            enqueue_processing=lambda: None,
        )

        assert outcome.trace_id == "trace-wms-001"
        assert len(calls) == 1
        assert calls[0]["db"] is db
        assert calls[0]["payload_json"]["status"] == "SUCCEEDED"
        assert calls[0]["trace_id"] == "trace-wms-001"

    @pytest.mark.asyncio
    async def test_process_external_records_handling_operation_callback(self) -> None:
        from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

        calls: list[dict[str, Any]] = []

        class FakeInboxService:
            async def create_external_http_inbox(self, **kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(id=321, trace_id=kwargs["trace_id"])

            async def mark_as_processed(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(id=321)

        class RecordingRackTaskService:
            async def record_callback_from_external_http(self, **_kwargs: Any) -> None:
                return None

        class RecordingHandlingOperationService:
            async def record_callback_from_external_http(self, **kwargs: Any) -> None:
                calls.append(kwargs)

        service = CallbackOrchestrationService(
            rack_task_service=RecordingRackTaskService(),
            handling_operation_service=RecordingHandlingOperationService(),
        )
        service._commit_and_enqueue_workline_processing = AsyncMock()  # type: ignore[method-assign]
        db = SimpleNamespace()
        payload = create_wms_external_payload(
            callback_type="CTU_BIN_MOVE_COMPLETED",
            dispatch_key="handling:bin-operation:trace-001:move:1",
            status="SUCCEEDED",
        )

        outcome = await service.process_external(
            db,  # type: ignore[arg-type]
            callback_type="CTU_BIN_MOVE_COMPLETED",
            payload=payload,
            request_id="req-ctu-bin-completed",
            trace_id="trace-bin-001",
            inbox_service=FakeInboxService(),  # type: ignore[arg-type]
            enqueue_processing=lambda: None,
        )

        assert outcome.trace_id == "trace-bin-001"
        assert len(calls) == 1
        assert calls[0]["db"] is db
        assert calls[0]["payload_json"]["dispatch_key"] == "handling:bin-operation:trace-001:move:1"
        assert calls[0]["trace_id"] == "trace-bin-001"

    @pytest.mark.asyncio
    async def test_process_external_routes_full_box_exchange_result_to_handling_lifecycle(self) -> None:
        from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

        calls: list[dict[str, Any]] = []

        class FakeInboxService:
            async def create_external_http_inbox(self, **kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(id=321, trace_id=kwargs["trace_id"])

            async def mark_as_processed(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(id=321)

        class RecordingHandlingOperationService:
            async def record_callback_from_external_http(self, **kwargs: Any) -> None:
                calls.append(kwargs)

        service = CallbackOrchestrationService(
            rack_task_service=SimpleNamespace(record_callback_from_external_http=AsyncMock()),
            handling_operation_service=RecordingHandlingOperationService(),
        )
        service._commit_and_enqueue_workline_processing = AsyncMock()  # type: ignore[method-assign]
        db = SimpleNamespace()
        payload = create_full_box_exchange_external_payload(dispatch_key="external:smt_full_box_exchange:release-001")

        outcome = await service.process_external(
            db,  # type: ignore[arg-type]
            callback_type="WMS_FULL_BOX_EXCHANGE_RESULT",
            payload=payload,
            request_id="REQ-FULL-BOX-001",
            trace_id="trace-full-box-001",
            inbox_service=FakeInboxService(),  # type: ignore[arg-type]
            enqueue_processing=lambda: None,
        )

        assert outcome.trace_id == "trace-full-box-001"
        assert len(calls) == 1
        assert calls[0]["db"] is db
        assert calls[0]["payload_json"]["exchange_status"] == "BUSINESS_COMPLETED"

    @pytest.mark.asyncio
    async def test_process_external_duplicate_does_not_record_handling_callback(self) -> None:
        from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

        duplicate_error = ValueError("已存在（幂等键重复）")
        duplicate_error.existing_inbox = SimpleNamespace(id=99, trace_id="trace-bin-001")  # type: ignore[attr-defined]
        handling_operation_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())

        class FakeInboxService:
            async def create_external_http_inbox(self, **_kwargs: Any) -> SimpleNamespace:
                raise duplicate_error

        service = CallbackOrchestrationService(
            rack_task_service=SimpleNamespace(record_callback_from_external_http=AsyncMock()),
            handling_operation_service=handling_operation_service,
        )
        service._commit_and_enqueue_workline_processing = AsyncMock()  # type: ignore[method-assign]

        outcome = await service.process_external(
            SimpleNamespace(),  # type: ignore[arg-type]
            callback_type="CTU_BIN_MOVE_COMPLETED",
            payload=create_wms_external_payload(
                callback_type="CTU_BIN_MOVE_COMPLETED",
                dispatch_key="handling:bin-operation:trace-001:move:1",
            ),
            request_id="req-duplicate-ctu",
            trace_id="trace-bin-001",
            inbox_service=FakeInboxService(),  # type: ignore[arg-type]
            enqueue_processing=lambda: None,
        )

        assert outcome.is_duplicate is True
        handling_operation_service.record_callback_from_external_http.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_external_lifecycle_only_rack_callback_does_not_enqueue_default_processor(self) -> None:
        from src.app.callback.services.callback_orchestration_service import CallbackOrchestrationService

        class FakeInboxService:
            def __init__(self) -> None:
                self.mark_processed_calls: list[tuple[Any, int, bool]] = []

            async def create_external_http_inbox(self, **kwargs: Any) -> SimpleNamespace:
                return SimpleNamespace(id=321, trace_id=kwargs["trace_id"])

            async def mark_as_processed(
                self,
                db: Any,
                inbox_id: int,
                *,
                auto_commit: bool = True,
            ) -> SimpleNamespace:
                self.mark_processed_calls.append((db, inbox_id, auto_commit))
                return SimpleNamespace(id=inbox_id)

        class RecordingRackTaskService:
            async def record_callback_from_external_http(self, **_kwargs: Any) -> None:
                return None

        service = CallbackOrchestrationService(rack_task_service=RecordingRackTaskService())
        service._enqueue_workline_processing = MagicMock()  # type: ignore[method-assign]
        inbox_service = FakeInboxService()
        db = SimpleNamespace(commit=AsyncMock())
        payload = create_wms_external_payload(
            callback_type="WMS_RACK_TASK_RESULT",
            dispatch_key="external:smt:release-001:RACK_OPERATION:1",
            status="SUCCEEDED",
        )

        with patch(
            "src.app.callback.services.callback_orchestration_service.publish_deferred_sse_events",
            new=AsyncMock(),
        ):
            await service.process_external(
                db,  # type: ignore[arg-type]
                callback_type="WMS_RACK_TASK_RESULT",
                payload=payload,
                request_id="req-wms-lifecycle-only",
                trace_id="trace-wms-lifecycle-only",
                inbox_service=inbox_service,  # type: ignore[arg-type]
            )

        db.commit.assert_awaited_once()
        assert inbox_service.mark_processed_calls == [(db, 321, False)]
        service._enqueue_workline_processing.assert_not_called()


class TestCallbackResultAPI:
    @pytest.mark.asyncio
    async def test_callback_result_success(self, db_session: AsyncSession, build_request: RequestFactory) -> None:
        existing_command = SimpleNamespace(
            trace_id="trace-001",
            task_type="PICK_AND_PUT",
            params={},
            workline_id=1,
            plugin_key="test_workline_plugin",
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
                        plugin_key="test_workline_plugin",
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
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                            ),
                            plugin_key="test_workline_plugin",
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
                request=build_request(
                    body=create_result_payload(data={"command_type": "TEST", "task_type": "TEST"}),
                    path="/api/v1/callback/result",
                ),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert _response_data(response)["ack"] is True
        assert mock_create_inbox.call_args.kwargs["task_type"] == "PICK_AND_PUT"
        assert "command_type" not in mock_create_inbox.call_args.kwargs
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
            plugin_key="test_workline_plugin",
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
                        plugin_key="test_workline_plugin",
                        contract_version="1.0",
                        is_active=True,
                    ),
                    plugin_key="test_workline_plugin",
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
            plugin_key="test_workline_plugin",
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
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                            ),
                            plugin_key="test_workline_plugin",
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
                        plugin_key="test_workline_plugin",
                        contract_version="1.0",
                    )
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_service.get_device_by_code",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        work_line_id=1,
                        plugin_key="test_workline_plugin",
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
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                            ),
                            plugin_key="test_workline_plugin",
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
            task_type="PICK_AND_PUT",
            params={},
            workline_id=1,
            plugin_key="test_workline_plugin",
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
                        plugin_key="test_workline_plugin",
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
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                            ),
                            plugin_key="test_workline_plugin",
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
                        plugin_key="test_workline_plugin",
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

        assert response["code"] == ResourceErrorCode.NOT_FOUND.code
        assert response["message"] == "未找到设备: ARM_01"
        data = _response_data(response)
        assert data["ack"] is False
        assert data["reason_code"] == ResourceErrorCode.NOT_FOUND.code
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
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                            ),
                            plugin_key="test_workline_plugin",
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
                        plugin_key="test_workline_plugin",
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
                        plugin_key="test_workline_plugin",
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
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                            ),
                            plugin_key="test_workline_plugin",
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
                response=Response(),
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
                response=Response(),
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
                response=Response(),
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
                response=Response(),
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
    async def test_callback_event_rejects_device_context_failure_as_response_model(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        http_response = Response()
        with (
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-event-ctx-001"),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(body=create_event_payload(), path="/api/v1/callback/event"),
                db=db_session,
                response=http_response,
            )

        assert http_response.status_code == 404
        assert response["code"] == ResourceErrorCode.NOT_FOUND.code
        assert response["message"] == "未找到设备: ARM_01"
        data = _response_data(response)
        assert data["ack"] is False
        assert data["reason_code"] == ResourceErrorCode.NOT_FOUND.code
        response_model_data = _response_model_data(response)
        assert response_model_data["ack"] is False
        assert response_model_data["reason_code"] == ResourceErrorCode.NOT_FOUND.code
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "DEVICE_CONTEXT_RESOLVE"
        assert log_kwargs["response_status"] == 404
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
                        plugin_key="test_workline_plugin",  # 使用有效的插件
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
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                            ),
                            plugin_key="test_workline_plugin",
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
                response=Response(),
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
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                                runtime_config_json={"event_type_mapping": {"SCAN_FINISH": "SCAN_COMPLETED"}},
                            ),
                            plugin_key="test_workline_plugin",
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
                response=Response(),
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
    async def test_callback_event_rejects_mapping_to_platform_start(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        http_response = Response()
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(capabilities_json={"supports_event_types": ["SCAN_COMPLETED"]}),
                            workline=SimpleNamespace(
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                                runtime_status="STOPPED",
                                runtime_config_json={"event_type_mapping": {"SCAN_FINISH": "WORKLINE_START_REQUESTED"}},
                            ),
                            plugin_key="test_workline_plugin",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.start_admission_service.admit_start_for_device",
                new=AsyncMock(),
            ) as mock_admit_start,
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-start-mapping-001"),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(
                    body=create_event_payload(event_type="SCAN_FINISH"),
                    path="/api/v1/callback/event",
                ),
                db=db_session,
                response=http_response,
            )

        assert response["code"] == "2004"
        data = _response_data(response)
        assert data["ack"] is False
        mock_admit_start.assert_not_awaited()
        mock_create_inbox.assert_not_awaited()
        mock_enqueue.assert_not_called()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["failure_stage"] == "CONTRACT_VALIDATE"
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
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                            ),
                            plugin_key="test_workline_plugin",
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
                response=Response(),
            )

        assert response["code"] == "2004"
        assert _response_data(response)["ack"] is False
        mock_create_inbox.assert_not_called()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "CONFIG_VALIDATE"
        mock_audit.assert_awaited_once()

    @pytest.mark.parametrize("runtime_status", ["STOPPED", "RECONCILING", "ESTOPPED"])
    @pytest.mark.asyncio
    async def test_callback_event_rejects_production_event_when_workline_not_accepting_work(
        self,
        runtime_status: str,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        http_response = Response()
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(
                                capabilities_json={"supports_event_types": ["SCAN_COMPLETED"]},
                            ),
                            workline=SimpleNamespace(
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                                runtime_status=runtime_status,
                            ),
                            plugin_key="test_workline_plugin",
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
                return_value=f"req-workline-{runtime_status.lower()}",
            ),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(body=create_event_payload(), path="/api/v1/callback/event"),
                db=db_session,
                response=http_response,
            )

        assert http_response.status_code == 409
        assert response["code"] == ResourceErrorCode.CONFLICT.code
        data = _response_data(response)
        assert data["ack"] is False
        assert data["reason_code"] == "WORKLINE_NOT_ACCEPTING_WORK"
        mock_create_inbox.assert_not_awaited()
        mock_enqueue.assert_not_called()
        db_session.commit.assert_not_awaited()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["response_status"] == 409
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "WORKLINE_GUARD"
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_event_accepts_estop_even_when_workline_not_ready(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        http_response = Response()
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(
                                capabilities_json={"supports_event_types": ["SCAN_COMPLETED"]},
                            ),
                            workline=SimpleNamespace(
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                                runtime_status="ESTOPPED",
                            ),
                            plugin_key="test_workline_plugin",
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-estop-001"),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(
                    body=create_event_payload(event_type="ESTOP_PRESSED"),
                    path="/api/v1/callback/event",
                ),
                db=db_session,
                response=http_response,
            )

        assert http_response.status_code == 200
        assert response["code"] == "1000"
        assert _response_data(response)["status"] == "submitted"
        inbox_kwargs = _await_kwargs(mock_create_inbox)
        assert inbox_kwargs["event_type"] == "ESTOP_PRESSED"
        assert inbox_kwargs["canonical_event_type"] == "ESTOP_PRESSED"
        mock_enqueue.assert_called_once()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_event_accepts_start_without_production_capability_gate(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        from src.app.workline.services.start_admission_service import StartAdmissionResult

        http_response = Response()
        admission_result = StartAdmissionResult(
            accepted=True,
            http_status=200,
            reason_code=None,
            message="START 准入通过",
            workline_id=1,
            diagnostic={"checked_devices": ["ARM_01"]},
        )
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(
                                capabilities_json={"supports_event_types": ["SCAN_COMPLETED"]},
                            ),
                            workline=SimpleNamespace(
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                                runtime_status="STOPPED",
                            ),
                            plugin_key="test_workline_plugin",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.start_admission_service.admit_start_for_device",
                new=AsyncMock(return_value=admission_result),
            ) as mock_admit_start,
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-start-001"),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(
                    body=create_event_payload(event_type="WORKLINE_START_REQUESTED"),
                    path="/api/v1/callback/event",
                ),
                db=db_session,
                response=http_response,
            )

        assert http_response.status_code == 200
        assert response["code"] == "1000"
        data = _response_data(response)
        assert data["status"] == "accepted"
        assert data["device_code"] == "ARM_01"
        response_model_data = _response_model_data(response)
        assert response_model_data["status"] == "accepted"
        assert response_model_data["diagnostic"] == {"checked_devices": ["ARM_01"]}
        mock_admit_start.assert_awaited_once()
        admit_kwargs = _await_kwargs(mock_admit_start)
        assert admit_kwargs["device_code"] == "ARM_01"
        assert admit_kwargs["request_id"] == "req-start-001"
        mock_create_inbox.assert_not_awaited()
        mock_enqueue.assert_not_called()
        mock_log_callback.assert_awaited_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_event_start_admission_response_time_includes_admission_duration(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.app.workline.services.start_admission_service import StartAdmissionResult

        current_time = {"value": 100.01}

        async def slow_admit_start(*_args: object, **_kwargs: object) -> StartAdmissionResult:
            current_time["value"] = 102.5
            return StartAdmissionResult(
                accepted=True,
                http_status=200,
                reason_code=None,
                message="START 准入通过",
                workline_id=1,
                diagnostic={},
            )

        monkeypatch.setattr(callback_ingress_module.time, "time", lambda: current_time["value"])
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(
                                capabilities_json={"supports_event_types": ["SCAN_COMPLETED"]},
                            ),
                            workline=SimpleNamespace(
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                                runtime_status="STOPPED",
                            ),
                            plugin_key="test_workline_plugin",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.start_admission_service.admit_start_for_device",
                new=AsyncMock(side_effect=slow_admit_start),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
        ):
            decision = await callback_ingress_module.handle_callback_event(
                build_request(
                    body=create_event_payload(event_type="WORKLINE_START_REQUESTED"),
                    path="/api/v1/callback/event",
                ),
                db_session,
                request_id="req-start-slow",
                start_time=100.0,
                enqueue_processing=lambda: None,
            )

        assert decision.http_status == 200
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["response_time_ms"] == 2500

    @pytest.mark.asyncio
    async def test_callback_event_rejects_start_when_admission_fails(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        from src.app.workline.services.start_admission_service import StartAdmissionResult

        http_response = Response()
        admission_result = StartAdmissionResult(
            accepted=False,
            http_status=409,
            reason_code="START_ADMISSION_DEVICE_NOT_IDLE",
            message="START 准入失败: 设备 RS-CONV-01 非空闲",
            workline_id=1,
            diagnostic={"device_code": "RS-CONV-01", "status": "RUNNING"},
        )
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(
                                capabilities_json={"supports_event_types": ["SCAN_COMPLETED"]},
                            ),
                            workline=SimpleNamespace(
                                plugin_key="test_workline_plugin",
                                contract_version="1.0",
                                is_active=True,
                                runtime_status="STOPPED",
                            ),
                            plugin_key="test_workline_plugin",
                            contract_version="1.0",
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.start_admission_service.admit_start_for_device",
                new=AsyncMock(return_value=admission_result),
            ) as mock_admit_start,
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-start-fail"),
        ):
            from src.app.callback.v1.callback import callback_event

            response = await callback_event(
                request=build_request(
                    body=create_event_payload(event_type="WORKLINE_START_REQUESTED"),
                    path="/api/v1/callback/event",
                ),
                db=db_session,
                response=http_response,
            )

        assert http_response.status_code == 409
        assert response["code"] == ResourceErrorCode.CONFLICT.code
        data = _response_data(response)
        assert data["ack"] is False
        assert data["reason_code"] == "START_ADMISSION_DEVICE_NOT_IDLE"
        assert data["diagnostic"]["device_code"] == "RS-CONV-01"
        response_model_data = _response_model_data(response)
        assert response_model_data["ack"] is False
        assert response_model_data["reason_code"] == "START_ADMISSION_DEVICE_NOT_IDLE"
        assert response_model_data["diagnostic"]["device_code"] == "RS-CONV-01"
        mock_admit_start.assert_awaited_once()
        mock_create_inbox.assert_not_awaited()
        mock_enqueue.assert_not_called()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["response_status"] == 409
        assert log_kwargs["failure_stage"] == "WORKLINE_GUARD"
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
    async def test_callback_external_accepts_rack_task_callback_without_trace_id(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_external_http_inbox",
                new=AsyncMock(return_value=SimpleNamespace(id=321, trace_id="trace-generated-001")),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.mark_as_processed",
                new=AsyncMock(),
            ) as mock_mark_processed,
            patch(
                "src.app.callback.services.callback_orchestration_service.callback_orchestration_service._resolve_rack_task_service",
                return_value=rack_task_service,
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-no-trace"),
        ):
            from src.app.callback.v1.callback import callback_external

            payload = create_wms_external_payload(
                callback_type="WMS_RACK_TASK_RESULT",
                dispatch_key="external:smt:release-001:RACK_OPERATION:1",
                status="SUCCEEDED",
            )
            payload.pop("trace_id")
            response = await callback_external(
                request=build_request(body=payload, path="/api/v1/callback/external"),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert _response_data(response)["status"] == "submitted"
        assert _response_data(response)["trace_id"] == "req-wms-no-trace"
        create_kwargs = _await_kwargs(mock_create_inbox)
        assert create_kwargs["trace_id"] == "req-wms-no-trace"
        rack_task_service.record_callback_from_external_http.assert_awaited_once()
        mock_mark_processed.assert_awaited_once_with(db_session, 321, auto_commit=False)
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["trace_id"] == "req-wms-no-trace"
        assert log_kwargs["ingress_outcome"] == "ACCEPTED"

    @pytest.mark.asyncio
    async def test_callback_external_rejects_non_rack_wms_callback_without_trace_id(
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
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-non-rack-no-trace"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_wms_external_payload(callback_type="WMS_INVENTORY_STATUS", trace_id=""),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert _response_data(response)["ack"] is False
        mock_create_inbox.assert_not_awaited()
        mock_log_callback.assert_awaited_once()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        assert "trace_id is required" in str(log_kwargs["error_message"])
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_external_accepts_wms_rack_arrived_without_status(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_external_http_inbox",
                new=AsyncMock(return_value=SimpleNamespace(id=321, trace_id="trace-wms-rack-success")),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.mark_as_processed",
                new=AsyncMock(),
            ) as mock_mark_processed,
            patch(
                "src.app.callback.services.callback_orchestration_service.callback_orchestration_service._resolve_rack_task_service",
                return_value=rack_task_service,
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback._enqueue_workline_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-rack-arrived"),
        ):
            from src.app.callback.v1.callback import callback_external

            payload = create_wms_external_payload(
                callback_type="WMS_RACK_ARRIVED",
                dispatch_key="rack-operation:op-001:2:ALLOCATE_AND_MOVE_RACK",
            )
            payload.pop("status")
            response = await callback_external(
                request=build_request(body=payload, path="/api/v1/callback/external"),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert _response_data(response)["status"] == "submitted"
        mock_create_inbox.assert_awaited_once()
        rack_task_service.record_callback_from_external_http.assert_awaited_once()
        mock_mark_processed.assert_not_awaited()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "ACCEPTED"
        mock_enqueue.assert_called_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.parametrize(
        "missing_field",
        [
            "dispatch_key",
            "status",
            "source_system",
            "source_event_id",
            "source_version",
            "occurred_at",
            "request_id",
            "timestamp",
            "signature",
        ],
    )
    async def test_callback_external_rejects_wms_rcs_task_result_missing_required_envelope_field(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
        missing_field: str,
    ) -> None:
        payload = create_wms_external_payload(callback_type="WMS_RACK_TASK_RESULT")
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

    @pytest.mark.parametrize(
        "missing_field",
        [
            "dispatch_key",
            "exchange_request_code",
            "rack_release_id",
            "wms_rcs_task_id",
            "source_system",
            "source_event_id",
            "source_version",
            "occurred_at",
            "request_id",
            "timestamp",
            "signature",
            "exchange_status",
        ],
    )
    async def test_callback_external_rejects_full_box_exchange_missing_required_envelope_field(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
        missing_field: str,
    ) -> None:
        payload = create_full_box_exchange_external_payload()
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-full-box-missing-field"),
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
    async def test_callback_external_accepts_full_box_exchange_business_completed(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        handling_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_external_http_inbox",
                new=AsyncMock(return_value=SimpleNamespace(id=321, trace_id="trace-full-box-001")),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_orchestration_service.callback_orchestration_service._resolve_handling_operation_service",
                return_value=handling_service,
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback._enqueue_workline_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", return_value="REQ-FULL-BOX-001"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_full_box_exchange_external_payload(),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert _response_data(response)["status"] == "submitted"
        mock_create_inbox.assert_awaited_once()
        handling_service.record_callback_from_external_http.assert_awaited_once()
        callback_kwargs = handling_service.record_callback_from_external_http.await_args.kwargs
        assert callback_kwargs["payload_json"]["exchange_status"] == "BUSINESS_COMPLETED"
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "ACCEPTED"
        mock_enqueue.assert_called_once()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_external_accepts_generic_rack_task_success(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_external_http_inbox",
                new=AsyncMock(return_value=SimpleNamespace(id=321, trace_id="trace-wms-rack-success")),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.mark_as_processed",
                new=AsyncMock(),
            ) as mock_mark_processed,
            patch(
                "src.app.callback.services.callback_orchestration_service.callback_orchestration_service._resolve_rack_task_service",
                return_value=rack_task_service,
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback._enqueue_workline_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-rack-success"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_wms_external_payload(
                        callback_type="WMS_RACK_TASK_RESULT",
                        dispatch_key="external:smt:release-001:RACK_OPERATION:1",
                        status="SUCCEEDED",
                    ),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert _response_data(response)["status"] == "submitted"
        mock_create_inbox.assert_awaited_once()
        rack_task_service.record_callback_from_external_http.assert_awaited_once()
        mock_mark_processed.assert_awaited_once_with(db_session, 321, auto_commit=False)
        callback_kwargs = rack_task_service.record_callback_from_external_http.await_args.kwargs
        assert callback_kwargs["payload_json"]["dispatch_key"] == "external:smt:release-001:RACK_OPERATION:1"
        assert callback_kwargs["payload_json"]["status"] == "SUCCEEDED"
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "ACCEPTED"
        mock_enqueue.assert_not_called()
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_external_accepts_generic_rack_task_failure(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_external_http_inbox",
                new=AsyncMock(return_value=SimpleNamespace(id=322, trace_id="trace-wms-rack-failed")),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.mark_as_processed",
                new=AsyncMock(),
            ) as mock_mark_processed,
            patch(
                "src.app.callback.services.callback_orchestration_service.callback_orchestration_service._resolve_rack_task_service",
                return_value=rack_task_service,
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
            patch("src.app.callback.v1.callback._enqueue_workline_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-rack-failed"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_wms_external_payload(
                        callback_type="RCS_RACK_TASK_RESULT",
                        dispatch_key="external:smt:release-001:RACK_OPERATION:2",
                        task_status="FAILED",
                        status=None,
                        reason_code="RCS_RACK_OPERATION_FAILED",
                        reason_message="外部系统拒绝",
                    ),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert _response_data(response)["status"] == "submitted"
        rack_task_service.record_callback_from_external_http.assert_awaited_once()
        mock_mark_processed.assert_awaited_once_with(db_session, 322, auto_commit=False)
        callback_kwargs = rack_task_service.record_callback_from_external_http.await_args.kwargs
        assert callback_kwargs["payload_json"]["task_status"] == "FAILED"
        assert callback_kwargs["payload_json"]["reason_code"] == "RCS_RACK_OPERATION_FAILED"
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "ACCEPTED"
        mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_external_duplicate_does_not_record_rack_task_again(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        duplicate_error = ValueError("外部 HTTP 回调已存在（幂等键重复）")
        duplicate_error.existing_inbox = SimpleNamespace(id=99, trace_id="trace-wms-001")  # type: ignore[attr-defined]
        rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_external_http_inbox",
                new=AsyncMock(side_effect=duplicate_error),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_orchestration_service.callback_orchestration_service._resolve_rack_task_service",
                return_value=rack_task_service,
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as mock_log_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as mock_audit,
            patch("src.app.callback.v1.callback._enqueue_workline_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-duplicate"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_wms_external_payload(
                        callback_type="WMS_RACK_TASK_RESULT",
                        dispatch_key="external:smt:release-001:RACK_OPERATION:1",
                        status="SUCCEEDED",
                    ),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert _response_data(response)["status"] == "duplicate"
        mock_create_inbox.assert_awaited_once()
        rack_task_service.record_callback_from_external_http.assert_not_awaited()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "DUPLICATE"
        mock_enqueue.assert_not_called()
        mock_audit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_callback_external_h4_accepts_wms_rack_operation_protocol_fields(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        """H4 边界: 接受 WMS 货架操作协议顶层业务字段。

        真实 WMS mock 集成测试 (test_real_mock_driven_sandbox_e2e) 通过
        _rack_operation_callback_payload 发送 rack_code/rack_kind/position_code/
        operation_key/operation_type/bin_mounts/material/task_type 等顶层业务
        字段; 这些是 WMS 协议的合法业务元数据, 不是 H4 关注的安全注入面。
        H4 子层守卫 (_FORBIDDEN_PARAM_KEYS 递归扫描) 仍阻断 plc_address /
        coordinate 等设备控制字段; 本测试只覆盖顶层白名单扩展的契约。

        注: 同样适用于 WMS 失败 payload 顶层 error_code/error_message
        (build_rack_operation_failure_payload 返回的诊断结构)。
        """
        rack_task_service = SimpleNamespace(record_callback_from_external_http=AsyncMock())
        wms_rack_op_payload = create_wms_external_payload(
            callback_type="WMS_RACK_ARRIVED",
            dispatch_key="external:smt:release-001:RACK_OPERATION:1",
            status="SUCCEEDED",
            # WMS 协议顶层业务字段 (Phase 1 H4 边界白名单扩展):
            rack_code="RACK-3C-001",
            rack_kind="SINGLE_LAYER",
            position_code="SINGLE_LAYER_A",
            target_position_code="SINGLE_LAYER_A",
            target_position_role="INPUT",
            source_position_code="INPUT_STAGE_1",
            operation_key="op-key-001",
            operation_type="ALLOCATE_AND_MOVE_RACK",
            task_type="ALLOCATE_AND_MOVE_RACK",
            workline_code="LINE-SMT-01",
            sequence_no=1,
            actions=[{"step": "MOVE_RACK", "from": "INPUT", "target": "SINGLE_LAYER_A"}],
            bin_mounts=[{"rack_code": "RACK-3C-001", "rack_slot_code": "A1", "bin_code": "BIN-001"}],
            material={"material_code": "MAT-001", "quantity": 1.0},
            source="INPUT_STAGE_1",
            station="STATION-01",
            target="SINGLE_LAYER_A",
        )
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.create_external_http_inbox",
                new=AsyncMock(return_value=SimpleNamespace(id=421, trace_id="trace-wms-rack-op")),
            ) as mock_create_inbox,
            patch(
                "src.app.callback.services.callback_ingress_service.inbox_service.mark_as_processed",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.services.callback_orchestration_service.callback_orchestration_service._resolve_rack_task_service",
                return_value=rack_task_service,
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-rack-op-h4"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(body=wms_rack_op_payload, path="/api/v1/callback/external"),
                db=db_session,
            )

        # H4 边界不拒绝 WMS 协议字段, response code 应为 1000 (submitted)
        assert response["code"] == "1000", (
            f"H4 边界应接受 WMS 协议字段, 实际 code={response.get('code')}: {response.get('message', '')}"
        )
        assert _response_data(response)["status"] == "submitted"
        mock_create_inbox.assert_awaited_once()
        # 验证 H4 失败原因码没出现在日志中 (即顶层字段未被拒绝)
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs.get("reason_code") != "CALLBACK_TOP_LEVEL_FIELD_NOT_ALLOWED"
        assert log_kwargs["ingress_outcome"] == "ACCEPTED"
