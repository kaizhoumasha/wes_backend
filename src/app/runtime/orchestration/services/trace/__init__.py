"""Trace 服务的惰性导出入口。"""

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .timeline_sequence_service import add_timeline_with_sequence as add_timeline_with_sequence
    from .timeline_sequence_service import allocate_timeline_seq_no as allocate_timeline_seq_no


_EXPORTS = {
    "add_timeline_with_sequence": ".timeline_sequence_service",
    "allocate_timeline_seq_no": ".timeline_sequence_service",
}

__all__ = [
    "add_timeline_with_sequence",
    "allocate_timeline_seq_no",
]


def __getattr__(name: str):
    if module_name := _EXPORTS.get(name):
        return getattr(import_module(module_name, __name__), name)
    raise AttributeError(name)
