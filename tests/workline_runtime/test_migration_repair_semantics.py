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


def test_system_outbox_deployed_schema_repair_adds_missing_final_contract_without_dropping_legacy_column() -> None:
    migration = Path("migrations/versions/20260530_0144_86b2d22f0103_repair_system_outbox_deployed_schema_.py")
    migration_text = migration.read_text(encoding="utf-8")
    upgrade_body = migration_text.split("def upgrade() -> None:", maxsplit=1)[1].split(
        "def downgrade() -> None:", maxsplit=1
    )[0]

    assert "_column_exists" in migration_text
    assert "_ensure_system_outbox_columns()" in upgrade_body
    assert "device_id" in migration_text
    assert "operation_domain" in migration_text
    assert "blocked_by_runtime_hold_id" in migration_text
    assert "ix_system_outbox_device_fifo" in migration_text
    assert "fk_system_outbox_device_id" in migration_text
    assert "op.drop_column" not in upgrade_body
    assert "operation_id" not in upgrade_body


def test_completion_policy_migration_only_backfills_canonical_full_box_policy() -> None:
    migration = Path("migrations/versions/20260526_1544_c5d469c98d89_set_handling_full_box_completion_policy.py")
    migration_text = migration.read_text(encoding="utf-8")
    upgrade_body = migration_text.split("def upgrade() -> None:", maxsplit=1)[1].split(
        "def downgrade() -> None:", maxsplit=1
    )[0]

    assert "_backfill_legacy" not in migration_text
    assert "system_outbox" not in migration_text
    assert "operation_id" not in migration_text
    assert "_ensure_completion_policy_column()" in upgrade_body
    assert "op.drop_column" not in migration_text
    assert "ix_wes_biz_handling_operations_completion_policy" in migration_text
    assert "UPDATE wes_biz.handling_operations" in upgrade_body
    assert "LIKE '%FULL_BOX_EXCHANGE%'" in migration_text
    assert "LIKE '%FULL_BIN_EXCHANGE%'" not in migration_text
    assert "LIKE '%RACK_BIN_EXCHANGE%'" not in migration_text


def test_completion_policy_migration_detects_existing_named_check_constraint() -> None:
    migration = Path("migrations/versions/20260526_1544_c5d469c98d89_set_handling_full_box_completion_policy.py")
    migration_text = migration.read_text(encoding="utf-8")

    assert "ck_handling_operations_operationcompletionpolicy" in migration_text
    assert "COMPLETION_POLICY_CONSTRAINT_NAMES" in migration_text
    assert "any(_constraint_exists(constraint_name) for constraint_name in COMPLETION_POLICY_CONSTRAINT_NAMES)" in (
        migration_text
    )


def test_workline_inbox_hot_queue_index_migration_uses_transactional_indexes() -> None:
    migration = Path("migrations/versions/20260527_1434_a6c2c77adabd_add_workline_inbox_hot_queue_indexes.py")
    migration_text = migration.read_text(encoding="utf-8")

    assert "autocommit_block" not in migration_text
    assert "postgresql_concurrently=True" not in migration_text
    assert "if_not_exists=True" in migration_text
    assert "if_exists=True" in migration_text


def test_test_deploy_pipeline_syncs_workline_and_device_seed_data() -> None:
    pipeline = Path("Jenkinsfile.test-deploy").read_text(encoding="utf-8")

    assert "sync_test_workline_devices.py" in pipeline
    assert pipeline.index("sync_test_workline_devices.py") < pipeline.index("sync_permissions.py")


def test_workline_activation_default_migration_preserves_existing_active_rows() -> None:
    migration = Path("migrations/versions/20260529_1053_c1ea657cb2d7_workline_activation_state_default.py")
    migration_text = migration.read_text(encoding="utf-8")
    upgrade_body = migration_text.split("def upgrade() -> None:", maxsplit=1)[1].split(
        "def downgrade() -> None:", maxsplit=1
    )[0]
    normalized_upgrade = " ".join(upgrade_body.split())

    assert "server_default=sa.false()" in upgrade_body
    assert "SET is_active = false WHERE is_active = true" not in normalized_upgrade
    assert "SET is_active = false WHERE is_active IS NULL OR is_active = true" not in normalized_upgrade


def test_workline_activation_default_migration_downgrade_restores_previous_server_default() -> None:
    migration = Path("migrations/versions/20260529_1053_c1ea657cb2d7_workline_activation_state_default.py")
    migration_text = migration.read_text(encoding="utf-8")
    downgrade_body = migration_text.split("def downgrade() -> None:", maxsplit=1)[1]

    assert "server_default=None" in downgrade_body
    assert "server_default=sa.true()" not in downgrade_body
