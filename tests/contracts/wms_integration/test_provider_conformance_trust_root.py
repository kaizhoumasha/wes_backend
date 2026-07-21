"""WMS staging conformance 的部署 trust root、sealed composition 与 revision 边界。"""

from __future__ import annotations

import copy
import importlib
import inspect
import os
from base64 import urlsafe_b64encode
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.app.runtime.system_capabilities.wms import conformance_trust_root, provider_conformance
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


class _DeploymentExecutionDelegate:
    """由受控 composition factory 包装的真实执行 delegate。"""

    __slots__ = ("_calls", "_observations")

    def __init__(self, *, observations, calls: list[str]) -> None:
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


@pytest.fixture
def trusted_execution(monkeypatch):
    """在隔离 reload 前配置部署环境，不猴补丁生产 registry。"""

    signer = _DeploymentSigner(key_id="staging-deployment-v1")
    encoded_public_key = urlsafe_b64encode(signer.public_key_bytes).rstrip(b"=").decode("ascii")
    original_environment = os.environ.get(conformance_trust_root.WMS_STAGING_CONFORMANCE_TRUST_ROOTS_ENV)
    monkeypatch.setenv(
        conformance_trust_root.WMS_STAGING_CONFORMANCE_TRUST_ROOTS_ENV,
        f'{{"{signer.key_id}":"{encoded_public_key}"}}',
    )
    importlib.reload(conformance_trust_root)
    importlib.reload(provider_conformance)
    calls: list[str] = []
    delegate = _DeploymentExecutionDelegate(observations=_matching_observations(), calls=calls)
    executor = provider_conformance.compose_query_inventory_staging_conformance_executor(
        profile=STAGING_PROFILE,
        endpoint_identity="wms-staging-query-primary",
        internal_revision="deploy-r42",
        composition_identity="wes-staging-query-composition-v1",
        signer=signer,
        execution_delegate=delegate,
    )
    yield signer, executor, calls

    if original_environment is None:
        monkeypatch.delenv(conformance_trust_root.WMS_STAGING_CONFORMANCE_TRUST_ROOTS_ENV, raising=False)
    else:
        monkeypatch.setenv(conformance_trust_root.WMS_STAGING_CONFORMANCE_TRUST_ROOTS_ENV, original_environment)
    importlib.reload(conformance_trust_root)
    importlib.reload(provider_conformance)


@pytest.mark.asyncio
async def test_live_runner_uses_deployment_trust_root_and_sealed_executor_capability(trusted_execution) -> None:
    _, executor, calls = trusted_execution

    report = await provider_conformance.run_query_inventory_staging_live_conformance(
        profile=STAGING_PROFILE,
        executor=executor,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
    )

    assert calls == [case.case_id for case in provider_conformance.QUERY_INVENTORY_CONFORMANCE_CASES]
    assert report.staging_attestation is not None
    assert report.endpoint_revision == provider_conformance.derive_staging_endpoint_revision(report.staging_attestation)
    assert provider_conformance.verify_wms_conformance_report(report.model_dump(mode="json")) == report
    assert not hasattr(executor, "attestation")
    assert not hasattr(executor, "composition_identity_digest")
    serialized = report.model_dump_json().lower()
    assert "wms-staging-query-primary" not in serialized
    assert "deploy-r42" not in serialized
    assert "wes-staging-query-composition-v1" not in serialized
    assert "private_key" not in serialized


