"""WMS 入站 callback 标准化。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from src.app.callback.contracts.external_callbacks import (
    WMS_ALLOWED_CALLBACK_TYPES,
    WMS_ORDINARY_EVENT_TYPES,
    validate_external_callback_type,
)
from src.app.runtime.system_capabilities.wms.contracts import (
    WmsEffectStatusHint,  # noqa: TC001 - Pydantic runtime field.
)
from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_TYPED_EFFECT_CALLBACK_TYPES

type JsonDict = dict[str, Any]
StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class WmsEffectStatusHintEnvelope(BaseModel):
    """WMS 异步 EFFECT 状态查询提示的完整公开包络。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_system: Literal["WMS"]
    callback_type: Literal["WMS_EFFECT_STATUS_HINT"]
    source_event_id: StableText = Field(max_length=160)
    occurred_at: AwareDatetime
    trace_id: StableText | None = Field(default=None, max_length=120)
    data: WmsEffectStatusHint


class WmsExecutionCallbackNormalizer:
    """只接受冻结 SPEC 的普通事件与 typed EFFECT status hint。"""

    def normalize(self, payload: JsonDict) -> JsonDict:
        callback_type = _require_first_str(payload, ("callback_type",), "callback_type")
        if callback_type == "WMS_EFFECT_STATUS_HINT":
            envelope = WmsEffectStatusHintEnvelope.model_validate(payload)
            canonical_payload = envelope.model_dump(mode="json")
            self.validate(canonical_payload, callback_type)
            return {
                "callback_type": callback_type,
                "trace_id": envelope.trace_id,
                "payload": canonical_payload,
            }
        self.validate(payload, callback_type)
        trace_id = _require_first_str(payload, ("trace_id",), "trace_id")
        return {
            "callback_type": callback_type,
            "trace_id": trace_id,
            "payload": payload,
        }

    def validate(self, payload: JsonDict, callback_type: str) -> None:
        """校验 WMS callback 的冻结允许集；非 WMS provider 沿用通用入口合同。"""

        if callback_type in WMS_TYPED_EFFECT_CALLBACK_TYPES:
            payload = WmsEffectStatusHintEnvelope.model_validate(payload).model_dump(mode="json")
        callback_type = validate_external_callback_type(payload, declared_callback_type=callback_type)
        if callback_type in WMS_TYPED_EFFECT_CALLBACK_TYPES:
            return


def _require_first_str(payload: JsonDict, aliases: tuple[str, ...], field_name: str) -> str:
    value = _resolve_first_str(payload, aliases)
    if value:
        return value
    raise ValueError(f"{field_name} is required")


def _resolve_first_str(payload: JsonDict, aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        value = payload.get(alias)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


wms_execution_callback_normalizer = WmsExecutionCallbackNormalizer()


__all__ = [
    "WMS_ALLOWED_CALLBACK_TYPES",
    "WMS_ORDINARY_EVENT_TYPES",
    "WmsEffectStatusHintEnvelope",
    "WmsExecutionCallbackNormalizer",
    "wms_execution_callback_normalizer",
]
