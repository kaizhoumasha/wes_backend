"""BarcodeDecisionService 回归测试。"""

from src.app.runtime.capabilities.phase4.contracts.six_in_one import SixInOne
from src.app.workline.domain import BarcodeDecisionType, barcode_decision_service


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
        assert result.business_key
        assert result.six_in_one.PkgID == "PKG001"

    def test_evaluate_returns_invalid_when_any_field_contains_comma(self):
        """任一 SixInOne 字段包含逗号时应判定为格式错误。"""
        six_in_one = SixInOne(
            HHPN="HHPN001",
            MfrPN="MFR,001",
            Qty="100",
            DateCode="20260414",
            LotCode="LOT001",
            PkgID="PKG001",
        )

        result = barcode_decision_service.evaluate(six_in_one)

        assert result.decision == BarcodeDecisionType.INVALID
        assert result.reason_code == "BARCODE_INVALID"
        assert result.reason_message == "条码格式错误: MfrPN"

    def test_evaluate_returns_ng_when_pkg_id_hits_business_rule(self):
        """命中业务规则的 PkgID 应返回 NG 判定。"""
        six_in_one = SixInOne(
            HHPN="620100L00-011-G",
            MfrPN="CC0402JRNPO9BN220",
            Qty="7387",
            DateCode="122625",
            LotCode="8904936031",
            PkgID="LOTSIZENG_001",
        )

        result = barcode_decision_service.evaluate(six_in_one)

        assert result.decision == BarcodeDecisionType.NG
        assert result.reason_code == "SCAN_NG_BY_RULE"
