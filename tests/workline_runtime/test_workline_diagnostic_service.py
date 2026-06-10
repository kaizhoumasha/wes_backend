from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from src.app.workline.models.diagnostic import WorklineDiagnostic
from src.workline_runtime.diagnostics import ErrorCode, build_diagnostic_context, build_diagnostic_event


class _DiagnosticRepoStub:
    def __init__(self) -> None:
        self.created: dict[str, Any] | None = None
        self.existing: Any | None = None
        self.get_by_diagnostic_key = AsyncMock(side_effect=self._get_by_key)
        self.create = AsyncMock(side_effect=self._create)
        self.create_idempotent_by_diagnostic_key = AsyncMock(side_effect=self._create)
        self.update_resource_wait_by_key = AsyncMock(side_effect=self._update_resource_wait)
        self.resolve_resource_wait_by_key = AsyncMock(return_value=1)
        self.resolve_other_active_resource_waits_for_inbox = AsyncMock(return_value=0)

    async def _get_by_key(self, _db: object, _key: str) -> Any | None:
        return self.existing

    async def _create(self, _db: object, data: dict[str, Any]) -> Any:
        self.created = data
        self.existing = SimpleNamespace(id=41, **data)
        return self.existing

    async def _update_resource_wait(
        self, _db: object, *, diagnostic_key: str, message: str, evidence_json: dict[str, Any]
    ) -> Any:
        self.created = {
            **(self.created or {}),
            "diagnostic_key": diagnostic_key,
            "message": message,
            "evidence_json": evidence_json,
        }
        self.existing = SimpleNamespace(id=41, **self.created)
        return self.existing


def test_entry_admission_diagnostic_resolver_removed_from_service_contract() -> None:
    from src.app.workline.repositories.diagnostic_repository import WorklineDiagnosticRepository
    from src.app.workline.services.diagnostic_service import WorklineDiagnosticService

    assert not hasattr(WorklineDiagnosticService, "resolve_entry_admission_diagnostics")
    assert not hasattr(WorklineDiagnosticRepository, "resolve_entry_admission_by_inbox_id")


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
    repo.create.assert_not_awaited()
    repo.create_idempotent_by_diagnostic_key.assert_awaited_once()
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
    repo.create_idempotent_by_diagnostic_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_diagnostic_repository_create_idempotent_by_diagnostic_key_returns_existing_without_rollback(
    db_session,
) -> None:
    from sqlalchemy import func, select

    from src.app.workline.repositories.diagnostic_repository import WorklineDiagnosticRepository

    repo = WorklineDiagnosticRepository()
    data = {
        "diagnostic_key": "duplicate-diagnostic-key",
        "trace_id": "trace-duplicate",
        "request_id": "request-duplicate",
        "diagnostic_code": "UNKNOWN",
        "error_domain": "UNKNOWN",
        "severity": "WARNING",
        "recoverability": "manual_intervention_required",
        "problem_class": "SOFTWARE",
        "owner": "runtime",
        "message": "重复诊断",
        "next_steps_json": [],
        "evidence_json": {},
        "card_json": {},
    }

    first = await repo.create_idempotent_by_diagnostic_key(db_session, data)
    second = await repo.create_idempotent_by_diagnostic_key(db_session, data)

    assert second.id == first.id
    count_result = await db_session.execute(
        select(func.count())
        .select_from(WorklineDiagnostic)
        .where(WorklineDiagnostic.diagnostic_key == data["diagnostic_key"])
    )
    assert count_result.scalar_one() == 1


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


@pytest.mark.asyncio
async def test_record_resource_wait_updates_existing_diagnostic_evidence() -> None:
    from src.app.workline.services.diagnostic_service import WorklineDiagnosticService
    from src.workline_runtime.resource_wait_evidence import ResourceWaitEvidence

    repo = _DiagnosticRepoStub()
    repo.existing = SimpleNamespace(
        id=41,
        diagnostic_key="RESOURCE_WAIT:11:station:TARGET_STATION",
        evidence_json={
            "first_seen_at": "2026-01-01T00:00:00",
            "last_seen_at": "2026-01-01T00:00:00",
            "wait_count": 1,
        },
    )
    service = WorklineDiagnosticService(repository=cast("Any", repo))

    diagnostic = await service.record_resource_wait(
        object(),
        evidence=ResourceWaitEvidence.build(
            inbox_id=11,
            resource_kind="STATION",
            resource_key="station:TARGET_STATION",
            reason_code="STATION_BUSY",
            message="目标 Station 忙",
            occurred_at="2026-01-01T00:00:10",
            session_id=22,
            workline_id=33,
            trace_id="trace-resource-wait",
        ),
        inbox=SimpleNamespace(id=11, trace_id="trace-resource-wait"),
        session=SimpleNamespace(id=22, workline_id=33),
        workline=SimpleNamespace(id=33, plugin_key="demo"),
        auto_commit=False,
    )

    assert diagnostic.id == 41
    repo.create.assert_not_awaited()
    repo.update_resource_wait_by_key.assert_awaited_once()
    evidence = repo.created["evidence_json"]
    assert evidence["first_seen_at"] == "2026-01-01T00:00:00"
    assert evidence["last_seen_at"] == "2026-01-01T00:00:10"
    assert evidence["wait_count"] == 2


@pytest.mark.asyncio
async def test_record_resource_wait_resolves_other_active_waits_for_same_inbox() -> None:
    from src.app.workline.services.diagnostic_service import WorklineDiagnosticService
    from src.workline_runtime.resource_wait_evidence import ResourceWaitEvidence

    repo = _DiagnosticRepoStub()
    service = WorklineDiagnosticService(repository=cast("Any", repo))

    db = object()
    await service.record_resource_wait(
        db,
        evidence=ResourceWaitEvidence.build(
            inbox_id=11,
            resource_kind="STATION",
            resource_key="station:TARGET_STATION_B",
            reason_code="STATION_BUSY",
            message="目标 Station B 忙",
            occurred_at="2026-01-01T00:00:10",
        ),
        inbox=SimpleNamespace(id=11),
        session=SimpleNamespace(id=22, workline_id=33),
        workline=SimpleNamespace(id=33, plugin_key="demo"),
        auto_commit=False,
    )

    repo.resolve_other_active_resource_waits_for_inbox.assert_awaited_once_with(
        db,
        inbox_id=11,
        keep_diagnostic_key="RESOURCE_WAIT:11:station:TARGET_STATION_B",
    )


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
