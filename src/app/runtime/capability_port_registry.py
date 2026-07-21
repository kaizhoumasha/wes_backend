"""CapabilityPortRegistry（主计划 §3.5 + §9.2）。

capability 注入边界: 只允许 query/effect port contract 注册;
inbound normalizer (WmsEventPort / DeviceEventPort / RuntimeInbox)
被 type guard 拒绝, 不能进入业务 capability。

factory pattern: register(port_protocol, factory) 按需构造, 不直接暴露
implementation type。capability 不持有底层 session/db client。

Inbound normalizer 不挂到通用 RuntimeCapabilityContext 上。
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

# H2: inbound normalizer 类型清单 — 注册表 type guard 拒绝这些类型
_INBOUND_NORMALIZER_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "WmsEventPort",
        "DeviceEventPort",
        "InboundEventPort",
        "RuntimeInbox",
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
        self._cache_instance: dict[str, bool] = {}

    def register(
        self,
        port_protocol: type[Any],
        factory: Callable[..., Any],
        *,
        cache_instance: bool = False,
    ) -> None:
        """注册 port protocol + factory。

        H2 type guard: 拒绝 inbound normalizer 类型注册。

        默认不在 registry 缓存实例，避免把绑定 AsyncSession 的 service
        泄漏到下一个 Inbox attempt。需要复用的纯 client 必须显式选择缓存。
        """
        port_name = port_protocol.__name__
        if port_name in _INBOUND_NORMALIZER_TYPE_NAMES:
            raise ValueError(
                f"CapabilityPortRegistry 拒绝注册 inbound normalizer 类型: {port_name}; "
                "inbound normalizer 不属于业务 capability (主计划 §3.5 I3 + H2)"
            )
        self._factories[port_name] = factory
        self._cache_instance[port_name] = cache_instance
        self._instances.pop(port_name, None)

    def get(self, port_protocol: type[Any]) -> Any:
        """获取 port 实例 (按需构造, 不暴露 implementation type)。"""
        port_name = port_protocol.__name__
        if port_name not in self._factories:
            raise KeyError(f"port {port_name} 未注册; 可用: {list(self._factories)}")
        if not self._cache_instance.get(port_name, False):
            return _validate_async_protocol_contract(port_protocol, self._factories[port_name]())
        if port_name not in self._instances:
            self._instances[port_name] = _validate_async_protocol_contract(
                port_protocol,
                self._factories[port_name](),
            )
        return self._instances[port_name]

    def fork_attempt(self) -> CapabilityPortRegistry:
        """复制 factory 声明但不复制实例，形成 attempt-scoped registry。"""

        registry = CapabilityPortRegistry()
        for port_name, factory in self._factories.items():
            registry._factories[port_name] = factory
            registry._cache_instance[port_name] = False
        return registry

    def list_registered(self) -> list[str]:
        """返回已注册 port 名称列表。"""
        return sorted(self._factories)

    def is_registered(self, port_protocol: type[Any]) -> bool:
        """检查 port 是否已注册。"""
        return port_protocol.__name__ in self._factories


def _validate_async_protocol_contract(port_protocol: type[Any], instance: Any) -> Any:
    """异步 Protocol 方法必须由 async implementation 实现，阻断旧同步 fake。"""

    for method_name, protocol_method in vars(port_protocol).items():
        if method_name.startswith("_") or not inspect.iscoroutinefunction(protocol_method):
            continue
        implementation = getattr(instance, method_name, None)
        if not inspect.iscoroutinefunction(implementation):
            raise TypeError(f"{port_protocol.__name__}.{method_name} requires async Port contract implementation")
    return instance


class RuntimeCapabilityContext:
    """Runtime capability 注入上下文 (主计划 §3.5 + §9.2)。

    capability 只能拿到:
    - query_ports: 只读事实查询 port (WmsMasterDataPort / typed operation query port)
    - effect_ports: 出站副作用 port (WmsFulfillmentPort / WmsInventoryTransactionPort)

    capability 不能拿到:
    - inbound normalizer (WmsEventPort / DeviceEventPort / RuntimeInbox)
    - HTTP client / service locator / DTO / provider exception
    """

    def __init__(
        self,
        registry: CapabilityPortRegistry,
        *,
        allowed_query_capabilities: Iterable[str] | None = None,
        allowed_effect_capabilities: Iterable[str] | None = None,
    ) -> None:
        self._registry = registry
        self._allowed_query_capabilities = _normalize_capability_set(allowed_query_capabilities)
        self._allowed_effect_capabilities = _normalize_capability_set(allowed_effect_capabilities)
        # Context 本身按 attempt 创建；只在该 attempt 内复用 Port proxy。
        self._attempt_ports: dict[tuple[str, str], Any] = {}

    @classmethod
    def from_provider_profile(cls, registry: CapabilityPortRegistry, profile: Any) -> RuntimeCapabilityContext:
        """按 ExternalContractProfile 创建受限 capability context。"""

        return cls(
            registry,
            allowed_query_capabilities=getattr(profile, "runtime_capabilities_query", ()),
            allowed_effect_capabilities=getattr(profile, "runtime_capabilities_effect", ()),
        )

    def get_query_port(self, port_protocol: type[Any]) -> Any:
        """获取 query port (只读事实查询)。"""
        return self._get_restricted_port(port_protocol, direction="query")

    def get_effect_port(self, port_protocol: type[Any]) -> Any:
        """获取 effect port (出站副作用, 必须先写 RuntimeIntentLog)。"""
        return self._get_restricted_port(port_protocol, direction="effect")

    def require_query_ports(self, port_protocols: Iterable[type[Any]]) -> None:
        """Fail-fast 校验 Definition 声明的所有 query Port。"""

        for port_protocol in port_protocols:
            self.get_query_port(port_protocol)

    def _get_restricted_port(self, port_protocol: type[Any], *, direction: str) -> Any:
        port_name = port_protocol.__name__
        cache_key = (direction, port_name)
        if cache_key in self._attempt_ports:
            return self._attempt_ports[cache_key]
        allowed = self._allowed_query_capabilities if direction == "query" else self._allowed_effect_capabilities
        if allowed is None:
            port = self._registry.get(port_protocol)
            self._attempt_ports[cache_key] = port
            return port
        allowed_methods = frozenset(
            capability.split(".", maxsplit=1)[1] for capability in allowed if capability.startswith(f"{port_name}.")
        )
        if not allowed_methods:
            raise PermissionError(f"provider 未声明 {direction} capability: {port_name}")
        port = self._registry.get(port_protocol)
        restricted_port = _RestrictedCapabilityPort(
            port,
            port_name=port_name,
            allowed_methods=allowed_methods,
            direction=direction,
        )
        self._attempt_ports[cache_key] = restricted_port
        return restricted_port


def _normalize_capability_set(capabilities: Iterable[str] | None) -> frozenset[str] | None:
    """None 表示沿用未受 provider profile 限制的旧 wiring；空集合表示显式拒绝全部。"""

    if capabilities is None:
        return None
    return frozenset(capability for capability in capabilities if capability)


class _RestrictedCapabilityPort:
    """只暴露 provider profile 已声明的 Port.method。"""

    def __init__(self, target: Any, *, port_name: str, allowed_methods: frozenset[str], direction: str) -> None:
        self._target = target
        self._port_name = port_name
        self._allowed_methods = allowed_methods
        self._direction = direction

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name not in self._allowed_methods:
            raise PermissionError(f"provider 未声明 {self._direction} capability: {self._port_name}.{name}")
        return getattr(self._target, name)
