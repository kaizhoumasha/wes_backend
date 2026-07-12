"""RuntimeInbox 生产三阶段链路的 PostgreSQL happy-path heavy integration。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from hashlib import sha256
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from src.app.device.models.command import DeviceCommand
from src.app.runtime.orchestration.models.session import RuntimeReconciliationState, SessionStatus, WorklineSession
from src.app.runtime.orchestration.models.timeline import (
    TimelineActionType,
    TimelineActorType,
    TimelineStage,
    TimelineStatus,
    WorklineTimeline,
)
from src.app.runtime.orchestration.repositories.runtime_inbox_repository import RuntimeInboxRepository
from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.intent.operation_service import WorklineOperationService
from src.app.runtime.orchestration.services.runtime_inbox import (
    RuntimeInboxConflict,
    RuntimeInboxReplayNotAllowed,
    RuntimeInboxService,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_service import (
    RuntimeInboxReplaySourceValidator,
)
from src.app.sys.models.audit_log import AuditLog, OperaStatus
from src.app.sys.models.outbox import SystemOutbox
from src.app.sys.services import AuditLogService
from src.app.workline.models.workline import LineType, WorkLine
from src.utils.timezone import timezone
from tests.support.runtime_inbox_processing_postgresql import (
    RecordingTaskQueueGateway,
    assert_effects,
    assert_processed_terminal,
    claim,
    processor,
    seed_scan_flow,
    with_temporary_runtime_database,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _canonical_payload_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


def test_device_event_persists_claims_and_applies_production_effects_once() -> None:
    """producer→RuntimeInbox→claim→三阶段→effects→fenced terminal 必须真实闭环。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            claimed = await claim(db, service, token="it-runtime-inbox-owner")
            assert claimed["id"] == seeded.inbox_id
            assert queue_gateway.outbox_enqueues == []
            result = await processor(service).process_claimed(db, claim=claimed)
            assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
            await assert_processed_terminal(db, inbox_id=seeded.inbox_id)
            await assert_effects(db, seeded, expected_count=1)
            assert queue_gateway.outbox_enqueues == [(None, 50)]

    asyncio.run(with_temporary_runtime_database(scenario))


