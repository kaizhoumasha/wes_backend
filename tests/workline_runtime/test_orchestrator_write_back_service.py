import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.workline.services.write_back_service import orchestrator_write_back_service
from src.workline_runtime.orchestrator import OrchestratorResult


def test_runtime_write_back_does_not_depend_on_celery_task_facade() -> None:
    source = Path("src/workline_runtime/runtime_intent_effects.py").read_text()
    tree = ast.parse(source)

    celery_imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src.celery_app.tasks")
    ]

    assert celery_imports == []


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.mark.asyncio
async def test_write_back_calls_applier(mock_db):
    session = MagicMock()
    session.trace_id = "test-trace"
    session.context_json = {"key": "value"}

    workline = MagicMock()
    inbox = MagicMock()
    inbox.trace_id = "test-trace"

    orch_result = OrchestratorResult(success=True, intents=[])

    with patch("src.workline_runtime.runtime_intent_effects.RuntimeIntentEffectApplier") as mock_applier_class:
        mock_applier = AsyncMock()
        mock_applier_class.return_value = mock_applier

        await orchestrator_write_back_service.write_back(
            db=mock_db,
            session=session,
            workline=workline,
            inbox=inbox,
            devices_by_role={"TEST_ROLE": [MagicMock()]},
            source_device=MagicMock(),
            orch_result=orch_result,
        )

        mock_applier.apply.assert_awaited_once()
        call_args = mock_applier.apply.call_args
        ctx = call_args[0][0]
        intents = call_args[0][1]

        assert ctx["db"] == mock_db
        assert ctx["session"] == session
        assert ctx["workline"] == workline
        assert ctx["inbox"] == inbox
        assert "TEST_ROLE" in ctx["devices_by_role"]
        assert ctx["orch_result"] == orch_result
        assert ctx["session_ctx"] == {"key": "value"}
        assert ctx["trace_id"] == "test-trace"
        assert intents == []
