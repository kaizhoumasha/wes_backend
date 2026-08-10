"""RuntimeInbox 无插件 PostgreSQL 领取、重放和防篡改证据。"""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.app.device.models.device import Device, DeviceStatus
from src.app.runtime.orchestration.runtime_inbox import RuntimeInbox
from src.app.runtime.orchestration.services.runtime_inbox import RuntimeInboxConflict, RuntimeInboxService
from src.app.sys.models.audit_log import AuditLog, OperaStatus
from src.app.sys.services import AuditLogService
from src.app.workline.models.workline import LineType, WorkLine
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
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def test_unbound_workline_device_event_persists_idempotently_without_execution_objects() -> None:
    """通用 ingress 不得要求插件 binding，也不得在领取前创建执行对象。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        service = RuntimeInboxService()
        async with session_factory() as db:
            workline = WorkLine(
                line_code="IT-RUNTIME-INBOX-UNBOUND",
                line_name="RuntimeInbox Unbound Ingress",
                line_type=LineType.AUTO,
                is_active=True,
            )
            db.add(workline)
            await db.flush()
            scanner = Device(
                device_code="IT-UNBOUND-SCANNER-01",
                device_name="Unbound Ingress Scanner",
                work_line_id=workline.id,
                device_role="SCANNER",
                device_status=DeviceStatus.IDLE,
                version=1,
            )
            db.add(scanner)
            await db.flush()
            payload = {
                "event_type": "SCAN_COMPLETED",
                "device_code": scanner.device_code,
                "data": {"scan_code": "UNBOUND-001"},
            }
            accepted = await service.accept_device_event(
                db,
                device_code=scanner.device_code,
                event_type="SCAN_COMPLETED",
                payload_json=payload,
                trace_id="trace-unbound-ingress",
                event_id="event-unbound-ingress",
                workline_id=workline.id,
                device_id=scanner.id,
            )
            duplicate = await service.accept_device_event(
                db,
                device_code=scanner.device_code,
                event_type="SCAN_COMPLETED",
                payload_json=payload,
                trace_id="trace-unbound-ingress",
                event_id="event-unbound-ingress",
                workline_id=workline.id,
                device_id=scanner.id,
            )
            await db.commit()
            assert accepted.record.id == duplicate.record.id
            assert accepted.record.execution_session_id is None
            assert accepted.record.workline_session_id is None

    asyncio.run(with_temporary_runtime_database(scenario))


def test_device_event_claims_and_processes_without_plugin_side_effects() -> None:
    """无插件 RuntimeInbox 仍完成领取、围栏终态和零业务副作用闭环。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            claimed = await claim(db, service, token="it-runtime-inbox-owner")
            result = await processor(service).process_claimed(db, claim=claimed)
            assert result == {"processed": 1, "success": 1, "failed": 0, "skipped": 0, "resource_wait": 0}
            await assert_processed_terminal(db, inbox_id=seeded.inbox_id)
            await assert_effects(db, seeded, expected_count=0)

    asyncio.run(with_temporary_runtime_database(scenario))


def test_claim_is_fenced_to_one_worker_before_terminal_processing() -> None:
    """同一 Inbox 一经领取，第二个 worker 不得获得并行处理权。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        service = RuntimeInboxService()
        async with session_factory() as db:
            seeded = await seed_scan_flow(db)
            first = await claim(db, service, token="it-runtime-inbox-first")
            second = await service.claim_for_processing(
                db,
                limit=1,
                processor_token="it-runtime-inbox-second",
                stale_after_seconds=60,
            )
            assert second == []
            assert not await service.mark_processed(
                db,
                inbox_id=seeded.inbox_id,
                lease_token="it-runtime-inbox-second",
            )
            await db.rollback()
            result = await processor(service).process_claimed(db, claim=first)
            assert result["success"] == 1
            await assert_processed_terminal(db, inbox_id=seeded.inbox_id)

    asyncio.run(with_temporary_runtime_database(scenario))


def test_manual_replay_same_request_concurrently_creates_one_runtime_inbox() -> None:
    """真实 PostgreSQL 唯一约束和源行锁共同收敛并发 replay identity。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        async with session_factory() as db:
            source = RuntimeInbox(
                kind="INTERNAL_EVENT",
                provider_code="RUNTIME",
                event_type="INTERNAL_EVENT",
                source_event_id="it-replay-source",
                payload_hash=_canonical_payload_hash({"event_type": "SESSION_RESUME", "data": {}}),
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
            assert source.id is not None
            source_id = source.id

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
                return int(result.replay_record.id)

        replay_ids = await asyncio.gather(replay_once(), replay_once())
        assert replay_ids[0] == replay_ids[1]
        async with session_factory() as db:
            rows = list(
                (
                    await db.scalars(
                        select(RuntimeInbox).where(
                            RuntimeInbox.source_event_id == f"replay:{source_id}:concurrent-request"
                        )
                    )
                ).all()
            )
            audits = list((await db.scalars(select(AuditLog).where(AuditLog.object_id == str(source_id)))).all())
            assert len(rows) == 1
            assert [audit.action for audit in audits].count("manual_replay") == 1
            assert audits[0].status == OperaStatus.SUCCESS

    asyncio.run(with_temporary_runtime_database(scenario))


def test_manual_replay_rejects_identity_with_different_payload_hash() -> None:
    """同一 replay identity 的不同请求内容必须显式冲突，不能覆盖首条证据。"""

    async def scenario(
        session_factory: async_sessionmaker[AsyncSession], _queue_gateway: RecordingTaskQueueGateway
    ) -> None:
        async with session_factory() as db:
            source = RuntimeInbox(
                kind="INTERNAL_EVENT",
                provider_code="RUNTIME",
                event_type="INTERNAL_EVENT",
                source_event_id="it-replay-conflict-source",
                payload_hash=_canonical_payload_hash({"event_type": "SESSION_RESUME", "data": {}}),
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
            assert source.id is not None
            source_id = source.id

        async def replay_once(reason: str) -> str:
            async with session_factory() as db:
                try:
                    await RuntimeInboxService(audit_service=AuditLogService()).replay_from_dead_letter(
                        db,
                        source_inbox_id=source_id,
                        request_id="same-identity",
                        actor="integration",
                        reason=reason,
                    )
                except RuntimeInboxConflict:
                    await db.commit()
                    return "conflict"
                await db.commit()
                return "success"

        outcomes = await asyncio.gather(replay_once("content-a"), replay_once("content-b"))
        assert sorted(outcomes) == ["conflict", "success"]

    asyncio.run(with_temporary_runtime_database(scenario))
