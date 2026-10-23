"""部署期显式插件输入；不安装插件、不提供业务时长默认值。"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.path_conf import BasePath


class DeploymentPluginSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=f"{BasePath}/.env", env_file_encoding="utf-8", extra="ignore")

    AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS: int | None = Field(default=None, gt=0)

    @field_validator("AUTOMATIC_PICKING_FIRST_PLAN_TIMEOUT_MS", mode="before")
    @classmethod
    def integer_milliseconds(cls, value):
        if value is None or type(value) is int:
            return value
        if type(value) is str and value.isascii() and value.isdigit():
            return int(value)
        raise ValueError("first plan timeout requires positive integer milliseconds or an unset value")


@lru_cache
def deployment_plugin_settings() -> DeploymentPluginSettings:
    return DeploymentPluginSettings()
