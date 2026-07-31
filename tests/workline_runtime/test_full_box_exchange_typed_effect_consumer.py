"""E11 旧 runtime consumer 已关闭的回归。"""

from __future__ import annotations

import pytest

from src.app.runtime.capabilities.material_flow.sorter_inbound_runtime_service import (
    sorter_inbound_runtime_service,
)


def test_full_box_exchange_waits_for_t5_runtime() -> None:
    with pytest.raises(RuntimeError, match="T5 synchronous WMS runtime is not implemented"):
        sorter_inbound_runtime_service.build_full_box_exchange_plan({})
