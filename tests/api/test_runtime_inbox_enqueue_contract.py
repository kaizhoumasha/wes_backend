"""RuntimeInbox 即时 enqueue 调用契约。"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    import pytest


def test_callback_enqueue_uses_runtime_gateway_and_absorbs_broker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.callback.v1 import callback

    gateway = MagicMock()
    gateway.enqueue_runtime_inbox.side_effect = ConnectionError("broker unavailable")
    monkeypatch.setattr(callback, "task_queue_gateway", gateway)

    callback._enqueue_runtime_inbox_processing()

    gateway.enqueue_runtime_inbox.assert_called_once_with(limit=10)
    gateway.enqueue_workline_inbox.assert_not_called()


def test_all_workline_inbox_callers_use_runtime_enqueue_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.workline.v1 import operation

    gateway = MagicMock()
    monkeypatch.setattr(operation, "task_queue_gateway", gateway)

    operation._enqueue_runtime_inbox_processing()

    gateway.enqueue_runtime_inbox.assert_called_once_with(limit=10)
    callers = (
        operation.replay_inbox,
        operation.submit_sandbox_external_callback,
    )
    for caller in callers:
        source = inspect.getsource(caller)
        assert "_enqueue_runtime_inbox_processing()" in source
        assert "_enqueue_workline_processing()" not in source
