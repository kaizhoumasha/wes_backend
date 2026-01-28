"""
软删除 Mixin

为模型添加软删除功能,数据不会被物理删除
"""

from datetime import datetime

from sqlmodel import Field

from src.core.mixins.base import BaseMixin


class SoftDeleteMixin(BaseMixin):
    """
    软删除 Mixin

    为模型添加软删除功能,数据不会被物理删除
    - deleted_at: 删除时间(None 表示未删除)
    - is_deleted: 删除标记

    使用示例:
        class Article(SoftDeleteMixin, table=True):
            id: Optional[int] = Field(primary_key=True)
            title: str

        article.soft_delete()  # 标记为已删除
        article.restore()     # 恢复已删除的记录
    """

    deleted_by: int | None = Field(
        default=None,
        sa_column_kwargs={"nullable": True, "comment": "删除人ID"},
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column_kwargs={"nullable": True, "comment": "删除时间"},
    )
    is_deleted: bool = Field(default=False, sa_column_kwargs={"comment": "是否已删除"})

    def soft_delete(self, deleted_by: int | None = None) -> None:
        """
        标记为已删除

        :param deleted_by: 删除人ID(如果模型有 AuditMixin)
        """
        from src.utils.timezone import timezone

        self.is_deleted = True
        self.deleted_at = timezone.now_for_db()  # 使用 naive datetime 用于数据库存储
        if deleted_by is not None and hasattr(self, "deleted_by"):
            self.deleted_by = deleted_by

    def restore(self) -> None:
        """恢复已删除的记录"""
        self.is_deleted = False
        self.deleted_at = None
        if hasattr(self, "deleted_by"):
            self.deleted_by = None
