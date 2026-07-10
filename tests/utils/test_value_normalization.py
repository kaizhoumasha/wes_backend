"""value_normalization 新增函数单元测试。"""

from decimal import Decimal
from enum import Enum

import pytest

from src.utils.value_normalization import (
    json_safe,
    mapping_copy,
    positive_quantity,
    positive_timeout_seconds,
    require_text,
    require_text_any,
    string_list,
)


class SampleEnum(Enum):
    VALUE = "value"


class TestRequireText:
    def test_returns_value_when_present(self) -> None:
        assert require_text("hello", "a") == "hello"

    def test_raises_when_empty_string(self) -> None:
        with pytest.raises(ValueError, match="a is required"):
            require_text("", "a")

    def test_raises_when_none(self) -> None:
        with pytest.raises(ValueError, match="a is required"):
            require_text(None, "a")

    def test_strips_surrounding_whitespace(self) -> None:
        assert require_text("  hello  ", "a") == "hello"

    def test_raises_when_blank_string(self) -> None:
        with pytest.raises(ValueError, match="a is required"):
            require_text("   ", "a")

    @pytest.mark.parametrize("value", [0, False, SampleEnum.VALUE])
    def test_raises_when_value_is_not_string(self, value: object) -> None:
        with pytest.raises(ValueError, match="a is required"):
            require_text(value, "a")


class TestRequireTextAny:
    def test_returns_first_match(self) -> None:
        assert require_text_any({"b": "world"}, "a", "b", "c") == "world"

    def test_raises_when_all_missing(self) -> None:
        with pytest.raises(ValueError, match="a/b/c is required"):
            require_text_any({}, "a", "b", "c")


class TestStringList:
    def test_list_of_strings(self) -> None:
        assert string_list({"a": ["x", "y"]}, "a") == ["x", "y"]

    def test_single_string_wrapped(self) -> None:
        assert string_list({"a": "x"}, "a") == ["x"]

    def test_missing_returns_empty(self) -> None:
        assert string_list({}, "a") == []

    def test_none_returns_empty(self) -> None:
        assert string_list({"a": None}, "a") == []


class TestMappingCopy:
    def test_copies_mapping(self) -> None:
        original = {"a": 1}
        result = mapping_copy(original)
        assert result == {"a": 1}
        assert result is not original

    def test_non_mapping_returns_empty(self) -> None:
        assert mapping_copy([1, 2, 3]) == {}


class TestJsonSafe:
    def test_decimal_to_string(self) -> None:
        assert json_safe(Decimal("3.14")) == "3.14"

    def test_tuple_to_list(self) -> None:
        assert json_safe((1, 2)) == [1, 2]

    def test_nested_mapping(self) -> None:
        assert json_safe({"a": Decimal("1.0")}) == {"a": "1.0"}

    def test_primitives_pass_through(self) -> None:
        assert json_safe("hello") == "hello"
        assert json_safe(42) == 42


class TestPositiveQuantity:
    def test_positive_int(self) -> None:
        assert positive_quantity(5) == 5.0

    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            positive_quantity(0)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            positive_quantity(-1)


class TestPositiveTimeoutSeconds:
    def test_positive_int(self) -> None:
        assert positive_timeout_seconds(60) == 60

    def test_none_defaults_to_300(self) -> None:
        assert positive_timeout_seconds(None) == 300

    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            positive_timeout_seconds(0)
