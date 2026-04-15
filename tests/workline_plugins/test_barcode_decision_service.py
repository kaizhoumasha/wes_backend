"""BarcodeDecisionService 回归测试。"""

from src.app.workline.domain import BarcodeDecisionType, barcode_decision_service
from src.workline_runtime.payloads import SixInOne


class TestBarcodeDecisionService:
    """条码业务判定服务测试。"""

    def test_evaluate_complete_six_in_one_returns_ok(self):
        """完整 SixInOne 数据应返回 OK 判定。"""
        six_in_one = SixInOne(
            HHPN="HHPN001",
            MfrPN="MFR001",
            Qty="100",
            DateCode="20260414",
            LotCode="LOT001",
            PkgID="PKG001",
        )

        result = barcode_decision_service.evaluate(six_in_one)

        assert result.decision == BarcodeDecisionType.OK
        assert result.pkg_id == "PKG001"
