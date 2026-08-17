"""WMS 四角色部署一致性的离线确定性 attestation。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from src.app.runtime.system_capabilities.wms.conformance_matrix import conformance_endpoint_digest
from src.app.runtime.system_capabilities.wms.generated_operation_index import (
    WMS_OPERATION_IDENTITIES,
    WMS_OPERATION_INDEX_DIGEST,
)
from src.app.runtime.system_capabilities.wms.provider_catalog import validate_wms_transport_configuration
from src.app.wms_integration.operation_contract import WmsOperationMode
from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from src.app.wms_integration.provider_readiness import WmsProviderProcessRole, WmsProviderReadiness

if TYPE_CHECKING:
    from collections.abc import Iterable

WMS_DEPLOYMENT_ATTESTATION_SCHEMA_VERSION = "wms-deployment-attestation.v1"
WmsDeploymentRole = Literal["api", "wes-worker", "fulfillment-worker", "beat"]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ImageIdentity = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
StableText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, strict=True)
_EXPECTED_ROLES: tuple[
    Literal["api"],
    Literal["wes-worker"],
    Literal["fulfillment-worker"],
    Literal["beat"],
] = (
    "api",
    "wes-worker",
    "fulfillment-worker",
    "beat",
)
_RUNTIME_CONFIGURATION_FIELDS = (
    "WMS_EFFECT_STATUS_TIMEOUT_SECONDS",
    "WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES",
    "WMS_EFFECT_IDEMPOTENCY_RETENTION_SECONDS",
    "WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS",
    "WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS",
    "WES_EFFECT_NOT_FOUND_GRACE_SECONDS",
    "WES_EFFECT_STATUS_SAFETY_MARGIN_SECONDS",
    "WES_EFFECT_STATUS_SCAN_BATCH_SIZE",
    "WES_EFFECT_STATUS_MAX_IN_FLIGHT",
    "WES_EFFECT_STATUS_CLAIM_LEASE_SECONDS",
    "WES_EFFECT_STATUS_MAX_QUERY_ATTEMPTS",
    "WES_EFFECT_STATUS_INITIAL_BACKOFF_SECONDS",
    "WES_EFFECT_STATUS_MAX_BACKOFF_SECONDS",
    "WES_EFFECT_STATUS_SCAN_PERIOD_SECONDS",
)
_REQUIRED_BEAT_SCHEDULES = (
    ("process-runtime-inbox-batch", "src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch"),
    ("dispatch-outbox-batch", "src.celery_app.tasks.sys.dispatch_system_outbox_batch"),
    ("dispatch-wms-data-outbox-batch", "src.celery_app.tasks.sys.dispatch_wms_data_outbox_batch"),
    (
        "dispatch-wms-fulfillment-outbox-batch",
        "src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch",
    ),
    (
        "dispatch-wms-confirmations-batch",
        "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch",
    ),
    (
        "process-execution-facts-batch",
        "src.celery_app.tasks.execution.process_execution_facts_batch",
    ),
    ("scan-wms-effect-status-batch", "src.celery_app.tasks.workline.scan_wms_effect_status_batch"),
    ("submit-transport-tasks-batch", "src.celery_app.tasks.transport.submit_transport_tasks_batch"),
    (
        "process-transport-evidence-batch",
        "src.celery_app.tasks.transport.process_transport_evidence_batch",
    ),
    ("reconcile-transport-tasks-batch", "src.celery_app.tasks.transport.reconcile_transport_tasks_batch"),
    (
        "publish-transport-outcomes-batch",
        "src.celery_app.tasks.transport.publish_transport_outcomes_batch",
    ),
)
_REQUIRED_DEVICE_COMMAND_BEAT_SCHEDULES = (
    (
        "dispatch-device-commands-batch",
        "src.celery_app.tasks.device_command.dispatch_device_commands_batch",
        10.0,
        10.0,
    ),
    (
        "process-device-evidence-batch",
        "src.celery_app.tasks.device_command.process_device_evidence_batch",
        10.0,
        10.0,
    ),
    (
        "reconcile-device-commands-batch",
        "src.celery_app.tasks.device_command.reconcile_device_commands_batch",
        30.0,
        30.0,
    ),
)
_REQUIRED_FULFILLMENT_ROUTES = (
    "src.celery_app.tasks.sys.dispatch_wms_fulfillment_outbox_batch",
    "src.celery_app.tasks.workline.check_wms_effect_status",
    "src.celery_app.tasks.workline.scan_wms_effect_status_batch",
    "src.celery_app.tasks.transport.submit_transport_tasks_batch",
    "src.celery_app.tasks.transport.process_transport_evidence_batch",
    "src.celery_app.tasks.transport.reconcile_transport_tasks_batch",
    "src.celery_app.tasks.transport.publish_transport_outcomes_batch",
    "src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch",
)


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


class WmsDeploymentCommonFacts(BaseModel):
    """四角色必须完全一致且不含敏感内容的发布事实。"""

    model_config = _MODEL_CONFIG

    image_identity: ImageIdentity
    provider_identity: StableText
    contract_version: StableText
    profile_digest: Sha256Digest
    operation_index_digest: Sha256Digest
    endpoint_digest: Sha256Digest
    operation_count: int
    operation_order_digest: Sha256Digest
    effect_admission_enabled: bool
    runtime_configuration_digest: Sha256Digest


class WmsLaneReadinessFacts(BaseModel):
    """只保留 lane 的数量与顺序摘要，不泄露 endpoint。"""

    model_config = _MODEL_CONFIG

    process_role: Literal["wes", "fulfillment"]
    execution_lane: Literal["wms-data", "wms-fulfillment"]
    operation_count: int
    operation_order_digest: Sha256Digest
    endpoint_key_count: int
    endpoint_key_order_digest: Sha256Digest


class ApiDeploymentFacts(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["api"] = "api"
    readiness: WmsLaneReadinessFacts


class WesWorkerDeploymentFacts(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["wes-worker"] = "wes-worker"
    queues: tuple[Literal["default"], Literal["celery"], Literal["device-command"]]
    readiness: WmsLaneReadinessFacts


class FulfillmentWorkerDeploymentFacts(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["fulfillment-worker"] = "fulfillment-worker"
    queues: tuple[Literal["wms-fulfillment"]]
    concurrency: Literal[1]
    readiness: WmsLaneReadinessFacts


class BeatDeploymentFacts(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["beat"] = "beat"
    required_schedule_digest: Sha256Digest
    fulfillment_route_digest: Sha256Digest


WmsDeploymentRoleFacts = (
    ApiDeploymentFacts | WesWorkerDeploymentFacts | FulfillmentWorkerDeploymentFacts | BeatDeploymentFacts
)


class WmsDeploymentAttestation(BaseModel):
    """单个目标 Compose 角色生成的严格、冻结发布 artifact。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["wms-deployment-attestation.v1"] = WMS_DEPLOYMENT_ATTESTATION_SCHEMA_VERSION
    role: WmsDeploymentRole
    common: WmsDeploymentCommonFacts
    role_facts: WmsDeploymentRoleFacts

    @model_validator(mode="after")
    def require_matching_role_facts(self) -> WmsDeploymentAttestation:
        if self.role != self.role_facts.kind:
            raise ValueError("deployment role and role facts kind must match")
        return self


