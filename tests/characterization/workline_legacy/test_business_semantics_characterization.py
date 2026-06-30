"""BC-05/06/07 characterization 输入提取（Phase 0 骨架）。

从旧测试/旧 runtime 提取业务语义 characterization, 作为目标态 contract test 的输入。
BC-07 分拣机入库 Phase 0 可 pending, 但必须有 fixture draft。
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _assert_characterization_sources_exist(sources: list[str]) -> None:
    for src in sources:
        source_path = REPO_ROOT / src
        assert source_path.exists(), f"characterization 来源缺失: {src}"
        if source_path.is_file():
            assert source_path.stat().st_size > 0, f"characterization 来源为空文件: {src}"
        else:
            assert any(source_path.iterdir()), f"characterization 来源为空目录: {src}"


def test_rough_sorter_inbound_characterization_inputs_extracted():
    """BC-05 输入提取: 粗分机正常入库的旧业务输入已被识别为 characterization 来源。

    Phase 0 验证输入来源存在; Phase 4 接入目标态 capability 后转为 contract test。
    """
    # 旧业务输入来源: rough_sorter plugin + 旧测试
    sources = [
        "src/workline_plugins/rough_sorter/plugin.py",
        "src/workline_plugins/rough_sorter/contract.py",
        "tests/workline_plugins/test_rough_sorter_contract.py",
        "tests/workline_plugins/test_rough_sorter_plugin.py",
    ]
    _assert_characterization_sources_exist(sources)


def test_full_box_exchange_characterization_inputs_extracted():
    """BC-06 输入提取: 满箱交换前置分流的旧业务输入已被识别为 characterization 来源。

    Phase 0 验证输入来源存在; Phase 3/4 接入目标态后转为 contract test。
    """
    # 旧业务输入来源: 主计划 §2.2 full-box exchange + smt_inbound_handoff + rack_operation
    sources = [
        "src/app/runtime/orchestration/models/smt_inbound_handoff.py",
        "src/app/workline/domain/services/smt_inbound_handoff_route_service.py",
        "docs/integration/wms_rcs_interface_requirements.md",
    ]
    _assert_characterization_sources_exist(sources)


def test_sorter_inbound_characterization_fixture_draft():
    """BC-07 输入提取: 分拣机入库 characterization fixture draft。

    Phase 0 可 pending, 但必须有 fixture draft。Phase 1 RuntimeIntentLog schema
    完成后升级为 contract test。
    """
    # fixture draft 来源: src/workline_plugins/smt_sorting_inbound/ + 阶段 1 contract test。
    # 原 tests/workline_runtime 已在阶段 3 删除 (wlr 整目录物理删除);行为契约由
    # tests/contracts/workline/ 下游 contract 覆盖(尤其是 sorter in/runtime_intent_log)。
    draft_sources = [
        "src/workline_plugins/smt_sorting_inbound/plugin.py",
        "src/workline_plugins/smt_sorting_inbound/flow_service.py",
        "src/app/runtime/orchestration/runtime_intent_log.py",
        "tests/contracts/workline/test_rough_sorter_inbound_contract.py",
        "tests/contracts/workline/test_runtime_intent_log_dispatch_contract.py",
    ]
    _assert_characterization_sources_exist(draft_sources)
