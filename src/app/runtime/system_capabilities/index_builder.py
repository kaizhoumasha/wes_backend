"""System Capability 确定性静态索引构建器。"""

from __future__ import annotations

import importlib
import inspect
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, get_type_hints

from src.app.runtime.extension_identity import sha256_digest, stable_sort
from src.app.runtime.system_capabilities.definition import (
    EffectCompletionMode,
    SystemCapabilityDefinition,
    SystemCapabilityMode,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

_BUILTIN_ADMISSIONS = frozenset({"provider-contract", "runtime"})


@dataclass(frozen=True, slots=True)
class SystemCapabilitySource:
    """构建期发现的单个 Definition 及其稳定 import 来源。"""

    module_name: str
    directory_key: str
    definition: SystemCapabilityDefinition
    export_name: str = "DEFINITION"


@dataclass(frozen=True, slots=True)
class GeneratedSystemCapabilityIndex:
    """校验后可写入 generated_index.py 的确定性产物。"""

    identities: tuple[tuple[str, str], ...]
    digest: str
    source: str


class SystemCapabilityIndexBuilder:
    """只在构建期发现并校验 System Capability Definition。"""

    def __init__(
        self,
        *,
        known_ports: Iterable[type[object]] | None = None,
        known_admissions: Iterable[str] | None = None,
    ) -> None:
        # 未注入 catalog 时拒绝所有 Port；不能靠模块名或类名推断“已注册”。
        self._known_ports = frozenset(known_ports or ())
        self._known_admissions = _BUILTIN_ADMISSIONS if known_admissions is None else frozenset(known_admissions)

    def discover(self, *, root: Path, package: str) -> tuple[SystemCapabilitySource, ...]:
        """扫描作者态 definition.py；扫描和动态 import 仅允许发生在生成阶段。"""

        sources: list[SystemCapabilitySource] = []
        for path in sorted(root.rglob("definition.py")):
            if path.parent == root:
                continue
            relative_parent = path.parent.relative_to(root)
            module_name = ".".join((package, *relative_parent.parts, "definition"))
            module = importlib.import_module(module_name)
            definition = getattr(module, "DEFINITION", None)
            if not isinstance(definition, SystemCapabilityDefinition):
                raise TypeError(f"{module_name}.DEFINITION must be SystemCapabilityDefinition")
            sources.append(
                SystemCapabilitySource(
                    module_name=module_name,
                    directory_key=".".join(relative_parent.parts),
                    definition=definition,
                )
            )
        return tuple(sources)

    def build(self, sources: Iterable[SystemCapabilitySource]) -> GeneratedSystemCapabilityIndex:
        """校验声明并渲染不含扫描逻辑的静态索引。"""

        ordered = stable_sort(sources, key=lambda item: self._identity_key(item.definition))
        seen: set[tuple[str, str]] = set()
        for source in ordered:
            definition = source.definition
            identity = self._identity_key(definition)
            if identity in seen:
                raise ValueError(f"duplicate identity: {identity[0]}@{identity[1]}")
            seen.add(identity)
            if source.directory_key != definition.capability_key:
                raise ValueError(
                    f"directory key {source.directory_key!r} does not match capability key "
                    f"{definition.capability_key!r}"
                )
            self._validate_definition(definition)

        identities = tuple(self._identity_key(item.definition) for item in ordered)
        digest = sha256_digest(tuple(item.definition.identity for item in ordered))
        return GeneratedSystemCapabilityIndex(
            identities=identities,
            digest=digest,
            source=self._render(ordered, identities=identities, digest=digest),
        )

    def _validate_definition(self, definition: SystemCapabilityDefinition) -> None:
        if (
            definition.mode is SystemCapabilityMode.QUERY
            and definition.completion_mode is not EffectCompletionMode.LOCAL_TRANSACTIONAL
        ):
            raise ValueError("QUERY capabilities must use LOCAL_TRANSACTIONAL completion mode")
        unknown_ports = tuple(port for port in definition.required_ports if port not in self._known_ports)
        if unknown_ports:
            names = ", ".join(f"{port.__module__}.{port.__qualname__}" for port in unknown_ports)
            raise ValueError(f"unknown Port: {names}")
        if definition.admission not in self._known_admissions:
            raise ValueError(f"unknown admission/profile: {definition.admission}")

        self._validate_handler_factory_signature(definition)

    @staticmethod
    def _identity_key(definition: SystemCapabilityDefinition) -> tuple[str, str]:
        return definition.capability_key, definition.contract_version

    @staticmethod
    def _validate_handler_factory_signature(definition: SystemCapabilityDefinition) -> None:
        factory = definition.handler_factory
        parameters = tuple(inspect.signature(factory).parameters.values())
        if len(parameters) != len(definition.required_ports):
            raise TypeError("handler_factory signature must exactly match required_ports")

        annotation_target = factory.__init__ if inspect.isclass(factory) else factory
        try:
            resolved_annotations = get_type_hints(annotation_target)
        except (NameError, TypeError) as exc:
            raise TypeError("handler_factory signature annotations must be resolvable") from exc

        for parameter, expected_port in zip(parameters, definition.required_ports, strict=True):
            annotation = resolved_annotations.get(parameter.name, parameter.annotation)
            if (
                parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                or parameter.default is not inspect.Parameter.empty
                or annotation is inspect.Parameter.empty
                or annotation is not expected_port
            ):
                raise TypeError(
                    "handler_factory signature parameters must match required_ports "
                    "in count, order, annotation, and requiredness"
                )

    @staticmethod
    def _render(
        sources: tuple[SystemCapabilitySource, ...],
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
        identity_block = (
            ["SYSTEM_CAPABILITY_IDENTITIES = ()"]
            if not identity_entries
            else ["SYSTEM_CAPABILITY_IDENTITIES = (", *identity_entries, ")"]
        )
        mapping_block = (
            ["SYSTEM_CAPABILITY_INDEX = MappingProxyType({})"]
            if not mapping_entries
            else ["SYSTEM_CAPABILITY_INDEX = MappingProxyType(", "    {", *mapping_entries, "    }", ")"]
        )
        lines = [
            '"""由 scripts/generate_runtime_extensions.py 生成；禁止手工编辑。"""',
            "",
            "from types import MappingProxyType",
            "",
            *imports,
            *(("",) if imports else ()),
            *identity_block,
            f"SYSTEM_CAPABILITY_INDEX_DIGEST = {json.dumps(digest)}",
            *mapping_block,
            "",
            "__all__ = [",
            '    "SYSTEM_CAPABILITY_IDENTITIES",',
            '    "SYSTEM_CAPABILITY_INDEX",',
            '    "SYSTEM_CAPABILITY_INDEX_DIGEST",',
            "]",
            "",
        ]
        return "\n".join(lines)


__all__ = [
    "GeneratedSystemCapabilityIndex",
    "SystemCapabilityIndexBuilder",
    "SystemCapabilitySource",
]
