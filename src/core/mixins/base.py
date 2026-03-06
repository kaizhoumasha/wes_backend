"""
基础 Mixin

提供所有数据类的通用基类
"""

from sqlmodel import SQLModel
from sqlmodel._compat import SQLModelConfig


class BaseMixin(SQLModel):
    """
    基础 Mixin

    系统内所有数据类的通用基类
    """

    model_config = SQLModelConfig(from_attributes=True)
