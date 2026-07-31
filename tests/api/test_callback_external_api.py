"""Callback external API 的冻结允许集与 fail-closed 合同。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.responses import JSONResponse

from tests.api import callback_test_support
from tests.api.callback_test_support import (
    RequestFactory,
    _await_kwargs,
    _response_data,
    create_external_payload,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


REMOVED_WMS_RCS_CALLBACK_TYPES = (
    "RCS_GRN_RECEIVED",
    "RCS_PALLET_ARRIVED",
    "RCS_INVENTORY_UPDATED",
    "RCS_PDA_OPERATION_RECORDED",
    "WMS_EXCHANGE_COMPLETED",
    "RCS_EXCHANGE_COMPLETED",
    "WMS_TASK_CHANGE",
    "RCS_TASK_CHANGE",
    "WMS_REJECTED",
    "RCS_REJECTED",
    "WMS_FAILED",
    "RCS_FAILED",
    "WMS_RACK_TASK_RESULT",
    "RCS_RACK_TASK_RESULT",
    "WMS_RACK_TASK_PROGRESS",
    "RCS_RACK_TASK_PROGRESS",
    "WMS_RACK_ARRIVED",
    "RCS_RACK_ARRIVED",
    "WMS_RACK_EXCHANGE_PROGRESS",
    "RCS_RACK_EXCHANGE_PROGRESS",
    "WMS_RACK_EXCHANGE_FAILED",
    "RCS_RACK_EXCHANGE_FAILED",
    "WMS_RACK_OPERATION_FAILED",
    "RCS_RACK_OPERATION_FAILED",
    "WMS_BIN_MOVE_PROGRESS",
    "RCS_BIN_MOVE_PROGRESS",
    "WMS_BIN_MOVE_COMPLETED",
    "RCS_BIN_MOVE_COMPLETED",
    "WMS_BIN_MOVE_FAILED",
    "RCS_BIN_MOVE_FAILED",
    "WMS_FULL_BOX_EXCHANGE_RESULT",
    "RCS_FULL_BOX_EXCHANGE_RESULT",
    "WMS_EMPTY_BOX_TRANSFER_RESULT",
    "RCS_EMPTY_BOX_TRANSFER_RESULT",
    "WMS_FULL_BOX_TRANSFER_RESULT",
    "RCS_FULL_BOX_TRANSFER_RESULT",
    "WMS_HANDLING_TASK_RESULT",
    "RCS_HANDLING_TASK_RESULT",
    "WMS_TRANSPORT_COMPLETED",
    "RCS_TRANSPORT_COMPLETED",
    "WMS_ROUGH_SORTER_INBOUND",
)

WMS_ORDINARY_EVENT_TYPES = (
    "WMS_GRN_RECEIVED",
    "WMS_PALLET_ARRIVED",
    "WMS_INVENTORY_UPDATED",
    "WMS_PDA_OPERATION_RECORDED",
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


def _response_body(response: dict | JSONResponse) -> dict:
    if isinstance(response, JSONResponse):
        return json.loads(response.body)
    return response


class TestCallbackExternalAPI:
    @pytest.mark.asyncio
    async def test_callback_diagnostic_redacts_signature_evidence_without_mutating_payload(
        self,
        db_session: AsyncSession,
    ) -> None:
        payload = {
            "signature": "top-secret",
            "data": {"Signature": "nested-secret", "items": [{"SIGNATURE": "list-secret"}]},
        }
        with patch.object(
            callback_test_support.callback_ingress_module.workline_diagnostic_service,
            "record_event",
            new=AsyncMock(),
        ) as record_event:
            await callback_test_support.callback_ingress_module._record_callback_diagnostic(
                db_session,
                error_code=callback_test_support.callback_ingress_module.ErrorCode.CALLBACK_SCHEMA_INVALID,
                message="callback schema invalid",
                request_id="request-diagnostic-1",
                callback_type="external",
                payload=payload,
            )

        assert record_event.await_args.kwargs["evidence"]["payload"]["signature"] == "***REDACTED***"
        assert record_event.await_args.kwargs["evidence"]["payload"]["data"]["Signature"] == "***REDACTED***"
        assert payload["signature"] == "top-secret"

    def test_external_callback_allow_list_keeps_non_wms_provider_matrix(self) -> None:
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
            "AGV_TASK_RESULT",
        }

        assert expected_types <= callback_test_support.callback_ingress_module._EXTERNAL_CALLBACK_ALLOWED_TYPES

    @pytest.mark.asyncio
    async def test_callback_external_keeps_agv_callback_path(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as callback_log,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
            patch("src.app.callback.v1.callback._enqueue_runtime_inbox_processing") as enqueue,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-ext-001"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(body=create_external_payload(), path="/api/v1/callback/external"),
                db=db_session,
            )

        body = _response_body(response)
        assert body["code"] == "1000"
        assert _response_data(body)["status"] == "submitted"
        assert _await_kwargs(callback_log)["ingress_outcome"] == "ACCEPTED"
        enqueue.assert_called_once()

    @pytest.mark.parametrize("callback_type", WMS_ORDINARY_EVENT_TYPES)
    @pytest.mark.asyncio
    async def test_callback_external_rejects_ordinary_wms_events(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
        callback_type: str,
    ) -> None:
        payload = {
            "callback_type": callback_type,
            "source_system": "WMS",
            "source_event_id": f"event-{callback_type.lower()}",
            "source_version": "1",
            "occurred_at": "2026-07-29T08:00:00Z",
            "request_id": f"request-{callback_type.lower()}",
            "trace_id": f"trace-{callback_type.lower()}",
            "data": {},
        }
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
            patch("src.app.callback.v1.callback._enqueue_runtime_inbox_processing"),
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-wms-event"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(body=payload, path="/api/v1/callback/external"),
                db=db_session,
            )

        body = _response_body(response)
        assert isinstance(response, JSONResponse)
        assert response.status_code == 400
        assert body["code"] == "2004"

    @pytest.mark.parametrize("callback_type", REMOVED_WMS_RCS_CALLBACK_TYPES)
    @pytest.mark.asyncio
    async def test_callback_external_rejects_removed_wms_rcs_family_before_runtime_inbox(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
        callback_type: str,
    ) -> None:
        source_system = "RCS" if callback_type.startswith("RCS_") else "WMS"
        payload = {
            "callback_type": callback_type,
            "source_system": source_system,
            "source_event_id": f"event-{callback_type.lower()}",
            "source_version": "1",
            "occurred_at": "2026-07-29T08:00:00Z",
            "request_id": f"request-{callback_type.lower()}",
            "trace_id": f"trace-{callback_type.lower()}",
            "data": {},
        }
        writer = callback_test_support.callback_ingress_module.callback_orchestration_service._runtime_inbox_writer
        with (
            patch.object(writer, "write_external_callback", new=AsyncMock()) as write_external_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-removed-callback"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(body=payload, path="/api/v1/callback/external"),
                db=db_session,
            )

        assert isinstance(response, JSONResponse)
        assert response.status_code == 400
        assert _response_body(response)["code"] == "2004"
        write_external_callback.assert_not_awaited()

    @pytest.mark.parametrize(
        "operation_identity",
        (
            "wms.inventory.confirm_inbound@v1",
            "wms.fulfillment.unknown_operation@v1",
        ),
    )
    @pytest.mark.asyncio
    async def test_callback_external_rejects_non_async_effect_hint_without_persistence(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
        operation_identity: str,
    ) -> None:
        payload = {
            "callback_type": "WMS_EFFECT_STATUS_HINT",
            "source_system": "WMS",
            "source_event_id": "wms-invalid-effect-001",
            "occurred_at": "2026-07-29T08:00:00Z",
            "trace_id": "trace-invalid-effect-001",
            "data": {
                "operation_identity": operation_identity,
                "idempotency_key": "idem-invalid-effect-001",
                "dispatch_key": "invalid-effect-001",
            },
        }
        writer = callback_test_support.callback_ingress_module.callback_orchestration_service._runtime_inbox_writer
        with (
            patch.object(
                writer,
                "write_external_callback",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        created=True,
                        record=SimpleNamespace(
                            id=46,
                            trace_id="trace-invalid-effect-001",
                            source_event_id="wms-invalid-effect-001",
                        ),
                    )
                ),
            ) as write_external_callback,
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ) as callback_log,
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ) as audit_log,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-invalid-effect"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(body=payload, path="/api/v1/callback/external"),
                db=db_session,
            )

        assert isinstance(response, JSONResponse)
        assert response.status_code == 400
        assert _response_body(response)["code"] == "2004"
        write_external_callback.assert_not_awaited()
        callback_log.assert_not_awaited()
        audit_log.assert_not_awaited()
        db_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_callback_external_rejects_missing_trace_id_with_http_400(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.callback_log_service.log_callback",
                new=AsyncMock(),
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.audit_log_service.create_audit_log",
                new=AsyncMock(),
            ),
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-ext-missing-trace"),
        ):
            from src.app.callback.v1.callback import callback_external

            response = await callback_external(
                request=build_request(
                    body=create_external_payload(trace_id=""),
                    path="/api/v1/callback/external",
                ),
                db=db_session,
            )

        assert isinstance(response, JSONResponse)
        assert response.status_code == 400
        assert _response_data(_response_body(response))["ack"] is False