class WmsDeploymentAttestationSummary(BaseModel):
    """临时 artifact 清理后由部署日志保留的 canonical 脱敏摘要。"""

    model_config = _MODEL_CONFIG

    schema_version: Literal["wms-deployment-attestation-summary.v1"] = "wms-deployment-attestation-summary.v1"
    roles: tuple[
        Literal["api"],
        Literal["wes-worker"],
        Literal["fulfillment-worker"],
        Literal["beat"],
    ]
    common: WmsDeploymentCommonFacts
    role_fact_digests: dict[WmsDeploymentRole, Sha256Digest]


def _lane_readiness_facts(readiness: WmsProviderReadiness) -> WmsLaneReadinessFacts:
    return WmsLaneReadinessFacts(
        process_role=readiness.process_role.value,
        execution_lane=readiness.execution_lane.value,
        operation_count=len(readiness.operation_identities),
        operation_order_digest=_canonical_digest(readiness.operation_identities),
        endpoint_key_count=len(readiness.endpoint_keys),
        endpoint_key_order_digest=_canonical_digest(readiness.endpoint_keys),
    )


def _static_lane_readiness_facts(process_role: WmsProviderProcessRole) -> WmsLaneReadinessFacts:
    lane = process_role.execution_lane
    operations = tuple(operation for operation in WMS_OPERATIONS if operation.execution_lane is lane)
    endpoint_keys: list[str] = []
    for operation in operations:
        endpoint_kind = "query" if operation.mode is WmsOperationMode.QUERY else "submit"
        endpoint_keys.append(f"{operation.identity}:{endpoint_kind}")
        if operation.supports_status_query:
            endpoint_keys.append(f"{operation.identity}:status")
    return WmsLaneReadinessFacts(
        process_role=process_role.value,
        execution_lane=lane.value,
        operation_count=len(operations),
        operation_order_digest=_canonical_digest(tuple(operation.identity for operation in operations)),
        endpoint_key_count=len(endpoint_keys),
        endpoint_key_order_digest=_canonical_digest(tuple(endpoint_keys)),
    )


