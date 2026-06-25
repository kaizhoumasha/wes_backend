"""BC-06 满箱交换前置分流 contract 壳（strict xfail）。

验收: 满箱/换箱/换架必须按外部履约 + 对账闭环建模, 不本地冒充完成。
Phase 0 缺 Phase 1 RuntimeIntentLog + Phase 3 WmsFulfillmentPort schema,
用 strict xfail 标明解除条件。Phase 3/4 完成后解除。

characterization 输入提取见 tests/characterization/workline_legacy/
test_full_box_exchange_characterization.py。
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=True, reason="Phase 1 RuntimeIntentLog + Phase 3 WmsFulfillmentPort/Reconciliation 未实现")
def test_full_box_exchange_uses_fulfillment_and_reconciliation_contract():
    """目标态: 满箱/换箱/换架生成外部履约、等待回调、对账 evidence, 不本地冒充完成。

    Phase 0 占位; Phase 3/4 接入真实 fulfillment + reconciliation 后补全 evidence 断言。
    """
    # 占位: Phase 3/4 实现后替换为 fixture 驱动的 fulfillment + reconciliation 闭环断言
    required_evidence = {
        "external_fulfillment_requested",
        "fulfillment_callback_received",
        "reconciliation_evidence",
    }
    actual_evidence: set[str] = set()
    assert required_evidence.issubset(actual_evidence)
