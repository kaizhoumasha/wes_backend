"""WMS staging conformance 的可信签发、canonical composition 与 revision 边界。"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.app.runtime.system_capabilities.wms import provider_conformance
from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILES

STAGING_PROFILE = WMS_PROVIDER_PROFILES["wms.2026-07-06.material-flow.staging"]
PRODUCTION_PROFILE = WMS_PROVIDER_PROFILES["wms.2026-07-06.material-flow.production"]
FIXTURE_DIGEST = "a" * 64
GENERATED_AT = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)


class _DeploymentSigner:
    """仅在测试内模拟部署受控签发器；私钥不会进入 attestation 或报告。"""

    __slots__ = ("_private_key", "key_id")

    def __init__(self, *, key_id: str) -> None:
        self.key_id = key_id
        self._private_key = Ed25519PrivateKey.generate()

    @property
    def public_key_bytes(self) -> bytes:
        return self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def sign(self, payload: bytes) -> bytes:
        return self._private_key.sign(payload)


class _ControlledExecutor:
    __slots__ = ("_calls", "_observations", "attestation", "composition_identity_digest")

    def __init__(self, *, attestation, observations, calls: list[str]) -> None:
        self.attestation = attestation
        self.composition_identity_digest = attestation.composition_identity_digest
        self._observations = observations
        self._calls = calls

    async def execute(self, case):
        self._calls.append(case.case_id)
        return self._observations[case.case_id]


def _matching_observations():
    return {
        case.case_id: provider_conformance.ConformanceObservation.model_validate(case.model_dump(mode="json"))
        for case in provider_conformance.QUERY_INVENTORY_CONFORMANCE_CASES
    }


def _trusted_execution():
    signer = _DeploymentSigner(key_id="staging-deployment-v1")
    verifier = provider_conformance.Ed25519StagingConformanceAttestationVerifier(
        trusted_public_keys={signer.key_id: signer.public_key_bytes}
    )
    attestation = provider_conformance.issue_staging_conformance_executor_attestation(
        profile=STAGING_PROFILE,
        endpoint_identity="wms-staging-query-primary",
        internal_revision="deploy-r42",
        composition_identity="wes-staging-query-composition-v1",
        signer=signer,
    )
    calls: list[str] = []
    executor = _ControlledExecutor(attestation=attestation, observations=_matching_observations(), calls=calls)
    return signer, verifier, attestation, executor, calls


@pytest.mark.asyncio
async def test_live_runner_verifies_deployment_signature_and_derives_report_revision() -> None:
    _, verifier, attestation, executor, calls = _trusted_execution()

    report = await provider_conformance.run_query_inventory_staging_live_conformance(
        profile=STAGING_PROFILE,
        executor=executor,
        attestation_verifier=verifier,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
    )

    assert calls == [case.case_id for case in provider_conformance.QUERY_INVENTORY_CONFORMANCE_CASES]
    assert report.endpoint_revision == provider_conformance.derive_staging_endpoint_revision(attestation)
    assert report.staging_attestation == attestation
    assert (
        provider_conformance.verify_wms_conformance_report(
            report.model_dump(mode="json"),
            staging_attestation_verifier=verifier,
        )
        == report
    )
    serialized = report.model_dump_json().lower()
    assert "wms-staging-query-primary" not in serialized
    assert "deploy-r42" not in serialized
    assert "wes-staging-query-composition-v1" not in serialized
    assert "private_key" not in serialized


@pytest.mark.asyncio
async def test_live_runner_rejects_self_signed_attestation_before_execute() -> None:
    _, trusted_verifier, _, _, _ = _trusted_execution()
    rogue_signer = _DeploymentSigner(key_id="rogue-deployment-v1")
    rogue_attestation = provider_conformance.issue_staging_conformance_executor_attestation(
        profile=STAGING_PROFILE,
        endpoint_identity="wms-staging-query-primary",
        internal_revision="deploy-r42",
        composition_identity="rogue-composition-v1",
        signer=rogue_signer,
    )
    calls: list[str] = []
    executor = _ControlledExecutor(
        attestation=rogue_attestation,
        observations=_matching_observations(),
        calls=calls,
    )

    with pytest.raises(ValueError, match=r"trusted signing key|signature"):
        await provider_conformance.run_query_inventory_staging_live_conformance(
            profile=STAGING_PROFILE,
            executor=executor,
            attestation_verifier=trusted_verifier,
            fixture_digest=FIXTURE_DIGEST,
            generated_at=GENERATED_AT,
        )
    assert calls == []


@pytest.mark.asyncio
async def test_live_runner_rejects_a_caller_defined_verifier_that_skips_public_key_verification() -> None:
    _, _, attestation, executor, calls = _trusted_execution()

    class _CallerDefinedVerifier:
        def verify(self, supplied_attestation):
            return supplied_attestation

    with pytest.raises(TypeError, match="Ed25519 public-key verifier"):
        await provider_conformance.run_query_inventory_staging_live_conformance(
            profile=STAGING_PROFILE,
            executor=executor,
            attestation_verifier=_CallerDefinedVerifier(),
            fixture_digest=FIXTURE_DIGEST,
            generated_at=GENERATED_AT,
        )
    assert executor.attestation == attestation
    assert calls == []


@pytest.mark.asyncio
async def test_live_runner_revalidates_complete_author_time_profile_before_attestation_or_execute() -> None:
    _, verifier, _, executor, calls = _trusted_execution()
    outer_identity_spoof = PRODUCTION_PROFILE.model_copy(update={"identity": STAGING_PROFILE.identity})
    incomplete_staging = STAGING_PROFILE.model_copy(update={"bindings": STAGING_PROFILE.bindings[:1]})

    for profile in (outer_identity_spoof, incomplete_staging):
        with pytest.raises(ValueError, match="canonical author-time staging profile"):
            await provider_conformance.run_query_inventory_staging_live_conformance(
                profile=profile,
                executor=executor,
                attestation_verifier=verifier,
                fixture_digest=FIXTURE_DIGEST,
                generated_at=GENERATED_AT,
            )
        assert calls == []


@pytest.mark.asyncio
async def test_live_runner_rejects_executor_not_matching_signed_composition() -> None:
    _, verifier, _, executor, calls = _trusted_execution()
    executor.composition_identity_digest = "f" * 64

    with pytest.raises(ValueError, match="controlled composition"):
        await provider_conformance.run_query_inventory_staging_live_conformance(
            profile=STAGING_PROFILE,
            executor=executor,
            attestation_verifier=verifier,
            fixture_digest=FIXTURE_DIGEST,
            generated_at=GENERATED_AT,
        )
    assert calls == []


def test_endpoint_revision_cannot_be_supplied_as_a_caller_owned_opaque_hex() -> None:
    report_parameters = inspect.signature(provider_conformance.build_wms_conformance_report).parameters
    live_parameters = inspect.signature(provider_conformance.run_query_inventory_staging_live_conformance).parameters

    assert "endpoint_revision" not in report_parameters
    assert "endpoint_revision" not in live_parameters
    kwargs = {
        "cases": provider_conformance.QUERY_INVENTORY_CONFORMANCE_CASES,
        "observations": tuple(_matching_observations().values()),
        "target": provider_conformance.ConformanceTarget.REPLAY,
        "profile": WMS_PROVIDER_PROFILES["wms.2026-07-06.material-flow.sandbox"],
        "fixture_digest": FIXTURE_DIGEST,
        "generated_at": GENERATED_AT,
        "endpoint_revision": "0" * 64,
    }
    with pytest.raises(TypeError, match="endpoint_revision"):
        provider_conformance.build_wms_conformance_report(**kwargs)


@pytest.mark.asyncio
async def test_live_runner_rejects_bare_callback_without_attested_composition() -> None:
    _, verifier, _, _, calls = _trusted_execution()

    async def bare_callback(case):
        calls.append(case.case_id)
        return _matching_observations()[case.case_id]

    with pytest.raises(TypeError, match=r"attested executor|controlled composition"):
        await provider_conformance.run_query_inventory_staging_live_conformance(
            profile=STAGING_PROFILE,
            executor=bare_callback,
            attestation_verifier=verifier,
            fixture_digest=FIXTURE_DIGEST,
            generated_at=GENERATED_AT,
        )
    assert calls == []
