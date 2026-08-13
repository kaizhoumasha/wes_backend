"""DeviceCommand schema cutover 必须恢复仍由目标模型拥有的命令外键。"""

from pathlib import Path


MIGRATION = Path("migrations/versions/20260813_1016_a08d72f135d2_rebuild_device_command_ecs_lifecycle.py")


def test_cutover_clears_retired_ids_and_recreates_live_command_foreign_keys() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'UPDATE "wes_biz"."runtime_holds" SET "source_command_id" = NULL' in source
    assert 'UPDATE "wes_biz"."ng_return_items" SET "source_command_id" = NULL' in source
    assert 'UPDATE "wes_biz"."workline_timelines" SET "related_command_id" = NULL' in source
    assert '"fk_runtime_holds_source_command_id"' in source
    assert '"fk_ng_return_items_source_command_id"' in source
    assert '"fk_workline_timelines_related_command_id"' in source


def test_status_observation_matches_uniform_wire_command_code_width() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'sa.Column("current_command_code", sa.String(length=160), nullable=True)' in source
