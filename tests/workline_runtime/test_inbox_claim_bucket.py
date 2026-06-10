from hashlib import md5
from pathlib import Path

import pytest

from src.app.workline.models.inbox import InboxKind, InboxStatus, SourceSystem, WorklineInbox
from src.app.workline.models.session import WorklineSession
from src.app.workline.models.workline import LineType, WorkLine
from src.app.workline.repositories.inbox_repository import WorklineInboxRepository
from src.app.workline.services.inbox_service import inbox_service
from src.core.mixins import DataTableMixin
from tests.helpers.workline_inbox_factory import build_workline_inbox


async def _create_workline_session(db_session, suffix: str) -> WorklineSession:
    workline = WorkLine(
        line_code=f"claim-bucket-{suffix}",
        line_name=f"claim-bucket-{suffix}",
        line_type=LineType.AUTO,
    )
    db_session.add(workline)
    await db_session.flush()

    session = WorklineSession(
        session_code=f"claim-bucket-session-{suffix}",
        workline_id=workline.id,
        plugin_key="claim-bucket-test",
    )
    db_session.add(session)
    await db_session.flush()
    return session


def test_claim_bucket_key_helper_priority() -> None:
    from src.app.workline.inbox_claim_bucket import build_claim_bucket_key

    assert build_claim_bucket_key(session_id=10, device_id=20, workline_id=30) == "session:10"
    assert build_claim_bucket_key(device_id=20, workline_id=30) == "device:20"
    assert build_claim_bucket_key(payload_json={"device_code": "SCN-01"}, workline_id=30) == "device_code:SCN-01"
    assert build_claim_bucket_key(payload_json={"location": "LOC-01"}, workline_id=30) == "device_code:LOC-01"
    assert build_claim_bucket_key(workline_id=30) == "workline:30"
    assert build_claim_bucket_key(payload_json={}) == "serial:unknown"


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"session_id": " 10 ", "device_id": 20, "workline_id": 30}, "session:10"),
        ({"device_id": " 20 ", "workline_id": 30}, "device:20"),
        ({"payload_json": {"device_code": "  SCN-01  "}, "workline_id": 30}, "device_code:SCN-01"),
        (
            {"payload_json": {"device_code": "   ", "location": "  LOC-01  "}, "workline_id": 30},
            "device_code:LOC-01",
        ),
        ({"workline_id": " 30 "}, "workline:30"),
        ({"payload_json": {"device_code": "   ", "location": ""}}, "serial:unknown"),
    ],
)
def test_claim_bucket_key_helper_matches_backfill_case_matrix(kwargs: dict[str, object], expected: str) -> None:
    from src.app.workline.inbox_claim_bucket import build_claim_bucket_key

    assert build_claim_bucket_key(**kwargs) == expected


def test_claim_bucket_key_helper_trims_payload_fallbacks() -> None:
    from src.app.workline.inbox_claim_bucket import build_claim_bucket_key

    assert build_claim_bucket_key(payload_json={"device_code": "  SCN-01  "}) == "device_code:SCN-01"
    assert build_claim_bucket_key(payload_json={"device_code": "   ", "location": "  LOC-01  "}) == "device_code:LOC-01"


def test_claim_bucket_key_helper_fits_model_column_length() -> None:
    from src.app.workline.inbox_claim_bucket import build_claim_bucket_key

    raw_key = "device_code:" + ("S" * 500)
    digest = md5(raw_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    claim_bucket_key = build_claim_bucket_key(payload_json={"device_code": "S" * 500})

    assert len(claim_bucket_key) <= 200
    assert claim_bucket_key == f"{raw_key[:183]}:{digest}"


def test_claim_bucket_key_uses_only_hot_queue_composite_index() -> None:
    indexes_by_name = {
        index.name: [column.name for column in index.columns] for index in WorklineInbox.__table__.indexes
    }

    assert indexes_by_name["ix_wes_biz_workline_inbox_hot_claim_bucket_fifo"] == [
        "claim_bucket_key",
        "received_at",
        "id",
    ]
    assert "ix_wes_biz_workline_inbox_claim_bucket_key" not in indexes_by_name


def test_claim_bucket_key_for_update_respects_explicit_none() -> None:
    from types import SimpleNamespace

    from src.app.workline.inbox_claim_bucket import build_claim_bucket_key_for_update

    current = SimpleNamespace(
        session_id=10,
        device_id=20,
        workline_id=30,
        payload_json={"device_code": "SCN-01"},
    )

    assert build_claim_bucket_key_for_update(current=current, data={"session_id": None}) == "device:20"
    assert (
        build_claim_bucket_key_for_update(current=current, data={"session_id": None, "device_id": None})
        == "device_code:SCN-01"
    )
    assert (
        build_claim_bucket_key_for_update(
            current=current,
            data={"session_id": None, "device_id": None, "payload_json": None},
        )
        == "workline:30"
    )
    assert (
        build_claim_bucket_key_for_update(
            current=current,
            data={"session_id": None, "device_id": None, "workline_id": None, "payload_json": None},
        )
        == "serial:unknown"
    )


def test_claim_bucket_key_migration_backfill_matches_runtime_normalization_contract() -> None:
    migration_sql = Path(
        "migrations/versions/20260609_2208_2937b05e1b1c_add_workline_inbox_claim_bucket_key.py"
    ).read_text()

    assert "btrim(payload_json ->> 'device_code')" in migration_sql
    assert "btrim(payload_json ->> 'location')" in migration_sql
    assert "length(normalized.raw_claim_bucket_key) <= 200" in migration_sql
    assert "substring(normalized.raw_claim_bucket_key from 1 for 183)" in migration_sql


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
async def test_repository_update_recomputes_claim_bucket_key(db_session) -> None:
    repository = WorklineInboxRepository()
    created = await repository.create(
        db_session,
        {
            "kind": InboxKind.DEVICE_EVENT,
            "source_system": SourceSystem.DEVICE,
            "source_message_id": "claim-key-update-guard",
            "payload_json": {"device_code": "SCN-01"},
            "status": InboxStatus.NEW,
        },
    )
    assert created is not None

    updated = await repository.update(
        db_session,
        created.id,
        {"payload_json": {"device_code": "SCN-02"}},
    )

    assert updated is not None
    assert updated.claim_bucket_key == "device_code:SCN-02"


@pytest.mark.asyncio
async def test_processing_retry_keeps_claim_bucket_frozen_after_resolved_session(db_session) -> None:
    repository = WorklineInboxRepository()
    session = await _create_workline_session(db_session, "processing")
    created = await repository.create(
        db_session,
        {
            "kind": InboxKind.DEVICE_EVENT,
            "source_system": SourceSystem.DEVICE,
            "source_message_id": "claim-key-processing-guard",
            "payload_json": {"device_code": "SCN-01"},
            "status": InboxStatus.PROCESSING,
            "processor_token": "processor-token",
        },
    )
    assert created is not None
    created.session_id = session.id

    updated = await inbox_service.park_for_retry(
        db_session,
        created.id,
        "RESOURCE_WAIT",
        processor_token="processor-token",
        auto_commit=False,
    )

    assert updated.status == InboxStatus.RETRY
    assert updated.claim_bucket_key == "device_code:SCN-01"


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
