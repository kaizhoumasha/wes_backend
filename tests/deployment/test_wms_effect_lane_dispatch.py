"""G3 双 WMS lane 的 Celery 路由、长期 client 与 readiness 合同。"""

from __future__ import annotations

import asyncio
import inspect
import os
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.wms_integration.operation_contract import WmsExecutionLane
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS, QUERY_OPERATIONS
from src.app.wms_integration.provider_readiness import WmsProviderProcessRole, WmsProviderReadiness

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_TASK = "src.celery_app.tasks.sys.dispatch_wms_data_outbox_batch"
FULFILLMENT_TASK = "src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch"
CONFIRMATION_TASK = "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch"
SYSTEM_TASK = "src.celery_app.tasks.sys.dispatch_system_outbox_batch"


def test_celery_routes_and_beat_directly_isolate_three_dispatch_tasks() -> None:
    from src.celery_app.config import beat_schedule, task_routes

    assert task_routes[DATA_TASK] == {"queue": "celery"}
    assert task_routes[FULFILLMENT_TASK] == {"queue": "wms-fulfillment"}
    assert task_routes[SYSTEM_TASK] == {"queue": "celery"}
    assert {
        entry["task"] for entry in beat_schedule.values() if entry["task"] in {SYSTEM_TASK, DATA_TASK, FULFILLMENT_TASK}
    } == {SYSTEM_TASK, DATA_TASK, FULFILLMENT_TASK}
    assert sum(entry["task"] == SYSTEM_TASK for entry in beat_schedule.values()) == 1
    assert sum(entry["task"] == DATA_TASK for entry in beat_schedule.values()) == 1
    assert sum(entry["task"] == FULFILLMENT_TASK for entry in beat_schedule.values()) == 1


def test_fulfillment_beat_messages_are_replaceable_database_scan_wakeups() -> None:
    from src.celery_app.config import beat_schedule, task_routes

    expected_periods = {
        "src.celery_app.tasks.transport.submit_transport_tasks_batch": (30.0, {"limit": 100}),
        "src.celery_app.tasks.transport.process_transport_evidence_batch": (10.0, {"limit": 100}),
        "src.celery_app.tasks.transport.reconcile_transport_tasks_batch": (30.0, {"limit": 100}),
        "src.celery_app.tasks.transport.publish_transport_outcomes_batch": (10.0, {"limit": 100}),
        CONFIRMATION_TASK: (10.0, {"limit": 100}),
        FULFILLMENT_TASK: (10.0, None),
        "src.celery_app.tasks.workline.scan_wms_effect_status_batch": (10.0, None),
    }
    fulfillment_schedules = {
        entry["task"]: entry
        for entry in beat_schedule.values()
        if task_routes.get(str(entry["task"]), {}).get("queue") == "wms-fulfillment"
    }

    assert set(fulfillment_schedules) == set(expected_periods)
    for task_name, (period, expected_kwargs) in expected_periods.items():
        entry = fulfillment_schedules[task_name]
        assert entry["schedule"] == period
        assert entry.get("kwargs") == expected_kwargs
        assert 0 < entry["options"]["expires"] <= period


def test_fulfillment_worker_deployment_consumes_only_the_public_fulfillment_queue() -> None:
    import yaml

    class _ComposeSafeLoader(yaml.SafeLoader):
        pass

    _ComposeSafeLoader.add_constructor(
        "!override",
        lambda loader, node: loader.construct_sequence(node, deep=True),
    )
    for compose_name in ("docker-compose.yml", "docker-compose.deploy.yml"):
        compose = yaml.load(
            (REPO_ROOT / compose_name).read_text(encoding="utf-8"),
            Loader=_ComposeSafeLoader,  # noqa: S506 -- 仅扩展 SafeLoader 解析 Compose 的 !override。
        )
        services = compose["services"]
        assert "celery-wms-fulfillment" in services
        fulfillment_command = str(services["celery-wms-fulfillment"]["command"])
        general_command = str(services["celery"]["command"])
        assert "--queues=wms-fulfillment" in fulfillment_command
        assert "--concurrency=1" in fulfillment_command
        assert "wms-fulfillment" not in general_command


