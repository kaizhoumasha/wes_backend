from pathlib import Path


def test_material_mount_stack_position_migration_matches_model_single_column_index() -> None:
    """cell_stack_position 字段 index=True 时，迁移必须同步创建/删除单列索引。"""

    migration_text = Path(
        "migrations/versions/20260519_1431_b4685be483de_add_material_mount_stack_position.py"
    ).read_text(encoding="utf-8")

    add_column_index = migration_text.index('"cell_stack_position"')
    create_single_index = migration_text.index('"ix_wes_biz_resource_bin_material_mounts_cell_stack_position"')
    create_stack_unique_index = migration_text.index('"ux_resource_bin_material_mounts_active_stack_position"')
    drop_single_index = migration_text.index(
        '"ix_wes_biz_resource_bin_material_mounts_cell_stack_position"', create_stack_unique_index
    )
    drop_column_index = migration_text.index('op.drop_column("resource_bin_material_mounts", "cell_stack_position"')

    assert add_column_index < create_single_index < create_stack_unique_index
    assert create_stack_unique_index < drop_single_index < drop_column_index
