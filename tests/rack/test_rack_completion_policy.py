from types import SimpleNamespace

from src.app.rack.models import RackOperationStatus, RackTaskStatus
from src.app.rack.services.completion_policy import (
    derive_required_task_status,
    requires_resource_projection_confirmation,
    resolve_operation_completion_policy,
    resolve_request_completion_policy,
)
from src.app.sys.models import OperationCompletionPolicy


def test_completion_policy_module_resolves_request_policy_defaults_to_projection() -> None:
    assert resolve_request_completion_policy(None) == OperationCompletionPolicy.RESOURCE_PROJECTION_REQUIRED
    assert (
        resolve_request_completion_policy(OperationCompletionPolicy.CALLBACK_TRUSTED)
        == OperationCompletionPolicy.CALLBACK_TRUSTED
    )


def test_completion_policy_module_derives_required_task_status_before_projection() -> None:
    assert derive_required_task_status([]) == RackOperationStatus.PENDING
    assert (
        derive_required_task_status([SimpleNamespace(task_status=RackTaskStatus.FAILED)]) == RackOperationStatus.FAILED
    )
    assert (
        derive_required_task_status([SimpleNamespace(task_status=RackTaskStatus.RECONCILING)])
        == RackOperationStatus.RECONCILING
    )
    assert (
        derive_required_task_status([SimpleNamespace(task_status=RackTaskStatus.REQUESTED)])
        == RackOperationStatus.PENDING
    )
    assert derive_required_task_status([SimpleNamespace(task_status=RackTaskStatus.SUCCEEDED)]) is None


def test_completion_policy_module_resolves_persisted_policy_safely() -> None:
    assert (
        resolve_operation_completion_policy(
            SimpleNamespace(completion_policy=OperationCompletionPolicy.CALLBACK_PLUS_RECONCILIATION.value)
        )
        == OperationCompletionPolicy.CALLBACK_PLUS_RECONCILIATION
    )
    assert (
        resolve_operation_completion_policy(SimpleNamespace(completion_policy="UNKNOWN"))
        == OperationCompletionPolicy.RESOURCE_PROJECTION_REQUIRED
    )
    assert requires_resource_projection_confirmation(OperationCompletionPolicy.RESOURCE_PROJECTION_REQUIRED) is True
    assert requires_resource_projection_confirmation(OperationCompletionPolicy.CALLBACK_TRUSTED) is False
