# runtime migration C5a 桥接:src.workline_runtime.business_identity 的门面副本
# wlr 目录在阶段 3 整体删除时,本桥接与 wlr 副本合并 / 删除。
# 自引用 src.workline_runtime.utils 已重定向到 src.app.workline.utils (C2)。

"""Runtime business identity helpers."""

from __future__ import annotations

from typing import Any

from src.app.workline.utils import ensure_dict, non_empty_str

_DISPLAY_IDENTITY_FIELDS = (
    "barcode",
    "PkgID",
    "pkg_id",
    "package_id",
    "container_code",
    "container_id",
    "box_code",
    "bin_code",
    "rack_code",
    "rack_id",
    "shelf_code",
    "shelf_id",
    "HHPN",
    "hhpn",
    "material_code",
    "MfrPN",
    "mfrpn",
)


def resolve_payload_display_identity(payload_json: dict[str, Any]) -> str | None:
    """Return the operator-facing scanned entity from an event payload.

    This value is only for runtime display. Session ownership still uses the
    plugin business_key resolver.
    """

    data = ensure_dict(payload_json.get("data"))
    for field in _DISPLAY_IDENTITY_FIELDS:
        value = non_empty_str(data.get(field))
        if value:
            return value
    return None
