"""
缓存配置中心

集中管理所有模块的缓存配置,遵循 DRY 原则
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CacheConfig:
    """缓存配置"""

    prefix: str
    expire: int


class CacheSettings:
    """缓存配置常量"""

    USER = CacheConfig(prefix="user:detail", expire=7200)
    USER_LIST = CacheConfig(prefix="user:list", expire=600)
    ROLE = CacheConfig(prefix="role:detail", expire=3600)
    ROLE_LIST = CacheConfig(prefix="role:list", expire=600)
    PERMISSION = CacheConfig(prefix="permission:detail", expire=3600)
    PERMISSION_LIST = CacheConfig(prefix="permission:list", expire=600)
    DEVICE = CacheConfig(prefix="app:device:detail", expire=3600)
    DEVICE_LIST = CacheConfig(prefix="app:device:list", expire=600)
    WORKLINE = CacheConfig(prefix="app:workline:detail", expire=3600)
    WORKLINE_LIST = CacheConfig(prefix="app:workline:list", expire=600)
    DEMO_PRODUCT = CacheConfig(prefix="demo_product:detail", expire=3600)
    DEMO_PRODUCT_LIST = CacheConfig(prefix="demo_product:list", expire=600)
    API_APP = CacheConfig(prefix="api_app:detail", expire=300)
    API_APP_LIST = CacheConfig(prefix="api_app:list", expire=120)
    AUDIT_LOG = CacheConfig(prefix="audit_log:detail", expire=1800)
    AUDIT_LOG_LIST = CacheConfig(prefix="audit_log:list", expire=300)

    NULL_VALUE = 300


cache_settings = CacheSettings()

__all__ = ["CacheConfig", "CacheSettings", "cache_settings"]
