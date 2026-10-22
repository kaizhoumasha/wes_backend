import importlib


def test_automatic_plugin_declaration_has_independent_identity_and_approved_roles():
    d = importlib.import_module("automatic_picking.definition").DEFINITION
    assert (d.plugin_key, d.plugin_version, d.supported_line_types) == ("automatic-picking", "0.1.0", ("AUTO",))
    assert {r.role_key for r in d.device_roles} == {"SCAN1", "SCAN2", "SCAN3", "SCAN4", "SOURCE_ARM", "TARGET_ARM"}
    assert {p.slot_key for p in d.position_slots if p.required} == {
        "FIVE_RACK",
        "RETURN_RACK",
        "TRANSFER_RACK",
        "INLET",
        "OUTLET",
    }
    assert {p.slot_key for p in d.position_slots if not p.required} == {"WORK_BIN", "SCAN_PLATFORM"}


def test_prepare_selects_only_frozen_automatic_identity():
    from automatic_picking.prepare_policy import AutomaticPickingPreparePolicy
    from wes_plugin_sdk.prepare_policy import PrepareContext, PrepareTaskType

    p = AutomaticPickingPreparePolicy()
    assert p.select_task_type(PrepareContext(True, "AUTO", "AUTO", "automatic-picking", None)) is PrepareTaskType.AUTO
    assert p.select_task_type(PrepareContext(True, "MANUAL", "AUTO", "manual-picking", None)) is None


def test_arm_positions_use_existing_configuration_and_start_resolution_without_site_defaults():
    from automatic_picking.definition import DEFINITION

    from src.app.workline.installed_plugin import parse_position_bindings, resolve_position_bindings
    from src.app.workline.models.workline import WorkLinePositionInput

    required = {slot.slot_key: "position-" + slot.slot_key for slot in DEFINITION.position_slots if slot.required}
    assert parse_position_bindings({"position_bindings": required}, DEFINITION.position_slots) == required
    arm_slots = tuple(slot for slot in DEFINITION.position_slots if not slot.required)
    bindings = {slot.slot_key: "position-" + slot.slot_key for slot in arm_slots}
    positions = tuple(
        WorkLinePositionInput(
            position_code=bindings[slot.slot_key],
            position_name=slot.display_name,
            position_type=slot.position_type,
            allowed_rack_kind=slot.allowed_rack_kind,
            logic_location_code="execution-" + slot.slot_key,
        )
        for slot in arm_slots
    )
    resolved = resolve_position_bindings(
        {"position_bindings": bindings}, DEFINITION.position_slots, positions, require_complete=False
    )
    arms = {
        row.position_role: (row.location_type, row.location_id)
        for row in resolved
        if row.position_role in ("WORK_BIN", "SCAN_PLATFORM")
    }
    assert arms == {
        "WORK_BIN": ("BIN", "execution-WORK_BIN"),
        "SCAN_PLATFORM": ("SCAN_PLATFORM", "execution-SCAN_PLATFORM"),
    }
