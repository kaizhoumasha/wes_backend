"""EXTERNAL_HTTP secret provider 的脱敏审计合同。"""

from __future__ import annotations

import pytest

from src.app.sys.external_http_credentials import CredentialRevokedError


class _StaticProvider:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    def resolve(self, _credential_reference: str) -> bytes:
        if self.error is not None:
            raise self.error
        return b"never-persist-this-secret"


def test_audited_credential_provider_reports_only_closed_redacted_status() -> None:
    from src.app.sys.external_http_credentials import AuditedVersionedCredentialProvider

    events = []
    provider = AuditedVersionedCredentialProvider(_StaticProvider(), observer=events.append)

    secret = provider.resolve("secret://wms/material-flow-production-hmac@v1")

    assert secret == b"never-persist-this-secret"
    assert len(events) == 1
    assert events[0].status == "RESOLVED"
    diagnostic = repr(events)
    assert "secret://" not in diagnostic
    assert "never-persist-this-secret" not in diagnostic
    assert "credential_reference" not in diagnostic


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (CredentialRevokedError(), "REVOKED"),
        (LookupError("unsafe secret://do-not-log@v1"), "RESOLUTION_FAILED"),
        (RuntimeError("never-persist-this-secret"), "PROVIDER_ERROR"),
    ],
)
def test_audited_credential_provider_preserves_error_and_redacts_diagnostics(
    error: Exception,
    expected_status: str,
) -> None:
    from src.app.sys.external_http_credentials import AuditedVersionedCredentialProvider

    events = []
    provider = AuditedVersionedCredentialProvider(_StaticProvider(error=error), observer=events.append)

    with pytest.raises(type(error)):
        provider.resolve("secret://wms/material-flow-production-hmac@v1")

    assert events[0].status == expected_status
    diagnostic = repr(events)
    assert "secret://" not in diagnostic
    assert "never-persist-this-secret" not in diagnostic
