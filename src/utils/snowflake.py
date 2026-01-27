"""
雪花 ID (Snowflake ID) 生成器

Twitter Snowflake 算法的优化实现
适用于 JavaScript/TypeScript（安全整数范围）

ID 结构（53 位）:
- 41 位时间戳（毫秒级，从纪元开始，可使用约 69 年）
- 3 位数据中心 ID（0-7，共 8 个）
- 3 位工作机器 ID（0-7，共 8 个）
- 6 位序列号（0-63，每毫秒最多 64 个 ID）

前端兼容性:
- 生成的 ID ≤ 9007199254740991 (Number.MAX_SAFE_INTEGER)
- JavaScript 安全整数，前端可直接使用 Number 类型
- 15-16位十进制数字（如：123456789012345）
- 无需使用 BigInt

参考: https://developer.twitter.com/en/docs/twitter-ids
"""

import threading
import time
from typing import Optional


class SnowflakeConfig:
    """雪花算法配置（53位方案 - JavaScript 安全）"""

    # 位分配（总共53位）
    WORKER_ID_BITS: int = 3  # 工作机器3位（0-7）
    DATACENTER_ID_BITS: int = 3  # 数据中心3位（0-7）
    SEQUENCE_BITS: int = 6  # 序列号6位（0-63）

    # 最大值
    MAX_WORKER_ID: int = (1 << WORKER_ID_BITS) - 1  # 7
    MAX_DATACENTER_ID: int = (1 << DATACENTER_ID_BITS) - 1  # 7
    SEQUENCE_MASK: int = (1 << SEQUENCE_BITS) - 1  # 63

    # 位移偏移
    WORKER_ID_SHIFT: int = SEQUENCE_BITS  # 6
    DATACENTER_ID_SHIFT: int = SEQUENCE_BITS + WORKER_ID_BITS  # 9
    TIMESTAMP_LEFT_SHIFT: int = SEQUENCE_BITS + WORKER_ID_BITS + DATACENTER_ID_BITS  # 12

    # 纪元时间戳（默认2024-01-01 00:00:00 UTC）
    # 从配置文件读取，支持时间戳滚动以延长系统使用寿命
    # 使用41位时间戳，可使用约69年
    EPOCH: int = 1704067200000  # 默认值，会被 _load_config() 覆盖

    # 时钟回拨容忍阈值（毫秒）
    CLOCK_BACKWARD_TOLERANCE_MS: int = 5_000

    # JavaScript 安全整数上限（2^53 - 1）
    MAX_SAFE_INTEGER: int = 9007199254740991

    @staticmethod
    def _load_config() -> None:
        """从配置文件加载 EPOCH 等配置参数"""
        try:
            from src.core.conf import settings

            SnowflakeConfig.EPOCH = settings.SNOWFLAKE_EPOCH
        except ImportError:
            # 如果配置模块不可用，使用默认值
            pass


# 模块加载时自动加载配置
SnowflakeConfig._load_config()


