"""BC-05 粗分机正常入库 contract 壳（strict xfail）。

验收: 扫码、识别、WMS 校验、箱格分配/预约、滚筒线路由语义被保护。
Phase 0 缺 Phase 1 runtime capability + RuntimeIntentLog schema,
用 strict xfail 标明解除条件。Phase 4 sorter-inbound-capability-spec 完成后解除。

characterization 输入提取见 tests/characterization/workline_legacy/
test_rough_sorter_inbound_characterization.py。
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=True, reason="Phase 1 RuntimeIntentLog + Phase 4 sorter-inbound-capability 未实现")
def test_rough_sorter_inbound_happy_path_contract():
    """目标态: 扫码→识别→WMS 校验→箱格分配/预约→滚筒线路由意图顺序正确。

    Phase 0 占位; Phase 4 接入真实 capability 后补全意图顺序断言。
    """
    # 占位: Phase 4 实现后替换为 fixture 驱动的 capability orchestration 断言
    expected_intent_order = [
        "SCAN_BARCODE",
        "WMS_VALIDATE_MATERIAL",
        "ALLOCATE_BIN_CELL",
        "ROUTE_TO_WORK_POSITION",
    ]
    actual_intent_order: list[str] = []
    assert actual_intent_order == expected_intent_order
