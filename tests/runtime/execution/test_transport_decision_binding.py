"""插件 Transport Decision 只映射到稳定的 Transport client identity。"""

import pytest
import wes_plugin_sdk as sdk

from src.app.execution import models as execution_models
from src.app.execution.services.reliable_rack_transport import ReliableRackTransportCreator
from src.app.transport.contracts import RackPosition, RackReference, RcsTemplateId, ZonePosition


def test_execution_exports_neutral_transport_decision_binding() -> None:
    assert hasattr(execution_models, "TransportDecisionBinding")
    assert not hasattr(execution_models, "RackReplacementTransportBinding")


def test_binding_identity_uses_only_neutral_decision_fields() -> None:
    binding_type = execution_models.TransportDecisionBinding
    binding = binding_type(
        correlation_id="operation-001",
        step="PRIMARY_MOVE",
        workline_id=11,
        resource_fence_id="resource-001",
        client_request_id="019cd8ce-34b7-7000-8000-000000000001",
        source_evidence_id=31,
    )

    assert binding.decision_identity == (11, "operation-001", "PRIMARY_MOVE")
    assert binding.resource_fence_identity == (11, "resource-001")
    assert "request_payload" not in binding_type.model_fields
    assert "status" not in binding_type.model_fields
    assert {"rack_replacement_id", "leg", "current_rack_id"}.isdisjoint(binding_type.model_fields)


def test_binding_metadata_scopes_decision_identity_without_business_cardinality() -> None:
    table = execution_models.TransportDecisionBinding.__table__
    constraints = {constraint.name: constraint for constraint in table.constraints}

    assert set(constraints) >= {
        "fk_transport_decision_bindings_workline",
        "ux_transport_decision_bindings_decision_identity",
        "ux_transport_decision_bindings_client_request_id",
    }
    assert [column.name for column in constraints["ux_transport_decision_bindings_decision_identity"].columns] == [
        "workline_id",
        "correlation_id",
        "step",
    ]
    assert "ux_transport_decision_bindings_workline_resource_step" not in constraints
    assert {index.name for index in table.indexes} >= {
        "ix_wes_biz_transport_decision_bindings_workline_resource",
    }
    assert all("OLD_OUT" not in str(getattr(constraint, "sqltext", "")) for constraint in table.constraints)


def test_batch_reconciliation_binding_is_not_exported() -> None:
    assert not hasattr(execution_models, "InboundEvidenceExecutionBinding")


@pytest.mark.asyncio
async def test_rotate_and_departure_reuse_one_bound_client_identity_per_decision() -> None:
    class Bindings:
        def __init__(self) -> None:
            self.rows = {}

        async def lock_decision_identity(self, _db, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        async def get_by_decision_identity_for_update(self, _db, **kwargs):  # type: ignore[no-untyped-def]
            return self.rows.get((kwargs["workline_id"], kwargs["correlation_id"], kwargs["step"]))

        async def add(self, _db, binding):  # type: ignore[no-untyped-def]
            self.rows[binding.decision_identity] = binding
            return binding

    class Transport:
        def __init__(self) -> None:
            self.rotates = []
            self.moves = []

        async def rotate_rack_in_session(self, _db, **kwargs):  # type: ignore[no-untyped-def]
            self.rotates.append(kwargs)

        async def move_rack_in_session(self, _db, **kwargs):  # type: ignore[no-untyped-def]
            self.moves.append(kwargs)

    binding_repo, transport = Bindings(), Transport()
    ids = iter(("rotate-request", "source-return-request", "transfer-return-request", "transfer-position-request"))
    creator = ReliableRackTransportCreator(transport, binding_repository=binding_repo, uuid_factory=lambda: next(ids))
    rotate = {
        "workline_id": 7,
        "source_evidence_id": 51,
        "correlation_id": "pt:31:source-face:12",
        "step": "MANUAL_PICKING_SOURCE_RACK_ROTATE",
        "rack_id": "R1",
        "position": sdk.TransportRackPosition("FIVE-POS"),
        "target_face": "270",
    }
    await creator.create_rotate(object(), **rotate)
    await creator.create_rotate(object(), **rotate)
    assert [call["client_request_id"] for call in transport.rotates] == ["rotate-request", "rotate-request"]
    assert all(call["position"] == RackPosition("FIVE-POS") for call in transport.rotates)
    assert all(call["rcs_template_id"] == RcsTemplateId.CTU02 for call in transport.rotates)

    source_return = {
        "workline_id": 7,
        "source_evidence_id": 51,
        "correlation_id": "pt:31:source-out:R1",
        "step": "MANUAL_PICKING_SOURCE_RACK_OUT",
        "rack_id": "R1",
        "destination": sdk.TransportZonePosition("WH01"),
    }
    await creator.create_source_return(object(), **source_return)
    await creator.create_source_return(object(), **source_return)
    transfer_return = {
        "workline_id": 7,
        "source_evidence_id": 71,
        "operation_id": "departure-op",
        "step": "MANUAL_PICKING_TRANSFER_RACK_OUT",
        "rack_id": "TARGET",
        "destination": sdk.TransportZonePosition("WH05"),
    }
    await creator.create_transfer_departure(object(), **transfer_return)
    await creator.create_transfer_departure(object(), **transfer_return)
    await creator.create_transfer_departure(
        object(),
        **{
            **transfer_return,
            "source_evidence_id": 72,
            "operation_id": "departure-position-op",
            "destination": sdk.TransportRackPosition("STORE-POS"),
        },
    )
    assert [call["client_request_id"] for call in transport.moves] == [
        "source-return-request",
        "source-return-request",
        "transfer-return-request",
        "transfer-return-request",
        "transfer-position-request",
    ]
    assert all(
        call["source"] == RackReference("R1") and call["target"] == ZonePosition("WH01") for call in transport.moves[:2]
    )
    assert all(
        call["rcs_template_id"] == RcsTemplateId.CTU03 and call["target_face"] is None for call in transport.moves[:2]
    )
    assert all(
        call["rcs_template_id"] == RcsTemplateId.F01 and call["target"] == ZonePosition("WH05")
        for call in transport.moves[2:4]
    )
    assert transport.moves[-1]["rcs_template_id"] == RcsTemplateId.F01
    assert transport.moves[-1]["target"] == RackPosition("STORE-POS")
