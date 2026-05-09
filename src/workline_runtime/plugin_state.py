"""插件业务态 helper。

`WorklineSession.plugin_state` 是插件业务进度的唯一当前事实源。
插件自己的 `context_json` 不允许再写入 `plugin_state`。
"""

from typing import Any

PLUGIN_STATE_KEY = "plugin_state"
RESERVED_RUNTIME_CONTEXT_KEYS = frozenset({PLUGIN_STATE_KEY})


def get_plugin_state(session: Any, default: str | None = None) -> str | None:
    """从 Session 读取插件业务状态。"""

    value = getattr(session, PLUGIN_STATE_KEY, None)
    return value if isinstance(value, str) and value else default


def context_patch_has_reserved_key(context_patch: dict[str, Any] | None) -> bool:
    """检测插件 context patch 是否尝试写 runtime-owned 字段。"""

    if not context_patch:
        return False
    return any(key in RESERVED_RUNTIME_CONTEXT_KEYS for key in context_patch)


def assert_context_patch_has_no_reserved_key(context_patch: dict[str, Any] | None) -> None:
    """插件 context patch 只能写业务数据，不能写 runtime-owned 字段。"""

    if context_patch_has_reserved_key(context_patch):
        raise ValueError("plugin_state is runtime-owned; use .transition(...) and the plugin state machine")


def project_issued_plugin_state(session: Any) -> str | None:
    """投影命令创建时的插件业务阶段快照。"""

    return get_plugin_state(session)


__all__ = [
    "PLUGIN_STATE_KEY",
    "RESERVED_RUNTIME_CONTEXT_KEYS",
    "assert_context_patch_has_no_reserved_key",
    "context_patch_has_reserved_key",
    "get_plugin_state",
    "project_issued_plugin_state",
]
