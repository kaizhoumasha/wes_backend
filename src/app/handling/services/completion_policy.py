"""Handling operation 完成策略解析。"""

from __future__ import annotations

from src.app.sys.models import OperationCompletionPolicy

_FULL_BOX_EXCHANGE_OPERATION_MARKER = "FULL_BOX_EXCHANGE"
_RECONCILED_EXCHANGE_OPERATION_MARKERS = (
    _FULL_BOX_EXCHANGE_OPERATION_MARKER,
    "RACK_BIN_EXCHANGE",
)


def is_full_box_exchange_operation_type(operation_type: str) -> bool:
    """判断 Handling operation_type 是否表示满箱/架箱交换。"""

    normalized = operation_type.upper()
    return _FULL_BOX_EXCHANGE_OPERATION_MARKER in normalized


def is_reconciled_exchange_operation_type(operation_type: str) -> bool:
    """判断 Handling operation_type 是否必须按外部履约 + 对账闭环处理。"""

    normalized = operation_type.upper()
    return any(marker in normalized for marker in _RECONCILED_EXCHANGE_OPERATION_MARKERS)


def resolve_request_completion_policy(operation_type: str) -> OperationCompletionPolicy:
    """解析新建 Handling operation 的完成确认策略。"""

    if is_reconciled_exchange_operation_type(operation_type):
        return OperationCompletionPolicy.CALLBACK_PLUS_RECONCILIATION
    return OperationCompletionPolicy.CALLBACK_TRUSTED


__all__ = [
    "is_full_box_exchange_operation_type",
    "is_reconciled_exchange_operation_type",
    "resolve_request_completion_policy",
]
