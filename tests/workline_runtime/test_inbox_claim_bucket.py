import pytest

from src.app.workline.models.inbox import InboxKind, InboxStatus, SourceSystem, WorklineInbox
from src.app.workline.repositories.inbox_repository import WorklineInboxRepository
from src.core.mixins import DataTableMixin
from tests.helpers.workline_inbox_factory import build_workline_inbox


def test_claim_bucket_key_helper_priority() -> None:
    from src.app.workline.inbox_claim_bucket import build_claim_bucket_key

    assert build_claim_bucket_key(session_id=10, device_id=20, workline_id=30) == "session:10"
    assert build_claim_bucket_key(device_id=20, workline_id=30) == "device:20"
    assert build_claim_bucket_key(payload_json={"device_code": "SCN-01"}, workline_id=30) == "device_code:SCN-01"
    assert build_claim_bucket_key(payload_json={"location": "LOC-01"}, workline_id=30) == "device_code:LOC-01"
    assert build_claim_bucket_key(workline_id=30) == "workline:30"
    assert build_claim_bucket_key(payload_json={}) == "serial:unknown"


def test_workline_inbox_model_declares_claim_bucket_key() -> None:
    column = WorklineInbox.__table__.c.claim_bucket_key

    assert column.nullable is False
    assert column.default is not None
    assert "claim_bucket_key" in WorklineInbox.model_fields


def test_workline_inbox_has_single_data_table_mixin() -> None:
    assert WorklineInbox.__bases__.count(DataTableMixin) == 1


@pytest.mark.asyncio
async def test_repository_create_injects_claim_bucket_key(db_session) -> None:
    repository = WorklineInboxRepository()

    created = await repository.create(
        db_session,
        {
            "kind": InboxKind.DEVICE_EVENT,
            "source_system": SourceSystem.DEVICE,
            "source_message_id": "claim-key-guard",
            "payload_json": {"device_code": "SCN-01", "event_type": "SCAN_COMPLETED"},
            "status": InboxStatus.NEW,
        },
    )

    assert created is not None
    assert created.claim_bucket_key == "device_code:SCN-01"


@pytest.mark.asyncio
async def test_repository_create_idempotent_injects_claim_bucket_key_for_direct_paths(db_session) -> None:
    repository = WorklineInboxRepository()

    created = await repository.create_idempotent(
        db_session,
        {
            "kind": InboxKind.TIMER_TIMEOUT,
            "source_system": SourceSystem.SYSTEM,
            "source_message_id": "claim-key-idempotent-guard",
            "idempotency_key": "claim-key-idempotent-guard",
            "payload_json": {"event_type": "TIMER_TIMEOUT"},
            "workline_id": 30,
            "status": InboxStatus.NEW,
        },
        idempotency_key="claim-key-idempotent-guard",
    )

    assert created.claim_bucket_key == "workline:30"


@pytest.mark.asyncio
async def test_claim_pending_messages_sorts_returning_rows_by_received_at_id() -> None:
    from datetime import datetime

    class _Mappings:
        def all(self) -> list[dict[str, object]]:
            return [
                {
                    "id": 2,
                    "processor_token": "token",
                    "received_at": datetime(2026, 1, 1, 0, 0, 2),
                    "session_id": None,
                    "workline_id": 1,
                    "device_id": None,
                    "kind": InboxKind.DEVICE_EVENT,
                    "payload_json": {},
                    "claim_bucket_key": "device:2",
                },
                {
                    "id": 1,
                    "processor_token": "token",
                    "received_at": datetime(2026, 1, 1, 0, 0, 1),
                    "session_id": None,
                    "workline_id": 1,
                    "device_id": None,
                    "kind": InboxKind.DEVICE_EVENT,
                    "payload_json": {},
                    "claim_bucket_key": "device:1",
                },
            ]

    class _Result:
        def mappings(self) -> _Mappings:
            return _Mappings()

    class _Db:
        async def execute(self, _statement: object) -> _Result:
            return _Result()

    claims = await WorklineInboxRepository().claim_pending_messages(
        _Db(),
        limit=2,
        processor_token="token",
    )

    assert [claim.id for claim in claims] == [1, 2]


def test_get_new_messages_is_removed_from_repository_and_service() -> None:
    from src.app.workline.services.inbox_service import WorklineInboxService

    assert not hasattr(WorklineInboxRepository, "get_new_messages")
    assert not hasattr(WorklineInboxService, "get_new_messages")


def test_workline_inbox_test_factory_populates_default_claim_bucket_key() -> None:
    inbox = build_workline_inbox(payload_json={"device_code": "SCN-01"})

    assert inbox.claim_bucket_key == "device_code:SCN-01"


def test_workline_inbox_test_factory_allows_explicit_claim_bucket_override() -> None:
    inbox = build_workline_inbox(payload_json={"device_code": "SCN-01"}, claim_bucket_key="test:bucket")

    assert inbox.claim_bucket_key == "test:bucket"
