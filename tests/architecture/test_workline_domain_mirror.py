"""Phase 2 burn-down 阶段 2 C3 — workline domain 业务概念镜像 AST 签名一致。

C3 镜像 3 个文件(plus contracts 是 package with __init__.py + 2 sub-modules):
  - src/app/workline/domain/ng_reason.py
  - src/app/workline/domain/material_identity.py
  - src/app/workline/domain/contracts/__init__.py  (+ device_error_codes.py + six_in_one.py)

(plugin_manifest 推迟到 C4 — 见 brief 顶部 Pre-Flight Finding)

不验证运行时行为, 只验证 mirror 文件存在 + 关键公开类/函数已导出。
"""

from __future__ import annotations

import importlib

import pytest

REPO_ROOT = None  # 不需要文件路径,用 importlib 验证模块加载


def _module_imports(name: str) -> bool:
    """如果模块可被 import 且不带异常,返回 True。"""
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def test_ng_reason_mirror_exposes_required_symbols() -> None:
    """ng_reason 镜像导出 NgReasonDefinition + build_ng_reason_catalog + BUILTIN_NG_REASONS。"""
    from src.app.workline.domain import ng_reason

    assert hasattr(ng_reason, "NgReasonDefinition")
    assert hasattr(ng_reason, "build_ng_reason_catalog")
    assert hasattr(ng_reason, "BUILTIN_NG_REASONS")


def test_material_identity_mirror_exposes_required_symbols() -> None:
    """material_identity 镜像导出 MaterialIdentity 三个公开类型。"""
    from src.app.workline.domain import material_identity

    assert hasattr(material_identity, "MaterialIdentityInput")
    assert hasattr(material_identity, "MaterialIdentity")
    assert hasattr(material_identity, "MaterialIdentityResolutionStatus")


def test_contracts_mirror_package_exposes_sixinone_and_device_error_code() -> None:
    """src/app/workline/domain/contracts/__init__.py 导出 SixInOne + DeviceErrorCode(无重命名)。"""
    from src.app.workline.domain import contracts

    assert hasattr(contracts, "SixInOne")
    assert hasattr(contracts, "DeviceErrorCode")
    assert set(getattr(contracts, "__all__", [])) >= {"SixInOne", "DeviceErrorCode"}


def test_domain_package_exposes_all_three_mirrors() -> None:
    """src.app.workline.domain 包导入正常, 3 个镜像都可独立 import."""
    assert _module_imports("src.app.workline.domain.ng_reason")
    assert _module_imports("src.app.workline.domain.material_identity")
    # contracts 是 package(__init__.py + 2 sub-modules),三者都要可 import
    assert _module_imports("src.app.workline.domain.contracts")
    assert _module_imports("src.app.workline.domain.contracts.six_in_one")
    assert _module_imports("src.app.workline.domain.contracts.device_error_codes")
