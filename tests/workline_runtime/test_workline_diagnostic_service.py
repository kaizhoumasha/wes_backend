from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from src.workline_runtime.diagnostics import ErrorCode, build_diagnostic_context, build_diagnostic_event


class _DiagnosticRepoStub:
    def __init__(self) -> None:
        self.created: dict[str, Any] | None = None
        self.existing: Any | None = None
        self.get_by_diagnostic_key = AsyncMock(side_effect=self._get_by_key)
        self.create = AsyncMock(side_effect=self._create)

    async def _get_by_key(self, _db: object, _key: str) -> Any | None:
        return self.existing

    async def _create(self, _db: object, data: dict[str, Any]) -> Any:
        self.created = data
        self.existing = SimpleNamespace(id=41, **data)
        return self.existing


@pytest.mark.asyncio
async def test_diagnostic_service_upserts_card_with_registry_and_redacted_evidence() -> None:
    from src.app.workline.services.diagnostic_service import WorklineDiagnosticService

    repo = _DiagnosticRepoStub()
    service = WorklineDiagnosticService(repository=cast("Any", repo))
    context = build_diagnostic_context(
        request_id="req-001",
        trace_id="trace-001",
        command=SimpleNamespace(command_code="CMD-001"),
        extra={"authorization": "Bearer secret", "barcode": "PKG-001"},
    )
    event = build_diagnostic_event(
        error_code=ErrorCode.OUTBOX_DISPATCH_FAILED,
        context=context,
        message="设备派发失败",
        operator_action="检查设备网络后重试",
    )

    diagnostic = await service.record_event(
        object(),
        event=event,
        evidence={"headers": {"Authorization": "Bearer secret"}, "body": {"password": "secret", "ok": True}},
        auto_commit=False,
    )

    assert diagnostic.id == 41
    assert repo.created is not None
    assert repo.created["diagnostic_key"] == "OUTBOX_DISPATCH_FAILED:trace-001:CMD-001:req-001"
    assert repo.created["owner"] == "integration"
    assert repo.created["recoverability"] == "manual_intervention_required"
    evidence = cast("dict[str, Any]", repo.created["evidence_json"])
    assert evidence == {"headers": {"Authorization": "***"}, "body": {"password": "***", "ok": True}}
    card = cast("dict[str, Any]", repo.created["card_json"])
    assert card["error_code"] == "OUTBOX_DISPATCH_FAILED"
    assert card["context"]["trace_id"] == "trace-001"


@pytest.mark.asyncio
async def test_diagnostic_service_returns_existing_record_for_duplicate_key() -> None:
    from src.app.workline.services.diagnostic_service import WorklineDiagnosticService

    repo = _DiagnosticRepoStub()
    repo.existing = SimpleNamespace(id=99, diagnostic_key="existing")
    service = WorklineDiagnosticService(repository=cast("Any", repo))
    context = build_diagnostic_context(request_id="req-001", trace_id="trace-001")
    event = build_diagnostic_event(
        error_code=ErrorCode.UNKNOWN,
        context=context,
        message="重复诊断",
    )

    diagnostic = await service.record_event(object(), event=event, auto_commit=False)

    assert diagnostic.id == 99
    repo.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_record_diagnostic_persists_card_after_logging() -> None:
    from src.app.workline import diagnostic_support

    inbox = SimpleNamespace(
        id=11,
        trace_id="trace-runtime-001",
        payload_json={"event_type": "SCAN_COMPLETED", "device_code": "SCN-01"},
    )

    with (
        patch.object(
            diagnostic_support,
            "_log_diagnostic",
            wraps=diagnostic_support._log_diagnostic,
        ),
        patch(
            "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ) as mock_record,
    ):
        await diagnostic_support._record_diagnostic(
            object(),
            inbox=inbox,
            error_code=ErrorCode.CALLBACK_SCHEMA_INVALID,
            message="payload invalid",
        )

    mock_record.assert_awaited_once()
    await_args = mock_record.await_args
    assert await_args is not None
    event = await_args.kwargs["event"]
    assert event.context.trace_id == "trace-runtime-001"
    assert event.context.inbox_id == 11


@pytest.mark.asyncio
async def test_runtime_record_diagnostic_swallow_log_construction_failure() -> None:
    from src.app.workline import diagnostic_support

    with (
        patch.object(diagnostic_support, "_log_diagnostic", side_effect=RuntimeError("expired orm object")),
        patch(
            "src.app.workline.services.diagnostic_service.workline_diagnostic_service.record_event",
            new=AsyncMock(),
        ) as mock_record,
    ):
        await diagnostic_support._record_diagnostic(
            object(),
            error_code=ErrorCode.UNKNOWN,
            message="original failure",
        )

    mock_record.assert_not_awaited()