class SnowflakeIDGenerator:
    """
    53位雪花 ID 生成器（JavaScript 安全方案）

    单例模式，线程安全
    生成的 ID 保证在 JavaScript 安全整数范围内

    使用示例:
        generator = SnowflakeIDGenerator(datacenter_id=0, worker_id=0)
        snowflake_id = generator.generate_id()

    ID 特性:
    - 15-16位十进制数字（如：123456789012345）
    - 保证在 JavaScript 安全整数范围内（≤ 9007199254740991）
    - 前端可直接使用 Number 类型，无需 BigInt
    - 时间跨度约 69 年（从 2024 年开始到 2093 年）
    - 支持 8×8 = 64 个节点
    - 每毫秒最多 64 个 ID

    位分配（53位）:
    - 41 位时间戳
    - 3 位数据中心 ID（0-7）
    - 3 位工作机器 ID（0-7）
    - 6 位序列号（0-63）

    适用场景:
    - 需要前端 JavaScript 兼容的分布式系统
    - 中小型分布式系统（最多64个节点）
    - 长期运行的项目（69年无需迁移）

    注意:
    - 这是 JavaScript 安全的标准方案
    - 64个节点对大多数项目完全够用
    - 每毫秒64个ID对大多数场景足够
    """

    _instance: Optional["SnowflakeIDGenerator"] = None
    _lock = threading.Lock()

    def __new__(cls, _datacenter_id: int = 0, _worker_id: int = 0):
        """单例模式"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance

    def __init__(self, datacenter_id: int = 0, worker_id: int = 0):
        """
        初始化雪花 ID 生成器

        :param datacenter_id: 数据中心 ID (0-7)
        :param worker_id: 工作机器 ID (0-7)
        """
        with self._lock:
            if hasattr(self, "_initialized") and self._initialized:
                return

            self.datacenter_id = datacenter_id
            self.worker_id = worker_id
            self.sequence = 0
            self.last_timestamp = -1
            self._instance_lock = threading.Lock()

            # 验证参数
            if not (0 <= self.datacenter_id <= SnowflakeConfig.MAX_DATACENTER_ID):
                raise ValueError(
                    f"datacenter_id 必须在 0~{SnowflakeConfig.MAX_DATACENTER_ID} 之间，当前值: {self.datacenter_id}"
                )
            if not (0 <= self.worker_id <= SnowflakeConfig.MAX_WORKER_ID):
                raise ValueError(f"worker_id 必须在 0~{SnowflakeConfig.MAX_WORKER_ID} 之间，当前值: {self.worker_id}")

            self._initialized = True

    def _current_millis(self) -> int:
        """获取当前时间戳（毫秒）"""
        return int(time.time() * 1000)

    def _till_next_millis(self, last_timestamp: int) -> int:
        """等待直到下一毫秒"""
        timestamp = self._current_millis()
        while timestamp <= last_timestamp:
            time.sleep(0.0001)  # 100 微秒
            timestamp = self._current_millis()
        return timestamp

    def generate_id(self) -> int:
        """
        生成53位雪花 ID

        :return: 53位雪花 ID（保证在JavaScript安全整数范围内）
        :raises ValueError: 如果生成的 ID 超出 JavaScript 安全整数范围
        """
        with self._instance_lock:
            timestamp = self._current_millis()

            # 时钟回拨处理
            if timestamp < self.last_timestamp:
                back_ms = self.last_timestamp - timestamp
                if back_ms <= SnowflakeConfig.CLOCK_BACKWARD_TOLERANCE_MS:
                    # 在容忍范围内，等待恢复
                    timestamp = self._till_next_millis(self.last_timestamp)
                else:
                    raise SystemError(f"时钟回拨超过容忍阈值 ({back_ms} ms)，无法生成雪花 ID")

            # 同一毫秒内，序列号递增
            if timestamp == self.last_timestamp:
                self.sequence = (self.sequence + 1) & SnowflakeConfig.SEQUENCE_MASK
                if self.sequence == 0:
                    # 序列号耗尽，等待下一毫秒
                    timestamp = self._till_next_millis(self.last_timestamp)
            else:
                self.sequence = 0

            self.last_timestamp = timestamp

            # 组合 53 位 ID
            # |------------------------------------------------------------------------------|
            # | 41 bits timestamp | 3 bits datacenter | 3 bits worker | 6 bits sequence    |
            # |------------------------------------------------------------------------------|
            snowflake_id = (
                ((timestamp - SnowflakeConfig.EPOCH) << SnowflakeConfig.TIMESTAMP_LEFT_SHIFT)
                | (self.datacenter_id << SnowflakeConfig.DATACENTER_ID_SHIFT)
                | (self.worker_id << SnowflakeConfig.WORKER_ID_SHIFT)
                | self.sequence
            )

            # 验证 JavaScript 安全性
            if snowflake_id > SnowflakeConfig.MAX_SAFE_INTEGER:
                raise ValueError(
                    f"生成的 ID {snowflake_id} 超出 JavaScript 安全整数范围 ({SnowflakeConfig.MAX_SAFE_INTEGER})"
                )

            return snowflake_id

    @staticmethod
    def parse_id(snowflake_id: int) -> dict:
        """
        解析53位雪花 ID

        :param snowflake_id: 雪花 ID（53位）
        :return: 包含时间戳、数据中心、工作机器、序列号的字典
        """
        timestamp = (snowflake_id >> SnowflakeConfig.TIMESTAMP_LEFT_SHIFT) + SnowflakeConfig.EPOCH
        datacenter_id = (snowflake_id >> SnowflakeConfig.DATACENTER_ID_SHIFT) & SnowflakeConfig.MAX_DATACENTER_ID
        worker_id = (snowflake_id >> SnowflakeConfig.WORKER_ID_SHIFT) & SnowflakeConfig.MAX_WORKER_ID
        sequence = snowflake_id & SnowflakeConfig.SEQUENCE_MASK

        return {
            "id": snowflake_id,
            "timestamp": timestamp,
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(timestamp / 1000)),
            "datacenter_id": datacenter_id,
            "worker_id": worker_id,
            "sequence": sequence,
        }


# 全局默认实例（从配置文件读取，默认使用 datacenter_id=0, worker_id=0）
_default_generator: SnowflakeIDGenerator | None = None


def get_snowflake_generator(datacenter_id: int | None = None, worker_id: int | None = None) -> SnowflakeIDGenerator:
    """
    获取雪花 ID 生成器实例

    :param datacenter_id: 数据中心 ID (0-7)，如果为 None 则从配置文件读取
    :param worker_id: 工作机器 ID (0-7)，如果为 None 则从配置文件读取
    :return: 雪花 ID 生成器实例
    """
    global _default_generator

    # 如果未提供参数，从配置文件读取
    if datacenter_id is None or worker_id is None:
        try:
            from src.core.conf import settings

            if datacenter_id is None:
                datacenter_id = settings.SNOWFLAKE_DATACENTER_ID
            if worker_id is None:
                worker_id = settings.SNOWFLAKE_WORKER_ID
        except ImportError:
            # 如果配置模块不可用，使用默认值
            if datacenter_id is None:
                datacenter_id = 0
            if worker_id is None:
                worker_id = 0

    if _default_generator is None:
        _default_generator = SnowflakeIDGenerator(datacenter_id, worker_id)
    return _default_generator


def generate_snowflake_id(datacenter_id: int | None = None, worker_id: int | None = None) -> int:
    """
    生成雪花 ID（便捷函数）

    :param datacenter_id: 数据中心 ID (0-7)，如果为 None 则从配置文件读取
    :param worker_id: 工作机器 ID (0-7)，如果为 None 则从配置文件读取
    :return: 53 位雪花 ID
    """
    generator = get_snowflake_generator(datacenter_id, worker_id)
    return generator.generate_id()
