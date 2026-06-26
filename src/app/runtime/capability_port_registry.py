"""CapabilityPortRegistry (Phase 1 CEO-009 + H2, 主计划 §3.5 + §9.2)。

capability 注入边界: 只允许 query/effect port contract 注册;
inbound normalizer (WmsEventPort / DeviceEventPort / RuntimeInbox consumer)
被 type guard 拒绝, 不能进入业务 capability。

factory pattern: register(port_protocol, factory) 按需构造, 不直接暴露
implementation type。capability 不持有底层 session/db client。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# H2: inbound normalizer 类型清单 — 注册表 type guard 拒绝这些类型
_INBOUND_NORMALIZER_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "WmsEventPort",
        "DeviceEventPort",
        "InboundEventPort",
        "RuntimeInbox",
        "RuntimeInboxConsumer",
    }
)


class CapabilityPortRegistry:
    """capability port 注册表 (主计划 §3.5 + §9.2)。

    只允许 query/effect port contract 注册; inbound normalizer 被 H2 type guard
    拒绝。factory pattern 避免直接暴露 implementation type。
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., Any]] = {}
        self._instances: dict[str, Any] = {}

    def register(self, port_protocol: type[Any], factory: Callable[..., Any]) -> None:
        """注册 port protocol + factory。

        H2 type guard: 拒绝 inbound normalizer 类型注册。
        """
        port_name = port_protocol.__name__
        if port_name in _INBOUND_NORMALIZER_TYPE_NAMES:
            raise ValueError(
                f"CapabilityPortRegistry 拒绝注册 inbound normalizer 类型: {port_name}; "
                "inbound normalizer 不属于业务 capability (主计划 §3.5 I3 + H2)"
            )
        self._factories[port_name] = factory

    def get(self, port_protocol: type[Any]) -> Any:
        """获取 port 实例 (按需构造, 不暴露 implementation type)。"""
        port_name = port_protocol.__name__
        if port_name not in self._factories:
            raise KeyError(f"port {port_name} 未注册; 可用: {list(self._factories)}")
        if port_name not in self._instances:
            self._instances[port_name] = self._factories[port_name]()
        return self._instances[port_name]

    def list_registered(self) -> list[str]:
        """返回已注册 port 名称列表。"""
        return sorted(self._factories)

    def is_registered(self, port_protocol: type[Any]) -> bool:
        """检查 port 是否已注册。"""
        return port_protocol.__name__ in self._factories


class RuntimeCapabilityContext:
    """Runtime capability 注入上下文 (主计划 §3.5 + §9.2)。

    capability 只能拿到:
    - query_ports: 只读事实查询 port (WmsMasterDataPort / WmsInventoryQueryPort)
    - effect_ports: 出站副作用 port (WmsFulfillmentPort / WmsInventoryTransactionPort)

    capability 不能拿到:
    - inbound normalizer (WmsEventPort / DeviceEventPort / RuntimeInbox consumer)
    - HTTP client / service locator / DTO / provider exception
    """

    def __init__(self, registry: CapabilityPortRegistry) -> None:
        self._registry = registry

    def get_query_port(self, port_protocol: type[Any]) -> Any:
        """获取 query port (只读事实查询)。"""
        return self._registry.get(port_protocol)

    def get_effect_port(self, port_protocol: type[Any]) -> Any:
        """获取 effect port (出站副作用, 必须先写 RuntimeIntentLog)。"""
        return self._registry.get(port_protocol)