def test_manual_replay_rejects_tampered_chain_without_new_replay_effect_or_audit() -> None:
    """真实 PostgreSQL 必须以锁定 root 事实拒绝 hash/root/归属篡改。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        service = RuntimeInboxService(audit_service=AuditLogService())
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            root = await db.get(RuntimeInbox, seeded.inbox_id)
            assert root is not None
            root.status = "DEAD_LETTER"
            root.failed_at = 1_700_000_000_001
            first = await service.replay_from_dead_letter(
                db,
                source_inbox_id=seeded.inbox_id,
                request_id="pg-chain-first",
                actor="integration",
                reason="seed replay chain",
            )
            first.replay_record.status = "DEAD_LETTER"
            first.replay_record.failed_at = 1_700_000_000_002
            await db.commit()
            replay_source_id = first.replay_record.id
            assert replay_source_id is not None

            baseline = {
                "inbox": await db.scalar(select(func.count()).select_from(RuntimeInbox)),
                "audit": await db.scalar(
                    select(func.count()).select_from(AuditLog).where(AuditLog.title == "RuntimeInbox 人工重放")
                ),
                "command": await db.scalar(select(func.count()).select_from(DeviceCommand)),
                "outbox": await db.scalar(select(func.count()).select_from(SystemOutbox)),
                "timeline": await db.scalar(select(func.count()).select_from(WorklineTimeline)),
            }

            for tamper in ("hash", "root", "ownership"):
                db.expire_all()
                replay_source = await db.get(RuntimeInbox, replay_source_id)
                assert replay_source is not None and isinstance(replay_source.payload_json, dict)
                envelope = dict(replay_source.payload_json)
                if tamper == "hash":
                    envelope["original_payload_hash"] = "tampered-root-hash"
                elif tamper == "root":
                    envelope["root_source_inbox_id"] = 999_999
                else:
                    envelope["original_workline_session_id"] = seeded.session_id + 1
                await db.execute(
                    update(RuntimeInbox)
                    .where(RuntimeInbox.id == replay_source_id)
                    .values(payload_json=envelope, payload_hash=_canonical_payload_hash(envelope))
                )

                with pytest.raises(RuntimeInboxReplayNotAllowed) as exc_info:
                    await service.replay_from_dead_letter(
                        db,
                        source_inbox_id=replay_source_id,
                        request_id=f"pg-chain-second-{tamper}",
                        actor="integration",
                        reason="reject tampered chain",
                    )
                assert exc_info.value.reason_code == "REPLAY_SOURCE_INTEGRITY_VIOLATION"
                await db.rollback()

                current = {
                    "inbox": await db.scalar(select(func.count()).select_from(RuntimeInbox)),
                    "audit": await db.scalar(
                        select(func.count()).select_from(AuditLog).where(AuditLog.title == "RuntimeInbox 人工重放")
                    ),
                    "command": await db.scalar(select(func.count()).select_from(DeviceCommand)),
                    "outbox": await db.scalar(select(func.count()).select_from(SystemOutbox)),
                    "timeline": await db.scalar(select(func.count()).select_from(WorklineTimeline)),
                }
                assert current == baseline

    asyncio.run(with_temporary_runtime_database(scenario))


def test_postgresql_retry_budget_constraint_and_replay_claim_contract() -> None:
    """DB 拒绝非正预算；合法 replay 使用固定预算并可被正常 claim。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        async with session_factory() as db:
            invalid = RuntimeInbox(
                kind="INTERNAL_EVENT",
                provider_code="RUNTIME",
                event_type="INTERNAL_EVENT",
                source_event_id="pg-invalid-retry-budget",
                payload_hash="hash",
                payload_json={"event_type": "INTERNAL_EVENT"},
                payload_schema_version=1,
                status="RECEIVED",
                claim_bucket_key="source:pg-invalid-retry-budget",
                received_at=1_700_000_000_000,
                max_retries=0,
            )
            db.add(invalid)
            with pytest.raises(IntegrityError, match="max_retries_positive"):
                await db.flush()
            await db.rollback()

            source_payload: dict[str, object] = {"event_type": "INTERNAL_EVENT", "data": {}}
            source = RuntimeInbox(
                kind="INTERNAL_EVENT",
                provider_code="RUNTIME",
                event_type="INTERNAL_EVENT",
                source_event_id="pg-fixed-replay-budget",
                payload_hash=_canonical_payload_hash(source_payload),
                payload_json=source_payload,
                payload_schema_version=1,
                status="DEAD_LETTER",
                claim_bucket_key="source:pg-fixed-replay-budget",
                received_at=1_700_000_000_001,
                failed_at=1_700_000_000_002,
                max_retries=99,
            )
            db.add(source)
            await db.commit()
            replay = await RuntimeInboxService(audit_service=AuditLogService()).replay_from_dead_letter(
                db,
                source_inbox_id=source.id,
                request_id="pg-fixed-budget",
                actor="integration",
                reason="fixed replay budget",
            )
            assert replay.replay_record.max_retries == 5
            await db.commit()

            claims = await RuntimeInboxService().claim_for_processing(
                db,
                limit=1,
                processor_token="pg-fixed-budget-claimer",
                stale_after_seconds=60,
            )
            assert [claim["id"] for claim in claims] == [replay.replay_record.id]
            claimed = await db.get(RuntimeInbox, replay.replay_record.id, populate_existing=True)
            assert claimed is not None
            assert claimed.status == "PROCESSING"
            assert claimed.max_retries == 5

    asyncio.run(with_temporary_runtime_database(scenario))


