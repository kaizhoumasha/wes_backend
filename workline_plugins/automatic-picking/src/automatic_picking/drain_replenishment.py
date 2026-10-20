"""正常补架的离线纯求值；不创建 intent、持久 current、计时器或物理动作。"""

from dataclasses import dataclass
from typing import Literal

from wes_plugin_sdk import (
    OperationConflict,
    OperationRejected,
    OperationUnavailable,
    ReturnBufferDrainReady,
    ReturnBufferDrainWait,
)


@dataclass(frozen=True, slots=True)
class DrainBufferFact:
    return_id: int
    workline_id: int
    location_code: str | None
    allocated: bool
    eligible: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class DrainReplenishmentFact:
    workline_id: int
    operation_id: str
    reservation_operation_id: str  # 显式给定的首次 READY 身份；仅局部快照，不是 wire/schema 变更。
    return_ids: tuple[int, ...]
    returned_ids: tuple[int, ...]  # 仅匹配原 execution 的权威成功及终位正事实。
    inflight_return_ids: tuple[int, ...]  # 已明确交接且已离开待派发 FIFO，不能由缺失记录推断。
    fifo: tuple[DrainBufferFact, ...]
    buffer_location_code: str
    outcome: (
        ReturnBufferDrainReady
        | ReturnBufferDrainWait
        | OperationUnavailable
        | OperationConflict
        | OperationRejected
        | None
    )
    outcome_operation_id: str | None
    published: bool
    departure_handoffs: tuple[tuple[str, str], ...]  # 当前决定的 rack → 原 CTU03；已承接面序及直接义务门禁。
    retry_at: int | None  # 显式冻结的毫秒到期点；求值不刷新它。
    now: int


@dataclass(frozen=True, slots=True)
class DrainReplenishmentEvaluation:
    # 瞬时求值分类，不是新增业务状态，也不授权调用方直接派发。
    action: Literal["RECOVER_CURRENT", "WAIT", "REQUEST", "EXIT_WAIT"]
    remaining_return_ids: tuple[int, ...]
    request_return_ids: tuple[int, ...] = ()


def _ids(values: tuple[int, ...]) -> set[int]:
    if type(values) is not tuple or any(type(i) is not int or not 0 < i < 2**63 for i in values):
        raise ValueError("return scope requires immutable positive execution IDs")
    result = set(values)
    if len(result) != len(values):
        raise ValueError("return scope contains duplicate executions")
    return result


def _request_prefix(fact: DrainReplenishmentFact, remaining: tuple[int, ...]) -> tuple[int, ...]:
    if type(fact.fifo) is not tuple or any(type(row) is not DrainBufferFact for row in fact.fifo):
        raise ValueError("FIFO requires immutable explicit facts")
    for row in fact.fifo:
        if (
            type(row.workline_id) is not int
            or row.workline_id != fact.workline_id
            or type(row.allocated) is not bool
            or type(row.eligible) is not bool
            or (row.location_code is not None and (type(row.location_code) is not str or not row.location_code.strip()))
        ):
            raise ValueError("FIFO identity or position differs from current WorkLine")
    fifo_ids = _ids(tuple(row.return_id for row in fact.fifo))
    original, inflight = set(fact.return_ids), set(fact.inflight_return_ids)
    queued = tuple(i for i in remaining if i not in inflight)
    if (
        fifo_ids & (set(fact.returned_ids) | inflight)
        or tuple(row.return_id for row in fact.fifo if row.return_id in original) != queued
    ):
        raise ValueError("current FIFO scope/order requires positive queue or handoff facts")
    prefix = []
    for row in fact.fifo:
        if (
            row.return_id not in original
            or row.allocated
            or not row.eligible
            or row.location_code != fact.buffer_location_code
        ):
            break
        prefix.append(row.return_id)
    return tuple(prefix)


def evaluate_replenishment(fact: DrainReplenishmentFact) -> DrainReplenishmentEvaluation:
    if (
        type(fact) is not DrainReplenishmentFact
        or type(fact.workline_id) is not int
        or not 0 < fact.workline_id < 2**63
        or type(fact.operation_id) is not str
        or not fact.operation_id.strip()
        or type(fact.buffer_location_code) is not str
        or not fact.buffer_location_code.strip()
        or type(fact.published) is not bool
        or type(fact.now) is not int
        or fact.now < 0
    ):
        raise ValueError("replenishment requires explicit current identity, buffer and time")
    original, returned, inflight = (_ids(ids) for ids in (fact.return_ids, fact.returned_ids, fact.inflight_return_ids))
    if type(fact.reservation_operation_id) is not str or not fact.reservation_operation_id.strip():
        raise ValueError("replenishment requires original READY reservation identity")
    if not original or not returned <= original or not inflight <= original - returned:
        raise ValueError("returned/inflight scope differs from original executions")
    remaining = tuple(i for i in fact.return_ids if i not in returned)

    result = fact.outcome
    if result is None:
        if fact.outcome_operation_id is not None or fact.published:
            raise ValueError("missing result cannot prove a published current identity")
    elif type(result) not in (
        ReturnBufferDrainReady,
        ReturnBufferDrainWait,
        OperationUnavailable,
        OperationConflict,
        OperationRejected,
    ):
        raise ValueError("current result requires an approved typed outcome")
    elif fact.outcome_operation_id != fact.operation_id:
        raise ValueError("result identity differs from current request")
    if type(result) is not ReturnBufferDrainReady and fact.operation_id == fact.reservation_operation_id:
        raise ValueError("a reservation requires a preceding READY, not an initial WAIT/unknown request")

    if type(fact.departure_handoffs) is not tuple:
        raise ValueError("handoffs require immutable original Transport identities")
    racks, transports = set(), set()
    for handoff in fact.departure_handoffs:
        if (
            type(handoff) is not tuple
            or len(handoff) != 2
            or any(type(value) is not str or not value.strip() for value in handoff)
            or handoff[0] in racks
            or handoff[1] in transports
        ):
            raise ValueError("handoffs require unique rack and original Transport identities")
        racks.add(handoff[0])
        transports.add(handoff[1])
    supplied = {rack.rack_id for rack in result.racks} if type(result) is ReturnBufferDrainReady else set()
    if not racks <= supplied:
        raise ValueError("handoffs differ from current supply decision")
    if (
        result is None
        or not fact.published
        or type(result) in (OperationUnavailable, OperationConflict, OperationRejected)
    ):
        return DrainReplenishmentEvaluation("RECOVER_CURRENT", remaining)
    if type(result) is ReturnBufferDrainWait:
        if type(fact.retry_at) is not int or fact.retry_at < 0:
            raise ValueError("published WAIT requires frozen retry_at")
        if not remaining:
            return DrainReplenishmentEvaluation("EXIT_WAIT", remaining)
        if fact.now < fact.retry_at:
            return DrainReplenishmentEvaluation("WAIT", remaining)
    elif racks != supplied:
        return DrainReplenishmentEvaluation("WAIT", remaining)

    prefix = _request_prefix(fact, remaining)
    return DrainReplenishmentEvaluation("REQUEST" if prefix else "WAIT", remaining, prefix)
