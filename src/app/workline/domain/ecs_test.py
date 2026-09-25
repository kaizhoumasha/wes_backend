"""ECS_TEST 运行模式的固定规则解析。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EcsTestRule:
    """一条来源事件到固定命令的静态映射。"""

    source_device_code: str
    target_device_code: str
    task_type: str
    params: dict[str, object]


def parse_ecs_test_rules(config: Mapping[str, object]) -> tuple[EcsTestRule, ...]:
    """校验 `ecs_test_rules` 结构；不做设备归属、在线状态或能力校验。"""

    raw_rules = config.get("ecs_test_rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("ecs_test_rules 缺失或为空")
    rules: list[EcsTestRule] = []
    seen_sources: set[str] = set()
    for raw in raw_rules:
        if not isinstance(raw, Mapping):
            raise ValueError("ecs_test_rules 每项必须为对象")  # noqa: TRY004 - 外部配置校验统一使用 ValueError
        source = raw.get("source_device_code")
        target = raw.get("target_device_code")
        task_type = raw.get("task_type")
        params = raw.get("params")
        if not isinstance(source, str) or not source:
            raise ValueError("ecs_test_rules.source_device_code 必须为非空字符串")
        if not isinstance(target, str) or not target:
            raise ValueError("ecs_test_rules.target_device_code 必须为非空字符串")
        if not isinstance(task_type, str) or not task_type:
            raise ValueError("ecs_test_rules.task_type 必须为非空字符串")
        if not isinstance(params, dict):
            raise ValueError("ecs_test_rules.params 必须为对象")  # noqa: TRY004 - 外部配置校验统一使用 ValueError
        if source in seen_sources:
            raise ValueError(f"ecs_test_rules 存在重复来源: {source}")
        seen_sources.add(source)
        rules.append(
            EcsTestRule(source_device_code=source, target_device_code=target, task_type=task_type, params=dict(params))
        )
    return tuple(rules)


__all__ = ["EcsTestRule", "parse_ecs_test_rules"]
