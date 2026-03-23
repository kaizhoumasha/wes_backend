"""
Repr Mixin

提供自动生成 __repr__ 方法的 Mixin
"""

from src.core.mixins.base import BaseMixin


class ReprMixin(BaseMixin):
    """
    通用 __repr__ Mixin

    自动生成包含所有字段值的字符串表示

    使用示例:
        class User(ReprMixin, table=True):
            id: Optional[int] = Field(primary_key=True)
            name: str

        print(user)  # <User(id=1, name='test')>
    """

    def __repr__(self) -> str:
        class_name = self.__class__.__name__
        attributes = [f"{k}={v!r}" for k, v in self.__dict__.items() if not k.startswith("_")]
        return f"<{class_name}({', '.join(attributes)})>"
