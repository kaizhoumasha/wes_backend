"""Callback API 幂等性专项测试。"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.runtime.orchestration.services.workline_runtime_status_projection_service import (
    WorkLineRuntimeStatusSnapshot,
)

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


def _runtime_accept_result(*, created: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        created=created,
        record=SimpleNamespace(id=901, source_event_id="runtime-inbox-source-event-901"),
    )


def _runtime_snapshot(runtime_status: str | None = "READY") -> WorkLineRuntimeStatusSnapshot:
    return WorkLineRuntimeStatusSnapshot(
        runtime_status=runtime_status,
        source="test/runtime-projection",
        stopped_at=None,
        stopped_reason=None,
        resumed_at=None,
        active_safety_incident_id=None,
    )


@pytest.fixture(autouse=True)
def mock_runtime_status_snapshot():
    with patch(
        "src.app.callback.services.callback_ingress_service."
        "workline_runtime_status_projection_service.runtime_status_snapshot",
        new=AsyncMock(return_value=_runtime_snapshot()),
    ):
        yield


@pytest.fixture
def db_session() -> AsyncSession:
    mock = AsyncMock(spec=AsyncSession)
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    return cast("AsyncSession", mock)


@pytest.fixture
def build_request() -> RequestFactory:
    def _build_request(*, body: JsonDict, path: str) -> Request:
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "192.168.1.100"
        request.url = MagicMock()
        request.url.path = path
        request.headers = {"User-Agent": "TestClient"}
        request.method = "POST"
        request.json = AsyncMock(return_value=body)
        return cast("Request", request)

    return _build_request


def create_result_payload() -> JsonDict:
    return {
        "command_code": "CMD-20250317-001",
        "device_code": "ARM_01",
        "result": "SUCCESS",
        "finish_time": 1702627250000,
        "source_event_id": "result-event-001",
        "data": {"task_type": "PICK_AND_PUT"},
    }


def create_event_payload() -> JsonDict:
    return {
        "device_code": "ARM_01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": 1702627300000,
        "data": {
            "location": "STATION_INPUT1",
            "barcode1": "PKG1_12345678",
        },
    }


def create_external_payload() -> JsonDict:
    return {
        "callback_type": "AGV_TASK_RESULT",
        "trace_id": "trace-agv-001",
        "command_code": "AGV-REQ-001",
        "result": "SUCCESS",
        "data": {"to_location": "STATION_OUTPUT1"},
    }


class TestCallbackResultIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_result_skips_business_side_effects(
        self, db_session: AsyncSession, build_request: RequestFactory
    ) -> None:
        runtime_write = AsyncMock(
            side_effect=[_runtime_accept_result(created=True), _runtime_accept_result(created=False)]
        )
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
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(
                                id=7,
                                device_code="ARM_01",
                                capabilities_json={"supports_result_callback": True},
                            ),
                            workline=SimpleNamespace(is_active=True),
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
                    )
                ),
            ),
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
            patch(
                "src.app.callback.services.callback_orchestration_service."
                "callback_orchestration_service._runtime_inbox_writer.write_result_callback",
                new=runtime_write,
            ),
            patch("src.app.callback.v1.callback._enqueue_runtime_inbox_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", side_effect=["req-001", "req-002"]),
        ):
            from src.app.callback.v1.callback import callback_result

            response1 = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

            response2 = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

        assert response1["code"] == "1000"
        assert response2["code"] == "1000"
        assert _response_data(response2)["ack"] is True
        assert mock_handle.await_count == 1
        assert mock_enqueue.call_count == 1
        assert mock_log_callback.await_count == 2
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["error_message"] == "幂等重复: 已存在相同事件"
        assert log_kwargs["ingress_outcome"] == "DUPLICATE"
        assert log_kwargs["failure_stage"] is None
        assert mock_audit.await_count == 1
        assert runtime_write.await_count == 2
        for call in runtime_write.await_args_list:
            assert {key: call.kwargs["payload"][key] for key in create_result_payload()} == create_result_payload()
            assert call.kwargs["canonical_result_type"] == "DEVICE_RESULT"


class TestCallbackEventIdempotency:
    @pytest.mark.asyncio
    async def test_runtime_duplicate_event_skips_transition_inbox_and_audit(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        runtime_write = AsyncMock(
            side_effect=[_runtime_accept_result(created=True), _runtime_accept_result(created=False)]
        )
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.device_context_service.resolve",
                new=AsyncMock(
                    return_value=(
                        SimpleNamespace(
                            device=SimpleNamespace(
                                id=7,
                                device_code="ARM_01",
                                capabilities_json={"supports_event_types": ["SCAN_COMPLETED"]},
                            ),
                            workline=SimpleNamespace(is_active=True),
                            work_line_id=1,
                            is_workline_bound=True,
                        ),
                        None,
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
                "src.app.callback.services.callback_orchestration_service."
                "callback_orchestration_service._runtime_inbox_writer.write_event_callback",
                new=runtime_write,
            ),
            patch("src.app.callback.v1.callback._enqueue_runtime_inbox_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", side_effect=["req-101", "req-102"]),
        ):
            from src.app.callback.v1.callback import callback_event

            response1 = await callback_event(
                request=build_request(body=create_event_payload(), path="/api/v1/callback/event"),
                db=db_session,
                response=Response(),
            )

            response2 = await callback_event(
                request=build_request(body=create_event_payload(), path="/api/v1/callback/event"),
                db=db_session,
                response=Response(),
            )

        assert response1["code"] == "1000"
        assert response2["code"] == "1000"
        assert _response_data(response2)["status"] == "duplicate"
        assert mock_enqueue.call_count == 1
        assert mock_log_callback.await_count == 2
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["error_message"] == "幂等重复: 已存在相同事件"
        assert log_kwargs["ingress_outcome"] == "DUPLICATE"
        assert log_kwargs["failure_stage"] is None
        assert mock_audit.await_count == 1
        assert runtime_write.await_count == 2
        for call in runtime_write.await_args_list:
            assert {key: call.kwargs["payload"][key] for key in create_event_payload()} == create_event_payload()
            assert call.kwargs["canonical_event_type"] == "SCAN_COMPLETED"


class TestCallbackExternalIdempotency:
    @pytest.mark.asyncio
    async def test_runtime_duplicate_external_callback_skips_transition_inbox_and_audit(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        runtime_write = AsyncMock(
            side_effect=[_runtime_accept_result(created=True), _runtime_accept_result(created=False)]
        )
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
                "src.app.callback.services.callback_orchestration_service."
                "callback_orchestration_service._runtime_inbox_writer.write_external_callback",
                new=runtime_write,
            ),
            patch("src.app.callback.v1.callback._enqueue_runtime_inbox_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", side_effect=["req-ext-101", "req-ext-102"]),
        ):
            from src.app.callback.v1.callback import callback_external

            response1 = await callback_external(
                request=build_request(body=create_external_payload(), path="/api/v1/callback/external"),
                db=db_session,
            )

            response2 = await callback_external(
                request=build_request(body=create_external_payload(), path="/api/v1/callback/external"),
                db=db_session,
            )

        assert response1["code"] == "1000"
        assert response2["code"] == "1000"
        assert _response_data(response2)["status"] == "duplicate"
        assert mock_enqueue.call_count == 1
        assert mock_log_callback.await_count == 2
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["error_message"] == "幂等重复: 已存在相同事件"
        assert log_kwargs["ingress_outcome"] == "DUPLICATE"
        assert log_kwargs["failure_stage"] is None
        assert mock_audit.await_count == 1
        assert runtime_write.await_count == 2
        for call in runtime_write.await_args_list:
            assert call.kwargs["payload"] == create_external_payload()
            assert call.kwargs["trace_id"] == "trace-agv-001"
