#!/usr/bin/env python3
"""
Redis 故障降级测试脚本

演示当 Redis 不可用时，系统如何自动降级到直接查询数据库
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


pytestmark = [
    pytest.mark.live,
    pytest.mark.manual,
    pytest.mark.skipif(
        os.getenv("RUN_REDIS_DEGRADATION") != "1" or not sys.stdin.isatty(),
        reason="manual resilience drill; requires interactive TTY and explicit opt-in",
    ),
]


async def test_redis_degradation():
    """测试 Redis 故障降级"""
    from src.database.redis_cache import CircuitState, get_cache

    print("=" * 60)
    print("Redis 故障降级测试")
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
    input("停止 Redis 后按回车继续...")

    # 5. 测试降级行为
    print("\n5. 测试降级行为")
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
        print(f"  缓存结果: {cached_value} (None 表示降级)")

        # 短暂延迟
        await asyncio.sleep(1)

    print()
    print("=" * 60)
    print("测试总结:")
    print("-" * 60)
    final_status = cache.get_status()
    print(f"最终熔断器状态: {final_status['circuit_breaker_state']}")
    print(f"最终失败计数: {final_status['failure_count']}")

    if final_status["circuit_breaker_state"] == CircuitState.OPEN.value:
        print()
        print("✓ 熔断器已打开，系统自动降级")
        print("✓ 所有请求绕过缓存，直接查询数据库")
        print("✓ 避免 Redis 故障影响主业务")

    print()
    print("=" * 60)

    # 6. 恢复测试
    print("\n6. 测试恢复")
    print("-" * 60)
    print("请重新启动 Redis:")
    print("  - Docker: docker start <redis_container>")
    print("  - 本地: redis-server")
    print()
    input("启动 Redis 后按回车继续...")

    # 等待熔断器进入半开状态
    print("\n等待熔断器超时...")
    await asyncio.sleep(cache.circuit_breaker.timeout + 2)

    # 尝试恢复
    print("尝试恢复缓存服务...")
    for i in range(5):
        cached_value = await cache.get(test_key)
        status = cache.get_status()
        print(f"尝试 #{i + 1}: 状态={status['circuit_breaker_state']}, 值={cached_value}")
        await asyncio.sleep(2)

    final_status = cache.get_status()
    if final_status["circuit_breaker_state"] == CircuitState.CLOSED.value:
        print("\n✓ 缓存服务已恢复正常")
    else:
        print(f"\n当前状态: {final_status['circuit_breaker_state']}")

    print()
    print("=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(test_redis_degradation())
    except KeyboardInterrupt:
        print("\n\n测试已中断")
