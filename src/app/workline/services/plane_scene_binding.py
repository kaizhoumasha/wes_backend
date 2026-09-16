"""Scene v2 逐资源绑定分类；只读诊断专用，不替代写路径的整体校验。

`installed_plugin.parse_device_bindings` / `parse_position_bindings` 是配置保存路径的
全量校验：任何未知键或非法值都会让整份 config 直接失败。Scene v2 需要按资源逐行展示
`BOUND` / `UNBOUND` / `INVALID`，并把 Definition 已移除但历史 config 仍保留的键单独
识别为孤儿绑定，因此在这里新增一套只读分类，不改动、不复用写路径的 fail-whole 校验。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wes_plugin_sdk import WorkLineDeviceRole, WorkLinePositionSlot


@dataclass(frozen=True, slots=True)
class ClassifiedBinding:
    """单个 Definition 资源键在当前 config 下的绑定分类。"""

    bound_code: str | None
    state: str  # "BOUND" | "UNBOUND" | "INVALID"


@dataclass(frozen=True, slots=True)
class OrphanBinding:
    """Definition 已不再声明、但历史 config 仍保留的绑定。"""

    key: str
    bound_code: str
    reason: str


@dataclass(frozen=True, slots=True)
class ClassifiedBindingSet:
    by_key: dict[str, ClassifiedBinding]
    orphans: tuple[OrphanBinding, ...]


def classify_device_bindings(config: object, roles: tuple[WorkLineDeviceRole, ...]) -> ClassifiedBindingSet:
    """按当前 Definition 声明的设备角色分类 `device_bindings`。"""

    return _classify(config, "device_bindings", {role.role_key for role in roles})


def classify_position_bindings(config: object, slots: tuple[WorkLinePositionSlot, ...]) -> ClassifiedBindingSet:
    """按当前 Definition 声明的位置槽位分类 `position_bindings`。"""

    return _classify(config, "position_bindings", {slot.slot_key for slot in slots})


def _classify(config: object, config_key: str, declared_keys: set[str]) -> ClassifiedBindingSet:
    if not isinstance(config, Mapping):
        return ClassifiedBindingSet(
            by_key={key: ClassifiedBinding(bound_code=None, state="INVALID") for key in declared_keys},
            orphans=(),
        )
    raw = config.get(config_key)
    if raw is not None and not isinstance(raw, Mapping):
        return ClassifiedBindingSet(
            by_key={key: ClassifiedBinding(bound_code=None, state="INVALID") for key in declared_keys},
            orphans=(),
        )
    raw = raw or {}

    by_key: dict[str, ClassifiedBinding] = {}
    orphans: list[OrphanBinding] = []
    for key, value in raw.items():
        has_value = isinstance(value, str) and value.strip() != ""
        if key not in declared_keys:
            if has_value:
                orphans.append(OrphanBinding(key=str(key), bound_code=value, reason="DEFINITION_ROLE_REMOVED"))
            continue
        by_key[key] = ClassifiedBinding(
            bound_code=value if has_value else None,
            state="BOUND" if has_value else "INVALID",
        )

    duplicated_codes = _duplicated_codes(by_key)
    for key, binding in list(by_key.items()):
        if binding.state == "BOUND" and binding.bound_code in duplicated_codes:
            by_key[key] = ClassifiedBinding(bound_code=binding.bound_code, state="INVALID")

    for key in declared_keys - set(by_key):
        by_key[key] = ClassifiedBinding(bound_code=None, state="UNBOUND")

    return ClassifiedBindingSet(by_key=by_key, orphans=tuple(orphans))


def reverse_bound_codes(bindings: ClassifiedBindingSet) -> dict[str, str]:
    """已绑定编码 -> Definition 资源键的反查表；仅取 BOUND 行，供活动对象做精确匹配。"""

    return {
        binding.bound_code: key
        for key, binding in bindings.by_key.items()
        if binding.state == "BOUND" and binding.bound_code is not None
    }


def resolve_resource_ref(
    *,
    location_code: str | None,
    device_codes: tuple[str, ...] | list[str],
    position_by_code: dict[str, str],
    device_by_code: dict[str, str],
) -> tuple[str, str] | None:
    """按已绑定编码精确匹配资源；不按位置码/对象类型/业务步骤做任何推测。

    返回 (group, key)；group 为 "POSITION_SLOT" 或 "DEVICE_ROLE"。
    """

    if location_code is not None and location_code in position_by_code:
        return ("POSITION_SLOT", position_by_code[location_code])
    for code in device_codes:
        if code in device_by_code:
            return ("DEVICE_ROLE", device_by_code[code])
    return None


def _duplicated_codes(by_key: dict[str, ClassifiedBinding]) -> set[str]:
    seen: dict[str, int] = {}
    for binding in by_key.values():
        if binding.bound_code is None:
            continue
        seen[binding.bound_code] = seen.get(binding.bound_code, 0) + 1
    return {code for code, count in seen.items() if count > 1}


__all__ = [
    "ClassifiedBinding",
    "ClassifiedBindingSet",
    "OrphanBinding",
    "classify_device_bindings",
    "classify_position_bindings",
    "resolve_resource_ref",
    "reverse_bound_codes",
]
