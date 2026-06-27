"""InboundNormalizerRegistry (Phase 1 CEO-009 / Packet D, 主计划 §3.5.1 + H2)。

入站事件 normalizer 注册表 (WmsEventPort / DeviceEventPort 等),
与 CapabilityPortRegistry 严格分离: 注册的 normalizer 不可注入业务
capability 上下文 (主计划 §3.5.1 + H2 黑名单); 只允许
RuntimeInboxConsumer 通过 RuntimeCapabilityContext.get_inbound_normalizer()
访问, 调用方模块路径必须命中 allowlist。

设计:
- factory pattern (与 CapabilityPortRegistry 对齐), 避免直接暴露 implementation type
- singleton per-port (多次 get 同一 port 只 factory() 一次)
- 不重复 H2 type guard (本 registry 本身就是 inbound normalizer 合法归宿)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class InboundNormalizerRegistry:
    """入站 normalizer 注册表 (主计划 §3.5.1 + Phase 1 CEO-009 / H2)。

    与 CapabilityPortRegistry 严格分离: 注册的 normalizer 不可注入业务
    capability 上下文; 只允许 RuntimeInboxConsumer 通过
    RuntimeCapabilityContext.get_inbound_normalizer() 获取。
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., Any]] = {}
        self._instances: dict[str, Any] = {}

    def register(self, port_protocol: type[Any], factory: Callable[..., Any]) -> None:
        """注册 inbound normalizer port protocol + factory。"""
        port_name = port_protocol.__name__
        self._factories[port_name] = factory

    def get(self, port_protocol: type[Any]) -> Any:
        """获取 normalizer instance (按需构造, singleton)。"""
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
