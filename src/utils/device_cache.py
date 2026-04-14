"""
工作线设备缓存

提供工作线设备列表的缓存，带有过期、穿透、击穿保护。
"""

import asyncio
import time
from typing import Any

from src.core.logger import logger


class WorklineDeviceCache:
    """工作线设备缓存 - 带过期、穿透、击穿保护

    缓存策略：
    - 容量限制: 最多缓存 100 个工作线（LRU 淘汰）
    - 过期 (TTL): 正常缓存默认 60 秒
    - 穿透保护: 空结果缓存 10 秒
    - 击穿保护: 单flight 模式防止并发查询
    """

    DEFAULT_MAXSIZE = 100

    def __init__(self, ttl: int = 60, null_ttl: int = 10, maxsize: int = DEFAULT_MAXSIZE) -> None:
        """
        Args:
            ttl: 正常缓存有效期（秒）
            null_ttl: 空结果缓存有效期（秒）- 防止穿透
            maxsize: 最大缓存条目数（超过后 LRU 淘汰）
        """
        self._cache: dict[int, tuple[list[Any], float]] = {}
        self._null_cache: dict[int, float] = {}  # 空结果缓存
        self._inflight: dict[int, asyncio.Lock] = {}  # 单flight 锁
        self._ttl = ttl
        self._null_ttl = null_ttl
        self._maxsize = maxsize

    async def get_devices(
        self,
        db: Any,
        workline_id: int,
        fetch_func: Any = None,
    ) -> list[Any] | None:
        """
        获取工作线设备（带缓存）

        Args:
            db: 数据库会话
            workline_id: 工作线 ID
            fetch_func: 获取设备的 async 函数，如果未提供则返回缓存

        Returns:
            设备列表 或 None（不存在）
        """
        now = time.time()

        # 1. 检查正常缓存
        if workline_id in self._cache:
            devices, expire_at = self._cache[workline_id]
            if now < expire_at:
                logger.debug(f"工作线 {workline_id} 设备缓存命中")
                return devices
            # 缓存已过期，删除
            del self._cache[workline_id]

        # 2. 检查空结果缓存（穿透保护）
        if workline_id in self._null_cache:
            null_expire_at = self._null_cache[workline_id]
            if now < null_expire_at:
                logger.debug(f"工作线 {workline_id} 空结果缓存命中")
                return None
            # 空结果缓存已过期，删除
            del self._null_cache[workline_id]

        # 如果没有提供 fetch_func，直接返回 None（需要查询）
        if fetch_func is None:
            return None

        # 3. 单flight 防止击穿
        if workline_id not in self._inflight:
            self._inflight[workline_id] = asyncio.Lock()

        async with self._inflight[workline_id]:
            try:
                # 双重检查（可能有其他请求刚更新了缓存）
                if workline_id in self._cache:
                    devices, expire_at = self._cache[workline_id]
                    if now < expire_at:
                        return devices
                    del self._cache[workline_id]

                # 4. 调用 fetch_func 获取数据
                devices = await fetch_func(db, workline_id)

                # 5. 更新缓存（超过容量时 LRU 淘汰）
                if len(self._cache) >= self._maxsize and workline_id not in self._cache:
                    # 淘汰最早的一个
                    oldest_key = next(iter(self._cache))
                    del self._cache[oldest_key]
                    logger.debug(f"缓存已满，淘汰工作线 {oldest_key}")

                if devices:
                    self._cache[workline_id] = (devices, now + self._ttl)
                    logger.debug(f"工作线 {workline_id} 设备已缓存 ({len(devices)} 个)")
                else:
                    self._null_cache[workline_id] = now + self._null_ttl
                    logger.debug(f"工作线 {workline_id} 空结果已缓存")

                return devices if devices else None
            except Exception as e:
                logger.error(f"获取工作线 {workline_id} 设备失败: {e}")
                return None
            finally:
                # 清理 inflight 锁，避免内存泄漏
                _ = self._inflight.pop(workline_id, None)

    def invalidate(self, workline_id: int) -> None:
        """主动失效指定工作线的缓存"""
        self._cache.pop(workline_id, None)  # type: ignore[reportUnusedCallResult]
        self._null_cache.pop(workline_id, None)  # type: ignore[reportUnusedCallResult]
        logger.debug(f"工作线 {workline_id} 缓存已失效")

    def invalidate_all(self) -> None:
        """清空所有缓存"""
        self._cache.clear()
        self._null_cache.clear()
        logger.info("所有工作线设备缓存已清空")

    def get_stats(self) -> dict[str, int]:
        """获取缓存统计信息"""
        return {
            "cached_count": len(self._cache),
            "null_cached_count": len(self._null_cache),
            "inflight_count": len(self._inflight),
        }


# 全局实例
workline_device_cache = WorklineDeviceCache(ttl=60, null_ttl=10)


__all__ = ["WorklineDeviceCache", "workline_device_cache"]
