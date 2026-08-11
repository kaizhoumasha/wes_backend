"""Runtime trace response 构造回归测试。"""

from src.app.runtime.orchestration.models.session import WorklineSession
from src.app.runtime.orchestration.services.trace.trace_response_builder import build_trace_session_item


def test_trace_session_item_does_not_require_retired_plugin_identity() -> None:
    session = WorklineSession(id=1, session_code="SESSION-001", workline_id=7)

    item = build_trace_session_item(session)

    assert item is not None
    assert "plugin_key" not in type(item).model_fields
