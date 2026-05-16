"""SMT 货架/料箱调度领域服务测试。"""

from src.app.workline.domain.services import SmtRackBinSchedulingDecision, SmtRackBinSchedulingService
from src.workline_runtime.services import build_workline_runtime_services


def test_smt_rack_bin_scheduler_allocates_stable_bin_location() -> None:
    """同一 PkgID 应得到稳定料箱调度结果，供粗分机 _allocate_bin 使用。"""

    service = SmtRackBinSchedulingService()

    first = service.allocate("PKG-001")
    second = service.allocate("PKG-001")

    assert first == second
    assert set(first) == {"bin_id", "bin_type", "bin_cell_location"}
    assert first["bin_id"].startswith("BIN_")
    assert first["bin_cell_location"].isdigit()


def test_smt_rack_bin_scheduler_plans_default_bin_allocation() -> None:
    """默认调度决策是分配料箱；满箱交换由后续真实资源策略触发。"""

    service = SmtRackBinSchedulingService()

    decision = service.plan_allocation("PKG-001", context={"workline_code": "SMT-01"})

    assert isinstance(decision, SmtRackBinSchedulingDecision)
    assert decision.bin_location == service.allocate("PKG-001")
    assert decision.full_box_exchange_request is None


def test_runtime_services_injects_default_smt_rack_bin_scheduler() -> None:
    """worker 构建运行时服务时应默认注入具体调度领域服务，而不是让插件走占位逻辑。"""

    services = build_workline_runtime_services()

    assert isinstance(services.bin_allocator, SmtRackBinSchedulingService)
    assert services.bin_allocator.allocate("PKG-002") == SmtRackBinSchedulingService().allocate("PKG-002")
