"""WMS typed-operation 测试与 conformance runner 共享的 fixture matrix。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.app.wms_integration.operation_contract import WmsOperationDefinition


class RejectFixture(BaseModel):
    """operation-specific 最小业务拒绝 fixture。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_identity: str
    reason_code: str


class IdentityMismatchFixture(BaseModel):
    """声明 identity 与返回 identity 不一致的最小 fixture。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_operation_identity: str
    actual_operation_identity: str


@dataclass(frozen=True, slots=True)
class OperationFixture:
    """单 operation 的四类最小资产。"""

    operation: WmsOperationDefinition
    request: BaseModel
    result: BaseModel
    reject: RejectFixture
    identity_mismatch: IdentityMismatchFixture


def _fixture_index(name: str, pairs: Iterable[tuple[str, dict[str, Any]]], expected: tuple[str, ...]) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for identity, fixture in pairs:
        if identity in indexed:
            raise ValueError(f"{name} fixture identities contain duplicate operation: {identity}")
        indexed[identity] = fixture
    if tuple(indexed) != expected:
        raise ValueError(f"{name} fixture identities must exactly match the operation registry")
    return indexed


def build_operation_fixture_matrix(
    *,
    operations: tuple[WmsOperationDefinition, ...],
    request_fixtures: Iterable[tuple[str, dict[str, Any]]],
    result_fixtures: Iterable[tuple[str, dict[str, Any]]],
    reject_fixtures: Iterable[tuple[str, dict[str, Any]]],
    identity_mismatch_fixtures: Iterable[tuple[str, dict[str, Any]]],
) -> tuple[OperationFixture, ...]:
    """collection/parameter generation 前 fail closed 校验四类 fixture。"""

    expected = tuple(operation.identity for operation in operations)
    if len(expected) != len(set(expected)):
        raise ValueError("operation fixture identities contain duplicate operation")
    requests = _fixture_index("request", request_fixtures, expected)
    results = _fixture_index("result", result_fixtures, expected)
    rejects = _fixture_index("reject", reject_fixtures, expected)
    mismatches = _fixture_index("identity mismatch", identity_mismatch_fixtures, expected)
    matrix = []
    for operation in operations:
        reject = RejectFixture.model_validate(rejects[operation.identity])
        mismatch = IdentityMismatchFixture.model_validate(mismatches[operation.identity])
        if reject.operation_identity != operation.identity or reject.reason_code not in operation.reject_codes:
            raise ValueError(f"reject fixture identity or reason mismatch: {operation.identity}")
        if (
            mismatch.expected_operation_identity != operation.identity
            or mismatch.actual_operation_identity == operation.identity
        ):
            raise ValueError(f"identity mismatch fixture is not mismatched: {operation.identity}")
        matrix.append(
            OperationFixture(
                operation=operation,
                request=operation.request_model.model_validate(requests[operation.identity]),
                result=operation.result_model.model_validate(results[operation.identity]),
                reject=reject,
                identity_mismatch=mismatch,
            )
        )
    return tuple(matrix)


__all__ = [
    "IdentityMismatchFixture",
    "OperationFixture",
    "RejectFixture",
    "build_operation_fixture_matrix",
]