def test_all_compose_profiles_and_test_deploy_pipeline_define_both_worker_roles() -> None:
    import yaml

    class _ComposeSafeLoader(yaml.SafeLoader):
        pass

    _ComposeSafeLoader.add_constructor(
        "!override",
        lambda loader, node: loader.construct_sequence(node, deep=True),
    )
    for compose_name in ("docker-compose.yml", "docker-compose.deploy.yml", "docker-compose.test-deploy.yml"):
        compose = yaml.load(
            (REPO_ROOT / compose_name).read_text(encoding="utf-8"),
            Loader=_ComposeSafeLoader,  # noqa: S506 -- 仅扩展 SafeLoader 解析 Compose 的 !override。
        )
        services = compose["services"]
        assert {"celery", "celery-wms-fulfillment"} <= set(services)
        assert "celery_worker" not in services

        general = services["celery"]
        fulfillment = services["celery-wms-fulfillment"]
        assert general["environment"]["CELERY_WORKER_QUEUES"] == "default,celery,device-command"
        assert general["environment"]["WMS_PROVIDER_PROCESS_ROLE"] == "wes"
        expected_profile_path = (
            "${WMS_PROVIDER_PROFILE_FILE:-}" if compose_name == "docker-compose.yml" else "/run/wes/wms-provider.yaml"
        )
        assert general["environment"]["WMS_PROVIDER_PROFILE_FILE"] == expected_profile_path
        assert fulfillment["extends"]["service"] == "celery"
        assert fulfillment["environment"] == {
            "CELERY_WORKER_QUEUES": "wms-fulfillment",
            "CELERY_WORKER_CONCURRENCY": "1",
            "WMS_PROVIDER_PROCESS_ROLE": "fulfillment",
            "WMS_DEPLOYMENT_ROLE": "fulfillment-worker",
        }

    test_entrypoint = (REPO_ROOT / "docker/test/celery.entrypoint.sh").read_text(encoding="utf-8")
    assert "${CELERY_WORKER_QUEUES:?CELERY_WORKER_QUEUES is required}" in test_entrypoint
    assert "${CELERY_WORKER_CONCURRENCY:?CELERY_WORKER_CONCURRENCY is required}" in test_entrypoint
    assert "${WMS_PROVIDER_PROCESS_ROLE:?WMS_PROVIDER_PROCESS_ROLE is required}" in test_entrypoint
    assert '--concurrency="${CELERY_WORKER_CONCURRENCY}"' in test_entrypoint
    assert '--queues="${CELERY_WORKER_QUEUES}"' in test_entrypoint

    pipeline = (REPO_ROOT / "Jenkinsfile.test-deploy").read_text(encoding="utf-8")
    assert "pull api celery celery-wms-fulfillment frontend" in pipeline
    assert "up -d --force-recreate api celery celery-wms-fulfillment frontend nginx" in pipeline
    assert "logs --tail=150 api celery celery-wms-fulfillment frontend nginx" in pipeline


def test_wms_effect_admission_switch_is_consistent_across_profiles_and_effect_creators() -> None:
    import yaml

    class _ComposeSafeLoader(yaml.SafeLoader):
        pass

    _ComposeSafeLoader.add_constructor(
        "!override",
        lambda loader, node: loader.construct_sequence(node, deep=True),
    )
    expected_profiles = {
        ".env.dev": "true",
        ".env.test": "true",
        ".env.prod": "false",
    }
    for profile_name, expected in expected_profiles.items():
        profile = (REPO_ROOT / profile_name).read_text(encoding="utf-8")
        assert f"WMS_EFFECT_ADMISSION_ENABLED={expected}" in profile

    for compose_name, roles in (
        ("docker-compose.yml", ("api", "celery", "celery_beat")),
        ("docker-compose.deploy.yml", ("api", "celery", "celery_beat")),
        ("docker-compose.test-deploy.yml", ("api", "celery")),
    ):
        compose = yaml.load(
            (REPO_ROOT / compose_name).read_text(encoding="utf-8"),
            Loader=_ComposeSafeLoader,  # noqa: S506 -- 仅扩展 SafeLoader 解析 Compose 的 !override。
        )
        values = {compose["services"][role]["environment"]["WMS_EFFECT_ADMISSION_ENABLED"] for role in roles}
        assert values == {"${WMS_EFFECT_ADMISSION_ENABLED:-false}"}


