"""Callback external API 测试。"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.api import callback_test_support
from tests.api.callback_test_support import (
    RequestFactory,
    _await_kwargs,
    _response_data,
    create_external_payload,
    create_full_box_exchange_external_payload,
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


class TestCallbackExternalAPI:
    def test_external_callback_allow_list_includes_phase3_ecs_device_matrix(self) -> None:
        expected_types = {
            "DEVICE_RESULT",
            "DEVICE_EVENT",
            "DEVICE_STATUS_CHANGED",
            "MATERIAL_ARRIVED",
            "SCAN_COMPLETED",
            "ESTOP_PRESSED",
            "DEVICE_ERROR",
            "DEVICE_ONLINE",
            "DEVICE_OFFLINE",
        }

        assert expected_types <= callback_test_support.callback_ingress_module._EXTERNAL_CALLBACK_ALLOWED_TYPES

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
            patch("src.app.runtime.orchestration.observability.runtime_observability_registry.emit") as emit,
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
        emit.assert_called_once_with(
            "callback.normalize",
            {
                "trace_id": "trace-agv-001",
                "correlation_id": "AGV-REQ-001",
                "provider_code": "AGV",
                "source_event_id": "req-ext-001",
            },
        )
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
    async def test_callback_external_rejects_callback_type_outside_allow_list(
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-ext-unsupported-type"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_external_payload(callback_type="ERP_STOCK_SYNCED"),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert _response_data(response)["ack"] is False
        mock_create_inbox.assert_not_awaited()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        assert "callback_type is not allow-listed" in str(log_kwargs["error_message"])
        mock_audit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_callback_external_rejects_ecs_device_source_mismatch(
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-ext-source-mismatch"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_external_payload(
                        callback_type="DEVICE_EVENT",
                        source_system="WMS",
                    ),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert _response_data(response)["ack"] is False
        mock_create_inbox.assert_not_awaited()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["failure_stage"] == "ENVELOPE_VALIDATE"
        assert "source_system must match callback_type provider" in str(log_kwargs["error_message"])
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
                        source_system="RCS",
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
