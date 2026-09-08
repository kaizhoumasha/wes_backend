"""联调诊断唯一配置入口，启动时读取；修改后重启生效。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.path_conf import BasePath


class DiagnosticsConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WMS_DIAGNOSTICS_",
        env_file=f"{BasePath}/.env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    retention_hours: int = Field(default=24, ge=1, le=168)
    max_records: int = Field(default=1000, ge=1, le=10000)
    max_record_bytes: int = Field(default=32768, ge=4096, le=65536)
    budget_ms: int = Field(default=100, ge=10, le=500)


@lru_cache(maxsize=1)
def diagnostics_config() -> DiagnosticsConfig:
    return DiagnosticsConfig()
