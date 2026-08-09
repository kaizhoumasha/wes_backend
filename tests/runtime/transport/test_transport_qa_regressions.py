"""后端 QA 发现的 Transport 合同回归。"""

import pytest

from src.app.transport.contracts import HandoffPosition, TransportContractError


def test_transport_identifier_rejects_text_that_cannot_be_encoded_as_utf8() -> None:
    """发送前拒绝无法进入 UTF-8 JSON wire 的标识，避免误判为交付未知。"""

    with pytest.raises(TransportContractError, match="valid UTF-8"):
        HandoffPosition("ROLLER_\ud800")
