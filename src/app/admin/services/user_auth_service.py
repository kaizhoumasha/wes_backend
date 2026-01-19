"""
用户认证服务（User Auth Service）

负责用户认证相关的业务逻辑，主要是密码哈希和验证。

职责：
1. 密码哈希（使用 Argon2）
2. 密码验证
3. 异步密码操作（使用线程池避免阻塞）

分离原因：
- 单一职责原则：只负责认证逻辑
- 便于测试：可以独立测试密码逻辑
- 性能优化：使用线程池处理 CPU 密集型操作
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from pwdlib import PasswordHash

from src.core.logger import logger


class PasswordHasher:
    """
    密码哈希服务类

    使用 pwdlib（FastAPI 官方推荐）支持现代密码哈希算法（Argon2）。
    """

    def __init__(self):
        # pwdlib - FastAPI 官方推荐，支持现代密码哈希算法（Argon2）
        self._hasher = PasswordHash.recommended()

        # 线程池用于 CPU 密集型操作（密码哈希）
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="password_hash")

    async def hash_async(self, password: str) -> str:
        """
        异步哈希密码

        使用 ThreadPoolExecutor 在独立线程中执行 CPU 密集型的密码哈希操作，
        避免阻塞事件循环，提升并发性能。

        Args:
            password: 明文密码

        Returns:
            哈希后的密码

        Reference:
            https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._hasher.hash, password)

    async def verify_async(self, plain_password: str, hashed_password: str) -> bool:
        """
        异步验证密码

        使用 ThreadPoolExecutor 在独立线程中执行密码验证，
        避免阻塞事件循环。

        Args:
            plain_password: 明文密码
            hashed_password: 哈希后的密码

        Returns:
            验证是否成功
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._hasher.verify, plain_password, hashed_password)

    def hash(self, password: str) -> str:
        """
        同步哈希密码

        Args:
            password: 明文密码

        Returns:
            哈希后的密码

        Note:
            这是同步方法，通常建议使用 hash_async 以获得更好的性能。
        """
        logger.warning("使用了同步密码哈希方法，建议使用 hash_async")
        return self._hasher.hash(password)

    def verify(self, plain_password: str, hashed_password: str) -> bool:
        """
        同步验证密码

        Args:
            plain_password: 明文密码
            hashed_password: 哈希后的密码

        Returns:
            验证是否成功

        Note:
            这是同步方法，通常建议使用 verify_async 以获得更好的性能。
        """
        logger.warning("使用了同步密码验证方法，建议使用 verify_async")
        return self._hasher.verify(plain_password, hashed_password)


# 单例模式的密码哈希服务
password_hasher = PasswordHasher()


__all__ = ["PasswordHasher", "password_hasher"]
