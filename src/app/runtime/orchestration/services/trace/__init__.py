"""Trace 服务的惰性导出入口。"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .timeline_sequence_service import add_timeline_with_sequence as add_timeline_with_sequence
    from .timeline_sequence_service import allocate_timeline_seq_no as allocate_timeline_seq_no
    from .trace_resource_view_builder import build_trace_resource_view as build_trace_resource_view
    from .trace_response_builder import build_failed_command_evidence as build_failed_command_evidence
    from .trace_response_builder import build_trace_response as build_trace_response
    from .trace_response_builder import build_trace_session_item as build_trace_session_item
    from .trace_response_builder import build_trace_timeline_item as build_trace_timeline_item


_EXPORTS = {
    "add_timeline_with_sequence": ".timeline_sequence_service",
    "allocate_timeline_seq_no": ".timeline_sequence_service",
    "build_trace_resource_view": ".trace_resource_view_builder",
    "build_failed_command_evidence": ".trace_response_builder",
    "build_trace_response": ".trace_response_builder",
    "build_trace_session_item": ".trace_response_builder",
    "build_trace_timeline_item": ".trace_response_builder",
}

__all__ = [
    "add_timeline_with_sequence",
    "allocate_timeline_seq_no",
    "build_failed_command_evidence",
    "build_trace_resource_view",
    "build_trace_response",
    "build_trace_session_item",
    "build_trace_timeline_item",
]


def __getattr__(name: str):
    if module_name := _EXPORTS.get(name):
        return getattr(import_module(module_name, __name__), name)
    raise AttributeError(name)
