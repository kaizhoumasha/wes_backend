#!/usr/bin/env python3
"""Redis 缓存客户端熔断状态人工演练。

本脚本只观察 Redis 缓存客户端状态，不访问业务 API 或数据库；业务层 fallback 需另行验收。
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def require_initial_cache_health(
    *,
    set_result: bool,
    cached_value: object,
    expected_value: object,
    delete_result: bool,
) -> None:
    """在人工停 Redis 前要求基础缓存读写全部成功。"""

    if not set_result:
        raise RuntimeError("初始 Redis 缓存写入失败")
    if cached_value != expected_value:
        raise RuntimeError("初始 Redis 缓存读取值不一致")
    if not delete_result:
        raise RuntimeError("初始 Redis 缓存删除失败")


def require_circuit_state(*, actual: str, expected: str, phase: str) -> None:
    """要求人工演练观察到指定熔断状态，否则明确失败。"""

    if actual != expected:
        raise RuntimeError(f"{phase}失败：预期熔断状态 {expected}，实际 {actual}")


async def attempt_cache_recovery(cache: Any, *, test_key: str, test_value: object) -> str:
    """用公开成功写操作推进 HALF_OPEN，并返回最终熔断状态。"""

    final_state = str(cache.get_status()["circuit_breaker_state"])
    for attempt in range(cache.circuit_breaker.half_open_max_calls):
        set_result = await cache.set(test_key, test_value)
        final_state = str(cache.get_status()["circuit_breaker_state"])
        print(f"尝试 #{attempt + 1}: 写入={'成功' if set_result else '失败'}, 状态={final_state}")
    return final_state


async def run_redis_degradation_drill() -> None:
    """运行 Redis 缓存客户端熔断状态人工演练。"""
    from src.database.redis_cache import CircuitState, get_cache

    print("=" * 60)
    print("Redis 缓存客户端熔断状态人工演练")
    print("仅验证缓存客户端状态；业务层数据库 fallback 需另行验收")
    print("=" * 60)
    print()

    # 获取缓存实例
    cache = get_cache()

    # 1. 测试正常情况
    print("1. 测试 Redis 正常情况")
    print("-" * 60)
    status = cache.get_status()
    print(f"缓存状态: {status}")
    print(f"可用性: {'✓ 正常' if status['available'] else '✗ 不可用'}")
    print(f"熔断器状态: {status['circuit_breaker_state']}")
    print()

    # 2. 测试缓存读取
    print("2. 测试缓存读取")
    print("-" * 60)
    test_key = "test:degradation"
    test_value = {"data": "test", "timestamp": 1234567890}

    # 设置缓存
    set_result = await cache.set(test_key, test_value)
    print(f"设置缓存: {'✓ 成功' if set_result else '✗ 失败'}")

    # 读取缓存
    cached_value = await cache.get(test_key)
    if cached_value:
        print(f"读取缓存: ✓ 成功, value={cached_value}")
    else:
        print("读取缓存: ✗ 失败或不存在")

    # 删除缓存
    delete_result = await cache.delete(test_key)
    print(f"删除缓存: {'✓ 成功' if delete_result else '✗ 失败'}")
    require_initial_cache_health(
        set_result=set_result,
        cached_value=cached_value,
        expected_value=test_value,
        delete_result=delete_result,
    )
    print("✓ 初始 Redis 缓存读写健康")
    print()

    # 3. 测试熔断器状态
    print("3. 测试熔断器机制")
    print("-" * 60)
    circuit_breaker = cache.circuit_breaker
    print(f"熔断器状态: {circuit_breaker.get_state().value}")
    print(f"失败计数: {circuit_breaker.failure_count}/{circuit_breaker.failure_threshold}")
    print(f"熔断超时: {circuit_breaker.timeout} 秒")
    print(f"半开最大尝试: {circuit_breaker.half_open_max_calls}")
    print()

    # 4. 模拟故障（需要手动停止 Redis）
    print("4. 模拟 Redis 故障")
    print("-" * 60)
    print("请手动停止 Redis 以测试降级功能:")
    print("  - Docker: docker stop <redis_container>")
    print("  - 本地: redis-cli shutdown")
    print()
    input("停止 Redis 后按回车继续...")  # noqa: ASYNC250 - 人工演练需等待运维操作

    # 5. 观察故障期间的缓存客户端状态
    print("\n5. 观察缓存客户端故障状态")
    print("-" * 60)

    # 连续尝试多次，触发熔断器
    for i in range(10):
        print(f"\n尝试 #{i + 1}:")
        cached_value = await cache.get(test_key)

        status = cache.get_status()
        cb_state = status["circuit_breaker_state"]

        if cb_state == CircuitState.CLOSED.value:
            print(f"  熔断器状态: {cb_state} (正常)")
        elif cb_state == CircuitState.OPEN.value:
            print(f"  熔断器状态: {cb_state} (已熔断，拒绝请求)")
        elif cb_state == CircuitState.HALF_OPEN.value:
            print(f"  熔断器状态: {cb_state} (半开，尝试恢复)")

        print(f"  失败计数: {status['failure_count']}/{status['failure_threshold']}")
        print(f"  缓存结果: {cached_value} (None 仅表示缓存客户端未返回值)")

        # 短暂延迟
        await asyncio.sleep(1)

    print()
    print("=" * 60)
    print("测试总结:")
    print("-" * 60)
    final_status = cache.get_status()
    print(f"最终熔断器状态: {final_status['circuit_breaker_state']}")
    print(f"最终失败计数: {final_status['failure_count']}")
    require_circuit_state(
        actual=final_status["circuit_breaker_state"],
        expected=CircuitState.OPEN.value,
        phase="Redis 故障观测",
    )
    print("✓ 已观察到 Redis 缓存客户端熔断器进入 OPEN")
    print("本演练未验证业务 API、数据库查询或业务层 fallback")

    print()
    print("=" * 60)

    # 6. 恢复测试
    print("\n6. 测试恢复")
    print("-" * 60)
    print("请重新启动 Redis:")
    print("  - Docker: docker start <redis_container>")
    print("  - 本地: redis-server")
    print()
    input("启动 Redis 后按回车继续...")  # noqa: ASYNC250 - 人工演练需等待运维操作

    # 等待熔断器进入半开状态
    print("\n等待熔断器超时...")
    await asyncio.sleep(cache.circuit_breaker.timeout + 2)

    # 使用成功写操作推进 HALF_OPEN，缓存 miss 不作为恢复成功信号。
    print("尝试恢复缓存客户端...")
    final_state = await attempt_cache_recovery(cache, test_key=test_key, test_value=test_value)
    require_circuit_state(
        actual=final_state,
        expected=CircuitState.CLOSED.value,
        phase="Redis 恢复观测",
    )
    print("\n✓ 已观察到 Redis 缓存客户端熔断器恢复为 CLOSED")
    delete_result = await cache.delete(test_key)
    print(f"清理演练缓存键: {'成功' if delete_result else '失败或不存在'}")

    print()
    print("=" * 60)
    print("演练完成")
    print("=" * 60)


def main() -> None:
    """校验人工入口并运行演练。"""

    if not sys.stdin.isatty():
        raise RuntimeError("Redis 缓存客户端人工演练需要交互式 TTY")
    asyncio.run(run_redis_degradation_drill())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n演练已中断")
        raise SystemExit(130) from None
