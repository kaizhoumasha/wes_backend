"""Trace resource view projection helpers."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from typing import Any

from src.app.workline.models.runtime import (
    RuntimeActiveBinRackBinView,
    RuntimeActiveBinRackCellView,
    RuntimeActiveBinRackView,
    RuntimeTraceResourceView,
)
from src.app.workline.utils import ensure_dict
from src.utils.timezone import timezone

_RACK_FIELDS = ("rack_id", "rack_code", "rack_kind", "rack_type")
_BIN_FIELDS = (
    "rack_slot_code",
    "rack_slot_location_code",
    "bin_id",
    "bin_code",
    "bin_type",
    "bin_orientation_code",
)
_CELL_FIELDS = (
    "bin_cell_index",
    "bin_cell_code",
    "bin_cell_location",
    "status",
    "capacity_depth_mm",
    "used_depth_mm",
    "material_identity_key",
    "pkg_code",
    "is_reserved",
)


def build_trace_resource_view(result: Any) -> RuntimeTraceResourceView:
    """从 Trace 历史 payload 投影资源视图，不访问实时资源快照。"""

    racks: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for active_bin_rack in _iter_active_bin_rack_payloads(result):
        _merge_active_bin_rack(racks, active_bin_rack)

    return RuntimeTraceResourceView(
        active_bin_racks=[
            RuntimeActiveBinRackView(
                **rack["fields"],
                bins=[
                    RuntimeActiveBinRackBinView(
                        **bin_item["fields"],
                        cells=[
                            RuntimeActiveBinRackCellView(**cell_fields) for cell_fields in bin_item["cells"].values()
                        ],
                    )
                    for bin_item in rack["bins"].values()
                ],
            )
            for rack in racks.values()
        ]
    )


def _iter_active_bin_rack_payloads(result: Any) -> list[dict[str, Any]]:
    payloads: list[tuple[tuple[int, float, float, int], dict[str, Any]]] = []
    discovery_index = 0
    for source, attr, time_attrs in (
        ("sessions", "context_json", ("last_ingress_at", "updated_at", "started_at", "created_at")),
        ("inboxes", "payload_json", ("received_at", "created_at")),
        ("outboxes", "payload_json", ("created_at", "updated_at")),
        ("timelines", "payload_json", ("occurred_at", "created_at")),
    ):
        for item in getattr(result, source, []) or []:
            payload = ensure_dict(getattr(item, attr, None))
            active_bin_rack = ensure_dict(payload.get("active_bin_rack"))
            if active_bin_rack:
                payloads.append((_payload_sort_key(item, discovery_index, time_attrs), active_bin_rack))
                discovery_index += 1
    return [payload for _, payload in sorted(payloads, key=lambda item: item[0])]


def _payload_sort_key(
    item: Any,
    discovery_index: int,
    time_attrs: tuple[str, ...],
) -> tuple[int, float, float, int]:
    seq_no = getattr(item, "seq_no", None)
    timestamp = _payload_timestamp(item, time_attrs)
    if timestamp is not None:
        return (0, timestamp, float(seq_no) if isinstance(seq_no, int) else 0.0, discovery_index)

    if isinstance(seq_no, int):
        return (1, float(seq_no), 0.0, discovery_index)

    return (2, 0.0, 0.0, discovery_index)


def _payload_timestamp(item: Any, attrs: tuple[str, ...]) -> float | None:
    for attr in attrs:
        value = getattr(item, attr, None)
        if value is None:
            continue
        if isinstance(value, datetime):
            return float(timezone.to_utc_timestamp(value))
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                continue
            timestamp = _parse_iso_timestamp(normalized)
            if timestamp is not None:
                return timestamp
    return None


def _parse_iso_timestamp(value: str) -> float | None:
    try:
        return float(timezone.to_utc_timestamp(datetime.fromisoformat(value.replace("Z", "+00:00"))))
    except ValueError:
        return None


def _merge_active_bin_rack(racks: OrderedDict[str, dict[str, Any]], payload: dict[str, Any]) -> None:
    rack_key = _first_non_empty(payload.get("rack_code"), payload.get("rack_id"))
    if rack_key is None:
        return

    rack = racks.setdefault(str(rack_key), {"fields": {}, "bins": OrderedDict()})
    _merge_non_empty(rack["fields"], payload, _RACK_FIELDS)

    for cell_payload in _iter_flat_cells(payload):
        _merge_cell_payload(rack, cell_payload)
    for bin_payload in _iter_nested_bins(payload):
        bin_key = _resolve_bin_key(rack, bin_payload)
        if bin_key is None:
            continue
        bin_item = rack["bins"].setdefault(bin_key, {"fields": {}, "cells": OrderedDict()})
        _merge_non_empty(bin_item["fields"], bin_payload, _BIN_FIELDS)
        for cell_payload in _iter_bin_cells(bin_payload):
            merged_cell_payload = {**bin_item["fields"], **cell_payload}
            _merge_cell_payload(rack, merged_cell_payload, bin_key=bin_key)


def _merge_cell_payload(rack: dict[str, Any], payload: dict[str, Any], *, bin_key: str | None = None) -> None:
    resolved_bin_key = bin_key or _resolve_bin_key(rack, payload)
    if resolved_bin_key is None:
        return

    bin_code = _first_non_empty(payload.get("bin_code"), _bin_field(rack, resolved_bin_key, "bin_code"))
    bin_cell_index = _first_non_empty(payload.get("bin_cell_index"))
    if bin_code is None or bin_cell_index is None:
        return

    bin_item = rack["bins"].setdefault(resolved_bin_key, {"fields": {}, "cells": OrderedDict()})
    _merge_non_empty(bin_item["fields"], payload, _BIN_FIELDS)
    cell_key = f"{bin_code}:{bin_cell_index}"
    cell_fields = bin_item["cells"].setdefault(cell_key, {})
    _merge_non_empty(cell_fields, payload, _CELL_FIELDS)


def _iter_flat_cells(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cells = payload.get("cells")
    if not isinstance(cells, list):
        return []
    return [ensure_dict(cell) for cell in cells if ensure_dict(cell)]


def _iter_nested_bins(payload: dict[str, Any]) -> list[dict[str, Any]]:
    bins = payload.get("bins")
    if not isinstance(bins, list):
        return []
    return [ensure_dict(bin_payload) for bin_payload in bins if ensure_dict(bin_payload)]


def _iter_bin_cells(bin_payload: dict[str, Any]) -> list[dict[str, Any]]:
    cells = bin_payload.get("cells")
    if not isinstance(cells, list):
        return []
    return [ensure_dict(cell) for cell in cells if ensure_dict(cell)]


def _resolve_bin_key(rack: dict[str, Any], payload: dict[str, Any]) -> str | None:
    rack_slot_code = _first_non_empty(payload.get("rack_slot_code"))
    bin_code = _first_non_empty(payload.get("bin_code"))
    if rack_slot_code is None or bin_code is None:
        rack_slot_code, bin_code = _fill_bin_key_from_existing(rack, rack_slot_code, bin_code)
    if rack_slot_code is None and bin_code is None:
        return None
    if rack_slot_code is None:
        return f"bin:{bin_code}"
    if bin_code is None:
        return f"slot:{rack_slot_code}"
    resolved_key = f"{rack_slot_code}:{bin_code}"
    _merge_bin_bucket(rack, f"bin:{bin_code}", resolved_key)
    _merge_bin_bucket(rack, f"slot:{rack_slot_code}", resolved_key)
    return resolved_key


def _merge_bin_bucket(rack: dict[str, Any], source_key: str, target_key: str) -> None:
    if source_key == target_key or source_key not in rack["bins"]:
        return
    source = rack["bins"].pop(source_key)
    target = rack["bins"].setdefault(target_key, {"fields": {}, "cells": OrderedDict()})
    _merge_non_empty(target["fields"], source["fields"], tuple(source["fields"].keys()))
    for cell_key, cell_fields in source["cells"].items():
        target_cell = target["cells"].setdefault(cell_key, {})
        _merge_non_empty(target_cell, cell_fields, tuple(cell_fields.keys()))


def _fill_bin_key_from_existing(
    rack: dict[str, Any],
    rack_slot_code: Any | None,
    bin_code: Any | None,
) -> tuple[Any | None, Any | None]:
    for bin_item in rack["bins"].values():
        fields = bin_item["fields"]
        existing_slot = _first_non_empty(fields.get("rack_slot_code"))
        existing_bin = _first_non_empty(fields.get("bin_code"))
        if rack_slot_code is None and bin_code is not None and existing_bin == bin_code:
            return existing_slot, bin_code
        if bin_code is None and rack_slot_code is not None and existing_slot == rack_slot_code:
            return rack_slot_code, existing_bin
    return rack_slot_code, bin_code


def _bin_field(rack: dict[str, Any], bin_key: str, field: str) -> Any | None:
    bin_item = rack["bins"].get(bin_key)
    if not bin_item:
        return None
    return _first_non_empty(bin_item["fields"].get(field))


def _merge_non_empty(target: dict[str, Any], source: dict[str, Any], fields: tuple[str, ...]) -> None:
    for field in fields:
        value = _first_non_empty(source.get(field))
        if value is not None:
            target[field] = value


def _first_non_empty(*values: Any) -> Any | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if stripped == "":
                continue
            return stripped
        return value
    return None


__all__ = ["build_trace_resource_view"]