def test_fulfillment_worker_has_no_replica_override_in_any_compose_contract() -> None:
    import yaml

    class _ComposeSafeLoader(yaml.SafeLoader):
        pass

    _ComposeSafeLoader.add_constructor(
        "!override",
        lambda loader, node: loader.construct_sequence(node, deep=True),
    )
    for compose_name in ("docker-compose.yml", "docker-compose.deploy.yml", "docker-compose.test-deploy.yml"):
        compose_path = REPO_ROOT / compose_name
        compose_text = compose_path.read_text(encoding="utf-8")
        compose = yaml.load(
            compose_text,
            Loader=_ComposeSafeLoader,  # noqa: S506 -- 仅扩展 SafeLoader 解析 Compose 的 !override。
        )

        assert "WMS_FULFILLMENT_CELERY_REPLICAS" not in compose_text
        assert compose["services"]["celery-wms-fulfillment"]["deploy"]["replicas"] == 1


def test_fulfillment_worker_startup_rejects_non_single_celery_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.celery_app import app as celery_app_module

    monkeypatch.setattr(
        celery_app_module.celery_async_runtime,
        "_process_role",
        WmsProviderProcessRole.FULFILLMENT,
    )
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "wms-fulfillment")
    monkeypatch.setenv("CELERY_WORKER_CONCURRENCY", "2")

    with pytest.raises(ValueError, match="concurrency=1"):
        celery_app_module._validate_worker_role_queue_contract(frozenset({"wms-fulfillment"}))


def test_fulfillment_worker_startup_accepts_single_celery_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.celery_app import app as celery_app_module

    monkeypatch.setattr(
        celery_app_module.celery_async_runtime,
        "_process_role",
        WmsProviderProcessRole.FULFILLMENT,
    )
    monkeypatch.setenv("CELERY_WORKER_QUEUES", "wms-fulfillment")
    monkeypatch.setenv("CELERY_WORKER_CONCURRENCY", "1")

    celery_app_module._validate_worker_role_queue_contract(frozenset({"wms-fulfillment"}))


@pytest.mark.parametrize(
    "missing_variable",
    ["CELERY_WORKER_QUEUES", "CELERY_WORKER_CONCURRENCY", "WMS_PROVIDER_PROCESS_ROLE"],
)
def test_test_worker_entrypoint_fails_closed_without_explicit_worker_config(missing_variable: str) -> None:
    import subprocess

    environment = os.environ.copy()
    environment["CELERY_WORKER_QUEUES"] = "default,celery,device"
    environment["CELERY_WORKER_CONCURRENCY"] = "4"
    environment["WMS_PROVIDER_PROCESS_ROLE"] = "wes"
    environment.pop(missing_variable)
    environment["PATH"] = "/bin:/usr/bin"
    completed = subprocess.run(
        ["/bin/sh", str(REPO_ROOT / "docker/test/celery.entrypoint.sh")],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.returncode != 0
    assert missing_variable in completed.stderr


def test_lane_runtime_module_exposes_data_and_fulfillment_owner_lifecycles() -> None:
    try:
        module = import_module("src.app.wms_integration.effect_lane_runtime")
    except ModuleNotFoundError:
        pytest.fail("G3 effect lane runtime is missing", pytrace=False)

    expected_exports = {
        "WmsEffectLaneRuntime",
        "bind_wms_effect_lane_runtime",
        "build_wms_effect_lane_runtime",
        "close_bound_wms_effect_lane_runtime",
        "get_wms_effect_lane_runtime",
    }
    assert expected_exports <= set(module.__all__)


@pytest.mark.asyncio
async def test_effect_preparation_runtime_is_single_loop_owned_and_has_no_resource_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.app.wms_integration.effect_preparation_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "_active_runtime", None)
    monkeypatch.setattr(runtime_module, "_active_loop", None)
    catalog = object()
    owner = runtime_module.build_wms_effect_preparation_runtime(catalog=catalog, admission_enabled=True)
    other = runtime_module.build_wms_effect_preparation_runtime(catalog=catalog, admission_enabled=True)

    runtime_module.bind_wms_effect_preparation_runtime(owner)
    runtime_module.bind_wms_effect_preparation_runtime(owner)
    assert runtime_module.get_wms_effect_preparation_runtime() is owner
    with pytest.raises(RuntimeError, match="already bound"):
        runtime_module.bind_wms_effect_preparation_runtime(other)
    with pytest.raises(RuntimeError, match="different"):
        runtime_module.unbind_wms_effect_preparation_runtime(other)

    await runtime_module.close_bound_wms_effect_preparation_runtime()
    assert runtime_module.get_wms_effect_preparation_runtime() is None