def test_claimed_replay_revalidates_persisted_chain_before_production_effects() -> None:
    """合法创建后的持久化篡改必须在消费最前端终止；合法控制组仍只产生一次 effect。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        service = RuntimeInboxService(audit_service=AuditLogService())
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            root = await db.get(RuntimeInbox, seeded.inbox_id)
            session = await db.get(WorklineSession, seeded.session_id)
            assert root is not None and session is not None
            root.status = "DEAD_LETTER"
            root.failed_at = 1_700_000_000_101
            session.status = SessionStatus.MANUAL_HOLD
            session.failure_code = "PAYLOAD_INVALID"
            session.failure_domain = "PAYLOAD"
            session.failure_message = "invalid payload"
            db.add(
                WorklineTimeline(
                    session_id=seeded.session_id,
                    workline_id=seeded.workline_id,
                    trace_id=seeded.trace_id,
                    seq_no=1,
                    occurred_at=timezone.now_for_db(),
                    stage=TimelineStage.MANUAL,
                    action_type=TimelineActionType.MANUAL_HOLD,
                    actor_type=TimelineActorType.ORCHESTRATOR,
                    to_status=SessionStatus.MANUAL_HOLD.value,
                    status=TimelineStatus.PENDING,
                    payload_json={"reason_code": "PAYLOAD_INVALID"},
                    related_inbox_id=seeded.inbox_id,
                )
            )
            await db.commit()
            root_snapshot = {
                "status": root.status,
                "payload_json": dict(root.payload_json or {}),
                "payload_hash": root.payload_hash,
            }

            async def create_replay_chain(label: str) -> tuple[RuntimeInbox, RuntimeInbox]:
                immediate = await service.replay_from_dead_letter(
                    db,
                    source_inbox_id=seeded.inbox_id,
                    request_id=f"pg-consume-immediate-{label}",
                    actor="integration",
                    reason="build replay chain",
                )
                immediate.replay_record.status = "DEAD_LETTER"
                immediate.replay_record.failed_at = 1_700_000_000_102
                await db.commit()
                current = await service.replay_from_dead_letter(
                    db,
                    source_inbox_id=immediate.replay_record.id,
                    request_id=f"pg-consume-current-{label}",
                    actor="integration",
                    reason="consume replay chain",
                )
                await db.commit()
                return immediate.replay_record, current.replay_record

            for tamper in ("stale_hash", "payload", "evidence", "root"):
                immediate, current = await create_replay_chain(tamper)
                immediate_id = int(immediate.id)
                current_id = int(current.id)
                immediate_snapshot = {
                    "status": immediate.status,
                    "payload_json": dict(immediate.payload_json or {}),
                    "payload_hash": immediate.payload_hash,
                }
                envelope = dict(current.payload_json or {})
                if tamper == "stale_hash":
                    tampered_hash = "stale-persisted-replay-hash"
                elif tamper == "payload":
                    envelope["original_payload"] = {
                        "event_type": "SCAN_COMPLETED",
                        "data": {"HHPN": "TAMPERED-CONSUME"},
                    }
                    tampered_hash = _canonical_payload_hash(envelope)
                elif tamper == "evidence":
                    envelope["original_workline_session_id"] = seeded.session_id + 1
                    tampered_hash = _canonical_payload_hash(envelope)
                else:
                    envelope["root_source_inbox_id"] = 999_999
                    tampered_hash = _canonical_payload_hash(envelope)
                await db.execute(
                    update(RuntimeInbox)
                    .where(RuntimeInbox.id == current_id)
                    .values(payload_json=envelope, payload_hash=tampered_hash)
                )
                await db.commit()

                baseline_effects = (
                    await db.scalar(select(func.count()).select_from(DeviceCommand)),
                    await db.scalar(select(func.count()).select_from(SystemOutbox)),
                    await db.scalar(select(func.count()).select_from(WorklineTimeline)),
                )
                claimed = await claim(db, service, token=f"pg-tampered-consume-{tamper}")
                assert claimed["id"] == current_id
                result = await processor(service).process_claimed(db, claim=claimed)
                assert result == {"processed": 1, "success": 0, "failed": 1, "skipped": 0, "resource_wait": 0}

                db.expire_all()
                terminal = await db.get(RuntimeInbox, current_id)
                persisted_immediate = await db.get(RuntimeInbox, immediate_id)
                persisted_root = await db.get(RuntimeInbox, seeded.inbox_id)
                assert terminal is not None and terminal.status == "DEAD_LETTER"
                assert terminal.last_error_message is not None
                assert "REPLAY_SOURCE_INTEGRITY_VIOLATION" in terminal.last_error_message
                assert "TAMPERED-CONSUME" not in terminal.last_error_message
                assert persisted_immediate is not None
                assert {
                    "status": persisted_immediate.status,
                    "payload_json": dict(persisted_immediate.payload_json or {}),
                    "payload_hash": persisted_immediate.payload_hash,
                } == immediate_snapshot
                assert persisted_root is not None
                assert {
                    "status": persisted_root.status,
                    "payload_json": dict(persisted_root.payload_json or {}),
                    "payload_hash": persisted_root.payload_hash,
                } == root_snapshot
                assert (
                    await db.scalar(select(func.count()).select_from(DeviceCommand)),
                    await db.scalar(select(func.count()).select_from(SystemOutbox)),
                    await db.scalar(select(func.count()).select_from(WorklineTimeline)),
                ) == baseline_effects

            _, legal = await create_replay_chain("legal-control")
            legal_id = int(legal.id)
            legal_claim = await claim(db, service, token="pg-legal-consume-control")
            assert legal_claim["id"] == legal_id
            legal_result = await processor(service).process_claimed(db, claim=legal_claim)
            assert legal_result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
            await assert_processed_terminal(db, inbox_id=legal_id)
            await assert_effects(db, replace(seeded, inbox_id=legal_id), expected_count=1)

    asyncio.run(with_temporary_runtime_database(scenario))


def test_consumer_replay_validation_does_not_deadlock_concurrent_api_replay() -> None:
    """consumer 验真后不得持 root 行锁跨越 Session/WorkLine 编排与 write-back。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            root = await db.get(RuntimeInbox, seeded.inbox_id)
            assert root is not None
            root.status = "DEAD_LETTER"
            root.failed_at = 1_700_000_000_201
            root.workline_session_id = seeded.session_id
            immediate = await service.replay_from_dead_letter(
                db,
                source_inbox_id=seeded.inbox_id,
                request_id="pg-deadlock-immediate",
                actor="integration",
                reason="build concurrent replay chain",
            )
            immediate.replay_record.status = "DEAD_LETTER"
            immediate.replay_record.failed_at = 1_700_000_000_202
            await db.commit()
            current = await service.replay_from_dead_letter(
                db,
                source_inbox_id=int(immediate.replay_record.id),
                request_id="pg-deadlock-current",
                actor="integration",
                reason="consumer replay",
            )
            await db.commit()
            current_id = int(current.replay_record.id)
            claimed = await claim(db, service, token="pg-deadlock-consumer-token")
            assert claimed["id"] == current_id

        consumer_validated = asyncio.Event()
        allow_consumer_continue = asyncio.Event()
        api_attempted_root = asyncio.Event()

        class _BarrierValidator(RuntimeInboxReplaySourceValidator):
            async def validate_for_consumption(self, db: AsyncSession, *, source: RuntimeInbox):
                validated = await super().validate_for_consumption(db, source=source)
                consumer_validated.set()
                await allow_consumer_continue.wait()
                return validated

        class _SignalingRuntimeInboxRepository(RuntimeInboxRepository):
            async def get_by_id_for_update(
                self,
                db: AsyncSession,
                inbox_id: int,
                *,
                populate_existing: bool = False,
            ) -> RuntimeInbox | None:
                if inbox_id == seeded.inbox_id:
                    api_attempted_root.set()
                return await super().get_by_id_for_update(
                    db,
                    inbox_id,
                    populate_existing=populate_existing,
                )

        async def consume_claimed() -> dict[str, int]:
            async with session_factory() as db:
                bridge = processor(service)
                bridge._replay_source_validator = _BarrierValidator(RuntimeInboxRepository())
                return await bridge.process_claimed(db, claim=claimed)

        async def replay_root_from_api() -> RuntimeInbox:
            await consumer_validated.wait()
            async with session_factory() as db:
                operation_service = WorklineOperationService(inbox_repo=_SignalingRuntimeInboxRepository())
                return await operation_service.replay_inbox(
                    db,
                    inbox_id=seeded.inbox_id,
                    request_id="pg-concurrent-api-replay",
                    actor="integration",
                    reason="prove stable lock order",
                )

        consumer_task = asyncio.create_task(consume_claimed())
        await asyncio.wait_for(consumer_validated.wait(), timeout=2)
        api_task = asyncio.create_task(replay_root_from_api())
        await asyncio.wait_for(api_attempted_root.wait(), timeout=2)
        allow_consumer_continue.set()
        consumer_result, api_replay = await asyncio.wait_for(
            asyncio.gather(consumer_task, api_task),
            timeout=5,
        )

        assert consumer_result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
        assert api_replay.kind == "REPLAY_REQUEST"
        assert api_replay.status == "RECEIVED"
        assert api_replay.payload_json["root_source_inbox_id"] == seeded.inbox_id
        async with session_factory() as db:
            await assert_processed_terminal(db, inbox_id=current_id)
            await assert_effects(db, replace(seeded, inbox_id=current_id), expected_count=1)

    asyncio.run(with_temporary_runtime_database(scenario))


