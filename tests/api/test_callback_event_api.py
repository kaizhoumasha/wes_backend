"""Callback event API 测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.response.response_code import ResourceErrorCode
from tests.api import callback_test_support
from tests.api.callback_test_support import (
    RequestFactory,
    _await_kwargs,
    _response_data,
    _response_model_data,
    callback_ingress_module,
    create_event_payload,
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

    @pytest.mark.parametrize("reserved_target", ["WORKLINE_START_REQUESTED", "ESTOP_PRESSED"])
    @pytest.mark.asyncio
    async def test_callback_event_rejects_mapping_to_reserved_target(
        self,
        reserved_target: str,
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
                                runtime_config_json={"event_type_mapping": {"SCAN_FINISH": reserved_target}},
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
            patch(
                "src.app.callback.v1.callback.get_request_id", return_value=f"req-{reserved_target.lower()}-mapping-001"
            ),
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
                                runtime_config_json={"event_type_mapping": {"ESTOP_PRESSED": "SCAN_COMPLETED"}},
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
                                runtime_config_json={
                                    "event_type_mapping": {"WORKLINE_START_REQUESTED": "SCAN_COMPLETED"}
                                },
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
