"""EXTERNAL_HTTP dispatcher 的受限命名故障注入边界。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum
from inspect import isawaitable
from typing import Any


class ExternalHttpDispatchFaultPoint(StrEnum):
    """只描述现有 claim/send/evidence 流程中的确定性边界。"""

    BEFORE_CLAIM = "BEFORE_CLAIM"
    AFTER_CLAIM_COMMIT = "AFTER_CLAIM_COMMIT"
    BEFORE_SEND = "BEFORE_SEND"
    AFTER_SEND = "AFTER_SEND"
    BEFORE_OUTBOX_EVIDENCE = "BEFORE_OUTBOX_EVIDENCE"
    AFTER_OUTBOX_EVIDENCE = "AFTER_OUTBOX_EVIDENCE"
    BEFORE_ATTEMPT_EVIDENCE = "BEFORE_ATTEMPT_EVIDENCE"
    AFTER_ATTEMPT_EVIDENCE = "AFTER_ATTEMPT_EVIDENCE"
    BEFORE_REDUCER_EVIDENCE = "BEFORE_REDUCER_EVIDENCE"
    AFTER_REDUCER_EVIDENCE = "AFTER_REDUCER_EVIDENCE"
    AFTER_EVIDENCE_COMMIT = "AFTER_EVIDENCE_COMMIT"


ExternalHttpDispatchFaultHook = Callable[
    [ExternalHttpDispatchFaultPoint, Any | None],
    Awaitable[None] | None,
]


async def emit_external_http_dispatch_fault(
    hook: ExternalHttpDispatchFaultHook | None,
    point: ExternalHttpDispatchFaultPoint,
    outbox: Any | None,
) -> None:
    """调用显式注入的测试/受限开发 hook；生产默认没有全局开启面。"""

    if hook is None:
        return
    result = hook(point, outbox)
    if isawaitable(result):
        await result


__all__ = [
    "ExternalHttpDispatchFaultHook",
    "ExternalHttpDispatchFaultPoint",
    "emit_external_http_dispatch_fault",
]
