"""
前端路由菜单解析工具

将前端 `src/router/index.ts` 中的受保护路由解析为后端菜单同步数据。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRONTEND_ROOT = BACKEND_ROOT.parent / "wes_frontend"
DEFAULT_MENU_MANIFEST_RELATIVE_PATH = Path("artifacts/menu-manifest.json")
ROUTE_FILE_RELATIVE_PATH = Path("src/router/index.ts")
ROUTES_DIRECTORY_RELATIVE_PATH = Path("src/router/routes")

_MENU_NAME_SUFFIXES = {"list", "page", "view", "detail", "form", "screen", "route", "index"}


@dataclass(slots=True)
class FrontendMenuDefinition:
    """前端路由解析后的菜单定义"""

    name: str
    title: str
    path: str
    component: str | None
    sort_order: int
    parent_name: str | None = None
    icon: str | None = None
    is_hidden: bool = False
    permission: str | None = None

    def to_model_data(self, parent_id: int | None = None) -> dict[str, object]:
        """转换为 Menu 模型需要的数据结构"""

        return {
            "name": self.name,
            "title": self.title,
            "path": self.path,
            "component": self.component,
            "icon": self.icon,
            "parent_id": parent_id,
            "sort_order": self.sort_order,
            "is_hidden": self.is_hidden,
        }


@dataclass(slots=True)
class _RouteNode:
    path: str | None
    name: str | None
    title: str | None
    requires_auth: bool | None
    component: str | None
    permission: str | None
    menu_name: str | None
    menu_title: str | None
    menu_icon: str | None
    menu_parent_name: str | None
    menu_sort_order: int | None
    menu_hidden: bool | None
    children: list[_RouteNode]


def resolve_frontend_root(frontend_path: str | Path | None = None) -> Path:
    """解析前端项目根目录"""

    raw_path = frontend_path or os.getenv("WES_FRONTEND_PATH") or DEFAULT_FRONTEND_ROOT
    return Path(raw_path).expanduser()


def load_frontend_router_menus(
    frontend_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> list[FrontendMenuDefinition]:
    """从前端 router 文件加载菜单定义"""

    if manifest_path is not None:
        return _load_frontend_menu_manifest(Path(manifest_path).expanduser())

    frontend_root = resolve_frontend_root(frontend_path)
    manifest_file = frontend_root / DEFAULT_MENU_MANIFEST_RELATIVE_PATH
    if manifest_file.exists():
        return _load_frontend_menu_manifest(manifest_file)

    router_file = frontend_root / ROUTE_FILE_RELATIVE_PATH

    if not router_file.exists():
        raise FileNotFoundError(f"前端路由文件不存在: {router_file}")

    source = router_file.read_text(encoding="utf-8")
    try:
        return parse_frontend_router_menus(source)
    except ValueError as exc:
        if "const routes" not in str(exc):
            raise
        return _load_frontend_router_menus_from_modules(frontend_root)


def _load_frontend_menu_manifest(manifest_path: Path) -> list[FrontendMenuDefinition]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"前端菜单清单不存在: {manifest_path}")

    raw_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, list):
        raise TypeError(f"前端菜单清单格式错误，应为数组: {manifest_path}")

    definitions: list[FrontendMenuDefinition] = []
    for index, item in enumerate(raw_payload):
        if not isinstance(item, dict):
            raise TypeError(f"前端菜单清单第 {index + 1} 项格式错误，应为对象")

        definitions.append(
            FrontendMenuDefinition(
                name=_require_manifest_string(item, "name", index),
                title=_require_manifest_string(item, "title", index),
                path=_require_manifest_string(item, "path", index),
                component=_optional_manifest_string(item.get("component")),
                sort_order=_require_manifest_int(item, "sortOrder", index),
                parent_name=_optional_manifest_string(item.get("parentName")),
                icon=_optional_manifest_string(item.get("icon")),
                is_hidden=_optional_manifest_bool(item.get("isHidden")) or False,
                permission=_optional_manifest_string(item.get("permission")),
            )
        )

    _validate_menu_definitions(definitions)
    return definitions


def _require_manifest_string(item: dict[str, object], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"前端菜单清单第 {index + 1} 项缺少有效字段 `{key}`")
    return value


def _optional_manifest_string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _require_manifest_int(item: dict[str, object], key: str, index: int) -> int:
    value = item.get(key)
    if not isinstance(value, int):
        raise TypeError(f"前端菜单清单第 {index + 1} 项缺少有效字段 `{key}`")
    return value


def _optional_manifest_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def parse_frontend_router_menus(source: str) -> list[FrontendMenuDefinition]:
    """解析 router 源码中的菜单定义"""

    routes_array = _extract_routes_array(source)
    routes = [_parse_route_object(route_source) for route_source in _extract_route_objects_from_array(routes_array)]
    return _build_menu_definitions(routes)


def _load_frontend_router_menus_from_modules(frontend_root: Path) -> list[FrontendMenuDefinition]:
    routes_dir = frontend_root / ROUTES_DIRECTORY_RELATIVE_PATH
    if not routes_dir.exists():
        raise FileNotFoundError(f"前端路由目录不存在: {routes_dir}")

    routes_index_path = routes_dir / "index.ts"
    module_cache: dict[str, str] = {}
    if routes_index_path.exists():
        index_source = routes_index_path.read_text(encoding="utf-8")
        imported_symbols = _parse_route_module_imports(index_source)
        try:
            routes_array = _extract_exported_function_return_array(index_source, "createRoutes")
            routes = _resolve_route_nodes_from_array_literal(routes_array, routes_dir, imported_symbols, module_cache)
            if routes:
                return _build_menu_definitions(routes)
        except ValueError:
            pass

    module_exports = [
        ("base.ts", ["shellBaseChildren"]),
        ("admin.ts", ["adminRoutes"]),
        ("biz.ts", ["bizRoutes"]),
        ("api-auth.ts", ["apiAuthRoutes"]),
        ("runtime.ts", ["runtimeRoutes"]),
        ("logs.ts", ["logRoutes"]),
    ]
    imported_symbols = {
        export_name: file_name for file_name, export_names in module_exports for export_name in export_names
    }
    routes: list[_RouteNode] = []
    for file_name, export_names in module_exports:
        module_path = routes_dir / file_name
        if not module_path.exists():
            continue

        module_source = _load_route_module_source(routes_dir, file_name, module_cache)
        for export_name in export_names:
            routes.extend(
                _resolve_route_nodes_from_expression(
                    export_name, routes_dir, imported_symbols, module_cache, module_source
                )
            )

    return _build_menu_definitions(routes)


def _parse_route_module_imports(source: str) -> dict[str, str]:
    imports: dict[str, str] = {}
    import_pattern = re.compile(r"import\s*{(?P<names>[^}]+)}\s*from\s*['\"](?P<module>\./[^'\"]+)['\"]")

    for match in import_pattern.finditer(source):
        module_name = match.group("module")[2:]
        file_name = module_name if module_name.endswith(".ts") else f"{module_name}.ts"
        for name in (_normalize_import_name(part) for part in match.group("names").split(",")):
            if name:
                imports[name] = file_name

    return imports


def _normalize_import_name(raw_name: str) -> str | None:
    stripped = raw_name.strip()
    if not stripped:
        return None

    return stripped.split(" as ", 1)[0].strip()


def _looks_like_route_export(name: str) -> bool:
    return name.endswith(("Routes", "Route", "Children"))


def _load_route_module_source(routes_dir: Path, file_name: str, module_cache: dict[str, str]) -> str:
    if file_name not in module_cache:
        module_cache[file_name] = (routes_dir / file_name).read_text(encoding="utf-8")
    return module_cache[file_name]


def _resolve_route_nodes_from_array_literal(
    array_literal: str,
    routes_dir: Path,
    imported_symbols: dict[str, str],
    module_cache: dict[str, str],
) -> list[_RouteNode]:
    inner = _strip_outer(array_literal, "[", "]")
    routes: list[_RouteNode] = []

    for item in _split_top_level(inner):
        stripped = _strip_leading_comments(item)
        if not stripped:
            continue

        expression = stripped[3:].strip() if stripped.startswith("...") else stripped
        routes.extend(_resolve_route_nodes_from_expression(expression, routes_dir, imported_symbols, module_cache))

    return routes


def _resolve_route_nodes_from_expression(
    expression: str,
    routes_dir: Path,
    imported_symbols: dict[str, str],
    module_cache: dict[str, str],
    module_source: str | None = None,
) -> list[_RouteNode]:
    stripped = expression.strip()
    if not stripped:
        return []

    if stripped.startswith("{"):
        return [_build_route_node_from_source(stripped, routes_dir, imported_symbols, module_cache)]

    if stripped.startswith("["):
        return _resolve_route_nodes_from_array_literal(stripped, routes_dir, imported_symbols, module_cache)

    name = _extract_expression_name(stripped)
    if not name:
        return []

    module_file = imported_symbols.get(name)
    if module_file is None:
        return []

    source = module_source or _load_route_module_source(routes_dir, module_file, module_cache)

    try:
        if "(" in stripped and stripped.endswith(")"):
            literal = _extract_exported_function_return_array(source, name)
        else:
            literal = _extract_exported_literal(source, name)
    except ValueError:
        return []

    if literal.startswith("["):
        return _resolve_route_nodes_from_array_literal(literal, routes_dir, imported_symbols, module_cache)

    return [_build_route_node_from_source(literal, routes_dir, imported_symbols, module_cache)]


def _extract_expression_name(expression: str) -> str | None:
    match = re.match(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)", expression.strip())
    return match.group("name") if match else None


def _build_route_node_from_source(
    route_source: str,
    routes_dir: Path,
    imported_symbols: dict[str, str],
    module_cache: dict[str, str],
) -> _RouteNode:
    props = _parse_object_literal_with_spreads(route_source, routes_dir, imported_symbols, module_cache)
    meta_props = _parse_object_literal(props.get("meta")) if props.get("meta") else {}
    menu_props = _parse_object_literal(meta_props.get("menu")) if meta_props.get("menu") else {}

    children: list[_RouteNode] = []
    if children_source := props.get("children"):
        children = _resolve_route_nodes_from_array_literal(children_source, routes_dir, imported_symbols, module_cache)

    return _RouteNode(
        path=_parse_string_literal(props.get("path")),
        name=_parse_string_literal(props.get("name")),
        title=_parse_string_literal(meta_props.get("title")),
        requires_auth=_parse_bool_literal(meta_props.get("requiresAuth")),
        component=_parse_component_path(props.get("component")),
        permission=_parse_permission(meta_props),
        menu_name=_parse_string_literal(menu_props.get("name")) or _parse_string_literal(meta_props.get("menuName")),
        menu_title=_parse_string_literal(menu_props.get("title")),
        menu_icon=_parse_string_literal(menu_props.get("icon")) or _parse_string_literal(meta_props.get("icon")),
        menu_parent_name=(
            _parse_string_literal(menu_props.get("parentName"))
            or _parse_string_literal(menu_props.get("parent_name"))
            or _parse_string_literal(meta_props.get("parentName"))
        ),
        menu_sort_order=(
            _parse_int_literal(menu_props.get("sortOrder"))
            or _parse_int_literal(menu_props.get("sort_order"))
            or _parse_int_literal(meta_props.get("sortOrder"))
        ),
        menu_hidden=_parse_bool_literal(menu_props.get("hidden"))
        if menu_props.get("hidden") is not None
        else _parse_bool_literal(meta_props.get("hidden")),
        children=children,
    )


def _parse_object_literal_with_spreads(
    source: str,
    routes_dir: Path,
    imported_symbols: dict[str, str],
    module_cache: dict[str, str],
) -> dict[str, str]:
    stripped = source.strip()
    if not stripped.startswith("{"):
        return {}

    body = _strip_outer(stripped, "{", "}")
    props: dict[str, str] = {}

    for item in _split_top_level(body):
        stripped_item = _strip_leading_comments(item)
        if not stripped_item:
            continue

        if stripped_item.startswith("..."):
            spread_literal = _resolve_spread_object_literal(
                stripped_item[3:].strip(), routes_dir, imported_symbols, module_cache
            )
            if spread_literal is not None:
                props.update(
                    _parse_object_literal_with_spreads(spread_literal, routes_dir, imported_symbols, module_cache)
                )
            continue

        key, value = _split_key_value(stripped_item)
        if not key:
            continue
        props[_normalize_property_key(key)] = value.strip()

    return props


def _resolve_spread_object_literal(
    expression: str,
    routes_dir: Path,
    imported_symbols: dict[str, str],
    module_cache: dict[str, str],
) -> str | None:
    stripped = expression.strip()
    if stripped.startswith("{"):
        return stripped

    name = _extract_expression_name(stripped)
    if not name:
        return None

    module_file = imported_symbols.get(name)
    if module_file is None:
        return None

    source = _load_route_module_source(routes_dir, module_file, module_cache)
    try:
        literal = _extract_exported_literal(source, name)
    except ValueError:
        return None

    return literal if literal.startswith("{") else None


def _build_menu_definitions(routes: list[_RouteNode]) -> list[FrontendMenuDefinition]:
    definitions: list[FrontendMenuDefinition] = []
    sort_counter = 0

    def walk(
        route_nodes: list[_RouteNode], parent_path: str, inherited_auth: bool, parent_menu_name: str | None
    ) -> None:
        nonlocal sort_counter

        for route in route_nodes:
            full_path = _join_paths(parent_path, route.path)
            effective_auth = route.requires_auth if route.requires_auth is not None else inherited_auth
            title = route.menu_title or route.title
            include_as_menu = effective_auth and bool(title)

            current_menu_name = parent_menu_name
            if include_as_menu:
                sort_counter += 1
                menu_name = route.menu_name or _derive_menu_name(
                    route_name=route.name,
                    route_path=full_path,
                    permission=route.permission,
                )
                definition = FrontendMenuDefinition(
                    name=menu_name,
                    title=title or "",
                    path=full_path,
                    component=route.component,
                    sort_order=route.menu_sort_order if route.menu_sort_order is not None else sort_counter,
                    parent_name=route.menu_parent_name or parent_menu_name,
                    icon=route.menu_icon,
                    is_hidden=route.menu_hidden if route.menu_hidden is not None else False,
                )
                definitions.append(definition)
                current_menu_name = definition.name

            if route.children:
                walk(route.children, full_path, effective_auth, current_menu_name)

    walk(routes, parent_path="", inherited_auth=False, parent_menu_name=None)
    _validate_menu_definitions(definitions)
    return definitions


def _extract_exported_literal(source: str, export_name: str) -> str:
    match = re.search(rf"\bexport\s+const\s+{re.escape(export_name)}\b", source)
    if not match:
        raise ValueError(f"未找到导出常量: {export_name}")

    equals_index = source.find("=", match.end())
    if equals_index == -1:
        raise ValueError(f"未找到 {export_name} 的赋值语句")

    object_start = source.find("{", equals_index)
    array_start = source.find("[", equals_index)
    candidates = [index for index in (object_start, array_start) if index != -1]
    if not candidates:
        raise ValueError(f"未找到 {export_name} 的字面量")

    literal_start = min(candidates)
    if literal_start == object_start:
        literal, _ = _extract_balanced(source, literal_start, "{", "}")
        return literal

    literal, _ = _extract_balanced(source, literal_start, "[", "]")
    return literal


def _extract_exported_function_return_array(source: str, function_name: str) -> str:
    match = re.search(rf"\bexport\s+function\s+{re.escape(function_name)}\b", source)
    if not match:
        raise ValueError(f"未找到导出函数: {function_name}")

    body_start = source.find("{", match.end())
    if body_start == -1:
        raise ValueError(f"未找到函数体: {function_name}")

    function_body, _ = _extract_balanced(source, body_start, "{", "}")
    return_matches = list(re.finditer(r"\breturn\b", function_body))
    for return_match in reversed(return_matches):
        array_start = function_body.find("[", return_match.end())
        if array_start == -1:
            continue
        balanced = _try_extract_balanced(function_body, array_start, "[", "]")
        if balanced is None:
            continue
        array_literal, _ = balanced
        return array_literal

    raise ValueError(f"未找到 {function_name} 的返回数组")


def _extract_routes_array(source: str) -> str:
    match = re.search(r"\bconst\s+routes\b", source)
    if not match:
        raise ValueError("未找到 `const routes` 定义")

    equals_index = source.find("=", match.end())
    if equals_index == -1:
        raise ValueError("未找到 routes 数组赋值语句")

    array_start = source.find("[", equals_index)
    if array_start == -1:
        raise ValueError("未找到 routes 数组字面量")

    array_literal, _ = _extract_balanced(source, array_start, "[", "]")
    return array_literal


def _extract_route_objects_from_array(array_literal: str) -> list[str]:
    inner = _strip_outer(array_literal, "[", "]")
    route_objects: list[str] = []

    for item in _split_top_level(inner):
        stripped = _strip_leading_comments(item)
        if not stripped:
            continue

        if stripped.startswith("{"):
            route_objects.append(_extract_first_object(stripped))
            continue

        route_objects.extend(_extract_objects_from_expression(stripped))

    return route_objects


def _extract_objects_from_expression(expression: str) -> list[str]:
    objects: list[str] = []
    index = 0

    while index < len(expression):
        char = expression[index]
        if char == "[":
            array_literal, next_index = _extract_balanced(expression, index, "[", "]")
            objects.extend(_extract_route_objects_from_array(array_literal))
            index = next_index
            continue
        index += 1

    return objects


def _extract_first_object(source: str) -> str:
    start = source.find("{")
    if start == -1:
        raise ValueError("对象字面量格式错误")
    object_literal, _ = _extract_balanced(source, start, "{", "}")
    return object_literal


def _parse_route_object(route_source: str) -> _RouteNode:
    props = _parse_object_literal(route_source)
    meta_props = _parse_object_literal(props.get("meta")) if props.get("meta") else {}
    menu_props = _parse_object_literal(meta_props.get("menu")) if meta_props.get("menu") else {}

    children: list[_RouteNode] = []
    if children_source := props.get("children"):
        children = [_parse_route_object(item) for item in _extract_route_objects_from_array(children_source)]

    return _RouteNode(
        path=_parse_string_literal(props.get("path")),
        name=_parse_string_literal(props.get("name")),
        title=_parse_string_literal(meta_props.get("title")),
        requires_auth=_parse_bool_literal(meta_props.get("requiresAuth")),
        component=_parse_component_path(props.get("component")),
        permission=_parse_permission(meta_props),
        menu_name=_parse_string_literal(menu_props.get("name")) or _parse_string_literal(meta_props.get("menuName")),
        menu_title=_parse_string_literal(menu_props.get("title")),
        menu_icon=_parse_string_literal(menu_props.get("icon")) or _parse_string_literal(meta_props.get("icon")),
        menu_parent_name=(
            _parse_string_literal(menu_props.get("parentName"))
            or _parse_string_literal(menu_props.get("parent_name"))
            or _parse_string_literal(meta_props.get("parentName"))
        ),
        menu_sort_order=(
            _parse_int_literal(menu_props.get("sortOrder"))
            or _parse_int_literal(menu_props.get("sort_order"))
            or _parse_int_literal(meta_props.get("sortOrder"))
        ),
        menu_hidden=_parse_bool_literal(menu_props.get("hidden"))
        if menu_props.get("hidden") is not None
        else _parse_bool_literal(meta_props.get("hidden")),
        children=children,
    )


def _parse_permission(meta_props: dict[str, str]) -> str | None:
    permission = meta_props.get("permission")
    if permission:
        return permission.strip()

    permissions = meta_props.get("permissions")
    if not permissions:
        return None

    return next((item.strip() for item in _split_top_level(_strip_outer(permissions, "[", "]")) if item.strip()), None)


def _parse_object_literal(source: str | None) -> dict[str, str]:
    if not source:
        return {}

    stripped = source.strip()
    if not stripped.startswith("{"):
        return {}

    body = _strip_outer(stripped, "{", "}")
    props: dict[str, str] = {}

    for item in _split_top_level(body):
        key, value = _split_key_value(item)
        if not key:
            continue
        props[_normalize_property_key(key)] = value.strip()

    return props


def _split_key_value(item: str) -> tuple[str | None, str]:
    stripped = _strip_leading_comments(item)
    if not stripped or stripped.startswith("..."):
        return None, ""

    index = _find_top_level_character(stripped, ":")
    if index == -1:
        return None, ""

    return stripped[:index], stripped[index + 1 :]


def _normalize_property_key(key: str) -> str:
    stripped = key.strip()
    if stripped.startswith(("'", '"', "`")) and stripped.endswith(("'", '"', "`")):
        return stripped[1:-1]
    return stripped


def _parse_string_literal(value: str | None) -> str | None:
    if value is None:
        return None

    stripped = value.strip()
    if len(stripped) < 2:
        return None

    quote = stripped[0]
    if quote not in {"'", '"', "`"} or stripped[-1] != quote:
        return None

    return (
        stripped[1:-1]
        .replace(r"\/", "/")
        .replace(r"\'", "'")
        .replace(r"\"", '"')
        .replace(r"\`", "`")
        .replace(r"\\", "\\")
    )


def _parse_bool_literal(value: str | None) -> bool | None:
    if value is None:
        return None

    stripped = value.strip()
    if stripped == "true":
        return True
    if stripped == "false":
        return False
    return None


def _parse_int_literal(value: str | None) -> int | None:
    if value is None:
        return None

    stripped = value.strip()
    if re.fullmatch(r"-?\d+", stripped):
        return int(stripped)
    return None


def _parse_component_path(value: str | None) -> str | None:
    if value is None:
        return None

    match = re.search(r"import\((['\"])(?P<path>.+?)\1\)", value)
    if not match:
        return None

    component_path = match.group("path")
    if component_path.startswith("@/"):
        return component_path[2:]
    return component_path


def _derive_menu_name(route_name: str | None, route_path: str, permission: str | None) -> str:
    if permission:
        if literal_permission := _parse_string_literal(permission):
            parts = literal_permission.split(":")
            if len(parts) >= 3:
                return f"{parts[0]}:{parts[1]}:menu"

        if permission_name := _derive_menu_name_from_permission_expression(permission):
            return permission_name

    category, resource = _derive_category_and_resource(route_name, route_path)
    return f"{category}:{resource}:menu"


def _derive_menu_name_from_permission_expression(permission: str) -> str | None:
    normalized = permission.replace(" ", "")
    match = re.fullmatch(r"(?P<root>[A-Z0-9_]+)\.(?P<resource>[A-Za-z0-9_.]+)\.(?P<action>[A-Za-z0-9_]+)", normalized)
    if not match:
        return None

    root = match.group("root")
    if root.endswith("_PERMISSIONS"):
        category = root.removesuffix("_PERMISSIONS").lower().replace("_", "-")
    elif root == "PERMISSIONS":
        chain = match.group("resource").split(".")
        if len(chain) < 2:
            return None
        category = _normalize_category(chain[0])
        resource = _normalize_resource(chain[-1])
        return f"{category}:{resource}:menu"
    else:
        return None

    resource_chain = match.group("resource").split(".")
    if not resource_chain:
        return None

    resource = _normalize_resource(resource_chain[-1])
    return f"{category}:{resource}:menu"


def _derive_category_and_resource(route_name: str | None, route_path: str) -> tuple[str, str]:
    path_segments = [segment for segment in route_path.split("/") if segment]

    category = _normalize_category(path_segments[0]) if len(path_segments) >= 2 else "system"

    resource = _derive_resource_from_route_name(route_name)
    if not resource:
        fallback_segment = path_segments[-1] if path_segments else "index"
        resource = _normalize_resource(fallback_segment)

    return category, resource


def _derive_resource_from_route_name(route_name: str | None) -> str | None:
    if not route_name:
        return None

    words = [_normalize_resource(word) for word in _split_identifier_words(route_name)]
    filtered = [word for word in words if word and word not in _MENU_NAME_SUFFIXES]
    if not filtered:
        return None

    return _singularize(filtered[-1])


def _normalize_category(value: str) -> str:
    return re.sub(r"-{2,}", "-", value.replace("_", "-").lower()).strip("-")


def _normalize_resource(value: str) -> str:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    snake = re.sub(r"[^a-zA-Z0-9]+", "_", snake)
    snake = re.sub(r"_+", "_", snake).strip("_").lower()
    return _singularize(snake)


def _singularize(value: str) -> str:
    if value.endswith("ies") and len(value) > 3:
        return f"{value[:-3]}y"
    if value.endswith("ses") and len(value) > 3:
        return value[:-2]
    if value.endswith("s") and len(value) > 3 and not value.endswith(("ss", "us")):
        return value[:-1]
    return value


def _split_identifier_words(value: str) -> list[str]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return [part for part in re.split(r"[^a-zA-Z0-9]+|\s+", normalized) if part]


def _join_paths(parent_path: str, route_path: str | None) -> str:
    if not route_path:
        return parent_path or "/"

    if route_path.startswith("/"):
        return route_path

    if not parent_path or parent_path == "/":
        return f"/{route_path.lstrip('/')}"

    return f"{parent_path.rstrip('/')}/{route_path.lstrip('/')}"


def _validate_menu_definitions(definitions: list[FrontendMenuDefinition]) -> None:
    name_to_path: dict[str, str] = {}

    for definition in definitions:
        if not definition.name.strip():
            raise ValueError(f"菜单 `{definition.title}` 缺少 name")
        if not definition.title.strip():
            raise ValueError(f"菜单 `{definition.name}` 缺少 title")
        if not definition.path.strip():
            raise ValueError(f"菜单 `{definition.name}` 缺少 path")

        existing_path = name_to_path.get(definition.name)
        if existing_path and existing_path != definition.path:
            raise ValueError(
                f"检测到重复菜单 name: `{definition.name}` 同时指向 `{existing_path}` 与 `{definition.path}`"
            )
        name_to_path[definition.name] = definition.path


def _split_top_level(source: str) -> list[str]:
    items: list[str] = []
    start = 0
    round_depth = 0
    square_depth = 0
    curly_depth = 0
    quote: str | None = None
    line_comment = False
    block_comment = False
    escape = False
    index = 0

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue

        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue

        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            index += 1
            continue

        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue

        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue

        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth -= 1
        elif char == "{":
            curly_depth += 1
        elif char == "}":
            curly_depth -= 1
        elif char == "," and round_depth == 0 and square_depth == 0 and curly_depth == 0:
            items.append(source[start:index])
            start = index + 1

        index += 1

    tail = source[start:]
    if tail.strip():
        items.append(tail)

    return items


def _find_top_level_character(source: str, target: str) -> int:
    round_depth = 0
    square_depth = 0
    curly_depth = 0
    quote: str | None = None
    line_comment = False
    block_comment = False
    escape = False
    index = 0

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue

        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue

        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            index += 1
            continue

        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue

        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue

        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]":
            square_depth -= 1
        elif char == "{":
            curly_depth += 1
        elif char == "}":
            curly_depth -= 1
        elif char == target and round_depth == 0 and square_depth == 0 and curly_depth == 0:
            return index

        index += 1

    return -1


def _try_extract_balanced(source: str, start: int, open_char: str, close_char: str) -> tuple[str, int] | None:
    depth = 0
    quote: str | None = None
    line_comment = False
    block_comment = False
    escape = False
    index = start

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue

        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue

        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = None
            index += 1
            continue

        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue

        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue

        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue

        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return source[start : index + 1], index + 1

        index += 1

    return None


def _extract_balanced(source: str, start: int, open_char: str, close_char: str) -> tuple[str, int]:
    balanced = _try_extract_balanced(source, start, open_char, close_char)
    if balanced is not None:
        return balanced

    raise ValueError(f"未找到匹配的 `{close_char}`")


def _strip_outer(source: str, open_char: str, close_char: str) -> str:
    stripped = source.strip()
    if stripped.startswith(open_char) and stripped.endswith(close_char):
        return stripped[1:-1]
    return stripped


def _strip_leading_comments(source: str) -> str:
    stripped = source.lstrip()

    while stripped.startswith(("//", "/*")):
        if stripped.startswith("//"):
            newline_index = stripped.find("\n")
            if newline_index == -1:
                return ""
            stripped = stripped[newline_index + 1 :].lstrip()
            continue

        end_index = stripped.find("*/")
        if end_index == -1:
            return ""
        stripped = stripped[end_index + 2 :].lstrip()

    return stripped


__all__ = [
    "DEFAULT_FRONTEND_ROOT",
    "FrontendMenuDefinition",
    "load_frontend_router_menus",
    "parse_frontend_router_menus",
    "resolve_frontend_root",
]
