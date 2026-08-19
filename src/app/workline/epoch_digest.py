"""LineRunEpoch 冻结配置与拓扑的确定性摘要。"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Protocol

    class _DeviceTopologyInput(Protocol):
        @property
        def device_code(self) -> str: ...

        @property
        def device_role(self) -> str: ...

        @property
        def endpoint_base_url(self) -> str: ...

        @property
        def contract_key(self) -> str: ...

        @property
        def contract_version(self) -> str: ...

        @property
        def status_max_age_ms(self) -> int: ...

        @property
        def command_timeout_ms(self) -> int: ...

    class _PositionTopologyInput(Protocol):
        @property
        def position_role(self) -> str: ...

        @property
        def location_id(self) -> str: ...

        @property
        def location_type(self) -> str: ...


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_configuration_snapshot(value: dict[str, object]) -> dict[str, object]:
    """验证并复制一个可 canonicalize 的 JSON object。"""

    snapshot: object = json.loads(_canonical_json(value))
    if not isinstance(snapshot, dict):
        raise TypeError("configuration snapshot must be a JSON object")
    return cast("dict[str, object]", snapshot)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def configuration_digest(
    plugin_key: str,
    plugin_version: str,
    flow_mode: str,
    configuration_snapshot: dict[str, object],
) -> str:
    """摘要 Epoch 插件身份、flow mode 与完整配置快照。"""

    return _digest(
        {
            "configuration_snapshot": configuration_snapshot,
            "flow_mode": flow_mode,
            "plugin_key": plugin_key,
            "plugin_version": plugin_version,
        }
    )


def topology_digest(
    device_bindings: Iterable[_DeviceTopologyInput],
    position_bindings: Iterable[_PositionTopologyInput],
) -> str:
    """摘要不依赖数据库生成 ID 的稳定 topology input。"""

    return _digest(
        {
            "devices": sorted(
                (
                    binding.device_code,
                    binding.device_role,
                    binding.endpoint_base_url,
                    binding.contract_key,
                    binding.contract_version,
                    binding.status_max_age_ms,
                    binding.command_timeout_ms,
                )
                for binding in device_bindings
            ),
            "positions": sorted(
                (binding.position_role, binding.location_id, binding.location_type) for binding in position_bindings
            ),
        }
    )


__all__ = ["canonical_configuration_snapshot", "configuration_digest", "topology_digest"]
