"""IntegrationLab fixture runner contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.app.contracts.external_contract_profile import ExternalContractProfile
from src.app.runtime.orchestration.scenario_replay import (
    ScenarioRecorder,
    ScenarioReplayResult,
    ScenarioReplayRunner,
)
from src.app.wms_integration.provider_simulator_registry import ProviderSimulatorRegistry


@dataclass(frozen=True, slots=True)
class IntegrationLabRunResult:
    """Validated IntegrationLab scenario execution result."""

    scenario_id: str
    provider_codes: tuple[str, ...]
    covered_cases: tuple[str, ...]
    required_event_kinds_present: bool
    missing_event_kinds: tuple[str, ...]
    replay_result: ScenarioReplayResult


class IntegrationLabScenarioRunner:
    """Validate WMS/ECS simulator fixtures and replay a deterministic scenario."""

    def __init__(
        self,
        *,
        repo_root: Path | None = None,
        recorder: ScenarioRecorder | None = None,
        replay_runner: ScenarioReplayRunner | None = None,
    ) -> None:
        self._repo_root = repo_root or Path.cwd()
        self._recorder = recorder or ScenarioRecorder()
        self._replay_runner = replay_runner or ScenarioReplayRunner()

    def run(self, fixture: Mapping[str, Any]) -> IntegrationLabRunResult:
        """Validate fixture coverage and replay simulator events."""

        scenario_id = _required_text(fixture, "scenario_id")
        _require_sandbox(_required_text(fixture, "environment"), "scenario")

        registries = self._load_provider_registries(fixture.get("provider_profiles"))
        scenario_cases = _scenario_case_refs(fixture.get("scenario_cases"))
        required_cases = _required_text_tuple(fixture, "required_cases")
        missing_cases = set(required_cases) - set(scenario_cases)
        if missing_cases:
            raise ValueError(f"IntegrationLab required_cases 缺失: {sorted(missing_cases)}")
        self._validate_provider_case_refs(scenario_cases, registries)

        simulator_events = _mapping_list(fixture.get("simulator_events"), field_name="simulator_events")
        required_event_kinds = set(_required_text_tuple(fixture, "required_event_kinds"))
        event_kinds = {str(event.get("kind")) for event in simulator_events if event.get("kind")}
        missing_event_kinds = tuple(sorted(required_event_kinds - event_kinds))
        if missing_event_kinds:
            raise ValueError(f"IntegrationLab required_event_kinds 缺失: {missing_event_kinds}")

        recording = self._recorder.record_simulator_events(
            scenario_id=scenario_id,
            simulator_events=simulator_events,
        )
        replay_result = self._replay_runner.replay(recording)
        return IntegrationLabRunResult(
            scenario_id=scenario_id,
            provider_codes=tuple(sorted(registries)),
            covered_cases=tuple(sorted(required_cases)),
            required_event_kinds_present=not missing_event_kinds,
            missing_event_kinds=missing_event_kinds,
            replay_result=replay_result,
        )

    def _load_provider_registries(self, raw_profiles: object) -> dict[str, ProviderSimulatorRegistry]:
        profiles = _mapping_list(raw_profiles, field_name="provider_profiles")
        registries: dict[str, ProviderSimulatorRegistry] = {}
        for raw_profile in profiles:
            profile = ExternalContractProfile.model_validate(raw_profile)
            _require_sandbox(profile.environment, f"profile {profile.provider_code}")
            registry = ProviderSimulatorRegistry(profile, repo_root=self._repo_root)
            registry.load()
            registries[profile.provider_code] = registry
        if not {"WMS", "ECS"}.issubset(registries):
            raise ValueError("IntegrationLab provider_profiles 必须同时包含 WMS 与 ECS sandbox profile")
        return registries

    def _validate_provider_case_refs(
        self,
        scenario_cases: Mapping[str, Mapping[str, str]],
        registries: Mapping[str, ProviderSimulatorRegistry],
    ) -> None:
        for case_id, provider_cases in scenario_cases.items():
            for provider_code, provider_case_id in provider_cases.items():
                registry = registries.get(provider_code)
                if registry is None:
                    raise ValueError(f"scenario_case={case_id} 引用了未知 provider: {provider_code}")
                if not registry.has_case(provider_case_id):
                    raise ValueError(f"scenario_case={case_id} 引用了缺失 fixture: {provider_code}.{provider_case_id}")


def _required_text(fixture: Mapping[str, Any], field_name: str) -> str:
    value = fixture.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"IntegrationLab fixture 缺少字段: {field_name}")
    return value


def _required_text_tuple(fixture: Mapping[str, Any], field_name: str) -> tuple[str, ...]:
    raw_values = fixture.get(field_name)
    if not isinstance(raw_values, list) or not raw_values:
        raise ValueError(f"IntegrationLab fixture 缺少列表字段: {field_name}")
    values: list[str] = []
    for raw_value in raw_values:
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(f"IntegrationLab fixture {field_name} 包含非法值: {raw_value!r}")
        values.append(raw_value)
    return tuple(values)


def _mapping_list(raw_values: object, *, field_name: str) -> list[Mapping[str, Any]]:
    if not isinstance(raw_values, list) or not raw_values:
        raise ValueError(f"IntegrationLab fixture 缺少列表字段: {field_name}")
    values: list[Mapping[str, Any]] = []
    for raw_value in raw_values:
        if not isinstance(raw_value, Mapping):
            raise TypeError(f"IntegrationLab fixture {field_name} 包含非对象值")
        values.append(raw_value)
    return values


def _scenario_case_refs(raw_cases: object) -> dict[str, Mapping[str, str]]:
    cases = _mapping_list(raw_cases, field_name="scenario_cases")
    scenario_cases: dict[str, Mapping[str, str]] = {}
    for raw_case in cases:
        case_id = raw_case.get("case_id")
        provider_cases = raw_case.get("provider_cases")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("IntegrationLab scenario_case 缺少 case_id")
        if not isinstance(provider_cases, Mapping) or not provider_cases:
            raise ValueError(f"IntegrationLab scenario_case={case_id} 缺少 provider_cases")
        normalized_provider_cases: dict[str, str] = {}
        for provider_code, provider_case_id in provider_cases.items():
            if not isinstance(provider_code, str) or not isinstance(provider_case_id, str):
                raise TypeError(f"IntegrationLab scenario_case={case_id} provider_cases 非法")
            normalized_provider_cases[provider_code] = provider_case_id
        scenario_cases[case_id] = normalized_provider_cases
    return scenario_cases


def _require_sandbox(environment: str, subject: str) -> None:
    if environment != "sandbox":
        raise ValueError(f"IntegrationLab {subject} 必须使用 sandbox environment")


__all__ = [
    "IntegrationLabRunResult",
    "IntegrationLabScenarioRunner",
]
