"""Phase 2 burn-down 阶段 2 C5a — orchestration bridges 镜像与重导出。

C5a 镜像 13 个文件:
  - src/app/runtime/orchestration/{enums, device_ordering, runtime_intent, effect_result,
                                     material_target_resolver}.py (5 clean leaves)
  - src/app/runtime/orchestration/{business_identity_bridge, lock_bridge,
                                     resource_wait_evidence_bridge, sandbox_catalog_bridge,
                                     events_bridge, topology_bridge, runtime_intent_effects}.py
  - src/app/workline/runtime_services.py

(plugin_* 推迟到 C5b)

不验证运行时行为, 只验证 mirror 文件存在 + 关键公开类/函数已导出。
"""

from __future__ import annotations

import importlib

import pytest


def _module_imports(name: str) -> bool:
    """如果模块可被 import 且不带异常,返回 True。"""
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def test_enums_mirror_exposes_failure_domain() -> None:
    """src/app/runtime/orchestration/enums.py 导出 FailureDomain。"""
    from src.app.runtime.orchestration import enums

    assert hasattr(enums, "FailureDomain")


def test_business_identity_bridge_exposes_resolve_payload_display_identity() -> None:
    """business_identity_bridge 导出 resolve_payload_display_identity。"""
    from src.app.runtime.orchestration import business_identity_bridge

    assert hasattr(business_identity_bridge, "resolve_payload_display_identity")


def test_lock_bridge_exposes_redis_distributed_lock() -> None:
    """lock_bridge 导出 RedisDistributedLock。"""
    from src.app.runtime.orchestration import lock_bridge

    assert hasattr(lock_bridge, "RedisDistributedLock")


def test_resource_wait_evidence_bridge_exposes_resource_wait_evidence() -> None:
    """resource_wait_evidence_bridge 导出 ResourceWaitEvidence。"""
    from src.app.runtime.orchestration import resource_wait_evidence_bridge

    assert hasattr(resource_wait_evidence_bridge, "ResourceWaitEvidence")


def test_sandbox_catalog_bridge_exposes_public_functions() -> None:
    """sandbox_catalog_bridge 导出 rough_sorter_scan_completed_payload。"""
    from src.app.runtime.orchestration import sandbox_catalog_bridge

    assert hasattr(sandbox_catalog_bridge, "rough_sorter_scan_completed_payload")


def test_events_bridge_exposes_assert_not_reserved() -> None:
    """events_bridge 导出 assert_not_reserved_runtime_event + RESERVED_RUNTIME_EVENTS。"""
    from src.app.runtime.orchestration import events_bridge

    assert hasattr(events_bridge, "assert_not_reserved_runtime_event")
    assert hasattr(events_bridge, "RESERVED_RUNTIME_EVENTS")


def test_device_ordering_mirror_exposes_device_sort_key() -> None:
    """device_ordering.py 导出 device_sort_key。"""
    from src.app.runtime.orchestration import device_ordering

    assert hasattr(device_ordering, "device_sort_key")


def test_topology_bridge_exposes_workline_topology_view() -> None:
    """topology_bridge 导出 WorklineTopologyView + validate_topology_manifest。"""
    from src.app.runtime.orchestration import topology_bridge

    assert hasattr(topology_bridge, "WorklineTopologyView")
    assert hasattr(topology_bridge, "validate_topology_manifest")


def test_runtime_intent_mirror_exposes_public_classes() -> None:
    """runtime_intent.py 导出 RuntimeIntent + BlockScope + Destination + RuntimeIntentKind。"""
    from src.app.runtime.orchestration import runtime_intent

    assert hasattr(runtime_intent, "RuntimeIntent")
    assert hasattr(runtime_intent, "BlockScope")
    assert hasattr(runtime_intent, "Destination")
    assert hasattr(runtime_intent, "RuntimeIntentKind")


