"""
OpenAPI 相关工具。

集中定义 operationId 生成规则，保证前后端基于同一套稳定命名约定。
"""

import re

from fastapi.routing import APIRoute

_PATH_VERSION_PATTERN = re.compile(r"^v\d+$")
_NON_IDENTIFIER_PATTERN = re.compile(r"[^a-zA-Z0-9]+")
_MULTI_UNDERSCORE_PATTERN = re.compile(r"_+")


def normalize_operation_id_part(value: str) -> str:
    """将路径段或名称归一化为 snake_case 风格的 operationId 片段。"""
    normalized = _NON_IDENTIFIER_PATTERN.sub("_", value.strip().lower()).strip("_")
    return _MULTI_UNDERSCORE_PATTERN.sub("_", normalized)


def generate_route_operation_id(route: APIRoute) -> str:
    """为未显式指定 operation_id 的路由生成稳定且紧凑的 operationId。"""
    method = next(iter(sorted(route.methods - {"HEAD", "OPTIONS"})), "route").lower()
    parts: list[str] = []

    for raw_part in route.path_format.strip("/").split("/"):
        if not raw_part or raw_part == "api" or _PATH_VERSION_PATTERN.fullmatch(raw_part):
            continue
        if raw_part.startswith("{") and raw_part.endswith("}"):
            param_name = normalize_operation_id_part(raw_part[1:-1])
            if param_name:
                parts.append(f"by_{param_name}")
            continue

        part = normalize_operation_id_part(raw_part)
        if part:
            parts.append(part)

    return "_".join([*parts, method]) if parts else method


__all__ = ["generate_route_operation_id", "normalize_operation_id_part"]
