"""
单据状态验证模块

提供 WMS/WES 系统中单据状态的验证和状态机管理。

设计理念：
- 严格的状态流转控制
- 可配置的允许编辑状态
- 遵循 DRY、KISS、SOLID、YAGNI 原则

使用示例：
    # 在模型中定义状态字段
    class Inbound(DocumentStatusMixin, BaseTableModelMixin, table=True):
        doc_status: str = Field(default=DocStatus.DRAFT)

    # Repository 会自动验证状态
    await repo.update(db, id, data)  # 如果状态不允许编辑，会抛出异常
"""

from enum import Enum
from typing import ClassVar


class DocStatus(str, Enum):
    """
    单据状态枚举

    WMS/WES 系统中单据的标准状态流转：
    DRAFT → CONFIRMED → COMPLETED
           ↓
        CANCELLED
    """

    DRAFT = "draft"  # 草稿：可编辑、可删除
    CONFIRMED = "confirmed"  # 已确认：不可编辑、不可删除
    COMPLETED = "completed"  # 已完成：只读
    CANCELLED = "cancelled"  # 已取消：只读
    REJECTED = "rejected"  # 已拒绝：可编辑（重新提交）


class DocumentStateMachine:
    """
    单据状态机

    管理单据状态的合法转换，防止非法状态流转。

    状态转换规则：
    - DRAFT → CONFIRMED, CANCELLED, REJECTED
    - CONFIRMED → COMPLETED, CANCELLED
    - REJECTED → CONFIRMED, CANCELLED
    - COMPLETED → 终态（不可转换）
    - CANCELLED → 终态（不可转换）

    使用示例：
        # 验证状态转换
        DocumentStateMachine.validate_transition(
            from_status=DocStatus.DRAFT,
            to_status=DocStatus.CONFIRMED
        )  # 通过

        # 验证编辑权限
        DocumentStateMachine.validate_edit(DocStatus.DRAFT)  # 通过
        DocumentStateMachine.validate_edit(DocStatus.CONFIRMED)  # 抛出异常
    """

    # 状态转换规则（单一信息源）
    TRANSITIONS: ClassVar[dict[DocStatus, list[DocStatus]]] = {
        DocStatus.DRAFT: [DocStatus.CONFIRMED, DocStatus.CANCELLED, DocStatus.REJECTED],
        DocStatus.CONFIRMED: [DocStatus.COMPLETED, DocStatus.CANCELLED],
        DocStatus.REJECTED: [DocStatus.CONFIRMED, DocStatus.CANCELLED],
        DocStatus.COMPLETED: [],  # 终态
        DocStatus.CANCELLED: [],  # 终态
    }

    # 允许编辑的状态
    EDITABLE_STATUSES: ClassVar[set[DocStatus]] = {
        DocStatus.DRAFT,
        DocStatus.REJECTED,
    }

    # 允许删除的状态
    DELETABLE_STATUSES: ClassVar[set[DocStatus]] = {
        DocStatus.DRAFT,
        DocStatus.REJECTED,
    }

    @classmethod
    def _to_enum(cls, status: DocStatus | str) -> DocStatus | None:
        """
        将字符串转换为 DocStatus 枚举

        Args:
            status: 状态（枚举或字符串）

        Returns:
            DocStatus 枚举，如果转换失败则返回 None
        """
        if isinstance(status, DocStatus):
            return status

        try:
            return DocStatus(status)
        except ValueError:
            return None

    @classmethod
    def can_transition(cls, from_status: DocStatus | str, to_status: DocStatus | str) -> bool:
        """
        检查状态转换是否合法

        Args:
            from_status: 当前状态
            to_status: 目标状态

        Returns:
            是否可以转换
        """
        from_enum = cls._to_enum(from_status)
        to_enum = cls._to_enum(to_status)

        if from_enum is None or to_enum is None:
            return False

        return to_enum in cls.TRANSITIONS.get(from_enum, [])

    @classmethod
    def validate_transition(cls, from_status: DocStatus | str, to_status: DocStatus | str) -> None:
        """
        验证状态转换，不合法则抛出异常

        Args:
            from_status: 当前状态
            to_status: 目标状态

        Raises:
            ValueError: 状态转换不合法
        """
        if not cls.can_transition(from_status, to_status):
            raise ValueError(f"不允许从 [{from_status}] 转换到 [{to_status}]")

    @classmethod
    def can_edit(cls, status: DocStatus | str) -> bool:
        """
        检查当前状态是否允许编辑

        Args:
            status: 当前状态

        Returns:
            是否允许编辑
        """
        status_enum = cls._to_enum(status)
        if status_enum is None:
            return False

        return status_enum in cls.EDITABLE_STATUSES

    @classmethod
    def can_delete(cls, status: DocStatus | str) -> bool:
        """
        检查当前状态是否允许删除

        Args:
            status: 当前状态

        Returns:
            是否允许删除
        """
        status_enum = cls._to_enum(status)
        if status_enum is None:
            return False

        return status_enum in cls.DELETABLE_STATUSES

    @classmethod
    def validate_edit(cls, status: DocStatus | str) -> None:
        """
        验证是否允许编辑，不允许则抛出异常

        Args:
            status: 当前状态

        Raises:
            ValueError: 当前状态不允许编辑
        """
        if not cls.can_edit(status):
            editable_statuses = ", ".join([s.value for s in cls.EDITABLE_STATUSES])
            raise ValueError(f"当前状态 [{status}] 不允许修改，只有 [{editable_statuses}] 状态的单据可以修改")

    @classmethod
    def validate_delete(cls, status: DocStatus | str) -> None:
        """
        验证是否允许删除，不允许则抛出异常

        Args:
            status: 当前状态

        Raises:
            ValueError: 当前状态不允许删除
        """
        if not cls.can_delete(status):
            deletable_statuses = ", ".join([s.value for s in cls.DELETABLE_STATUSES])
            raise ValueError(f"当前状态 [{status}] 不允许删除，只有 [{deletable_statuses}] 状态的单据可以删除")


__all__ = [
    "DocStatus",
    "DocumentStateMachine",
]
