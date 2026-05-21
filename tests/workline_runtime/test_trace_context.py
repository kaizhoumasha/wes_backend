"""TraceContext 与 TRACE 传播收口测试。"""

from types import SimpleNamespace

from src.workline_runtime.diagnostics import build_diagnostic_context
from src.workline_runtime.plugin_context import PluginContextBuilder
from src.workline_runtime.services import WorklineRuntimeServices
from src.workline_runtime.trace_context import TraceContext


class TestTraceContext:
    def test_from_runtime_binds_entities_and_payload_fields(self) -> None:
        session = SimpleNamespace(
            id=11,
            workline_id=2,
            trace_id="trace-session",
            plugin_key="smt_classifier",
            contract_version="1.2.3",
            last_request_id="req-session",
        )
        workline = SimpleNamespace(id=2, plugin_key="smt_classifier", contract_version="1.2.3")
        inbox = SimpleNamespace(
            id=33,
            source_message_id="req-001",
            trace_id="trace-001",
            workline_id=2,
            device_id=5,
            command_id=7,
            payload_json={
                "device_code": "DEV-01",
                "command_code": "CMD-01",
                "canonical_event_type": "SCAN_COMPLETED",
            },
        )
        command = SimpleNamespace(
            id=7,
            command_code="CMD-01",
            trace_id="trace-001",
            workline_id=2,
            device_id=5,
            plugin_key="smt_classifier",
            contract_version="1.2.3",
        )
        outbox = SimpleNamespace(id=9, dispatch_key="dispatch-9", workline_id=2, session_id=11)

        trace = TraceContext.from_runtime(
            session=session,
            workline=workline,
            inbox=inbox,
            command=command,
            outbox=outbox,
        )

        assert trace.request_id == "req-001"
        assert trace.trace_id == "trace-001"
        assert trace.workline_id == 2
        assert trace.session_id == 11
        assert trace.inbox_id == 33
        assert trace.device_id == 5
        assert trace.device_code == "DEV-01"
        assert trace.command_id == 7
        assert trace.command_code == "CMD-01"
        assert trace.outbox_id == 9
        assert trace.dispatch_key == "dispatch-9"
        assert trace.canonical_event_type == "SCAN_COMPLETED"
        assert trace.plugin_key == "smt_classifier"
        assert trace.contract_version == "1.2.3"

    def test_diagnostic_and_plugin_context_share_same_trace(self) -> None:
        session = SimpleNamespace(
            id=21,
            workline_id=3,
            trace_id="trace-ctx-001",
            plugin_key="smt_classifier",
            contract_version="2.0.0",
            last_request_id="req-ctx-001",
            context_json={"barcode": "BC-001"},
        )
        workline = SimpleNamespace(id=3, plugin_key="smt_classifier", contract_version="2.0.0", config={"mode": "auto"})
        inbox = SimpleNamespace(
            id=55,
            source_message_id="req-ctx-001",
            trace_id="trace-ctx-001",
            workline_id=3,
            device_id=8,
            payload_json={"device_code": "DEV-CTX-01", "event_type": "SCAN_COMPLETED"},
        )
        devices_by_role = {"SCANNER": [SimpleNamespace(id=8, device_code="DEV-CTX-01", device_role="SCANNER")]}
        services = WorklineRuntimeServices()

        trace = TraceContext.from_runtime(session=session, workline=workline, inbox=inbox)
        diagnostic = build_diagnostic_context(trace=trace, session=session, inbox=inbox, workline=workline)
        ctx = PluginContextBuilder().build(
            session=session,
            workline=workline,
            devices_by_role=devices_by_role,
            services=services,
            inbox=inbox,
            trace=trace,
        )

        assert diagnostic.request_id == "req-ctx-001"
        assert diagnostic.trace_id == "trace-ctx-001"
        assert diagnostic.session_id == 21
        assert diagnostic.workline_id == 3
        assert diagnostic.plugin_key == "smt_classifier"
        assert diagnostic.canonical_event_type == "SCAN_COMPLETED"
        assert ctx.trace_id == "trace-ctx-001"
        assert ctx.trace.request_id == "req-ctx-001"
        assert ctx.trace.trace_id == "trace-ctx-001"
        assert ctx.diagnostics is not None
        assert ctx.diagnostics.trace_id == "trace-ctx-001"
        assert ctx.diagnostics.session_id == 21
