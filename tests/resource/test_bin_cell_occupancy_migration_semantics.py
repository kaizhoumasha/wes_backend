from pathlib import Path


def test_bin_cell_occupancy_migration_backfills_existing_active_mounts_before_runtime_switch() -> None:
    """新增聚合表迁移必须把旧 active 明细回填成可查询的格位聚合。"""

    migration_text = Path(
        "migrations/versions/20260519_1417_286ddc5bc27d_add_bin_cell_occupancy_aggregate.py"
    ).read_text(encoding="utf-8")

    create_table_index = migration_text.index('op.create_table(\n        "resource_bin_cell_occupancies"')
    occupancy_index = migration_text.index('"ux_resource_bin_cell_occupancies_active_cell"')
    add_mount_column_index = migration_text.index(
        '"resource_bin_material_mounts",\n        sa.Column("bin_cell_occupancy_id"'
    )
    backfill_insert_index = migration_text.index('INSERT INTO "wes_biz"."resource_bin_cell_occupancies"')
    backfill_update_index = migration_text.index('UPDATE "wes_biz"."resource_bin_material_mounts" AS mounts')

    assert create_table_index < add_mount_column_index < backfill_insert_index < backfill_update_index
    assert backfill_update_index < occupancy_index
    assert "WHERE mounts.ended_at IS NULL AND mounts.bin_cell_occupancy_id IS NULL" in migration_text
    assert "COUNT(*)::integer AS reel_count" in migration_text
    assert "SUM(COALESCE" in migration_text and "reel_thickness" in migration_text


def test_bin_cell_occupancy_migration_normalizes_legacy_material_identity_keys_before_backfill() -> None:
    """旧 MAT:HHPN:DateCode:LotCode 必须先归一成 5 段 key，再生成 occupancy。"""

    migration_text = Path(
        "migrations/versions/20260519_1417_286ddc5bc27d_add_bin_cell_occupancy_aggregate.py"
    ).read_text(encoding="utf-8")

    normalize_index = migration_text.index('UPDATE "wes_biz"."resource_bin_material_mounts"')
    backfill_insert_index = migration_text.index('INSERT INTO "wes_biz"."resource_bin_cell_occupancies"')

    assert normalize_index < backfill_insert_index
    assert "regexp_split_to_array(material_identity_key, ':')" in migration_text
    assert "'MAT:' || split_part(material_identity_key, ':', 2) || '::'" in migration_text


def test_bin_cell_occupancy_migration_downgrade_blocks_stacked_active_mounts_before_old_unique_indexes() -> None:
    """存在同格或同 identity 堆叠时，downgrade 应给出显式不可逆错误。"""

    migration_text = Path(
        "migrations/versions/20260519_1417_286ddc5bc27d_add_bin_cell_occupancy_aggregate.py"
    ).read_text(encoding="utf-8")

    guard_index = migration_text.index("Cannot downgrade resource_bin_cell_occupancies")
    recreate_identity_index = migration_text.index(
        'op.create_index(\n        "ux_resource_bin_material_mounts_active_material_identity"',
        guard_index,
    )
    recreate_cell_index = migration_text.index(
        'op.create_index(\n        "ux_resource_bin_material_mounts_active_cell"',
        recreate_identity_index,
    )

    assert guard_index < recreate_identity_index < recreate_cell_index
    assert "GROUP BY material_identity_key" in migration_text
    assert "GROUP BY bin_code, bin_cell_index" in migration_text
    assert "HAVING COUNT(*) > 1" in migration_text
