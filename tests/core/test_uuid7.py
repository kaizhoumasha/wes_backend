from __future__ import annotations

import importlib
from uuid import RFC_4122, UUID

import pytest


def _uuid7_helpers() -> tuple[object, object]:
    try:
        module = importlib.import_module("src.core.uuid7")
    except ModuleNotFoundError:
        pytest.fail("src.core.uuid7 must provide the shared UUIDv7 helpers")
    return getattr(module, "new_uuid7", None), getattr(module, "is_uuid7", None)


def test_uuid7_helpers_generate_unique_valid_ids_from_fixed_millisecond_and_entropy() -> None:
    new_uuid7, is_uuid7 = _uuid7_helpers()

    assert callable(new_uuid7)
    assert callable(is_uuid7)
    timestamp_ms = 1710000000123
    values = [
        new_uuid7(timestamp_ms=timestamp_ms, random_bits=entropy)  # type: ignore[operator]
        for entropy in (1, 2)
    ]
    parsed = [UUID(value) for value in values]

    assert len(set(values)) == 2
    assert all(value.version == 7 and value.variant == RFC_4122 for value in parsed)
    assert all((value.int >> 80) == timestamp_ms for value in parsed)
    assert all(is_uuid7(value) for value in values)  # type: ignore[operator]
    assert is_uuid7("019f12d0-58d7-4b4d-a23a-1b90aa5d4472") is False  # type: ignore[operator]
