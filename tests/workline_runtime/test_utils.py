"""测试 workline_runtime.utils 公共工具函数。"""

from __future__ import annotations

import pytest

from src.workline_runtime.utils import (
    JsonDict,
    ensure_dict,
    non_empty_str,
    payload_dict,
    resolve_first_str,
)


class TestNonEmptyStr:
    """测试 non_empty_str 函数。"""

    def test_returns_string_for_valid_string(self) -> None:
        assert non_empty_str("hello") == "hello"

    def test_returns_none_for_empty_string(self) -> None:
        assert non_empty_str("") is None

    def test_returns_none_for_whitespace_only(self) -> None:
        assert non_empty_str("   ") == "   "  # whitespace is still a valid string

    def test_returns_none_for_none(self) -> None:
        assert non_empty_str(None) is None

    def test_returns_none_for_int(self) -> None:
        assert non_empty_str(123) is None

    def test_returns_none_for_list(self) -> None:
        assert non_empty_str(["a", "b"]) is None


class TestPayloadDict:
    """测试 payload_dict 函数。"""

    def test_returns_dict_for_dict_input(self) -> None:
        input_dict = {"key": "value"}
        assert payload_dict(input_dict) == input_dict

    def test_returns_empty_dict_for_none(self) -> None:
        assert payload_dict(None) == {}

    def test_returns_empty_dict_for_string(self) -> None:
        assert payload_dict("not a dict") == {}

    def test_returns_empty_dict_for_list(self) -> None:
        assert payload_dict([1, 2, 3]) == {}

    def test_returns_dict_for_nested_dict(self) -> None:
        nested = {"outer": {"inner": "value"}}
        assert payload_dict(nested) == nested


class TestEnsureDict:
    """测试 ensure_dict 函数（与 payload_dict 功能相同，公共 API）。"""

    def test_returns_dict_for_dict_input(self) -> None:
        input_dict = {"key": "value"}
        assert ensure_dict(input_dict) == input_dict

    def test_returns_empty_dict_for_none(self) -> None:
        assert ensure_dict(None) == {}


class TestResolveFirstStr:
    """测试 resolve_first_str 函数。"""

    def test_returns_first_non_empty_value(self) -> None:
        payload = {"error_code": "E001", "code": "E002"}
        assert resolve_first_str(payload, ("error_code", "code")) == "E001"

    def test_falls_back_to_second_alias(self) -> None:
        payload = {"code": "E002"}  # no error_code
        assert resolve_first_str(payload, ("error_code", "code")) == "E002"

    def test_returns_empty_string_when_all_missing(self) -> None:
        payload = {}
        assert resolve_first_str(payload, ("error_code", "code")) == ""

    def test_returns_empty_string_when_all_empty(self) -> None:
        payload = {"error_code": "", "code": ""}
        assert resolve_first_str(payload, ("error_code", "code")) == ""

    def test_skips_non_string_values(self) -> None:
        payload = {"error_code": 123, "code": "E002"}
        assert resolve_first_str(payload, ("error_code", "code")) == "E002"
