"""插件业务态 helper 测试。"""

from src.workline_runtime.plugin_state import (
    PLUGIN_STATE_KEY,
    get_plugin_state,
    project_plugin_state_for_trace,
    set_plugin_state,
)


def test_get_plugin_state_reads_only_plugin_state_key() -> None:
    """插件业务态只从 plugin_state 读取，不再回退 step_code。"""

    assert get_plugin_state({PLUGIN_STATE_KEY: "WAITING_PICK_PLACE"}) == "WAITING_PICK_PLACE"
    assert get_plugin_state({"step_code": "WAITING_PICK_PLACE"}, default="IDLE") == "IDLE"
    assert get_plugin_state(None, default="IDLE") == "IDLE"


def test_set_plugin_state_skips_empty_values() -> None:
    """空状态不应污染 context patch。"""

    context_patch: dict[str, object] = {"barcode": "PKG-001"}

    set_plugin_state(context_patch, "")
    set_plugin_state(context_patch, None)

    assert context_patch == {"barcode": "PKG-001"}


def test_project_plugin_state_for_trace_projects_only_plugin_state() -> None:
    """step_code 快照由 plugin_state 投影得到。"""

    assert project_plugin_state_for_trace({PLUGIN_STATE_KEY: "SCAN_01"}) == "SCAN_01"
    assert project_plugin_state_for_trace({"step_code": "SCAN_01"}) is None
