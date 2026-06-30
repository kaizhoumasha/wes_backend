"""BinTransitMembership 队列投影服务。"""

from __future__ import annotations

from typing import Any

from src.app.handling.models.bin_transit_membership import (
    BinTransitMembership,
    BinTransitMembershipStatus,
    BinTransitQueue,
)
from src.app.handling.repositories.bin_transit_membership_repository import (
    BinTransitMembershipRepository,
    bin_transit_membership_repository,
)
from src.app.runtime.orchestration.models.object_transition_event import ObjectTransitionDomain
from src.app.runtime.orchestration.services.inbox.object_transition_event_service import (
    ObjectTransitionEventService,
    object_transition_event_service,
)
from src.core.base_service import BaseService
from src.utils.timezone import timezone


class BinTransitMembershipService(BaseService[BinTransitMembership, BinTransitMembershipRepository]):
    """维护料箱流水线队列 active/history membership。"""

    def __init__(
        self,
        *,
        repository: BinTransitMembershipRepository = bin_transit_membership_repository,
        transition_service: ObjectTransitionEventService = object_transition_event_service,
    ) -> None:
        super().__init__(repository, enable_cache=False)
        self.transition_service = transition_service

    async def enter_queue(
        self,
        db: Any,
        *,
        queue: BinTransitQueue | str,
        bin_code: str | None = None,
        placeholder_key: str | None = None,
        workline_id: int | None = None,
        workline_code: str | None = None,
        workline_session_id: int | None = None,
        handling_operation_id: int | None = None,
        handling_move_id: int | None = None,
        trace_id: str | None = None,
        reason_code: str = "QUEUE_ENTERED",
        source_event_id: str,
        evidence_json: dict[str, Any] | None = None,
        auto_commit: bool = True,
    ) -> BinTransitMembership:
        """进入队列；同一 key 重放同队列时复用 active membership。"""

        resolved_queue = _queue(queue)
        object_key = _object_key(bin_code=bin_code, placeholder_key=placeholder_key)
        active = await self._get_active(db, bin_code=bin_code, placeholder_key=placeholder_key)
        if active is not None:
            if _queue_value(getattr(active, "current_queue", None)) == resolved_queue.value:
                return active
            raise ValueError(f"{object_key} 已 active 于队列 {_queue_value(getattr(active, 'current_queue', None))}")

        membership = await self.repo.create_without_session_rollback(
            db,
            {
                "bin_code": _optional_text(bin_code),
                "placeholder_key": _optional_text(placeholder_key),
                "workline_id": workline_id,
                "workline_code": _optional_text(workline_code),
                "current_queue": resolved_queue,
                "membership_status": BinTransitMembershipStatus.ACTIVE,
                "handling_operation_id": handling_operation_id,
                "handling_move_id": handling_move_id,
                "trace_id": _optional_text(trace_id),
                "workline_session_id": workline_session_id,
                "entered_at": timezone.now_for_db(),
                "evidence_json": dict(evidence_json or {}),
            },
        )
        await self._record_transition(
            db,
            object_key=object_key,
            from_state=None,
            to_state=resolved_queue.value,
            reason_code=reason_code,
            source_event_id=source_event_id,
            handling_operation_id=handling_operation_id,
            handling_move_id=handling_move_id,
            workline_session_id=workline_session_id,
            trace_id=trace_id,
            evidence_json=evidence_json,
        )
        await self._finish(db, membership, auto_commit=auto_commit)
        return membership

    async def switch_queue(
        self,
        db: Any,
        *,
        to_queue: BinTransitQueue | str,
        bin_code: str | None = None,
        placeholder_key: str | None = None,
        workline_id: int | None = None,
        workline_code: str | None = None,
        workline_session_id: int | None = None,
        handling_operation_id: int | None = None,
        handling_move_id: int | None = None,
        trace_id: str | None = None,
        reason_code: str = "QUEUE_SWITCHED",
        source_event_id: str,
        evidence_json: dict[str, Any] | None = None,
        auto_commit: bool = True,
    ) -> BinTransitMembership:
        """切换队列；关闭旧 active membership 并打开新 membership。"""

        resolved_queue = _queue(to_queue)
        object_key = _object_key(bin_code=bin_code, placeholder_key=placeholder_key)
        active = await self._get_active(db, bin_code=bin_code, placeholder_key=placeholder_key)
        if active is None:
            return await self.enter_queue(
                db,
                bin_code=bin_code,
                placeholder_key=placeholder_key,
                queue=resolved_queue,
                workline_id=workline_id,
                workline_code=workline_code,
                workline_session_id=workline_session_id,
                handling_operation_id=handling_operation_id,
                handling_move_id=handling_move_id,
                trace_id=trace_id,
                reason_code=reason_code,
                source_event_id=source_event_id,
                evidence_json=evidence_json,
                auto_commit=auto_commit,
            )

        from_state = _queue_value(getattr(active, "current_queue", None))
        if from_state == resolved_queue.value:
            return active

        now = timezone.now_for_db()
        active.membership_status = BinTransitMembershipStatus.LEFT
        active.left_at = now
        db.add(active)
        await db.flush()

        new_membership = await self.repo.create_without_session_rollback(
            db,
            {
                "bin_code": _optional_text(bin_code) or getattr(active, "bin_code", None),
                "placeholder_key": _optional_text(placeholder_key) or getattr(active, "placeholder_key", None),
                "workline_id": workline_id if workline_id is not None else getattr(active, "workline_id", None),
                "workline_code": _optional_text(workline_code) or getattr(active, "workline_code", None),
                "current_queue": resolved_queue,
                "membership_status": BinTransitMembershipStatus.ACTIVE,
                "handling_operation_id": handling_operation_id,
                "handling_move_id": handling_move_id,
                "trace_id": _optional_text(trace_id) or getattr(active, "trace_id", None),
                "workline_session_id": (
                    workline_session_id
                    if workline_session_id is not None
                    else getattr(active, "workline_session_id", None)
                ),
                "entered_at": now,
                "evidence_json": dict(evidence_json or {}),
            },
        )
        await self._record_transition(
            db,
            object_key=object_key,
            from_state=from_state,
            to_state=resolved_queue.value,
            reason_code=reason_code,
            source_event_id=source_event_id,
            handling_operation_id=handling_operation_id,
            handling_move_id=handling_move_id,
            workline_session_id=workline_session_id,
            trace_id=trace_id,
            evidence_json=evidence_json,
        )
        await self._finish(db, new_membership, auto_commit=auto_commit)
        return new_membership

    async def leave_queue(
        self,
        db: Any,
        *,
        bin_code: str | None = None,
        placeholder_key: str | None = None,
        handling_operation_id: int | None = None,
        handling_move_id: int | None = None,
        trace_id: str | None = None,
        reason_code: str = "QUEUE_LEFT",
        source_event_id: str,
        evidence_json: dict[str, Any] | None = None,
        auto_commit: bool = True,
        ignore_missing: bool = False,
    ) -> BinTransitMembership | None:
        """离开当前队列并保留 history 记录。"""

        object_key = _object_key(bin_code=bin_code, placeholder_key=placeholder_key)
        active = await self._get_active(db, bin_code=bin_code, placeholder_key=placeholder_key)
        if active is None:
            if ignore_missing:
                return None
            raise ValueError(f"{object_key} 没有 active membership")

        from_state = _queue_value(getattr(active, "current_queue", None))
        active.membership_status = BinTransitMembershipStatus.LEFT
        active.left_at = timezone.now_for_db()
        active.evidence_json = _merged_evidence(active, evidence_json)
        db.add(active)
        await db.flush()
        await self._record_transition(
            db,
            object_key=object_key,
            from_state=from_state,
            to_state=BinTransitMembershipStatus.LEFT.value,
            reason_code=reason_code,
            source_event_id=source_event_id,
            handling_operation_id=handling_operation_id,
            handling_move_id=handling_move_id,
            workline_session_id=getattr(active, "workline_session_id", None),
            trace_id=trace_id or getattr(active, "trace_id", None),
            evidence_json=evidence_json,
        )
        await self._finish(db, active, auto_commit=auto_commit)
        return active

    async def resolve_placeholder(
        self,
        db: Any,
        *,
        placeholder_key: str,
        bin_code: str,
        handling_operation_id: int | None = None,
        handling_move_id: int | None = None,
        trace_id: str | None = None,
        reason_code: str = "PLACEHOLDER_RESOLVED",
        source_event_id: str,
        evidence_json: dict[str, Any] | None = None,
        auto_commit: bool = True,
    ) -> BinTransitMembership:
        """将 active placeholder 解析为真实 bin，冲突时转入 RECONCILING。"""

        placeholder_key = _required_text(placeholder_key, "placeholder_key")
        bin_code = _required_text(bin_code, "bin_code")
        placeholder = await self.repo.get_active_by_placeholder_key(db, placeholder_key)
        if placeholder is None:
            resolved = await self.repo.get_active_by_bin_code(db, bin_code)
            if (
                resolved is not None
                and dict(getattr(resolved, "evidence_json", None) or {}).get("resolved_from_placeholder_key")
                == placeholder_key
            ):
                return resolved
            raise ValueError(f"{placeholder_key} 没有 active placeholder membership")

        from_state = _queue_value(getattr(placeholder, "current_queue", None))
        existing_bin = await self.repo.get_active_by_bin_code(db, bin_code)
        if existing_bin is not None and getattr(existing_bin, "id", None) != getattr(placeholder, "id", None):
            placeholder.membership_status = BinTransitMembershipStatus.RECONCILING
            placeholder.evidence_json = _merged_evidence(
                placeholder,
                {
                    **dict(evidence_json or {}),
                    "conflicting_bin_code": bin_code,
                },
            )
            db.add(placeholder)
            await db.flush()
            await self._record_transition(
                db,
                object_key=placeholder_key,
                from_state=from_state,
                to_state=BinTransitMembershipStatus.RECONCILING.value,
                reason_code=reason_code,
                source_event_id=source_event_id,
                handling_operation_id=handling_operation_id,
                handling_move_id=handling_move_id,
                workline_session_id=getattr(placeholder, "workline_session_id", None),
                trace_id=trace_id or getattr(placeholder, "trace_id", None),
                evidence_json=placeholder.evidence_json,
            )
            await self._finish(db, placeholder, auto_commit=auto_commit)
            return placeholder

        placeholder.bin_code = bin_code
        placeholder.placeholder_key = None
        placeholder.handling_operation_id = handling_operation_id
        placeholder.handling_move_id = handling_move_id
        if trace_id is not None:
            placeholder.trace_id = trace_id
        placeholder.evidence_json = _merged_evidence(
            placeholder,
            {
                **dict(evidence_json or {}),
                "resolved_from_placeholder_key": placeholder_key,
            },
        )
        db.add(placeholder)
        await db.flush()
        await self._record_transition(
            db,
            object_key=bin_code,
            from_state=from_state,
            to_state=from_state,
            reason_code=reason_code,
            source_event_id=source_event_id,
            handling_operation_id=handling_operation_id,
            handling_move_id=handling_move_id,
            workline_session_id=getattr(placeholder, "workline_session_id", None),
            trace_id=trace_id or getattr(placeholder, "trace_id", None),
            evidence_json={
                **dict(evidence_json or {}),
                "previous_object_key": placeholder_key,
            },
        )
        await self._finish(db, placeholder, auto_commit=auto_commit)
        return placeholder

    async def mark_reconciling(
        self,
        db: Any,
        *,
        bin_code: str | None = None,
        placeholder_key: str | None = None,
        handling_operation_id: int | None = None,
        handling_move_id: int | None = None,
        trace_id: str | None = None,
        reason_code: str = "QUEUE_MEMBERSHIP_RECONCILING",
        source_event_id: str,
        evidence_json: dict[str, Any] | None = None,
        auto_commit: bool = True,
        ignore_missing: bool = False,
    ) -> BinTransitMembership | None:
        """将 active membership 标记为 RECONCILING。"""

        object_key = _object_key(bin_code=bin_code, placeholder_key=placeholder_key)
        active = await self._get_active(db, bin_code=bin_code, placeholder_key=placeholder_key)
        if active is None:
            if ignore_missing:
                return None
            raise ValueError(f"{object_key} 没有 active membership")
        from_state = _queue_value(getattr(active, "current_queue", None))
        active.membership_status = BinTransitMembershipStatus.RECONCILING
        active.evidence_json = _merged_evidence(active, evidence_json)
        db.add(active)
        await db.flush()
        await self._record_transition(
            db,
            object_key=object_key,
            from_state=from_state,
            to_state=BinTransitMembershipStatus.RECONCILING.value,
            reason_code=reason_code,
            source_event_id=source_event_id,
            handling_operation_id=handling_operation_id,
            handling_move_id=handling_move_id,
            workline_session_id=getattr(active, "workline_session_id", None),
            trace_id=trace_id or getattr(active, "trace_id", None),
            evidence_json=evidence_json,
        )
        await self._finish(db, active, auto_commit=auto_commit)
        return active

    async def _get_active(
        self,
        db: Any,
        *,
        bin_code: str | None,
        placeholder_key: str | None,
    ) -> BinTransitMembership | None:
        bin_code = _optional_text(bin_code)
        placeholder_key = _optional_text(placeholder_key)
        if bin_code is not None:
            return await self.repo.get_active_by_bin_code(db, bin_code)
        if placeholder_key is not None:
            return await self.repo.get_active_by_placeholder_key(db, placeholder_key)
        raise ValueError("bin_code 或 placeholder_key 至少需要一个")

    async def _record_transition(
        self,
        db: Any,
        *,
        object_key: str,
        from_state: str | None,
        to_state: str,
        reason_code: str,
        source_event_id: str,
        handling_operation_id: int | None,
        handling_move_id: int | None,
        workline_session_id: int | None,
        trace_id: str | None,
        evidence_json: dict[str, Any] | None,
    ) -> None:
        source_ref_json = {
            key: value
            for key, value in {
                "handling_operation_id": handling_operation_id,
                "handling_move_id": handling_move_id,
            }.items()
            if value is not None
        }
        await self.transition_service.record_transition(
            db,
            domain=ObjectTransitionDomain.HANDLING,
            object_type="BIN_TRANSIT",
            object_key=object_key,
            projection_type="QUEUE_MEMBERSHIP",
            from_state=from_state,
            to_state=to_state,
            reason_code=reason_code,
            source_event_id=source_event_id,
            source_ref_json=source_ref_json,
            evidence_json=dict(evidence_json or {}),
            workline_session_id=workline_session_id,
            trace_id=trace_id,
            auto_commit=False,
        )

    async def _finish(self, db: Any, membership: BinTransitMembership, *, auto_commit: bool) -> None:
        await db.refresh(membership)
        if auto_commit:
            await self._commit_mutation(db)


def _queue(value: BinTransitQueue | str) -> BinTransitQueue:
    if isinstance(value, BinTransitQueue):
        return value
    try:
        return BinTransitQueue(str(value))
    except ValueError as exc:
        raise ValueError(f"未知 BinTransitQueue: {value}") from exc


def _queue_value(value: Any) -> str:
    return _queue(value).value


def _object_key(*, bin_code: str | None, placeholder_key: str | None) -> str:
    bin_code = _optional_text(bin_code)
    placeholder_key = _optional_text(placeholder_key)
    if bin_code is not None:
        return bin_code
    if placeholder_key is not None:
        return placeholder_key
    raise ValueError("bin_code 或 placeholder_key 至少需要一个")


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _merged_evidence(membership: BinTransitMembership, evidence_json: dict[str, Any] | None) -> dict[str, Any]:
    return {
        **dict(getattr(membership, "evidence_json", None) or {}),
        **dict(evidence_json or {}),
    }


bin_transit_membership_service = BinTransitMembershipService()


__all__ = [
    "BinTransitMembershipService",
    "bin_transit_membership_service",
]
