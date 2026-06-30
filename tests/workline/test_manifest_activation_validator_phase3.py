"""Phase 3 WorkLine manifest activation validator tests."""

from __future__ import annotations


def test_manifest_activation_validator_rejects_unknown_queue_code() -> None:
    from src.app.workline.services.manifest_validator import WorkLineManifestActivationValidator

    validator = WorkLineManifestActivationValidator()
    result = validator.validate_queue_refs(
        declared_queue_codes={"Q-IN", "Q-OUT"},
        referenced_queue_codes={"Q-IN", "Q-OTU"},
    )

    assert result.valid is False
    assert result.blockers == ["UNKNOWN_QUEUE_CODE:Q-OTU"]
    assert result.runtime_hold_write_allowed is False


def test_manifest_activation_validator_allows_matching_queue_codes() -> None:
    from src.app.workline.services.manifest_validator import WorkLineManifestActivationValidator

    validator = WorkLineManifestActivationValidator()
    result = validator.validate_queue_refs(
        declared_queue_codes={"Q-IN", "Q-OUT"},
        referenced_queue_codes={"Q-IN", "Q-OUT"},
    )

    assert result.valid is True
    assert result.blockers == []
    assert result.runtime_hold_write_allowed is True


def test_manifest_activation_validator_rejects_missing_device_roles_and_capabilities() -> None:
    """WorkLine 激活前必须校验 required device role / capability。"""

    from src.app.workline.services.manifest_validator import WorkLineManifestActivationValidator

    validator = WorkLineManifestActivationValidator()
    result = validator.validate_activation_refs(
        declared_queue_codes={"Q-IN"},
        referenced_queue_codes={"Q-IN"},
        declared_device_roles={"SCANNER"},
        required_device_roles={"SCANNER", "SORTER_HOST"},
        declared_capabilities={"scan_bin"},
        required_capabilities={"scan_bin", "sort_bin"},
    )

    assert result.valid is False
    assert result.blockers == ["MISSING_CAPABILITY:sort_bin", "MISSING_DEVICE_ROLE:SORTER_HOST"]
    assert result.runtime_hold_write_allowed is False
