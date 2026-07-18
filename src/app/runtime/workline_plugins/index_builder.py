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
from src.app.runtime.orchestration.events_bridge import assert_not_reserved_runtime_event
from src.app.runtime.system_capabilities.definition import SystemCapabilityMode
from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition
from src.app.runtime.workline_plugins.schema import STATE_MACHINE_CONTRACT_PROFILES

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from src.app.runtime.workline_plugins.schema import WorklinePluginSchema

_ALLOWED_EVENT_CATEGORIES = frozenset({"ENTRY_DEVICE", "INTERNAL", "COMMAND_RESULT", "OPERATOR", "SAFETY"})
_ALLOWED_PIPELINE_QUEUE_ROLES = frozenset(
    {"BUFFER", "GATE", "WAIT", "WORKSTATION", "EXCEPTION", "ENTRY", "SCAN", "WORK"}
)
_ALLOWED_PIPELINE_ORDER_POLICIES = frozenset({"FIFO", "LIFO", "PRIORITY"})


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
        cls._validate_session_and_state_machines(schema)
        cls._validate_pipeline_queues(schema)
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
    def _validate_rack_positions(cls, schema: WorklinePluginSchema) -> dict[str, frozenset[str]]:
        rack_positions: dict[str, frozenset[str]] = {}
        for position in schema.rack_positions:
            code = cls._require_identity(position.code, field_name="rack position code")
            cls._require_identity(position.role, field_name="rack position role")
            cls._require_identity(position.station_code, field_name="rack position station_code")
            if code in rack_positions:
                raise ValueError(f"duplicate rack position: {code}")
            carrier = position.carrier_capability
            if carrier.min_capacity < 0:
                raise ValueError("rack position min_capacity must not be negative")
            if carrier.max_capacity < carrier.min_capacity:
                raise ValueError("rack position max_capacity must be greater than or equal to min_capacity")
            if not carrier.allowed_rack_kinds:
                raise ValueError("rack position allowed_rack_kinds must not be empty")
            allowed_rack_kinds = tuple(
                cls._require_identity(rack_kind, field_name="allowed rack kind")
                for rack_kind in carrier.allowed_rack_kinds
            )
            if len(set(allowed_rack_kinds)) != len(allowed_rack_kinds):
                raise ValueError("rack position allowed_rack_kinds must be unique")
            allowed_slot_kinds = tuple(
                cls._require_identity(slot_kind, field_name="allowed slot kind")
                for slot_kind in carrier.allowed_slot_kinds
            )
            if len(set(allowed_slot_kinds)) != len(allowed_slot_kinds):
                raise ValueError("rack position allowed_slot_kinds must be unique")
            rack_positions[code] = frozenset(allowed_rack_kinds)
        return rack_positions

    @classmethod
    def _validate_schema_references(
        cls,
        schema: WorklinePluginSchema,
        *,
        device_roles: set[str],
        rack_positions: dict[str, frozenset[str]],
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

        event_names: set[str] = set()
        for event in schema.events:
            event_name = cls._require_identity(event.event, field_name="event")
            if event_name in event_names:
                raise ValueError(f"duplicate event binding: {event_name}")
            event_names.add(event_name)
            category = cls._require_identity(event.category, field_name="event category")
            if category not in _ALLOWED_EVENT_CATEGORIES:
                raise ValueError(f"event category must be one of: {', '.join(sorted(_ALLOWED_EVENT_CATEGORIES))}")
            try:
                assert_not_reserved_runtime_event(
                    event_name,
                    owner="Workline Plugin Definition",
                    declaration_surface="schema.events",
                )
            except ValueError as exc:
                raise ValueError(f"reserved runtime event cannot be declared by plugin: {event_name}") from exc
            if not event.source_device_roles:
                raise ValueError(f"event source_device_roles must not be empty: {event_name}")
            if len(set(event.source_device_roles)) != len(event.source_device_roles):
                raise ValueError(f"event source_device_roles must be unique: {event_name}")
            for role in event.source_device_roles:
                if role not in device_roles:
                    raise ValueError(f"unknown event device role: {role}")
        command_names: set[str] = set()
        for command in schema.commands:
            command_name = cls._require_identity(command.command, field_name="command")
            if command_name in command_names:
                raise ValueError(f"duplicate command binding: {command_name}")
            command_names.add(command_name)
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
            if boundary.rack_kind not in rack_positions[boundary.rack_position_code]:
                raise ValueError(
                    "resource boundary rack_kind is not allowed by rack position: "
                    f"{boundary.rack_position_code}:{boundary.rack_kind}"
                )
            if boundary in boundaries:
                raise ValueError("duplicate resource boundary")
            boundaries.add(boundary)

    @classmethod
    def _validate_session_and_state_machines(cls, schema: WorklinePluginSchema) -> None:
        session_subject = schema.session_subject
        if session_subject is None:
            if schema.state_machines:
                raise ValueError("state machines require a session subject")
            return
        session_type = cls._require_identity(session_subject.type, field_name="session subject type")
        session_form = cls._require_identity(session_subject.physical_form, field_name="session subject physical_form")
        identity_sources = tuple(
            cls._require_identity(source, field_name="session subject identity source")
            for source in session_subject.identity_sources
        )
        if not identity_sources:
            raise ValueError("session subject identity_sources must not be empty")
        if len(set(identity_sources)) != len(identity_sources):
            raise ValueError("session subject identity_sources must be unique")

        machine_ids: set[str] = set()
        for machine in schema.state_machines:
            machine_id = cls._require_identity(machine.id, field_name="state machine id")
            if machine_id in machine_ids:
                raise ValueError(f"duplicate state machine id: {machine_id}")
            machine_ids.add(machine_id)
            subject = machine.subject
            subject_category = cls._require_identity(subject.category, field_name="state machine subject category")
            subject_type = cls._require_identity(subject.type, field_name="state machine subject type")
            subject_form = cls._require_identity(
                subject.physical_form, field_name="state machine subject physical_form"
            )
            if (subject_type, subject_form) != (session_type, session_form):
                raise ValueError(f"state machine subject must match session subject: {machine_id}")
            if subject_category != session_type:
                raise ValueError(f"state machine subject category must equal session subject type: {machine_id}")
            owner_model = cls._require_identity(machine.state_owner.model, field_name="state machine owner model")
            owner_field = cls._require_identity(machine.state_owner.field, field_name="state machine owner field")
            granularity = cls._require_identity(machine.granularity, field_name="state machine granularity")
            profiles = tuple(
                profile
                for profile in STATE_MACHINE_CONTRACT_PROFILES
                if (
                    profile.subject_type,
                    profile.owner_model,
                    profile.owner_field,
                    profile.granularity,
                )
                == (session_type, owner_model, owner_field, granularity)
            )
            if len(profiles) != 1:
                raise ValueError(
                    f"unsupported state machine contract: {session_type}/{owner_model}.{owner_field}/{granularity}"
                )
            profile = profiles[0]
            transitions = tuple(machine.transitions)
            if not transitions:
                raise ValueError(f"state machine transitions must declare an initial state: {machine_id}")
            declared_states = tuple(
                cls._require_identity(transition.from_state, field_name="state machine from_state")
                for transition in transitions
            )
            if len(set(declared_states)) != len(declared_states):
                raise ValueError(f"state machine states must be unique: {machine_id}")
            initial_state = declared_states[0]
            if initial_state not in profile.allowed_states:
                raise ValueError(
                    f"state machine initial state must be a valid {profile.status_contract}: {initial_state}"
                )
            invalid_declared_states = sorted(set(declared_states) - profile.allowed_states)
            if invalid_declared_states:
                raise ValueError(
                    f"state machine states must be valid {profile.status_contract}: "
                    f"{', '.join(invalid_declared_states)}"
                )
            declared_state_set = set(declared_states)
            for transition in transitions:
                to_states = tuple(
                    cls._require_identity(state, field_name="state machine to_state") for state in transition.to_states
                )
                if len(set(to_states)) != len(to_states):
                    raise ValueError(f"state machine transition targets must be unique: {machine_id}")
                invalid_targets = sorted(set(to_states) - profile.allowed_states)
                if invalid_targets:
                    raise ValueError(
                        f"state machine transition targets must be valid {profile.status_contract}: "
                        f"{', '.join(invalid_targets)}"
                    )
                unknown_states = sorted(set(to_states) - declared_state_set)
                if unknown_states:
                    raise ValueError(f"unknown state reference in {machine_id}: {', '.join(unknown_states)}")

    @classmethod
    def _validate_pipeline_queues(cls, schema: WorklinePluginSchema) -> None:
        queue_codes: set[str] = set()
        for queue in schema.pipeline_queues:
            code = cls._require_identity(queue.code, field_name="pipeline queue code")
            if code in queue_codes:
                raise ValueError(f"duplicate pipeline queue code: {code}")
            queue_codes.add(code)
            role = cls._require_identity(queue.role, field_name="pipeline queue role")
            if role not in _ALLOWED_PIPELINE_QUEUE_ROLES:
                raise ValueError(f"unsupported pipeline queue role: {role}")
            capacity = queue.capacity
            if isinstance(capacity, bool) or not ((isinstance(capacity, int) and capacity > 0) or capacity == "MANY"):
                raise ValueError("pipeline queue capacity must be a positive integer or MANY")
            policy = cls._require_identity(queue.order_policy, field_name="pipeline queue order_policy")
            if policy not in _ALLOWED_PIPELINE_ORDER_POLICIES:
                raise ValueError(f"unsupported pipeline queue order_policy: {policy}")

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
