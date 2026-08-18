"""换架业务身份只负责映射到 Transport 的 UUIDv7 client identity。"""

from src.app.execution import models as execution_models


def test_execution_exports_narrow_rack_replacement_transport_binding() -> None:
    assert hasattr(execution_models, "RackReplacementTransportBinding")


def test_binding_identity_is_only_replacement_and_leg() -> None:
    binding_type = execution_models.RackReplacementTransportBinding
    binding = binding_type(
        rack_replacement_id="REPLACE-001",
        leg="OLD_OUT",
        line_run_epoch_id=11,
        current_rack_id="RACK-OLD",
        client_request_id="019cd8ce-34b7-7000-8000-000000000001",
        source_evidence_id=31,
    )

    assert binding.business_identity == ("REPLACE-001", "OLD_OUT")
    assert binding.rack_fence_identity == (11, "RACK-OLD")
    assert "request_payload" not in binding_type.model_fields
    assert "status" not in binding_type.model_fields


def test_binding_metadata_installs_named_epoch_rack_fence_constraints() -> None:
    table = execution_models.RackReplacementTransportBinding.__table__

    assert {constraint.name for constraint in table.constraints} >= {
        "fk_rack_replacement_transport_bindings_epoch",
        "ux_rack_replacement_transport_bindings_epoch_rack_leg",
    }
    assert {index.name for index in table.indexes} >= {
        "ix_wes_biz_rack_replacement_transport_bindings_epoch_rack",
    }


def test_batch_reconciliation_binding_is_not_exported() -> None:
    assert not hasattr(execution_models, "InboundEvidenceExecutionBinding")
