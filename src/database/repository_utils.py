"""Repository 公共小工具。"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, select

from src.core.logger import logger


def as_version(value: object) -> int | None:
    """仅在 value 为 int 时返回版本号。"""
    return value if isinstance(value, int) else None


def split_model_data(data: dict[str, Any], relation_info: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """按关系元数据拆分主表字段和关联字段。"""
    main_data = {key: value for key, value in data.items() if key not in relation_info}
    relation_data = {key: value for key, value in data.items() if key in relation_info}
    return main_data, relation_data


def has_soft_delete_mixin(model: object) -> bool:
    """检测模型是否混入了 SoftDeleteMixin。"""
    return (
        hasattr(model, "is_deleted")
        and callable(getattr(model, "soft_delete", None))
        and callable(getattr(model, "restore", None))
    )


def has_audit_model_mixin(model: type[Any]) -> bool:
    """检测模型是否混入了 AuditableMixin。"""
    return any(base.__name__ == "AuditableMixin" for base in model.__mro__)


def should_filter_deleted(model: object, include_deleted: bool) -> bool:
    """判断是否需要自动追加软删除过滤。"""
    return has_soft_delete_mixin(model) and not include_deleted


def has_relation_payload(data: dict[str, Any], relation_info: dict[str, Any]) -> bool:
    """判断更新数据中是否包含关系字段。"""
    return any(key in relation_info for key in data)


def capture_old_values(instance: Any, data: dict[str, Any], relation_info: dict[str, Any]) -> dict[str, Any]:
    """捕获更新前的字段值。"""
    old_values: dict[str, Any] = {}
    for key in data:
        if key not in relation_info and hasattr(instance, key):
            old_values[key] = getattr(instance, key)
    return old_values


def apply_model_updates(instance: Any, data: dict[str, Any], relation_info: dict[str, Any]) -> None:
    """将主表字段更新应用到实例上。"""
    for field, value in data.items():
        if field in relation_info or field == "version":
            continue
        if hasattr(instance, field):
            setattr(instance, field, value)


def capture_old_values_for_delete(instance: Any) -> dict[str, Any]:
    """捕获删除前字段旧值。"""
    old_values: dict[str, Any] = {}
    instance_type = cast("type[Any]", type(instance))
    model_fields = getattr(instance_type, "model_fields", None)
    if not model_fields:
        return old_values

    for field_name in model_fields:
        if hasattr(instance, field_name):
            try:
                old_values[field_name] = getattr(instance, field_name)
            except Exception as exc:
                logger.debug(f"无法获取字段 {field_name} 的值: {exc}")
                continue
    return old_values


def analyze_update_data(model: type[Any], data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """分析更新数据中的关系字段。"""
    from src.database.relation_metadata import RelationMetadata

    relation_info = RelationMetadata.get_relation_info(model)
    return relation_info, has_relation_payload(data, relation_info)


def validate_version_before_update(
    instance: Any,
    model_name: str,
    resource_id: int,
    data: dict[str, Any],
) -> None:
    """在 update 主流程前执行乐观锁快速校验。"""
    if not hasattr(instance, "version") or "version" not in data:
        return

    current_version = as_version(getattr(instance, "version", None))
    provided_version_raw = data["version"]
    provided_version = as_version(provided_version_raw)
    if current_version == provided_version_raw:
        return

    from src.core.exceptions import OptimisticLockException

    raise OptimisticLockException(
        resource_type=model_name,
        resource_id=resource_id,
        current_version=current_version,
        provided_version=provided_version,
    )


def increment_instance_version(instance: Any, model_name: str, pk_column: str) -> None:
    """若实例支持乐观锁，则递增其版本号。"""
    if not hasattr(instance, "increment_version"):
        return

    old_version = instance.version
    instance.increment_version()
    pk_value = getattr(instance, pk_column)
    logger.debug(f"乐观锁：{model_name} (ID: {pk_value}) 版本号从 {old_version} 递增到 {instance.version}")


def require_soft_delete_support(model: object, model_name: str) -> None:
    """确保模型支持软删除。"""
    if not has_soft_delete_mixin(model):
        raise ValueError(f"{model_name} 不支持软删除（未混入 SoftDeleteMixin）")


def require_existing_instance(instance: Any, model_name: str) -> None:
    """确保实例存在。"""
    if instance is None:
        raise ValueError(f"{model_name} 不存在")


def require_deleted_instance(instance: Any, model_name: str) -> None:
    """确保实例当前处于已删除状态。"""
    if not instance.is_deleted:
        raise ValueError(f"{model_name} 未被删除，无需恢复")


def build_deleted_count_query(pk_attr: Any, model: Any) -> Any:
    """构建已删除记录统计查询。"""
    return select(func.count(pk_attr)).where(model.is_deleted)


def build_deleted_items_query(model: Any, limit: int, offset: int) -> Any:
    """构建已删除记录列表查询。"""
    return select(model).where(model.is_deleted).order_by(model.deleted_at.desc()).offset(offset).limit(limit)


def collect_one_to_many_delete_batches(model: Any, instance: Any) -> list[tuple[str, Any, set[int]]]:
    """收集一对多关联的待删除对象批次。"""
    from src.database.relation_metadata import RelationMetadata, RelationType

    if not RelationMetadata.has_relations(model):
        return []

    batches: list[tuple[str, Any, set[int]]] = []
    relation_info = RelationMetadata.get_relation_info(model)
    for relation_name, info in relation_info.items():
        relation_type = info.get("relation_type", "ONETOMANY")
        if relation_type != RelationType.ONETOMANY:
            continue

        relation_attr = getattr(model, relation_name, None)
        if not relation_attr:
            continue

        current_relations = getattr(instance, relation_name, [])
        if not current_relations:
            continue

        ids_to_delete = {rel.id for rel in current_relations if hasattr(rel, "id") and rel.id is not None}
        if ids_to_delete:
            batches.append((relation_name, relation_attr, ids_to_delete))

    return batches


__all__ = [
    "analyze_update_data",
    "apply_model_updates",
    "as_version",
    "build_deleted_count_query",
    "build_deleted_items_query",
    "capture_old_values",
    "capture_old_values_for_delete",
    "collect_one_to_many_delete_batches",
    "has_audit_model_mixin",
    "has_relation_payload",
    "has_soft_delete_mixin",
    "increment_instance_version",
    "require_deleted_instance",
    "require_existing_instance",
    "require_soft_delete_support",
    "should_filter_deleted",
    "split_model_data",
    "validate_version_before_update",
]
