# 在类定义外先加载环境变量
import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.path_conf import BasePath

load_dotenv(BasePath / ".env", override=True)  # 添加 override=True 强制覆盖已存在的环境变量


class Settings(BaseSettings):
    """Global Settings"""

    model_config = SettingsConfigDict(
        env_file=f"{BasePath}/.env", env_file_encoding="utf-8", extra="ignore"
    )

    # 项目名称
    PROJECT_NAME: str = os.getenv("PROJECT_NAME", "FastAPI")
    # 项目版本
    VERSION: str = os.getenv("VERSION", "1.0.0")
    # 项目描述
    DESCRIPTION: str = os.getenv("DESCRIPTION", "FastAPI")
    # 日期时间格式
    DATETIME_FORMAT: str = os.getenv("DATETIME_FORMAT", "%Y-%m-%d")
    # 时区设置
    DATETIME_TIMEZONE: str = os.getenv("DATETIME_TIMEZONE", "Asia/Shanghai")
    # 项目API版本
    API_PATH: str = os.getenv("API_PATH", "/api")
    # 项目文档地址
    DOCS_URL: str = f"{API_PATH}/docs"
    # 项目文档地址
    OPENAPI_URL: str = f"{API_PATH}/openapi.json"
    # 项目文档地址
    REDOC_URL: str = f"{API_PATH}/redoc"

    APP_DEBUG: bool = bool(os.getenv("APP_DEBUG", "false"))
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))

    # 日志配置
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")
    LOG_ROTATION_SIZE: str = os.getenv("LOG_ROTATION_SIZE", "100 MB")
    LOG_RETENTION_DAYS: str = os.getenv("LOG_RETENTION_DAYS", "30 days")
    LOG_JSON_OUTPUT: bool = bool(os.getenv("LOG_JSON_OUTPUT", "true"))
    LOG_COMPRESSION: str = os.getenv("LOG_COMPRESSION", "zip")

    # CORS
    CORS_ALLOWED_ORIGINS: list[str] = [  # 末尾不带斜杠
        "http://127.0.0.1:8001",
        "http://localhost:5173",
    ]
    CORS_EXPOSE_HEADERS: list[str] = [
        "X-Request-ID",
    ]
    MIDDLEWARE_CORS: bool = True

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./sql_app.db")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # 雪花ID配置（分布式唯一ID生成）
    # 是否启用雪花ID作为默认主键
    USE_SNOWFLAKE_ID: bool = os.getenv("USE_SNOWFLAKE_ID", "false").lower() in ("true", "1", "yes")
    # 数据中心ID（0-7）：用于标识不同数据中心或机房
    SNOWFLAKE_DATACENTER_ID: int = int(os.getenv("SNOWFLAKE_DATACENTER_ID", "0"))
    # 工作机器ID（0-7）：用于标识同一数据中心内的不同服务器
    SNOWFLAKE_WORKER_ID: int = int(os.getenv("SNOWFLAKE_WORKER_ID", "0"))
    # 纪元时间戳（毫秒级Unix时间戳）：雪花ID的时间起点
    # ⚠️ 警告：所有节点必须使用相同的 EPOCH，否则会生成重复 ID
    SNOWFLAKE_EPOCH: int = int(os.getenv("SNOWFLAKE_EPOCH", "1704067200000"))

    # JWT 配置
    # JWT 密钥（生产环境必须使用强随机密钥并通过环境变量设置）
    JWT_SECRET_KEY: str = os.getenv(
        "JWT_SECRET_KEY",
        "your-secret-key-change-in-production-min-32-chars-long",
    )
    # JWT 算法
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    # Access Token 过期时间（秒）
    JWT_ACCESS_TOKEN_EXPIRE_SECONDS: int = int(
        os.getenv("JWT_ACCESS_TOKEN_EXPIRE_SECONDS", "3600")
    )  # 默认 1 小时
    # Refresh Token 过期时间（秒）
    JWT_REFRESH_TOKEN_EXPIRE_SECONDS: int = int(
        os.getenv("JWT_REFRESH_TOKEN_EXPIRE_SECONDS", "604800")
    )  # 默认 7 天
    # Redis Token 前缀
    JWT_ACCESS_TOKEN_REDIS_PREFIX: str = os.getenv(
        "JWT_ACCESS_TOKEN_REDIS_PREFIX", "auth:access_token"
    )
    JWT_REFRESH_TOKEN_REDIS_PREFIX: str = os.getenv(
        "JWT_REFRESH_TOKEN_REDIS_PREFIX", "auth:refresh_token"
    )
    JWT_USER_REDIS_PREFIX: str = os.getenv("JWT_USER_REDIS_PREFIX", "auth:user")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 验证雪花ID配置
        if not (0 <= self.SNOWFLAKE_DATACENTER_ID <= 7):
            raise ValueError(
                f"SNOWFLAKE_DATACENTER_ID 必须在 0-7 之间，当前值: {self.SNOWFLAKE_DATACENTER_ID}"
            )
        if not (0 <= self.SNOWFLAKE_WORKER_ID <= 7):
            raise ValueError(
                f"SNOWFLAKE_WORKER_ID 必须在 0-7 之间，当前值: {self.SNOWFLAKE_WORKER_ID}"
            )


@lru_cache
def get_settings() -> Settings:
    """获取全局配置"""
    return Settings()


# 创建配置实例
settings = get_settings()