@pytest.mark.asyncio
async def test_import_time_trust_root_is_immune_to_module_attribute_rebinding(
    trusted_execution,
    monkeypatch,
) -> None:
    """compose/run/report verify 只使用 import 时冻结的部署 root。"""

    signer, _, calls = trusted_execution
    empty_registry = provider_conformance.StagingConformanceTrustRootRegistry.from_public_keys({})
    monkeypatch.setattr(provider_conformance, "_DEPLOYMENT_TRUST_ROOT_REGISTRY", empty_registry, raising=False)
    monkeypatch.setattr(provider_conformance, "WMS_STAGING_CONFORMANCE_TRUST_ROOTS", empty_registry)
    monkeypatch.setattr(conformance_trust_root, "WMS_STAGING_CONFORMANCE_TRUST_ROOTS", empty_registry)
    monkeypatch.setenv(conformance_trust_root.WMS_STAGING_CONFORMANCE_TRUST_ROOTS_ENV, "{}")

    delegate = _DeploymentExecutionDelegate(observations=_matching_observations(), calls=calls)
    rebound_executor = provider_conformance.compose_query_inventory_staging_conformance_executor(
        profile=STAGING_PROFILE,
        endpoint_identity="wms-staging-query-secondary",
        internal_revision="deploy-r43",
        composition_identity="wes-staging-query-composition-v2",
        signer=signer,
        execution_delegate=delegate,
    )
    report = await provider_conformance.run_query_inventory_staging_live_conformance(
        profile=STAGING_PROFILE,
        executor=rebound_executor,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
    )

    assert provider_conformance.verify_wms_conformance_report(report.model_dump(mode="json")) == report


def test_public_entrypoints_do_not_expose_raw_creator_or_resolver_through_closure_reflection() -> None:
    """只约束公开 API 形状；同进程任意代码执行不属于本证明的攻击模型。"""

    exposed: list[str] = []
    for entrypoint in (
        provider_conformance.compose_query_inventory_staging_conformance_executor,
        provider_conformance.run_query_inventory_staging_live_conformance,
        provider_conformance.verify_wms_conformance_report,
    ):
        function = getattr(entrypoint, "__func__", entrypoint)
        for cell in function.__closure__ or ():
            candidate = cell.cell_contents
            name = getattr(candidate, "__name__", type(candidate).__name__).lower()
            if callable(candidate) and any(
                marker in name for marker in ("capability", "controlled", "creator", "resolver")
            ):
                exposed.append(name)

    assert exposed == []


def test_public_api_exposes_deployment_composition_without_a_raw_attestation_issuer() -> None:
    assert hasattr(provider_conformance, "compose_query_inventory_staging_conformance_executor")
    assert not hasattr(provider_conformance, "issue_staging_conformance_executor_attestation")
    assert "issue_staging_conformance_executor_attestation" not in provider_conformance.__all__


def test_controlled_composition_rejects_an_untrusted_signer_before_executor_creation(trusted_execution) -> None:
    _, _, calls = trusted_execution
    rogue_signer = _DeploymentSigner(key_id="rogue-deployment-v1")
    delegate = _DeploymentExecutionDelegate(observations=_matching_observations(), calls=calls)

    with pytest.raises(ValueError, match="trusted signing key"):
        provider_conformance.compose_query_inventory_staging_conformance_executor(
            profile=STAGING_PROFILE,
            endpoint_identity="wms-staging-query-primary",
            internal_revision="deploy-r42",
            composition_identity="rogue-composition-v1",
            signer=rogue_signer,
            execution_delegate=delegate,
        )
    assert calls == []


def test_runner_and_report_verifier_do_not_accept_a_caller_owned_verifier() -> None:
    runner_parameters = inspect.signature(provider_conformance.run_query_inventory_staging_live_conformance).parameters
    verifier_parameters = inspect.signature(provider_conformance.verify_wms_conformance_report).parameters

    assert "attestation_verifier" not in runner_parameters
    assert "staging_attestation_verifier" not in verifier_parameters


@pytest.mark.asyncio
async def test_live_runner_revalidates_complete_author_time_profile_before_execute(trusted_execution) -> None:
    _, executor, calls = trusted_execution
    outer_identity_spoof = PRODUCTION_PROFILE.model_copy(update={"identity": STAGING_PROFILE.identity})
    incomplete_staging = STAGING_PROFILE.model_copy(update={"bindings": STAGING_PROFILE.bindings[:1]})

    for profile in (outer_identity_spoof, incomplete_staging):
        with pytest.raises(ValueError, match="canonical author-time staging profile"):
            await provider_conformance.run_query_inventory_staging_live_conformance(
                profile=profile,
                executor=executor,
                fixture_digest=FIXTURE_DIGEST,
                generated_at=GENERATED_AT,
            )
        assert calls == []


