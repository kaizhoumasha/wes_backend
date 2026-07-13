"""PostgreSQL application_name 生成合同。"""

from __future__ import annotations

import pytest

from src.database.db import build_database_application_name


def test_application_name_uses_environment_role_hostname_pid_and_run_id() -> None:
    name = build_database_application_name(
        prefix="test",
        role="integration",
        hostname="runner-01",
        pid=4321,
        run_id="case-a",
    )

    assert name == "test:integration:runner-01:4321:case-a"


def test_application_name_truncates_prefix_and_hostname_but_preserves_identity_suffix() -> None:
    name = build_database_application_name(
        prefix="environment-" * 10,
        role="celery",
        hostname="worker-host-" * 10,
        pid=98765,
        run_id="run-20260714",
    )

    assert len(name) <= 63
    assert ":celery:" in name
    assert name.endswith(":98765:run-20260714")
    assert name.isascii() and name.isprintable()


def test_application_name_sanitizes_non_printable_and_non_ascii_characters() -> None:
    name = build_database_application_name(
        prefix="生产\nprod",
        role="api",
        hostname="主机\tapi-host",
        pid=123,
    )

    assert name.isascii() and name.isprintable()
    assert "\n" not in name and "\t" not in name
    assert ":api:" in name
    assert name.endswith(":123")


def test_application_name_rejects_identity_suffix_that_cannot_fit_postgresql_limit() -> None:
    with pytest.raises(ValueError, match="63"):
        build_database_application_name(
            prefix="prod",
            role="integration",
            hostname="runner",
            pid=1,
            run_id="x" * 64,
        )
