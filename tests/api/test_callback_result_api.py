"""Callback result API 测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.response.response_code import ResourceErrorCode
from tests.api import callback_test_support
from tests.api.callback_test_support import (
    RequestFactory,
    _await_kwargs,
    _response_data,
    create_result_payload,
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
                "src.app.callback.services.callback_orchestration_service.callback_orchestration_service._runtime_inbox_writer.write_result_callback",
                new=AsyncMock(return_value=SimpleNamespace(created=True, record=SimpleNamespace(id=801))),
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
            patch("src.app.callback.v1.callback._enqueue_runtime_inbox_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-001"),
            patch("src.app.runtime.orchestration.observability.runtime_observability_registry.emit") as emit,
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
        mock_create_inbox.assert_not_awaited()
        mock_mark_finished.assert_awaited_once_with(
            db_session,
            device_id=7,
            command_id=1001,
            success=True,
            error_code=None,
            auto_commit=False,
        )
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["trace_id"] == "trace-001"
        assert log_kwargs["ingress_outcome"] == "ACCEPTED"
        assert log_kwargs["failure_stage"] is None
        emit.assert_called_once_with(
            "callback.normalize",
            {
                "trace_id": "trace-001",
                "correlation_id": "CMD-20250317-001",
                "provider_code": "ECS",
                "source_event_id": "req-001",
            },
        )
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
                "src.app.callback.services.callback_orchestration_service.callback_orchestration_service._runtime_inbox_writer.write_result_callback",
                new=AsyncMock(return_value=SimpleNamespace(created=True, record=SimpleNamespace(id=802))),
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
            patch("src.app.callback.v1.callback._enqueue_runtime_inbox_processing"),
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
    async def test_callback_result_rejects_undeclared_provider_profile_normalizer(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        admission = MagicMock(side_effect=PermissionError("provider=ECS 未声明 result normalizer: DEVICE_RESULT"))
        with (
            patch(
                "src.app.callback.services.callback_ingress_service.callback_provider_profile_admission_service",
                new=SimpleNamespace(admit=admission),
                create=True,
            ),
            patch(
                "src.app.callback.services.callback_ingress_service.device_command_service.get_command_by_code",
                new=AsyncMock(),
            ) as mock_get_command,
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-result-profile-admission"),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

        assert response["code"] == "2004"
        assert _response_data(response)["ack"] is False
        admission.assert_called_once_with(
            provider_code="ECS",
            callback_type="DEVICE_RESULT",
            direction="result",
        )
        mock_get_command.assert_not_awaited()
        mock_create_inbox.assert_not_awaited()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["failure_stage"] == "CONTRACT_VALIDATE"
        assert "未声明 result normalizer" in str(log_kwargs["error_message"])
        mock_audit.assert_awaited_once()

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

    @pytest.mark.asyncio
    async def test_callback_result_duplicate_ack_comes_from_runtime_inbox(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        existing_command = SimpleNamespace(
            id=1001,
            trace_id="trace-runtime-dup-result",
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
                "src.app.callback.services.callback_orchestration_service.callback_orchestration_service._runtime_inbox_writer.write_result_callback",
                new=AsyncMock(return_value=SimpleNamespace(created=False, record=SimpleNamespace(id=901))),
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
            patch("src.app.callback.v1.callback._enqueue_runtime_inbox_processing") as mock_enqueue,
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-runtime-dup-result"),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

        assert response["code"] == "1000"
        assert _response_data(response)["ack"] is True
        mock_create_inbox.assert_not_awaited()
        mock_handle.assert_not_awaited()
        mock_enqueue.assert_not_called()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "DUPLICATE"
        mock_audit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_callback_result_conflict_maps_runtime_inbox_conflict(
        self,
        db_session: AsyncSession,
        build_request: RequestFactory,
    ) -> None:
        from src.app.runtime.orchestration.consumers.runtime_inbox_service import RuntimeInboxConflict

        existing_command = SimpleNamespace(
            id=1001,
            trace_id="trace-runtime-conflict-result",
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
                "src.app.callback.services.callback_orchestration_service.callback_orchestration_service._runtime_inbox_writer.write_result_callback",
                new=AsyncMock(
                    side_effect=RuntimeInboxConflict(
                        provider_code="ECS",
                        event_type="result",
                        source_event_id="evt-result-conflict",
                        existing_payload_hash="hash-old",
                        incoming_payload_hash="hash-new",
                    )
                ),
            ),
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
            patch("src.app.callback.v1.callback.get_request_id", return_value="req-runtime-conflict-result"),
        ):
            from src.app.callback.v1.callback import callback_result

            response = await callback_result(
                request=build_request(body=create_result_payload(), path="/api/v1/callback/result"),
                db=db_session,
            )

        assert response["code"] == ResourceErrorCode.CONFLICT.code
        assert _response_data(response)["ack"] is False
        mock_create_inbox.assert_not_awaited()
        log_kwargs = _await_kwargs(mock_log_callback)
        assert log_kwargs["ingress_outcome"] == "REJECTED"
        assert log_kwargs["failure_stage"] == "ORCHESTRATION"
        mock_audit.assert_awaited_once()
