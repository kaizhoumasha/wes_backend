"""
动态模型工厂

用于创建 Update/Create 等 Schema 模型，避免重复代码
"""

from datetime import datetime
from typing import Any, ClassVar, Union, cast, get_args, get_origin, get_type_hints

from pydantic import ConfigDict
from pydantic.fields import PydanticUndefined
from sqlmodel import Field

from src.core.mixins import BaseMixin


def _is_optional_type(field_type: Any) -> bool:
    """检查类型是否为 Optional（即 Union[T, None] 或 T | None）"""
    if field_type is None:
        return True
    origin = get_origin(field_type)
    if origin is Union:
        args = get_args(field_type)
        return len(args) == 2 and type(None) in args
    if origin is UnionType:  # Python 3.10+ 的 T | None 语法
        args = get_args(field_type)
        return type(None) in args
    return False


# Python 3.10+ 的 Union 类型（用于 T | None 语法）
try:
    from types import UnionType
except ImportError:
    UnionType = type(None)


class ModelFactory:
    """Update 模型工厂类（单例模式）"""

    # 类级别的实例缓存，为每个 base_model 保存唯一工厂实例
    _instances: ClassVar[dict[type[BaseMixin], "ModelFactory"]] = {}

    def __new__(cls, base_model: type[BaseMixin]) -> "ModelFactory":
        """单例模式：为每个 base_model 只创建一个工厂实例"""
        if base_model not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[base_model] = instance
        return cls._instances[base_model]

    def __init__(self, base_model: type[BaseMixin]):
        """初始化工厂（单例模式下只会执行一次）"""
        # 避免重复初始化
        if not hasattr(self, "_initialized"):
            self.base_model = base_model
            self._cache: dict[object, type[BaseMixin]] = {}
            self._initialized = True

    def create_model(  # noqa: PLR0912
        self,
        base_model: type[BaseMixin] | None = None,
        model_name: str | None = None,
        exclude_fields: set[str] | None = None,
        make_optional: bool = True,
        keep_required: set[str] | None = None,
        add_timestamps: bool = False,
        config_dict: dict[str, Any] | None = None,
    ) -> type[BaseMixin]:
        """
        根据基础模型创建对应的 Update 模型

        Args:
            base_model: 基础模型类（如果为 None，使用工厂的 base_model）
            model_name: 生成的模型名称
            exclude_fields: 要排除的字段集合（如 id, created_at 等）
            make_optional: 是否将字段改为 Optional
            keep_required: 即使 make_optional=True，这些字段也保持必需
            add_timestamps: 是否添加 updated_at 时间戳
            config_dict: 额外的模型配置

        Returns:
            生成的 Update 模型类
        """
        # 使用传入的 base_model 或默认使用工厂的 base_model
        model = base_model if base_model is not None else self.base_model

        if model_name is None:
            model_name = f"{model.__name__}Update"

        if exclude_fields is None:
            exclude_fields = set()

        if keep_required is None:
            keep_required = set()

        # 准备字段定义（使用 (type, Field) 元组格式）
        fields: dict[str, tuple[Any, Any]] = {}

        # 获取基础模型的类型提示
        type_hints: dict[str, Any] = get_type_hints(model, include_extras=True)

        # 处理基础模型的字段
        for field_name, field_info in model.model_fields.items():
            if field_name in exclude_fields:
                continue

            # 获取字段类型
            field_type = type_hints.get(field_name, field_info.annotation)

            # 构建字段属性
            field_kwargs: dict[str, Any] = {}

            # 复制元数据
            if field_info.description:
                field_kwargs["description"] = field_info.description

            if field_info.title:
                field_kwargs["title"] = field_info.title

            # 复制验证约束（从 FieldInfo 的 metadata 或直接属性）
            # 这些约束对于 OpenAPI 生成和前端验证同步至关重要
            constraint_attrs = [
                # 字符串约束
                "min_length",
                "max_length",
                "regex",
                # 数值约束
                "ge",
                "gt",
                "le",
                "lt",
                # 其他约束
                "frozen",
                "schema_extra",
            ]

            for attr in constraint_attrs:
                if hasattr(field_info, attr):
                    value = getattr(field_info, attr)
                    if value is not None:
                        field_kwargs[attr] = value

            # 从 metadata 中提取约束（处理 Field() 嵌套）
            if field_info.metadata:
                for metadata in field_info.metadata:
                    for attr in constraint_attrs:
                        if hasattr(metadata, attr):
                            value = getattr(metadata, attr)
                            if value is not None and attr not in field_kwargs:
                                field_kwargs[attr] = value

            # 决定如何处理默认值
            is_required = field_info.is_required()
            already_optional = _is_optional_type(field_type)

            # 初始化 final_type 和默认值
            final_type = field_type
            default_value = ...

            if make_optional and field_name not in keep_required:
                # Update 模型：所有字段都改为可选
                final_type = field_type | None if not already_optional else field_type
                default_value = None
            elif field_info.default_factory is not None:
                # Create 模型需要保留 default_factory 语义：字段可省略，缺省时自动生成默认值。
                field_kwargs["default_factory"] = field_info.default_factory
            elif field_info.default is not PydanticUndefined and field_info.default != ...:
                # 保持原默认值
                default_value = field_info.default
            elif is_required and not already_optional:
                # 保持必需
                default_value = ...
            else:
                # 原本就是可选字段，保持默认值为 None
                default_value = None

            # 设置默认值（如果没有 default_factory）
            if "default_factory" not in field_kwargs:
                field_kwargs["default"] = default_value

            fields[field_name] = (final_type, cast("Any", Field(**field_kwargs)))

        # 添加时间戳字段
        if add_timestamps:
            fields["updated_at"] = (datetime | None, Field(default=None, description="更新时间"))

        # 合并配置
        model_config_dict: dict[str, Any] = {
            "extra": "forbid",
            "from_attributes": True,
            "str_strip_whitespace": True,
        }

        if config_dict:
            model_config_dict.update(config_dict)

        # 使用 type() 创建类，继承自 base_model（保留所有验证器）
        # 然后在 namespace 中覆盖字段定义以修改可选性
        namespace: dict[str, Any] = {
            "model_config": ConfigDict(**model_config_dict),
            "__annotations__": {},
        }

        # 添加字段到 namespace（覆盖基类的字段定义）
        for field_name, (field_type, field_obj) in fields.items():
            # 设置类型注解
            namespace["__annotations__"][field_name] = field_type
            # 设置字段对象
            namespace[field_name] = field_obj

        # 创建新类，继承自 base_model 和 BaseMixin
        # 这样可以保留 base_model 的所有验证器
        return cast("type[BaseMixin]", type(model_name, (model, BaseMixin), namespace))

    def create(
        self,
        name_suffix: str = "Update",
        exclude: tuple[str, ...] | None = None,
        make_optional: bool = True,
        keep_required: tuple[str, ...] | None = None,
        add_timestamps: bool = False,
    ) -> type[BaseMixin]:
        """
        创建 Update 模型

        Args:
            name_suffix: 模型名称后缀
            exclude: 排除的字段元组
            make_optional: 是否使字段可选
            keep_required: 保持必需的字段元组
            add_timestamps: 是否添加时间戳
        """
        cache_key = (name_suffix, exclude, make_optional, keep_required, add_timestamps)

        if cache_key not in self._cache:
            exclude_set: set[str] = set(exclude or ())
            keep_required_set: set[str] = set(keep_required or ())

            self._cache[cache_key] = self.create_model(
                base_model=self.base_model,
                model_name=f"{self.base_model.__name__}{name_suffix}",
                exclude_fields=exclude_set,
                make_optional=make_optional,
                keep_required=keep_required_set,
                add_timestamps=add_timestamps,
            )

        return self._cache[cache_key]

    def for_create(
        self,
        exclude: tuple[str, ...] | None = None,
    ) -> type[BaseMixin]:
        """创建用于创建的模型（所有字段必需）"""
        if exclude:
            exclude = tuple(set(exclude) - {"id", "created_at", "updated_at"})
        else:
            exclude = ("id", "created_at", "updated_at")
        return self.create(name_suffix="Create", exclude=exclude, make_optional=False)

    def for_update(
        self,
        exclude: tuple[str, ...] | None = None,
    ) -> type[BaseMixin]:
        """创建用于更新的模型（所有字段可选）"""
        if exclude:
            exclude = tuple(set(exclude) - {"id", "created_at", "updated_at"})
        else:
            exclude = ("id", "created_at", "updated_at")
        return self.create(name_suffix="Update", exclude=exclude, make_optional=True)

    def for_optimistic_update(
        self,
        exclude: tuple[str, ...] | None = None,
    ) -> type[BaseMixin]:
        """创建用于乐观锁更新的模型（所有字段可选，但 version 必填）"""
        if exclude:
            exclude = tuple(set(exclude) - {"id", "created_at", "updated_at", "version"})
        else:
            exclude = ("id", "created_at", "updated_at")

        cache_key = ("OptimisticUpdate", exclude)

        if cache_key not in self._cache:
            update_model = self.create(
                name_suffix="OptimisticUpdateBase",
                exclude=exclude,
                make_optional=True,
            )
            namespace: dict[str, Any] = {
                "__annotations__": {"version": int},
                "version": Field(..., description="乐观锁版本号，更新时必传"),
            }
            self._cache[cache_key] = type(
                f"{self.base_model.__name__}OptimisticUpdate",
                (update_model, BaseMixin),
                namespace,
            )

        return self._cache[cache_key]
