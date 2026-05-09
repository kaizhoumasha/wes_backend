"""SMT 粗分机插件上下文模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SmtClassifierContext(BaseModel):
    """SMT 插件自己的业务上下文快照。"""

    barcode: str | None = None
    pkg_id: str | None = None
    barcodes: list[str] = Field(default_factory=list)
    location: str | None = None
    device_code: str | None = None
    reel_diameter: str | float | None = None
    reel_thickness: str | float | None = None
    bin_location: dict[str, Any] | None = None
    ng_reason: str | None = None
    pick_place_reason: str | None = None
    scan_ng_reason_code: str | None = None
    scan_ng_reason_message: str | None = None
    manual_hold_reason_code: str | None = None
    manual_hold_reason_message: str | None = None
    ng_handled: bool | None = None
    inspection_error: str | None = None
    manual_hold: bool | None = None

    model_config = ConfigDict(extra="allow")

    @classmethod
    def from_mapping(cls, value: Any) -> SmtClassifierContext:
        """从 session.context_json 等 dict 结构解析插件上下文。"""

        return cls.model_validate(dict(value) if isinstance(value, dict) else {})

    @classmethod
    def from_session(cls, session: Any) -> SmtClassifierContext:
        """从 PluginContext.session 中解析插件上下文。"""

        return cls.from_mapping(getattr(session, "context_json", None))

    def to_patch(self, *, exclude_none: bool = True) -> dict[str, Any]:
        """投影为可写回 session.context_json 的 patch。"""

        patch = self.model_dump(exclude_none=exclude_none)
        patch.pop("plugin_state", None)
        return patch


def parse_smt_context(ctx: Any) -> SmtClassifierContext:
    """从 runtime PluginContext 中解析 SMT 插件上下文。"""

    return SmtClassifierContext.from_session(getattr(ctx, "session", None))


__all__ = ["SmtClassifierContext", "parse_smt_context"]
