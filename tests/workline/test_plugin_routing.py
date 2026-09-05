"""插件专属后续处理必须按原 Epoch 精确路由。"""

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.app.execution.plugin_binding import PluginRuntimeBinding
from src.app.workline.installed_plugin import InstalledWorkLinePlugin
from src.app.workline.models.workline import LineType
from src.app.workline.plugin_routing import InstalledPluginTransportOutcomePublisher, InstalledPluginWmsFollowUpPlanner


class _Sessions:
    @asynccontextmanager
    async def begin(self):  # type: ignore[no-untyped-def]
        yield object()


class _Repository:
    def __init__(self, value: object | None) -> None:
        self.value = value

    async def get_by_id(self, _db: object, _id: int) -> object | None:
        return self.value

    async def get_by_client_request_id(self, _db: object, _client_request_id: str) -> object | None:
        return self.value


class _FollowUpPlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def plan(self, _db: object, _confirmation: object, **_kwargs: object) -> object:
        self.calls += 1
        return SimpleNamespace(operation_id="FOLLOW-UP")


class _OutcomePublisher:
    def __init__(self) -> None:
        self.outcomes: list[object] = []

    async def publish(self, outcome: object) -> None:
        self.outcomes.append(outcome)


def _plugin(*, version: str, planner: object | None = None, publisher: object | None = None) -> InstalledWorkLinePlugin:
    return InstalledWorkLinePlugin(
        display_name="Example",
        runtime_binding=PluginRuntimeBinding(
            plugin_key="example",
            plugin_version=version,
            handlers=(),
            fact_factory=object(),  # type: ignore[arg-type]
        ),
        start_plan_builder=object(),
        supported_line_types=(LineType.AUTO,),
        wms_confirmation_follow_up_planner=planner,
        transport_outcome_publisher=publisher,
    )


@pytest.mark.asyncio
async def test_wms_follow_up_uses_the_original_executions_exact_epoch_plugin() -> None:
    planner = _FollowUpPlanner()
    router = InstalledPluginWmsFollowUpPlanner(
        (_plugin(version="1.0", planner=planner),),
        execution_repository=_Repository(SimpleNamespace(line_run_epoch_id=31)),  # type: ignore[arg-type]
        epoch_repository=_Repository(SimpleNamespace(plugin_key="example", plugin_version="1.0")),  # type: ignore[arg-type]
    )

    result = await router.plan(
        object(),
        SimpleNamespace(material_execution_id=21),  # type: ignore[arg-type]
        response_result="WAIT",
        retry_after_ms=1000,
        received_at=datetime(2026, 9, 5),
    )

    assert result.operation_id == "FOLLOW-UP"  # type: ignore[union-attr]
    assert planner.calls == 1


@pytest.mark.asyncio
async def test_transport_outcome_uses_the_binding_epochs_exact_plugin() -> None:
    publisher = _OutcomePublisher()
    router = InstalledPluginTransportOutcomePublisher(
        _Sessions(),
        (_plugin(version="1.0", publisher=publisher),),
        binding_repository=_Repository(SimpleNamespace(line_run_epoch_id=31)),  # type: ignore[arg-type]
        epoch_repository=_Repository(SimpleNamespace(plugin_key="example", plugin_version="1.0")),  # type: ignore[arg-type]
    )
    outcome = SimpleNamespace(client_request_id="REQUEST-1", caller=SimpleNamespace(workline_id="7"))

    await router.publish(outcome)  # type: ignore[arg-type]

    assert publisher.outcomes == [outcome]


@pytest.mark.asyncio
async def test_plugin_specific_continuation_does_not_fall_back_to_another_version() -> None:
    router = InstalledPluginTransportOutcomePublisher(
        _Sessions(),
        (_plugin(version="2.0", publisher=_OutcomePublisher()),),
        binding_repository=_Repository(SimpleNamespace(line_run_epoch_id=31)),  # type: ignore[arg-type]
        epoch_repository=_Repository(SimpleNamespace(plugin_key="example", plugin_version="1.0")),  # type: ignore[arg-type]
    )
    outcome = SimpleNamespace(client_request_id="REQUEST-1", caller=SimpleNamespace(workline_id="7"))

    with pytest.raises(LookupError, match=r"example@1\.0"):
        await router.publish(outcome)  # type: ignore[arg-type]
