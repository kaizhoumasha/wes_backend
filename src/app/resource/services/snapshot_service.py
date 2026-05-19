"""资源料箱内容快照服务。"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from src.app.resource.models import BinContentSnapshot, BinContentSnapshotStatus
from src.app.resource.repositories import (
    BinContentSnapshotItemRepository,
    BinContentSnapshotRepository,
    bin_content_snapshot_item_repository,
    bin_content_snapshot_repository,
)
from src.utils.timezone import timezone

SNAPSHOT_KEY_MAX_LENGTH = 160
SNAPSHOT_KEY_DIGEST_LENGTH = 16

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


def _db_time(value: Any) -> Any:
    return timezone.to_db_datetime(value) or timezone.now_for_db()


def _bounded_key(*parts: str) -> str:
    raw = ":".join(parts)
    if len(raw) <= SNAPSHOT_KEY_MAX_LENGTH:
        return raw
    digest = sha256(raw.encode("utf-8")).hexdigest()[:SNAPSHOT_KEY_DIGEST_LENGTH]
    digest_suffix = f":{digest}"
    prefix_budget = SNAPSHOT_KEY_MAX_LENGTH - len(digest_suffix)
    prefix = raw[:prefix_budget].rstrip(":")
    return f"{prefix}:{digest}" if prefix else digest


def _stable_hash(payload: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> str:
    digest_payload = {
        "snapshot": dict(payload),
        "items": [dict(item) for item in items],
    }
    encoded = json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _require_snapshot(snapshot: BinContentSnapshot | None, *, snapshot_id: str) -> BinContentSnapshot:
    if snapshot is None:
        raise RuntimeError(f"Failed to create BinContentSnapshot: {snapshot_id}")
    return snapshot


class ResourceSnapshotService:
    """写入料箱内容过程快照。"""

    def __init__(
        self,
        *,
        snapshot_repo: BinContentSnapshotRepository = bin_content_snapshot_repository,
        snapshot_item_repo: BinContentSnapshotItemRepository = bin_content_snapshot_item_repository,
    ) -> None:
        self.snapshot_repo = snapshot_repo
        self.snapshot_item_repo = snapshot_item_repo

    async def record_empty_bin_snapshots_from_arrived_rack(
        self,
        db: AsyncSession,
        *,
        rack_code: str,
        bin_mounts: Sequence[Mapping[str, Any]],
        source_session_id: int | None,
        source_event_id: str,
        captured_at: datetime,
    ) -> list[BinContentSnapshot]:
        """空架到位后，为 4 个空料箱写入完整空快照。"""

        captured_at_for_db = _db_time(captured_at)
        snapshot_group_key = _bounded_key("EMPTY_RACK_ARRIVED", source_event_id, rack_code)
        created: list[BinContentSnapshot] = []
        for mount in bin_mounts:
            bin_code = str(mount["bin_code"])
            snapshot_payload = {
                "snapshot_id": _bounded_key("EMPTY_RACK_ARRIVED", source_event_id, rack_code, bin_code),
                "bin_code": bin_code,
                "source_session_id": source_session_id,
                "source_event_id": source_event_id,
                "captured_at": captured_at_for_db,
                "snapshot_status": BinContentSnapshotStatus.COMPLETE.value,
                "snapshot_reason": "EMPTY_RACK_ARRIVED",
                "snapshot_group_key": snapshot_group_key,
            }
            snapshot = _require_snapshot(
                await self.snapshot_repo.create(
                    db,
                    {
                        **snapshot_payload,
                        "snapshot_hash": _stable_hash(snapshot_payload, []),
                    },
                ),
                snapshot_id=str(snapshot_payload["snapshot_id"]),
            )
            created.append(snapshot)
        return created

    async def record_material_mounted_snapshot(
        self,
        db: AsyncSession,
        *,
        bin_code: str,
        bin_cell_code: str | None,
        bin_cell_index: str,
        pkg_code: str | None,
        material_code: str | None,
        lot_code: str | None,
        date_code: str | None,
        qty_snapshot: float | None,
        wms_inventory_id: str | None,
        source_session_id: int | None,
        source_event_id: str,
        captured_at: datetime,
    ) -> BinContentSnapshot:
        """OUTPUT_ARM 成功放入后，写入该料箱最新的物料占格快照。"""

        captured_at_for_db = _db_time(captured_at)
        snapshot_id = _bounded_key(
            "MATERIAL_MOUNTED",
            source_event_id,
            bin_code,
            bin_cell_index,
            pkg_code or "NO_PKG",
        )
        snapshot_payload = {
            "snapshot_id": snapshot_id,
            "bin_code": bin_code,
            "source_session_id": source_session_id,
            "source_event_id": source_event_id,
            "captured_at": captured_at_for_db,
            "snapshot_status": BinContentSnapshotStatus.COMPLETE.value,
            "snapshot_reason": "MATERIAL_MOUNTED",
            "snapshot_group_key": _bounded_key("MATERIAL_MOUNTED", source_event_id),
        }
        item_payload = {
            "snapshot_id": snapshot_id,
            "bin_cell_code": bin_cell_code,
            "bin_cell_index": bin_cell_index,
            "pkg_code": pkg_code,
            "material_code": material_code,
            "lot_code": lot_code,
            "date_code": date_code,
            "qty_snapshot": qty_snapshot,
            "wms_inventory_id": wms_inventory_id,
        }
        snapshot = _require_snapshot(
            await self.snapshot_repo.create(
                db,
                {
                    **snapshot_payload,
                    "snapshot_hash": _stable_hash(snapshot_payload, [item_payload]),
                },
            ),
            snapshot_id=snapshot_id,
        )
        _ = await self.snapshot_item_repo.create(db, item_payload)
        return snapshot


resource_snapshot_service = ResourceSnapshotService()


__all__ = ["ResourceSnapshotService", "resource_snapshot_service"]