@pytest.mark.asyncio
async def test_effect_preparation_runtime_rejects_cross_loop_unbind_and_closes_only_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.app.wms_integration.effect_preparation_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "_active_runtime", None)
    monkeypatch.setattr(runtime_module, "_active_loop", None)
    owner = runtime_module.build_wms_effect_preparation_runtime(catalog=object(), admission_enabled=True)
    candidate = runtime_module.build_wms_effect_preparation_runtime(catalog=object(), admission_enabled=True)
    runtime_module.bind_wms_effect_preparation_runtime(owner)

    async def unbind_from_other_loop() -> str:
        with pytest.raises(RuntimeError, match="event loop mismatch"):
            runtime_module.unbind_wms_effect_preparation_runtime(owner)
        return "rejected"

    assert await asyncio.to_thread(lambda: asyncio.run(unbind_from_other_loop())) == "rejected"
    assert runtime_module.get_wms_effect_preparation_runtime() is owner
    with pytest.raises(RuntimeError, match="different"):
        await runtime_module.close_wms_effect_preparation_runtime(candidate)
    assert runtime_module.get_wms_effect_preparation_runtime() is owner

    await runtime_module.close_wms_effect_preparation_runtime(owner)
    assert runtime_module.get_wms_effect_preparation_runtime() is None


def test_celery_async_runtime_requires_an_explicit_wms_process_role() -> None:
    from src.celery_app.async_runtime import CeleryAsyncRuntime

    wes = CeleryAsyncRuntime(process_role=WmsProviderProcessRole.WES)
    fulfillment = CeleryAsyncRuntime(process_role=WmsProviderProcessRole.FULFILLMENT)

    assert wes.process_role is WmsProviderProcessRole.WES
    assert fulfillment.process_role is WmsProviderProcessRole.FULFILLMENT


def _readiness(
    *,
    process_role: WmsProviderProcessRole,
    operation_identities: tuple[str, ...],
) -> WmsProviderReadiness:
    return WmsProviderReadiness(
        process_role=process_role,
        execution_lane=process_role.execution_lane,
        profile_revision="tests-profile-revision",
        profile_digest="tests-profile-digest",
        operation_identities=operation_identities,
        endpoint_keys=(),
        operation_endpoint_digests=(),
    )


@pytest.mark.asyncio
async def test_effect_lane_runtime_validates_readiness_and_owns_sender_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.app.sys.services.outbox_engine as engine_module
    from src.app.wms_integration.effect_lane_runtime import WmsEffectLaneRuntime

    data_identity = next(
        operation.identity for operation in EFFECT_OPERATIONS if operation.execution_lane is WmsExecutionLane.WMS_DATA
    )
    readiness = _readiness(
        process_role=WmsProviderProcessRole.WES,
        operation_identities=(data_identity,),
    )
    client = SimpleNamespace(aclose=AsyncMock())
    runtime = WmsEffectLaneRuntime(
        process_role=WmsProviderProcessRole.WES,
        readiness=readiness,
        client=client,
    )

    assert runtime.process_role is WmsProviderProcessRole.WES
    assert runtime.readiness is readiness
    assert runtime.client is client
    assert runtime.operation_identities == frozenset({data_identity})

    with pytest.raises(ValueError, match="does not belong"):
        await runtime.send(SimpleNamespace(operation_identity="tests.foreign.effect@v1"))

    expected_result = object()
    sender = AsyncMock(return_value=expected_result)
    monkeypatch.setattr(engine_module, "send_external_http_with_client", sender)
    request = SimpleNamespace(operation_identity=data_identity)

    assert await runtime.send(request) is expected_result
    sender.assert_awaited_once_with(request, client=client)
    await runtime.aclose()
    client.aclose.assert_awaited_once()

    with pytest.raises(ValueError, match="process role/readiness mismatch"):
        WmsEffectLaneRuntime(
            process_role=WmsProviderProcessRole.FULFILLMENT,
            readiness=readiness,
            client=client,
        )
    with pytest.raises(ValueError, match="at least one EFFECT"):
        WmsEffectLaneRuntime(
            process_role=WmsProviderProcessRole.WES,
            readiness=_readiness(
                process_role=WmsProviderProcessRole.WES,
                operation_identities=(QUERY_OPERATIONS[0].identity,),
            ),
            client=client,
        )


