"""插件业务态 helper 测试。"""

from src.workline_runtime.plugin_state import (
    PLUGIN_STATE_KEY,
    assert_context_patch_has_no_reserved_key,
    get_plugin_state,
    project_issued_plugin_state,
)


def test_get_plugin_state_reads_only_plugin_state_key() -> None:
    """插件业务态只从 plugin_state 读取，不再回退 step_code。"""

    class Session:
        plugin_state = "WAITING_PICK_PLACE"

    assert get_plugin_state(Session()) == "WAITING_PICK_PLACE"
    assert get_plugin_state(None, default="IDLE") == "IDLE"


def test_context_patch_rejects_reserved_plugin_state() -> None:
    """插件 context patch 不允许写 runtime-owned plugin_state。"""

    context_patch: dict[str, object] = {"barcode": "PKG-001"}

    assert_context_patch_has_no_reserved_key(context_patch)

    try:
        assert_context_patch_has_no_reserved_key({PLUGIN_STATE_KEY: "WAITING_PICK_PLACE"})
    except ValueError as exc:
        assert "runtime-owned" in str(exc)
    else:
        raise AssertionError("reserved plugin_state should be rejected")


def test_project_issued_plugin_state_reads_session_plugin_state() -> None:
    """命令发行快照来自 Session 当前 plugin_state。"""

    class Session:
        plugin_state = "SCAN_01"

    assert project_issued_plugin_state(Session()) == "SCAN_01"
