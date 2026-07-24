"""WMS evidence 服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from src.app.wms_integration.evidence import (
    ExternalReference,
    ExternalReferenceCatalog,
    ExternalReferenceDriftKind,
)
from src.app.wms_integration.models import WMS_CALL_EVIDENCE_RETENTION_DAYS, WmsCallEvidence, WmsEvidenceStatus
from src.app.wms_integration.repositories import (
    WmsCallEvidenceArchiveRepository,
    WmsCallEvidenceRepository,
    wms_call_evidence_archive_repository,
    wms_call_evidence_repository,
)
from src.app.wms_integration.services.redaction import bounded_redacted_snapshot, canonical_sha256
from src.core.base_service import BaseService
from src.utils.timezone import timezone
from src.utils.value_normalization import require_text

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

ASYNC_SUMMARY_METADATA_KEYS = ("status", "error_code", "reason_code", "result_code")
MAX_ASYNC_METADATA_LENGTH = 240
MAX_ASYNC_PAYLOAD_KEYS = 120


@dataclass(frozen=True, slots=True)
class WmsExternalReferenceDriftItem:
    """单条 WMS evidence 外部引用漂移。"""

    evidence_key: str
    operation_name: str
    snapshot_field: str
    kind: ExternalReferenceDriftKind
    reference: ExternalReference
    expected_source_version: str | None


@dataclass(frozen=True, slots=True)
class WmsExternalReferenceDriftReport:
    """WMS evidence drift job 只读扫描结果。"""

    scanned_evidence_count: int
    scanned_reference_count: int
    drift_items: list[WmsExternalReferenceDriftItem]

    @property
    def drift_count(self) -> int:
        return len(self.drift_items)


@dataclass(frozen=True, slots=True)
class WmsEvidenceArchiveReport:
    """WMS evidence retention/archive 扫描结果。"""

    cutoff_at: datetime
    scanned_count: int
    archived_count: int
    deleted_count: int


class WmsCallEvidenceService(BaseService[WmsCallEvidence, WmsCallEvidenceRepository]):
    """WMS 调用证据服务。"""

    def __init__(
        self,
        repository: WmsCallEvidenceRepository | None = None,
        archive_repository: WmsCallEvidenceArchiveRepository | None = None,
    ) -> None:
        super().__init__(repository or wms_call_evidence_repository)
        self.archive_repo = archive_repository or wms_call_evidence_archive_repository

    async def record_sync_call(
        self,
        db: AsyncSession,
        *,
        evidence_key: str,
        provider_profile_identity: str,
        operation_name: str,
        target_code: str | None,
        status: WmsEvidenceStatus,
        request_snapshot: dict[str, Any],
        response_snapshot: dict[str, Any] | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        dispatch_key: str | None = None,
        http_status: int | None = None,
        reason_code: str | None = None,
        retryable: bool | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> WmsCallEvidence:
        """记录同步 WMS 调用 evidence，重复 evidence_key 直接复用既有记录。"""

        redacted_request = bounded_redacted_snapshot(request_snapshot)
        redacted_response = bounded_redacted_snapshot(response_snapshot or {})
        return await self._create_or_get(
            db,
            {
                "evidence_key": evidence_key,
                "provider_profile_identity": require_text(
                    provider_profile_identity,
                    "provider_profile_identity",
                ),
                "operation_name": operation_name,
                "target_code": target_code,
                "status": status,
                "request_id": request_id,
                "trace_id": trace_id,
                "dispatch_key": dispatch_key,
                "request_snapshot": redacted_request,
                "response_snapshot": redacted_response,
                "request_hash": canonical_sha256(redacted_request),
                "response_hash": canonical_sha256(redacted_response) if response_snapshot is not None else None,
                "http_status": http_status,
                "reason_code": reason_code,
                "retryable": retryable,
                "started_at": started_at or timezone.now_for_db(),
                "finished_at": finished_at,
            },
        )

    async def record_async_summary(
        self,
        db: AsyncSession,
        *,
        evidence_key: str,
        operation_name: str,
        target_code: str | None,
        status: WmsEvidenceStatus,
        dispatch_key: str | None,
        request_id: str | None,
        trace_id: str | None,
        source_ref_type: str,
        source_ref_id: str,
        summary: dict[str, Any],
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> WmsCallEvidence:
        """记录异步 outbox/callback evidence，只保存关联键和脱敏摘要。"""

        redacted_summary = _build_async_evidence_summary(summary)
        return await self._create_or_get(
            db,
            {
                "evidence_key": evidence_key,
                "operation_name": operation_name,
                "target_code": target_code,
                "status": status,
                "request_id": request_id,
                "trace_id": trace_id,
                "dispatch_key": dispatch_key,
                "source_ref_type": source_ref_type,
                "source_ref_id": source_ref_id,
                "request_snapshot": redacted_summary,
                "response_snapshot": {},
                "request_hash": canonical_sha256(redacted_summary),
                "response_hash": None,
                "started_at": started_at or timezone.now_for_db(),
                "finished_at": finished_at,
            },
        )

    async def _create_or_get(self, db: AsyncSession, data: dict[str, Any]) -> WmsCallEvidence:
        existing = await self.repo.get_by_evidence_key(db, str(data["evidence_key"]))
        if existing is not None:
            return existing

        try:
            created = await self.repo.create(db, data)
        except ValueError:
            existing = await self.repo.get_by_evidence_key(db, str(data["evidence_key"]))
            if existing is not None:
                return existing
            raise
        if created is None:
            existing = await self.repo.get_by_evidence_key(db, str(data["evidence_key"]))
            if existing is not None:
                return existing
            raise RuntimeError("创建 WMS evidence 失败")
        return created

    async def run_external_reference_drift_job(
        self,
        db: AsyncSession,
        *,
        catalog: ExternalReferenceCatalog,
        limit: int = 500,
        operation_name: str | None = None,
    ) -> WmsExternalReferenceDriftReport:
        """扫描 evidence envelope 中的 ExternalReference，并按 catalog 分类漂移。"""

        evidence_rows = await self.repo.list_recent_for_drift_scan(db, limit=limit, operation_name=operation_name)
        scanned_reference_count = 0
        drift_items: list[WmsExternalReferenceDriftItem] = []

        for evidence in evidence_rows:
            for snapshot_field, snapshot in _iter_evidence_snapshots(evidence):
                for reference in _extract_external_references(snapshot):
                    scanned_reference_count += 1
                    drift = catalog.classify(reference)
                    if drift.kind is ExternalReferenceDriftKind.NONE:
                        continue
                    drift_items.append(
                        WmsExternalReferenceDriftItem(
                            evidence_key=evidence.evidence_key,
                            operation_name=evidence.operation_name,
                            snapshot_field=snapshot_field,
                            kind=drift.kind,
                            reference=reference,
                            expected_source_version=drift.expected_source_version,
                        )
                    )

        return WmsExternalReferenceDriftReport(
            scanned_evidence_count=len(evidence_rows),
            scanned_reference_count=scanned_reference_count,
            drift_items=drift_items,
        )

    async def archive_expired_evidence(
        self,
        db: AsyncSession,
        *,
        now: datetime | None = None,
        retention_days: int = WMS_CALL_EVIDENCE_RETENTION_DAYS,
        limit: int = 500,
    ) -> WmsEvidenceArchiveReport:
        """把超过保留期且非 in-flight 的 WMS evidence 移入 archive 表。"""

        effective_now = now or timezone.now_for_db()
        cutoff_at = effective_now - timedelta(days=retention_days)
        expired_rows = await self.repo.list_expired_for_archive(db, cutoff_at=cutoff_at, limit=limit)

        archived_ids: list[int] = []
        for evidence in expired_rows:
            original_id = _require_evidence_id(evidence.id)
            existing_archive = await self.archive_repo.get_by_original_evidence_id(db, original_id)
            if existing_archive is None:
                created = await self.archive_repo.create(
                    db,
                    _archive_evidence_data(
                        evidence,
                        archived_at=effective_now,
                        retention_cutoff_at=cutoff_at,
                    ),
                )
                if created is None:
                    raise RuntimeError("创建 WMS evidence archive 失败")
            archived_ids.append(original_id)

        deleted_count = await self.repo.delete_by_ids(db, archived_ids)
        return WmsEvidenceArchiveReport(
            cutoff_at=cutoff_at,
            scanned_count=len(expired_rows),
            archived_count=len(archived_ids),
            deleted_count=deleted_count,
        )


def _build_async_evidence_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """把异步事实源 payload 收敛为可留痕摘要，避免复制完整 payload。"""

    bounded_payload = bounded_redacted_snapshot(summary)
    payload_keys = sorted(str(key) for key in summary)
    evidence_summary: dict[str, Any] = {
        "payload_hash": canonical_sha256(bounded_payload),
        "payload_key_count": len(payload_keys),
        "payload_keys": payload_keys[:MAX_ASYNC_PAYLOAD_KEYS],
        "payload_kind": type(summary).__name__,
    }
    if len(payload_keys) > MAX_ASYNC_PAYLOAD_KEYS:
        evidence_summary["payload_keys_truncated"] = len(payload_keys) - MAX_ASYNC_PAYLOAD_KEYS

    for key in ASYNC_SUMMARY_METADATA_KEYS:
        value = summary.get(key)
        if _is_scalar(value):
            evidence_summary[key] = _truncate_metadata_value(value)
    return evidence_summary


def _require_evidence_id(value: int | None) -> int:
    if value is None:
        raise ValueError("wms evidence id must not be None when archiving")
    return value


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _truncate_metadata_value(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_ASYNC_METADATA_LENGTH:
        omitted = len(value) - MAX_ASYNC_METADATA_LENGTH
        return f"{value[:MAX_ASYNC_METADATA_LENGTH]}...<truncated chars={omitted}>"
    return value


def _iter_evidence_snapshots(evidence: WmsCallEvidence) -> tuple[tuple[str, dict[str, Any]], ...]:
    snapshots: list[tuple[str, dict[str, Any]]] = []
    if isinstance(evidence.request_snapshot, dict):
        snapshots.append(("request_snapshot", evidence.request_snapshot))
    if isinstance(evidence.response_snapshot, dict):
        snapshots.append(("response_snapshot", evidence.response_snapshot))
    return tuple(snapshots)


def _extract_external_references(snapshot: dict[str, Any]) -> list[ExternalReference]:
    raw_refs = snapshot.get("external_refs")
    if not isinstance(raw_refs, list):
        return []

    references: list[ExternalReference] = []
    for raw_ref in raw_refs:
        if isinstance(raw_ref, ExternalReference):
            references.append(raw_ref)
            continue
        if not isinstance(raw_ref, dict):
            continue
        try:
            reference = ExternalReference.model_validate(raw_ref)
        except ValidationError:
            reference = None
        if reference is not None:
            references.append(reference)
    return references


def _archive_evidence_data(
    evidence: WmsCallEvidence,
    *,
    archived_at: datetime,
    retention_cutoff_at: datetime,
) -> dict[str, Any]:
    return {
        "original_evidence_id": _require_evidence_id(evidence.id),
        "evidence_key": evidence.evidence_key,
        "provider_profile_identity": evidence.provider_profile_identity,
        "operation_name": evidence.operation_name,
        "target_code": evidence.target_code,
        "status": evidence.status,
        "request_id": evidence.request_id,
        "trace_id": evidence.trace_id,
        "dispatch_key": evidence.dispatch_key,
        "source_ref_type": evidence.source_ref_type,
        "source_ref_id": evidence.source_ref_id,
        "request_snapshot": dict(evidence.request_snapshot),
        "response_snapshot": dict(evidence.response_snapshot),
        "request_hash": evidence.request_hash,
        "response_hash": evidence.response_hash,
        "http_status": evidence.http_status,
        "reason_code": evidence.reason_code,
        "retryable": evidence.retryable,
        "started_at": evidence.started_at,
        "finished_at": evidence.finished_at,
        "archived_at": archived_at,
        "retention_cutoff_at": retention_cutoff_at,
    }


wms_call_evidence_service = WmsCallEvidenceService()


__all__ = [
    "WmsCallEvidenceService",
    "WmsEvidenceArchiveReport",
    "WmsExternalReferenceDriftItem",
    "WmsExternalReferenceDriftReport",
    "wms_call_evidence_service",
]
