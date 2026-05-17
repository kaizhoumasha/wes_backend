from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.resource.models import BinStatus
from src.app.resource.services import RackReleaseService


class _FakeRackReleaseRepository:
    def __init__(self, existing: Any | None = None) -> None:
        self.existing = existing
        self.lookup_ids: list[str] = []
        self.created_payload: dict[str, Any] | None = None

    async def get_by_release_id(self, _db: Any, rack_release_id: str) -> Any | None:
        self.lookup_ids.append(rack_release_id)
        return self.existing

    async def create(self, _db: Any, data: dict[str, Any]) -> Any:
        self.created_payload = data
        return SimpleNamespace(id=77, **data)


class _FakeRackReleaseBinSnapshotRepository:
    def __init__(self) -> None:
        self.created_payloads: list[dict[str, Any]] = []

    async def create(self, _db: Any, data: dict[str, Any]) -> Any:
        self.created_payloads.append(data)
        return SimpleNamespace(id=len(self.created_payloads), **data)


@pytest.mark.asyncio
async def test_rack_release_service_records_release_and_four_bin_snapshots() -> None:
    release_repo = _FakeRackReleaseRepository()
    snapshot_repo = _FakeRackReleaseBinSnapshotRepository()
    service = RackReleaseService(  # type: ignore[arg-type]
        repo=release_repo,
        snapshot_repo=snapshot_repo,
    )

    released_at = datetime(2026, 5, 16, 9, 30, 0)

    release = await service.record_release_snapshot(
        object(),
        rack_release_id="release-001",
        single_layer_rack_code="RACK-SL-001",
        released_at=released_at,
        slot_snapshots=[
            {"slot_code": "A01", "bin_code": "BIN-001", "bin_type_code": "BIN-3", "usage_snapshot": 0.9},
            {"slot_code": "A02", "bin_code": "BIN-002", "bin_type_code": "BIN-3", "usage_snapshot": 0.8},
            {"slot_code": "A03", "bin_code": "BIN-003", "bin_type_code": "BIN-5", "usage_snapshot": 0.7},
            {"slot_code": "A04", "bin_code": "BIN-004", "bin_type_code": "BIN-9", "usage_snapshot": 0.6},
        ],
        source_classifier_line_code="SMT-01",
        source_task_batch_id="batch-001",
        source_event_id="release-event-001",
        inbox_id=10,
        session_id=123,
        trace_id="trace-runtime",
    )

    assert release.rack_release_id == "release-001"
    assert release_repo.lookup_ids == ["release-001"]
    assert release_repo.created_payload is not None
    assert release_repo.created_payload["release_status"] == "CANDIDATE"
    assert release_repo.created_payload["moved_out_at"] == released_at
    assert release_repo.created_payload["snapshot_hash"].startswith("sha256:")
    assert release_repo.created_payload["idempotency_key"].startswith("sha256:")
    assert len(snapshot_repo.created_payloads) == 4
    assert snapshot_repo.created_payloads[0]["rack_release_id"] == "release-001"
    assert snapshot_repo.created_payloads[0]["slot_code"] == "A01"
    assert snapshot_repo.created_payloads[0]["bin_code"] == "BIN-001"
    assert snapshot_repo.created_payloads[0]["bin_execution_status"] == BinStatus.FULL_SNAPSHOT


@pytest.mark.asyncio
async def test_rack_release_service_reuses_existing_release_without_rewriting_snapshots() -> None:
    existing = SimpleNamespace(id=88, rack_release_id="release-001")
    release_repo = _FakeRackReleaseRepository(existing=existing)
    snapshot_repo = _FakeRackReleaseBinSnapshotRepository()
    service = RackReleaseService(  # type: ignore[arg-type]
        repo=release_repo,
        snapshot_repo=snapshot_repo,
    )

    release = await service.record_release_snapshot(
        object(),
        rack_release_id="release-001",
        single_layer_rack_code="RACK-SL-001",
        released_at=datetime(2026, 5, 16, 9, 30, 0),
        slot_snapshots=[],
    )

    assert release is existing
    assert release_repo.created_payload is None
    assert snapshot_repo.created_payloads == []