def test_effect_result_mirror_exposes_runtime_intent_effect_result() -> None:
    """effect_result.py 导出 RuntimeIntentEffectResult + WriteBackDisposition。"""
    from src.app.runtime.orchestration import effect_result

    assert hasattr(effect_result, "RuntimeIntentEffectResult")
    assert hasattr(effect_result, "WriteBackDisposition")


def test_material_target_resolver_mirror_exposes_resolve_destination_device() -> None:
    """material_target_resolver.py 导出 resolve_destination_device。"""
    from src.app.runtime.orchestration import material_target_resolver

    assert hasattr(material_target_resolver, "resolve_destination_device")


def test_runtime_intent_effects_mirror_exposes_runtime_intent_effect_applier() -> None:
    """runtime_intent_effects.py 导出 RuntimeIntentEffectApplier。"""
    from src.app.runtime.orchestration import runtime_intent_effects

    assert hasattr(runtime_intent_effects, "RuntimeIntentEffectApplier")


def test_workline_runtime_services_mirror_exposes_public_factories() -> None:
    """workline runtime_services 镜像导出 factory + facade。"""
    from src.app.workline import runtime_services

    assert hasattr(runtime_services, "WorklineRuntimeServices")
    assert hasattr(runtime_services, "build_workline_runtime_services")


def test_no_wlr_self_imports_in_new_mirrors() -> None:
    """新 mirror 文件不能含 wlr 自引用(R-WLR guardrail clean)。"""
    import subprocess

    files_to_check = [
        "src/app/runtime/orchestration/enums.py",
        "src/app/runtime/orchestration/business_identity_bridge.py",
        "src/app/runtime/orchestration/lock_bridge.py",
        "src/app/runtime/orchestration/resource_wait_evidence_bridge.py",
        "src/app/runtime/orchestration/sandbox_catalog_bridge.py",
        "src/app/runtime/orchestration/events_bridge.py",
        "src/app/runtime/orchestration/device_ordering.py",
        "src/app/runtime/orchestration/topology_bridge.py",
        "src/app/runtime/orchestration/runtime_intent.py",
        "src/app/runtime/orchestration/effect_result.py",
        "src/app/runtime/orchestration/material_target_resolver.py",
        "src/app/runtime/orchestration/runtime_intent_effects.py",
        "src/app/workline/runtime_services.py",
    ]
    grep_args = [
        "grep",
        "-lE",
        "from src\\.workline_runtime|import src\\.workline_runtime",
        *files_to_check,
    ]
    result = subprocess.run(
        grep_args,
        capture_output=True,
        text=True,
        check=False,
    )
    bad = result.stdout.strip().split("\n") if result.stdout.strip() else []
    assert bad == [], f"以下 mirror 文件仍含 wlr 自引用: {bad}"


def test_orchestration_subpackage_imports_cleanly() -> None:
    """src.app.runtime.orchestration 包及其新镜像子模块都可独立 import。"""
    assert _module_imports("src.app.runtime.orchestration.enums")
    assert _module_imports("src.app.runtime.orchestration.business_identity_bridge")
    assert _module_imports("src.app.runtime.orchestration.lock_bridge")
    assert _module_imports("src.app.runtime.orchestration.resource_wait_evidence_bridge")
    assert _module_imports("src.app.runtime.orchestration.sandbox_catalog_bridge")
    assert _module_imports("src.app.runtime.orchestration.events_bridge")
    assert _module_imports("src.app.runtime.orchestration.device_ordering")
    assert _module_imports("src.app.runtime.orchestration.topology_bridge")
    assert _module_imports("src.app.runtime.orchestration.runtime_intent")
    assert _module_imports("src.app.runtime.orchestration.effect_result")
    assert _module_imports("src.app.runtime.orchestration.material_target_resolver")
    assert _module_imports("src.app.runtime.orchestration.runtime_intent_effects")
    assert _module_imports("src.app.workline.runtime_services")
