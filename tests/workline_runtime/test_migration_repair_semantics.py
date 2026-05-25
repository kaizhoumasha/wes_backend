from pathlib import Path


def test_failed_outbox_repair_does_not_treat_pending_as_device_occupancy() -> None:
    migration = Path("migrations/versions/20260425_1515_f7a8b9c0d1e2_repair_failed_outbox_device_commands.py")
    migration_text = migration.read_text(encoding="utf-8")
    release_device_sql = migration_text.split("UPDATE wes_biz.devices AS d", maxsplit=1)[1]

    assert "active_dc.status IN ('SENT', 'ACK_RECEIVED')" in release_device_sql
    assert "active_dc.status IN ('PENDING', 'SENT', 'ACK_RECEIVED')" not in release_device_sql


def test_system_outbox_rack_domain_migration_is_explicitly_destructive_for_legacy_runtime_rows() -> None:
    migration = Path("migrations/versions/20260525_1239_3cf0dc588be9_system_outbox_and_rack_operation_domain.py")
    migration_text = migration.read_text(encoding="utf-8")
    normalized_text = " ".join(migration_text.split())

    assert "DELETE FROM wes_biz.workline_rack_tasks" in migration_text
    assert "DELETE FROM wes_biz.workline_dispatch_attempts" in migration_text
    assert "UPDATE wes_biz.workline_diagnostics SET outbox_id = NULL" in migration_text
    assert "UPDATE wes_biz.runtime_holds SET source_outbox_id = NULL" in migration_text
    assert "UPDATE wes_biz.workline_sessions SET reconciliation_source_outbox_id = NULL" in normalized_text
    assert "FROM wes_biz.workline_outbox" not in migration_text
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
