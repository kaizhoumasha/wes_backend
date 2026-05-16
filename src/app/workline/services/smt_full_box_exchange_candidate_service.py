"""SMT 满箱交换候选释放服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, TypedDict

from src.app.resource.models import RackReleaseStatus
from src.app.resource.repositories import rack_release_bin_snapshot_repository, rack_release_repository
from src.app.workline.services.inbox_service import DuplicateInboxError, WorklineInboxService, inbox_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SmtFullBoxExchangeCandidateStatus(str, Enum):
    """SMT 满箱交换候选扫描结果。"""

    INBOX_CREATED = "INBOX_CREATED"
    ALREADY_LINKED = "ALREADY_LINKED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class SmtFullBoxExchangeCandidateResult:
    """SMT 满箱交换候选扫描结果。"""

    status: SmtFullBoxExchangeCandidateStatus
    rack_release_id: str
    inbox: Any | None = None
    reason_code: str | None = None
    message: str | None = None


class SmtFullBoxExchangeCandidateScanResult(TypedDict):
    """SMT 满箱交换候选批量扫描统计。"""

    scanned: int
    inbox_created: int
    already_linked: int
    skipped: int
    errors: int


class SmtFullBoxExchangeCandidateService:
    """从单层货架释放事实派生满箱交换插件入口 Inbox。"""

    EVENT_TYPE = "SINGLE_LAYER_RACK_RELEASED"
    EVENT_ID_PREFIX = "smt-full-box-exchange"
    EXPECTED_BIN_COUNT = 4

    def __init__(
        self,
        *,
        rack_release_repo: Any = rack_release_repository,
        rack_release_snapshot_repo: Any = rack_release_bin_snapshot_repository,
        inbox_service: WorklineInboxService = inbox_service,
    ) -> None:
        self.rack_release_repo = rack_release_repo
        self.rack_release_snapshot_repo = rack_release_snapshot_repo
        self.inbox_service = inbox_service

    async def create_inbox_for_release(
        self,
        db: AsyncSession,
        *,
        rack_release_id: str,
        source_device_code: str,
        expected_bin_count: int = EXPECTED_BIN_COUNT,
        auto_commit: bool = True,
    ) -> SmtFullBoxExchangeCandidateResult:
        """为已完成且已移出的单层货架释放创建满箱交换入口 Inbox。"""

        release = await self.rack_release_repo.get_by_release_id(db, rack_release_id)
        if release is None:
            return self._skipped(rack_release_id, "RACK_RELEASE_NOT_FOUND", "未找到单层货架释放事实")

        existing_inbox_id = _optional_int(getattr(release, "inbox_id", None))
        if existing_inbox_id is not None:
            return SmtFullBoxExchangeCandidateResult(
                status=SmtFullBoxExchangeCandidateStatus.ALREADY_LINKED,
                rack_release_id=rack_release_id,
                reason_code="RACK_RELEASE_ALREADY_HAS_INBOX",
                message="释放周期已关联 WorklineInbox",
            )

        release_status = _enum_value(getattr(release, "release_status", None))
        if release_status != RackReleaseStatus.CANDIDATE.value:
            return self._skipped(rack_release_id, "RACK_RELEASE_STATUS_NOT_CANDIDATE", "释放周期状态不可派生 Inbox")

        if getattr(release, "moved_out_at", None) is None:
            return self._skipped(rack_release_id, "RACK_NOT_MOVED_OUT", "单层货架尚未离开粗分机")

        snapshots = await self.rack_release_snapshot_repo.list_by_release_id(db, rack_release_id)
        snapshot_validation = _validate_release_snapshots(snapshots, expected_bin_count=expected_bin_count)
        if snapshot_validation is not None:
            return self._skipped(rack_release_id, snapshot_validation[0], snapshot_validation[1])

        event_id = f"{self.EVENT_ID_PREFIX}:{rack_release_id}"
        data = _release_payload(release, snapshots)
        try:
            created_inbox = await self.inbox_service.create_device_event_inbox(
                db=db,
                device_code=source_device_code,
                event_type=self.EVENT_TYPE,
                canonical_event_type=self.EVENT_TYPE,
                timestamp=_timestamp_ms(getattr(release, "released_at", None)),
                data=data,
                source_message_id=_optional_text(getattr(release, "source_event_id", None)),
                trace_id=_optional_text(getattr(release, "trace_id", None)),
                event_id=event_id,
                causation_id=_optional_text(getattr(release, "source_event_id", None)),
                auto_commit=False,
            )
        except DuplicateInboxError as exc:
            created_inbox = exc.existing_inbox

        await self._mark_release_inbox_created(
            db,
            release=release,
            inbox_id=created_inbox.id,
            auto_commit=auto_commit,
        )
        return SmtFullBoxExchangeCandidateResult(
            status=SmtFullBoxExchangeCandidateStatus.INBOX_CREATED,
            rack_release_id=rack_release_id,
            inbox=created_inbox,
        )

    async def scan_candidates(
        self,
        db: AsyncSession,
        *,
        source_device_code: str,
        limit: int = 50,
        auto_commit: bool = True,
    ) -> SmtFullBoxExchangeCandidateScanResult:
        """批量扫描释放候选并派生满箱交换入口 Inbox。"""

        candidates = await self.rack_release_repo.list_full_box_exchange_candidates(db, limit=limit)
        result: SmtFullBoxExchangeCandidateScanResult = {
            "scanned": len(candidates),
            "inbox_created": 0,
            "already_linked": 0,
            "skipped": 0,
            "errors": 0,
        }
        for release in candidates:
            rack_release_id = _optional_text(getattr(release, "rack_release_id", None))
            if rack_release_id is None:
                result["errors"] += 1
                continue
            try:
                candidate_result = await self.create_inbox_for_release(
                    db,
                    rack_release_id=rack_release_id,
                    source_device_code=source_device_code,
                    auto_commit=False,
                )
            except Exception:
                result["errors"] += 1
                continue

            if candidate_result.status == SmtFullBoxExchangeCandidateStatus.INBOX_CREATED:
                result["inbox_created"] += 1
            elif candidate_result.status == SmtFullBoxExchangeCandidateStatus.ALREADY_LINKED:
                result["already_linked"] += 1
            elif candidate_result.status == SmtFullBoxExchangeCandidateStatus.SKIPPED:
                result["skipped"] += 1

        if auto_commit:
            await db.commit()
        return result

    async def _mark_release_inbox_created(
        self,
        db: AsyncSession,
        *,
        release: Any,
        inbox_id: int,
        auto_commit: bool,
    ) -> None:
        update_payload: dict[str, Any] = {
            "inbox_id": inbox_id,
            "release_status": RackReleaseStatus.INBOX_CREATED.value,
        }
        version = getattr(release, "version", None)
        if version is not None:
            update_payload["version"] = version

        await self.rack_release_repo.update(db, release.id, update_payload)
        if auto_commit:
            await db.commit()

    def _skipped(self, rack_release_id: str, reason_code: str, message: str) -> SmtFullBoxExchangeCandidateResult:
        return SmtFullBoxExchangeCandidateResult(
            status=SmtFullBoxExchangeCandidateStatus.SKIPPED,
            rack_release_id=rack_release_id,
            reason_code=reason_code,
            message=message,
        )


def _release_payload(release: Any, snapshots: list[Any]) -> dict[str, Any]:
    return {
        "rack_release_id": str(release.rack_release_id),
        "single_layer_rack_code": str(release.single_layer_rack_code),
        "source_classifier_line_code": _optional_text(getattr(release, "source_classifier_line_code", None)),
        "source_task_batch_id": _optional_text(getattr(release, "source_task_batch_id", None)),
        "release_cycle_seq": getattr(release, "release_cycle_seq", None),
        "released_at": _isoformat_utc(getattr(release, "released_at", None)),
        "moved_out_at": _isoformat_utc(getattr(release, "moved_out_at", None)),
        "snapshot_hash": str(release.snapshot_hash),
        "bin_snapshots": [_snapshot_payload(snapshot) for snapshot in sorted(snapshots, key=_slot_sort_key)],
    }


def _snapshot_payload(snapshot: Any) -> dict[str, Any]:
    return {
        "slot_code": str(snapshot.slot_code),
        "bin_code": str(snapshot.bin_code),
        "bin_type_code": _optional_text(getattr(snapshot, "bin_type_code", None)),
        "bin_execution_status": _enum_value(getattr(snapshot, "bin_execution_status", None)),
        "usage_snapshot": getattr(snapshot, "usage_snapshot", None),
        "material_summary_json": dict(getattr(snapshot, "material_summary_json", None) or {}),
        "wms_inventory_refs_json": dict(getattr(snapshot, "wms_inventory_refs_json", None) or {}),
        "snapshot_id": _optional_text(getattr(snapshot, "snapshot_id", None)),
        "content_snapshot_hash": _optional_text(getattr(snapshot, "content_snapshot_hash", None)),
    }


def _validate_release_snapshots(snapshots: list[Any], *, expected_bin_count: int) -> tuple[str, str] | None:
    if len(snapshots) != expected_bin_count:
        return "RACK_RELEASE_SNAPSHOT_INCOMPLETE", "单层货架释放快照不是 4 箱"

    slot_codes = [_optional_text(getattr(snapshot, "slot_code", None)) for snapshot in snapshots]
    if any(slot_code is None for slot_code in slot_codes):
        return "RACK_RELEASE_SLOT_MISSING", "单层货架释放快照缺少槽位编码"
    if len(set(slot_codes)) != len(slot_codes):
        return "RACK_RELEASE_SLOT_DUPLICATED", "单层货架释放快照存在重复槽位"

    bin_codes = [_optional_text(getattr(snapshot, "bin_code", None)) for snapshot in snapshots]
    if any(bin_code is None for bin_code in bin_codes):
        return "RACK_RELEASE_BIN_MISSING", "单层货架释放快照缺少料箱编码"
    return None


def _timestamp_ms(value: Any) -> int:
    if isinstance(value, datetime):
        return int(_as_aware_utc(value).timestamp() * 1000)
    return int(datetime.now(UTC).timestamp() * 1000)


def _isoformat_utc(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    return _as_aware_utc(value).isoformat()


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _slot_sort_key(snapshot: Any) -> str:
    return str(getattr(snapshot, "slot_code", ""))


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _enum_value(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    return str(raw)


smt_full_box_exchange_candidate_service = SmtFullBoxExchangeCandidateService()


__all__ = [
    "SmtFullBoxExchangeCandidateResult",
    "SmtFullBoxExchangeCandidateScanResult",
    "SmtFullBoxExchangeCandidateService",
    "SmtFullBoxExchangeCandidateStatus",
    "smt_full_box_exchange_candidate_service",
]
