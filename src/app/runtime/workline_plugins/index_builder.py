"""Workline Plugin 确定性静态索引构建器。"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.runtime.extension_identity import sha256_digest, stable_sort
from src.app.runtime.system_capabilities.definition import SystemCapabilityMode
from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path


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
