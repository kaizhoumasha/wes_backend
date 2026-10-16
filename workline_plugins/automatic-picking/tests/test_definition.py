import importlib


def test_automatic_plugin_declaration_has_independent_identity_and_approved_roles():
    d = importlib.import_module("automatic_picking.definition").DEFINITION
    assert (d.plugin_key, d.plugin_version, d.supported_line_types) == ("automatic-picking", "0.1.0", ("AUTO",))
    assert {r.role_key for r in d.device_roles} == {"SCAN1", "SCAN2", "SCAN3", "SCAN4", "SOURCE_ARM", "TARGET_ARM"}
    assert {p.slot_key for p in d.position_slots} == {"FIVE_RACK", "RETURN_RACK", "TRANSFER_RACK", "INLET", "OUTLET"}
    assert all(p.required for p in d.position_slots)


def test_prepare_selects_only_frozen_automatic_identity():
    from automatic_picking.prepare_policy import AutomaticPickingPreparePolicy
    from wes_plugin_sdk.prepare_policy import PrepareContext, PrepareTaskType

    p = AutomaticPickingPreparePolicy()
    assert p.select_task_type(PrepareContext(True, "AUTO", "AUTO", "automatic-picking", None)) is PrepareTaskType.AUTO
    assert p.select_task_type(PrepareContext(True, "MANUAL", "AUTO", "manual-picking", None)) is None
