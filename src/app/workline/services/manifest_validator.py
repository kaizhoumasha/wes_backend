"""WorkLine manifest activation validators."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ManifestActivationValidationResult:
    """WorkLine manifest activation validation result."""

    valid: bool
    blockers: list[str]
    runtime_hold_write_allowed: bool
    warnings: list[str] = field(default_factory=list)


class WorkLineManifestActivationValidator:
    """启动前 manifest 引用完整性校验。"""

    def validate_queue_refs(
        self,
        *,
        declared_queue_codes: set[str],
        referenced_queue_codes: set[str],
    ) -> ManifestActivationValidationResult:
        unknown = sorted(referenced_queue_codes - declared_queue_codes)
        blockers = [f"UNKNOWN_QUEUE_CODE:{queue_code}" for queue_code in unknown]
        return ManifestActivationValidationResult(
            valid=not blockers,
            blockers=blockers,
            runtime_hold_write_allowed=not blockers,
        )

    def validate_activation_refs(
        self,
        *,
        declared_queue_codes: set[str],
        referenced_queue_codes: set[str],
        declared_device_roles: set[str],
        required_device_roles: set[str],
        declared_capabilities: set[str],
        required_capabilities: set[str],
    ) -> ManifestActivationValidationResult:
        """校验 WorkLine 激活前的 queue/device/capability 引用完整性。"""

        queue_result = self.validate_queue_refs(
            declared_queue_codes=declared_queue_codes,
            referenced_queue_codes=referenced_queue_codes,
        )
        missing_capabilities = sorted(required_capabilities - declared_capabilities)
        missing_device_roles = sorted(required_device_roles - declared_device_roles)
        blockers = [
            *queue_result.blockers,
            *[f"MISSING_CAPABILITY:{capability}" for capability in missing_capabilities],
            *[f"MISSING_DEVICE_ROLE:{role}" for role in missing_device_roles],
        ]
        return ManifestActivationValidationResult(
            valid=not blockers,
            blockers=blockers,
            runtime_hold_write_allowed=not blockers,
        )


workline_manifest_activation_validator = WorkLineManifestActivationValidator()


__all__ = [
    "ManifestActivationValidationResult",
    "WorkLineManifestActivationValidator",
    "workline_manifest_activation_validator",
]
