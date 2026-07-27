"""由 generated Definitions 与作者态静态 registrations 构造的只读 handler registry。"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from src.app.runtime.workline_plugins.dispatcher import HandlerRegistration
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_HANDLER_REGISTRATIONS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition

type HandlerKey = tuple[str, str, str]


def build_generated_handler_registry(
    plugin_index: Mapping[tuple[str, str], WorklinePluginDefinition],
) -> Mapping[HandlerKey, tuple[HandlerRegistration, ...]]:
    """只接受 generated index 已声明 route；不扫描目录、不动态 import。"""

    registrations: dict[HandlerKey, tuple[HandlerRegistration, ...]] = {}
    for identity, definition in plugin_index.items():
        for route in definition.routes:
            authored = WORKLINE_PLUGIN_HANDLER_REGISTRATIONS.get((*identity, route), ())
            registrations[(*identity, route)] = tuple(
                HandlerRegistration(handler=handler, facts_model=facts_model, facts_builder=facts_builder)
                for handler, facts_model, facts_builder in authored
            )
    return MappingProxyType(registrations)


__all__ = ["build_generated_handler_registry"]
