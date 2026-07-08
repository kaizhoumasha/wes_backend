"""Callback 域 utils + contracts mirror 完整性测试。

校验 callback 域从 src.workline_runtime 解耦后,本地镜像 (callback/utils 与
callback/contracts) 对外公开 API 与 legacy runtime 镜像版本行为一致。

注:这是 mirror 完整性测试,不重 legacy runtime 测试,只验证本地镜像能 import + 关键
API 行为(TraceContext 字段填充 / TimelineGenerator 返回值 / utils 边界)。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.callback.contracts import (
    TimelineGenerator,
    TraceContext,
    timeline_generator,
)
from src.app.callback.contracts.trace_context import (
    TraceContext as TraceContextDirect,
)
from src.app.callback.utils import non_empty_str


class _StubSession:
    id = 99
    workline_id = 7
    trace_id = "sess-trace"
    last_request_id = "req-1"
    plugin_key = "plug-a"
    contract_version = "v1"


class _StubCommand:
    id = 5
    command_code = "CC-1"
    trace_id = "cmd-trace"
    workline_id = 7
    device_id = 42
    plugin_key = "plug-c"
    contract_version = "v1"


def test_non_empty_str_returns_str() -> None:
    assert non_empty_str("hi") == "hi"


def test_non_empty_str_rejects_empty_or_non_str() -> None:
    assert non_empty_str("") is None
    assert non_empty_str(None) is None
    assert non_empty_str(42) is None
    assert non_empty_str(" ") == " "


def test_tracecontext_re_exports_match_direct_import() -> None:
    assert TraceContext is TraceContextDirect


def test_tracecontext_from_request_populates_minimal_fields() -> None:
    t = TraceContext.from_request(
        request_id="r1",
        trace_id="t1",
        device_id=42,
        device_code="dev-1",
        canonical_event_type="INBOX_CLAIM",
        transition="PENDING->ACK",
    )
    assert t.request_id == "r1"
    assert t.trace_id == "t1"
    assert t.device_id == 42
    assert t.device_code == "dev-1"
    assert t.canonical_event_type == "INBOX_CLAIM"
    assert t.transition == "PENDING->ACK"
    # unset 字段保持 None
    assert t.event_id is None
    assert t.workline_id is None
    assert t.inbox_id is None


def test_tracecontext_with_session_fills_runtime_fields() -> None:
    t = TraceContext.from_request(request_id="req-x").with_session(_StubSession())
    assert t.session_id == 99
    assert t.workline_id == 7
    assert t.trace_id == "sess-trace"
    assert t.request_id == "req-1"
    assert t.plugin_key == "plug-a"
    assert t.contract_version == "v1"


def test_tracecontext_with_command_fills_command_fields() -> None:
    t = TraceContext.from_request(trace_id="base").with_command(_StubCommand())
    assert t.command_id == 5
    assert t.command_code == "CC-1"
    assert t.trace_id == "cmd-trace"
    assert t.device_id == 42
    assert t.workline_id == 7


def test_tracecontext_with_inbox_extracts_payload_event_type_fallback() -> None:
    inbox = SimpleNamespace(
        id=10,
        source_message_id="msg-1",
        trace_id="inb-trace",
        workline_id=7,
        session_id=99,
        device_id=42,
        command_id=5,
        payload_json={"event_id": "ev-x", "device_code": "dev-x", "canonical_event_type": "INBOX_CLAIM"},
    )
    t = TraceContext.from_request().with_inbox(inbox)
    assert t.inbox_id == 10
    assert t.session_id == 99
    assert t.command_id == 5
    assert t.device_code == "dev-x"
    assert t.canonical_event_type == "INBOX_CLAIM"
    assert t.event_id == "ev-x"


def test_tracecontext_with_methods_are_immutable_bind() -> None:
    t1 = TraceContext.from_request(request_id="r1")
    t2 = t1.with_request_id("r2")
    # 原实例不变
    assert t1.request_id == "r1"
    assert t2.request_id == "r2"


def test_tracecontext_project_timeline_payload_includes_required_fields() -> None:
    t = TraceContext.from_request(
        request_id="r1",
        trace_id="t1",
        event_id="e1",
        causation_id="c1",
        canonical_event_type="INBOX_CLAIM",
    )
    payload = t.project_timeline_payload(extra_field="v")
    assert payload["request_id"] == "r1"
    assert payload["trace_id"] == "t1"
    assert payload["event_id"] == "e1"
    assert payload["causation_id"] == "c1"
    assert payload["canonical_event_type"] == "INBOX_CLAIM"
    assert payload["extra_field"] == "v"


def test_tracecontext_project_outbox_trace_resolves_outbox_fields() -> None:
    outbox = SimpleNamespace(id=11, dispatch_key="dk-1", dispatch_type="HTTP", target_code="dev-7")
    t = TraceContext.from_request(trace_id="t1").with_outbox(outbox)
    projected = t.project_outbox_trace(outbox=outbox)
    assert projected["outbox_id"] == 11
    assert projected["dispatch_key"] == "dk-1"
    assert projected["dispatch_type"] == "HTTP"
    assert projected["target_code"] == "dev-7"


def test_tracecontext_as_dict_drops_unset_fields() -> None:
    t = TraceContext.from_request(request_id="r1")
    d = t.as_dict()
    assert d == {"request_id": "r1"}


def test_timeline_generator_singleton_exported() -> None:
    assert isinstance(timeline_generator, TimelineGenerator)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hi", "hi"),
        ("", None),
        (None, None),
        (123, None),
        (" ", " "),
    ],
)
def test_non_empty_str_parametrized(raw: object, expected: str | None) -> None:
    assert non_empty_str(raw) == expected  # type: ignore[arg-type]
