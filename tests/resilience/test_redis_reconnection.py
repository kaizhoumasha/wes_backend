#!/usr/bin/env python3
"""
Redis 自动重连测试脚本

演示 Redis 故障恢复时，系统如何自动重连并恢复缓存功能
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


async def test_redis_reconnection():
    """测试 Redis 自动重连"""
    from src.database.redis_cache import get_cache
    from src.database.redis_client import ensure_redis_connection

    print("=" * 60)
    print("Redis 自动重连测试")
    print("=" * 60)
    print()

    # 获取缓存实例
    cache = get_cache()

    # 1. 测试正常情况
    print("1. 测试当前状态")
    print("-" * 60)
    status = cache.get_status()
    print(f"缓存状态: {status}")
    print(f"Redis 可用: {cache.redis is not None}")
    print()

    # 2. 测试缓存操作
    print("2. 测试缓存操作")
    print("-" * 60)
    test_key = "test:reconnection"
    test_value = {"data": "test", "timestamp": 1234567890}

    # 设置缓存
    set_result = await cache.set(test_key, test_value)
    print(f"设置缓存: {'✓ 成功' if set_result else '✗ 失败（降级）'}")

    # 读取缓存
    cached_value = await cache.get(test_key)
    if cached_value:
        print(f"读取缓存: ✓ 成功, value={cached_value}")
    else:
        print("读取缓存: ✗ 失败或降级")
    print()

    # 3. 尝试触发重连
    print("3. 测试自动重连机制")
    print("-" * 60)
    print("调用 ensure_redis_connection()...")

    reconnected = await ensure_redis_connection()
    print(f"重连结果: {'✓ 成功' if reconnected else '✗ 失败'}")
    print()

    # 4. 再次测试缓存操作
    print("4. 重连后测试缓存操作")
    print("-" * 60)
    cached_value = await cache.get(test_key)
    if cached_value:
        print(f"读取缓存: ✓ 成功, value={cached_value}")
    else:
        print("读取缓存: ✗ 失败或降级")
    print()

    # 5. 显示最终状态
    print("5. 最终状态")
    print("-" * 60)
    status = cache.get_status()
    print(f"缓存状态: {status}")
    print(f"Redis 可用: {cache.redis is not None}")
    print(f"熔断器状态: {status['circuit_breaker_state']}")
    print()

    print("=" * 60)
    print("测试完成")
    print("=" * 60)
    print()
    print("提示：")
    print("1. 如果 Redis 当前不可用：")
    print("   - 启动 Redis: docker-compose start redis")
    print("   - 等待 30 秒后重运行此测试")
    print()
    print("2. 如果 Redis 当前可用：")
    print("   - 停止 Redis: docker-compose stop redis")
    print("   - 等待几秒后启动 Redis")
    print("   - 再次运行此测试，观察自动重连")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(test_redis_reconnection())
    except KeyboardInterrupt:
        print("\n\n测试已中断")
