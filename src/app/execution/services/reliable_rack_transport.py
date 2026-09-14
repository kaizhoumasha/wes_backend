"""从冻结业务意图可靠创建货架 TransportTask。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from wes_plugin_sdk import (
    TransportRackMovePosition,
    TransportRackPosition,
    TransportRackReference,
    TransportRcsTemplateId,
    TransportZonePosition,
)

from src.app.execution.models import TransportDecisionBinding
from src.app.execution.repositories import transport_decision_binding_repository
from src.app.transport.contracts import (
    BinMove,
    RackMovePosition,
    RackPosition,
    RackReference,
    RcsTemplateId,
    TransportCaller,
    TransportExecutionAuthority,
    ZonePosition,
)
from src.core.uuid7 import new_uuid7

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class RackTransportIntent(Protocol):
    @property
    def rack_id(self) -> str: ...

    @property
    def source(self) -> TransportRackMovePosition: ...

    @property
    def target(self) -> TransportRackMovePosition: ...

    @property
    def target_face(self) -> str: ...

    @property
    def rcs_template_id(self) -> TransportRcsTemplateId: ...


class TransportBindingRepositoryPort(Protocol):
    async def lock_decision_identity(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        correlation_id: str,
        step: str,
    ) -> None: ...

    async def get_by_decision_identity_for_update(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        correlation_id: str,
        step: str,
    ) -> TransportDecisionBinding | None: ...

    async def add(self, db: AsyncSession, binding: TransportDecisionBinding) -> TransportDecisionBinding: ...


class TransportServicePort(Protocol):
    async def move_rack_in_session(
        self,
        db: AsyncSession,
        client_request_id: str,
        caller: TransportCaller,
        rack_id: str,
        source: RackMovePosition,
        target: RackMovePosition,
        target_face: str | None = None,
        rcs_template_id: RcsTemplateId = RcsTemplateId.F01,
        *,
        execution_authority: TransportExecutionAuthority,
    ) -> object: ...


class BinTransportServicePort(Protocol):
    async def move_bins_in_session(
        self,
        db: AsyncSession,
        client_request_id: str,
        caller: TransportCaller,
        moves: tuple[BinMove, ...],
        *,
        execution_authority: TransportExecutionAuthority,
    ) -> object: ...


async def _binding_for(
    db: AsyncSession,
    repository: TransportBindingRepositoryPort,
    uuid_factory: Any,
    *,
    workline_id: int,
    source_evidence_id: int,
    correlation_id: str,
    step: str,
    resource_fence_id: str,
) -> TransportDecisionBinding:
    await repository.lock_decision_identity(db, workline_id=workline_id, correlation_id=correlation_id, step=step)
    binding = await repository.get_by_decision_identity_for_update(
        db, workline_id=workline_id, correlation_id=correlation_id, step=step
    )
    if binding is None:
        return await repository.add(
            db,
            TransportDecisionBinding(
                correlation_id=correlation_id,
                step=step,
                workline_id=workline_id,
                resource_fence_id=resource_fence_id,
                client_request_id=uuid_factory(),
                source_evidence_id=source_evidence_id,
            ),
        )
    if (
        binding.workline_id != workline_id
        or binding.resource_fence_id != resource_fence_id
        or binding.source_evidence_id != source_evidence_id
    ):
        raise ValueError("existing transport decision binding conflict")
    return binding


class ReliableRackTransportCreator:
    """复用中立 binding，把同一业务步骤稳定映射为一个 Transport client identity。"""

    def __init__(
        self,
        transport_service: TransportServicePort,
        *,
        binding_repository: TransportBindingRepositoryPort | None = None,
        uuid_factory: Any = new_uuid7,
    ) -> None:
        self._transport = transport_service
        self._bindings = binding_repository or transport_decision_binding_repository
        self._uuid_factory = uuid_factory

    async def create(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        source_evidence_id: int,
        correlation_id: str,
        step: str,
        resource_fence_id: str,
        intent: RackTransportIntent,
    ) -> object:
        binding = await _binding_for(
            db,
            self._bindings,
            self._uuid_factory,
            workline_id=workline_id,
            source_evidence_id=source_evidence_id,
            correlation_id=correlation_id,
            step=step,
            resource_fence_id=resource_fence_id,
        )
        return await self._transport.move_rack_in_session(
            db,
            client_request_id=binding.client_request_id,
            caller=TransportCaller(workline_id=str(workline_id)),
            rack_id=intent.rack_id,
            source=self._convert_position(intent.source),
            target=self._convert_position(intent.target),
            target_face=intent.target_face,
            rcs_template_id=RcsTemplateId(intent.rcs_template_id.value),
            execution_authority=TransportExecutionAuthority(workline_id=workline_id),
        )

    @staticmethod
    def _convert_position(position: TransportRackMovePosition) -> RackMovePosition:
        if type(position) is TransportRackReference:
            return RackReference(position.location_code)
        if type(position) is TransportZonePosition:
            return ZonePosition(position.location_code)
        if type(position) is TransportRackPosition:
            return RackPosition(position.location_code)
        raise TypeError("unsupported Transport rack position")


class ReliableBinTransportCreator:
    """把冻结的 Bin 批次稳定映射到原 Transport client identity。"""

    def __init__(
        self,
        transport_service: BinTransportServicePort,
        *,
        binding_repository: TransportBindingRepositoryPort | None = None,
        uuid_factory: Any = new_uuid7,
    ) -> None:
        self._transport = transport_service
        self._bindings = binding_repository or transport_decision_binding_repository
        self._uuid_factory = uuid_factory

    async def create(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        source_evidence_id: int,
        correlation_id: str,
        step: str,
        resource_fence_id: str,
        moves: tuple[BinMove, ...],
    ) -> object:
        binding = await _binding_for(
            db,
            self._bindings,
            self._uuid_factory,
            workline_id=workline_id,
            source_evidence_id=source_evidence_id,
            correlation_id=correlation_id,
            step=step,
            resource_fence_id=resource_fence_id,
        )
        return await self._transport.move_bins_in_session(
            db,
            binding.client_request_id,
            TransportCaller(workline_id=str(workline_id)),
            moves,
            execution_authority=TransportExecutionAuthority(workline_id=workline_id),
        )


__all__ = ["RackTransportIntent", "ReliableBinTransportCreator", "ReliableRackTransportCreator", "TransportServicePort"]
