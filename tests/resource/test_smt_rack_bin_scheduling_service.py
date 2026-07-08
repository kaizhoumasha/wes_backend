from __future__ import annotations

from src.app.resource.services.smt_rack_bin_scheduling_service import SmtRackBinSchedulingService


def test_rack_slot_numeric_alias_maps_to_c_slot() -> None:
    service = SmtRackBinSchedulingService()

    assert service._canonical_rack_slot_code(service.RACK_SLOT_C_NUMERIC_ALIAS) == "C"