def _worker_queues(raw_queues: str | None) -> tuple[str, ...]:
    return tuple(queue.strip() for queue in (raw_queues or "").split(",") if queue.strip())


def _beat_role_facts(
    *,
    beat_schedule_source: Mapping[str, Mapping[str, object]],
    task_routes_source: Mapping[str, Mapping[str, object]],
) -> BeatDeploymentFacts:
    schedules: list[tuple[str, str, object, object]] = []
    for schedule_name, expected_task in _REQUIRED_BEAT_SCHEDULES:
        schedule = beat_schedule_source.get(schedule_name)
        if schedule is None or schedule.get("task") != expected_task or "schedule" not in schedule:
            raise ValueError(f"Beat required schedule is missing or invalid: {schedule_name}")
        schedules.append((schedule_name, expected_task, schedule["schedule"], schedule.get("options")))

    for schedule_name, expected_task, expected_period, expected_expires in _REQUIRED_DEVICE_COMMAND_BEAT_SCHEDULES:
        schedule = beat_schedule_source.get(schedule_name)
        options = schedule.get("options") if schedule is not None else None
        if (
            schedule is None
            or schedule.get("task") != expected_task
            or schedule.get("schedule") != expected_period
            or not isinstance(options, Mapping)
            or options.get("expires") != expected_expires
            or task_routes_source.get(expected_task, {}).get("queue") != "device-command"
        ):
            raise ValueError(f"DeviceCommand Beat schedule or route is invalid: {schedule_name}")
        schedules.append((schedule_name, expected_task, expected_period, {"expires": expected_expires}))

    for schedule_name, schedule in beat_schedule_source.items():
        task_name = schedule.get("task")
        if not isinstance(task_name, str) or task_routes_source.get(task_name, {}).get("queue") != "wms-fulfillment":
            continue
        period = schedule.get("schedule")
        options = schedule.get("options")
        expires = options.get("expires") if isinstance(options, Mapping) else None
        if (
            not isinstance(period, (int, float))
            or isinstance(period, bool)
            or not isinstance(expires, (int, float))
            or isinstance(expires, bool)
            or expires <= 0
            or expires > period
        ):
            raise ValueError(
                f"Beat fulfillment task expires must be positive and no greater than schedule: {schedule_name}"
            )

    execution_task = "src.celery_app.tasks.execution.process_execution_facts_batch"
    execution_schedule = beat_schedule_source["process-execution-facts-batch"]
    execution_options = execution_schedule.get("options")
    if (
        execution_schedule.get("schedule") != 10.0
        or not isinstance(execution_options, Mapping)
        or execution_options.get("expires") != 10.0
    ):
        raise ValueError("Beat execution scanner must use schedule=10s and expires=10s")
    if task_routes_source.get(execution_task, {}).get("queue") != "device-command":
        raise ValueError("Beat execution scanner must route to device-command")

    routes: list[tuple[str, str]] = []
    for task_name in _REQUIRED_FULFILLMENT_ROUTES:
        route = task_routes_source.get(task_name)
        if route is None or route.get("queue") != "wms-fulfillment":
            raise ValueError(f"Beat fulfillment/status task must route to wms-fulfillment: {task_name}")
        routes.append((task_name, "wms-fulfillment"))
    return BeatDeploymentFacts(
        required_schedule_digest=_canonical_digest(tuple(schedules)),
        fulfillment_route_digest=_canonical_digest(tuple(routes)),
    )


