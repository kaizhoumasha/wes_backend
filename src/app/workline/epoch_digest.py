"""LineRunEpoch 冻结配置与拓扑的确定性摘要。"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding, LineRunEpochPositionBinding


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def configuration_digest(plugin_key: str, plugin_version: str, flow_mode: str) -> str:
    """仅摘要 Epoch 自身冻结的插件身份与 flow mode。"""

    return _digest(
        {
            "flow_mode": flow_mode,
            "plugin_key": plugin_key,
            "plugin_version": plugin_version,
        }
    )


def topology_digest(
    device_bindings: Iterable[LineRunEpochDeviceBinding],
    position_bindings: Iterable[LineRunEpochPositionBinding],
) -> str:
    """摘要按完整不可变身份排序后的 Epoch bindings。"""

    return _digest(
        {
            "devices": sorted(binding.identity_tuple() for binding in device_bindings),
            "positions": sorted(binding.identity_tuple() for binding in position_bindings),
        }
    )


__all__ = ["configuration_digest", "topology_digest"]
