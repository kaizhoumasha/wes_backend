"""诊断展示不能泄漏凭据或按输入规模无界分配。"""

import json

from src.app.wms_diagnostics.redaction import preview_json, safe_value


def test_nested_credentials_are_removed_before_preview_and_copy() -> None:
    value, incomplete = safe_value({"data": [{"password": "secret", "bin_id": "B1"}], "access_token": "token"})
    assert value == {"data": [{"password": "[REDACTED]", "bin_id": "B1"}], "access_token": "[REDACTED]"}
    assert incomplete is False


def test_invalid_json_is_omitted_instead_of_leaking_unstructured_secrets() -> None:
    text, state = preview_json(b'{"password":"secret"')
    assert text is None
    assert state == "UNSAFE_JSON"


def test_utf8_preview_is_bounded_and_reports_truncation() -> None:
    text, state = preview_json(json.dumps({"data": "仓" * 10000}, ensure_ascii=False).encode(), max_bytes=100)
    assert text is not None and len(text.encode()) <= 100
    assert state == "TRUNCATED"


def test_cycles_and_deep_values_stop_without_recursing_forever() -> None:
    value = {}
    value["child"] = value
    result, incomplete = safe_value(value)
    assert incomplete is True
    assert len(json.dumps(result)) < 10000


def test_oversized_scalar_is_bounded_before_serialization() -> None:
    result, incomplete = safe_value({"value": "x" * 1000000})
    assert incomplete is True
    assert len(json.dumps(result)) < 10000


def test_many_redacted_keys_still_consume_traversal_and_output_budget() -> None:
    result, incomplete = safe_value({f"token_{i}": "secret" for i in range(10000)})
    assert incomplete is True
    assert len(result) <= 2048
    assert len(json.dumps(result).encode()) < 32768
