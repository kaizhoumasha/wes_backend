"""Runtime production P0 E2E evidence gate."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class RuntimeP0E2EValidation:
    """Validation result for a runtime production P0 E2E artifact."""

    valid: bool
    reason: str = "OK"
    missing_profile_fields: tuple[str, ...] = ()
    invalid_profile_fields: tuple[str, ...] = ()
    missing_source_fields: tuple[str, ...] = ()
    invalid_source_fields: tuple[str, ...] = ()
    failed_latency_fields: tuple[str, ...] = ()
    missing_event_groups: tuple[str, ...] = ()
    missing_effects: tuple[str, ...] = ()
    missing_exception_paths: tuple[str, ...] = ()
    invalid_exception_paths: tuple[str, ...] = ()


class RuntimeP0E2EGate:
    """Validate production evidence for the runtime P0 hot-path closure gate."""

    max_p95_seconds = 30.0

    _REQUIRED_EVENT_GROUPS: ClassVar[dict[str, frozenset[str]]] = {
        "manifest": frozenset({"workline_manifest"}),
        "session": frozenset({"execution_session"}),
        "inbox": frozenset({"runtime_inbox"}),
        "intent": frozenset({"runtime_intent", "runtime_outbox"}),
        "device": frozenset({"device_command"}),
        "wms": frozenset({"wms_fulfillment"}),
        "plane": frozenset({"plane_snapshot"}),
    }
    _REQUIRED_EFFECT_PREFIXES: ClassVar[dict[str, str]] = {
        "device-command": "device-command:",
        "wms-fulfillment": "wms-fulfillment:",
    }
    _REQUIRED_EXCEPTION_PATHS = ("callback_out_of_order", "ecs_timeout", "wms_reject")
    _FORBIDDEN_ENVIRONMENTS = frozenset({"sandbox", "local-lightweight", "ci-lightweight", "lightweight"})

    def validate_artifact(self, artifact: Mapping[str, object]) -> RuntimeP0E2EValidation:
        """Validate a structured production P0 E2E artifact."""

        profile_validation = self._validate_profile(artifact.get("profile"))
        if profile_validation is not None:
            return profile_validation

        source_validation = self._validate_source(artifact.get("source"))
        if source_validation is not None:
            return source_validation

        latency_validation = self._validate_latency(artifact.get("latency"))
        if latency_validation is not None:
            return latency_validation

        recording = artifact.get("recording")
        if not isinstance(recording, Mapping):
            return RuntimeP0E2EValidation(
                valid=False,
                reason="MISSING_E2E_RECORDING",
                missing_event_groups=tuple(sorted(self._REQUIRED_EVENT_GROUPS)),
            )

        events = _event_mappings(recording.get("events"))
        missing_event_groups = _missing_event_groups(events, self._REQUIRED_EVENT_GROUPS)
        if missing_event_groups:
            return RuntimeP0E2EValidation(
                valid=False,
                reason="MISSING_E2E_EVENT_GROUPS",
                missing_event_groups=missing_event_groups,
            )

        missing_effects = _missing_effects(events, self._REQUIRED_EFFECT_PREFIXES)
        if missing_effects:
            return RuntimeP0E2EValidation(
                valid=False,
                reason="MISSING_E2E_EFFECTS",
                missing_effects=missing_effects,
            )

        exception_validation = self._validate_exception_paths(artifact.get("exception_paths"))
        if exception_validation is not None:
            return exception_validation

        return RuntimeP0E2EValidation(valid=True)

    def _validate_profile(self, profile: object) -> RuntimeP0E2EValidation | None:
        required_fields = ("kind", "environment", "dependency_profile")
        if not isinstance(profile, Mapping):
            return RuntimeP0E2EValidation(
                valid=False,
                reason="MISSING_PROFILE_METADATA",
                missing_profile_fields=tuple(f"profile.{field}" for field in required_fields),
            )

        missing_fields = tuple(
            f"profile.{field}" for field in required_fields if not _non_empty_text(profile.get(field))
        )
        if missing_fields:
            return RuntimeP0E2EValidation(
                valid=False,
                reason="MISSING_PROFILE_METADATA",
                missing_profile_fields=missing_fields,
            )

        invalid_fields: list[str] = []
        if profile.get("kind") != "production-e2e":
            invalid_fields.append("profile.kind")
        environment = str(profile["environment"]).strip().lower()
        if environment in self._FORBIDDEN_ENVIRONMENTS:
            invalid_fields.append("profile.environment")
        dependency_profile = str(profile["dependency_profile"]).strip().lower()
        if "wms" not in dependency_profile or "ecs" not in dependency_profile:
            invalid_fields.append("profile.dependency_profile")
        if invalid_fields:
            return RuntimeP0E2EValidation(
                valid=False,
                reason="INVALID_PROFILE_METADATA",
                invalid_profile_fields=tuple(sorted(invalid_fields)),
            )
        return None

    def _validate_source(self, source: object) -> RuntimeP0E2EValidation | None:
        if not isinstance(source, Mapping):
            return RuntimeP0E2EValidation(
                valid=False,
                reason="MISSING_SOURCE_PROVENANCE",
                missing_source_fields=(
                    "source.environment",
                    "source.evidence",
                    "source.evidence_sha256",
                    "source.kind",
                ),
            )

        required_fields = ("source.environment", "source.evidence", "source.evidence_sha256", "source.kind")
        missing_fields = tuple(field for field in required_fields if not _non_empty_text(source.get(field[7:])))
        invalid_fields: list[str] = []
        evidence_sha256 = source.get("evidence_sha256")
        if _non_empty_text(evidence_sha256) and not _is_sha256_hex(evidence_sha256):
            invalid_fields.append("source.evidence_sha256")
        environment = source.get("environment")
        if isinstance(environment, str) and environment.strip().lower() in self._FORBIDDEN_ENVIRONMENTS:
            invalid_fields.append("source.environment")
        if source.get("kind") not in (None, "trace-query"):
            invalid_fields.append("source.kind")
        if missing_fields:
            return RuntimeP0E2EValidation(
                valid=False,
                reason="MISSING_SOURCE_PROVENANCE",
                missing_source_fields=missing_fields,
            )
        if invalid_fields:
            return RuntimeP0E2EValidation(
                valid=False,
                reason="INVALID_SOURCE_PROVENANCE",
                invalid_source_fields=tuple(sorted(invalid_fields)),
            )
        return None

    def _validate_latency(self, latency: object) -> RuntimeP0E2EValidation | None:
        if not isinstance(latency, Mapping):
            return RuntimeP0E2EValidation(
                valid=False,
                reason="E2E_LATENCY_EXCEEDED",
                failed_latency_fields=("latency.p95_seconds",),
            )
        p95_seconds = latency.get("p95_seconds")
        if not isinstance(p95_seconds, int | float) or isinstance(p95_seconds, bool):
            return RuntimeP0E2EValidation(
                valid=False,
                reason="E2E_LATENCY_EXCEEDED",
                failed_latency_fields=("latency.p95_seconds",),
            )
        if p95_seconds >= self.max_p95_seconds:
            return RuntimeP0E2EValidation(
                valid=False,
                reason="E2E_LATENCY_EXCEEDED",
                failed_latency_fields=("latency.p95_seconds",),
            )
        return None

    def _validate_exception_paths(self, exception_paths: object) -> RuntimeP0E2EValidation | None:
        if not isinstance(exception_paths, Mapping):
            return RuntimeP0E2EValidation(
                valid=False,
                reason="MISSING_EXCEPTION_PATHS",
                missing_exception_paths=self._REQUIRED_EXCEPTION_PATHS,
            )

        missing_paths: list[str] = []
        invalid_paths: list[str] = []
        for path_name in self._REQUIRED_EXCEPTION_PATHS:
            raw_path = exception_paths.get(path_name)
            if not isinstance(raw_path, Mapping):
                missing_paths.append(path_name)
                continue
            if raw_path.get("result") != "RECONCILING":
                invalid_paths.append(f"{path_name}.result")
            if not _non_empty_text(raw_path.get("evidence")):
                missing_paths.append(f"{path_name}.evidence")
            evidence_sha256 = raw_path.get("evidence_sha256")
            if not _non_empty_text(evidence_sha256):
                missing_paths.append(f"{path_name}.evidence_sha256")
            elif not _is_sha256_hex(evidence_sha256):
                invalid_paths.append(f"{path_name}.evidence_sha256")

        if missing_paths:
            return RuntimeP0E2EValidation(
                valid=False,
                reason="MISSING_EXCEPTION_PATHS",
                missing_exception_paths=tuple(sorted(missing_paths)),
            )
        if invalid_paths:
            return RuntimeP0E2EValidation(
                valid=False,
                reason="INVALID_EXCEPTION_PATHS",
                invalid_exception_paths=tuple(sorted(invalid_paths)),
            )
        return None


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256_hex(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _event_mappings(raw_events: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(raw_events, list):
        return ()
    return tuple(event for event in raw_events if isinstance(event, Mapping))


def _missing_event_groups(
    events: tuple[Mapping[str, object], ...],
    required_event_groups: Mapping[str, frozenset[str]],
) -> tuple[str, ...]:
    event_kinds = {event.get("kind") for event in events}
    return tuple(
        sorted(
            group_name
            for group_name, allowed_kinds in required_event_groups.items()
            if event_kinds.isdisjoint(allowed_kinds)
        )
    )


def _missing_effects(
    events: tuple[Mapping[str, object], ...],
    required_effect_prefixes: Mapping[str, str],
) -> tuple[str, ...]:
    effect_keys: list[str] = []
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            continue
        effect_key = payload.get("effect_key")
        if isinstance(effect_key, str):
            effect_keys.append(effect_key)
    return tuple(
        sorted(
            effect_name
            for effect_name, effect_prefix in required_effect_prefixes.items()
            if not any(effect_key.startswith(effect_prefix) for effect_key in effect_keys)
        )
    )


runtime_p0_e2e_gate = RuntimeP0E2EGate()


__all__ = [
    "RuntimeP0E2EGate",
    "RuntimeP0E2EValidation",
    "runtime_p0_e2e_gate",
]
