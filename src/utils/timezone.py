"""
时区管理模块

提供统一的时区转换和管理功能，避免时区相关的错误。

参考: fastapi_best_architecture/backend/utils/timezone.py
"""

import zoneinfo
from datetime import UTC, datetime

from src.core.conf import settings


class TimeZone:
    """
    时区转换器

    提供本地时区和 UTC 时区之间的转换功能
    """

    def __init__(self) -> None:
        """初始化时区转换器"""
        # 从配置获取时区，默认使用 Asia/Shanghai
        tz_name = getattr(settings, "DATETIME_TIMEZONE", "Asia/Shanghai")
        self.tz_info = zoneinfo.ZoneInfo(tz_name)

    def now(self) -> datetime:
        """
        获取当前时区时间（aware datetime）

        Returns:
            当前时区的 datetime 对象
        """
        return datetime.now(self.tz_info)

    def now_utc(self) -> datetime:
        """
        获取当前 UTC 时间（aware datetime，用于 API 响应和时间戳计算）

        Returns:
            UTC 时区的 aware datetime 对象
        """
        return datetime.now(UTC)

    def now_for_db(self) -> datetime:
        """
        获取当前 UTC 时间（naive datetime，用于数据库存储）

        数据库使用 TIMESTAMP WITHOUT TIME ZONE 存储时间，
        需要传入 UTC naive datetime。

        Returns:
            UTC naive datetime 对象
        """
        return datetime.now(UTC).replace(tzinfo=None)

    @staticmethod
    def parse_datetime(t: object) -> datetime | None:
        """
        将 datetime 或 ISO 字符串解析为 datetime。

        Args:
            t: datetime 对象、ISO 时间字符串或 None

        Returns:
            datetime 对象；无法解析时返回 None。
        """
        if t is None:
            return None
        if isinstance(t, datetime):
            return t
        if not isinstance(t, str):
            return None

        value = t.strip()
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def to_db_datetime(t: object) -> datetime | None:
        """
        将 datetime、ISO 字符串或 Unix 秒时间戳转换为 UTC naive datetime，用于数据库存储。

        Args:
            t: datetime 对象、ISO 时间字符串、Unix 秒时间戳或 None

        Returns:
            UTC naive datetime；无法解析时返回 None。
        """
        if t is None or isinstance(t, bool):
            return None
        if isinstance(t, (int, float)):
            return TimeZone.to_utc(t).replace(tzinfo=None)

        parsed = TimeZone.parse_datetime(t)
        if parsed is None:
            return None
        return TimeZone.to_utc(parsed).replace(tzinfo=None)

    def from_datetime(self, t: datetime) -> datetime:
        """
        将 datetime 对象转换为当前时区时间

        Args:
            t: 需要转换的 datetime 对象（可以是 naive 或 aware）

        Returns:
            当前时区的 aware datetime 对象
        """
        if t.tzinfo is None:
            # naive datetime，假定为 UTC
            t = t.replace(tzinfo=UTC)
        return t.astimezone(self.tz_info)

    def from_str(self, t_str: str, format_str: str | None = None) -> datetime:
        """
        将时间字符串转换为当前时区的 datetime 对象

        Args:
            t_str: 时间字符串
            format_str: 时间格式字符串，默认使用 settings.DATETIME_FORMAT

        Returns:
            当前时区的 aware datetime 对象
        """
        if format_str is None:
            format_str = settings.DATETIME_FORMAT
        dt = datetime.strptime(t_str, format_str)
        # 假定输入为本地时区时间
        return dt.replace(tzinfo=self.tz_info)

    @staticmethod
    def to_str(t: datetime, format_str: str | None = None) -> str:
        """
        将 datetime 对象转换为指定格式的时间字符串

        Args:
            t: datetime 对象
            format_str: 时间格式字符串，默认使用 settings.DATETIME_FORMAT

        Returns:
            格式化后的时间字符串
        """
        if format_str is None:
            format_str = settings.DATETIME_FORMAT
        return t.strftime(format_str)

    @staticmethod
    def to_utc(t: datetime | float) -> datetime:
        """
        将 datetime 对象或时间戳转换为 UTC 时区时间

        Args:
            t: 需要转换的 datetime 对象或时间戳

        Returns:
            UTC 时区的 aware datetime 对象
        """
        if isinstance(t, datetime):
            return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
        # 时间戳（整数或浮点）
        return datetime.fromtimestamp(t, tz=UTC)

    @staticmethod
    def to_utc_timestamp(t: datetime) -> int:
        """
        将 datetime 对象转换为 UTC 时间戳（秒）

        Args:
            t: datetime 对象

        Returns:
            UTC 时间戳（秒）
        """
        t = t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
        return int(t.timestamp())


# 全局时区实例
timezone = TimeZone()
