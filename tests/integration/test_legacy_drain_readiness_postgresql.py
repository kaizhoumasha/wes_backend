"""Legacy drain 的单 statement、只读 PostgreSQL owner。"""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, text

from src.app.runtime.orchestration.repositories.legacy_drain_readiness_repository import (
    LegacyDrainReadinessRepository,
)
from src.utils.timezone import timezone


@pytest.mark.asyncio
async def test_repository_executes_one_read_only_snapshot_with_timeout(integration_db_session) -> None:
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement: str, _parameters, _context, _executemany) -> None:
        statements.append(" ".join(statement.split()))

    event.listen(integration_db_session.bind.sync_engine, "before_cursor_execute", record_statement)
    try:
        snapshot = await LegacyDrainReadinessRepository().load_snapshot(
            integration_db_session,
            producer_freeze_at=timezone.now_utc() + timedelta(days=1),
        )
    finally:
        event.remove(integration_db_session.bind.sync_engine, "before_cursor_execute", record_statement)

    selects = [statement for statement in statements if statement.upper().startswith(("SELECT", "WITH"))]
    assert len(selects) == 1
    assert sum("statement_timeout" in statement.lower() for statement in statements) == 1
    assert sum("transaction read only" in statement.lower() for statement in statements) == 1
    assert not any(
        statement.upper().startswith(("INSERT", "UPDATE", "DELETE", "LOCK")) or "FOR UPDATE" in statement.upper()
        for statement in statements
    )
    assert all(value >= 0 for value in snapshot.counts.values())
    assert snapshot.watermarks


async def _insert_returning_id(db, statement: str, parameters: dict[str, object]) -> int:
    result = await db.execute(text(statement), parameters)
    return int(result.scalar_one())


async def _insert_external_http_outbox(
    db,
    *,
    dispatch_key: str,
    operation_domain: str,
    operation_identity: str,
    idempotency_key: str,
    payload_hash: str,
) -> int:
    target_snapshot = json.dumps({"code": "WMS_TASK4_READINESS", "url": "http://factory-wms/effects"})
    return await _insert_returning_id(
        db,
        """
        INSERT INTO wes_biz.system_outbox (
            created_at, updated_at,
            operation_domain, dispatch_type, dispatch_key, idempotency_key,
            target_type, target_code, provider_profile_identity, provider_profile_hash,
            operation_identity, binding_revision, target_snapshot_json, target_snapshot_hash,
            auth_scheme, network_trust_mode, payload_json, canonical_payload_bytes,
            payload_hash, status, attempt_count
        ) VALUES (
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
            :operation_domain, 'EXTERNAL_HTTP', :dispatch_key, :idempotency_key,
            'HTTP_ENDPOINT', 'WMS_TASK4_READINESS', 'task4.wms-effect.v1', :digest,
            :operation_identity, :digest, CAST(:target_snapshot AS json), :digest,
            'NONE', 'isolated_lan', CAST(:payload AS json), :canonical_payload,
            :payload_hash, 'SENT', 0
        ) RETURNING id
        """,
        {
            "operation_domain": operation_domain,
            "dispatch_key": dispatch_key,
            "idempotency_key": idempotency_key,
            "operation_identity": operation_identity,
            "digest": "d" * 64,
            "target_snapshot": target_snapshot,
            "payload": json.dumps({"dispatch_key": dispatch_key}),
            "canonical_payload": b"{}",
            "payload_hash": payload_hash,
        },
    )


