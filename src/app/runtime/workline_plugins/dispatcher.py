"""Workline Plugin 确定性静态分派边界。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from src.app.runtime.system_capabilities.outcomes import ContractViolation
from src.app.runtime.workline_plugins.contracts import PluginContext, PluginDecision

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from src.app.runtime.system_capabilities.gateway import SystemCapabilityGateway
    from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition

    type RouteHandler = Callable[..., Awaitable[PluginDecision[Any]]]


class WorklinePluginDispatcher:
    """只校验并调用静态 route handler；不接收 DB，也不执行持久化。"""

    def __init__(
        self,
        *,
        plugin_index: Mapping[tuple[str, str], WorklinePluginDefinition],
        route_handlers: Mapping[tuple[str, str, str], RouteHandler],
    ) -> None:
        self._plugin_index = dict(plugin_index)
        self._route_handlers = dict(route_handlers)

    async def dispatch(
        self,
        *,
        plugin_key: str,
        contract_version: str,
        logical_route: str,
        raw_input: dict[str, Any],
        state: object,
        context: PluginContext[Any],
        handler_kwargs: Mapping[str, Any],
        gateway: SystemCapabilityGateway | Any,
        requested_capabilities: tuple[tuple[str, str], ...] = (),
    ) -> PluginDecision[Any] | ContractViolation:
        identity = (plugin_key, contract_version)
        definition = self._plugin_index.get(identity)
        if definition is None:
            return _violation("PLUGIN_IDENTITY_UNKNOWN", "plugin identity is not present in generated index")
        if logical_route not in definition.routes:
            return _violation("PLUGIN_ROUTE_UNKNOWN", "logical route is not declared by plugin")
        handler = self._route_handlers.get((plugin_key, contract_version, logical_route))
        if handler is None:
            return _violation("PLUGIN_ROUTE_AMBIGUOUS", "logical route has no unique static handler")
        undeclared = set(requested_capabilities) - set(definition.allowed_capabilities)
        if undeclared:
            return _violation("CAPABILITY_NOT_DECLARED", "handler requested an undeclared system capability")
        try:
            typed_state = definition.state_model.model_validate(state)
            typed_context_state = definition.state_model.model_validate(context.state)
            if typed_state != typed_context_state:
                raise ValueError("context state does not match pinned state")
            parser = definition.parsers[logical_route]
            logical_input = parser(raw_input)
            decision = await handler(
                logical_input,
                state=typed_state,
                context=context,
                gateway=gateway,
                **dict(handler_kwargs),
            )
            if not isinstance(decision, PluginDecision):
                raise TypeError("handler must return PluginDecision")
            definition.state_model.model_validate(decision.next_state)
        except (KeyError, TypeError, ValidationError, ValueError) as exc:
            return _violation("PLUGIN_CONTRACT_INVALID", str(exc))
        return decision


def _violation(error_code: str, message: str) -> ContractViolation:
    return ContractViolation(error_code=error_code, message=message)


__all__ = ["WorklinePluginDispatcher"]
