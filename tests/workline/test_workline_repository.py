"""WorkLineRepository 纯函数辅助的边界。"""

from __future__ import annotations

from src.app.workline.repositories.workline_repository import _ecs_test_source_devices


def test_ecs_test_source_devices_skips_unparseable_config_without_raising():
    """一条活动线的坏配置不能让 Transport debug-run 创建收到 500；跳过它，继续处理其它线。"""

    configs = [
        {
            "ecs_test_rules": [
                {"source_device_code": "OK-1", "target_device_code": "T-1", "task_type": "MOVE_FORWARD", "params": {}}
            ]
        },
        {"ecs_test_rules": "not-a-list"},  # 理论上不会出现（START 已校验冻结），防御性跳过
        {},
    ]

    result = _ecs_test_source_devices(configs)

    assert result == frozenset({"OK-1"})


def test_ecs_test_source_devices_unions_multiple_active_lines():
    configs = [
        {"ecs_test_rules": [{"source_device_code": "A", "target_device_code": "T", "task_type": "X", "params": {}}]},
        {"ecs_test_rules": [{"source_device_code": "B", "target_device_code": "T", "task_type": "X", "params": {}}]},
    ]

    assert _ecs_test_source_devices(configs) == frozenset({"A", "B"})