@pytest.mark.asyncio
async def test_postgresql_snapshot_scopes_pairs_and_preserves_both_conflict_identities_without_mutation(
    integration_session_factory,
) -> None:
    repository = LegacyDrainReadinessRepository()
    freeze_at = timezone.now_utc() + timedelta(days=1)
    async with integration_session_factory() as read_db:
        before = await repository.load_snapshot(read_db, producer_freeze_at=freeze_at)
        await read_db.rollback()

    identity = uuid4().hex
    now_ms = int(timezone.now_utc().timestamp() * 1000)
    dispatch_key = f"task4-dispatch:{identity}"
    unmatched_key = f"task4-unmatched:{identity}"
    standalone_key = f"task4-station-lease:{identity}"
    row_ids: dict[str, int | tuple[int, int, int]] = {}
    try:
        async with integration_session_factory.begin() as seed_db:
            row_ids["inbox"] = await _insert_returning_id(
                seed_db,
                """
                INSERT INTO wes_runtime.runtime_inbox (
                    provider_code, event_type, source_event_id, payload_hash, kind,
                    payload_json, payload_schema_version, claim_bucket_key, received_at,
                    status, attempt_count, max_retries
                ) VALUES (
                    'TASK4_TEST', 'TASK4_TEST_EVENT', :source_event_id, :payload_hash,
                    'INTERNAL_EVENT', CAST('{}' AS json), 1, :claim_bucket_key, :received_at,
                    'RECEIVED', 0, 3
                ) RETURNING id
                """,
                {
                    "source_event_id": f"task4:{identity}",
                    "payload_hash": "a" * 64,
                    "claim_bucket_key": f"task4:{identity}",
                    "received_at": now_ms,
                },
            )
            row_ids["session"] = await _insert_returning_id(
                seed_db,
                "INSERT INTO wes_runtime.execution_sessions (workline_id, state) VALUES (:workline_id, 'RUNNING') RETURNING id",
                {"workline_id": int(identity[:8], 16) % 1_000_000_000 + 1},
            )
            row_ids["correlation"] = await _insert_returning_id(
                seed_db,
                """
                INSERT INTO wes_runtime.execution_correlations (
                    correlation_id, execution_session_id, trace_id
                ) VALUES (:correlation_id, :session_id, :trace_id) RETURNING id
                """,
                {
                    "correlation_id": f"task4-correlation:{identity}",
                    "session_id": row_ids["session"],
                    "trace_id": f"task4-trace:{identity}",
                },
            )
            row_ids["intent"] = await _insert_returning_id(
                seed_db,
                """
                INSERT INTO wes_runtime.runtime_intent_logs (
                    execution_session_id, correlation_id, provider_code, operation_kind,
                    target_domain, target_action, idempotency_key, request_hash, dispatch_key,
                    operation_identity, binding_snapshot_json, provider_snapshot_json,
                    precondition_json, payload_hash, effect_status, outcome_json,
                    outcome_history_json, status_check_count, status_resubmit_count,
                    status_binding_snapshot_json
                ) VALUES (
                    :session_id, :correlation_id, 'WMS', 'system_capability_effect',
                    'wms_integration', 'confirm_inbound', :idempotency_key, :digest, :dispatch_key,
                    'wms.inventory.confirm_inbound@v1', CAST('{}' AS json), CAST('{}' AS json),
                    CAST('{}' AS json), :digest, 'ACCEPTED', CAST('{}' AS json),
                    CAST('[]' AS json), 0, 0, CAST('{}' AS json)
                ) RETURNING id
                """,
                {
                    "session_id": row_ids["session"],
                    "correlation_id": f"task4-correlation:{identity}",
                    "idempotency_key": f"intent-idem:{identity}",
                    "digest": "b" * 64,
                    "dispatch_key": dispatch_key,
                },
            )
            paired_id = await _insert_external_http_outbox(
                seed_db,
                dispatch_key=dispatch_key,
                operation_domain="WMS",
                operation_identity="wms.inventory.confirm_outbound@v1",
                idempotency_key=f"outbox-idem:{identity}",
                payload_hash="c" * 64,
            )
            unmatched_id = await _insert_external_http_outbox(
                seed_db,
                dispatch_key=unmatched_key,
                operation_domain="WMS",
                operation_identity="wms.inventory.confirm_inbound@v1",
                idempotency_key=f"unmatched-idem:{identity}",
                payload_hash="e" * 64,
            )
            standalone_id = await _insert_external_http_outbox(
                seed_db,
                dispatch_key=standalone_key,
                operation_domain="WMS_INVENTORY",
                operation_identity="wms.inventory.confirm_inbound@v1",
                idempotency_key=f"station-lease-idem:{identity}",
                payload_hash="f" * 64,
            )
            row_ids["outboxes"] = (paired_id, unmatched_id, standalone_id)

        async with integration_session_factory() as read_db:
            observed = await repository.load_snapshot(read_db, producer_freeze_at=freeze_at)
            await read_db.rollback()

        assert observed.counts["runtime_inbox_processable"] == before.counts["runtime_inbox_processable"] + 1
        assert observed.counts["runtime_intent_active"] == before.counts["runtime_intent_active"] + 1
        assert observed.counts["system_outbox_sent_intent_accepted"] == (
            before.counts["system_outbox_sent_intent_accepted"] + 1
        )
        assert observed.counts["system_outbox_identity_digest_conflict"] == (
            before.counts["system_outbox_identity_digest_conflict"] + 1
        )
        assert observed.counts["system_outbox_unmatched_pair"] == before.counts["system_outbox_unmatched_pair"] + 1
        expected_conflict = {
            "kind": "system_outbox_identity_digest_conflict",
            "dispatch_key": dispatch_key,
            "intent_id": row_ids["intent"],
            "outbox_id": paired_id,
            "intent_operation_identity": "wms.inventory.confirm_inbound@v1",
            "outbox_operation_identity": "wms.inventory.confirm_outbound@v1",
            "intent_idempotency_key": f"intent-idem:{identity}",
            "outbox_idempotency_key": f"outbox-idem:{identity}",
            "intent_digest": "b" * 64,
            "outbox_digest": "c" * 64,
        }
        assert expected_conflict in observed.investigations
        assert any(
            item.get("kind") == "system_outbox_sent_intent_accepted" and item.get("dispatch_key") == dispatch_key
            for item in observed.investigations
        )
        assert any(item.get("dispatch_key") == unmatched_key for item in observed.investigations)
        assert not any(item.get("dispatch_key") == standalone_key for item in observed.investigations)
        assert all("payload" not in key and "action" not in key for item in observed.investigations for key in item)
    finally:
        if row_ids:
            async with integration_session_factory.begin() as cleanup_db:
                outbox_ids = row_ids.get("outboxes")
                if isinstance(outbox_ids, tuple) and len(outbox_ids) == 3:
                    await cleanup_db.execute(
                        text("DELETE FROM wes_biz.system_outbox WHERE id IN (:first, :second, :third)"),
                        {"first": outbox_ids[0], "second": outbox_ids[1], "third": outbox_ids[2]},
                    )
                await cleanup_db.execute(
                    text("DELETE FROM wes_runtime.runtime_intent_logs WHERE id = :id"),
                    {"id": row_ids.get("intent")},
                )
                await cleanup_db.execute(
                    text("DELETE FROM wes_runtime.execution_correlations WHERE id = :id"),
                    {"id": row_ids.get("correlation")},
                )
                await cleanup_db.execute(
                    text("DELETE FROM wes_runtime.execution_sessions WHERE id = :id"),
                    {"id": row_ids.get("session")},
                )
                await cleanup_db.execute(
                    text("DELETE FROM wes_runtime.runtime_inbox WHERE id = :id"),
                    {"id": row_ids.get("inbox")},
                )