def _runtime_configuration_digest(settings_source: Any) -> str:
    payload = {
        "effect_admission_enabled": settings_source.WMS_EFFECT_ADMISSION_ENABLED,
        **{field_name: getattr(settings_source, field_name) for field_name in _RUNTIME_CONFIGURATION_FIELDS},
    }
    return _canonical_digest(payload)


def _build_common_facts(
    *,
    settings_source: Any,
    image_identity: str,
) -> tuple[WmsDeploymentCommonFacts, Any]:
    startup = validate_wms_transport_configuration(settings_source=settings_source)
    compiled_profile = startup.compiled_profile
    return (
        WmsDeploymentCommonFacts(
            image_identity=image_identity,
            provider_identity=compiled_profile.profile.profile.identity,
            contract_version=compiled_profile.profile.profile.contract_version,
            profile_digest=compiled_profile.profile_digest,
            operation_index_digest=WMS_OPERATION_INDEX_DIGEST,
            endpoint_digest=conformance_endpoint_digest(compiled_profile),
            operation_count=len(WMS_OPERATION_IDENTITIES),
            operation_order_digest=_canonical_digest(WMS_OPERATION_IDENTITIES),
            effect_admission_enabled=settings_source.WMS_EFFECT_ADMISSION_ENABLED,
            runtime_configuration_digest=_runtime_configuration_digest(settings_source),
        ),
        startup,
    )


def build_wms_deployment_attestation(
    *,
    role: WmsDeploymentRole,
    image_identity: str,
    settings_source: Any,
    worker_queues: str | None = None,
    worker_concurrency: str | int | None = None,
    beat_schedule_source: Mapping[str, Mapping[str, object]] | None = None,
    task_routes_source: Mapping[str, Mapping[str, object]] | None = None,
) -> WmsDeploymentAttestation:
    """从一次 transport 编译产物构造单角色脱敏 artifact。"""

    common, startup = _build_common_facts(
        settings_source=settings_source,
        image_identity=image_identity,
    )

    if role == "api":
        role_facts: WmsDeploymentRoleFacts = ApiDeploymentFacts(readiness=_lane_readiness_facts(startup.wes_readiness))
    elif role == "wes-worker":
        queues = _worker_queues(worker_queues)
        if queues != ("default", "celery", "device-command"):
            raise ValueError("WES worker queues must be exactly default,celery,device-command")
        role_facts = WesWorkerDeploymentFacts(
            queues=("default", "celery", "device-command"),
            readiness=_lane_readiness_facts(startup.wes_readiness),
        )
    elif role == "fulfillment-worker":
        queues = _worker_queues(worker_queues)
        if queues != ("wms-fulfillment",):
            raise ValueError("fulfillment worker must consume only wms-fulfillment")
        try:
            concurrency = int(worker_concurrency) if worker_concurrency is not None else None
        except (TypeError, ValueError) as exc:
            raise ValueError("fulfillment worker must use concurrency=1") from exc
        if concurrency != 1:
            raise ValueError("fulfillment worker must use concurrency=1")
        role_facts = FulfillmentWorkerDeploymentFacts(
            queues=("wms-fulfillment",),
            concurrency=concurrency,
            readiness=_lane_readiness_facts(startup.fulfillment_readiness),
        )
    else:
        if beat_schedule_source is None or task_routes_source is None:
            from src.celery_app import config

            beat_schedule_source = config.beat_schedule
            task_routes_source = config.task_routes
        role_facts = _beat_role_facts(
            beat_schedule_source=beat_schedule_source,
            task_routes_source=task_routes_source,
        )
    return WmsDeploymentAttestation(role=role, common=common, role_facts=role_facts)


