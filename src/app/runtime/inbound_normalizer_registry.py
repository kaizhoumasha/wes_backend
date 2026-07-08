"""InboundNormalizerRegistry（主计划 §3.5.1 + H2）。

入站事件 normalizer 注册表 (WmsEventPort / DeviceEventPort 等),
与 CapabilityPortRegistry 严格分离: 注册的 normalizer 不可注入业务
capability 上下文 (主计划 §3.5.1 + H2 黑名单); 只允许
RuntimeInboxConsumer 通过专用 InboundNormalizerContext 访问。

设计:
- factory pattern (与 CapabilityPortRegistry 对齐), 避免直接暴露 implementation type
- singleton per-port (多次 get 同一 port 只 factory() 一次)
- thread-safe lazy initialization: instance 创建受 class-level lock 保护,
  多个 RuntimeInboxConsumer worker 并发 get() 同一 port 不会重复构造实例
- 不重复 H2 type guard (本 registry 本身就是 inbound normalizer 合法归宿)

并发安全修复:
原 _instances: dict[str, Any] 单例 cache 在 async consumer 并发 get() 时
存在 race condition (TOCTOU between `if not in _instances` 与赋值)。
修复:double-check locking 模式,加 class-level threading.Lock。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


class InboundNormalizerRegistry:
    """入站 normalizer 注册表（主计划 §3.5.1 + H2）。

    与 CapabilityPortRegistry 严格分离: 注册的 normalizer 不可注入业务
    capability 上下文; 只允许 RuntimeInboxConsumer 通过
    InboundNormalizerContext 获取。
    """

    # Class-level lock 保护所有 instance 的 _instances 缓存初始化,
    # 避免多个 RuntimeInboxConsumer worker 同时首次 get() 同一 port 时
    # 重复调用 factory()。粒度 = class 级,牺牲一些并发性换取实现简洁性
    # (registry 操作频率远低于业务请求,单锁开销可忽略)。
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., Any]] = {}
        self._instances: dict[str, Any] = {}

    def register(self, port_protocol: type[Any], factory: Callable[..., Any]) -> None:
        """注册 inbound normalizer port protocol + factory。"""
        port_name = port_protocol.__name__
        self._factories[port_name] = factory

    def get(self, port_protocol: type[Any]) -> Any:
        """获取 normalizer instance (按需构造, singleton, thread-safe)。

        Double-check locking 模式:
        - 第一次 fast path 检查 (无锁),避免热路径上加锁开销
        - 仅当 instance 缺失时,才进入 slow path 加 class-level lock
        - lock 内再次检查 (double-check),防止 TOCTOU 重复构造
        """
        port_name = port_protocol.__name__
        if port_name not in self._factories:
            raise KeyError(f"port {port_name} 未注册; 可用: {list(self._factories)}")
        # Fast path: 实例已存在,直接返回,避免锁开销。
        instance = self._instances.get(port_name)
        if instance is not None:
            return instance
        # Slow path: 加 class-level lock 保护首次构造。
        with self._lock:
            # Double-check:其他线程可能在我们等待锁时已完成构造。
            instance = self._instances.get(port_name)
            if instance is None:
                instance = self._factories[port_name]()
                self._instances[port_name] = instance
        return instance

    def list_registered(self) -> list[str]:
        """返回已注册 port 名称列表。"""
        return sorted(self._factories)

    def is_registered(self, port_protocol: type[Any]) -> bool:
        """检查 port 是否已注册。"""
        return port_protocol.__name__ in self._factories
