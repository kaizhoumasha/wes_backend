"""生产基础数据脚本不得重新引入已退役业务执行配置。"""

from pathlib import Path


def test_production_base_data_does_not_seed_retired_rough_sorter_execution() -> None:
    source = Path("scripts/data/init_production_base_data.sql").read_text(encoding="utf-8")

    for retired_token in (
        "WL-ROUGH-SORTER-TEST",
        "rough_sorter.v2",
        "rough_sorter_devices",
        "smt_inbound_handoff",
        "ecs_host",
        "ecs_port",
        "粗分机工作线/设备",
        "获取作业线插件选项",
        "/api/v1/workline/plugins/options",
    ):
        assert retired_token not in source

    assert "'biz:workline:list'" in source
    assert "'POST', '/api/v1/workline/work_lines/query'" in source
