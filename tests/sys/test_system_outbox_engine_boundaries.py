"""SystemOutboxEngine 的调度、transport 与辅助函数边界。"""

from __future__ import annotations

import json
import re
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import src.app.sys.services.outbox_engine as outbox_engine_module
from src.app.sys.dispatch_concurrency import DispatchClaimBatch, DispatchClaimMetrics
from src.app.sys.external_http_transport import (
    MAX_EXTERNAL_HTTP_RESPONSE_BODY_BYTES,
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportResult,
)
from src.app.sys.models import SystemOutboxDispatchType, SystemOutboxStatus
from src.app.sys.models.outbox import WMS_ASYNC_EFFECT_OPERATION_IDENTITIES
from src.app.sys.services.outbox_engine import (
    SystemOutboxEngine,
    _commit_if_supported,
    _dispatch_device_command,
    _dispatch_workline_domain,
    _extract_protocol_error_code,
    _send_external_http,
)
from tests.support.external_http import StaticTestCredentialProvider, signed_external_http_request
from tests.sys.test_system_outbox_engine import (
    FakeDispatchAttemptService,
    FakeFairDispatchScheduler,
    FakeSystemOutboxRepository,
    _outbox,
)


def _empty_batch(*claims) -> DispatchClaimBatch:
    return DispatchClaimBatch(
        claims=claims,
        metrics=DispatchClaimMetrics(0, len(claims), 0, None, (), (), ()),
    )