@pytest.mark.asyncio
async def test_effect_lane_runtime_build_bind_and_close_are_single_loop_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.app.wms_integration.effect_lane_runtime as runtime_module
    import src.celery_app.outbox_dispatch_composition as composition

    data_identity = next(
        operation.identity for operation in EFFECT_OPERATIONS if operation.execution_lane is WmsExecutionLane.WMS_DATA
    )
    fulfillment_identity = next(
        operation.identity
        for operation in EFFECT_OPERATIONS
        if operation.execution_lane is WmsExecutionLane.WMS_FULFILLMENT
    )
    startup = SimpleNamespace(
        wes_readiness=_readiness(
            process_role=WmsProviderProcessRole.WES,
            operation_identities=(data_identity,),
        ),
        fulfillment_readiness=_readiness(
            process_role=WmsProviderProcessRole.FULFILLMENT,
            operation_identities=(fulfillment_identity,),
        ),
    )
    monkeypatch.setattr(runtime_module, "_active_runtime", None)
    monkeypatch.setattr(runtime_module, "_active_loop", None)
    clear_scoped_engines = MagicMock()
    monkeypatch.setattr(composition, "clear_scoped_outbox_engine_cache", clear_scoped_engines)

    wes_runtime = runtime_module.build_wms_effect_lane_runtime(
        startup,
        process_role=WmsProviderProcessRole.WES,
    )
    fulfillment_runtime = runtime_module.build_wms_effect_lane_runtime(
        startup,
        process_role=WmsProviderProcessRole.FULFILLMENT,
    )
    assert wes_runtime.operation_identities == frozenset({data_identity})
    assert fulfillment_runtime.operation_identities == frozenset({fulfillment_identity})

    runtime_module.bind_wms_effect_lane_runtime(wes_runtime)
    runtime_module.bind_wms_effect_lane_runtime(wes_runtime)
    assert runtime_module.get_wms_effect_lane_runtime() is wes_runtime
    with pytest.raises(RuntimeError, match="already bound"):
        runtime_module.bind_wms_effect_lane_runtime(fulfillment_runtime)

    await runtime_module.close_bound_wms_effect_lane_runtime()
    assert runtime_module.get_wms_effect_lane_runtime() is None
    clear_scoped_engines.assert_called_once_with()
    await runtime_module.close_bound_wms_effect_lane_runtime()
    await fulfillment_runtime.aclose()

    monkeypatch.setattr(runtime_module, "_active_runtime", wes_runtime)
    monkeypatch.setattr(runtime_module, "_active_loop", object())
    with pytest.raises(RuntimeError, match="event loop mismatch"):
        runtime_module.get_wms_effect_lane_runtime()
    monkeypatch.setattr(runtime_module, "_active_runtime", None)
    monkeypatch.setattr(runtime_module, "_active_loop", None)


@pytest.mark.asyncio
async def test_scoped_engine_composition_fails_closed_and_injects_exact_lane_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.app.wms_integration.effect_lane_runtime as runtime_module
    import src.celery_app.outbox_dispatch_composition as composition

    monkeypatch.setattr(composition, "_scoped_engine_cache", {})
    monkeypatch.setattr(composition, "_scoped_engine_owner_pid", None)
    monkeypatch.setattr(composition, "_scoped_engine_owner_loop", None)
    scopes = composition.build_outbox_claim_scopes()
    system_engine = composition.build_scoped_outbox_engine(composition.OutboxClaimScopeName.SYSTEM)
    assert system_engine.operation_identities is None
    assert system_engine.exclude_operation_identities == tuple(
        sorted(scopes[composition.OutboxClaimScopeName.SYSTEM].excluded_operation_identities)
    )

    monkeypatch.setattr(runtime_module, "get_wms_effect_lane_runtime", lambda: None)
    with pytest.raises(RuntimeError, match="not initialized"):
        composition.build_scoped_outbox_engine(composition.OutboxClaimScopeName.WMS_DATA)

    data_scope = scopes[composition.OutboxClaimScopeName.WMS_DATA]
    data_runtime = SimpleNamespace(
        process_role=WmsProviderProcessRole.FULFILLMENT,
        operation_identities=data_scope.included_operation_identities,
        send=AsyncMock(),
    )
    monkeypatch.setattr(runtime_module, "get_wms_effect_lane_runtime", lambda: data_runtime)
    with pytest.raises(RuntimeError, match="worker process role"):
        composition.build_scoped_outbox_engine(composition.OutboxClaimScopeName.WMS_DATA)

    data_runtime.process_role = WmsProviderProcessRole.WES
    data_runtime.operation_identities = frozenset()
    with pytest.raises(RuntimeError, match="claim scope/readiness mismatch"):
        composition.build_scoped_outbox_engine(composition.OutboxClaimScopeName.WMS_DATA)

    data_runtime.operation_identities = data_scope.included_operation_identities
    data_engine = composition.build_scoped_outbox_engine(composition.OutboxClaimScopeName.WMS_DATA)
    assert data_engine.operation_identities == tuple(sorted(data_scope.included_operation_identities or ()))
    assert await data_engine.workline_domain_dispatcher(object(), 50) == {
        "dispatched": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
    }

    fulfillment_scope = scopes[composition.OutboxClaimScopeName.WMS_DISPATCH]
    fulfillment_runtime = SimpleNamespace(
        process_role=WmsProviderProcessRole.FULFILLMENT,
        operation_identities=fulfillment_scope.included_operation_identities,
        send=AsyncMock(),
    )
    monkeypatch.setattr(runtime_module, "get_wms_effect_lane_runtime", lambda: fulfillment_runtime)
    fulfillment_engine = composition.build_scoped_outbox_engine(composition.OutboxClaimScopeName.WMS_DISPATCH)
    assert fulfillment_engine.operation_identities == tuple(
        sorted(fulfillment_scope.included_operation_identities or ())
    )


