"""Handling operation 完成策略解析。"""

from __future__ import annotations

from src.app.sys.models import OperationCompletionPolicy

_FULL_BOX_EXCHANGE_OPERATION_MARKERS = ("FULL_BOX_EXCHANGE", "FULL_BIN_EXCHANGE", "RACK_BIN_EXCHANGE")


def is_full_box_exchange_operation_type(operation_type: str) -> bool:
    """判断 Handling operation_type 是否表示满箱/架箱交换。"""

    normalized = operation_type.upper()
    return any(marker in normalized for marker in _FULL_BOX_EXCHANGE_OPERATION_MARKERS)


def resolve_request_completion_policy(operation_type: str) -> OperationCompletionPolicy:
    """解析新建 Handling operation 的完成确认策略。"""

    if is_full_box_exchange_operation_type(operation_type):
        return OperationCompletionPolicy.CALLBACK_PLUS_RECONCILIATION
    return OperationCompletionPolicy.CALLBACK_TRUSTED


__all__ = ["is_full_box_exchange_operation_type", "resolve_request_completion_policy"]
