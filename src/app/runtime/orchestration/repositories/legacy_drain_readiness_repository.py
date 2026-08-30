"""Legacy drain 的一次性只读数据库 snapshot owner。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import bindparam, text

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

LEGACY_DRAIN_COUNT_KEYS = (
    "runtime_inbox_processable",
    "runtime_inbox_lease",
    "runtime_inbox_dead_letter",
    "runtime_intent_active",
    "runtime_intent_ambiguous",
    "system_outbox_active",
    "system_outbox_ambiguous",
    "system_outbox_unmatched_pair",
    "system_outbox_identity_digest_conflict",
    "system_outbox_sent_intent_accepted",
    "runtime_hold_active_blocker",
    "business_runtime_hold_active_blocker",
    "ng_return_item_active",
    "reconciliation_case_open",
    "bin_cell_reservation_active",
)


@dataclass(frozen=True)
class LegacyDrainPairScope:
    operation_domain: str
    dispatch_type: str
    producer: str
    operation_identities: tuple[str, ...]

    def bind_parameters(self) -> dict[str, object]:
        return {
            "paired_operation_domain": self.operation_domain,
            "paired_dispatch_type": self.dispatch_type,
            "paired_operation_identities": self.operation_identities,
        }


LEGACY_DRAIN_PAIR_SCOPE = LegacyDrainPairScope(
    operation_domain="WMS",
    dispatch_type="EXTERNAL_HTTP",
    producer="src/app/runtime/system_capabilities/wms/effect_runtime.py:SystemCapabilityEffectRuntime.prepare",
    operation_identities=(
        "wms.inventory.reserve_inventory@v1",
        "wms.inventory.release_reservation@v1",
        "wms.inventory.confirm_inbound@v1",
        "wms.inventory.confirm_outbound@v1",
        "wms.inventory.transfer_inventory@v1",
        "wms.inventory.confirm_return_putaway@v1",
        "wms.fulfillment.notify_pkg_binding@v1",
        "wms.fulfillment.request_rack_supply@v1",
        "wms.fulfillment.change_rack_face@v1",
        "wms.fulfillment.publish_manual_task@v1",
        "wms.fulfillment.cancel_request@v1",
    ),
)


@dataclass(frozen=True)
class LegacyDrainDatabaseSnapshot:
    counts: dict[str, int]
    watermarks: dict[str, tuple[int, int | None]]
    investigations: tuple[dict[str, object], ...]


class LegacyDrainReadinessRepository:
    """以一条 PostgreSQL SELECT 读取全部 legacy drain 谓词和原身份。"""

    _STATEMENT = text(
        r"""
