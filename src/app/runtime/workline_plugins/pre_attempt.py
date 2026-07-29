"""Generated plugin attempt 前置事实解析门面。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from typing import Any


class PreAttemptStatus(str, Enum):
    """插件前置事实解析对通用 bridge 的封闭三态。"""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    FACTS_CHANGED = "FACTS_CHANGED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class PreAttemptResolution:
    """前置解析结果；BLOCKED 必须携带稳定原因码。"""

    status: PreAttemptStatus
    reason_code: str | None = None

    def __post_init__(self) -> None:
        blocked = self.status is PreAttemptStatus.BLOCKED
        if blocked != bool(self.reason_code):
            raise ValueError("only BLOCKED pre-attempt resolution requires reason_code")

    @classmethod
    def not_applicable(cls) -> PreAttemptResolution:
        return cls(PreAttemptStatus.NOT_APPLICABLE)

    @classmethod
    def facts_changed(cls) -> PreAttemptResolution:
        return cls(PreAttemptStatus.FACTS_CHANGED)

    @classmethod
    def blocked(cls, reason_code: str) -> PreAttemptResolution:
        return cls(PreAttemptStatus.BLOCKED, reason_code=reason_code)


async def resolve_plugin_pre_attempt_facts(
    db: Any,
    *,
    session: Any,
    workline: Any,
    dispatch_request: Any,
    services: Any,
) -> PreAttemptResolution:
    """按已生成的插件 identity 调用其可选前置事实解析器。"""

    plugin_key = getattr(dispatch_request, "plugin_key", None)
    contract_version = getattr(dispatch_request, "contract_version", None)
    if not isinstance(plugin_key, str) or not isinstance(contract_version, str):
        return PreAttemptResolution.not_applicable()

    from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX

    if (plugin_key, contract_version) not in WORKLINE_PLUGIN_INDEX:
        return PreAttemptResolution.not_applicable()

    module_name = f"src.app.runtime.workline_plugins.{plugin_key}.pre_attempt"
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return PreAttemptResolution.not_applicable()
        raise
    resolver = getattr(module, "resolve_pre_attempt_facts", None)
    if not callable(resolver):
        return PreAttemptResolution.not_applicable()
    resolution = await resolver(
        db,
        session=session,
        workline=workline,
        dispatch_request=dispatch_request,
        services=services,
    )
    if not isinstance(resolution, PreAttemptResolution):
        return PreAttemptResolution.blocked("PLUGIN_PRE_ATTEMPT_RESULT_INVALID")
    return resolution


__all__ = [
    "PreAttemptResolution",
    "PreAttemptStatus",
    "resolve_plugin_pre_attempt_facts",
]
