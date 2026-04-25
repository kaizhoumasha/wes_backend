"""插件业务态 helper。

`plugin_state` 是插件业务状态在 runtime context 中的唯一写入 key。
`WorklineSession.status` 继续表示平台生命周期状态；`step_code` 只作为查询/追踪投影保留。
"""

from typing import Any

PLUGIN_STATE_KEY = "plugin_state"


def _context_dict(value: Any) -> dict[str, Any]:
    """将未知 context 视作只读字典输入。"""

    return value if isinstance(value, dict) else {}


def get_plugin_state(context: Any, default: str | None = None) -> str | None:
    """从 context 读取插件业务状态。"""

    value = _context_dict(context).get(PLUGIN_STATE_KEY)
    return value if isinstance(value, str) and value else default


def set_plugin_state(context_patch: dict[str, Any], plugin_state: str | None) -> None:
    """将插件业务状态写入 context patch。"""

    if isinstance(plugin_state, str) and plugin_state:
        context_patch[PLUGIN_STATE_KEY] = plugin_state


def project_plugin_state_for_trace(context: Any) -> str | None:
    """将插件业务状态投影到 `step_code` 等查询/追踪字段。"""

    return get_plugin_state(context)


__all__ = [
    "PLUGIN_STATE_KEY",
    "get_plugin_state",
    "project_plugin_state_for_trace",
    "set_plugin_state",
]
