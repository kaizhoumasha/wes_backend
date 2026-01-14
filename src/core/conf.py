
# 在类定义外先加载环境变量
from functools import lru_cache
import os

from ..core.path_conf import BasePath
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

env_path = os.path.join(BasePath, ".env")
load_dotenv(env_path, override=True)  # 添加 override=True 强制覆盖已存在的环境变量

class Settings(BaseSettings):
    """Global Settings"""

    model_config = SettingsConfigDict(
        env_file=f"{BasePath}/.env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )

    # 项目名称
    PROJECT_NAME: str = os.getenv("PROJECT_NAME", "FastAPI")
    # 项目版本
    VERSION: str = os.getenv("VERSION", "1.0.0")
    # 项目描述
    DESCRIPTION: str = os.getenv("DESCRIPTION", "FastAPI")
    # 日期时间格式
    DATETIME_FORMAT: str = os.getenv("DATETIME_FORMAT", "%Y-%m-%d")
    # 项目API版本
    API_PATH: str = os.getenv("API_PATH", "/api")
    # 项目文档地址
    DOCS_URL: str = f"{API_PATH}/docs"
    # 项目文档地址
    OPENAPI_URL: str = f"{API_PATH}/openapi.json"
    # 项目文档地址
    REDOC_URL: str = f"{API_PATH}/redoc"

    APP_DEBUG: bool = bool(os.getenv("APP_DEBUG", False))
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", 8000))


@lru_cache
def get_settings() -> Settings:
    """获取全局配置"""
    return Settings()


# 创建配置实例
settings = get_settings()