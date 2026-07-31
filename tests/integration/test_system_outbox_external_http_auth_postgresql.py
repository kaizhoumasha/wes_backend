"""共享 EXTERNAL_HTTP NONE/HMAC 数据库组合约束与迁移回放。"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from tests.support.runtime_inbox_postgresql import connect, run_alembic, temporary_database


async def _insert_external_http_binding(
    connection: asyncpg.Connection,
    *,
    auth_scheme: str,
    network_trust_mode: str,
    credential_reference: str | None,
) -> None:
    suffix = uuid4().hex
    await connection.execute(
        """
        INSERT INTO wes_biz.system_outbox (
            created_at,
            operation_domain,
            dispatch_type,
            dispatch_key,
            target_type,
            target_code,
            provider_profile_identity,
            provider_profile_hash,
            operation_identity,
            binding_revision,
            target_snapshot_json,
            target_snapshot_hash,
            auth_scheme,
            network_trust_mode,
            credential_reference,
            payload_json,
            canonical_payload_bytes,
            payload_hash,
            status,
            attempt_count
        ) VALUES (
            CURRENT_TIMESTAMP,
            'TEST',
            'EXTERNAL_HTTP',
            $1,
            'HTTP_ENDPOINT',
            'WMS_TEST',
            'wms.full-factory.production',
            $2,
            'tests.external-http.effect@v1',
            $3,
            $4::json,
            $5,
            $6,
            $7,
            $8,
            '{}'::json,
            '{}'::bytea,
            $9,
            'NEW',
            0
        )
        """,
        f"auth-binding:{suffix}",
        "a" * 64,
        "b" * 64,
        '{"code":"WMS_TEST","http_method":"POST","timeout_seconds":15,"url":"http://factory-wms/effect"}',
        "c" * 64,
        auth_scheme,
        network_trust_mode,
        credential_reference,
        "d" * 64,
    )


@pytest.mark.integration
def test_fresh_database_auth_constraint_accepts_only_closed_none_hmac_combinations() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            connection = await connect(database)
            try:
                await _insert_external_http_binding(
                    connection,
                    auth_scheme="NONE",
                    network_trust_mode="isolated_lan",
                    credential_reference=None,
                )
                await _insert_external_http_binding(
                    connection,
                    auth_scheme="HMAC_SHA256",
                    network_trust_mode="authenticated_network",
                    credential_reference="secret://wms/factory@v1",
                )
                for auth_scheme, network_trust_mode, credential_reference in (
                    ("NONE", "authenticated_network", None),
                    ("NONE", "isolated_lan", "secret://wms/factory@v1"),
                    ("HMAC_SHA256", "isolated_lan", None),
                    ("BEARER", "isolated_lan", None),
                ):
                    with pytest.raises(asyncpg.CheckViolationError):
                        await _insert_external_http_binding(
                            connection,
                            auth_scheme=auth_scheme,
                            network_trust_mode=network_trust_mode,
                            credential_reference=credential_reference,
                        )
            finally:
                await connection.close()

    asyncio.run(scenario())


@pytest.mark.integration
def test_auth_modes_revision_downgrades_and_reupgrades_without_data_compatibility_path() -> None:
    async def scenario() -> None:
        async with temporary_database() as (database, database_url):
            run_alembic("upgrade", "head", database_url=database_url)
            run_alembic("downgrade", "-1", database_url=database_url)
            connection = await connect(database)
            try:
                assert not await connection.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'wes_biz'
                          AND table_name = 'system_outbox'
                          AND column_name = 'network_trust_mode'
                    )
                    """
                )
            finally:
                await connection.close()

            run_alembic("upgrade", "head", database_url=database_url)
            connection = await connect(database)
            try:
                assert await connection.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = 'wes_biz'
                          AND table_name = 'system_outbox'
                          AND column_name = 'network_trust_mode'
                    )
                    """
                )
            finally:
                await connection.close()

    asyncio.run(scenario())
