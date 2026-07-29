"""Generated plugin attempt 前置事实解析门面。"""

from __future__ import annotations

from importlib import import_module
from typing import Any


async def resolve_plugin_pre_attempt_facts(
    db: Any,
    *,
    session: Any,
    workline: Any,
    dispatch_request: Any,
    services: Any,
) -> bool:
    """按已生成的插件 identity 调用其可选前置事实解析器。"""

    plugin_key = getattr(dispatch_request, "plugin_key", None)
    contract_version = getattr(dispatch_request, "contract_version", None)
    if not isinstance(plugin_key, str) or not isinstance(contract_version, str):
        return False

    from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX

    if (plugin_key, contract_version) not in WORKLINE_PLUGIN_INDEX:
        return False

    module_name = f"src.app.runtime.workline_plugins.{plugin_key}.pre_attempt"
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return False
        raise
    resolver = getattr(module, "resolve_pre_attempt_facts", None)
    if not callable(resolver):
        return False
    return bool(
        await resolver(
            db,
            session=session,
            workline=workline,
            dispatch_request=dispatch_request,
            services=services,
        )
    )


__all__ = ["resolve_plugin_pre_attempt_facts"]
