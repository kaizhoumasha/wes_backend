"""工作线运行时服务容器。

插件只能通过这个容器访问明确允许的内部领域能力，避免直接依赖 HTTP、
Repository 或 SQL。未注入的能力必须由插件自身使用确定性领域 fallback。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping


@runtime_checkable
class BinAllocator(Protocol):
    """料箱分配领域服务接口。"""

    def allocate(self, barcode: str) -> Mapping[str, Any]:
        """按条码分配料箱。"""


@dataclass(frozen=True, slots=True)
class WorklineRuntimeServices:
    """插件运行时可访问的领域服务集合。"""

    bin_allocator: BinAllocator | None = None


def build_workline_runtime_services() -> WorklineRuntimeServices:
    """构建当前 worker 使用的运行时服务集合。"""

    return WorklineRuntimeServices()


__all__ = ["BinAllocator", "WorklineRuntimeServices", "build_workline_runtime_services"]