def test_manual_replay_hold_provenance_survives_archive_and_is_revoked_by_later_transition() -> None:
    """真实 Repo/archive/fencing/writeback 必须承载 wrong→correct，并让后续迁移撤销旧授权。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        service = RuntimeInboxService()
        repository = RuntimeInboxRepository()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            source = await db.get(RuntimeInbox, seeded.inbox_id)
            session = await db.get(WorklineSession, seeded.session_id)
            assert source is not None and session is not None
            source.status = "DEAD_LETTER"
            source.failed_at = 1_700_000_000_001
            source.workline_session_id = seeded.session_id
            session.status = SessionStatus.MANUAL_HOLD
            session.failure_code = "PAYLOAD_INVALID"
            session.failure_domain = "PAYLOAD"
            session.failure_message = "invalid payload"
            hold = WorklineTimeline(
                session_id=seeded.session_id,
                workline_id=seeded.workline_id,
                trace_id=seeded.trace_id,
                seq_no=1,
                occurred_at=timezone.now_for_db(),
                stage=TimelineStage.MANUAL,
                action_type=TimelineActionType.MANUAL_HOLD,
                actor_type=TimelineActorType.ORCHESTRATOR,
                to_status=SessionStatus.MANUAL_HOLD.value,
                status=TimelineStatus.PENDING,
                payload_json={"reason_code": "PAYLOAD_INVALID"},
                related_inbox_id=seeded.inbox_id,
            )
            wrong_source = RuntimeInbox(
                kind="DEVICE_EVENT",
                provider_code="ECS",
                event_type="SCAN_COMPLETED",
                source_event_id="it-wrong-replay-source",
                payload_hash=_canonical_payload_hash({"event_type": "SCAN_COMPLETED", "data": {"HHPN": "WRONG"}}),
                payload_json={"event_type": "SCAN_COMPLETED", "data": {"HHPN": "WRONG"}},
                payload_schema_version=1,
                workline_id=seeded.workline_id,
                workline_session_id=seeded.session_id,
                trace_id=seeded.trace_id,
                status="DEAD_LETTER",
                claim_bucket_key=f"session:{seeded.session_id}",
                received_at=1_700_000_000_002,
                failed_at=1_700_000_000_003,
            )
            db.add_all([hold, wrong_source])
            await db.commit()

            wrong = await service.replay_from_dead_letter(
                db,
                source_inbox_id=wrong_source.id,
                request_id="it-wrong-replay",
                actor="integration",
                reason="wrong source",
            )
            await db.commit()
            wrong_claim = await claim(db, service, token="it-wrong-replay-token")
            wrong_result = await processor(service).process_claimed(db, claim=wrong_claim)
            assert wrong_result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
            await assert_processed_terminal(db, inbox_id=wrong.replay_record.id)
            command_count = await db.scalar(
                select(func.count()).select_from(DeviceCommand).where(DeviceCommand.workline_id == seeded.workline_id)
            )
            outbox_count = await db.scalar(
                select(func.count()).select_from(SystemOutbox).where(SystemOutbox.session_id == seeded.session_id)
            )
            command_timeline_count = await db.scalar(
                select(func.count())
                .select_from(WorklineTimeline)
                .where(
                    WorklineTimeline.session_id == seeded.session_id,
                    WorklineTimeline.action_type == TimelineActionType.COMMAND_SENT,
                )
            )
            assert (command_count, outbox_count, command_timeline_count) == (0, 0, 0)

            archive = await db.scalar(
                select(WorklineTimeline).where(
                    WorklineTimeline.session_id == seeded.session_id,
                    WorklineTimeline.message == "DUPLICATE_ENTRY_ARCHIVED",
                )
            )
            assert archive is not None
            assert archive.action_type == TimelineActionType.EVENT_PROCESSED
            assert archive.to_status is None
            assert archive.related_inbox_id == wrong.replay_record.id
            assert archive.seq_no > 1
            evidence_after_archive = await repository.get_latest_manual_hold_evidence(
                db,
                session_id=seeded.session_id,
            )
            assert evidence_after_archive is not None
            assert evidence_after_archive.action_type == TimelineActionType.MANUAL_HOLD.value
            assert evidence_after_archive.related_inbox_id == seeded.inbox_id

            correct = await service.replay_from_dead_letter(
                db,
                source_inbox_id=seeded.inbox_id,
                request_id="it-correct-replay",
                actor="integration",
                reason="correct source",
            )
            await db.commit()
            correct_claim = await claim(db, service, token="it-correct-replay-token")
            correct_result = await processor(service).process_claimed(db, claim=correct_claim)
            assert correct_result == {
                "processed": 1,
                "success": 1,
                "failed": 0,
                "skipped": 0,
                "resource_wait": 0,
            }
            await assert_processed_terminal(db, inbox_id=correct.replay_record.id)
            await assert_effects(db, replace(seeded, inbox_id=correct.replay_record.id), expected_count=1)

            evidence_after_transition = await repository.get_latest_manual_hold_evidence(
                db,
                session_id=seeded.session_id,
            )
            assert evidence_after_transition is not None
            assert evidence_after_transition.action_type == TimelineActionType.WAIT_STARTED.value
            assert evidence_after_transition.related_inbox_id == correct.replay_record.id

    asyncio.run(with_temporary_runtime_database(scenario))


def test_manual_replay_same_request_concurrently_creates_one_runtime_inbox() -> None:
    """真实 PostgreSQL 唯一约束与 source 行锁共同收敛并发 replay identity。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        async with session_factory() as db:
            source = RuntimeInbox(
                kind="INTERNAL_EVENT",
                provider_code="RUNTIME",
                event_type="INTERNAL_EVENT",
                source_event_id="it-replay-source",
                payload_hash="it-replay-hash",
                payload_json={"event_type": "SESSION_RESUME", "data": {}},
                payload_schema_version=1,
                trace_id="it-replay-trace",
                event_id="it-replay-event",
                status="DEAD_LETTER",
                claim_bucket_key="source:it-replay-source",
                received_at=1_700_000_000_000,
                failed_at=1_700_000_000_001,
            )
            db.add(source)
            await db.commit()
            source_id = source.id
        assert source_id is not None

        async def replay_once() -> int:
            async with session_factory() as db:
                result = await RuntimeInboxService(audit_service=AuditLogService()).replay_from_dead_letter(
                    db,
                    source_inbox_id=source_id,
                    request_id="concurrent-request",
                    actor="integration",
                    reason="concurrent replay",
                )
                await db.commit()
                assert result.replay_record.id is not None
                return result.replay_record.id

        replay_ids = await asyncio.gather(replay_once(), replay_once())
        assert replay_ids[0] == replay_ids[1]

        async with session_factory() as db:
            replay_rows = (
                (
                    await db.execute(
                        select(RuntimeInbox).where(
                            RuntimeInbox.source_event_id == f"replay:{source_id}:concurrent-request"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(replay_rows) == 1
            persisted_source = await db.get(RuntimeInbox, source_id)
            assert persisted_source is not None and persisted_source.status == "DEAD_LETTER"
            audits = (await db.execute(select(AuditLog).where(AuditLog.object_id == str(source_id)))).scalars().all()
            assert [audit.action for audit in audits].count("manual_replay") == 1
            assert [audit.action for audit in audits].count("manual_replay_conflict") == 0
            audit = audits[0]
            assert audit.status == OperaStatus.SUCCESS and audit.code == "200"
            assert audit.args is not None
            assert audit.args["replay_source_event_id"] == f"replay:{source_id}:concurrent-request"
            assert audit.args["replay_payload_hash"] == replay_rows[0].payload_hash
            assert audit.args["actor"] == "integration"
            assert audit.args["reason"] == "concurrent replay"
            assert audit.args["replay_trace_id"] == replay_rows[0].trace_id == "it-replay-trace"
            assert audit.args["causation_id"] == replay_rows[0].causation_id == "it-replay-event"
            assert "original_payload" not in audit.args

    asyncio.run(with_temporary_runtime_database(scenario))


def test_manual_replay_same_identity_different_hash_has_one_success_and_one_conflict() -> None:
    """并发同 identity 异 canonical hash 必须保留一行，并各写一次成功/冲突审计。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        async with session_factory() as db:
            source = RuntimeInbox(
                kind="INTERNAL_EVENT",
                provider_code="RUNTIME",
                event_type="INTERNAL_EVENT",
                source_event_id="it-replay-conflict-source",
                payload_hash="it-replay-conflict-hash",
                payload_json={"event_type": "SESSION_RESUME", "data": {}},
                payload_schema_version=1,
                trace_id="it-replay-conflict-trace",
                event_id="it-replay-conflict-event",
                status="DEAD_LETTER",
                claim_bucket_key="source:it-replay-conflict-source",
                received_at=1_700_000_000_000,
                failed_at=1_700_000_000_001,
            )
            db.add(source)
            await db.commit()
            source_id = source.id
        assert source_id is not None

        async def replay_once(reason: str) -> tuple[str, int | None]:
            async with session_factory() as db:
                try:
                    result = await RuntimeInboxService(audit_service=AuditLogService()).replay_from_dead_letter(
                        db,
                        source_inbox_id=source_id,
                        request_id="same-identity",
                        actor="integration",
                        reason=reason,
                    )
                except RuntimeInboxConflict:
                    await db.commit()
                    return ("conflict", None)
                await db.commit()
                return ("success", result.replay_record.id)

        outcomes = await asyncio.gather(replay_once("content-a"), replay_once("content-b"))
        assert sorted(outcome for outcome, _ in outcomes) == ["conflict", "success"]

        async with session_factory() as db:
            replay_rows = (
                (
                    await db.execute(
                        select(RuntimeInbox).where(RuntimeInbox.source_event_id == f"replay:{source_id}:same-identity")
                    )
                )
                .scalars()
                .all()
            )
            assert len(replay_rows) == 1
            persisted_source = await db.get(RuntimeInbox, source_id)
            assert persisted_source is not None and persisted_source.status == "DEAD_LETTER"
            audits = (await db.execute(select(AuditLog).where(AuditLog.object_id == str(source_id)))).scalars().all()
            by_action = {audit.action: audit for audit in audits}
            assert [audit.action for audit in audits].count("manual_replay") == 1
            assert [audit.action for audit in audits].count("manual_replay_conflict") == 1

            success = by_action["manual_replay"]
            assert success.status == OperaStatus.SUCCESS and success.code == "200"
            assert success.args is not None
            assert success.args["replay_source_event_id"] == f"replay:{source_id}:same-identity"
            assert success.args["replay_payload_hash"] == replay_rows[0].payload_hash
            assert success.args["actor"] == "integration"
            assert success.args["reason"] == replay_rows[0].payload_json["reason"]
            assert success.args["replay_trace_id"] == replay_rows[0].trace_id == "it-replay-conflict-trace"
            assert success.args["causation_id"] == replay_rows[0].causation_id == "it-replay-conflict-event"
            assert "original_payload" not in success.args

            conflict = by_action["manual_replay_conflict"]
            assert conflict.status == OperaStatus.FAIL and conflict.code == "409"
            assert conflict.args is not None
            assert conflict.args["source_event_id"] == f"replay:{source_id}:same-identity"
            assert conflict.args["existing_payload_hash"] == replay_rows[0].payload_hash
            assert conflict.args["incoming_payload_hash"] != replay_rows[0].payload_hash
            assert conflict.args["actor"] == "integration"
            assert conflict.args["source_inbox_id"] == str(source_id)
            assert conflict.args["request_id"] == "same-identity"
            assert "original_payload" not in conflict.args and "reason" not in conflict.args

    asyncio.run(with_temporary_runtime_database(scenario))


def test_manual_replay_waits_for_session_lock_and_rejects_new_pending_reconciliation() -> None:
    """双会话竞态必须服从 Session→WorkLine 锁序，锁后新 PENDING 不得放行 replay。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        async with session_factory() as db:
            workline = WorkLine(
                line_code="IT-REPLAY-LOCK-WORKLINE",
                line_name="Replay Lock Workline",
                line_type=LineType.AUTO,
                is_active=True,
            )
            db.add(workline)
            await db.flush()
            session = WorklineSession(
                session_code="IT-REPLAY-LOCK-SESSION",
                workline_id=workline.id,
                plugin_key="test",
                status=SessionStatus.RUNNING,
            )
            db.add(session)
            await db.flush()
            source = RuntimeInbox(
                kind="INTERNAL_EVENT",
                provider_code="RUNTIME",
                event_type="INTERNAL_EVENT",
                source_event_id="it-replay-lock-source",
                payload_hash="it-replay-lock-hash",
                payload_json={"event_type": "SESSION_RESUME", "data": {"session_id": session.id}},
                payload_schema_version=1,
                workline_id=workline.id,
                workline_session_id=session.id,
                status="DEAD_LETTER",
                claim_bucket_key=f"session:{session.id}",
                received_at=1_700_000_000_000,
                failed_at=1_700_000_000_001,
            )
            db.add(source)
            await db.commit()
            source_id = source.id
            session_id = session.id
            workline_id = workline.id
        assert source_id is not None and session_id is not None and workline_id is not None

        updater_holds_lock = asyncio.Event()
        replay_attempted_lock = asyncio.Event()
        allow_updater_commit = asyncio.Event()

        class _SignalingSessionRepository(WorklineSessionRepository):
            async def get_for_update(
                self,
                db: AsyncSession,
                locked_session_id: int,
                *,
                populate_existing: bool = False,
            ) -> WorklineSession | None:
                replay_attempted_lock.set()
                return await super().get_for_update(
                    db,
                    locked_session_id,
                    populate_existing=populate_existing,
                )

        async def mark_reconciliation_pending() -> None:
            async with session_factory() as db:
                locked = await WorklineSessionRepository().get_for_update(db, session_id)
                assert locked is not None
                locked.reconciliation_state = RuntimeReconciliationState.PENDING
                await db.flush()
                updater_holds_lock.set()
                await allow_updater_commit.wait()
                await db.commit()

        updater = asyncio.create_task(mark_reconciliation_pending())
        async with session_factory() as db:
            # 先把三类 replay 所有权实体放入 identity map，证明锁读会主动刷新旧快照。
            assert await db.get(WorklineSession, session_id) is not None
            assert await db.get(RuntimeInbox, source_id) is not None
            assert await db.get(WorkLine, workline_id) is not None
            await updater_holds_lock.wait()
            service = WorklineOperationService(session_repo=_SignalingSessionRepository())
            replay_task = asyncio.create_task(
                service.replay_inbox(
                    db,
                    inbox_id=source_id,
                    request_id="race-pending",
                    actor="integration",
                    reason="must observe pending",
                    auto_commit=False,
                )
            )
            await asyncio.wait_for(replay_attempted_lock.wait(), timeout=1)
            await asyncio.sleep(0.05)
            assert not replay_task.done()
            allow_updater_commit.set()
            try:
                await replay_task
            except RuntimeInboxReplayNotAllowed as exc:
                assert exc.reason_code == "SOURCE_RECONCILIATION_PENDING"
            else:
                raise AssertionError("pending reconciliation must reject replay")
            await db.rollback()
        await updater

    asyncio.run(with_temporary_runtime_database(scenario))
