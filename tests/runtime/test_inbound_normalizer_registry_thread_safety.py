"""InboundNormalizerRegistry thread-safety 单测。

覆盖场景:
1. 单线程 sequential get() 同一 port → singleton 行为正确
2. 单线程 sequential get() 不同 port → instance 唯一性正确
3. 多线程并发 get() 同一 port → 必须返回同一 instance (无 race)
4. 多线程并发 get() 不同 port → 各自 instance 唯一,无串扰
5. fast path 与 slow path 行为一致 (cache hit vs miss)
6. get() 未注册 port → KeyError
7. 100 并发线程同时首次 get() 同一 port → factory 只调用一次 (DCL 验证)
8. 不同 registry 实例互相隔离
"""

from __future__ import annotations

import threading

import pytest

from src.app.runtime.inbound_normalizer_registry import InboundNormalizerRegistry


class _FakePort:
    """Protocol-like stub for testing."""


class _AnotherFakePort:
    """Another Protocol-like stub for testing."""


class _CountingFactory:
    """Factory that increments a counter to detect duplicate construction."""

    def __init__(self) -> None:
        self.call_count = 0
        self._lock = threading.Lock()

    def __call__(self) -> object:
        with self._lock:
            self.call_count += 1
        return object()  # 每个调用返回不同 instance,以验证 singleton 是否被破坏


def test_single_thread_get_returns_same_instance_for_same_port() -> None:
    """Sequential get() on same port must return the same instance."""
    registry = InboundNormalizerRegistry()
    factory = _CountingFactory()
    registry.register(_FakePort, factory)

    instance_1 = registry.get(_FakePort)
    instance_2 = registry.get(_FakePort)

    assert instance_1 is instance_2
    assert factory.call_count == 1


def test_single_thread_get_returns_distinct_instances_for_distinct_ports() -> None:
    """Sequential get() on distinct ports must return distinct instances."""
    registry = InboundNormalizerRegistry()
    factory_a = _CountingFactory()
    factory_b = _CountingFactory()

    registry.register(_FakePort, factory_a)
    registry.register(_AnotherFakePort, factory_b)

    instance_a = registry.get(_FakePort)
    instance_b = registry.get(_AnotherFakePort)

    assert instance_a is not instance_b
    assert factory_a.call_count == 1
    assert factory_b.call_count == 1


def test_get_unregistered_port_raises_key_error() -> None:
    """get() on an unregistered port must raise KeyError with port name in message."""
    registry = InboundNormalizerRegistry()

    with pytest.raises(KeyError, match="_FakePort"):
        registry.get(_FakePort)


def test_concurrent_get_same_port_returns_same_instance() -> None:
    """Concurrent get() on same port must return the same singleton instance.

    多调用方并发 get() 同一 port 时,double-check locking 必须保证只调用 factory() 一次。
    """
    registry = InboundNormalizerRegistry()
    factory = _CountingFactory()
    registry.register(_FakePort, factory)

    results: list[object] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(100)

    def worker() -> None:
        try:
            barrier.wait(timeout=5.0)
            instance = registry.get(_FakePort)
            results.append(instance)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"线程异常: {errors}"
    assert len(results) == 100
    first = results[0]
    assert all(instance is first for instance in results), "并发 get() 返回不同 instance"
    assert factory.call_count == 1, f"factory 被调用 {factory.call_count} 次,应为 1 次"


def test_concurrent_get_distinct_ports_no_cross_contamination() -> None:
    """Concurrent get() on distinct ports must not cross-contaminate instances."""
    registry = InboundNormalizerRegistry()
    factory_a = _CountingFactory()
    factory_b = _CountingFactory()

    registry.register(_FakePort, factory_a)
    registry.register(_AnotherFakePort, factory_b)

    results_a: list[object] = []
    results_b: list[object] = []
    barrier = threading.Barrier(100)
    errors: list[BaseException] = []

    def worker_a() -> None:
        try:
            barrier.wait(timeout=5.0)
            results_a.append(registry.get(_FakePort))
        except BaseException as exc:
            errors.append(exc)

    def worker_b() -> None:
        try:
            barrier.wait(timeout=5.0)
            results_b.append(registry.get(_AnotherFakePort))
        except BaseException as exc:
            errors.append(exc)

    threads = []
    for _ in range(50):
        threads.append(threading.Thread(target=worker_a))
        threads.append(threading.Thread(target=worker_b))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"线程异常: {errors}"
    assert len(results_a) == 50
    assert len(results_b) == 50
    # singleton 一致性
    assert all(instance is results_a[0] for instance in results_a)
    assert all(instance is results_b[0] for instance in results_b)
    assert results_a[0] is not results_b[0]
    # 各 factory 只被调用一次
    assert factory_a.call_count == 1
    assert factory_b.call_count == 1


def test_fast_path_returns_same_instance_as_slow_path() -> None:
    """Fast path (cache hit) 与 slow path (first construction) 返回 instance 必须一致。"""
    registry = InboundNormalizerRegistry()
    factory = _CountingFactory()
    registry.register(_FakePort, factory)

    # 第一次 get() 走 slow path
    first = registry.get(_FakePort)
    assert factory.call_count == 1

    # 后续 get() 都走 fast path
    for _ in range(10):
        cached = registry.get(_FakePort)
        assert cached is first
    assert factory.call_count == 1, "fast path 不应触发 factory 调用"


def test_registry_is_isolated_per_instance() -> None:
    """两个独立 registry 实例必须互不影响 (Class-level lock 不污染 instance state)。"""
    registry_a = InboundNormalizerRegistry()
    registry_b = InboundNormalizerRegistry()

    factory_a = _CountingFactory()
    factory_b = _CountingFactory()

    registry_a.register(_FakePort, factory_a)
    registry_b.register(_FakePort, factory_b)

    instance_a = registry_a.get(_FakePort)
    instance_b = registry_b.get(_FakePort)

    assert instance_a is not instance_b
    assert factory_a.call_count == 1
    assert factory_b.call_count == 1


def test_list_registered_and_is_registered() -> None:
    """list_registered() 与 is_registered() 不受 thread-safety 修复影响,行为不变。"""
    registry = InboundNormalizerRegistry()
    registry.register(_FakePort, lambda: object())

    assert registry.is_registered(_FakePort)
    assert not registry.is_registered(_AnotherFakePort)
    assert registry.list_registered() == ["_FakePort"]
