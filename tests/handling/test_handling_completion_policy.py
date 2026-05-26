from __future__ import annotations

import pytest

from src.app.handling.services import is_full_box_exchange_operation_type, resolve_request_completion_policy
from src.app.sys.models import OperationCompletionPolicy


@pytest.mark.parametrize(
    "operation_type",
    [
        "FULL_BOX_EXCHANGE",
        "SINGLE_LAYER_FULL_BOX_EXCHANGE",
    ],
)
def test_full_box_exchange_operation_types_require_callback_plus_reconciliation(operation_type: str) -> None:
    assert is_full_box_exchange_operation_type(operation_type) is True
    assert resolve_request_completion_policy(operation_type) == OperationCompletionPolicy.CALLBACK_PLUS_RECONCILIATION


@pytest.mark.parametrize(
    "operation_type",
    [
        "SORTER_FEED_BIN",
        "FULL_BIN_EXCHANGE",
        "RACK_BIN_EXCHANGE",
        "SINGLE_LAYER_FULL_BIN_EXCHANGE",
    ],
)
def test_non_full_box_operation_types_use_callback_trusted(operation_type: str) -> None:
    assert is_full_box_exchange_operation_type(operation_type) is False
    assert resolve_request_completion_policy(operation_type) == OperationCompletionPolicy.CALLBACK_TRUSTED
