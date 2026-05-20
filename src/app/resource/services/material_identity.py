"""资源物料身份键工具。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MaterialIdentity:
    material_code: str
    vendor_code: str | None
    date_code: str
    lot_code: str


def parse_material_identity_key(value: str | None) -> MaterialIdentity | None:
    """解析 MAT 身份键，兼容旧 4 段 key 和新 5 段 key。"""

    if value is None:
        return None
    parts = value.split(":")
    if len(parts) == 5 and parts[0] == "MAT":
        return MaterialIdentity(parts[1], parts[2] or None, parts[3], parts[4])
    if len(parts) == 4 and parts[0] == "MAT":
        return MaterialIdentity(parts[1], None, parts[2], parts[3])
    return None


def material_identity_keys_match(left: str | None, right: str | None) -> bool:
    """判断两个物料身份键是否指向同一物料。

    新 key 带 vendor；旧 active 数据可能缺 vendor。两边都有 vendor 时必须一致；
    任一边缺 vendor 时，只能按 HHPN/DateCode/LotCode 兼容。
    """

    if left is None or right is None:
        return False
    if left == right:
        return True
    left_identity = parse_material_identity_key(left)
    right_identity = parse_material_identity_key(right)
    if left_identity is None or right_identity is None:
        return False
    if (
        left_identity.material_code != right_identity.material_code
        or left_identity.date_code != right_identity.date_code
        or left_identity.lot_code != right_identity.lot_code
    ):
        return False
    return (
        left_identity.vendor_code is None
        or right_identity.vendor_code is None
        or left_identity.vendor_code == right_identity.vendor_code
    )


def material_identity_lookup_keys(value: str) -> tuple[str, ...]:
    """返回查询 active 聚合占用时需要覆盖的 canonical/legacy key。"""

    identity = parse_material_identity_key(value)
    if identity is None:
        return (value,)
    keys = [
        f"MAT:{identity.material_code}:{identity.vendor_code or ''}:{identity.date_code}:{identity.lot_code}",
        f"MAT:{identity.material_code}::{identity.date_code}:{identity.lot_code}",
        f"MAT:{identity.material_code}:{identity.date_code}:{identity.lot_code}",
    ]
    return tuple(dict.fromkeys(keys))
