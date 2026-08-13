"""Trace 服务的惰性导出入口。"""

from importlib import import_module

_EXPORTS = {
    "add_timeline_with_sequence": ".timeline_sequence_service",
    "allocate_timeline_seq_no": ".timeline_sequence_service",
    "TraceQueryResult": ".trace_query_service",
    "TraceQueryService": ".trace_query_service",
    "trace_query_service": ".trace_query_service",
    "build_trace_resource_view": ".trace_resource_view_builder",
    "build_failed_command_evidence": ".trace_response_builder",
    "build_trace_response": ".trace_response_builder",
    "build_trace_session_item": ".trace_response_builder",
    "build_trace_timeline_item": ".trace_response_builder",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    if module_name := _EXPORTS.get(name):
        return getattr(import_module(module_name, __name__), name)
    raise AttributeError(name)
