"""旧 Runtime EXTERNAL_REQUEST WMS facade 的发布阻断合同。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.app.runtime.orchestration.models.session import SessionStatus
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent
from src.app.runtime.orchestration.runtime_intent_effects import RuntimeIntentEffectApplier
from src.app.sys.models import SystemOutbox
from src.app.workline.services.write_back_service import EffectApplyState
from src.app.workline.trace_context import TraceContext
from src.utils.timezone import timezone


class _CollectingDb:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, value: Any) -> None:
        self.added.append(value)


def _ctx(db: Any) -> dict[str, Any]:
    session = SimpleNamespace(
        id=31,
        workline_id=41,
        status=SessionStatus.RUNNING.value,
        plugin_key="runtime-test",
        contract_version="v1",
        context_json={},
        trace_id="trace-external-facade-removed",
    )
    workline = SimpleNamespace(id=41, plugin_key="runtime-test", contract_version="v1")
    inbox = SimpleNamespace(
        id=501,
        trace_id=session.trace_id,
        payload_json={"event_type": "INTERNAL_EVENT"},
    )
    trace = TraceContext.from_runtime(session=session, workline=workline, inbox=inbox)
    return {
        "db": db,
        "session": session,
        "workline": workline,
        "inbox": inbox,
        "devices_by_role": {},
        "source_device": None,
        "effect_state": EffectApplyState(),
        "current_status": session.status,
        "trace_id": trace.trace_id,
        "trace": trace,
        "session_ctx": {},
        "now": timezone.now_for_db(),
        "awaiting_device_command_pk": None,
        "awaiting_command_code": None,
        "next_timeline_seq_no": None,
    }


@pytest.mark.asyncio
async def test_external_wms_fulfillment_request_fails_closed_without_legacy_outbox() -> None:
    """未迁移 WMS facade 必须明确失败，不能绕开 35 operation registry。"""

    db = _CollectingDb()
    intent = RuntimeIntent.external_request(
        dispatch_key="wms-fulfillment:REQ-REMOVED",
        target_code="WMS_REMOVED_FACADE_TARGET",
        source_system="WMS",
        payload={"request_id": "wms-fulfillment:REQ-REMOVED"},
        timeout_seconds=30,
    )

    with pytest.raises(RuntimeError, match="WMS external HTTP facade is removed"):
        await RuntimeIntentEffectApplier().apply(_ctx(db), [intent])

    assert [item for item in db.added if isinstance(item, SystemOutbox)] == []
