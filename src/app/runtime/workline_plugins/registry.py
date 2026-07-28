"""generated Workline Plugin Definition 的唯一通用读取边界。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.capabilities.material_flow.contracts.material_identity import (
    MaterialIdentity,
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    material_identity_input_to_hash,
)
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX

if TYPE_CHECKING:
    from collections.abc import Sequence

    from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition


def get_workline_plugin_definition(
    plugin_key: str | None,
    contract_version: str | None = None,
) -> WorklinePluginDefinition | None:
    if not plugin_key:
        return None
    if contract_version:
        return WORKLINE_PLUGIN_INDEX.get((plugin_key, contract_version))
    matches = [definition for (key, _), definition in WORKLINE_PLUGIN_INDEX.items() if key == plugin_key]
    return matches[0] if len(matches) == 1 else None


def list_workline_plugin_definitions() -> list[WorklinePluginDefinition]:
    return [WORKLINE_PLUGIN_INDEX[identity] for identity in sorted(WORKLINE_PLUGIN_INDEX)]


def get_workline_contract_version(plugin_key: str | None, contract_version: str | None = None) -> str | None:
    definition = get_workline_plugin_definition(plugin_key, contract_version)
    return None if definition is None else definition.contract_version


def resolve_workline_business_key(
    plugin_key: str | None,
    payload: dict[str, Any],
    *,
    contract_version: str | None = None,
) -> str | None:
    definition = get_workline_plugin_definition(plugin_key, contract_version)
    resolver = None if definition is None else definition.business_key_resolver
    return resolver(payload) if resolver is not None else None


def classify_workline_result(
    plugin_key: str | None,
    payload: dict[str, Any],
    *,
    contract_version: str | None = None,
) -> str | None:
    definition = get_workline_plugin_definition(plugin_key, contract_version)
    classifier = None if definition is None else definition.result_classifier
    return classifier(payload) if classifier is not None else None


def parse_workline_input_evidence(
    plugin_key: str | None,
    payload: dict[str, Any] | None,
    *,
    contract_version: str | None = None,
) -> Any | None:
    definition = get_workline_plugin_definition(plugin_key, contract_version)
    parser = None if definition is None else definition.input_evidence_parser
    return parser(payload) if parser is not None else None


def resolve_workline_material_identity(
    plugin_key: str | None,
    input_value: MaterialIdentityInput,
    *,
    contract_version: str | None = None,
) -> MaterialIdentity:
    definition = get_workline_plugin_definition(plugin_key, contract_version)
    resolver = None if definition is None else definition.material_identity_resolver
    if resolver is not None:
        return resolver(input_value)
    return MaterialIdentity(
        resolution_status=MaterialIdentityResolutionStatus.MISSING,
        raw_evidence_hash=material_identity_input_to_hash(input_value),
    )


def list_workline_ng_reasons(
    plugin_key: str | None,
    *,
    contract_version: str | None = None,
) -> tuple[Any, ...]:
    definition = get_workline_plugin_definition(plugin_key, contract_version)
    resolver = None if definition is None else definition.ng_reason_resolver
    if resolver is not None:
        return tuple(resolver())
    return ()


def validate_workline_plugin_assignment(plugin_key: str, workline: Any, devices: Sequence[Any]) -> None:
    definition = get_workline_plugin_definition(plugin_key, getattr(workline, "contract_version", None))
    if definition is None:
        from src.core.exceptions import BadRequestException

        raise BadRequestException(message=f"不支持的工作线 plugin: {plugin_key}")
    from src.app.runtime.orchestration.topology_bridge import WorklineTopologyView, validate_topology_schema

    try:
        validate_topology_schema(plugin_key, definition.schema, WorklineTopologyView.from_devices(list(devices)))
    except ValueError as exc:
        from src.core.exceptions import BadRequestException

        raise BadRequestException(message=str(exc)) from exc


# 迁移期调用名保留在 generated registry 内；不再代表独立 catalog 或身份真源。
get_workline_capability_definition = get_workline_plugin_definition
list_workline_capability_definitions = list_workline_plugin_definitions
parse_workline_six_in_one = parse_workline_input_evidence
validate_workline_capability_assignment = validate_workline_plugin_assignment


__all__ = [
    "classify_workline_result",
    "get_workline_capability_definition",
    "get_workline_contract_version",
    "get_workline_plugin_definition",
    "list_workline_capability_definitions",
    "list_workline_ng_reasons",
    "list_workline_plugin_definitions",
    "parse_workline_input_evidence",
    "parse_workline_six_in_one",
    "resolve_workline_business_key",
    "resolve_workline_material_identity",
    "validate_workline_capability_assignment",
    "validate_workline_plugin_assignment",
]