def _verify_role_facts(artifact: WmsDeploymentAttestation) -> None:
    if artifact.role in {"api", "wes-worker"}:
        expected_readiness = _static_lane_readiness_facts(WmsProviderProcessRole.WES)
        if artifact.role_facts.readiness != expected_readiness:
            raise ValueError(f"{artifact.role} WES/data readiness drift")
        if artifact.role == "wes-worker" and artifact.role_facts.queues != (
            "default",
            "celery",
            "device-command",
        ):
            raise ValueError("wes-worker queue facts drift")
        return
    if artifact.role == "fulfillment-worker":
        expected_readiness = _static_lane_readiness_facts(WmsProviderProcessRole.FULFILLMENT)
        if artifact.role_facts.readiness != expected_readiness:
            raise ValueError("fulfillment-worker readiness drift")
        if artifact.role_facts.queues != ("wms-fulfillment",) or artifact.role_facts.concurrency != 1:
            raise ValueError("fulfillment-worker queue/concurrency facts drift")
        return

    from src.celery_app import config

    expected_beat_facts = _beat_role_facts(
        beat_schedule_source=config.beat_schedule,
        task_routes_source=config.task_routes,
    )
    if artifact.role_facts != expected_beat_facts:
        raise ValueError("beat schedule/route facts drift")


def verify_wms_deployment_attestations(
    artifacts: Iterable[WmsDeploymentAttestation],
    *,
    settings_source: Any,
    expected_image_identity: str,
) -> tuple[WmsDeploymentAttestation, ...]:
    """以 API verifier 容器的本地编译结果验证四角色事实。"""

    artifacts_by_input_order = tuple(artifacts)
    if len(artifacts_by_input_order) != len(_EXPECTED_ROLES):
        raise ValueError("expected exactly four artifacts with every deployment role exactly once")
    roles = tuple(artifact.role for artifact in artifacts_by_input_order)
    if len(set(roles)) != len(_EXPECTED_ROLES) or set(roles) != set(_EXPECTED_ROLES):
        raise ValueError("expected exactly four artifacts with every deployment role exactly once")

    expected_common, _startup = _build_common_facts(
        settings_source=settings_source,
        image_identity=expected_image_identity,
    )
    for artifact in artifacts_by_input_order:
        if artifact.common != expected_common:
            raise ValueError("WMS deployment facts differ from trusted common baseline")
        _verify_role_facts(artifact)

    return tuple(
        next(artifact for artifact in artifacts_by_input_order if artifact.role == role) for role in _EXPECTED_ROLES
    )


def parse_wms_deployment_attestation_lines(
    artifact_lines: Iterable[str],
) -> tuple[WmsDeploymentAttestation, ...]:
    """从 stdin compact JSON lines 解析恰好四份 artifact。"""

    lines = tuple(line.strip() for line in artifact_lines if line.strip())
    if len(lines) != len(_EXPECTED_ROLES):
        raise ValueError("verify-stdin requires exactly four compact JSON artifacts")
    return tuple(WmsDeploymentAttestation.model_validate_json(line) for line in lines)


def summarize_wms_deployment_attestations(
    artifacts: Iterable[WmsDeploymentAttestation],
) -> WmsDeploymentAttestationSummary:
    """对已验证 artifact 输出可由部署日志留存的脱敏摘要。"""

    verified = tuple(artifacts)
    if tuple(artifact.role for artifact in verified) != _EXPECTED_ROLES:
        raise ValueError("summary requires verified artifacts in canonical role order")
    return WmsDeploymentAttestationSummary(
        roles=_EXPECTED_ROLES,
        common=verified[0].common,
        role_fact_digests={
            artifact.role: _canonical_digest(artifact.role_facts.model_dump(mode="json")) for artifact in verified
        },
    )


__all__ = [
    "WMS_DEPLOYMENT_ATTESTATION_SCHEMA_VERSION",
    "WmsDeploymentAttestation",
    "WmsDeploymentAttestationSummary",
    "WmsDeploymentRole",
    "build_wms_deployment_attestation",
    "parse_wms_deployment_attestation_lines",
    "summarize_wms_deployment_attestations",
    "verify_wms_deployment_attestations",
]
