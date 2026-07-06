"""Callback ingress route 与编排兜底测试。"""

import importlib
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.callback.models import (
    CallbackEventIngressResponse,
    CallbackExternalIngressResponse,
    CallbackResultIngressResponse,
)
from src.app.workline.trace_context import TraceContext
from src.core.conf import settings
from tests.api import callback_test_support
from tests.api.callback_test_support import (
    RequestFactory,
    _get_route,
    callback_ingress_module,
    create_full_box_exchange_external_payload,
    create_result_payload,
    create_wms_external_payload,
)


@pytest.fixture(autouse=True)
def mock_fast_fail_check():
    yield from callback_test_support.mock_fast_fail_check.__wrapped__()


@pytest.fixture
def db_session() -> AsyncSession:
    return callback_test_support.db_session.__wrapped__()


@pytest.fixture
def build_request() -> RequestFactory:
    return callback_test_support.build_request.__wrapped__()


def _runtime_inbox_writer_stub(*, created: bool = True) -> SimpleNamespace:
    result = SimpleNamespace(created=created, record=SimpleNamespace(id=901))
    return SimpleNamespace(
        write_result_callback=AsyncMock(return_value=result),
        write_event_callback=AsyncMock(return_value=result),
        write_external_callback=AsyncMock(return_value=result),
    )


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

        with patch("src.app.runtime.orchestration.repositories.session_repository.WorklineSessionRepository", RepoStub):
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

        service = CallbackOrchestrationService(
            rack_task_service=RecordingRackTaskService(),
            runtime_inbox_writer=_runtime_inbox_writer_stub(),
        )
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
            runtime_inbox_writer=_runtime_inbox_writer_stub(),
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
            runtime_inbox_writer=_runtime_inbox_writer_stub(),
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
            runtime_inbox_writer=_runtime_inbox_writer_stub(),
        )
        service._commit_and_enqueue_workline_processing = AsyncMock()  # type: ignore[method-assign]
        db = SimpleNamespace(commit=AsyncMock())

        with patch(
            "src.app.callback.services.callback_orchestration_service.publish_deferred_sse_events",
            new=AsyncMock(),
        ):
            outcome = await service.process_external(
                db,  # type: ignore[arg-type]
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

        assert outcome.is_duplicate is False
        db.commit.assert_awaited_once()
        service._commit_and_enqueue_workline_processing.assert_not_awaited()  # type: ignore[attr-defined]
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

        service = CallbackOrchestrationService(
            rack_task_service=RecordingRackTaskService(),
            runtime_inbox_writer=_runtime_inbox_writer_stub(),
        )
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


class TestCallbackContractBoundary:
    def test_device_models_no_longer_export_event_contract_types(self) -> None:
        device_models = importlib.import_module("src.app.device.models")

        assert not hasattr(device_models, "EventRequest")
        assert not hasattr(device_models, "EventType")
