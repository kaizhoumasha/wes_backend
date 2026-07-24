"""RuntimeInbox canonical payload bytes 配置合同。"""

from __future__ import annotations

import secrets

from cryptography.fernet import Fernet

from src.core.conf import Settings


def _settings_kwargs() -> dict[str, object]:
    return {
        "_env_file": None,
        "JWT_SECRET_KEY": secrets.token_urlsafe(32),
        "API_SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "DATABASE_RUNTIME_ROLE": "cli",
        "DATABASE_POOL_SIZE": 1,
        "WMS_EFFECT_STATUS_URL": "https://wms.example/status",
        "WMS_EFFECT_STATUS_TIMEOUT_SECONDS": 2,
        "WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES": 4096,
        "WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS": 9,
        "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS": 2,
        "WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS": 6,
        "WES_EFFECT_NOT_FOUND_GRACE_SECONDS": 3,
        "WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS": 3,
        "WES_EFFECT_STATUS_SCAN_BATCH_SIZE": 50,
        "WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS": 5,
        "WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS": 8,
        "WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS": 1,
        "WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS": 8,
        "WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1": "unit-test-status-secret",
    }


def test_runtime_inbox_payload_max_bytes_defaults_to_one_mib() -> None:
    configured = Settings(**_settings_kwargs())

    assert configured.runtime_inbox_payload_max_bytes == 1024 * 1024


def test_runtime_inbox_payload_max_bytes_accepts_explicit_configuration() -> None:
    configured = Settings(**{**_settings_kwargs(), "runtime_inbox_payload_max_bytes": 2048})

    assert configured.runtime_inbox_payload_max_bytes == 2048
