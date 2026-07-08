"""C3 inventory: 外部权威 QueryPort response 必含 AuthorityMetadata (H1 / CEO-005)。

主计划 §7.5 C3 + §3.4 Authority Matrix：查询响应强制带
scope/authority/source/evidence_at；外部权威 QueryPort response
（WMS MasterData / Document / InventoryQuery / ReconciliationQuery）
额外必含 source_version。

本测试建立 Response schema 的 authority
metadata 注册表机制：被声明为"权威查询响应"的 Response 类必须复合
AuthorityMetadata（或 ExternalAuthorityMetadata）；未被声明的 Response
不检查，避免一次性 cascade。

注册表采用显式声明（而非扫描全部 *Response 类），原因：
1. 项目 34+ Response 类散落在各域 models/，多数是本地配置/内部 DTO，
   不属于 C3 范围（主计划 §7.5 C3 只约束"查询响应"，非全部 response）
2. 一次性全量强制会导致 review 和回归范围失控
3. 显式注册表让 CEO-005 可逐域渐进落地，每域一个 PR 切片

注册表位置：src/core/authority_registry.py（CEO-005 随各域落地逐步填充）
"""

from __future__ import annotations

import importlib
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from src.core.authority_metadata import (
    AuthorityMetadata,
    ExternalAuthorityMetadata,
)


def _is_authority_metadata_annotated(annotation: object) -> bool:
    """判断字段注解是否为 AuthorityMetadata 或其 Optional/Union 形式。"""
    if annotation in (AuthorityMetadata, ExternalAuthorityMetadata):
        return True
    # Optional[AuthorityMetadata] / AuthorityMetadata | None
    args: tuple[object, ...] = getattr(annotation, "__args__", ())
    if not args:
        return False
    return any(arg in (AuthorityMetadata, ExternalAuthorityMetadata) for arg in args)


def _load_registry() -> list[str]:
    """加载权威查询响应注册表 (dotted path 列表)。

    实施初期注册表为空，随各域 QueryPort response 落地逐步填充。
    注册表文件 src/core/authority_registry.py 在第一个域落地时创建。
    """
    try:
        module = importlib.import_module("src.core.authority_registry")
    except ModuleNotFoundError:
        return []
    registry = getattr(module, "AUTHORITY_RESPONSE_REGISTRY", [])
    return list(registry)


def test_authority_metadata_contract_complete():
    """AuthorityMetadata 四字段必填 + extra=forbid。"""
    meta = AuthorityMetadata(
        scope="WORKLINE_LOCAL",
        authority="WMS",
        source="wms_inventory_query",
        evidence_at="2026-06-26T10:00:00Z",
    )
    assert meta.scope and meta.authority and meta.source and meta.evidence_at


def test_external_authority_metadata_requires_source_version():
    """外部权威 QueryPort response 额外必含 source_version。"""
    meta = ExternalAuthorityMetadata(
        scope="WORKLINE_LOCAL",
        authority="WMS",
        source="wms_inventory_query",
        evidence_at="2026-06-26T10:00:00Z",
        source_version="2026-06-26T10:00:00Z",
    )
    assert meta.source_version

    # 缺 source_version 应拒绝
    with pytest.raises(ValidationError):
        ExternalAuthorityMetadata(
            scope="WORKLINE_LOCAL",
            authority="WMS",
            source="wms_inventory_query",
            evidence_at="2026-06-26T10:00:00Z",
        )  # type: ignore[call-arg]


def test_registered_authority_responses_contain_metadata_field():
    """注册表中的每个 Response 类必须含 authority_metadata 字段。

    实施初期注册表为空 → 本测试 skip。
    随各域 QueryPort response 落地，注册表填充后本测试开始生效。
    """
    registry = _load_registry()
    if not registry:
        pytest.skip("authority_registry 未创建或为空 — CEO-005 各域 QueryPort response 落地后逐步填充注册表")

    missing: list[str] = []
    for dotted_path in registry:
        module_path, _, class_name = dotted_path.rpartition(".")
        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
        except (ImportError, AttributeError):
            missing.append(f"{dotted_path} (无法 import)")
            continue

        type_hints = get_type_hints(cls, include_extras=True)
        has_metadata = any(_is_authority_metadata_annotated(ann) for ann in type_hints.values())
        if not has_metadata:
            missing.append(dotted_path)

    assert missing == [], "注册表中的 Response 类缺少 authority_metadata 字段:\n  - " + "\n  - ".join(missing)
