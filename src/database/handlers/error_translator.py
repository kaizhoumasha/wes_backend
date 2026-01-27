"""
数据库错误转换器

将数据库完整性约束错误转换为用户友好的中文提示。

支持的错误类型：
- 外键约束错误
- 唯一约束错误
- 非空约束错误
"""

import re
from typing import NoReturn

from sqlalchemy.exc import IntegrityError

from src.core.exceptions import ConflictException, ValidationException


class ErrorTranslator:
    """错误转换器"""

    def __init__(self, model: type):
        """
        初始化错误转换器

        Args:
            model: SQLModel 类
        """
        self.model = model

    def handle_integrity_error(self, e: IntegrityError) -> NoReturn:
        """
        统一处理数据库完整性约束错误

        将数据库错误转换为用户友好的中文提示。

        Args:
            e: IntegrityError 异常

        Raises:
            ConflictException: 资源冲突（重复、外键约束）
            ValidationException: 数据验证失败（非空约束）
        """
        error_msg = str(e.orig) if hasattr(e, "orig") else str(e)

        # 尝试匹配各种约束错误
        self._check_foreign_key_constraint(error_msg)
        self._check_duplicate_key_constraint(error_msg)
        self._check_not_null_constraint(error_msg)

        # 如果都未命中，抛出通用验证异常
        raise ValidationException(f"数据库操作失败: {error_msg}")

    def _check_foreign_key_constraint(self, error_msg: str) -> None:
        """
        检查外键约束错误

        Args:
            error_msg: 错误消息

        Raises:
            ConflictException: 友好的外键约束错误提示
        """
        # PostgreSQL 外键约束错误模式
        # 示例: update or delete on table "xxx" violates foreign key constraint "yyy" on table "zzz"
        pattern = r'violates foreign key constraint.*on table "([^"]+)"'
        match = re.search(pattern, error_msg, re.IGNORECASE)

        if match:
            table_name = match.group(1)
            table_cn_name = self._get_table_cn_name(table_name)
            raise ConflictException(
                f"当前删除的数据与[{table_cn_name}]中的数据有关联，请先删除[{table_cn_name}]关联的数据",
                detail={"constraint": "foreign_key", "related_table": table_name, "table_cn_name": table_cn_name},
            )

        # 另一种模式: still referenced from table "xxx"
        pattern2 = r'still referenced from table "([^"]+)"'
        match2 = re.search(pattern2, error_msg, re.IGNORECASE)

        if match2:
            table_name = match2.group(1)
            table_cn_name = self._get_table_cn_name(table_name)
            raise ConflictException(
                f"当前删除的数据与[{table_cn_name}]中的数据有关联，请先删除[{table_cn_name}]关联的数据",
                detail={"constraint": "foreign_key", "related_table": table_name, "table_cn_name": table_cn_name},
            )

    def _check_duplicate_key_constraint(self, error_msg: str) -> None:
        """
        检查唯一约束错误

        Args:
            error_msg: 错误消息

        Raises:
            ConflictException: 友好的唯一约束错误提示
        """
        # PostgreSQL 唯一约束错误模式
        # 示例: duplicate key value violates unique constraint "uq_xxx"
        # DETAIL:  Key (column1, column2)=(value1, value2) already exists.
        if "duplicate key value violates unique constraint" not in error_msg.lower():
            return

        # 提取字段名和值: Key (column1, column2)=(value1, value2)
        pattern = r"Key \(([^)]+)\)=\(([^)]+)\)"
        match = re.search(pattern, error_msg)

        if match:
            columns = match.group(1)
            values = match.group(2)
            # 分割多个字段和值
            column_list = [col.strip() for col in columns.split(",")]
            value_list = [val.strip().strip("'\"") for val in values.split(",")]
            # 转换字段为中文名称
            column_cn_names = [self._get_column_cn_name(col) for col in column_list]

            # 构建友好的错误消息（适用于创建和修改操作）
            if len(column_list) == 1:
                message = f"{column_cn_names[0]} '{value_list[0]}' 已被使用，请使用其他值"
            else:
                field_value_pairs = [f"{cn}='{val}'" for cn, val in zip(column_cn_names, value_list, strict=True)]
                message = f"{', '.join(field_value_pairs)} 组合已被使用，请使用其他值"

            raise ConflictException(
                message,
                detail={
                    "constraint": "unique",
                    "fields": column_list,
                    "field_cn_names": column_cn_names,
                    "values": value_list,
                },
            )

        # 如果无法提取字段名，使用通用提示
        raise ConflictException("数据已存在，请使用其他值", detail={"constraint": "unique"})

    def _check_not_null_constraint(self, error_msg: str) -> None:
        """
        检查非空约束错误

        Args:
            error_msg: 错误消息

        Raises:
            ValidationException: 友好的非空约束错误提示
        """
        # PostgreSQL 非空约束错误模式
        # 示例: null value in column "xxx" violates not-null constraint
        pattern = r'null value in column "([^"]+)" violates not-null constraint'
        match = re.search(pattern, error_msg, re.IGNORECASE)

        if match:
            column_name = match.group(1)
            column_cn_name = self._get_column_cn_name(column_name)
            raise ValidationException(
                f"[{column_cn_name}]不能为空",
                detail={"constraint": "not_null", "field": column_name, "field_cn_name": column_cn_name},
            )

    def _get_table_cn_name(self, table_en_name: str) -> str:
        """
        通过英文表名找到中文表名

        Args:
            table_en_name: 英文表名

        Returns:
            中文表名（如果找不到则返回英文表名）
        """
        # 尝试从模型的 metadata 中查找表的 comment
        if hasattr(self.model, "__table__"):
            table = self.model.__table__  # type: ignore[attr-defined]
            if table.name == table_en_name and table.comment:
                return table.comment

        # 如果找不到，返回英文表名
        return table_en_name

    def _get_column_cn_name(self, column_en_name: str) -> str:
        """
        通过英文字段名找到中文字段名

        Args:
            column_en_name: 英文字段名

        Returns:
            中文字段名（如果找不到则返回英文字段名）
        """
        # 尝试从模型的字段定义中获取 description 或 comment
        model_fields = getattr(self.model, "model_fields", None)
        if model_fields:
            field = model_fields.get(column_en_name)
            if field and hasattr(field, "description") and field.description:
                return field.description

        # 尝试从 SQLAlchemy 的 column comment 中获取
        if hasattr(self.model, "__table__"):
            table = self.model.__table__  # type: ignore[attr-defined]
            if column_en_name in table.columns:
                column = table.columns[column_en_name]
                if column.comment:
                    return column.comment

        # 如果找不到，返回英文字段名
        return column_en_name


__all__ = ["ErrorTranslator"]
