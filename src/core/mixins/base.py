"""
基础 Mixin

提供所有数据类的通用基类
"""

from sqlmodel import SQLModel
from sqlmodel._compat import SQLModelConfig

from src.database.metadata import metadata as shared_metadata

# 所有 SQLModel 表统一使用同一份 metadata，确保命名约定对 FK/PK/UK 生效。
SQLModel.metadata = shared_metadata


class BaseMixin(SQLModel):
    """
    基础 Mixin

    系统内所有数据类的通用基类
    """

    model_config = SQLModelConfig(from_attributes=True)