@pytest.mark.asyncio
async def test_dispatch_zero_limit_and_workline_saturation_return_without_claiming() -> None:
    scheduler = SimpleNamespace(claim=AsyncMock())
    engine = SystemOutboxEngine(
        outbox_repository=SimpleNamespace(),
        dispatch_scheduler=scheduler,
        workline_domain_dispatcher=AsyncMock(return_value={"dispatched": 1, "success": 1, "failed": 0, "skipped": 0}),
    )
    db = SimpleNamespace(commit=AsyncMock())

    assert await engine.dispatch(db, limit=0) == {
        "dispatched": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
    }
    assert await engine.dispatch(db, limit=1) == {
        "dispatched": 1,
        "success": 1,
        "failed": 0,
        "skipped": 0,
    }
    scheduler.claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_skips_claim_without_persisted_outbox_id() -> None:
    claim = SimpleNamespace(
        outbox=SimpleNamespace(id=None),
        lease_owner_token="lease-1",
        policy=SimpleNamespace(lease_seconds=30, retry_budget=3),
    )
    scheduler = SimpleNamespace(claim=AsyncMock(return_value=_empty_batch(claim)))
    attempt_service = FakeDispatchAttemptService()
    engine = SystemOutboxEngine(
        outbox_repository=SimpleNamespace(),
        dispatch_scheduler=scheduler,
        dispatch_attempt_service=attempt_service,
        workline_domain_dispatcher=AsyncMock(return_value={"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}),
    )

    result = await engine.dispatch(SimpleNamespace(commit=AsyncMock()), limit=2)

    assert result == {"dispatched": 0, "success": 0, "failed": 0, "skipped": 1}
    assert attempt_service.created == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dispatch_result", "repository_update"), ((True, "value"), (True, "none"), (False, "value"), (False, "none"))
)
async def test_non_http_dispatch_result_covers_success_failure_and_lost_lease(
    dispatch_result,
    repository_update,
) -> None:
    message = _outbox(dispatch_type=SystemOutboxDispatchType.INTERNAL_SIGNAL)
    repository = FakeSystemOutboxRepository([message])
    if dispatch_result:
        repository.mark_as_sent = AsyncMock(  # type: ignore[method-assign]
            return_value=message if repository_update == "value" else None
        )
    else:
        repository.mark_as_failed = AsyncMock(  # type: ignore[method-assign]
            return_value=message if repository_update == "value" else None
        )
    engine = SystemOutboxEngine(
        outbox_repository=repository,
        dispatch_scheduler=FakeFairDispatchScheduler(repository),
        dispatch_attempt_service=FakeDispatchAttemptService(),
        workline_domain_dispatcher=AsyncMock(return_value={"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}),
    )
    engine.dispatch_single = AsyncMock(return_value=dispatch_result)  # type: ignore[method-assign]

    result = await engine.dispatch(SimpleNamespace(commit=AsyncMock()), limit=1)

    assert result["dispatched"] == 1
    expected_key = "success" if dispatch_result and repository_update == "value" else "failed"
    if repository_update == "none":
        expected_key = "skipped"
    assert result[expected_key] == 1


@pytest.mark.asyncio
async def test_dispatch_uses_remaining_capacity_for_second_workline_pass() -> None:
    workline = AsyncMock(
        side_effect=(
            {"dispatched": 2, "success": 2, "failed": 0, "skipped": 0},
            {"dispatched": 2, "success": 2, "failed": 0, "skipped": 0},
        )
    )
    engine = SystemOutboxEngine(
        outbox_repository=SimpleNamespace(),
        dispatch_scheduler=SimpleNamespace(claim=AsyncMock(return_value=_empty_batch())),
        dispatch_attempt_service=FakeDispatchAttemptService(),
        workline_domain_dispatcher=workline,
    )

    result = await engine.dispatch(SimpleNamespace(commit=AsyncMock()), limit=4)

    assert result == {"dispatched": 4, "success": 4, "failed": 0, "skipped": 0}
    assert workline.await_count == 2


@pytest.mark.asyncio
async def test_lost_external_http_finalization_without_unknown_fence_skips_late_evidence() -> None:
    message = _outbox()
    repository = FakeSystemOutboxRepository([message])
    repository.mark_as_sent = AsyncMock(return_value=None)  # type: ignore[method-assign]
    bridge = SimpleNamespace(record_result=AsyncMock())
    engine = SystemOutboxEngine(
        outbox_repository=repository,
        dispatch_scheduler=FakeFairDispatchScheduler(repository),
        external_http_sender=AsyncMock(
            return_value=ExternalHttpTransportResult.accepted(
                http_status_code=202,
                protocol_result=ExternalHttpProtocolResult.ACCEPTED,
            )
        ),
        credential_provider=StaticTestCredentialProvider(),
        dispatch_attempt_service=FakeDispatchAttemptService(),
        effect_transport_bridge=bridge,
        workline_domain_dispatcher=AsyncMock(return_value={"dispatched": 0, "success": 0, "failed": 0, "skipped": 0}),
    )

    result = await engine.dispatch(SimpleNamespace(commit=AsyncMock()), limit=1)

    assert result == {"dispatched": 1, "success": 0, "failed": 0, "skipped": 1}
    bridge.record_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_engine_lazy_resolvers_and_none_record_target_are_explicit() -> None:
    injected_resolver = object()
    engine = SystemOutboxEngine(
        outbox_repository=SimpleNamespace(),
        dispatch_scheduler=SimpleNamespace(),
        effect_transport_resolver=injected_resolver,  # type: ignore[arg-type]
    )
    assert engine._resolve_effect_transport_resolver() is injected_resolver

    default_engine = SystemOutboxEngine(
        outbox_repository=SimpleNamespace(),
        dispatch_scheduler=SimpleNamespace(),
    )
    assert callable(default_engine._resolve_external_http_recovery_context_factory())
    assert callable(default_engine._resolve_effect_transport_resolver())
    assert (
        await default_engine._record_effect_transport_result(
            object(),
            outbox=SimpleNamespace(),
            dispatch_attempt=SimpleNamespace(),
            result=ExternalHttpTransportResult.sandbox_accepted(),
            updated=None,
        )
        is None
    )


@pytest.mark.asyncio
async def test_dispatch_single_handles_internal_signal_and_unknown_type(monkeypatch: pytest.MonkeyPatch) -> None:
    internal_sender = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "src.app.sys.services.outbox_delivery.dispatch_internal_signal",
        internal_sender,
    )
    engine = SystemOutboxEngine(
        outbox_repository=SimpleNamespace(),
        dispatch_scheduler=SimpleNamespace(),
    )

    assert (
        await engine.dispatch_single(
            object(),
            SimpleNamespace(dispatch_type=SystemOutboxDispatchType.INTERNAL_SIGNAL),
        )
        is True
    )
    assert await engine.dispatch_single(object(), SimpleNamespace(dispatch_type="UNKNOWN")) is False
    internal_sender.assert_awaited_once()


def test_async_wms_status_enqueue_filters_rejects_and_contains_broker_failure() -> None:
    operation_identity = next(iter(WMS_ASYNC_EFFECT_OPERATION_IDENTITIES))
    gateway = SimpleNamespace(enqueue_wms_effect_status=Mock(side_effect=RuntimeError("broker down")))
    engine = SystemOutboxEngine(
        outbox_repository=SimpleNamespace(),
        dispatch_scheduler=SimpleNamespace(),
        task_queue_gateway=gateway,
    )
    outbox = SimpleNamespace(operation_identity=operation_identity, dispatch_key="dispatch-1")

    engine._enqueue_wms_effect_status_if_needed(
        outbox=outbox,
        result=ExternalHttpTransportResult.not_sent(
            phase=outbox_engine_module.ExternalHttpTransportPhase.CONNECTING,
            safe_to_retry=True,
            error_code="CONNECT_ERROR",
        ),
    )
    engine._enqueue_wms_effect_status_if_needed(
        outbox=outbox,
        result=ExternalHttpTransportResult.ambiguous(
            phase=outbox_engine_module.ExternalHttpTransportPhase.AWAITING_RESPONSE,
            error_code="READ_TIMEOUT",
        ),
    )

    gateway.enqueue_wms_effect_status.assert_called_once_with(dispatch_key="dispatch-1")


class _Client:
    def __init__(self, result) -> None:
        self.result = result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, *_args, **_kwargs):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_result", "expected_outcome", "expected_error"),
    (
        (httpx.InvalidURL("invalid"), ExternalHttpTransportOutcome.NOT_SENT, "INVALIDURL"),
        (
            httpx.PoolTimeout("pool timeout"),
            ExternalHttpTransportOutcome.AMBIGUOUS,
            "POOLTIMEOUT",
        ),
        (
            RuntimeError("unexpected"),
            ExternalHttpTransportOutcome.AMBIGUOUS,
            "UNCLASSIFIED_TRANSPORT_ERROR",
        ),
    ),
)
async def test_sender_classifies_configuration_generic_timeout_and_unknown_error(
    monkeypatch,
    client_result,
    expected_outcome,
    expected_error,
) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: _Client(client_result))

    result = await _send_external_http(signed_external_http_request({"request_id": "REQ-1"}))

    assert result.outcome is expected_outcome
    assert result.error_code == expected_error


