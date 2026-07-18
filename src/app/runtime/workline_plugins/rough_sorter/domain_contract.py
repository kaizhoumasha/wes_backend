"""粗分机 Definition 使用的纯领域解析合同。"""

from __future__ import annotations

from typing import Any, cast

from src.app.runtime.capabilities.material_flow.contracts.material_identity import (
    MaterialIdentity,
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    material_identity_input_to_hash,
)
from src.app.runtime.capabilities.material_flow.contracts.ng_reason import NgReasonDefinition, NgReasonSource
from src.app.runtime.capabilities.material_flow.contracts.rough_sorter import (
    classify_rough_sorter_result,
    normalize_six_in_one_payload,
    resolve_rough_sorter_business_key,
)


def parse_six_in_one(payload: dict[str, Any] | None) -> Any:
    return normalize_six_in_one_payload(payload)


def resolve_material_identity(input_value: MaterialIdentityInput) -> MaterialIdentity:
    session_context = input_value.session_context or {}
    source_payload = dict(cast("dict[str, Any]", input_value.source_payload or {}))
    command_payload = input_value.command_payload or {}
    raw_six_in_one = session_context.get("six_in_one")
    six_in_one = dict(cast("dict[str, Any]", raw_six_in_one)) if isinstance(raw_six_in_one, dict) else {}
    business_key = (
        resolve_rough_sorter_business_key(source_payload)
        or _text(session_context.get("business_key"))
        or _text(command_payload.get("business_key"))
        or _text(six_in_one.get("PkgID"))
    )
    if business_key is None:
        return MaterialIdentity(
            resolution_status=MaterialIdentityResolutionStatus.MISSING,
            raw_evidence_hash=material_identity_input_to_hash(input_value),
        )
    return MaterialIdentity(
        resolution_status=MaterialIdentityResolutionStatus.RESOLVED,
        idempotency_key=business_key,
        business_key=business_key,
        display={key: value for key, value in six_in_one.items() if value is not None},
        raw_evidence_hash=material_identity_input_to_hash(input_value),
    )


def list_ng_reasons() -> tuple[NgReasonDefinition, ...]:
    return tuple(
        NgReasonDefinition(
            canonical_code=code,
            label=label,
            source=NgReasonSource.PLUGIN,
            plugin_key="rough_sorter",
            contract_version="rough_sorter.v2",
            maps_from=(code,),
        )
        for code, label in (
            ("BARCODE_INVALID", "条码无效"),
            ("BARCODE_INCOMPLETE", "条码不完整"),
            ("BARCODE_RULE_NG", "条码规则判定 NG"),
            ("MEASUREMENT_NG", "测量业务判定 NG"),
            ("WMS_REJECTED", "WMS 库存校验拒绝"),
        )
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = [
    "classify_rough_sorter_result",
    "list_ng_reasons",
    "parse_six_in_one",
    "resolve_material_identity",
    "resolve_rough_sorter_business_key",
]
