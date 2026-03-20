"""
API 性能基准测试

使用方法：
pytest tests/benchmark/test_api_performance.py --benchmark-json=benchmark.json
"""

import asyncio
import time

import pytest
from httpx import AsyncClient, Client
from redis import Redis as SyncRedis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.conf import settings

# 基准测试配置
BASE_URL = "http://localhost:8001"


@pytest.mark.benchmark()
class TestAPIPerformance:
    """API 性能基准测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """测试前准备"""
        self.client = Client(base_url=BASE_URL)
        yield
        self.client.close()

    def test_health_check(self, benchmark):
        """健康检查性能"""

        def _health_check():
            response = self.client.get("/api/v1/performance/health")
            assert response.status_code == 200
            return response

        _ = benchmark.pedantic(_health_check, iterations=100, rounds=10)

    def test_get_user_list(self, benchmark):
        """获取用户列表性能"""

        def _get_list():
            response = self.client.post("/api/v1/users/query", json={"offset": 0, "limit": 10})
            assert response.status_code in [200, 401, 403]
            return response

        _ = benchmark.pedantic(_get_list, iterations=50, rounds=5)

    def test_get_user_detail(self, benchmark):
        """获取用户详情性能"""

        def _get_detail():
            response = self.client.get("/api/v1/users/1")
            assert response.status_code in [200, 401, 403, 404]
            return response

        _ = benchmark.pedantic(_get_detail, iterations=100, rounds=10)


@pytest.mark.benchmark()
class TestDatabasePerformance:
    """数据库性能基准测试"""

    def test_db_query_performance(self, benchmark):
        """数据库查询性能"""

        async def _db_query():
            engine = create_async_engine(settings.DATABASE_URL)
            async with engine.begin() as conn:
                result = await conn.execute(text("SELECT 1"))
                result.fetchone()
            await engine.dispose()

        _ = benchmark.pedantic(lambda: asyncio.run(_db_query()), iterations=100, rounds=10)


@pytest.mark.benchmark()
class TestCachePerformance:
    """缓存性能基准测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """测试前准备"""
        self.redis_client = SyncRedis.from_url(settings.REDIS_URL, db=0, decode_responses=True)
        try:
            self.redis_client.ping()
        except Exception as exc:
            pytest.skip(f"Redis unavailable for benchmark: {exc}")
        yield
        self.redis_client.close()

    def test_redis_get_performance(self, benchmark):
        """Redis GET 性能"""

        def _redis_get():
            self.redis_client.get("test_key")
            return True

        _ = benchmark.pedantic(_redis_get, iterations=1000, rounds=10)

    def test_redis_set_performance(self, benchmark):
        """Redis SET 性能"""

        def _redis_set():
            self.redis_client.setex(f"bench_test_{time.time()}", 60, "benchmark_value")
            return True

        _ = benchmark.pedantic(_redis_set, iterations=1000, rounds=10)


@pytest.mark.benchmark()
class TestConcurrentRequests:
    """并发请求性能测试"""

    @pytest.fixture(autouse=True)
    async def setup(self):
        """测试前准备"""
        self.client = AsyncClient(base_url=BASE_URL)
        yield
        await self.client.aclose()

    async def test_concurrent_get_requests(self):
        """测试并发 GET 请求"""

        async def _make_request(client, url):
            start = time.time()
            response = await client.get(url)
            elapsed = time.time() - start
            return elapsed, response.status_code

        # 测试不同并发级别
        for concurrency in [10, 50, 100]:
            start = time.time()

            tasks = [_make_request(self.client, "/api/v1/users") for _ in range(concurrency)]

            results = await asyncio.gather(*tasks)
            elapsed_times = [r[0] for r in results]
            status_codes = [r[1] for r in results]

            total_time = time.time() - start
            success_rate = sum(1 for s in status_codes if s == 200) / len(status_codes)

            print(f"\n并发级别: {concurrency}")
            print(f"  总耗时: {total_time:.2f}s")
            print(f"  成功率: {success_rate:.2%}")
            print(f"  平均响应时间: {sum(elapsed_times) / len(elapsed_times) * 1000:.2f}ms")
            print(f"  最大响应时间: {max(elapsed_times) * 1000:.2f}ms")
            print(f"  最小响应时间: {min(elapsed_times) * 1000:.2f}ms")
            print(f"  RPS: {concurrency / total_time:.2f}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
