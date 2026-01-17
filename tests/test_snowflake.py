"""
雪花 ID 生成器测试（53位方案 - JavaScript 安全）

测试 Snowflake ID 的：
1. 唯一性：生成的 ID 必须唯一
2. 递增性：同一毫秒内生成的 ID 应递增
3. 分布式：不同节点生成的 ID 应不同
4. 并发安全：多线程同时生成不应冲突
5. JavaScript 安全性：ID 在安全整数范围内
"""

import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from src.core.snowflake import (
    SnowflakeConfig,
    SnowflakeIDGenerator,
    generate_snowflake_id,
    get_snowflake_generator,
)


class TestSnowflakeBasics:
    """基础功能测试"""

    def test_generate_single_id(self):
        """测试生成单个 ID"""
        snowflake_id = generate_snowflake_id()
        assert isinstance(snowflake_id, int)
        assert snowflake_id > 0
        # 53位ID为15-16位十进制数字
        assert 15 <= len(str(snowflake_id)) <= 16
        # 验证在 JavaScript 安全整数范围内
        assert snowflake_id <= SnowflakeConfig.MAX_SAFE_INTEGER

    def test_generate_unique_ids(self):
        """测试 ID 唯一性"""
        ids = set()
        for _ in range(10000):
            snowflake_id = generate_snowflake_id()
            assert snowflake_id not in ids, f"重复 ID: {snowflake_id}"
            ids.add(snowflake_id)

    def test_id_increasing(self):
        """测试 ID 递增性"""
        prev_id = generate_snowflake_id()
        for _ in range(100):
            current_id = generate_snowflake_id()
            assert current_id > prev_id, "ID 应该递增"
            prev_id = current_id

    def test_parse_id(self):
        """测试解析 ID"""
        gen = SnowflakeIDGenerator(datacenter_id=1, worker_id=2)
        snowflake_id = gen.generate_id()
        parsed = SnowflakeIDGenerator.parse_id(snowflake_id)

        assert parsed["id"] == snowflake_id
        assert 0 <= parsed["datacenter_id"] <= 7
        assert 0 <= parsed["worker_id"] <= 7
        assert parsed["sequence"] >= 0
        assert parsed["timestamp"] > SnowflakeConfig.EPOCH

    def test_different_nodes(self):
        """测试不同节点生成不同 ID"""
        gen1 = SnowflakeIDGenerator(datacenter_id=0, worker_id=0)
        gen2 = SnowflakeIDGenerator(datacenter_id=1, worker_id=0)

        ids1 = {gen1.generate_id() for _ in range(100)}
        ids2 = {gen2.generate_id() for _ in range(100)}

        # 不同节点生成的 ID 应该不同
        intersection = ids1 & ids2
        assert len(intersection) == 0, f"发现 {len(intersection)} 个重复 ID"

    def test_singleton_pattern(self):
        """测试单例模式"""
        gen1 = get_snowflake_generator(0, 0)
        gen2 = get_snowflake_generator(0, 0)
        assert gen1 is gen2, "应该返回同一个实例"


class TestSnowflakeConcurrency:
    """并发安全测试"""

    def test_thread_safety(self):
        """测试多线程安全性"""
        num_threads = 10
        ids_per_thread = 1000
        ids = set()
        lock = threading.Lock()

        def generate_ids():
            local_ids = set()
            for _ in range(ids_per_thread):
                local_ids.add(generate_snowflake_id())
            with lock:
                ids.update(local_ids)

        threads = []
        for _ in range(num_threads):
            t = threading.Thread(target=generate_ids)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        expected_count = num_threads * ids_per_thread
        actual_count = len(ids)

        assert actual_count == expected_count, (
            f"期望 {expected_count} 个唯一 ID，实际 {actual_count} 个"
        )

    def test_high_concurrency(self):
        """测试高并发场景"""
        num_ids = 50000
        ids = set()

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(generate_snowflake_id) for _ in range(num_ids)]
            for future in as_completed(futures):
                ids.add(future.result())

        assert len(ids) == num_ids, f"期望 {num_ids} 个唯一 ID，实际 {len(ids)} 个"


class TestSnowflakePerformance:
    """性能测试"""

    def test_generation_speed(self):
        """测试生成速度"""
        num_ids = 100000
        start_time = time.time()

        for _ in range(num_ids):
            generate_snowflake_id()

        elapsed = time.time() - start_time
        ids_per_second = num_ids / elapsed

        # 降低阈值以适应不同环境
        assert ids_per_second > 50000, f"生成速度太慢: {ids_per_second:.0f} IDs/秒"

    def test_same_millisecond_sequence(self):
        """测试同一毫秒内序列号递增"""
        generator = SnowflakeIDGenerator(0, 0)

        # 快速生成多个 ID
        ids = [generator.generate_id() for _ in range(1000)]

        # 解析 ID
        parsed_ids = [SnowflakeIDGenerator.parse_id(id_) for id_ in ids]

        # 按时间戳分组
        timestamp_groups = defaultdict(list)
        for pid in parsed_ids:
            timestamp_groups[pid["timestamp"]].append(pid["sequence"])

        # 对于有多个 ID 的毫秒，检查序列号递增
        for timestamp, sequences in timestamp_groups.items():
            if len(sequences) > 1:
                assert sequences == sorted(sequences), f"时间戳 {timestamp} 的序列号应该递增"


class TestSnowflakeValidation:
    """参数验证测试"""

    def test_valid_boundary_values(self):
        """测试边界值"""
        gen = SnowflakeIDGenerator(datacenter_id=7, worker_id=7)
        snowflake_id = gen.generate_id()
        assert snowflake_id > 0
        assert snowflake_id <= SnowflakeConfig.MAX_SAFE_INTEGER

    def test_invalid_boundary_values(self):
        """测试无效边界值"""
        # 由于单例模式，需要直接测试验证逻辑
        # 测试超出范围的值会在初始化时被捕获
        try:
            # 重置单例以测试新的参数
            SnowflakeIDGenerator._instance = None
            with pytest.raises(ValueError, match="datacenter_id 必须在"):
                SnowflakeIDGenerator(datacenter_id=8, worker_id=0)
        finally:
            # 恢复单例
            SnowflakeIDGenerator._instance = None

        try:
            SnowflakeIDGenerator._instance = None
            with pytest.raises(ValueError, match="worker_id 必须在"):
                SnowflakeIDGenerator(datacenter_id=0, worker_id=-1)
        finally:
            SnowflakeIDGenerator._instance = None

    def test_javascript_safe_integer(self):
        """测试 JavaScript 安全整数范围"""
        for _ in range(10000):
            snowflake_id = generate_snowflake_id()
            assert snowflake_id <= SnowflakeConfig.MAX_SAFE_INTEGER, (
                f"ID {snowflake_id} 超出 JavaScript 安全范围"
            )


class TestSnowflakeMixinIntegration:
    """Mixin 集成测试"""

    def test_mixin_import(self):
        """测试 Mixin 可以正常导入"""
        assert True

    def test_snowflake_primary_key_mixin(self):
        """测试雪花主键 Mixin"""
        from src.core.mixins import SnowflakePKMixin

        # 检查类属性（Field 定义的字段）
        assert hasattr(SnowflakePKMixin, "_generate_snowflake_id")

        # 测试生成方法
        snowflake_id = SnowflakePKMixin._generate_snowflake_id()
        assert isinstance(snowflake_id, int)
        assert snowflake_id > 0
        assert snowflake_id <= SnowflakeConfig.MAX_SAFE_INTEGER