@pytest.mark.asyncio
async def test_sender_rejects_oversized_response_before_json_interpretation(monkeypatch) -> None:
    response = SimpleNamespace(
        status_code=200,
        content=b"x" * (MAX_EXTERNAL_HTTP_RESPONSE_BODY_BYTES + 1),
    )
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: _Client(response))

    result = await _send_external_http(signed_external_http_request({"request_id": "REQ-1"}))

    assert result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS
    assert result.error_code == "WMS_WIRE_BUDGET_EXCEEDED"


def test_protocol_code_extractor_rejects_non_object_json() -> None:
    assert (
        _extract_protocol_error_code(
            b"[]",
            json_module=json,
            stable_code_pattern=re.compile(r"^[A-Z][A-Z0-9_]{0,119}$"),
        )
        is None
    )


@pytest.mark.asyncio
async def test_default_domain_gateways_delegate_to_their_owned_services(monkeypatch) -> None:
    workline_dispatch = AsyncMock(return_value={"dispatched": 1, "success": 1, "failed": 0, "skipped": 0})
    device_dispatch = AsyncMock(return_value=True)
    outbox_dispatch_module = import_module("src.app.runtime.orchestration.services.inbox.outbox_dispatch_service")
    device_gateway_module = import_module("src.app.runtime.orchestration.services.device_command_gateway")
    monkeypatch.setattr(
        outbox_dispatch_module,
        "outbox_dispatch_service",
        SimpleNamespace(dispatch=workline_dispatch),
    )
    monkeypatch.setattr(
        device_gateway_module,
        "device_command_gateway",
        SimpleNamespace(dispatch=device_dispatch),
    )

    assert (await _dispatch_workline_domain("db", 3))["success"] == 1
    assert await _dispatch_device_command("db", "outbox") is True
    workline_dispatch.assert_awaited_once_with("db", limit=3)
    device_dispatch.assert_awaited_once_with("db", "outbox")


@pytest.mark.asyncio
async def test_commit_helper_supports_absent_sync_and_async_hooks() -> None:
    calls: list[str] = []

    def sync_commit():
        calls.append("sync")

    async def async_commit():
        calls.append("async")

    await _commit_if_supported(SimpleNamespace())
    await _commit_if_supported(SimpleNamespace(commit=sync_commit))
    await _commit_if_supported(SimpleNamespace(commit=async_commit))

    assert calls == ["sync", "async"]
