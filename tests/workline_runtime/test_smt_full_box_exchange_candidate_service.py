from datetime import UTC, datetime
from types import SimpleNamespace, TracebackType
from typing import Any

import pytest

from src.app.resource.models import BinStatus, RackReleaseStatus
from src.app.workline.services.inbox_service import DuplicateInboxError
from src.app.workline.services.smt_full_box_exchange_candidate_service import (
    SmtFullBoxExchangeCandidateService,
    SmtFullBoxExchangeCandidateStatus,
)


class _FakeDb:
    def __init__(self) -> None:
        self.commits = 0
        self.pending_inboxes: list[Any] = []
        self.committed_inboxes: list[Any] = []
        self.rolled_back_savepoints = 0

    async def commit(self) -> None:
        self.commits += 1
        self.committed_inboxes.extend(self.pending_inboxes)
        self.pending_inboxes.clear()

    def begin_nested(self) -> "_FakeSavepoint":
        return _FakeSavepoint(self)


class _FakeSavepoint:
    def __init__(self, db: _FakeDb) -> None:
        self.db = db
        self.pending_start = 0

    async def __aenter__(self) -> "_FakeSavepoint":
        self.pending_start = len(self.db.pending_inboxes)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> bool:
        if exc_type is not None:
            del self.db.pending_inboxes[self.pending_start :]
            self.db.rolled_back_savepoints += 1
        return False


class _FakeRackReleaseRepository:
    def __init__(self, release: Any | None) -> None:
        self.release = release
        self.updated_payload: dict[str, Any] | None = None

    async def get_by_release_id(self, _db: Any, _rack_release_id: str) -> Any | None:
        return self.release

    async def update(self, _db: Any, _id: int, data: dict[str, Any]) -> Any:
        self.updated_payload = data
        for key, value in data.items():
            setattr(self.release, key, value)
        return self.release

    async def list_full_box_exchange_candidates(self, _db: Any, *, limit: int) -> list[Any]:
        _ = limit
        return [self.release] if self.release is not None else []


class _FakeRackReleaseBinSnapshotRepository:
    def __init__(self, snapshots: list[Any]) -> None:
        self.snapshots = snapshots

    async def list_by_release_id(self, _db: Any, _rack_release_id: str) -> list[Any]:
        return self.snapshots