@pytest.mark.asyncio
async def test_live_runner_rejects_attestation_and_digest_reuse_on_a_caller_executor(trusted_execution) -> None:
    _, sealed_executor, calls = trusted_execution
    report = await provider_conformance.run_query_inventory_staging_live_conformance(
        profile=STAGING_PROFILE,
        executor=sealed_executor,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
    )
    calls.clear()

    class _CallerExecutor:
        attestation = report.staging_attestation
        composition_identity_digest = report.staging_attestation.composition_identity_digest

        async def execute(self, case):
            calls.append(case.case_id)
            return _matching_observations()[case.case_id]

    with pytest.raises(TypeError, match="controlled composition factory"):
        await provider_conformance.run_query_inventory_staging_live_conformance(
            profile=STAGING_PROFILE,
            executor=_CallerExecutor(),
            fixture_digest=FIXTURE_DIGEST,
            generated_at=GENERATED_AT,
        )
    assert calls == []


@pytest.mark.asyncio
async def test_public_module_api_has_no_private_attestation_rebinding_creator(trusted_execution) -> None:
    """公开模块 API 不提供把落盘 attestation 绑定到其它 delegate 的 helper。"""

    _, sealed_executor, calls = trusted_execution
    report = await provider_conformance.run_query_inventory_staging_live_conformance(
        profile=STAGING_PROFILE,
        executor=sealed_executor,
        fixture_digest=FIXTURE_DIGEST,
        generated_at=GENERATED_AT,
    )
    persisted_report = provider_conformance.verify_wms_conformance_report(report.model_dump(mode="json"))
    calls.clear()
    fake_delegate = _DeploymentExecutionDelegate(observations=_matching_observations(), calls=calls)

    private_creators = {
        name: candidate
        for name, candidate in vars(provider_conformance).items()
        if name.startswith("_")
        and callable(candidate)
        and (
            "controlled_executor" in name
            or "composition_boundary" in name
            or (
                "staging" in name
                and "executor" in name
                and any(marker in name for marker in ("compose", "create", "issue", "mint", "register"))
            )
        )
    }
    accepted_forgery: list[str] = []
    for name, creator in private_creators.items():
        parameters = inspect.signature(creator).parameters
        if {"attestation", "execution_delegate"}.issubset(parameters):
            forged_executor = creator(
                attestation=persisted_report.staging_attestation,
                execution_delegate=fake_delegate,
            )
            try:
                await provider_conformance.run_query_inventory_staging_live_conformance(
                    profile=STAGING_PROFILE,
                    executor=forged_executor,
                    fixture_digest=FIXTURE_DIGEST,
                    generated_at=GENERATED_AT,
                )
            except TypeError:
                rejected = True
            else:
                rejected = False
            if not rejected:
                accepted_forgery.append(name)

    assert accepted_forgery == []
    assert private_creators == {}
    assert calls == []


@pytest.mark.asyncio
async def test_live_runner_rejects_a_copied_executor_without_factory_identity(trusted_execution) -> None:
    _, sealed_executor, calls = trusted_execution
    copied_executor = copy.copy(sealed_executor)

    with pytest.raises(TypeError, match="controlled composition factory"):
        await provider_conformance.run_query_inventory_staging_live_conformance(
            profile=STAGING_PROFILE,
            executor=copied_executor,
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
async def test_live_runner_rejects_bare_callback_without_controlled_composition(trusted_execution) -> None:
    _, _, calls = trusted_execution

    async def bare_callback(case):
        calls.append(case.case_id)
        return _matching_observations()[case.case_id]

    with pytest.raises(TypeError, match="controlled composition factory"):
        await provider_conformance.run_query_inventory_staging_live_conformance(
            profile=STAGING_PROFILE,
            executor=bare_callback,
            fixture_digest=FIXTURE_DIGEST,
            generated_at=GENERATED_AT,
        )
    assert calls == []
