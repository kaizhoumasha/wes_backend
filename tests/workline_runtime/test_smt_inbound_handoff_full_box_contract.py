from types import SimpleNamespace

from src.app.runtime.orchestration.services.intent.smt_inbound_handoff_service import SmtInboundHandoffService


def test_full_box_exchange_move_preserves_rack_release_identity() -> None:
    service = SmtInboundHandoffService()
    demand = SimpleNamespace(rack_release_id="release-001", single_layer_rack_code="RACK-001")

    moves = service._full_box_exchange_moves(
        demand=demand,
        snapshots=[{"usage": 0.9, "rack_slot_code": "A", "bin_code": "BIN-001"}],
    )

    assert moves == [
        {
            "source_type": "RACK_SLOT",
            "source_code": "RACK-001:A",
            "target_type": "FULL_BOX_EXCHANGE_BUFFER",
            "target_code": "SMT_FULL_BOX_EXCHANGE",
            "rack_release_id": "release-001",
            "rack_code": "RACK-001",
            "rack_slot_code": "A",
            "bin_code": "BIN-001",
            "required": True,
        }
    ]
