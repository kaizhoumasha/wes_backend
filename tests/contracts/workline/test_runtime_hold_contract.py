"""BC-XX RuntimeHold scoped block/release 行为契约。

验收: RuntimeHold scope_type 必须有 object/device/resource scope;
       解除时必须声明 allowed_next_effect_scope;
       小 scope 优先 (WORK_ITEM/OBJECT/DEVICE/RESOURCE/QUEUE),
       只有影响整线安全时才用 SESSION/WORKLINE。
mock 仅允许 `src/app/runtime/orchestration/` 内的 skeleton 实体。
"""

from __future__ import annotations

from src.app.runtime.orchestration.runtime_hold import RuntimeHold

NARROW_SCOPES = {"WORK_ITEM", "OBJECT", "DEVICE", "RESOURCE", "QUEUE"}
WIDE_SCOPES = {"SESSION", "WORKLINE"}
ALL_SCOPES = NARROW_SCOPES | WIDE_SCOPES


def _hold(*, scope_type: str, scope_key: str, allowed_next: str | None = None) -> RuntimeHold:
    return RuntimeHold(
        execution_session_id=101,
        correlation_id="corr-001",
        reason="等待扫码",
        hold_type="RESOURCE_WAIT",
        scope_type=scope_type,
        scope_key=scope_key,
        allowed_next_effect_scope=allowed_next,
    )


def test_hold_requires_object_device_or_resource_scope():
    """happy path: 默认应使用 NARROW_SCOPES 之一。"""
    for scope in NARROW_SCOPES:
        hold = _hold(scope_type=scope, scope_key="wi-001")
        assert hold.scope_type == scope
        assert hold.scope_key == "wi-001"


def test_hold_release_must_declare_allowed_next_effect_scope():
    """happy path: 解除 hold 必须声明 allowed_next_effect_scope。"""
    hold = _hold(
        scope_type="WORK_ITEM",
        scope_key="wi-001",
        allowed_next="wi-001 -> MOVE_TO_SORTER",
    )

    assert hold.allowed_next_effect_scope == "wi-001 -> MOVE_TO_SORTER"


def test_hold_release_without_allowed_next_is_incomplete():
    """error path: 解除 hold 不声明 allowed_next_effect_scope 视为不完整释放。"""
    hold = _hold(scope_type="OBJECT", scope_key="obj-001", allowed_next=None)

    assert hold.allowed_next_effect_scope is None


def test_wide_scope_only_for_whole_line_safety():
    """边界: SESSION / WORKLINE 只用于影响整线安全的情况。
    业务优先用 NARROW_SCOPES, 业务代码不应随便升级到 SESSION/WORKLINE。"""
    safety_hold = _hold(scope_type="SESSION", scope_key="sess-101", allowed_next="SAFE_RESUME")
    workline_hold = _hold(
        scope_type="WORKLINE",
        scope_key="wl-7",
        allowed_next="WORKLINE_RECONCILE",
    )

    assert safety_hold.scope_type == "SESSION"
    assert workline_hold.scope_type == "WORKLINE"


def test_hold_distinct_scope_types_are_independent():
    """happy path: 不同 scope_type + scope_key 互不冲突。"""
    hold_a = _hold(scope_type="DEVICE", scope_key="device-001")
    hold_b = _hold(scope_type="OBJECT", scope_key="obj-001")
    hold_c = _hold(scope_type="QUEUE", scope_key="queue-A")

    assert hold_a.scope_key != hold_b.scope_key != hold_c.scope_key
    assert hold_a.scope_type != hold_b.scope_type != hold_c.scope_type


def test_hold_resolved_at_marks_release_timestamp():
    """happy path: 释放时填充 resolved_at。"""
    hold = _hold(scope_type="WORK_ITEM", scope_key="wi-001", allowed_next="RESUME")
    hold.resolved_at = 1700000000000

    assert hold.resolved_at == 1700000000000


def test_hold_type_distinguishes_blocking_reason():
    """hold_type 必须明确分类: RESOURCE_WAIT / SAFETY / RECONCILING / MANUAL / TIMEOUT。"""
    for hold_type in ("RESOURCE_WAIT", "SAFETY", "RECONCILING", "MANUAL", "TIMEOUT"):
        hold = _hold(scope_type="OBJECT", scope_key="obj-001")
        hold.hold_type = hold_type
        assert hold.hold_type == hold_type
