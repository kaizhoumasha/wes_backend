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

    async def get_by_exchange_request_code(self, _db: Any, exchange_request_code: str) -> Any | None:
        self.lookup_codes.append(exchange_request_code)
        return self.existing

    async def create(self, _db: Any, data: dict[str, Any]) -> Any:
        self.created_payload = data
        return SimpleNamespace(id=77, **data)


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