class _FakeInboxService:
    def __init__(self, duplicate_inbox: Any | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.duplicate_inbox = duplicate_inbox

    async def create_device_event_inbox(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.duplicate_inbox is not None:
            raise DuplicateInboxError(
                "设备事件已存在（幂等键重复）",
                existing_inbox=self.duplicate_inbox,
            )
        inbox = SimpleNamespace(id=701, payload_json={"data": kwargs["data"]}, event_id=kwargs["event_id"])
        db = kwargs.get("db")
        if hasattr(db, "pending_inboxes"):
            db.pending_inboxes.append(inbox)
        return inbox


class _FailingRackReleaseRepository(_FakeRackReleaseRepository):
    async def update(self, _db: Any, _id: int, data: dict[str, Any]) -> Any:
        self.updated_payload = data
        raise RuntimeError("mark release failed")


def _release(**overrides: Any) -> Any:
    data = {
        "id": 31,
        "version": 0,
        "rack_release_id": "release-001",
        "single_layer_rack_code": "RACK-SL-001",
        "source_classifier_line_code": "WL-SMT-CLASSIFIER-01",
        "source_task_batch_id": "batch-001",
        "source_event_id": "classifier-release-event-001",
        "release_status": RackReleaseStatus.CANDIDATE,
        "released_at": datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
        "moved_out_at": datetime(2026, 5, 16, 10, 3, tzinfo=UTC),
        "inbox_id": None,
        "release_cycle_seq": 2,
        "snapshot_hash": "snapshot-hash-001",
        "trace_id": "trace-release-001",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _snapshots(count: int = 4) -> list[Any]:
    return [
        SimpleNamespace(
            slot_code=f"S{index}",
            bin_code=f"BIN-{index:03d}",
            bin_type_code="SMT_BIN",
            bin_execution_status=BinStatus.IN_USE,
            usage_snapshot=0.9,
            material_summary_json={"pkg_count": index},
            wms_inventory_refs_json={"inventory_version": f"v{index}"},
            snapshot_id=f"bin-snapshot-{index}",
            content_snapshot_hash=f"content-hash-{index}",
        )
        for index in range(1, count + 1)
    ]


@pytest.mark.asyncio
async def test_candidate_service_creates_single_layer_release_inbox_and_marks_release() -> None:
    release_repo = _FakeRackReleaseRepository(_release())
    snapshot_repo = _FakeRackReleaseBinSnapshotRepository(_snapshots())
    inbox_service = _FakeInboxService()
    service = SmtFullBoxExchangeCandidateService(
        rack_release_repo=release_repo,
        rack_release_snapshot_repo=snapshot_repo,
        inbox_service=inbox_service,
    )

    result = await service.create_inbox_for_release(
        _FakeDb(),
        rack_release_id="release-001",
        source_device_code="SMT_FULL_EXCHANGE_TRIGGER_01",
    )

    assert result.status == SmtFullBoxExchangeCandidateStatus.INBOX_CREATED
    assert result.inbox.id == 701
    assert inbox_service.calls[0]["event_type"] == "SINGLE_LAYER_RACK_RELEASED"
    assert inbox_service.calls[0]["canonical_event_type"] == "SINGLE_LAYER_RACK_RELEASED"
    assert inbox_service.calls[0]["event_id"] == "smt-full-box-exchange:release-001"
    assert inbox_service.calls[0]["trace_id"] == "trace-release-001"
    assert inbox_service.calls[0]["source_message_id"] == "release-001"
    assert inbox_service.calls[0]["data"]["rack_release_id"] == "release-001"
    assert inbox_service.calls[0]["data"]["single_layer_rack_id"] == "RACK-SL-001"
    assert [item["slot_code"] for item in inbox_service.calls[0]["data"]["bin_snapshots"]] == [
        "S1",
        "S2",
        "S3",
        "S4",
    ]
    assert [item["bin_id"] for item in inbox_service.calls[0]["data"]["bins"]] == [
        "BIN-001",
        "BIN-002",
        "BIN-003",
        "BIN-004",
    ]
    assert release_repo.updated_payload == {
        "inbox_id": 701,
        "release_status": RackReleaseStatus.INBOX_CREATED.value,
        "version": 0,
    }


@pytest.mark.asyncio
async def test_candidate_service_skips_release_before_rack_moved_out() -> None:
    release_repo = _FakeRackReleaseRepository(_release(moved_out_at=None))
    snapshot_repo = _FakeRackReleaseBinSnapshotRepository(_snapshots())
    inbox_service = _FakeInboxService()
    service = SmtFullBoxExchangeCandidateService(
        rack_release_repo=release_repo,
        rack_release_snapshot_repo=snapshot_repo,
        inbox_service=inbox_service,
    )

    result = await service.create_inbox_for_release(
        _FakeDb(),
        rack_release_id="release-001",
        source_device_code="SMT_FULL_EXCHANGE_TRIGGER_01",
    )

    assert result.status == SmtFullBoxExchangeCandidateStatus.SKIPPED
    assert result.reason_code == "RACK_NOT_MOVED_OUT"
    assert inbox_service.calls == []
    assert release_repo.updated_payload is None


@pytest.mark.asyncio
async def test_candidate_service_skips_incomplete_four_bin_snapshot() -> None:
    release_repo = _FakeRackReleaseRepository(_release())
    snapshot_repo = _FakeRackReleaseBinSnapshotRepository(_snapshots(count=3))
    inbox_service = _FakeInboxService()
    service = SmtFullBoxExchangeCandidateService(
        rack_release_repo=release_repo,
        rack_release_snapshot_repo=snapshot_repo,
        inbox_service=inbox_service,
    )

    result = await service.create_inbox_for_release(
        _FakeDb(),
        rack_release_id="release-001",
        source_device_code="SMT_FULL_EXCHANGE_TRIGGER_01",
    )

    assert result.status == SmtFullBoxExchangeCandidateStatus.SKIPPED
    assert result.reason_code == "RACK_RELEASE_SNAPSHOT_INCOMPLETE"
    assert inbox_service.calls == []
    assert release_repo.updated_payload is None


@pytest.mark.asyncio
async def test_candidate_service_skips_duplicate_bin_snapshot() -> None:
    snapshots = _snapshots()
    snapshots[1].bin_code = "BIN-001"
    release_repo = _FakeRackReleaseRepository(_release())
    snapshot_repo = _FakeRackReleaseBinSnapshotRepository(snapshots)
    inbox_service = _FakeInboxService()
    service = SmtFullBoxExchangeCandidateService(
        rack_release_repo=release_repo,
        rack_release_snapshot_repo=snapshot_repo,
        inbox_service=inbox_service,
    )

    result = await service.create_inbox_for_release(
        _FakeDb(),
        rack_release_id="release-001",
        source_device_code="SMT_FULL_EXCHANGE_TRIGGER_01",
    )

    assert result.status == SmtFullBoxExchangeCandidateStatus.SKIPPED
    assert result.reason_code == "RACK_RELEASE_BIN_DUPLICATED"
    assert inbox_service.calls == []
    assert release_repo.updated_payload is None


@pytest.mark.asyncio
async def test_candidate_service_returns_already_linked_when_release_has_inbox() -> None:
    release_repo = _FakeRackReleaseRepository(_release(inbox_id=701))
    snapshot_repo = _FakeRackReleaseBinSnapshotRepository(_snapshots())
    inbox_service = _FakeInboxService()
    service = SmtFullBoxExchangeCandidateService(
        rack_release_repo=release_repo,
        rack_release_snapshot_repo=snapshot_repo,
        inbox_service=inbox_service,
    )

    result = await service.create_inbox_for_release(
        _FakeDb(),
        rack_release_id="release-001",
        source_device_code="SMT_FULL_EXCHANGE_TRIGGER_01",
    )

    assert result.status == SmtFullBoxExchangeCandidateStatus.ALREADY_LINKED
    assert result.reason_code == "RACK_RELEASE_ALREADY_HAS_INBOX"
    assert inbox_service.calls == []
    assert release_repo.updated_payload is None


@pytest.mark.asyncio
async def test_candidate_service_returns_existing_inbox_after_duplicate_inbox_race() -> None:
    existing_inbox = SimpleNamespace(id=702, payload_json={}, event_id="smt-full-box-exchange:release-001")
    release_repo = _FakeRackReleaseRepository(_release())
    snapshot_repo = _FakeRackReleaseBinSnapshotRepository(_snapshots())
    inbox_service = _FakeInboxService(duplicate_inbox=existing_inbox)
    service = SmtFullBoxExchangeCandidateService(
        rack_release_repo=release_repo,
        rack_release_snapshot_repo=snapshot_repo,
        inbox_service=inbox_service,
    )

    result = await service.create_inbox_for_release(
        _FakeDb(),
        rack_release_id="release-001",
        source_device_code="SMT_FULL_EXCHANGE_TRIGGER_01",
    )

    assert result.status == SmtFullBoxExchangeCandidateStatus.INBOX_CREATED
    assert result.inbox is existing_inbox
    assert release_repo.updated_payload == {
        "inbox_id": 702,
        "release_status": RackReleaseStatus.INBOX_CREATED.value,
        "version": 0,
    }


@pytest.mark.asyncio
async def test_candidate_service_batch_scan_counts_created_inbox() -> None:
    release_repo = _FakeRackReleaseRepository(_release())
    snapshot_repo = _FakeRackReleaseBinSnapshotRepository(_snapshots())
    inbox_service = _FakeInboxService()
    service = SmtFullBoxExchangeCandidateService(
        rack_release_repo=release_repo,
        rack_release_snapshot_repo=snapshot_repo,
        inbox_service=inbox_service,
    )

    result = await service.scan_candidates(
        _FakeDb(),
        source_device_code="SMT_FULL_EXCHANGE_TRIGGER_01",
        limit=20,
    )

    assert result == {
        "scanned": 1,
        "inbox_created": 1,
        "already_linked": 0,
        "skipped": 0,
        "errors": 0,
    }


@pytest.mark.asyncio
async def test_candidate_service_batch_scan_rolls_back_inbox_when_release_mark_fails() -> None:
    db = _FakeDb()
    release_repo = _FailingRackReleaseRepository(_release())
    snapshot_repo = _FakeRackReleaseBinSnapshotRepository(_snapshots())
    inbox_service = _FakeInboxService()
    service = SmtFullBoxExchangeCandidateService(
        rack_release_repo=release_repo,
        rack_release_snapshot_repo=snapshot_repo,
        inbox_service=inbox_service,
    )

    result = await service.scan_candidates(
        db,
        source_device_code="SMT_FULL_EXCHANGE_TRIGGER_01",
        limit=20,
    )

    assert result == {
        "scanned": 1,
        "inbox_created": 0,
        "already_linked": 0,
        "skipped": 0,
        "errors": 1,
    }
    assert inbox_service.calls
    assert db.rolled_back_savepoints == 1
    assert db.committed_inboxes == []