@pytest.mark.asyncio
async def test_repeated_scoped_engine_builds_preserve_cursor_until_later_buckets_are_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.celery_app.outbox_dispatch_composition as composition

    buckets = tuple(f"bucket-{index}" for index in range(5))

    class _CursorEngine:
        def __init__(self, **_kwargs: object) -> None:
            self.cursor = 0

        async def dispatch(self, _db: object, *, limit: int) -> tuple[str, ...]:
            claimed = tuple(buckets[(self.cursor + offset) % len(buckets)] for offset in range(limit))
            self.cursor = (self.cursor + limit) % len(buckets)
            return claimed

    monkeypatch.setattr(composition, "SystemOutboxEngine", _CursorEngine)
    monkeypatch.setattr(composition, "_scoped_engine_cache", {}, raising=False)
    monkeypatch.setattr(composition, "_scoped_engine_owner_pid", None, raising=False)
    monkeypatch.setattr(composition, "_scoped_engine_owner_loop", None, raising=False)

    claimed: list[str] = []
    engine_ids: list[int] = []
    for _ in range(3):
        engine = composition.build_scoped_outbox_engine(composition.OutboxClaimScopeName.SYSTEM)
        engine_ids.append(id(engine))
        claimed.extend(await engine.dispatch(object(), limit=2))

    assert len(set(engine_ids)) == 1
    assert set(buckets) <= set(claimed)


def test_effect_status_tasks_are_exactly_routed_to_fulfillment_queue() -> None:
    from src.celery_app.config import beat_schedule, task_routes

    check_task = "src.celery_app.tasks.workline.check_wms_effect_status"
    scan_task = "src.celery_app.tasks.workline.scan_wms_effect_status_batch"

    assert task_routes[check_task] == {"queue": "wms-fulfillment"}
    assert task_routes[scan_task] == {"queue": "wms-fulfillment"}
    assert beat_schedule["scan-wms-effect-status-batch"]["task"] == scan_task


def test_effect_lane_runtime_builder_has_no_discarded_settings_parameter() -> None:
    from src.app.wms_integration.effect_lane_runtime import build_wms_effect_lane_runtime

    assert "settings_source" not in inspect.signature(build_wms_effect_lane_runtime).parameters


@pytest.mark.asyncio
async def test_scoped_engine_cache_rejects_fork_or_event_loop_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.celery_app.outbox_dispatch_composition as composition

    current_loop = asyncio.get_running_loop()
    monkeypatch.setattr(composition, "_scoped_engine_cache", {})
    monkeypatch.setattr(composition, "_scoped_engine_owner_pid", os.getpid() + 1)
    monkeypatch.setattr(composition, "_scoped_engine_owner_loop", current_loop)
    with pytest.raises(RuntimeError, match="process fork"):
        composition.build_scoped_outbox_engine(composition.OutboxClaimScopeName.SYSTEM)

    monkeypatch.setattr(composition, "_scoped_engine_owner_pid", os.getpid())
    monkeypatch.setattr(composition, "_scoped_engine_owner_loop", object())
    with pytest.raises(RuntimeError, match="event loop mismatch"):
        composition.build_scoped_outbox_engine(composition.OutboxClaimScopeName.SYSTEM)
    with pytest.raises(RuntimeError, match="different event loop"):
        composition.clear_scoped_outbox_engine_cache()
