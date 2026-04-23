"""SixInOne SSOT 合同测试。"""

import hashlib
import json

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

    assert six_in_one.business_key is None
    assert six_in_one.is_complete is False
    assert "PkgID" in six_in_one.missing_fields


def test_six_in_one_business_field_iteration_uses_single_source_of_truth() -> None:
    six_in_one = SixInOne(
        HHPN="PN001",
        MfrPN="MFR001",
        Qty="100",
        DateCode="20260421",
        LotCode="LOT001",
        PkgID="PKG001",
    )

    assert six_in_one.BUSINESS_FIELD_NAMES == ("HHPN", "MfrPN", "Qty", "DateCode", "LotCode", "PkgID")
    assert six_in_one.iter_business_fields() == [
        ("HHPN", "PN001"),
        ("MfrPN", "MFR001"),
        ("Qty", "100"),
        ("DateCode", "20260421"),
        ("LotCode", "LOT001"),
        ("PkgID", "PKG001"),
    ]


def test_six_in_one_ignores_input_business_key_and_derives_from_pkg_id() -> None:
    six_in_one = SixInOne.model_validate(
        {
            "business_key": "EXTERNAL-BIZ-001",
            "PkgID": "PKG001",
        }
    )

    expected_hash = hashlib.sha256(json.dumps("PKG001", ensure_ascii=False).encode("utf-8")).hexdigest()[:16]

    assert six_in_one.business_key == expected_hash
