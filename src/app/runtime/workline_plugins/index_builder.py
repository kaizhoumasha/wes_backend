"""Workline Plugin 确定性静态索引构建器。"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.runtime.capabilities.material_flow.contracts.ng_reason import (
    NgReasonSource,
    build_ng_reason_catalog,
)
from src.app.runtime.extension_identity import sha256_digest, stable_sort
from src.app.runtime.system_capabilities.definition import SystemCapabilityMode
from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from src.app.runtime.workline_plugins.schema import WorklinePluginSchema


@dataclass(frozen=True, slots=True)
class WorklinePluginSource:
    """构建期发现的单个 Definition 及其稳定 import 来源。"""

    module_name: str
    directory_key: str
    definition: WorklinePluginDefinition
    export_name: str = "DEFINITION"


@dataclass(frozen=True, slots=True)
class GeneratedWorklinePluginIndex:
    """校验后可写入 generated_index.py 的确定性产物。"""

    identities: tuple[tuple[str, str], ...]
    digest: str
    source: str


class WorklinePluginIndexBuilder:
    """只在构建期发现并校验 Workline Plugin Definition。"""

    def __init__(
        self,
        *,
        capability_modes: Mapping[tuple[str, str], SystemCapabilityMode],
    ) -> None:
        self._capability_modes = dict(capability_modes)

    def discover(self, *, root: Path, package: str) -> tuple[WorklinePluginSource, ...]:
        """扫描作者态 definition.py；扫描和动态 import 仅允许发生在生成阶段。"""

        sources: list[WorklinePluginSource] = []
        for path in sorted(root.rglob("definition.py")):
            if path.parent == root:
                continue
            relative_parent = path.parent.relative_to(root)
            module_name = ".".join((package, *relative_parent.parts, "definition"))
            module = importlib.import_module(module_name)
            definition = getattr(module, "DEFINITION", None)
            if not isinstance(definition, WorklinePluginDefinition):
                raise TypeError(f"{module_name}.DEFINITION must be WorklinePluginDefinition")
            sources.append(
                WorklinePluginSource(
                    module_name=module_name,
                    directory_key=".".join(relative_parent.parts),
                    definition=definition,
                )
            )
        return tuple(sources)

    def build(self, sources: Iterable[WorklinePluginSource]) -> GeneratedWorklinePluginIndex:
        """校验声明引用和全局 route 唯一性并渲染静态索引。"""

        ordered = stable_sort(sources, key=lambda item: self._identity_key(item.definition))
        seen_identities: set[tuple[str, str]] = set()
        seen_routes: set[str] = set()
        for source in ordered:
            definition = source.definition
            identity = self._identity_key(definition)
            if identity in seen_identities:
                raise ValueError(f"duplicate identity: {identity[0]}@{identity[1]}")
            seen_identities.add(identity)
            if source.directory_key != definition.plugin_key:
                raise ValueError(
                    f"directory key {source.directory_key!r} does not match plugin key {definition.plugin_key!r}"
                )
            self._validate_definition_contract(definition)
            for route in definition.routes:
                if route in seen_routes:
                    raise ValueError(f"duplicate route: {route}")
                seen_routes.add(route)
            for capability in definition.allowed_capabilities:
                mode = self._capability_modes.get(capability)
                if mode is None:
                    raise ValueError(f"unknown capability reference: {capability[0]}@{capability[1]}")
                if mode not in (SystemCapabilityMode.QUERY, SystemCapabilityMode.EFFECT):
                    raise ValueError(f"unsupported capability mode: {mode}")

        identities = tuple(self._identity_key(item.definition) for item in ordered)
        digest = sha256_digest(tuple(item.definition.identity for item in ordered))
        return GeneratedWorklinePluginIndex(
            identities=identities,
            digest=digest,
            source=self._render(ordered, identities=identities, digest=digest),
        )

    @staticmethod
    def _require_identity(value: object, *, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} requires non-empty identity")
        return value

    @classmethod
    def _validate_definition_contract(cls, definition: WorklinePluginDefinition) -> None:
        if len(set(definition.routes)) != len(definition.routes):
            raise ValueError("duplicate route")
        if len(set(definition.allowed_capabilities)) != len(definition.allowed_capabilities):
            raise ValueError("duplicate capability")

        schema = definition.schema
        device_roles = cls._validate_device_requirements(schema)
        rack_positions = cls._validate_rack_positions(schema)
        cls._validate_schema_references(
            schema,
            device_roles=device_roles,
            rack_positions=rack_positions,
        )
        cls._validate_ng_reason_contract(definition)

    @classmethod
    def _validate_device_requirements(cls, schema: WorklinePluginSchema) -> set[str]:
        device_roles: set[str] = set()
        for device in schema.devices:
            role = cls._require_identity(device.role, field_name="device role")
            if role in device_roles:
                raise ValueError(f"duplicate device role: {role}")
            device_roles.add(role)
            if device.min_count < 0:
                raise ValueError("device min_count must not be negative")
            if device.max_count is not None and device.max_count < device.min_count:
                raise ValueError("device max_count must be greater than or equal to min_count")
        return device_roles

    @classmethod
    def _validate_rack_positions(cls, schema: WorklinePluginSchema) -> set[str]:
        rack_positions: set[str] = set()
        for position in schema.rack_positions:
            code = cls._require_identity(position.code, field_name="rack position code")
            cls._require_identity(position.role, field_name="rack position role")
            cls._require_identity(position.station_code, field_name="rack position station_code")
            if code in rack_positions:
                raise ValueError(f"duplicate rack position: {code}")
            rack_positions.add(code)
            carrier = position.carrier_capability
            if carrier.min_capacity < 0:
                raise ValueError("rack position min_capacity must not be negative")
            if carrier.max_capacity < carrier.min_capacity:
                raise ValueError("rack position max_capacity must be greater than or equal to min_capacity")
            for rack_kind in carrier.allowed_rack_kinds:
                cls._require_identity(rack_kind, field_name="allowed rack kind")
        return rack_positions

    @classmethod
    def _validate_schema_references(
        cls,
        schema: WorklinePluginSchema,
        *,
        device_roles: set[str],
        rack_positions: set[str],
    ) -> None:
        def validate_node(node: object) -> None:
            kind = cls._require_identity(getattr(node, "kind", None), field_name="topology node kind")
            ref = cls._require_identity(getattr(node, "ref", None), field_name="topology node ref")
            known_refs = device_roles if kind == "DEVICE_ROLE" else rack_positions if kind == "RACK_POSITION" else set()
            if ref not in known_refs:
                raise ValueError(f"unknown topology reference: {kind}:{ref}")

        for edge in schema.topology.flow_edges:
            validate_node(edge.from_node)
            validate_node(edge.to_node)
            cls._require_identity(edge.type, field_name="topology edge type")

        for event in schema.events:
            cls._require_identity(event.event, field_name="event")
            for role in event.source_device_roles:
                if role not in device_roles:
                    raise ValueError(f"unknown event device role: {role}")
        for command in schema.commands:
            cls._require_identity(command.command, field_name="command")
            if command.target_device_role not in device_roles:
                raise ValueError(f"unknown command device role: {command.target_device_role}")

        boundaries = set()
        for boundary in schema.resource_boundaries:
            for field_name in (
                "rack_position_code",
                "rack_kind",
                "business_demand_type",
                "wms_operation_type",
                "snapshot_kind",
                "lease_scope",
            ):
                cls._require_identity(getattr(boundary, field_name), field_name=f"resource boundary {field_name}")
            if boundary.rack_position_code not in rack_positions:
                raise ValueError(f"unknown resource boundary rack position: {boundary.rack_position_code}")
            if boundary in boundaries:
                raise ValueError("duplicate resource boundary")
            boundaries.add(boundary)

    @staticmethod
    def _validate_ng_reason_contract(definition: WorklinePluginDefinition) -> None:
        if definition.ng_reason_resolver is not None:
            reasons = tuple(definition.ng_reason_resolver())
            for reason in reasons:
                if reason.source == NgReasonSource.PLUGIN and (
                    reason.plugin_key,
                    reason.contract_version,
                ) != (definition.plugin_key, definition.contract_version):
                    raise ValueError("plugin NG reason identity does not match Definition")
            build_ng_reason_catalog(reasons)

    @staticmethod
    def _identity_key(definition: WorklinePluginDefinition) -> tuple[str, str]:
        return definition.plugin_key, definition.contract_version

    @staticmethod
    def _render(
        sources: tuple[WorklinePluginSource, ...],
        *,
        identities: tuple[tuple[str, str], ...],
        digest: str,
    ) -> str:
        imports = sorted(
            f"from {source.module_name} import {source.export_name} as _DEFINITION_{index}"
            for index, source in enumerate(sources)
        )
        identity_entries = [
            f"    ({json.dumps(key, ensure_ascii=False)}, {json.dumps(version, ensure_ascii=False)}),"
            for key, version in identities
        ]
        mapping_entries = [
            f"        ({json.dumps(key, ensure_ascii=False)}, {json.dumps(version, ensure_ascii=False)}): "
            f"_DEFINITION_{index},"
            for index, (key, version) in enumerate(identities)
        ]
        if not identity_entries:
            identity_block = ["WORKLINE_PLUGIN_IDENTITIES = ()"]
        elif len(identities) == 1:
            key, version = identities[0]
            identity_block = [
                "WORKLINE_PLUGIN_IDENTITIES = "
                f"(({json.dumps(key, ensure_ascii=False)}, {json.dumps(version, ensure_ascii=False)}),)"
            ]
        else:
            identity_block = ["WORKLINE_PLUGIN_IDENTITIES = (", *identity_entries, ")"]
        mapping_block = (
            ["WORKLINE_PLUGIN_INDEX = MappingProxyType({})"]
            if not mapping_entries
            else ["WORKLINE_PLUGIN_INDEX = MappingProxyType(", "    {", *mapping_entries, "    }", ")"]
        )
        lines = [
            '"""由 scripts/generate_runtime_extensions.py 生成；禁止手工编辑。"""',
            "",
            "from types import MappingProxyType",
            "",
            *imports,
            *(("",) if imports else ()),
            *identity_block,
            f"WORKLINE_PLUGIN_INDEX_DIGEST = {json.dumps(digest)}",
            *mapping_block,
            "",
            "__all__ = [",
            '    "WORKLINE_PLUGIN_IDENTITIES",',
            '    "WORKLINE_PLUGIN_INDEX",',
            '    "WORKLINE_PLUGIN_INDEX_DIGEST",',
            "]",
            "",
        ]
        return "\n".join(lines)


__all__ = [
    "GeneratedWorklinePluginIndex",
    "WorklinePluginIndexBuilder",
    "WorklinePluginSource",
]
