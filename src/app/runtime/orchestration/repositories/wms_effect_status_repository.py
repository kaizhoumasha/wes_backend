"""WMS EFFECT status claim 与 lease fencing 的数据库边界。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import or_, select

from src.app.effect_ledger_status import SystemOutboxStatus
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentLog, RuntimeIntentStatus
from src.app.sys.models.outbox import WMS_EFFECT_OPERATION_IDENTITIES, SystemOutbox

_CLAIMABLE_INTENT_STATUSES = (
    RuntimeIntentStatus.PROPOSED,
    RuntimeIntentStatus.ACCEPTED,
    RuntimeIntentStatus.UNKNOWN,
)
_QUERYABLE_TRANSPORT_TERMINALS = (
    SystemOutboxStatus.SENT,
    SystemOutboxStatus.UNKNOWN,
    SystemOutboxStatus.FAILED,
)


@dataclass(frozen=True, slots=True)
class WmsEffectStatusClaim:
    """已提交短事务后可带到事务外执行 HTTP 的冻结 claim 身份。"""

    intent: RuntimeIntentLog
    outbox: SystemOutbox
    lease_token: str


class WmsEffectStatusRepository:
    """只负责 status claim、关联读取与 current-token fencing。"""

    async def claim_by_dispatch_key(
        self,
        db: Any,
        *,
        dispatch_key: str,
        now: datetime,
        lease_seconds: float,
    ) -> WmsEffectStatusClaim | None:
        rows = await self._claim(
            db,
            now=now,
            lease_seconds=lease_seconds,
            limit=1,
            dispatch_key=dispatch_key,
        )
        return rows[0] if rows else None

    async def claim_due_batch(
        self,
        db: Any,
        *,
        now: datetime,
        lease_seconds: float,
        limit: int,
    ) -> tuple[WmsEffectStatusClaim, ...]:
        return await self._claim(
            db,
            now=now,
            lease_seconds=lease_seconds,
            limit=limit,
            dispatch_key=None,
        )

    async def _claim(
        self,
        db: Any,
        *,
        now: datetime,
        lease_seconds: float,
        limit: int,
        dispatch_key: str | None,
    ) -> tuple[WmsEffectStatusClaim, ...]:
        if lease_seconds <= 0 or limit <= 0:
            raise ValueError("WMS EFFECT status claim bounds must be positive")
        intent_columns = cast("Any", RuntimeIntentLog).__table__.c
        outbox_columns = cast("Any", SystemOutbox).__table__.c
        statement = (
            select(RuntimeIntentLog, SystemOutbox)
            .join(SystemOutbox, outbox_columns.dispatch_key == intent_columns.dispatch_key)
            .where(
                intent_columns.effect_status.in_(_CLAIMABLE_INTENT_STATUSES),
                intent_columns.status_binding_snapshot_hash.is_not(None),
                or_(
                    intent_columns.status_check_after.is_(None),
                    intent_columns.status_check_after <= now,
                ),
                or_(
                    intent_columns.status_check_lease_token.is_(None),
                    intent_columns.status_check_lease_until <= now,
                ),
                outbox_columns.operation_identity.in_(WMS_EFFECT_OPERATION_IDENTITIES),
                outbox_columns.status.in_(_QUERYABLE_TRANSPORT_TERMINALS),
            )
            .order_by(intent_columns.status_check_after.asc().nullsfirst(), intent_columns.id.asc())
            .limit(limit)
            .with_for_update(of=RuntimeIntentLog, skip_locked=True)
        )
        if dispatch_key is not None:
            statement = statement.where(intent_columns.dispatch_key == dispatch_key)
        result = await db.execute(statement)
        claimed: list[WmsEffectStatusClaim] = []
        lease_until = now + timedelta(seconds=lease_seconds)
        for intent, outbox in result.all():
            token = uuid4().hex
            if intent.status_check_started_at is None:
                intent.status_check_started_at = now
            intent.status_check_count = int(intent.status_check_count or 0) + 1
            intent.status_check_lease_token = token
            intent.status_check_lease_until = lease_until
            claimed.append(WmsEffectStatusClaim(intent=intent, outbox=outbox, lease_token=token))
        if claimed:
            await db.flush()
        return tuple(claimed)

    async def get_claim_for_update(
        self,
        db: Any,
        *,
        dispatch_key: str,
        lease_token: str,
    ) -> WmsEffectStatusClaim | None:
        intent_columns = cast("Any", RuntimeIntentLog).__table__.c
        outbox_columns = cast("Any", SystemOutbox).__table__.c
        result = await db.execute(
            select(RuntimeIntentLog, SystemOutbox)
            .join(SystemOutbox, outbox_columns.dispatch_key == intent_columns.dispatch_key)
            .where(
                intent_columns.dispatch_key == dispatch_key,
                intent_columns.status_check_lease_token == lease_token,
            )
            .with_for_update(of=RuntimeIntentLog)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return WmsEffectStatusClaim(intent=row[0], outbox=row[1], lease_token=lease_token)

    @staticmethod
    async def reserve_resubmit(db: Any, *, claim: WmsEffectStatusClaim) -> bool:
        intent = claim.intent
        if intent.status_check_lease_token != claim.lease_token or int(intent.status_resubmit_count or 0) != 0:
            return False
        intent.status_resubmit_count = 1
        await db.flush()
        return True

    @staticmethod
    async def release_claim(
        db: Any,
        *,
        claim: WmsEffectStatusClaim,
        status_check_after: datetime | None,
    ) -> bool:
        intent = claim.intent
        if intent.status_check_lease_token != claim.lease_token:
            return False
        intent.status_check_after = status_check_after
        intent.status_check_lease_token = None
        intent.status_check_lease_until = None
        await db.flush()
        return True


wms_effect_status_repository = WmsEffectStatusRepository()

__all__ = [
    "WmsEffectStatusClaim",
    "WmsEffectStatusRepository",
    "wms_effect_status_repository",
]
