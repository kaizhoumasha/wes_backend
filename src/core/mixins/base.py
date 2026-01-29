"""
基础 Mixin

提供所有数据类的通用基类
"""

from sqlmodel import SQLModel


class BaseMixin(SQLModel):
    """
    基础 Mixin

    系统内所有数据类的通用基类
    """

    class Config:
        # 使用 Pydantic 的序列化优化
        from_attributes = True