WITH
runtime_inbox_snapshot AS (
    SELECT
        count(*) FILTER (WHERE status IN ('RECEIVED', 'FAILED')) AS processable,
        count(*) FILTER (WHERE status = 'PROCESSING') AS lease,
        count(*) FILTER (WHERE status = 'DEAD_LETTER') AS dead_letter,
        count(*) AS row_count,
        max(id) AS max_id
    FROM wes_runtime.runtime_inbox
),
runtime_intent_snapshot AS (
    SELECT
        count(*) FILTER (WHERE effect_status IN ('PROPOSED', 'ACCEPTED')) AS active,
        count(*) FILTER (WHERE effect_status IN ('UNKNOWN', 'RECONCILING')) AS ambiguous,
        count(*) AS row_count,
        max(id) AS max_id
    FROM wes_runtime.runtime_intent_logs
),
system_outbox_snapshot AS (
    SELECT
        count(*) FILTER (WHERE status IN ('NEW', 'DISPATCHING', 'RETRY_WAIT')) AS active,
        count(*) FILTER (WHERE status = 'UNKNOWN') AS ambiguous,
        count(*) AS row_count,
        max(id) AS max_id
    FROM wes_biz.system_outbox
),
paired_intents AS (
    SELECT intent.*
    FROM wes_runtime.runtime_intent_logs AS intent
    WHERE intent.operation_kind = 'system_capability_effect'
      AND intent.operation_identity IN :paired_operation_identities
),
paired_outboxes AS (
    SELECT outbox.*
    FROM wes_biz.system_outbox AS outbox
    WHERE outbox.operation_domain = :paired_operation_domain
      AND outbox.dispatch_type = :paired_dispatch_type
      AND outbox.operation_identity IN :paired_operation_identities
),
intent_outbox_pairs AS (
    SELECT
        intent.id AS intent_id,
        outbox.id AS outbox_id,
        coalesce(intent.dispatch_key, outbox.dispatch_key) AS dispatch_key,
        intent.effect_status,
        outbox.status AS outbox_status,
        intent.operation_identity AS intent_operation_identity,
        outbox.operation_identity AS outbox_operation_identity,
        intent.idempotency_key AS intent_idempotency_key,
        outbox.idempotency_key AS outbox_idempotency_key,
        coalesce(intent.payload_hash, intent.request_hash) AS intent_digest,
        outbox.payload_hash AS outbox_digest
    FROM paired_intents AS intent
    FULL OUTER JOIN paired_outboxes AS outbox ON outbox.dispatch_key = intent.dispatch_key
),
pair_snapshot AS (
    SELECT
        count(*) FILTER (WHERE intent_id IS NULL OR outbox_id IS NULL) AS unmatched,
        count(*) FILTER (
            WHERE intent_id IS NOT NULL AND outbox_id IS NOT NULL AND (
                intent_operation_identity IS DISTINCT FROM outbox_operation_identity
                OR intent_idempotency_key IS DISTINCT FROM outbox_idempotency_key
                OR intent_digest IS DISTINCT FROM outbox_digest
            )
        ) AS identity_digest_conflict,
        count(*) FILTER (WHERE effect_status = 'ACCEPTED' AND outbox_status = 'SENT') AS sent_intent_accepted
    FROM intent_outbox_pairs
),
runtime_hold_snapshot AS (
    SELECT
        count(*) FILTER (WHERE resolved_at IS NULL) AS active,
        count(*) AS row_count,
        max(id) AS max_id
    FROM wes_runtime.runtime_holds
),
business_hold_snapshot AS (
    SELECT
        count(*) FILTER (WHERE blocking AND status IN ('OPEN', 'IN_PROGRESS', 'REOPENED')) AS active,
        count(*) AS row_count,
        max(id) AS max_id
    FROM wes_biz.runtime_holds
),
ng_return_snapshot AS (
    SELECT
        count(*) FILTER (WHERE status IN ('WAITING_REWORK', 'REWORKING')) AS active,
        count(*) AS row_count,
        max(id) AS max_id
    FROM wes_biz.ng_return_items
),
reconciliation_snapshot AS (
    SELECT
        count(*) FILTER (WHERE status = 'OPEN') AS active,
        count(*) AS row_count,
        max(id) AS max_id
    FROM wes_runtime.reconciliation_cases
),
reservation_snapshot AS (
    SELECT
        count(*) FILTER (WHERE reservation_status IN ('PLANNED', 'RECONCILING')) AS active,
        count(*) AS row_count,
        max(id) AS max_id
    FROM wes_biz.workline_bin_cell_reservations
),
manual_investigations AS (
    SELECT item
    FROM (
        SELECT jsonb_build_object(
            'kind', 'runtime_inbox_dead_letter', 'table', 'wes_runtime.runtime_inbox',
            'id', id, 'status', status, 'source_event_id', source_event_id
        ) AS item, 10 AS sort_group, id AS sort_id
        FROM wes_runtime.runtime_inbox WHERE status = 'DEAD_LETTER' ORDER BY id LIMIT 100
    ) AS inbox_items
    UNION ALL
    SELECT item FROM (
        SELECT jsonb_build_object(
            'kind', 'runtime_intent_ambiguous', 'table', 'wes_runtime.runtime_intent_logs',
            'id', id, 'status', effect_status, 'dispatch_key', dispatch_key,
            'operation_identity', operation_identity, 'idempotency_key', idempotency_key
        ) AS item, id FROM wes_runtime.runtime_intent_logs
        WHERE effect_status IN ('UNKNOWN', 'RECONCILING') ORDER BY id LIMIT 100
    ) AS intent_items
    UNION ALL
    SELECT item FROM (
        SELECT jsonb_build_object(
            'kind', 'system_outbox_ambiguous', 'table', 'wes_biz.system_outbox',
            'id', id, 'status', status, 'dispatch_key', dispatch_key,
            'operation_identity', operation_identity, 'idempotency_key', idempotency_key
        ) AS item, id FROM wes_biz.system_outbox WHERE status = 'UNKNOWN' ORDER BY id LIMIT 100
    ) AS outbox_items
    UNION ALL
    SELECT item FROM (
        SELECT jsonb_build_object(
            'kind', issue.kind,
            'dispatch_key', dispatch_key,
            'intent_id', intent_id, 'outbox_id', outbox_id,
            'intent_operation_identity', intent_operation_identity,
            'outbox_operation_identity', outbox_operation_identity,
            'intent_idempotency_key', intent_idempotency_key,
            'outbox_idempotency_key', outbox_idempotency_key,
            'intent_digest', intent_digest, 'outbox_digest', outbox_digest
        ) AS item, coalesce(intent_id, outbox_id) AS id
        FROM intent_outbox_pairs
        CROSS JOIN LATERAL (
            SELECT 'system_outbox_unmatched_pair' AS kind
            WHERE intent_id IS NULL OR outbox_id IS NULL
            UNION ALL
            SELECT 'system_outbox_identity_digest_conflict' AS kind
            WHERE intent_id IS NOT NULL AND outbox_id IS NOT NULL AND (
                intent_operation_identity IS DISTINCT FROM outbox_operation_identity
                OR intent_idempotency_key IS DISTINCT FROM outbox_idempotency_key
                OR intent_digest IS DISTINCT FROM outbox_digest
            )
            UNION ALL
            SELECT 'system_outbox_sent_intent_accepted' AS kind
            WHERE effect_status = 'ACCEPTED' AND outbox_status = 'SENT'
        ) AS issue
        ORDER BY id LIMIT 100
    ) AS pair_items
    UNION ALL
    SELECT item FROM (
        SELECT jsonb_build_object(
            'kind', 'runtime_hold_active_blocker', 'table', 'wes_runtime.runtime_holds',
            'id', id, 'scope_key', scope_key
        ) AS item, id FROM wes_runtime.runtime_holds WHERE resolved_at IS NULL ORDER BY id LIMIT 100
    ) AS runtime_hold_items
    UNION ALL
    SELECT item FROM (
        SELECT jsonb_build_object(
            'kind', 'business_runtime_hold_active_blocker', 'table', 'wes_biz.runtime_holds',
            'id', id, 'status', status
        ) AS item, id FROM wes_biz.runtime_holds
        WHERE blocking AND status IN ('OPEN', 'IN_PROGRESS', 'REOPENED') ORDER BY id LIMIT 100
    ) AS business_hold_items
    UNION ALL
    SELECT item FROM (
        SELECT jsonb_build_object(
            'kind', 'ng_return_item_active', 'table', 'wes_biz.ng_return_items',
            'id', id, 'status', status, 'material_identity_key', material_identity_key
        ) AS item, id FROM wes_biz.ng_return_items
        WHERE status IN ('WAITING_REWORK', 'REWORKING') ORDER BY id LIMIT 100
    ) AS ng_items
    UNION ALL
    SELECT item FROM (
        SELECT jsonb_build_object(
            'kind', 'reconciliation_case_open', 'table', 'wes_runtime.reconciliation_cases',
            'id', id, 'status', status, 'dispatch_key', dispatch_key
        ) AS item, id FROM wes_runtime.reconciliation_cases WHERE status = 'OPEN' ORDER BY id LIMIT 100
    ) AS reconciliation_items
    UNION ALL
    SELECT item FROM (
        SELECT jsonb_build_object(
            'kind', 'bin_cell_reservation_active', 'table', 'wes_biz.workline_bin_cell_reservations',
            'id', id, 'status', reservation_status, 'reservation_key', reservation_key
        ) AS item, id FROM wes_biz.workline_bin_cell_reservations
        WHERE reservation_status IN ('PLANNED', 'RECONCILING') ORDER BY id LIMIT 100
    ) AS reservation_items
),
investigation_snapshot AS (
    SELECT coalesce(jsonb_agg(item), '[]'::jsonb) AS items FROM manual_investigations
)
SELECT jsonb_build_object(
    'counts', jsonb_build_object(
        'runtime_inbox_processable', runtime_inbox_snapshot.processable,
        'runtime_inbox_lease', runtime_inbox_snapshot.lease,
        'runtime_inbox_dead_letter', runtime_inbox_snapshot.dead_letter,
        'runtime_intent_active', runtime_intent_snapshot.active,
        'runtime_intent_ambiguous', runtime_intent_snapshot.ambiguous,
        'system_outbox_active', system_outbox_snapshot.active,
        'system_outbox_ambiguous', system_outbox_snapshot.ambiguous,
        'system_outbox_unmatched_pair', pair_snapshot.unmatched,
        'system_outbox_identity_digest_conflict', pair_snapshot.identity_digest_conflict,
        'system_outbox_sent_intent_accepted', pair_snapshot.sent_intent_accepted,
        'runtime_hold_active_blocker', runtime_hold_snapshot.active,
        'business_runtime_hold_active_blocker', business_hold_snapshot.active,
        'ng_return_item_active', ng_return_snapshot.active,
        'reconciliation_case_open', reconciliation_snapshot.active,
        'bin_cell_reservation_active', reservation_snapshot.active
    ),
    'watermarks', jsonb_build_object(
        'runtime_inbox', jsonb_build_object('rows', runtime_inbox_snapshot.row_count, 'max_id', runtime_inbox_snapshot.max_id),
        'runtime_intent_log', jsonb_build_object('rows', runtime_intent_snapshot.row_count, 'max_id', runtime_intent_snapshot.max_id),
        'system_outbox', jsonb_build_object('rows', system_outbox_snapshot.row_count, 'max_id', system_outbox_snapshot.max_id),
        'runtime_hold', jsonb_build_object('rows', runtime_hold_snapshot.row_count, 'max_id', runtime_hold_snapshot.max_id),
        'business_runtime_hold', jsonb_build_object('rows', business_hold_snapshot.row_count, 'max_id', business_hold_snapshot.max_id),
        'ng_return_item', jsonb_build_object('rows', ng_return_snapshot.row_count, 'max_id', ng_return_snapshot.max_id),
        'reconciliation_case', jsonb_build_object('rows', reconciliation_snapshot.row_count, 'max_id', reconciliation_snapshot.max_id),
        'bin_cell_reservation', jsonb_build_object('rows', reservation_snapshot.row_count, 'max_id', reservation_snapshot.max_id)
    ),
    'investigations', investigation_snapshot.items
)
FROM runtime_inbox_snapshot
CROSS JOIN runtime_intent_snapshot
CROSS JOIN system_outbox_snapshot
CROSS JOIN pair_snapshot
CROSS JOIN runtime_hold_snapshot
CROSS JOIN business_hold_snapshot
CROSS JOIN ng_return_snapshot
CROSS JOIN reconciliation_snapshot
CROSS JOIN reservation_snapshot
CROSS JOIN investigation_snapshot
"""
    ).bindparams(bindparam("paired_operation_identities", expanding=True))

    async def load_snapshot(
        self,
        db: AsyncSession,
        *,
        producer_freeze_at: datetime,
    ) -> LegacyDrainDatabaseSnapshot:
        if producer_freeze_at.tzinfo is None or producer_freeze_at.utcoffset() is None:
            raise ValueError("producer_freeze_at must be timezone-aware")
        await db.execute(text("SET TRANSACTION READ ONLY"))
        await db.execute(text("SET LOCAL statement_timeout = '10s'"))
        payload = (await db.execute(self._STATEMENT, LEGACY_DRAIN_PAIR_SCOPE.bind_parameters())).scalar_one()
        if not isinstance(payload, dict):
            raise TypeError("legacy drain query returned invalid payload")
        raw_counts = payload.get("counts")
        raw_watermarks = payload.get("watermarks")
        raw_investigations = payload.get("investigations")
        if not isinstance(raw_counts, dict) or set(raw_counts) != set(LEGACY_DRAIN_COUNT_KEYS):
            raise ValueError("legacy drain query returned invalid counts")
        if not isinstance(raw_watermarks, dict) or not isinstance(raw_investigations, list):
            raise TypeError("legacy drain query returned invalid snapshot")
        counts = {key: int(raw_counts[key]) for key in LEGACY_DRAIN_COUNT_KEYS}
        watermarks: dict[str, tuple[int, int | None]] = {}
        for table_name, raw_watermark in raw_watermarks.items():
            if not isinstance(table_name, str) or not isinstance(raw_watermark, dict):
                raise TypeError("legacy drain query returned invalid watermark")
            row_count = raw_watermark.get("rows")
            max_id = raw_watermark.get("max_id")
            if not isinstance(row_count, int) or (max_id is not None and not isinstance(max_id, int)):
                raise ValueError("legacy drain query returned invalid watermark")
            watermarks[table_name] = (row_count, max_id)
        investigations: list[dict[str, object]] = []
        for raw_item in raw_investigations:
            if not isinstance(raw_item, dict):
                raise TypeError("legacy drain query returned invalid investigation")
            investigations.append({str(key): value for key, value in raw_item.items() if value is not None})
        return LegacyDrainDatabaseSnapshot(
            counts=counts,
            watermarks=watermarks,
            investigations=tuple(investigations),
        )


__all__ = [
    "LEGACY_DRAIN_COUNT_KEYS",
    "LEGACY_DRAIN_PAIR_SCOPE",
    "LegacyDrainDatabaseSnapshot",
    "LegacyDrainPairScope",
    "LegacyDrainReadinessRepository",
]
