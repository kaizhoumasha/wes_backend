"""Phase 4 design documentation contracts."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

PHASE4_SPEC_FILES = (
    "cell-reservation-spec.md",
    "material-location-query-spec.md",
    "workline-active-objects-spec.md",
    "sorter-inbound-capability-spec.md",
    "smt-ng-wms-reconciliation-spec.md",
)
DEFERRED_SPEC_REFERENCES = {"fulfillment-provider-adapter-spec.md"}
SPEC_REFERENCE_PATTERN = re.compile(r"`(?:docs/architecture/)?([^`/]+-spec\.md)`")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _phase4_main_plan_text(main_plan: str) -> str:
    start = main_plan.index("### 10.5 Material-flow target capabilities")
    next_section = main_plan.find("### 10.6", start)
    phase4_section = main_plan[start:] if next_section == -1 else main_plan[start:next_section]
    phase4_startup_lines = "\n".join(line for line in main_plan.splitlines() if "Phase 4 启动时" in line)
    implementation = _read(REPO_ROOT / "docs" / "architecture" / "workline-restructuring-implementation.md")
    impl_start = implementation.index("### 10.5 Material-flow target capabilities")
    impl_next = implementation.find("### 10.6", impl_start)
    impl_phase4_section = implementation[impl_start:] if impl_next == -1 else implementation[impl_start:impl_next]
    return f"{phase4_section}\n{phase4_startup_lines}\n{impl_phase4_section}"


def _extract_spec_references(*texts: str) -> set[str]:
    return {match.group(1) for text in texts for match in SPEC_REFERENCE_PATTERN.finditer(text)}


def _section_between(text: str, start_heading: str, end_heading: str) -> str:
    start = text.index(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


def _src_defines_symbol(symbol: str) -> bool:
    return any(
        f"class {symbol}" in path.read_text(encoding="utf-8") for path in (REPO_ROOT / "src" / "app").rglob("*.py")
    )


def test_phase4_design_package_exists_and_is_linked_from_main_plan() -> None:
    main_plan = _read(REPO_ROOT / "docs" / "architecture" / "workline-and-plugin-restructuring.md")
    phase4_main_plan = _phase4_main_plan_text(main_plan)
    umbrella = REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-07-03-phase4-design-with-residuals.md"

    assert umbrella.exists()
    umbrella_text = _read(umbrella)
    assert "Residual Readiness Register" in umbrella_text
    assert "scripts/check_runtime_production_closure_gate.py" in umbrella_text

    for filename in PHASE4_SPEC_FILES:
        assert filename in umbrella_text
        assert filename in phase4_main_plan
        assert (REPO_ROOT / "docs" / "architecture" / filename).exists()


def test_phase4_spec_references_are_not_dangling() -> None:
    main_plan = _read(REPO_ROOT / "docs" / "architecture" / "workline-and-plugin-restructuring.md")
    phase4_main_plan = _phase4_main_plan_text(main_plan)
    umbrella_text = _read(REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-07-03-phase4-design-with-residuals.md")
    spec_texts = [_read(REPO_ROOT / "docs" / "architecture" / filename) for filename in PHASE4_SPEC_FILES]

    referenced_specs = _extract_spec_references(phase4_main_plan, umbrella_text, *spec_texts)
    required_specs = set(PHASE4_SPEC_FILES)

    for filename in referenced_specs:
        if filename in DEFERRED_SPEC_REFERENCES:
            continue
        assert filename in required_specs, f"{filename} is referenced by Phase 4 docs but not in registry"
        assert (REPO_ROOT / "docs" / "architecture" / filename).exists(), f"{filename} is referenced but missing"


def test_phase4_specs_keep_residual_gates_explicit() -> None:
    required_tokens = (
        "边界声明",
        "Residual Readiness",
        "行为契约测试",
        "实施前置条件",
        "Legacy cleanup",
    )

    for filename in PHASE4_SPEC_FILES:
        text = _read(REPO_ROOT / "docs" / "architecture" / filename)
        for token in required_tokens:
            assert token in text, f"{filename} missing {token}"
        for residual in (
            "Callback admission",
            "WorkLine runtime projection cleanup",
            "Runtime production closure profile",
        ):
            assert residual in text, f"{filename} missing {residual} residual gate"
        assert "不复用旧 plugin" in text


def test_cell_reservation_spec_reuses_existing_model_and_maps_target_states() -> None:
    text = _read(REPO_ROOT / "docs" / "architecture" / "cell-reservation-spec.md")

    for token in (
        "WorklineBinCellReservation",
        "WorklineBinCellReservationService",
        "`RESERVED`",
        "`OCCUPIED`",
        "`RELEASED`",
        "`RECONCILING`",
        "BinCellReservationStatus.PLANNED",
        "BinCellReservationStatus.CONSUMED",
        "BinCellReservationStatus.RELEASED",
        "持久状态缺口",
        "禁止新建第二套 reservation model",
    ):
        assert token in text


def test_phase4_design_does_not_prematurely_close_implementation_or_legacy_drop() -> None:
    docs = [
        _read(REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-07-03-phase4-design-with-residuals.md"),
        _read(REPO_ROOT / "docs" / "architecture" / "workline-and-plugin-restructuring.md"),
        *[_read(REPO_ROOT / "docs" / "architecture" / filename) for filename in PHASE4_SPEC_FILES],
    ]
    combined = "\n".join(docs)

    for forbidden in (
        "Phase 4 已完成",
        "Phase4 已完成",
        "Phase 4 实现完成",
        "Phase4 实现完成",
        "Phase 5 可以提前删除",
        "绕过 production closure",
    ):
        assert forbidden not in combined

    assert "Phase 4 设计可以先行" in combined
    assert "legacy cleanup 才能删除" in combined


def test_phase4_spec_status_headers_match_development_scope() -> None:
    expected_status_tokens = {
        "cell-reservation-spec.md": ("CellReservation", "开发/测试"),
        "material-location-query-spec.md": ("MaterialLocationQuery", "开发/测试"),
        "workline-active-objects-spec.md": ("WorklineActiveObjects", "开发/测试"),
        "sorter-inbound-capability-spec.md": ("runtime capability", "evidence profile"),
        "smt-ng-wms-reconciliation-spec.md": ("runtime capability", "evidence profile"),
    }

    for filename, required_tokens in expected_status_tokens.items():
        header = "\n".join(_read(REPO_ROOT / "docs" / "architecture" / filename).splitlines()[:4])
        assert "未实现" not in header, f"{filename} status header is stale"
        for token in required_tokens:
            assert token in header, f"{filename} status header missing {token}"


def test_sorter_runtime_mapping_does_not_mark_target_location_event_as_existing() -> None:
    text = _read(REPO_ROOT / "docs" / "architecture" / "sorter-inbound-capability-spec.md")
    runtime_mapping = _section_between(text, "## 10. Runtime 集成映射", "## 11. 实时决策延迟预算")

    if _src_defines_symbol("RuntimeLocationEvent"):
        return

    runtime_location_rows = [
        line for line in runtime_mapping.splitlines() if line.startswith("|") and "`RuntimeLocationEvent`" in line
    ]
    assert runtime_location_rows
    for line in runtime_location_rows:
        assert "✅" not in line
        assert "🆕" in line
        assert "需新增" in line or "ObjectTransitionEvent" in line


def test_sorter_cell_reservation_rows_reuse_existing_model() -> None:
    text = _read(REPO_ROOT / "docs" / "architecture" / "sorter-inbound-capability-spec.md")
    runtime_mapping = _section_between(text, "## 10. Runtime 集成映射", "## 11. 实时决策延迟预算")

    assert "`CellReservation` (🆕)" not in runtime_mapping
    cell_reservation_rows = [
        line
        for line in runtime_mapping.splitlines()
        if line.startswith("|") and "格位分配" in line and "CellReservation" in line
    ]
    assert cell_reservation_rows
    assert all("WorklineBinCellReservation" in line for line in cell_reservation_rows)
    assert all("♻️" in line for line in cell_reservation_rows)


def test_sorter_wms_pkg_binding_uses_fulfillment_port() -> None:
    text = _read(REPO_ROOT / "docs" / "architecture" / "sorter-inbound-capability-spec.md")
    runtime_mapping = _section_between(text, "## 10. Runtime 集成映射", "## 11. 实时决策延迟预算")

    pkg_binding_rows = [line for line in runtime_mapping.splitlines() if line.startswith("|") and "PKG 绑定" in line]
    inventory_transaction_rows = [
        line for line in runtime_mapping.splitlines() if line.startswith("|") and "库存事务" in line
    ]

    assert pkg_binding_rows
    assert inventory_transaction_rows
    assert all("wms.fulfillment.notify_pkg_binding@v1" in line for line in pkg_binding_rows)
    assert all("WmsInventoryTransactionPort" not in line for line in pkg_binding_rows)
    assert all("WmsInventoryTransactionPort" in line for line in inventory_transaction_rows)


def test_sorter_characterization_to_target_mapping_is_explicit() -> None:
    text = _read(REPO_ROOT / "docs" / "architecture" / "sorter-inbound-capability-spec.md")

    for token in (
        "characterization-to-target",
        "BC-05",
        "BC-06",
        "BC-07",
        "wms.fulfillment.notify_pkg_binding@v1",
        "WmsInventoryTransactionPort",
        "RuntimeLocationEvent",
        "WorklineBinCellReservation",
    ):
        assert token in text


def test_smt_ng_wms_reconciliation_contract_covers_conflict_scenarios() -> None:
    text = _read(REPO_ROOT / "docs" / "architecture" / "smt-ng-wms-reconciliation-spec.md")

    for token in (
        "NG evidence",
        "本地物理事实缺失",
        "WMS 拒绝",
        "目标箱回写失败",
        "重复 callback",
        "乱序 callback",
        "source_version drift",
        "RuntimeHold 解除只释放声明 scope",
    ):
        assert token in text


def test_material_flow_mock_acceptance_is_non_production_scope() -> None:
    docs = [
        _read(REPO_ROOT / "docs" / "superpowers" / "plans" / "2026-07-04-runtime-evidence-readiness.md"),
        _read(REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-07-03-phase4-design-with-residuals.md"),
        _phase4_main_plan_text(_read(REPO_ROOT / "docs" / "architecture" / "workline-and-plugin-restructuring.md")),
        _read(REPO_ROOT / "docs" / "architecture" / "sorter-inbound-capability-spec.md"),
        _read(REPO_ROOT / "docs" / "architecture" / "smt-ng-wms-reconciliation-spec.md"),
    ]
    combined = "\n".join(docs)

    for token in (
        "本机开发环境 MOCK 验收",
        "不做生产接入",
        "tests/mock/material_flow",
        "生产热路径",
        "runtime residual gate",
    ):
        assert token in combined


def test_production_closure_gate_is_mock_for_current_dev_test_scope() -> None:
    docs = [
        _read(REPO_ROOT / "docs" / "superpowers" / "plans" / "2026-07-04-runtime-evidence-readiness.md"),
        _read(REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-07-03-phase4-design-with-residuals.md"),
        _phase4_main_plan_text(_read(REPO_ROOT / "docs" / "architecture" / "workline-and-plugin-restructuring.md")),
        *[_read(REPO_ROOT / "docs" / "architecture" / filename) for filename in PHASE4_SPEC_FILES],
    ]
    combined = "\n".join(docs)

    for token in (
        "当前开发/测试默认使用 MOCK closure",
        "真实 artifact 不再作为当前开发/测试推进阻塞项",
        "`--closure-profile production`",
    ):
        assert token in combined

    for forbidden in (
        "Wave2/Wave3 阻塞",
        "等待真实环境 evidence",
        "production closure artifacts 未完整",
        "正式上线前必须有 production P0 E2E artifact",
        "必须先补齐 production closure artifact 和 benchmark evidence",
        "生产闭环实现必须等待 RuntimeInbox cutover、P0 E2E artifact 和 production benchmark artifact",
        "实现前必须通过 production closure gate，尤其是 RuntimeInbox cutover 与 queue writer PostgreSQL evidence",
    ):
        assert forbidden not in combined
