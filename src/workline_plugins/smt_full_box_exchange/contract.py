"""SMT 满箱交换插件合同。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SINGLE_LAYER_RACK_RELEASED = "SINGLE_LAYER_RACK_RELEASED"
SMT_FULL_BOX_EXCHANGE_CALLBACK = "SMT_FULL_BOX_EXCHANGE"


def resolve_smt_full_box_exchange_business_key(payload_json: dict[str, Any]) -> str | None:
    """从单层货架释放事件中解析业务键。"""

    data = payload_json.get("data")
    if not isinstance(data, Mapping):
        data = payload_json
    value = data.get("rack_release_id")
    return value if isinstance(value, str) and value else None


__all__ = [
    "SINGLE_LAYER_RACK_RELEASED",
    "SMT_FULL_BOX_EXCHANGE_CALLBACK",
    "resolve_smt_full_box_exchange_business_key",
]
