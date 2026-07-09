"""货架业务操作编排服务。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError

from src.app.rack.models.operation import RackOperationStatus, RackTaskStatus, RackTaskType
from src.app.rack.repositories.operation_repository import (
    RackOperationRepository,
    RackTaskRepository,
    rack_operation_repository,
    rack_task_repository,
)
from src.app.rack.services.completion_policy import (
    derive_required_task_status,
    requires_resource_projection_confirmation,
    resolve_operation_completion_policy,
    resolve_request_completion_policy,
)
from src.app.rack.services.gateway import (
    DEFAULT_RACK_OPERATION_ENDPOINT,
    WmsRcsRackGateway,
    wms_rcs_rack_gateway,
)
from src.app.rack.services.task_lifecycle_service import (
    RackTaskLifecycleService,
    rack_task_lifecycle_service,
)
from src.app.resource.models import RackKind
from src.app.resource.repositories.resource_repository import (
    RackPlacementRepository,
    rack_placement_repository,
)
from src.app.runtime.capabilities.material_flow.station_lease_service import (
    StationLeaseService,
    station_lease_service,
)
from src.app.sys.models.outbox import (
    DispatchEnvelope,
    OperationCompletionPolicy,
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxStatus,
    SystemOutboxTargetType,
)
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository, outbox_repository
from src.app.workline.services.rack_position_service import (
    WorklineRackPositionService,
    workline_rack_position_service,
)
from src.utils.timezone import timezone
from src.utils.value_normalization import coerce_optional_int, coerce_optional_str, enum_value, require_text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS = 300
_RACK_TASK_TYPE_ALIASES = {
    "MOVE_OUT_ACTIVE_RACK": RackTaskType.MOVE_RACK.value,
}


@dataclass(frozen=True)
class RackTaskSpec:
    """货架 operation 拆分后的低级 task 描述。"""

    sequence_no: int
    task_type: str
    rack_code: str | None
    rack_kind: str | None
    source_position_code: str | None
    target_position_code: str | None
    target_position_role: str | None
    dispatch_key: str
    target_code: str
    request_json: dict[str, Any]
    actions_json: dict[str, Any]
    required: bool


class RackOperationService:
    """货架业务操作服务。

    该服务只负责短事务内的容量校验、task/outbox 创建和 session 等待标记；
    外部 HTTP、Celery 派发和 WMS/RCS SDK 调用由既有 outbox 流程处理。
    """

    def __init__(
        self,
        *,
        rack_task_repository: RackTaskRepository = rack_task_repository,
        rack_operation_repository: RackOperationRepository = rack_operation_repository,
        rack_task_lifecycle_service: RackTaskLifecycleService = rack_task_lifecycle_service,
        outbox_repository: SystemOutboxRepository = outbox_repository,
        rack_position_service: WorklineRackPositionService = workline_rack_position_service,
        rack_placement_repository: RackPlacementRepository = rack_placement_repository,
        station_lease_service: StationLeaseService = station_lease_service,
        gateway: WmsRcsRackGateway = wms_rcs_rack_gateway,
    ) -> None:
        self.rack_task_repository = rack_task_repository
        self.rack_operation_repository = rack_operation_repository
        self.rack_task_lifecycle_service = rack_task_lifecycle_service
        self.outbox_repository = outbox_repository
        self.rack_position_service = rack_position_service
        self.rack_placement_repository = rack_placement_repository
        self.station_lease_service = station_lease_service
        self.gateway = gateway

    async def request_operation_tasks(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        operation_type: str,
        workline: Any | None = None,
        session: Any | None,
        target_code: str | None = None,
        trace_id: str,
        task_specs: Sequence[Mapping[str, Any] | RackTaskSpec],
        completion_policy: OperationCompletionPolicy | str | None = None,
        timeout_seconds: int = DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS,
    ) -> list[Any]:
        """按插件给出的低级货架 task 描述创建可追踪的 operation 任务。"""

        operation_key = require_text(operation_key, "operation_key")
        operation_type = require_text(operation_type, "operation_type")
        workline_code = coerce_optional_str(getattr(workline, "line_code", None))
        workline_id = coerce_optional_int(getattr(workline, "id", None))
        material_session_id = coerce_optional_int(getattr(session, "id", None))
        target_code = coerce_optional_str(target_code) or DEFAULT_RACK_OPERATION_ENDPOINT
        trace_id = require_text(trace_id, "trace_id")
        specs = self._normalize_task_specs(
            operation_key=operation_key,
            operation_type=operation_type,
            workline_id=workline_id,
            workline_code=workline_code,
            material_session_id=material_session_id,
            target_code=target_code,
            trace_id=trace_id,
            task_specs=task_specs,
        )

        operation = await self._get_or_create_operation(
            db,
            operation_key=operation_key,
            operation_type=operation_type,
            workline_id=workline_id,
            workline_code=workline_code,
            material_session_id=material_session_id,
            trace_id=trace_id,
            task_specs=specs,
            completion_policy=resolve_request_completion_policy(completion_policy),
        )
        existing_tasks = await self.rack_task_repository.list_by_operation_key(db, operation_key=operation_key)
        if existing_tasks:
            _ensure_existing_operation_request_consistent(
                existing_tasks,
                operation_key=operation_key,
                operation_type=operation_type,
                specs=specs,
            )
            await self._ensure_existing_task_outbox_session_owners(db, existing_tasks, session=session)
            return list(existing_tasks)

        await self._ensure_capacity_for_task_specs(
            db,
            operation_key=operation_key,
            workline_code=workline_code,
            specs=specs,
        )

        created_tasks: list[Any] = []
        released_station_position_codes = _released_station_position_codes(specs)
        for spec in specs:
            outbox = await self._get_or_create_outbox(
                db,
                session=session,
                workline=workline,
                spec=spec,
                released_station_position_codes=released_station_position_codes,
            )
            task = await self.rack_task_lifecycle_service.record_requested_task(
                db,
                session=session,
                workline=workline,
                outbox=outbox,
                operation_id=coerce_optional_int(getattr(operation, "id", None)),
                operation_key=operation_key,
                operation_type=operation_type,
                sequence_no=spec.sequence_no,
                task_type=spec.task_type,
                task_key=spec.dispatch_key,
                dispatch_key=spec.dispatch_key,
                target_code=spec.target_code,
                request_json=spec.request_json,
                timeout_seconds=timeout_seconds,
                source_system="WMS_RCS",
                trace_id=trace_id,
                rack_kind=spec.rack_kind,
                rack_code=spec.rack_code,
                source_position_code=spec.source_position_code,
                target_position_code=spec.target_position_code,
                target_position_role=spec.target_position_role,
                actions_json=spec.actions_json,
            )
            created_tasks.append(task)

        _ = await self.sync_operation_status(db, operation_key=operation_key)
        return created_tasks

    async def _get_or_create_operation(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        operation_type: str,
        workline_id: int | None,
        workline_code: str | None,
        material_session_id: int | None,
        trace_id: str,
        task_specs: list[RackTaskSpec],
        completion_policy: OperationCompletionPolicy,
    ) -> Any:
        existing = await self.rack_operation_repository.get_by_operation_key(db, operation_key)
        if existing is not None:
            if getattr(existing, "operation_type", None) != operation_type:
                raise ValueError("existing rack operation type differs from request")
            return existing
        operation_data = {
            "operation_key": operation_key,
            "operation_type": operation_type,
            "operation_status": RackOperationStatus.REQUESTED.value,
            "completion_policy": completion_policy.value,
            "workline_id": workline_id,
            "workline_code": workline_code,
            "material_session_id": material_session_id,
            "trace_id": trace_id,
            "request_json": {
                "task_specs": [asdict(spec) for spec in task_specs],
            },
            "requested_at": timezone.now_for_db(),
            "started_at": timezone.now_for_db(),
        }
        try:
            return await self.rack_operation_repository.create(db, operation_data)
        except (IntegrityError, ValueError) as exc:
            existing = await self.rack_operation_repository.get_by_operation_key(db, operation_key)
            if existing is None:
                raise
            if getattr(existing, "operation_type", None) != operation_type:
                raise ValueError("existing rack operation type differs from request") from exc
            return existing

    async def _get_or_create_outbox(
        self,
        db: AsyncSession,
        *,
        session: Any | None,
        workline: Any | None,
        spec: RackTaskSpec,
        released_station_position_codes: set[str],
    ) -> SystemOutbox:
        payload_json = _outbox_payload(spec)
        existing = await self.outbox_repository.get_by_dispatch_key_for_update(db, spec.dispatch_key)
        if existing is not None:
            _ensure_existing_outbox_active(existing)
            _ensure_existing_outbox_shape(existing, spec=spec, payload_json=payload_json)
            await self._prepare_existing_outbox_for_request(db, existing, session=session, payload_json=payload_json)
            return existing

        station_position_code = _station_dispatch_position_code(spec)
        workline_id = coerce_optional_int(getattr(workline, "id", None))
        workline_code = coerce_optional_str(getattr(workline, "line_code", None))
        if station_position_code is not None and workline_id is not None and workline_code is not None:
            try:
                async with db.begin_nested():
                    outbox = await self.station_lease_service.claim_station_dispatch_lease(
                        db,
                        workline_id=workline_id,
                        workline_code=workline_code,
                        position_code=station_position_code,
                        envelope=DispatchEnvelope(
                            session_id=coerce_optional_int(getattr(session, "id", None)),
                            workline_id=workline_id,
                            operation_domain="RACK",
                            operation_key=coerce_optional_str(spec.request_json.get("operation_key")),
                            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
                            dispatch_key=spec.dispatch_key,
                            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
                            target_code=spec.target_code,
                            payload_json=payload_json,
                            trace_id=coerce_optional_str(spec.request_json.get("trace_id")),
                        ),
                        allow_active_rack_bound=_allows_active_rack_bound_for_station_claim(
                            spec,
                            station_position_code=station_position_code,
                            released_station_position_codes=released_station_position_codes,
                        ),
                        allow_active_operation_key=coerce_optional_str(spec.request_json.get("operation_key")),
                    )
                    if outbox is None:
                        raise ValueError("station dispatch lease is not available")
            except IntegrityError:
                existing = await self.outbox_repository.get_by_dispatch_key_for_update(db, spec.dispatch_key)
                if existing is None:
                    raise
                _ensure_existing_outbox_active(existing)
                _ensure_existing_outbox_shape(existing, spec=spec, payload_json=payload_json)
                await self._prepare_existing_outbox_for_request(
                    db, existing, session=session, payload_json=payload_json
                )
                return existing
            return outbox

        outbox = SystemOutbox(
            session_id=coerce_optional_int(getattr(session, "id", None)),
            workline_id=workline_id,
            operation_domain="RACK",
            operation_key=coerce_optional_str(spec.request_json.get("operation_key")),
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            dispatch_key=spec.dispatch_key,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=spec.target_code,
            payload_json=payload_json,
            status=SystemOutboxStatus.NEW,
            trace_id=coerce_optional_str(spec.request_json.get("trace_id")),
        )
        try:
            async with db.begin_nested():
                db.add(outbox)
                await db.flush()
        except IntegrityError:
            existing = await self.outbox_repository.get_by_dispatch_key_for_update(db, spec.dispatch_key)
            if existing is None:
                raise
            _ensure_existing_outbox_active(existing)
            _ensure_existing_outbox_shape(existing, spec=spec, payload_json=payload_json)
            await self._prepare_existing_outbox_for_request(db, existing, session=session, payload_json=payload_json)
            return existing
        return outbox

    async def _ensure_existing_task_outbox_session_owners(
        self,
        db: AsyncSession,
        tasks: list[Any],
        *,
        session: Any | None,
    ) -> None:
        session_id = coerce_optional_int(getattr(session, "id", None))
        if session_id is None:
            return
        for task in tasks:
            dispatch_key = coerce_optional_str(getattr(task, "dispatch_key", None))
            if dispatch_key is None:
                continue
            outbox = await self.outbox_repository.get_by_dispatch_key_for_update(db, dispatch_key)
            if outbox is not None:
                await self._ensure_existing_outbox_session_owner(db, outbox, session=session)

    @staticmethod
    async def _prepare_existing_outbox_for_request(
        db: AsyncSession,
        outbox: SystemOutbox,
        *,
        session: Any | None,
        payload_json: dict[str, Any],
    ) -> None:
        changed = False
        can_patch_existing_outbox = _is_new_outbox(outbox)
        if outbox.payload_json != payload_json:
            if not can_patch_existing_outbox:
                raise ValueError("existing rack operation outbox payload differs after dispatch")
            outbox.payload_json = payload_json
            changed = True

        session_id = coerce_optional_int(getattr(session, "id", None))
        if session_id is not None:
            if outbox.session_id is None:
                if not can_patch_existing_outbox:
                    raise ValueError("existing rack outbox session owner missing after dispatch")
                outbox.session_id = session_id
                changed = True
            elif outbox.session_id != session_id:
                raise ValueError("existing rack outbox belongs to another session")

        if changed:
            await db.flush()

    @staticmethod
    async def _ensure_existing_outbox_session_owner(
        db: AsyncSession, outbox: SystemOutbox, *, session: Any | None
    ) -> None:
        session_id = coerce_optional_int(getattr(session, "id", None))
        if session_id is None:
            return
        if outbox.session_id is None:
            outbox.session_id = session_id
            await db.flush()
            return
        if outbox.session_id != session_id:
            raise ValueError("existing rack outbox belongs to another session")

    async def derive_operation_status(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
    ) -> str:
        """按 sibling task 与 resource projection 派生 operation 状态。"""

        operation_key = require_text(operation_key, "operation_key")
        operation = await self.rack_operation_repository.get_by_operation_key(db, operation_key)
        completion_policy = resolve_operation_completion_policy(operation)
        tasks = await self.rack_task_repository.list_by_operation_key(
            db,
            operation_key=operation_key,
        )
        required_tasks = [task for task in tasks if _task_required(task)]
        derived_status = derive_required_task_status(required_tasks)
        if derived_status is not None:
            return derived_status.value

        if requires_resource_projection_confirmation(
            completion_policy
        ) and not await self._resource_projection_confirms_success(db, required_tasks):
            return RackOperationStatus.RECONCILING.value
        return RackOperationStatus.SUCCEEDED.value

    async def _persist_operation_status(self, db: AsyncSession, *, operation_key: str) -> str:
        return await self.sync_operation_status(db, operation_key=operation_key)

    async def sync_operation_status(self, db: AsyncSession, *, operation_key: str) -> str:
        """在同一事务中回写 RackOperation 派生状态。"""

        operation = await self.rack_operation_repository.get_by_operation_key(db, operation_key)
        completion_policy = resolve_operation_completion_policy(operation)
        operation_status = await self.derive_operation_status(db, operation_key=operation_key)
        result_json_patch = {}
        if (
            completion_policy == OperationCompletionPolicy.CALLBACK_PLUS_RECONCILIATION
            and operation_status == RackOperationStatus.SUCCEEDED.value
        ):
            result_json_patch["reconciliation_expected"] = True
        _ = await self.rack_operation_repository.mark_status(
            db,
            operation_key=operation_key,
            operation_status=operation_status,
            result_json_patch=result_json_patch,
        )
        return operation_status

    def _normalize_task_specs(
        self,
        *,
        operation_key: str,
        operation_type: str,
        workline_id: int | None,
        workline_code: str | None,
        material_session_id: int | None,
        trace_id: str,
        target_code: str,
        task_specs: Sequence[Mapping[str, Any] | RackTaskSpec],
    ) -> list[RackTaskSpec]:
        specs = [
            self._normalize_task_spec(
                operation_key=operation_key,
                operation_type=operation_type,
                workline_id=workline_id,
                workline_code=workline_code,
                material_session_id=material_session_id,
                target_code=target_code,
                trace_id=trace_id,
                task_spec=task_spec,
            )
            for task_spec in task_specs
        ]
        if not specs:
            raise ValueError("rack operation task_specs is required")
        for spec in specs:
            _ensure_task_spec_contract(spec)
        sequence_numbers = [spec.sequence_no for spec in specs]
        if len(set(sequence_numbers)) != len(sequence_numbers):
            raise ValueError("rack operation task_specs sequence_no must be unique")
        return sorted(specs, key=lambda spec: spec.sequence_no)

    def _normalize_task_spec(
        self,
        *,
        operation_key: str,
        operation_type: str,
        workline_id: int | None,
        workline_code: str | None,
        material_session_id: int | None,
        target_code: str,
        trace_id: str,
        task_spec: Mapping[str, Any] | RackTaskSpec,
    ) -> RackTaskSpec:
        if isinstance(task_spec, RackTaskSpec):
            task_spec = asdict(task_spec)

        sequence_no = _required_int(task_spec.get("sequence_no"), "task_specs[].sequence_no")
        if sequence_no <= 0:
            raise ValueError("rack operation task_specs sequence_no must be greater than 0")
        raw_task_type = enum_value(task_spec.get("task_type"))
        task_type = _rack_task_type(raw_task_type)
        rack_code = coerce_optional_str(task_spec.get("rack_code"))
        rack_kind = coerce_optional_str(task_spec.get("rack_kind"))
        source_position_code = coerce_optional_str(task_spec.get("source_position_code"))
        target_position_code = coerce_optional_str(task_spec.get("target_position_code"))
        target_position_role = coerce_optional_str(task_spec.get("target_position_role"))
        spec_target_code = coerce_optional_str(task_spec.get("target_code")) or target_code

        raw_actions = task_spec.get("actions_json")
        actions_json = dict(raw_actions) if isinstance(raw_actions, Mapping) else {}
        required = bool(task_spec.get("required", actions_json.get("required", True)))
        actions_json.setdefault("action", str(raw_task_type or task_type))
        if actions_json.get("action") != task_type:
            actions_json.setdefault("task_type", task_type)
        actions_json["required"] = required

        raw_request = task_spec.get("request_json")
        request_json = dict(raw_request) if isinstance(raw_request, Mapping) else {}
        envelope = self.gateway.build_task_envelope(
            operation_key=operation_key,
            operation_type=operation_type,
            sequence_no=sequence_no,
            task_type=task_type,
            trace_id=trace_id,
            workline_id=workline_id,
            workline_code=workline_code,
            material_session_id=material_session_id,
            rack_code=rack_code,
            rack_kind=rack_kind,
            source_position_code=source_position_code,
            target_position_code=target_position_code,
            target_position_role=target_position_role,
            actions_json=actions_json,
            request_json=request_json,
            target_code=spec_target_code,
        )
        return RackTaskSpec(
            sequence_no=sequence_no,
            task_type=task_type,
            rack_code=rack_code,
            rack_kind=rack_kind,
            source_position_code=source_position_code,
            target_position_code=target_position_code,
            target_position_role=target_position_role,
            dispatch_key=coerce_optional_str(task_spec.get("dispatch_key")) or envelope.dispatch_key,
            target_code=envelope.target_code,
            request_json=envelope.payload_json,
            actions_json=actions_json,
            required=required,
        )

    async def _ensure_capacity_for_task_specs(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        workline_code: str | None,
        specs: list[RackTaskSpec],
    ) -> None:
        if workline_code is None:
            return
        await self._ensure_move_rack_sources_for_task_specs(
            db,
            operation_key=operation_key,
            workline_code=workline_code,
            specs=specs,
        )
        for target_position_code in sorted(_reserved_target_position_codes(specs)):
            target_specs = _target_occupying_specs(specs, target_position_code=target_position_code)
            capacity = await self._require_target_position_capacity_for_specs(
                db,
                workline_code=workline_code,
                target_position_code=target_position_code,
                target_specs=target_specs,
            )
            await self._ensure_capacity_for_target_position(
                db,
                operation_key=operation_key,
                workline_code=workline_code,
                target_position_code=target_position_code,
                capacity=capacity,
                incoming_target_count=len(target_specs),
                specs=specs,
            )

    async def _ensure_move_rack_sources_for_task_specs(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        workline_code: str,
        specs: list[RackTaskSpec],
    ) -> None:
        move_specs_by_source: dict[str, list[RackTaskSpec]] = {}
        source_rack_keys: set[tuple[str, str]] = set()
        for spec in specs:
            if spec.task_type != RackTaskType.MOVE_RACK.value:
                continue
            rack_code = coerce_optional_str(spec.rack_code)
            source_position_code = coerce_optional_str(spec.source_position_code)
            if rack_code is None or source_position_code is None:
                raise ValueError("rack operation MOVE_RACK requires rack_code and source_position_code")
            source_rack_key = (source_position_code, rack_code)
            if source_rack_key in source_rack_keys:
                raise ValueError(
                    "rack operation MOVE_RACK source rack duplicated: "
                    f"{workline_code}/{source_position_code} rack_code={rack_code}"
                )
            source_rack_keys.add(source_rack_key)
            move_specs_by_source.setdefault(source_position_code, []).append(spec)

        for source_position_code, source_specs in move_specs_by_source.items():
            active_source_racks_by_code = await self._active_racks_by_code_at_position(
                db,
                workline_code=workline_code,
                position_code=source_position_code,
            )
            for spec in source_specs:
                rack_code = coerce_optional_str(spec.rack_code)
                if rack_code is None:
                    raise ValueError("rack operation MOVE_RACK requires rack_code")
                active_source_rack = active_source_racks_by_code.get(rack_code)
                if active_source_rack is None:
                    raise ValueError(
                        "rack operation MOVE_RACK source rack mismatch: "
                        f"{workline_code}/{source_position_code} rack_code={rack_code}"
                    )
                actual_rack_kind = _rack_kind_value(getattr(active_source_rack, "rack_kind", None))
                spec_rack_kind = _rack_kind_value(spec.rack_kind)
                if spec_rack_kind is not None and actual_rack_kind != spec_rack_kind:
                    raise ValueError(
                        "rack operation MOVE_RACK source rack_kind mismatch: "
                        f"{workline_code}/{source_position_code} rack_code={rack_code} "
                        f"expected={actual_rack_kind} requested={spec_rack_kind}"
                    )
                active_claims = await self.rack_task_repository.list_move_rack_source_claims(
                    db,
                    workline_code=workline_code,
                    source_position_code=source_position_code,
                    rack_code=rack_code,
                )
                conflicting_claims = [
                    task for task in active_claims if getattr(task, "operation_key", None) != operation_key
                ]
                if conflicting_claims:
                    claimed_operation_key = getattr(conflicting_claims[0], "operation_key", None)
                    raise ValueError(
                        "rack operation MOVE_RACK source rack already claimed: "
                        f"{workline_code}/{source_position_code} rack_code={rack_code} "
                        f"operation_key={claimed_operation_key}"
                    )

    async def _require_target_position_capacity_for_specs(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        target_position_code: str,
        target_specs: list[RackTaskSpec],
    ) -> int:
        capacity: int | None = None
        validated_rack_kinds: set[RackKind] = set()
        for spec in target_specs:
            if spec.rack_kind is None:
                raise ValueError(
                    f"rack operation target position requires rack_kind: {workline_code}/{target_position_code}"
                )
            rack_kind = _rack_kind(spec.rack_kind)
            if rack_kind in validated_rack_kinds:
                continue
            validated_rack_kinds.add(rack_kind)
            _position, position_capacity = await self.rack_position_service.require_position_capacity_for_update(
                db,
                workline_code=workline_code,
                position_code=target_position_code,
                rack_kind=rack_kind,
            )
            capacity = position_capacity if capacity is None else capacity
        if capacity is None:
            raise ValueError(
                f"rack operation target position has no inbound tasks: {workline_code}/{target_position_code}"
            )
        return capacity

    async def _ensure_capacity_for_target_position(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        workline_code: str,
        target_position_code: str,
        capacity: int,
        incoming_target_count: int,
        specs: list[RackTaskSpec],
    ) -> None:
        active_count = await self.rack_placement_repository.count_active_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=target_position_code,
        )
        same_operation_release_count = await self._same_operation_release_count(
            db,
            operation_key=operation_key,
            workline_code=workline_code,
            source_position_code=target_position_code,
            specs=specs,
        )
        other_operation_active_target_task_count = await self._other_operation_active_target_task_count(
            db,
            operation_key=operation_key,
            workline_code=workline_code,
            target_position_code=target_position_code,
        )
        available_capacity_for_operation = (
            capacity - active_count - other_operation_active_target_task_count + same_operation_release_count
        )
        if incoming_target_count > available_capacity_for_operation:
            raise ValueError(
                "rack operation target position capacity unavailable: "
                f"{workline_code}/{target_position_code} "
                f"capacity={capacity} active={active_count} "
                f"other_operation_target_tasks={other_operation_active_target_task_count} "
                f"same_operation_release={same_operation_release_count} "
                f"incoming_target_tasks={incoming_target_count}"
            )

    async def _same_operation_release_count(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        workline_code: str,
        source_position_code: str,
        specs: list[RackTaskSpec],
    ) -> int:
        active_source_rack_codes = await self._active_rack_codes_at_position(
            db,
            workline_code=workline_code,
            position_code=source_position_code,
        )
        release_rack_codes = {
            rack_code
            for spec in specs
            if spec.required
            and spec.task_type == RackTaskType.MOVE_RACK.value
            and spec.source_position_code == source_position_code
            for rack_code in [coerce_optional_str(spec.rack_code)]
            if rack_code in active_source_rack_codes
        }
        existing_tasks = await self.rack_task_repository.list_by_operation_key(db, operation_key=operation_key)
        release_rack_codes.update(
            rack_code
            for task in existing_tasks
            if _task_required(task)
            and _task_type(task) == RackTaskType.MOVE_RACK.value
            and getattr(task, "source_position_code", None) == source_position_code
            and _task_status(task)
            in {
                RackTaskStatus.PLANNED.value,
                RackTaskStatus.REQUESTED.value,
                RackTaskStatus.IN_PROGRESS.value,
                RackTaskStatus.SUCCEEDED.value,
            }
            for rack_code in [coerce_optional_str(getattr(task, "rack_code", None))]
            if rack_code in active_source_rack_codes
        )
        return len(release_rack_codes)

    async def _active_rack_codes_at_position(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
    ) -> set[str]:
        return set(
            await self._active_racks_by_code_at_position(
                db,
                workline_code=workline_code,
                position_code=position_code,
            )
        )

    async def _active_racks_by_code_at_position(
        self,
        db: AsyncSession,
        *,
        workline_code: str,
        position_code: str,
    ) -> dict[str, Any]:
        return {
            rack_code: placement
            for placement in await self.rack_placement_repository.list_active_by_workline_position(
                db,
                workline_code=workline_code,
                position_code=position_code,
            )
            for rack_code in [coerce_optional_str(getattr(placement, "rack_code", None))]
            if rack_code is not None
        }

    async def _other_operation_active_target_task_count(
        self,
        db: AsyncSession,
        *,
        operation_key: str,
        workline_code: str,
        target_position_code: str,
    ) -> int:
        active_target_tasks = await self.rack_task_repository.list_active_by_target_position(
            db,
            workline_code=workline_code,
            target_position_code=target_position_code,
        )
        return sum(
            1
            for task in active_target_tasks
            if getattr(task, "operation_key", None) != operation_key
            and _task_occupies_target_position(task, target_position_code=target_position_code)
        )

    async def _resource_projection_confirms_success(self, db: AsyncSession, tasks: list[Any]) -> bool:
        move_out_rack_codes = {
            rack_code
            for task in tasks
            if _task_type(task) == RackTaskType.MOVE_RACK.value
            for rack_code in [coerce_optional_str(getattr(task, "rack_code", None))]
            if rack_code is not None
        }
        target_tasks_by_position: dict[tuple[str, str], list[Any]] = {}

        for task in tasks:
            task_type = _task_type(task)
            if task_type == RackTaskType.MOVE_RACK.value and await self._move_out_rack_still_at_source(db, task):
                return False

            target_position_code = coerce_optional_str(getattr(task, "target_position_code", None))
            if target_position_code is None:
                if task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value:
                    return False
                continue

            workline_code = coerce_optional_str(getattr(task, "workline_code", None))
            if workline_code is None:
                return False
            target_tasks_by_position.setdefault((workline_code, target_position_code), []).append(task)

        for (workline_code, target_position_code), target_tasks in target_tasks_by_position.items():
            placements = await self.rack_placement_repository.list_active_by_workline_position(
                db,
                workline_code=workline_code,
                position_code=target_position_code,
            )
            available_placements = list(placements)
            for task in target_tasks:
                if not _consume_target_projection(task, available_placements, move_out_rack_codes=move_out_rack_codes):
                    return False
        return True

    async def _move_out_rack_still_at_source(self, db: AsyncSession, task: Any) -> bool:
        rack_code = coerce_optional_str(getattr(task, "rack_code", None))
        source_position_code = coerce_optional_str(getattr(task, "source_position_code", None))
        workline_code = coerce_optional_str(getattr(task, "workline_code", None))
        if rack_code is None or source_position_code is None or workline_code is None:
            return False
        placements = await self.rack_placement_repository.list_active_by_workline_position(
            db,
            workline_code=workline_code,
            position_code=source_position_code,
        )
        return any(coerce_optional_str(getattr(placement, "rack_code", None)) == rack_code for placement in placements)


def _ensure_existing_operation_request_consistent(
    tasks: list[Any],
    *,
    operation_key: str,
    operation_type: str,
    specs: list[RackTaskSpec],
) -> None:
    sorted_tasks = sorted(tasks, key=lambda task: task.sequence_no)
    sorted_specs = sorted(specs, key=lambda spec: spec.sequence_no)
    if len(sorted_tasks) != len(sorted_specs):
        raise ValueError("existing rack operation task count differs from request")

    for task, spec in zip(sorted_tasks, sorted_specs, strict=True):
        if getattr(task, "operation_key", None) != operation_key:
            raise ValueError("existing rack operation key differs from request")
        if getattr(task, "operation_type", None) != operation_type:
            raise ValueError("existing rack operation type differs from request")
        if (
            getattr(task, "sequence_no", None) != spec.sequence_no
            or _task_type(task) != spec.task_type
            or getattr(task, "dispatch_key", None) != spec.dispatch_key
            or getattr(task, "task_key", None) != spec.dispatch_key
            or getattr(task, "target_code", None) != spec.target_code
        ):
            raise ValueError("existing rack operation task identity differs from request")
        if _task_required(task) != spec.required:
            raise ValueError("existing rack operation required flag differs from request")
        _ensure_task_request_json_matches(task, spec)
        _ensure_task_actions_json_matches(task, spec)


def _ensure_task_spec_contract(spec: RackTaskSpec) -> None:
    if (
        spec.task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value
        and coerce_optional_str(spec.target_position_code) is None
    ):
        raise ValueError("rack operation ALLOCATE_AND_MOVE_RACK requires target_position_code")
    if (
        spec.task_type == RackTaskType.MOVE_RACK.value
        and coerce_optional_str(spec.target_position_code) is None
        and coerce_optional_str(spec.target_position_role) is None
    ):
        raise ValueError("rack operation MOVE_RACK requires target_position_code or target_position_role")


def _ensure_task_request_json_matches(task: Any, spec: RackTaskSpec) -> None:
    request_json = getattr(task, "request_json", None)
    if not isinstance(request_json, dict):
        raise TypeError("existing rack operation request_json missing")

    expected = {
        "operation_key": spec.request_json.get("operation_key"),
        "operation_type": spec.request_json.get("operation_type"),
        "sequence_no": spec.sequence_no,
        "task_type": spec.task_type,
        "rack_code": spec.rack_code,
        "rack_kind": spec.rack_kind,
        "source_position_code": spec.source_position_code,
        "target_position_code": spec.target_position_code,
        "target_position_role": spec.target_position_role,
    }

    for key, value in expected.items():
        if request_json.get(key) != value:
            raise ValueError(f"existing rack operation request_json {key} differs from request")
    if _request_json_for_idempotency(request_json) != _request_json_for_idempotency(spec.request_json):
        raise ValueError("existing rack operation request_json differs from request")


def _ensure_task_actions_json_matches(task: Any, spec: RackTaskSpec) -> None:
    actions_json = getattr(task, "actions_json", None)
    if not isinstance(actions_json, dict):
        raise TypeError("existing rack operation actions_json missing")
    if actions_json != spec.actions_json:
        raise ValueError("existing rack operation actions_json differs from request")


def _request_json_for_idempotency(payload: Mapping[str, Any]) -> dict[str, Any]:
    lifecycle_keys = {
        "timeout_seconds",
        "trace_id",
        "request_id",
        "dispatch_key",
        "callback_type",
        "source",
        "target",
        "actions",
    }
    return {key: value for key, value in payload.items() if key not in lifecycle_keys}


def _consume_target_projection(
    task: Any,
    placements: list[Any],
    *,
    move_out_rack_codes: set[str],
) -> bool:
    for index, placement in enumerate(placements):
        if _target_projection_matches_task(task, placement, move_out_rack_codes=move_out_rack_codes):
            placements.pop(index)
            return True
    return False


def _target_projection_matches_task(
    task: Any,
    placement: Any,
    *,
    move_out_rack_codes: set[str],
) -> bool:
    task_type = _task_type(task)
    task_rack_kind = coerce_optional_str(enum_value(getattr(task, "rack_kind", None)))
    task_rack_code = coerce_optional_str(getattr(task, "rack_code", None))
    if task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value and task_rack_kind is None:
        return False
    if task_type == RackTaskType.MOVE_RACK.value and task_rack_code is None:
        return False

    placement_rack_code = coerce_optional_str(getattr(placement, "rack_code", None))
    if task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value and placement_rack_code in move_out_rack_codes:
        return False
    if task_rack_code is not None and placement_rack_code != task_rack_code:
        return False
    placement_rack_kind = coerce_optional_str(enum_value(getattr(placement, "rack_kind", None)))
    return task_rack_kind is None or placement_rack_kind == task_rack_kind


def _outbox_payload(spec: RackTaskSpec) -> dict[str, Any]:
    return {
        **spec.request_json,
        "dispatch_key": spec.dispatch_key,
        "actions": spec.actions_json,
    }


def _station_dispatch_position_code(spec: RackTaskSpec) -> str | None:
    if spec.rack_kind != RackKind.SINGLE_LAYER.value:
        return None
    if spec.task_type == RackTaskType.MOVE_RACK.value:
        return spec.source_position_code
    if spec.task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value:
        return spec.target_position_code
    return None


def _released_station_position_codes(specs: list[RackTaskSpec]) -> set[str]:
    return {
        source_position_code
        for spec in specs
        for source_position_code in [coerce_optional_str(spec.source_position_code)]
        if spec.rack_kind == RackKind.SINGLE_LAYER.value
        and spec.task_type == RackTaskType.MOVE_RACK.value
        and source_position_code is not None
    }


def _allows_active_rack_bound_for_station_claim(
    spec: RackTaskSpec,
    *,
    station_position_code: str,
    released_station_position_codes: set[str],
) -> bool:
    if spec.task_type == RackTaskType.MOVE_RACK.value:
        return True
    return (
        spec.task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value
        and station_position_code in released_station_position_codes
    )


def _ensure_existing_outbox_shape(
    outbox: SystemOutbox,
    *,
    spec: RackTaskSpec,
    payload_json: dict[str, Any],
) -> None:
    if outbox.dispatch_type != SystemOutboxDispatchType.EXTERNAL_HTTP:
        raise ValueError("existing rack operation outbox dispatch_type differs from request")
    if outbox.target_type != SystemOutboxTargetType.HTTP_ENDPOINT:
        raise ValueError("existing rack operation outbox target_type differs from request")
    if outbox.target_code != spec.target_code:
        raise ValueError("existing rack operation outbox target_code differs from request")

    existing_payload = outbox.payload_json if isinstance(outbox.payload_json, dict) else {}
    for key, value in payload_json.items():
        if key in {"trace_id", "request_id", "dispatch_key", "callback_type", "source", "target", "actions"}:
            continue
        if existing_payload.get(key) != value:
            raise ValueError(f"existing rack operation outbox payload {key} differs from request")


def _ensure_existing_outbox_active(outbox: SystemOutbox) -> None:
    status = getattr(outbox, "status", None)
    active_statuses = {
        SystemOutboxStatus.NEW,
        SystemOutboxStatus.DISPATCHING,
        SystemOutboxStatus.SENT,
        SystemOutboxStatus.BLOCKED_RESOURCE,
    }
    if isinstance(status, str):
        active_values = {item.value for item in active_statuses}
        status_is_active = status in active_values
        status_is_blocked = status == SystemOutboxStatus.BLOCKED_RESOURCE.value
    else:
        status_is_active = status in active_statuses
        status_is_blocked = status == SystemOutboxStatus.BLOCKED_RESOURCE

    if not status_is_active or (getattr(outbox, "finished_at", None) is not None and not status_is_blocked):
        raise ValueError("existing rack operation outbox is no longer active")


def _is_new_outbox(outbox: SystemOutbox) -> bool:
    status = getattr(outbox, "status", None)
    if isinstance(status, str):
        return status == SystemOutboxStatus.NEW.value
    return status == SystemOutboxStatus.NEW


def _reserved_target_position_codes(specs: list[RackTaskSpec]) -> set[str]:
    return {
        target_position_code
        for spec in specs
        for target_position_code in [coerce_optional_str(spec.target_position_code)]
        if target_position_code is not None
        and _spec_occupies_target_position(spec, target_position_code=target_position_code)
    }


def _target_occupying_specs(
    specs: list[RackTaskSpec],
    *,
    target_position_code: str,
) -> list[RackTaskSpec]:
    return [spec for spec in specs if _spec_occupies_target_position(spec, target_position_code=target_position_code)]


def _spec_occupies_target_position(spec: RackTaskSpec, *, target_position_code: str) -> bool:
    if spec.target_position_code != target_position_code:
        return False
    if spec.task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value:
        return True
    return spec.task_type == RackTaskType.MOVE_RACK.value


def _task_occupies_target_position(task: Any, *, target_position_code: str) -> bool:
    task_type = _task_type(task)
    if task_type == RackTaskType.ALLOCATE_AND_MOVE_RACK.value:
        return getattr(task, "target_position_code", None) == target_position_code
    return (
        task_type == RackTaskType.MOVE_RACK.value
        and getattr(task, "target_position_code", None) == target_position_code
    )


def _task_required(task: Any) -> bool:
    actions_json = getattr(task, "actions_json", None)
    if isinstance(actions_json, dict) and "required" in actions_json:
        return bool(actions_json["required"])
    return True


def _task_status(task: Any) -> str | None:
    return enum_value(getattr(task, "task_status", None))


def _task_type(task: Any) -> str | None:
    return enum_value(getattr(task, "task_type", None))


def _required_int(value: Any, field_name: str) -> int:
    number = coerce_optional_int(value)
    if number is None:
        raise ValueError(f"{field_name} is required")
    return number


def _rack_kind(value: RackKind | str) -> RackKind:
    raw_value = enum_value(value)
    try:
        return RackKind(str(raw_value))
    except ValueError as exc:
        raise ValueError(f"unsupported rack_kind: {raw_value}") from exc


def _rack_kind_value(value: Any) -> str | None:
    if coerce_optional_str(value) is None:
        return None
    return _rack_kind(value).value


def _rack_task_type(value: Any) -> str:
    raw_value = enum_value(value)
    if raw_value in _RACK_TASK_TYPE_ALIASES:
        return _RACK_TASK_TYPE_ALIASES[raw_value]
    try:
        return RackTaskType(str(raw_value)).value
    except ValueError as exc:
        raise ValueError(f"unsupported rack task_type: {raw_value}") from exc


rack_operation_service = RackOperationService()


__all__ = [
    "DEFAULT_RACK_OPERATION_TIMEOUT_SECONDS",
    "RackOperationService",
    "RackOperationStatus",
    "RackTaskSpec",
    "rack_operation_service",
]
