"""API 认证模块常量定义"""


class CacheKeys:
    """缓存键常量

    统一管理所有 API 认证相关的缓存键,确保命名一致性和可维护性。
    """

    # 应用缓存键
    APP_BY_ID = "api_app:detail:{id}"  # 通过 ID 查询应用 (BaseService 使用)
    APP_BY_APP_ID = "api_app:app_id:{app_id}"  # 通过 app_id 查询应用

    # 权限缓存键
    APP_PERMISSIONS = "api_app:perms:{app_id}"  # 应用权限集合

    # 速率限制缓存键
    RATE_LIMIT_MINUTE = "api_app:rate:minute:{app_id}:{time}"  # 分钟级速率限制
    RATE_LIMIT_HOUR = "api_app:rate:hour:{app_id}:{time}"  # 小时级速率限制

    @staticmethod
    def app_by_id(app_id: int) -> str:
        """生成应用详情缓存键 (通过 ID)"""
        return CacheKeys.APP_BY_ID.format(id=app_id)

    @staticmethod
    def app_by_app_id(app_id: str) -> str:
        """生成应用详情缓存键 (通过 app_id)"""
        return CacheKeys.APP_BY_APP_ID.format(app_id=app_id)

    @staticmethod
    def app_permissions(app_id: int) -> str:
        """生成应用权限缓存键"""
        return CacheKeys.APP_PERMISSIONS.format(app_id=app_id)

    @staticmethod
    def rate_limit_minute(app_id: str, time_slot: int) -> str:
        """生成分钟级速率限制缓存键

        Args:
            app_id: 应用 ID
            time_slot: 时间槽 (current_time // 60)
        """
        return CacheKeys.RATE_LIMIT_MINUTE.format(app_id=app_id, time=time_slot)

    @staticmethod
    def rate_limit_hour(app_id: str, time_slot: int) -> str:
        """生成小时级速率限制缓存键

        Args:
            app_id: 应用 ID
            time_slot: 时间槽 (current_time // 3600)
        """
        return CacheKeys.RATE_LIMIT_HOUR.format(app_id=app_id, time=time_slot)


class CacheExpire:
    """缓存过期时间常量 (秒)"""

    APP_DETAIL = 300  # 应用详情: 5 分钟
    APP_PERMISSIONS = 300  # 应用权限: 5 分钟
    APP_PERMISSIONS_EMPTY = 120  # 空权限集: 2 分钟
    RATE_LIMIT_MINUTE = 60  # 分钟级速率限制: 1 分钟
    RATE_LIMIT_HOUR = 3600  # 小时级速率限制: 1 小时
