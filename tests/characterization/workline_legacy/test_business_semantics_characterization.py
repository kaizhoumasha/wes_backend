"""BC-05/06/07 characterization 输入提取。

从旧测试/旧 runtime 提取业务语义 characterization, 作为目标态 contract test 的输入。
BC-07 分拣机入库可 pending, 但必须有 fixture draft。
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

    验证输入来源存在; 接入目标态 capability 后转为 contract test。
    """
    # legacy archive 只用于发现旧输入，不是目标合同真源；新规格、fixture 与合同测试承接目标态语义。
    sources = [
        "docs/archive/legacy-workline-plugins/src-workline_plugins/rough_sorter/plugin.py",
        "docs/business/rough_sorter_scan_decision_contract.md",
        "src/app/runtime/capabilities/material_flow/contracts/rough_sorter.py",
        "tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json",
        "tests/contracts/workline/test_rough_sorter_inbound_contract.py",
        "tests/contracts/workline/test_rough_sorter_scan_decision_spec.py",
        "tests/workline_plugins/rough_sorter/test_handlers.py",
        "tests/workline_runtime/test_sorter_inbound_runtime_service.py",
    ]
    _assert_characterization_sources_exist(sources)


def test_full_box_exchange_characterization_inputs_extracted():
    """BC-06 输入提取: 满箱交换前置分流的旧业务输入已被识别为 characterization 来源。

    验证输入来源存在; 接入目标态后转为 contract test。
    """
    # 旧业务输入来源: 主计划 §2.2 full-box exchange + smt_inbound_handoff + rack_operation
    sources = [
        "src/app/runtime/orchestration/models/smt_inbound_handoff.py",
        "src/app/runtime/capabilities/material_flow/smt_inbound_handoff_route_service.py",
        "docs/integration/wms_rcs_interface_requirements.md",
    ]
    _assert_characterization_sources_exist(sources)


def test_sorter_inbound_characterization_fixture_draft():
    """BC-07 输入提取: 分拣机入库 characterization fixture draft。

    可 pending, 但必须有 fixture draft。RuntimeIntentLog schema
    完成后升级为 contract test。
    """
    # fixture draft 来源: legacy archive + material-flow runtime/capability contract test。
    # 旧 src/workline_plugins 已在 target-state runtime capability wiring 退出运行路径。
    draft_sources = [
        "docs/archive/legacy-workline-plugins/src-workline_plugins/smt_sorting_inbound/plugin.py",
        "docs/archive/legacy-workline-plugins/src-workline_plugins/smt_sorting_inbound/flow_service.py",
        "src/app/runtime/workline_plugins/rough_sorter/domain_contract.py",
        "src/app/runtime/capabilities/material_flow/sorter_inbound_runtime_service.py",
        "src/app/runtime/orchestration/runtime_intent_log.py",
        "tests/contracts/workline/test_rough_sorter_inbound_contract.py",
        "tests/contracts/workline/test_runtime_intent_log_dispatch_contract.py",
        "tests/workline_runtime/test_sorter_inbound_runtime_service.py",
    ]
    _assert_characterization_sources_exist(draft_sources)
