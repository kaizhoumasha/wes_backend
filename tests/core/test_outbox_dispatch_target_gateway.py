"""G3 commit 后精确 Outbox target 唤醒合同。"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

import src.core.task_queue_gateway as gateway_module


class _CapturingGateway(gateway_module.CeleryTaskQueueGateway):
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def _send_task(self, task_name: str, *, kwargs: dict[str, Any]) -> None:
        self.sent.append((task_name, kwargs))


def test_enqueue_outbox_requires_explicit_targets_without_implicit_system_default() -> None:
    signature = inspect.signature(gateway_module.TaskQueueGateway.enqueue_outbox)

    assert "targets" in signature.parameters
    assert signature.parameters["targets"].default is inspect.Parameter.empty


def test_gateway_deduplicates_public_targets_and_maps_fulfillment_to_its_own_task() -> None:
    target_type = getattr(gateway_module, "OutboxDispatchTarget", None)
    assert target_type is not None, "G3 public OutboxDispatchTarget is missing"
    assert tuple(target_type.__members__) == ("SYSTEM", "WMS_DATA", "WMS_FULFILLMENT")
    gateway = _CapturingGateway()

    gateway.enqueue_outbox(
        targets=(
            target_type.WMS_FULFILLMENT,
            target_type.WMS_DATA,
            target_type.WMS_FULFILLMENT,
            target_type.SYSTEM,
        ),
        limit=23,
    )

    assert gateway.sent == [
        (gateway_module.DISPATCH_SYSTEM_OUTBOX_TASK, {"limit": 23}),
        (gateway_module.DISPATCH_WMS_DATA_OUTBOX_TASK, {"limit": 23}),
        (gateway_module.DISPATCH_WMS_FULFILLMENT_OUTBOX_TASK, {"limit": 23}),
    ]
    assert all("target" not in kwargs for _task, kwargs in gateway.sent)


def test_gateway_empty_explicit_target_set_is_a_noop() -> None:
    target_type = getattr(gateway_module, "OutboxDispatchTarget", None)
    if target_type is None:
        pytest.fail("G3 public OutboxDispatchTarget is missing", pytrace=False)
    gateway = _CapturingGateway()

    gateway.enqueue_outbox(targets=(), limit=50)

    assert gateway.sent == []


def test_gateway_enqueues_transport_evidence_with_its_fixed_batch_limit() -> None:
    gateway = _CapturingGateway()

    assert tuple(inspect.signature(gateway_module.TaskQueueGateway.enqueue_transport_evidence).parameters) == ("self",)
    gateway.enqueue_transport_evidence()

    assert gateway.sent == [("src.celery_app.tasks.transport.process_transport_evidence_batch", {"limit": 100})]


def test_gateway_enqueues_device_command_dispatch_with_fixed_batch_limit() -> None:
    gateway = _CapturingGateway()

    assert tuple(inspect.signature(gateway_module.TaskQueueGateway.enqueue_device_commands).parameters) == ("self",)
    gateway.enqueue_device_commands()

    assert gateway.sent == [("src.celery_app.tasks.device_command.dispatch_device_commands_batch", {"limit": 100})]


def test_gateway_enqueues_incident_owned_safety_drain_without_snapshot_payload() -> None:
    gateway = _CapturingGateway()

    assert tuple(inspect.signature(gateway_module.TaskQueueGateway.enqueue_safety_drain).parameters) == ("self",)
    gateway.enqueue_safety_drain()

    assert gateway.sent == [
        (
            "src.celery_app.tasks.workline.drain_safety_incidents_batch",
            {"limit": 10, "command_limit": 100},
        )
    ]


def test_gateway_execution_and_wms_wakes_are_exactly_two_no_payload_methods() -> None:
    gateway = _CapturingGateway()

    assert tuple(inspect.signature(gateway_module.TaskQueueGateway.enqueue_execution_facts).parameters) == ("self",)
    assert tuple(inspect.signature(gateway_module.TaskQueueGateway.enqueue_wms_confirmations).parameters) == ("self",)
    gateway.enqueue_execution_facts()
    gateway.enqueue_wms_confirmations()

    assert gateway.sent == [
        ("src.celery_app.tasks.execution.process_execution_facts_batch", {}),
        ("src.celery_app.tasks.wms_confirmation.dispatch_wms_confirmations_batch", {}),
    ]
