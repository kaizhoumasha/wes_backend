from pathlib import Path


def test_failed_outbox_repair_does_not_treat_pending_as_device_occupancy() -> None:
    migration = Path("migrations/versions/20260425_1515_f7a8b9c0d1e2_repair_failed_outbox_device_commands.py")
    migration_text = migration.read_text(encoding="utf-8")

    assert "superseded by system outbox consolidation" in migration_text
    assert "UPDATE wes_biz.devices AS d" not in migration_text


def test_system_outbox_rack_domain_migration_is_explicitly_destructive_for_legacy_runtime_rows() -> None:
    migration = Path("migrations/versions/20260525_1239_3cf0dc588be9_system_outbox_and_rack_operation_domain.py")
    migration_text = migration.read_text(encoding="utf-8")
    normalized_text = " ".join(migration_text.split())

    assert "DELETE FROM wes_biz.workline_dispatch_attempts" in migration_text
    assert "UPDATE wes_biz.workline_diagnostics SET outbox_id = NULL" in migration_text
    assert "UPDATE wes_biz.runtime_holds SET source_outbox_id = NULL" in migration_text
    assert "UPDATE wes_biz.workline_sessions SET reconciliation_source_outbox_id = NULL" in normalized_text
    assert "INSERT INTO wes_biz.rack_tasks" not in migration_text


def test_system_outbox_rack_domain_migration_does_not_use_bind_parameters_inside_do_block() -> None:
    migration = Path("migrations/versions/20260525_1239_3cf0dc588be9_system_outbox_and_rack_operation_domain.py")
    migration_text = migration.read_text(encoding="utf-8")
    drop_constraint_helper = migration_text.split("def _drop_constraint_if_exists", maxsplit=1)[1].split(
        "def _drop_index_if_exists", maxsplit=1
    )[0]

    assert "DO $$" not in drop_constraint_helper
    assert ".bindparams(" in drop_constraint_helper
    assert "op.drop_constraint" in drop_constraint_helper


def test_handling_core_migration_creates_final_system_outbox_contract_directly() -> None:
    migration = Path("migrations/versions/20260522_1449_745068e173c2_add_handling_core.py")
    migration_text = migration.read_text(encoding="utf-8")
    system_outbox_block = migration_text.split('op.create_table(\n        "system_outbox"', maxsplit=1)[1].split(
        'op.create_table(\n        "handling_operation_steps"', maxsplit=1
    )[0]

    assert '"operation_id"' not in system_outbox_block
    assert '"operation_domain"' in system_outbox_block
    assert '"operation_key"' in system_outbox_block
    assert '"device_id"' in system_outbox_block
    assert '"blocked_by_runtime_hold_id"' in system_outbox_block
    assert "ix_system_outbox_domain_operation" in system_outbox_block
    assert "ix_system_outbox_operation_status" not in system_outbox_block


def test_system_outbox_rack_domain_migration_does_not_rewrite_system_outbox_shape() -> None:
    migration = Path("migrations/versions/20260525_1239_3cf0dc588be9_system_outbox_and_rack_operation_domain.py")
    migration_text = migration.read_text(encoding="utf-8")
    upgrade_body = migration_text.split("def upgrade() -> None:", maxsplit=1)[1].split(
        "def downgrade() -> None:", maxsplit=1
    )[0]

    assert 'op.add_column(\n        "system_outbox"' not in upgrade_body
    assert 'op.drop_column("system_outbox", "operation_id"' not in upgrade_body
    assert "ix_system_outbox_operation_status" not in upgrade_body


def test_completion_policy_migration_only_backfills_canonical_full_box_policy() -> None:
    migration = Path("migrations/versions/20260526_1544_c5d469c98d89_set_handling_full_box_completion_policy.py")
    migration_text = migration.read_text(encoding="utf-8")
    upgrade_body = migration_text.split("def upgrade() -> None:", maxsplit=1)[1].split(
        "def downgrade() -> None:", maxsplit=1
    )[0]

    assert "_ensure_" not in migration_text
    assert "_backfill_legacy" not in migration_text
    assert "system_outbox" not in migration_text
    assert "operation_id" not in migration_text
    assert "UPDATE wes_biz.handling_operations" in upgrade_body
    assert "LIKE '%FULL_BOX_EXCHANGE%'" in migration_text
    assert "LIKE '%FULL_BIN_EXCHANGE%'" not in migration_text
    assert "LIKE '%RACK_BIN_EXCHANGE%'" not in migration_text
