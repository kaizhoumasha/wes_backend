"""平台插件在进入无事务 QUERY 阶段前必须重校验 binding 可变准入事实。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.app.runtime.extension_identity import sha256_digest
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    _build_plugin_dispatch_request,
)
from src.app.runtime.workline_plugins.attempt_coordinator import AttemptSnapshot
from src.app.runtime.workline_plugins.generated_index import WORKLINE_PLUGIN_INDEX_DIGEST
from src.app.workline.services.plugin_binding_service import (
    PluginBindingAdmissionError,
    workline_plugin_binding_service,
)


@pytest.mark.asyncio
async def test_dispatch_request_rejects_disabled_binding_before_stage_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"provider_profile": "runtime"}
    binding = SimpleNamespace(
        id=17,
        binding_version=4,
        plugin_key="rough_sorter",
        contract_version="rough_sorter.v2",
        typed_config_json=config,
        typed_config_hash=sha256_digest(config),
        is_enabled=False,
        is_revoked=False,
        environment="sandbox",
        valid_from=None,
        valid_until=None,
    )
    get_pinned = AsyncMock(return_value=binding)
    monkeypatch.setattr(workline_plugin_binding_service, "get_pinned", get_pinned)
    db = object()

    with pytest.raises(PluginBindingAdmissionError, match="kill switch"):
        await _build_plugin_dispatch_request(
            db,
            inbox=SimpleNamespace(
                id=1,
                kind="DEVICE_EVENT",
                event_type="SCAN_COMPLETED",
                payload_json={"event_type": "SCAN_COMPLETED", "data": {"PkgID": "PKG-1"}},
            ),
            session=SimpleNamespace(
                plugin_state_json={"phase": "READY"},
                context_json={},
                current_material_unit_id=None,
                awaiting_device_command_code=None,
            ),
            workline=SimpleNamespace(id=3),
            snapshot=AttemptSnapshot(
                processor_token="lease-1",
                session_version=7,
                plugin_state_version=0,
                binding_id=17,
                binding_version=4,
                plugin_config_hash=sha256_digest(config),
                index_digest=WORKLINE_PLUGIN_INDEX_DIGEST,
            ),
        )

    get_pinned.assert_awaited_once_with(db, binding_id=17)
