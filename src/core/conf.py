# 在类定义外先加载环境变量
import json
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.path_conf import BasePath

_ = load_dotenv(BasePath / ".env", override=True)  # 添加 override=True 强制覆盖已存在的环境变量


class Settings(BaseSettings):
    """
    全局配置类

    Pydantic BaseSettings 会自动从环境变量读取配置，无需显式调用 os.getenv()。
    环境变量优先级高于默认值。

    配置方式：
    1. 通过环境变量（优先级最高）
    2. 通过 .env 文件
    3. 使用代码中的默认值
    """

    model_config = SettingsConfigDict(env_file=f"{BasePath}/.env", env_file_encoding="utf-8", extra="ignore")

    # ==================== 项目配置 ====================

    PROJECT_NAME: str = "FastAPI"
    VERSION: str = "1.0.0"
    DESCRIPTION: str = "FastAPI"
    DATETIME_FORMAT: str = "%Y-%m-%d"
    DATETIME_TIMEZONE: str = "Asia/Shanghai"
    API_PATH: str = "/api"

    # ==================== 安全配置 ====================

    SKIP_API_AUTH: bool = False

    @computed_field
    @property
    def DOCS_URL(self) -> str:
        return f"{self.API_PATH}/docs"

    @computed_field
    @property
    def OPENAPI_URL(self) -> str:
        return f"{self.API_PATH}/openapi.json"

    @computed_field
    @property
    def REDOC_URL(self) -> str:
        return f"{self.API_PATH}/redoc"

    # ==================== 应用配置 ====================

    APP_DEBUG: bool = False
    APP_HOST: str = "0.0.0.0"  # nosec B104 - service must bind all interfaces in container/server deployments
    APP_PORT: int = 8000

    # ==================== 日志配置 ====================

    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "logs"
    LOG_ROTATION_SIZE: str = "100 MB"
    LOG_RETENTION_DAYS: str = "30 days"
    LOG_JSON_OUTPUT: bool = True
    LOG_COMPRESSION: str = "zip"

    # ==================== CORS 配置 ====================

    # CORS_ALLOWED_ORIGINS: 允许的跨域来源列表
    # - 当 CORS_ALLOW_CREDENTIALS=True（Cookie/凭证）时，不允许使用通配符 "*"
    # - 当 CORS_ALLOW_CREDENTIALS=False 时，可按需使用通配符 "*"
    # - 生产环境建议始终明确配置域名列表
    # 环境变量格式:
    #   - 通配符: "*"
    #   - JSON 数组: '["http://example.com", "https://app.example.com"]'
    #   - 逗号分隔: "http://example.com,https://app.example.com"
    #   - 单个地址: "http://example.com"
    CORS_ALLOWED_ORIGINS: str | list[str] = [  # 支持字符串或列表
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://localhost:5173",
        # 生产环境请添加实际的前端域名
    ]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_EXPOSE_HEADERS: list[str] = ["X-Request-ID"]
    MIDDLEWARE_CORS: bool = True

    @field_validator("CORS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str] | None) -> list[str]:
        """解析 CORS_ALLOWED_ORIGINS 环境变量"""
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            # 尝试解析 JSON 数组
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                # 使用逗号分隔
                return [origin.strip() for origin in v.split(",")]
        return []

    # ==================== 数据库配置 ====================

    # PostgreSQL 配置组件
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "app_db"

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        """
        动态构建数据库 URL

        Pydantic 会优先读取环境变量 DATABASE_URL（如果存在），
        否则根据 PostgreSQL 配置组件构建。
        """
        # 尝试从环境变量获取（Pydantic 自动处理）
        # 这里只需要返回构建的 URL
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # ==================== Redis 配置 ====================

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0

    @computed_field
    @property
    def REDIS_URL(self) -> str:
        """动态构建 Redis URL"""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ==================== 雪花ID配置 ====================

    USE_SNOWFLAKE_ID: bool = False
    SNOWFLAKE_DATACENTER_ID: int = 0
    SNOWFLAKE_WORKER_ID: int = 0
    SNOWFLAKE_EPOCH: int = 1704067200000

    # ==================== JWT 配置 ====================

    JWT_SECRET_KEY: str = ""  # 默认空值，通过 model_validator 验证
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_SECONDS: int = 3600  # 默认 1 小时
    JWT_REFRESH_TOKEN_EXPIRE_SECONDS: int = 604800  # 默认 7 天
    JWT_ACCESS_TOKEN_REDIS_PREFIX: str = "auth:access_token"
    JWT_REFRESH_TOKEN_REDIS_PREFIX: str = "auth:refresh_token"
    JWT_USER_REDIS_PREFIX: str = "auth:user"

    # ==================== Celery 配置 ====================

    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None

    @computed_field
    @property
    def CELERY_BROKER(self) -> str:
        """Celery Broker URL (优先使用环境变量，否则基于 Redis 配置构建)"""
        if self.CELERY_BROKER_URL:
            return self.CELERY_BROKER_URL
        # 基于 Redis 配置构建
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/1"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/1"

    @computed_field
    @property
    def CELERY_BACKEND(self) -> str:
        """Celery Result Backend URL (优先使用环境变量，否则基于 Redis 配置构建)"""
        if self.CELERY_RESULT_BACKEND:
            return self.CELERY_RESULT_BACKEND
        # 基于 Redis 配置构建
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/2"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/2"

    # ==================== API 认证配置 ====================

    API_SECRET_ENCRYPTION_KEY: str = ""  # Fernet 加密密钥，通过 model_validator 验证

    # ==================== Cookie 安全配置 ====================
    # 控制 Cookie 的 secure 标志，生产环境应设置为 True
    # True: Cookie 仅通过 HTTPS 传输（推荐用于生产环境）
    # False: Cookie 可通过 HTTP 或 HTTPS 传输（仅用于本地开发）
    COOKIE_SECURE: bool | None = None  # None 表示自动根据 APP_DEBUG 判断
    # Cookie SameSite 策略:
    # - lax/strict: 同站点优先（默认）
    # - none: 允许跨站点发送（必须与 Secure=true 一起使用）
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"

    @computed_field
    @property
    def COOKIE_SECURE_EFFECTIVE(self) -> bool:
        """
        计算 Cookie secure 的最终生效值

        规则：
        1. 显式配置 COOKIE_SECURE 时，直接使用
        2. 未配置时，自动根据 APP_DEBUG 判断（非调试环境启用 Secure）
        """
        return self.COOKIE_SECURE if self.COOKIE_SECURE is not None else not self.APP_DEBUG

    # ==================== Ip location ====================
    IP_LOCATION_PARSE: Literal["online", "offline", "false"] = "offline"
    IP_LOCATION_REDIS_PREFIX: str | None = "ip:location"
    IP_LOCATION_EXPIRE_SECONDS: int | None = 60 * 60 * 24 * 1  # 过期时间，单位：秒

    # ==================== 配置验证 ====================

    @model_validator(mode="after")
    def validate_security_settings(self):
        """验证安全相关配置"""

        # ==================== 安全验证 ====================

        # 验证 JWT 密钥
        if not self.JWT_SECRET_KEY:
            raise ValueError(
                "❌ 安全错误: JWT_SECRET_KEY 未在环境变量中设置。\n"
                '   生成方法: python -c "import secrets; print(secrets.token_urlsafe(32))"\n'
                "   然后在 .env 文件中添加: JWT_SECRET_KEY=<生成的密钥>"
            )
        if len(self.JWT_SECRET_KEY) < 32:
            raise ValueError(f"❌ 安全错误: JWT_SECRET_KEY 长度不足（当前: {len(self.JWT_SECRET_KEY)}，要求: ≥32）")

        # 验证 API 加密密钥
        if not self.API_SECRET_ENCRYPTION_KEY:
            raise ValueError(
                "❌ 安全错误: API_SECRET_ENCRYPTION_KEY 未在环境变量中设置。\n"
                '   生成方法: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\n'
                "   然后在 .env 文件中添加: API_SECRET_ENCRYPTION_KEY=<生成的密钥>"
            )
        try:
            from cryptography.fernet import Fernet

            _ = Fernet(self.API_SECRET_ENCRYPTION_KEY.encode())
        except Exception as e:
            raise ValueError(f"❌ 安全错误: API_SECRET_ENCRYPTION_KEY 格式无效: {e}") from e

        # 检查是否使用了弱默认值
        weak_keys = [
            "your-secret-key-change-in-production",
            "changeme",
            "secret",
            "password",
            "123456",
        ]
        if any(weak_key in self.JWT_SECRET_KEY.lower() for weak_key in weak_keys):
            raise ValueError(
                "❌ 安全错误: JWT_SECRET_KEY 使用了弱密钥。\n"
                '   生成方法: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )

        # 验证数据库密码
        db_url = self.DATABASE_URL.lower()
        weak_passwords = ["changeme", "password", "123456", "admin", "root"]
        if any(weak_pwd in db_url for weak_pwd in weak_passwords):
            raise ValueError(
                "❌ 安全错误: 数据库密码使用了弱默认值。\n"
                '   生成方法: python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )

        # 验证 CORS 配置
        # 规范要求：allow_credentials=True 时，allow_origins 不能使用通配符 '*'
        if self.MIDDLEWARE_CORS and self.CORS_ALLOW_CREDENTIALS and "*" in self.CORS_ALLOWED_ORIGINS:
            raise ValueError(
                "❌ 配置错误: 当 CORS_ALLOW_CREDENTIALS=True 时，不允许使用 CORS 通配符 '*'。\n"
                "   请明确指定 CORS_ALLOWED_ORIGINS 域名列表\n"
                '   示例: CORS_ALLOWED_ORIGINS=["https://app.example.com"]'
            )

        # 验证 Cookie 策略
        # 浏览器规范要求：SameSite=None 时，必须同时启用 Secure
        if self.COOKIE_SAMESITE == "none" and not self.COOKIE_SECURE_EFFECTIVE:
            raise ValueError(
                "❌ 配置错误: 当 COOKIE_SAMESITE=none 时，必须启用 COOKIE_SECURE。\n"
                "   请设置 COOKIE_SECURE=true（并使用 HTTPS）"
            )

        # ==================== 功能验证 ====================

        # 验证雪花ID配置
        if not (0 <= self.SNOWFLAKE_DATACENTER_ID <= 7):
            raise ValueError(
                f"❌ 配置错误: SNOWFLAKE_DATACENTER_ID 必须在 0-7 之间，当前值: {self.SNOWFLAKE_DATACENTER_ID}"
            )
        if not (0 <= self.SNOWFLAKE_WORKER_ID <= 7):
            raise ValueError(f"❌ 配置错误: SNOWFLAKE_WORKER_ID 必须在 0-7 之间，当前值: {self.SNOWFLAKE_WORKER_ID}")

        return self


@lru_cache
def get_settings() -> Settings:
    """获取全局配置（单例模式）"""
    return Settings()


# 创建配置实例
settings = get_settings()
