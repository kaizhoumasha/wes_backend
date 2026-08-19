"""粗分机业务 E2E fixture 只提供静态主数据与可信 STOPPED 投影。"""

from pathlib import Path


def test_business_loop_seed_requires_public_start_to_create_epoch() -> None:
    source = Path("workline_plugins/rough_sorter/fixtures/business-loop-seed.sql").read_text(encoding="utf-8")

    assert "INSERT INTO wes_biz.work_lines" in source
    assert "__ROUGH_SORTER_CONFIG__" in source
    assert "__ECS_ENDPOINT__" in source
    assert "INSERT INTO wes_runtime.workline_runtime_status_projections" in source
    assert "'STOPPED'" in source
    assert "INSERT INTO wes_biz.line_run_epochs" not in source
    assert "INSERT INTO wes_biz.line_run_epoch_device_bindings" not in source
    assert "INSERT INTO wes_biz.line_run_epoch_position_bindings" not in source
