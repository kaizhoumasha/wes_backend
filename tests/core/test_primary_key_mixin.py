"""PrimaryKeyMixin 的数据库索引合同。"""

from __future__ import annotations

from typing import ClassVar

from src.core.mixins import DataTableMixin


class PrimaryKeyIndexContractModel(DataTableMixin, table=True):
    """只用于验证 DataTableMixin 的继承主键定义。"""

    __tablename__: ClassVar[str] = "primary_key_index_contract_models"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "wes_biz"}


def test_primary_key_mixin_does_not_create_redundant_unique_index() -> None:
    id_column = PrimaryKeyIndexContractModel.__table__.c.id
    id_indexes = [
        index
        for index in PrimaryKeyIndexContractModel.__table__.indexes
        if tuple(column.name for column in index.columns) == ("id",)
    ]

    assert id_column.primary_key is True
    assert id_column.nullable is False
    assert id_column.index in {False, None}
    assert id_column.unique in {False, None}
    assert id_indexes == []
