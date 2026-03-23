"""
状态验证 Mixin 模块

提供各种状态验证的 Mixin 类，用于模型层的状态管理。

设计理念：
- 使用 Mixin 模式提供可组合的状态验证能力
- 每个 Mixin 负责一种状态的验证
- 模型可以混入多个 Mixin 以支持多种状态验证
- 通过约定的方法名（validate_xxx_status）实现自动发现

使用示例：
    # 单据状态验证
    class Inbound(DocumentStatusMixin, DataTableMixin, table=True):
        doc_status: str = Field(default="draft")

    # 货架状态验证
    class Shelf(ShelfStatusMixin, DataTableMixin, table=True):
        shelf_status: str = Field(default="empty")

    # 多状态验证
    class Container(DocumentStatusMixin, ContainerStatusMixin, DataTableMixin, table=True):
        doc_status: str = Field(default="draft")
        container_status: str = Field(default="empty")
"""


class DocumentStatusMixin:
    """
    单据状态验证 Mixin

    为模型提供单据状态验证能力。
    适用于：入库单、出库单、盘点单等单据类型。

    必需属性：
        doc_status: str - 单据状态字段

    提供方法：
        validate_document_status(operation: str) - 验证单据状态是否允许操作
    """

    # 类型提示（子类必须定义）
    doc_status: str

    def validate_document_status(self, operation: str) -> None:
        """
        验证单据状态是否允许操作

        Args:
            operation: 操作类型（"edit" 或 "delete"）

        Raises:
            ValueError: 当前状态不允许操作
        """
        from src.database.document_status import DocumentStateMachine

        if operation == "edit":
            DocumentStateMachine.validate_edit(self.doc_status)
        elif operation == "delete":
            DocumentStateMachine.validate_delete(self.doc_status)


class ShelfStatusMixin:
    """
    货架状态验证 Mixin

    为模型提供货架状态验证能力。
    适用于：货架、货位等存储位置。

    必需属性：
        shelf_status: str - 货架状态字段

    提供方法：
        validate_shelf_status(operation: str) - 验证货架状态是否允许操作

    状态说明：
        - empty: 空闲，可以存放货物
        - occupied: 已占用，不能删除
        - locked: 锁定，不能编辑或删除
        - maintenance: 维护中，不能编辑或删除
    """

    shelf_status: str

    def validate_shelf_status(self, operation: str) -> None:
        """
        验证货架状态是否允许操作

        Args:
            operation: 操作类型（"edit" 或 "delete"）

        Raises:
            ValueError: 当前状态不允许操作
        """
        # 可编辑的状态
        editable_statuses = {"empty", "occupied"}
        # 可删除的状态
        deletable_statuses = {"empty"}

        if operation == "edit":
            if self.shelf_status not in editable_statuses:
                raise ValueError(f"货架状态 [{self.shelf_status}] 不允许修改，只有 {editable_statuses} 状态可以修改")
        elif operation == "delete" and self.shelf_status not in deletable_statuses:
            raise ValueError(f"货架状态 [{self.shelf_status}] 不允许删除，只有 {deletable_statuses} 状态可以删除")


class ContainerStatusMixin:
    """
    料箱状态验证 Mixin

    为模型提供料箱状态验证能力。
    适用于：料箱、托盘等容器。

    必需属性：
        container_status: str - 料箱状态字段

    提供方法：
        validate_container_status(operation: str) - 验证料箱状态是否允许操作

    状态说明：
        - empty: 空箱，可以编辑和删除
        - loaded: 已装载，不能删除
        - in_transit: 运输中，不能编辑或删除
        - damaged: 损坏，只能删除
    """

    container_status: str

    def validate_container_status(self, operation: str) -> None:
        """
        验证料箱状态是否允许操作

        Args:
            operation: 操作类型（"edit" 或 "delete"）

        Raises:
            ValueError: 当前状态不允许操作
        """
        editable_statuses = {"empty", "loaded"}
        deletable_statuses = {"empty", "damaged"}

        if operation == "edit":
            if self.container_status not in editable_statuses:
                raise ValueError(
                    f"料箱状态 [{self.container_status}] 不允许修改，只有 {editable_statuses} 状态可以修改"
                )
        elif operation == "delete" and self.container_status not in deletable_statuses:
            raise ValueError(f"料箱状态 [{self.container_status}] 不允许删除，只有 {deletable_statuses} 状态可以删除")


class MaterialStatusMixin:
    """
    物料状态验证 Mixin

    为模型提供物料状态验证能力。
    适用于：物料、库存等。

    必需属性：
        material_status: str - 物料状态字段

    提供方法：
        validate_material_status(operation: str) - 验证物料状态是否允许操作

    状态说明：
        - available: 可用，可以编辑和删除
        - reserved: 已预留，不能删除
        - locked: 锁定，不能编辑或删除
        - expired: 过期，只能删除
    """

    material_status: str

    def validate_material_status(self, operation: str) -> None:
        """
        验证物料状态是否允许操作

        Args:
            operation: 操作类型（"edit" 或 "delete"）

        Raises:
            ValueError: 当前状态不允许操作
        """
        editable_statuses = {"available", "reserved"}
        deletable_statuses = {"available", "expired"}

        if operation == "edit":
            if self.material_status not in editable_statuses:
                raise ValueError(f"物料状态 [{self.material_status}] 不允许修改，只有 {editable_statuses} 状态可以修改")
        elif operation == "delete" and self.material_status not in deletable_statuses:
            raise ValueError(f"物料状态 [{self.material_status}] 不允许删除，只有 {deletable_statuses} 状态可以删除")


__all__ = [
    "ContainerStatusMixin",
    "DocumentStatusMixin",
    "MaterialStatusMixin",
    "ShelfStatusMixin",
]
