"""SixInOne SSOT 合同测试。"""

from src.workline_runtime.contracts import SixInOne


def test_six_in_one_generates_business_key_from_scheme_a_fields() -> None:
    six_in_one = SixInOne(
        HHPN="PN001",
        MfrPN="MFR001",
        Qty="100",
        DateCode="20260421",
        LotCode="LOT001",
        PkgID="PKG001",
    )

    assert six_in_one.business_key is not None
    assert six_in_one.is_complete is True


def test_six_in_one_does_not_parse_plugin_specific_aliases_by_itself() -> None:
    six_in_one = SixInOne.model_validate({"ProductNo": "PN001", "PONumber": "PKG001"})

    assert six_in_one.has_any_value is False
    assert six_in_one.business_key is None


def test_six_in_one_computes_missing_fields() -> None:
    six_in_one = SixInOne(
        HHPN="PN001",
        MfrPN="MFR001",
        Qty="100",
        DateCode="20260421",
        LotCode="LOT001",
    )

    assert six_in_one.business_key is not None
    assert six_in_one.is_complete is False
    assert "PkgID" in six_in_one.missing_fields
