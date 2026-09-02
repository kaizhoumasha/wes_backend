"""插件 Transport Decision 只映射到稳定的 Transport client identity。"""

from src.app.execution import models as execution_models


def test_execution_exports_neutral_transport_decision_binding() -> None:
    assert hasattr(execution_models, "TransportDecisionBinding")
    assert not hasattr(execution_models, "RackReplacementTransportBinding")


def test_binding_identity_uses_only_neutral_decision_fields() -> None:
    binding_type = execution_models.TransportDecisionBinding
    binding = binding_type(
        correlation_id="operation-001",
        step="PRIMARY_MOVE",
        line_run_epoch_id=11,
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
        "fk_transport_decision_bindings_epoch",
        "ux_transport_decision_bindings_decision_identity",
        "ux_transport_decision_bindings_client_request_id",
    }
    assert [column.name for column in constraints["ux_transport_decision_bindings_decision_identity"].columns] == [
        "line_run_epoch_id",
        "correlation_id",
        "step",
    ]
    assert "ux_transport_decision_bindings_epoch_resource_step" not in constraints
    assert {index.name for index in table.indexes} >= {
        "ix_wes_biz_transport_decision_bindings_epoch_resource",
    }
    assert all("OLD_OUT" not in str(getattr(constraint, "sqltext", "")) for constraint in table.constraints)


def test_batch_reconciliation_binding_is_not_exported() -> None:
    assert not hasattr(execution_models, "InboundEvidenceExecutionBinding")
