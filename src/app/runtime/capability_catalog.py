"""WorkLine capability catalog replacing the legacy plugin registry.

Phase5 technical lane 只保留目标态业务合同查询；不再动态加载 plugin class，
也不保存旧 plugin runtime instance。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from src.app.runtime.capabilities.phase4.contracts.material_identity import (
    MaterialIdentity,
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    material_identity_input_to_hash,
)
from src.app.runtime.capabilities.phase4.contracts.ng_reason import NgReasonDefinition, NgReasonSource
from src.app.runtime.capabilities.phase4.contracts.rough_sorter import (
    ROUGH_SORTER_CONTRACT_VERSION,
    ROUGH_SORTER_PLUGIN_KEY,
    classify_rough_sorter_result,
    normalize_six_in_one_payload,
    resolve_rough_sorter_business_key,
)
from src.app.runtime.capabilities.phase4.contracts.rough_sorter_context import RoughSorterContext
from src.app.runtime.capabilities.phase4.contracts.smt_sorting_inbound import (
    NG_REASON_LOCAL_SORTING_NG,
    SMT_SORTING_INBOUND_CONTRACT_VERSION,
    SMT_SORTING_INBOUND_PLUGIN_KEY,
)
from src.app.runtime.capabilities.phase4.contracts.sorting_inbound_context import SortingInboundContext
from src.app.workline.domain.plugin_manifest import WorklinePluginManifest
from src.app.workline.utils import payload_dict

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from src.app.runtime.orchestration.topology_bridge import WorklineTopologyView

_MANIFEST_DIR = Path(__file__).resolve().parents[1] / "workline" / "domain" / "contracts" / "manifests"


@dataclass(frozen=True, slots=True)
class WorklineCapabilityDefinition:
    """目标态 WorkLine capability 定义。"""

    capability_key: str
    contract_version: str
    manifest: WorklinePluginManifest
    context_model: type[Any] | None = None
    business_key_resolver: Callable[[dict[str, Any]], str | None] | None = None
    result_classifier: Callable[[dict[str, Any]], str | None] | None = None
    material_identity_resolver: Callable[[MaterialIdentityInput], MaterialIdentity] | None = None
    ng_reason_resolver: Callable[[], tuple[NgReasonDefinition, ...]] | None = None


def _non_empty_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _payload_data(payload_json: dict[str, Any]) -> dict[str, Any]:
    return payload_dict(payload_json.get("data"))


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return dict(cast("dict[str, Any]", value)) if isinstance(value, dict) else {}


def _missing_material_identity(input_value: MaterialIdentityInput) -> MaterialIdentity:
    return MaterialIdentity(
        resolution_status=MaterialIdentityResolutionStatus.MISSING,
        raw_evidence_hash=material_identity_input_to_hash(input_value),
    )


def _ng_reason(plugin_key: str, contract_version: str, canonical_code: str, label: str) -> NgReasonDefinition:
    return NgReasonDefinition(
        canonical_code=canonical_code,
        label=label,
        source=NgReasonSource.PLUGIN,
        plugin_key=plugin_key,
        contract_version=contract_version,
        maps_from=(canonical_code,),
    )


def _rough_sorter_ng_reasons() -> tuple[NgReasonDefinition, ...]:
    return (
        _ng_reason(ROUGH_SORTER_PLUGIN_KEY, ROUGH_SORTER_CONTRACT_VERSION, "BARCODE_INVALID", "条码无效"),
        _ng_reason(ROUGH_SORTER_PLUGIN_KEY, ROUGH_SORTER_CONTRACT_VERSION, "BARCODE_INCOMPLETE", "条码不完整"),
        _ng_reason(ROUGH_SORTER_PLUGIN_KEY, ROUGH_SORTER_CONTRACT_VERSION, "BARCODE_RULE_NG", "条码规则判定 NG"),
        _ng_reason(ROUGH_SORTER_PLUGIN_KEY, ROUGH_SORTER_CONTRACT_VERSION, "MEASUREMENT_NG", "测量业务判定 NG"),
        _ng_reason(ROUGH_SORTER_PLUGIN_KEY, ROUGH_SORTER_CONTRACT_VERSION, "WMS_REJECTED", "WMS 库存校验拒绝"),
    )


def _sorting_ng_reasons() -> tuple[NgReasonDefinition, ...]:
    return (
        _ng_reason(
            SMT_SORTING_INBOUND_PLUGIN_KEY,
            SMT_SORTING_INBOUND_CONTRACT_VERSION,
            NG_REASON_LOCAL_SORTING_NG,
            "本地分拣 NG",
        ),
    )


def resolve_sorting_inbound_business_key(payload_json: dict[str, Any]) -> str | None:
    """按现场扫码/命令 payload 派生分拣入库业务主键。"""

    data = _payload_data(payload_json)
    return (
        _non_empty_str(data.get("material_identity_key"))
        or _non_empty_str(data.get("PkgID"))
        or _non_empty_str(data.get("pkg_code"))
        or _non_empty_str(payload_json.get("business_key"))
    )


def classify_sorting_inbound_result(payload_json: dict[str, Any]) -> str | None:
    """返回 SMT 分拣入库业务分类；普通成功/失败交给通用分类器。"""

    data = _payload_data(payload_json)
    reason_code = _non_empty_str(data.get("reason_code")) or _non_empty_str(payload_json.get("reason_code"))
    if reason_code == NG_REASON_LOCAL_SORTING_NG:
        return "business_decision"
    return None


def _resolve_rough_sorter_material_identity(input_value: MaterialIdentityInput) -> MaterialIdentity:
    session_context = input_value.session_context or {}
    source_payload = dict(cast("dict[str, Any]", input_value.source_payload or {}))
    command_payload = input_value.command_payload or {}
    six_in_one = _dict_or_empty(session_context.get("six_in_one"))
    business_key = (
        resolve_rough_sorter_business_key(source_payload)
        or _non_empty_str(session_context.get("business_key"))
        or _non_empty_str(command_payload.get("business_key"))
        or _non_empty_str(six_in_one.get("PkgID"))
    )
    if business_key is None:
        return _missing_material_identity(input_value)
    return MaterialIdentity(
        resolution_status=MaterialIdentityResolutionStatus.RESOLVED,
        idempotency_key=business_key,
        business_key=business_key,
        display={key: value for key, value in six_in_one.items() if value is not None},
        raw_evidence_hash=material_identity_input_to_hash(input_value),
    )


def _resolve_sorting_inbound_material_identity(input_value: MaterialIdentityInput) -> MaterialIdentity:
    source_payload = dict(cast("dict[str, Any]", input_value.source_payload or {}))
    command_payload = dict(cast("dict[str, Any]", input_value.command_payload or {}))
    session_context = _dict_or_empty(input_value.session_context)
    sorting_context = _dict_or_empty(session_context.get("sorting"))
    current_material = _dict_or_empty(sorting_context.get("current_material"))
    business_key = (
        resolve_sorting_inbound_business_key(source_payload)
        or resolve_sorting_inbound_business_key(command_payload)
        or _non_empty_str(current_material.get("material_identity_key"))
        or _non_empty_str(current_material.get("pkg_code"))
    )
    if business_key is None:
        return _missing_material_identity(input_value)
    return MaterialIdentity(
        resolution_status=MaterialIdentityResolutionStatus.RESOLVED,
        idempotency_key=business_key,
        business_key=business_key,
        display={key: value for key, value in current_material.items() if value is not None},
        raw_evidence_hash=material_identity_input_to_hash(input_value),
    )


WORKLINE_CAPABILITY_CATALOG: dict[str, WorklineCapabilityDefinition] = {
    SMT_SORTING_INBOUND_PLUGIN_KEY: WorklineCapabilityDefinition(
        capability_key=SMT_SORTING_INBOUND_PLUGIN_KEY,
        contract_version=SMT_SORTING_INBOUND_CONTRACT_VERSION,
        manifest=WorklinePluginManifest.from_yaml_file(_MANIFEST_DIR / "smt_sorting_inbound.yaml"),
        context_model=SortingInboundContext,
        business_key_resolver=resolve_sorting_inbound_business_key,
        result_classifier=classify_sorting_inbound_result,
        material_identity_resolver=_resolve_sorting_inbound_material_identity,
        ng_reason_resolver=_sorting_ng_reasons,
    ),
    ROUGH_SORTER_PLUGIN_KEY: WorklineCapabilityDefinition(
        capability_key=ROUGH_SORTER_PLUGIN_KEY,
        contract_version=ROUGH_SORTER_CONTRACT_VERSION,
        manifest=WorklinePluginManifest.from_yaml_file(_MANIFEST_DIR / "rough_sorter.yaml"),
        context_model=RoughSorterContext,
        business_key_resolver=resolve_rough_sorter_business_key,
        result_classifier=classify_rough_sorter_result,
        material_identity_resolver=_resolve_rough_sorter_material_identity,
        ng_reason_resolver=_rough_sorter_ng_reasons,
    ),
}


def get_workline_capability_definition(capability_key: str | None) -> WorklineCapabilityDefinition | None:
    """按 capability key 获取 WorkLine capability 定义。"""

    if not capability_key:
        return None
    return WORKLINE_CAPABILITY_CATALOG.get(capability_key)


def list_workline_capability_definitions() -> list[WorklineCapabilityDefinition]:
    """按 capability key 稳定导出 capability 定义。"""

    return [WORKLINE_CAPABILITY_CATALOG[key] for key in sorted(WORKLINE_CAPABILITY_CATALOG)]


def parse_workline_six_in_one(capability_key: str | None, payload: dict[str, Any] | None) -> Any | None:
    """解析 capability 声明的 SixInOne 入站证据。"""

    if capability_key == ROUGH_SORTER_PLUGIN_KEY:
        return normalize_six_in_one_payload(payload)
    return None


def resolve_workline_business_key(capability_key: str | None, payload_json: dict[str, Any]) -> str | None:
    """通过 capability 静态合同解析业务主键。"""

    definition = get_workline_capability_definition(capability_key)
    if definition is None or definition.business_key_resolver is None:
        return None
    return definition.business_key_resolver(payload_json)


def classify_workline_result(capability_key: str | None, payload_json: dict[str, Any]) -> str | None:
    """通过 capability 静态合同解析命令结果分类。"""

    definition = get_workline_capability_definition(capability_key)
    if definition is None or definition.result_classifier is None:
        return None
    return definition.result_classifier(payload_json)


def get_workline_context_model(capability_key: str | None) -> type[Any] | None:
    """获取 capability 上下文模型。"""

    definition = get_workline_capability_definition(capability_key)
    return None if definition is None else definition.context_model


def resolve_workline_material_identity(
    capability_key: str | None,
    input_value: MaterialIdentityInput,
) -> MaterialIdentity:
    """通过 capability 静态合同解析物料身份，缺省时返回 MISSING。"""

    definition = get_workline_capability_definition(capability_key)
    if definition is None or definition.material_identity_resolver is None:
        return _missing_material_identity(input_value)
    return definition.material_identity_resolver(input_value)


def list_workline_ng_reasons(capability_key: str | None) -> tuple[NgReasonDefinition, ...]:
    """列出 capability NG 原因，缺省时返回空目录。"""

    definition = get_workline_capability_definition(capability_key)
    if definition is None or definition.ng_reason_resolver is None:
        return ()
    return definition.ng_reason_resolver()


def get_workline_contract_version(capability_key: str | None) -> str | None:
    """获取 capability 合同版本。"""

    definition = get_workline_capability_definition(capability_key)
    return None if definition is None else definition.contract_version


def validate_workline_capability_assignment(
    capability_key: str,
    workline: Any,
    devices: Sequence[Any],
) -> None:
    """校验工作线拓扑/设备要求。"""

    definition = get_workline_capability_definition(capability_key)
    if definition is None:
        from src.core.exceptions import BadRequestException

        raise BadRequestException(message=f"不支持的工作线 capability: {capability_key}")

    from src.app.runtime.orchestration.topology_bridge import WorklineTopologyView, validate_topology_manifest

    topology: WorklineTopologyView = WorklineTopologyView.from_devices(list(devices))
    try:
        validate_topology_manifest(definition.manifest, topology)
    except ValueError as exc:
        from src.core.exceptions import BadRequestException

        raise BadRequestException(message=str(exc)) from exc


__all__ = [
    "WORKLINE_CAPABILITY_CATALOG",
    "WorklineCapabilityDefinition",
    "classify_workline_result",
    "get_workline_capability_definition",
    "get_workline_context_model",
    "get_workline_contract_version",
    "list_workline_capability_definitions",
    "list_workline_ng_reasons",
    "parse_workline_six_in_one",
    "resolve_sorting_inbound_business_key",
    "resolve_workline_business_key",
    "resolve_workline_material_identity",
    "validate_workline_capability_assignment",
]
