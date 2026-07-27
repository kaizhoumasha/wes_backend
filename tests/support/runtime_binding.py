"""Mandatory runtime binding 测试构造器共用字段。"""

from __future__ import annotations

from typing import Any

from src.app.workline.models import LineType, WorkLine, WorklinePluginBinding
from src.utils.timezone import timezone


def binding_pin_fields(*, binding_id: int = 1) -> dict[str, Any]:
    """返回彼此一致的测试 binding pins；PostgreSQL 验收另行使用真实 binding FK。"""

    return {
        "plugin_binding_id": binding_id,
        "plugin_binding_version": 1,
        "plugin_config_hash": "c" * 64,
        "plugin_index_digest": "d" * 64,
    }


async def bind_runtime_workline(
    db: Any,
    workline: WorkLine,
    *,
    plugin_key: str = "test-plugin",
    contract_version: str = "v1",
) -> WorklinePluginBinding:
    """给已持久化的测试工作线追加 immutable binding 并设置 active pins。"""

    workline.plugin_key = plugin_key
    workline.contract_version = contract_version
    binding = WorklinePluginBinding(
        workline_id=workline.id,
        plugin_key=plugin_key,
        contract_version=contract_version,
        binding_version=1,
        typed_config_json={},
        typed_config_hash="c" * 64,
        provider_profile_snapshot_json=[],
        device_snapshot_json=[],
        generated_index_digest="d" * 64,
        environment="test",
        activated_at=timezone.now_for_db(),
        activated_by="pytest",
        activated_reason="mandatory runtime binding fixture",
    )
    db.add(binding)
    await db.flush()
    workline.active_plugin_binding_id = binding.id
    workline.active_plugin_binding_version = binding.binding_version
    workline.active_plugin_config_hash = binding.typed_config_hash
    workline.active_plugin_index_digest = binding.generated_index_digest
    workline.active_plugin_provider_requirements_json = []
    await db.flush()
    return binding


async def seed_runtime_binding(
    db: Any,
    *,
    line_code: str,
    plugin_key: str = "test-plugin",
    contract_version: str = "v1",
) -> tuple[WorkLine, WorklinePluginBinding]:
    """创建测试工作线和对应 binding，供真实 PostgreSQL fixture 复用。"""

    workline = WorkLine(
        line_code=line_code,
        line_name=line_code,
        line_type=LineType.AUTO,
        plugin_key=plugin_key,
        contract_version=contract_version,
        is_active=True,
    )
    db.add(workline)
    await db.flush()
    return workline, await bind_runtime_workline(
        db,
        workline,
        plugin_key=plugin_key,
        contract_version=contract_version,
    )


__all__ = ["bind_runtime_workline", "binding_pin_fields", "seed_runtime_binding"]
