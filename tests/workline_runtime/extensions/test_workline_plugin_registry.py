"""generated Workline Plugin registry 的 version-aware 查询合同。"""

from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from src.app.runtime.workline_plugins import registry
from src.app.runtime.workline_plugins.definition import WorklinePluginDefinition


class _Config(BaseModel):
    enabled: bool = True


class _State(BaseModel):
    count: int = 0


def _parse(payload: dict[str, object]) -> dict[str, object]:
    return payload


def _business_key_v2(payload: dict[str, object]) -> str:
    return f"v2:{payload['id']}"


def _business_key_v3(payload: dict[str, object]) -> str:
    return f"v3:{payload['id']}"


def _definition(version: str, *, resolver: object | None = None) -> WorklinePluginDefinition:
    return WorklinePluginDefinition(
        plugin_key="demo",
        contract_version=version,
        config_model=_Config,
        state_model=_State,
        routes=("scan",),
        allowed_capabilities=(),
        parsers={"scan": _parse},
        business_key_resolver=resolver,
    )


def test_registry_uses_exact_plugin_key_and_contract_version_identity(monkeypatch) -> None:
    v2 = _definition("v2")
    v3 = _definition("v3")
    monkeypatch.setattr(registry, "WORKLINE_PLUGIN_INDEX", {("demo", "v2"): v2, ("demo", "v3"): v3})

    assert registry.get_workline_plugin_definition("demo", "v2") is v2
    assert registry.get_workline_plugin_definition("demo", "v3") is v3
    assert registry.get_workline_plugin_definition("demo", "unknown") is None


def test_registry_without_version_fails_closed_when_plugin_key_is_ambiguous(monkeypatch) -> None:
    v2 = _definition("v2")
    v3 = _definition("v3")
    monkeypatch.setattr(registry, "WORKLINE_PLUGIN_INDEX", {("demo", "v2"): v2, ("demo", "v3"): v3})

    assert registry.get_workline_plugin_definition("demo") is None
    assert registry.get_workline_contract_version("demo") is None


def test_registry_pinned_helper_reads_requested_old_contract_version(monkeypatch) -> None:
    v2 = _definition("v2", resolver=_business_key_v2)
    v3 = _definition("v3", resolver=_business_key_v3)
    monkeypatch.setattr(registry, "WORKLINE_PLUGIN_INDEX", {("demo", "v2"): v2, ("demo", "v3"): v3})

    assert registry.resolve_workline_business_key("demo", {"id": "M1"}, contract_version="v2") == "v2:M1"
    assert registry.resolve_workline_business_key("demo", {"id": "M1"}, contract_version="v3") == "v3:M1"


def test_input_normalizer_forwards_pinned_contract_version(monkeypatch) -> None:
    from src.app.runtime.normalization.normalizers import input_normalizer

    seen: list[tuple[str | None, str | None]] = []

    def classify(
        plugin_key: str | None,
        payload: dict[str, object],
        *,
        contract_version: str | None = None,
    ) -> None:
        seen.append((plugin_key, contract_version))

    monkeypatch.setattr(input_normalizer, "classify_workline_result", classify)
    inbox = SimpleNamespace(
        kind="COMMAND_RESULT",
        payload_json={"command_code": "CMD-1", "result": "SUCCESS"},
        trace_id="trace-1",
    )

    input_normalizer.normalize_inbox_input(inbox, plugin_key="demo", contract_version="v2")

    assert seen == [("demo", "v2")]
