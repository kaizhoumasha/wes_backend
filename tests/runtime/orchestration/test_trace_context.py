"""Runtime orchestration trace 合同测试。"""

from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace

from src.app.runtime.orchestration.timeline_generator import TimelineGenerator, timeline_generator
from src.app.runtime.orchestration.trace_context import TraceContext


class _StubSession:
    id = 99
    workline_id = 7
    trace_id = "sess-trace"
    last_request_id = "req-1"
    plugin_key = "retired-plugin"
    contract_version = "v1"


class _StubCommand:
    id = 5
    command_code = "CC-1"
    trace_id = "cmd-trace"
    workline_id = 7
    device_id = 42


def test_from_request_populates_only_current_trace_fields() -> None:
    trace = TraceContext.from_request(
        request_id="r1",
        trace_id="t1",
        device_id=42,
        device_code="dev-1",
        canonical_event_type="INBOX_CLAIM",
        transition="PENDING->ACK",
    )

    assert trace.as_dict() == {
        "request_id": "r1",
        "trace_id": "t1",
        "device_id": 42,
        "device_code": "dev-1",
        "canonical_event_type": "INBOX_CLAIM",
        "transition": "PENDING->ACK",
    }
    assert {field.name for field in fields(TraceContext)}.isdisjoint({"plugin_key", "contract_version"})


def test_runtime_binding_ignores_retired_plugin_identity() -> None:
    workline = SimpleNamespace(id=7, plugin_key="retired-plugin", contract_version="v1")

    trace = TraceContext.from_runtime(session=_StubSession(), workline=workline, command=_StubCommand())

    assert trace.session_id == 99
    assert trace.workline_id == 7
    assert trace.trace_id == "cmd-trace"
    assert trace.request_id == "req-1"
    assert trace.command_id == 5
    assert trace.command_code == "CC-1"
    assert trace.device_id == 42
    assert "plugin_key" not in trace.as_dict()
    assert "contract_version" not in trace.as_dict()


def test_with_inbox_prefers_runtime_session_reference_and_payload_event_fallback() -> None:
    inbox = SimpleNamespace(
        id=10,
        source_message_id="msg-1",
        trace_id="inb-trace",
        workline_id=7,
        workline_session_ref=101,
        session_id=99,
        device_id=42,
        command_id=5,
        payload_json={
            "event_id": "ev-x",
            "causation_id": "cause-x",
            "device_code": "dev-x",
            "command_code": "cmd-x",
            "event_type": "INBOX_CLAIM",
        },
    )

    trace = TraceContext.from_request().with_inbox(inbox)

    assert trace.session_id == 101
    assert trace.inbox_id == 10
    assert trace.event_id == "ev-x"
    assert trace.causation_id == "cause-x"
    assert trace.device_code == "dev-x"
    assert trace.command_code == "cmd-x"
    assert trace.canonical_event_type == "INBOX_CLAIM"


def test_bindings_are_immutable_and_do_not_clear_existing_values() -> None:
    original = TraceContext.from_request(request_id="r1", trace_id="t1")

    updated = original.with_request_id("r2").with_trace_id(None)

    assert original.request_id == "r1"
    assert updated.request_id == "r2"
    assert updated.trace_id == "t1"


def test_projection_methods_emit_current_trace_contract() -> None:
    outbox = SimpleNamespace(
        id=11,
        dispatch_key="dk-1",
        dispatch_type="HTTP",
        target_code="dev-7",
        workline_id=7,
        session_id=99,
    )
    trace = TraceContext.from_request(
        request_id="r1",
        trace_id="t1",
        event_id="e1",
        causation_id="c1",
        canonical_event_type="INBOX_CLAIM",
    ).with_outbox(outbox)

    assert trace.project_timeline_payload(extra_field="v") == {
        "request_id": "r1",
        "trace_id": "t1",
        "event_id": "e1",
        "causation_id": "c1",
        "canonical_event_type": "INBOX_CLAIM",
        "extra_field": "v",
    }
    assert trace.project_outbox_trace(outbox=outbox) == {
        "outbox_id": 11,
        "dispatch_key": "dk-1",
        "dispatch_type": "HTTP",
        "target_code": "dev-7",
    }


def test_timeline_generator_uses_runtime_singleton() -> None:
    assert isinstance(timeline_generator, TimelineGenerator)
