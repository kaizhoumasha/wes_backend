from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.resource.models import FullBoxExchangeStatus
from src.app.resource.services import FullBoxExchangeTaskService


class _FakeDb:
    def __init__(self) -> None:
        self.flush_count = 0

    async def flush(self) -> None:
        self.flush_count += 1


class _FakeFullBoxExchangeTaskRepository:
    def __init__(self, existing: Any | None = None) -> None:
        self.existing = existing
        self.lookup_codes: list[str] = []
        self.created_payload: dict[str, Any] | None = None
        self.updated_payload: dict[str, Any] | None = None

    async def get_by_exchange_request_code(self, _db: Any, exchange_request_code: str) -> Any | None:
        self.lookup_codes.append(exchange_request_code)
        return self.existing

    async def create(self, _db: Any, data: dict[str, Any]) -> Any:
        self.created_payload = data
        return SimpleNamespace(id=77, **data)

    async def update(self, _db: Any, id: int, data: dict[str, Any]) -> Any:
        self.updated_payload = {"id": id, **data}
        return SimpleNamespace(id=id, **data)


class _RecordingRelationProjector:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record_full_box_exchange_physical_completed(self, _db: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(status="PROJECTED")


@pytest.mark.asyncio
async def test_full_box_exchange_task_service_records_requested_external_request() -> None:
    repo = _FakeFullBoxExchangeTaskRepository()
    service = FullBoxExchangeTaskService(repo=repo)  # type: ignore[arg-type]
    db = _FakeDb()

    task = await service.record_requested_from_external_request(
        db,  # type: ignore[arg-type]
        session=SimpleNamespace(id=123),
        outbox=SimpleNamespace(id=456),
        dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
        target_code="http://wms-rcs/api/full-box-exchange",
        payload_json={
            "exchange_request_code": "external:smt:release-001:FULL_BIN_EXCHANGE",
            "rack_release_id": "release-001",
            "exchange_area_code": "SMT-EXCHANGE",
            "requested_bins": [{"bin_code": "BIN-001", "rack_slot_code": "A01"}, "ignored"],
        },
        trace_id="trace-runtime",
    )

    assert task is not None
    assert db.flush_count == 1
    assert repo.lookup_codes == ["external:smt:release-001:FULL_BIN_EXCHANGE"]
    assert repo.created_payload is not None
    assert repo.created_payload["exchange_request_code"] == "external:smt:release-001:FULL_BIN_EXCHANGE"
    assert repo.created_payload["rack_release_id"] == "release-001"
    assert repo.created_payload["session_id"] == 123
    assert repo.created_payload["outbox_id"] == 456
    assert repo.created_payload["dispatch_key"] == "external:smt:release-001:FULL_BIN_EXCHANGE"
    assert repo.created_payload["exchange_status"] == FullBoxExchangeStatus.REQUESTED
    assert repo.created_payload["exchange_area_code"] == "SMT-EXCHANGE"
    assert repo.created_payload["requested_bins_json"] == [{"bin_code": "BIN-001", "rack_slot_code": "A01"}]
    assert repo.created_payload["request_payload_hash"].startswith("sha256:")
    assert repo.created_payload["trace_id"] == "trace-runtime"


@pytest.mark.asyncio
async def test_full_box_exchange_task_service_reuses_existing_request() -> None:
    existing = SimpleNamespace(id=88, exchange_request_code="external:smt:release-001:FULL_BIN_EXCHANGE")
    repo = _FakeFullBoxExchangeTaskRepository(existing=existing)
    service = FullBoxExchangeTaskService(repo=repo)  # type: ignore[arg-type]
    db = _FakeDb()

    task = await service.record_requested_from_external_request(
        db,  # type: ignore[arg-type]
        session=SimpleNamespace(id=123),
        outbox=SimpleNamespace(id=456),
        dispatch_key="external:smt:release-001:FULL_BIN_EXCHANGE",
        target_code="http://wms-rcs/api/full-box-exchange",
        payload_json={
            "exchange_request_code": "external:smt:release-001:FULL_BIN_EXCHANGE",
            "rack_release_id": "release-001",
        },
        trace_id="trace-runtime",
    )

    assert task is existing
    assert db.flush_count == 0
    assert repo.created_payload is None


@pytest.mark.asyncio
async def test_full_box_exchange_task_service_skips_non_full_box_payload() -> None:
    repo = _FakeFullBoxExchangeTaskRepository()
    service = FullBoxExchangeTaskService(repo=repo)  # type: ignore[arg-type]

    task = await service.record_requested_from_external_request(
        _FakeDb(),  # type: ignore[arg-type]
        session=SimpleNamespace(id=123),
        outbox=SimpleNamespace(id=456),
        dispatch_key="external:smt_classifier:trace-001:RACK_EXCHANGE_AND_SUPPLY",
        target_code="http://wms-rcs/api/rack-exchange",
        payload_json={"request_type": "SMT_RACK_EXCHANGE_AND_SUPPLY"},
        trace_id="trace-runtime",
    )

    assert task is None
    assert repo.lookup_codes == []
    assert repo.created_payload is None


@pytest.mark.asyncio
async def test_full_box_exchange_task_service_records_external_callback_status() -> None:
    existing = SimpleNamespace(
        id=88,
        version=3,
        exchange_request_code="external:smt:release-001:FULL_BIN_EXCHANGE",
    )
    repo = _FakeFullBoxExchangeTaskRepository(existing=existing)
    service = FullBoxExchangeTaskService(repo=repo)  # type: ignore[arg-type]

    task = await service.record_callback_from_external_http(
        _FakeDb(),  # type: ignore[arg-type]
        payload_json={
            "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
            "exchange_request_code": "external:smt:release-001:FULL_BIN_EXCHANGE",
            "rack_release_id": "release-001",
            "wms_rcs_task_id": "wms-task-001",
            "source_event_id": "wms-event-001",
            "exchange_status": "PHYSICAL_COMPLETED",
            "queue_position": "2",
            "eta_seconds": 120,
        },
        trace_id="trace-runtime",
    )

    assert task is not None
    assert repo.lookup_codes == ["external:smt:release-001:FULL_BIN_EXCHANGE"]
    assert repo.updated_payload is not None
    assert repo.updated_payload["id"] == 88
    assert repo.updated_payload["version"] == 3
    assert repo.updated_payload["exchange_status"] == FullBoxExchangeStatus.PHYSICAL_COMPLETED
    assert repo.updated_payload["wms_rcs_task_id"] == "wms-task-001"
    assert repo.updated_payload["wms_rcs_event_id"] == "wms-event-001"
    assert repo.updated_payload["queue_position"] == 2
    assert repo.updated_payload["eta_seconds"] == 120
    assert repo.updated_payload["last_callback_payload_hash"].startswith("sha256:")
    assert repo.updated_payload["trace_id"] == "trace-runtime"


@pytest.mark.asyncio
async def test_full_box_exchange_task_service_projects_physical_completed_relations() -> None:
    existing = SimpleNamespace(
        id=88,
        version=3,
        exchange_request_code="external:smt:release-001:FULL_BIN_EXCHANGE",
        rack_release_id="release-001",
    )
    repo = _FakeFullBoxExchangeTaskRepository(existing=existing)
    projector = _RecordingRelationProjector()
    service = FullBoxExchangeTaskService(  # type: ignore[arg-type]
        repo=repo,
        relation_projector=projector,
    )

    await service.record_callback_from_external_http(
        _FakeDb(),  # type: ignore[arg-type]
        payload_json={
            "callback_type": "WMS_FULL_BOX_EXCHANGE_RESULT",
            "exchange_request_code": "external:smt:release-001:FULL_BIN_EXCHANGE",
            "rack_release_id": "release-001",
            "wms_rcs_task_id": "wms-task-001",
            "source_event_id": "wms-event-001",
            "source_version": "1",
            "occurred_at": "2026-05-16T09:00:00Z",
            "exchange_status": "PHYSICAL_COMPLETED",
            "post_exchange_relations": {
                "bin_mounts": [{"rack_code": "RACK-002", "rack_slot_code": "A01", "bin_code": "BIN-001"}]
            },
        },
        trace_id="trace-runtime",
    )

    assert len(projector.calls) == 1
    assert projector.calls[0]["exchange_request_code"] == "external:smt:release-001:FULL_BIN_EXCHANGE"
    assert projector.calls[0]["rack_release_id"] == "release-001"
    assert projector.calls[0]["source_event_id"] == "wms-event-001"
    assert projector.calls[0]["source_task_id"] == "wms-task-001"
    assert projector.calls[0]["post_exchange_relations"]["bin_mounts"][0]["bin_code"] == "BIN-001"
