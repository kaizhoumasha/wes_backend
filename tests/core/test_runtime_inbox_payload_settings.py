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
    }


def test_runtime_inbox_payload_max_bytes_defaults_to_one_mib() -> None:
    configured = Settings(**_settings_kwargs())

    assert configured.runtime_inbox_payload_max_bytes == 1024 * 1024


def test_runtime_inbox_payload_max_bytes_accepts_explicit_configuration() -> None:
    configured = Settings(**{**_settings_kwargs(), "runtime_inbox_payload_max_bytes": 2048})

    assert configured.runtime_inbox_payload_max_bytes == 2048
