"""SMT 入库 handoff 应用服务。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, cast

from src.app.workline.domain.services.smt_inbound_handoff_reason import (
    SMT_INBOUND_HANDOFF_REASON_CATALOG,
    SmtInboundHandoffReasonCatalog,
    SmtInboundHandoffReasonCode,
)
from src.app.workline.domain.services.smt_usage_policy import SMT_USAGE_POLICY, SmtUsagePolicy
from src.app.workline.models.smt_inbound_handoff import (
    SmtInboundHandoffDemand,
    SmtInboundHandoffDemandStatus,
)
from src.app.workline.repositories.smt_inbound_handoff_repository import (
    SmtInboundHandoffRepository,
    smt_inbound_handoff_repository,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SmtInboundHandoffService:
    """处理粗分机 release fact 到 handoff demand 的幂等入口。"""

    def __init__(
        self,
        *,
        repository: SmtInboundHandoffRepository = smt_inbound_handoff_repository,
        usage_policy: SmtUsagePolicy = SMT_USAGE_POLICY,
        reason_catalog: SmtInboundHandoffReasonCatalog = SMT_INBOUND_HANDOFF_REASON_CATALOG,
    ) -> None:
        self.repository = repository
        self.usage_policy = usage_policy
        self.reason_catalog = reason_catalog

    async def create_or_get_from_release(
        self,
        db: AsyncSession,
        *,
        rack_release_id: str | None = None,
        single_layer_rack_code: str | None = None,
        source_workline_id: int | None = None,
        source_workline_code: str | None = None,
        release_reason_code: str | None = None,
        bin_snapshots: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        business_demand_key: str | None = None,
        station_code: str | None = None,
        **release_evidence: Any,
    ) -> SmtInboundHandoffDemand:
        """从 release fact 幂等创建或返回已有 handoff demand。"""

        snapshot_doc, snapshots = self._normalize_bin_snapshots(bin_snapshots)
        release_fact = {
            "rack_release_id": rack_release_id,
            "single_layer_rack_code": single_layer_rack_code,
            "source_workline_id": source_workline_id,
            "source_workline_code": source_workline_code,
            "release_reason_code": release_reason_code,
            "bin_snapshots": snapshots,
            "trace_id": trace_id,
            "business_demand_key": business_demand_key,
            "station_code": station_code,
            **release_evidence,
        }
        resolved_release_id = self._release_id_or_hold_key(
            rack_release_id,
            business_demand_key=business_demand_key,
            trace_id=trace_id,
            release_fact=release_fact,
        )

        existing = await self.repository.get_demand_by_release_id(db, resolved_release_id)
        if existing is not None:
            return existing

        failure_code = self._release_fact_failure_code(
            rack_release_id=rack_release_id,
            single_layer_rack_code=single_layer_rack_code,
            snapshots=snapshots,
        )
        if failure_code is None:
            failure_code = self._usage_failure_code(snapshots)
        source_items = (
            []
            if failure_code is not None
            else self._source_items_from_snapshots(
                rack_release_id=resolved_release_id,
                snapshots=snapshots,
            )
        )
        if failure_code is None and not source_items:
            failure_code = SmtInboundHandoffReasonCode.RELEASE_SNAPSHOT_INVALID.value

        demand = await self.repository.create_or_get_demand_by_release(
            db,
            self._demand_data(
                rack_release_id=resolved_release_id,
                single_layer_rack_code=single_layer_rack_code,
                source_workline_id=source_workline_id,
                source_workline_code=source_workline_code,
                release_reason_code=release_reason_code,
                snapshot_doc=snapshot_doc,
                trace_id=trace_id,
                failure_code=failure_code,
            ),
        )
        if failure_code is None and getattr(demand, "id", None) is not None:
            await self.repository.create_source_items_idempotent(
                db,
                [
                    {
                        **item,
                        "handoff_demand_id": int(cast("int", demand.id)),
                    }
                    for item in source_items
                ],
            )
        return demand

    def _demand_data(
        self,
        *,
        rack_release_id: str,
        single_layer_rack_code: str | None,
        source_workline_id: int | None,
        source_workline_code: str | None,
        release_reason_code: str | None,
        snapshot_doc: dict[str, Any],
        trace_id: str | None,
        failure_code: str | None,
    ) -> dict[str, Any]:
        reason = self.reason_catalog.get(failure_code) if failure_code is not None else None
        return {
            "demand_key": f"smt-inbound-handoff:{rack_release_id}",
            "rack_release_id": rack_release_id,
            "source_workline_id": source_workline_id,
            "source_workline_code": self._text_or_none(source_workline_code),
            "single_layer_rack_code": self._text_or_none(single_layer_rack_code) or "",
            "release_reason_code": self._text_or_none(release_reason_code),
            "bin_snapshots_json": snapshot_doc,
            "status": (
                SmtInboundHandoffDemandStatus.MANUAL_HOLD
                if failure_code is not None
                else SmtInboundHandoffDemandStatus.CREATED
            ),
            "failure_code": reason.failure_code if reason is not None else None,
            "failure_message": reason.default_message if reason is not None else None,
            "trace_id": self._text_or_none(trace_id),
        }

    def _release_fact_failure_code(
        self,
        *,
        rack_release_id: str | None,
        single_layer_rack_code: str | None,
        snapshots: list[dict[str, Any]],
    ) -> str | None:
        if self._text_or_none(rack_release_id) is None or self._text_or_none(single_layer_rack_code) is None:
            return SmtInboundHandoffReasonCode.RELEASE_FACT_MISSING.value
        if not snapshots:
            return SmtInboundHandoffReasonCode.RELEASE_SNAPSHOT_INVALID.value
        return None

    def _usage_failure_code(self, snapshots: Sequence[Mapping[str, Any]]) -> str | None:
        for snapshot in snapshots:
            result = self.usage_policy.resolve_release_bin_usage(snapshot)
            if not result.valid:
                return result.failure_code or SmtInboundHandoffReasonCode.USAGE_INVALID.value
        return None

    def _source_items_from_snapshots(
        self,
        *,
        rack_release_id: str,
        snapshots: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for snapshot_index, snapshot in enumerate(snapshots, start=1):
            bin_code = self._text_or_none(snapshot.get("bin_code") or snapshot.get("bin_id"))
            cells = snapshot.get("cells") or snapshot.get("bin_cells")
            if not isinstance(cells, Sequence) or isinstance(cells, (str, bytes)):
                continue
            for cell_index, cell in enumerate(cells, start=1):
                if not isinstance(cell, Mapping) or self._cell_is_empty(cell):
                    continue
                item = self._source_item_from_cell(
                    rack_release_id=rack_release_id,
                    bin_code=bin_code,
                    fallback_index=f"{snapshot_index}-{cell_index}",
                    cell=cell,
                )
                if item is not None:
                    items.append(item)
        return items

    def _source_item_from_cell(
        self,
        *,
        rack_release_id: str,
        bin_code: str | None,
        fallback_index: str,
        cell: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        resolved_bin_code = self._text_or_none(cell.get("bin_code") or cell.get("bin_id")) or bin_code
        bin_cell_index = self._int_or_none(cell.get("bin_cell_index") or cell.get("cell_index"))
        bin_cell_code = self._text_or_none(cell.get("bin_cell_code") or cell.get("bin_cell_location"))
        material_identity_key = self._text_or_none(cell.get("material_identity_key"))
        pkg_code = self._text_or_none(cell.get("pkg_code") or cell.get("PkgID"))
        if resolved_bin_code is None or (material_identity_key is None and pkg_code is None):
            return None
        item_identity = bin_cell_code or str(bin_cell_index or fallback_index)
        return {
            "item_key": f"{rack_release_id}:{resolved_bin_code}:{item_identity}",
            "bin_code": resolved_bin_code,
            "bin_cell_index": bin_cell_index,
            "bin_cell_code": bin_cell_code,
            "material_identity_key": material_identity_key,
            "pkg_code": pkg_code,
            "reel_thickness_mm": self._decimal_or_none(cell.get("reel_thickness_mm") or cell.get("reel_thickness")),
        }

    @staticmethod
    def _normalize_bin_snapshots(
        bin_snapshots: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if isinstance(bin_snapshots, Mapping):
            snapshot_doc = dict(bin_snapshots)
            raw_snapshots = snapshot_doc.get("bins") or snapshot_doc.get("bin_snapshots")
        else:
            snapshot_doc = {}
            raw_snapshots = bin_snapshots
        snapshots = [dict(item) for item in (raw_snapshots or []) if isinstance(item, Mapping)]
        snapshot_doc["bins"] = snapshots
        return snapshot_doc, snapshots

    @classmethod
    def _release_id_or_hold_key(
        cls,
        rack_release_id: str | None,
        *,
        business_demand_key: str | None,
        trace_id: str | None,
        release_fact: Mapping[str, Any],
    ) -> str:
        value = cls._text_or_none(rack_release_id)
        if value is not None:
            return value
        fallback = cls._text_or_none(business_demand_key) or cls._text_or_none(trace_id)
        if fallback is not None:
            return f"missing-rack-release:{fallback}"
        digest = hashlib.sha256(json.dumps(release_fact, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return f"missing-rack-release:{digest}"

    @staticmethod
    def _cell_is_empty(cell: Mapping[str, Any]) -> bool:
        status = str(cell.get("status") or cell.get("cell_status") or "").strip().upper()
        return status in {"", "EMPTY", "EMPTY_VERIFIED", "AVAILABLE"}

    @staticmethod
    def _text_or_none(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _decimal_or_none(value: Any) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return decimal if decimal.is_finite() else None


smt_inbound_handoff_service = SmtInboundHandoffService()


__all__ = [
    "SmtInboundHandoffService",
    "smt_inbound_handoff_service",
]
