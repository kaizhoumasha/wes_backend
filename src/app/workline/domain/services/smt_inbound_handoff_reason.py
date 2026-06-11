"""SMT 入库 handoff 受控原因码 catalog。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


class SmtInboundHandoffReasonCode(str, Enum):
    """SMT 入库 handoff 受控原因码。"""

    RELEASE_FACT_MISSING = "RELEASE_FACT_MISSING"
    RELEASE_SNAPSHOT_INVALID = "RELEASE_SNAPSHOT_INVALID"
    USAGE_INVALID = "USAGE_INVALID"
    WMS_RCS_REJECTED = "WMS_RCS_REJECTED"
    WMS_RCS_TIMEOUT = "WMS_RCS_TIMEOUT"
    WMS_RCS_RACK_RELEASE_ID_MISMATCH = "WMS_RCS_RACK_RELEASE_ID_MISMATCH"
    POST_EXCHANGE_RELATIONS_MISSING = "POST_EXCHANGE_RELATIONS_MISSING"
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
    TARGET_WORKLINE_NOT_READY = "TARGET_WORKLINE_NOT_READY"
    SOURCE_STATION_BUSY = "SOURCE_STATION_BUSY"
    TARGET_SESSION_BUSY = "TARGET_SESSION_BUSY"
    ECS_DEVICE_NOT_IDLE = "ECS_DEVICE_NOT_IDLE"
    SOURCE_ITEM_CLAIM_CONFLICT = "SOURCE_ITEM_CLAIM_CONFLICT"
    INTERNAL_INBOX_ENVELOPE_INVALID = "INTERNAL_INBOX_ENVELOPE_INVALID"
    SOURCE_PICK_EVENT_CREATE_FAILED = "SOURCE_PICK_EVENT_CREATE_FAILED"
    SOURCE_PICK_COMMAND_NOT_CREATED = "SOURCE_PICK_COMMAND_NOT_CREATED"
    SOURCE_PICK_INBOX_DEAD_LETTER = "SOURCE_PICK_INBOX_DEAD_LETTER"
    PLUGIN_CONTRACT_INVALID = "PLUGIN_CONTRACT_INVALID"


class SmtInboundHandoffReasonCategory(str, Enum):
    """原因码分类。"""

    RELEASE_FACT = "RELEASE_FACT"
    USAGE = "USAGE"
    WMS_RCS = "WMS_RCS"
    RECONCILE = "RECONCILE"
    ROUTE = "ROUTE"
    TARGET_BUSY = "TARGET_BUSY"
    ECS = "ECS"
    CLAIM = "CLAIM"
    PLUGIN = "PLUGIN"


class SmtInboundHandoffRecoverability(str, Enum):
    """原因码恢复属性。"""

    MANUAL = "MANUAL"
    RETRYABLE = "RETRYABLE"
    RECONCILE = "RECONCILE"


@dataclass(frozen=True)
class SmtInboundHandoffReasonDefinition:
    """单个 handoff 受控原因定义。"""

    code: SmtInboundHandoffReasonCode
    category: SmtInboundHandoffReasonCategory
    default_message: str
    available_actions: tuple[str, ...]
    recoverability: SmtInboundHandoffRecoverability

    @property
    def failure_code(self) -> str:
        """持久化到 demand/source item 的稳定 failure_code。"""

        return self.code.value


@dataclass(frozen=True)
class SmtInboundHandoffReasonCatalog:
    """SMT 入库 handoff 原因码查询表。"""

    reasons: tuple[SmtInboundHandoffReasonDefinition, ...]
    by_code: dict[str, SmtInboundHandoffReasonDefinition]
    by_category: dict[SmtInboundHandoffReasonCategory, tuple[SmtInboundHandoffReasonDefinition, ...]]

    def get(self, code: SmtInboundHandoffReasonCode | str) -> SmtInboundHandoffReasonDefinition:
        """按原因码读取定义。"""

        key = code.value if isinstance(code, SmtInboundHandoffReasonCode) else code
        return self.by_code[key]


_BUILTIN_REASONS: tuple[SmtInboundHandoffReasonDefinition, ...] = (
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.RELEASE_FACT_MISSING,
        category=SmtInboundHandoffReasonCategory.RELEASE_FACT,
        default_message="release fact 缺少创建 handoff demand 所需关键字段",
        available_actions=("REEVALUATE", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.MANUAL,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.RELEASE_SNAPSHOT_INVALID,
        category=SmtInboundHandoffReasonCategory.RELEASE_FACT,
        default_message="release 快照无效或缺少可分拣 source item evidence",
        available_actions=("REEVALUATE", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.MANUAL,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.USAGE_INVALID,
        category=SmtInboundHandoffReasonCategory.USAGE,
        default_message="release 快照 usage 无法按 0..1 口径计算",
        available_actions=("REEVALUATE", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.MANUAL,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.WMS_RCS_REJECTED,
        category=SmtInboundHandoffReasonCategory.WMS_RCS,
        default_message="WMS/RCS 拒绝满箱交换请求",
        available_actions=("CONVERT_TO_SORTING", "RETRY_EXCHANGE", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.MANUAL,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.WMS_RCS_TIMEOUT,
        category=SmtInboundHandoffReasonCategory.WMS_RCS,
        default_message="WMS/RCS 满箱交换回调超时",
        available_actions=("RETRY_EXCHANGE", "CONVERT_TO_SORTING", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.MANUAL,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.WMS_RCS_RACK_RELEASE_ID_MISMATCH,
        category=SmtInboundHandoffReasonCategory.WMS_RCS,
        default_message="WMS/RCS 回调 rack_release_id 与 demand 不一致",
        available_actions=("RECONCILE", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.RECONCILE,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.POST_EXCHANGE_RELATIONS_MISSING,
        category=SmtInboundHandoffReasonCategory.RECONCILE,
        default_message="满箱交换完成后缺少 post_exchange_relations 对账事实",
        available_actions=("RECONCILE", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.RECONCILE,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.ROUTE_NOT_FOUND,
        category=SmtInboundHandoffReasonCategory.ROUTE,
        default_message="未找到可承接 source item 的 SMT 入库分拣 WorkLine 配置候选",
        available_actions=("REEVALUATE", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.MANUAL,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.TARGET_WORKLINE_NOT_READY,
        category=SmtInboundHandoffReasonCategory.TARGET_BUSY,
        default_message="目标分拣 WorkLine 暂未 READY",
        available_actions=("REEVALUATE",),
        recoverability=SmtInboundHandoffRecoverability.RETRYABLE,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.SOURCE_STATION_BUSY,
        category=SmtInboundHandoffReasonCategory.TARGET_BUSY,
        default_message="source station lease 暂不可用",
        available_actions=("REEVALUATE",),
        recoverability=SmtInboundHandoffRecoverability.RETRYABLE,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.TARGET_SESSION_BUSY,
        category=SmtInboundHandoffReasonCategory.TARGET_BUSY,
        default_message="目标分拣 session 仍有未关闭 current_material",
        available_actions=("REEVALUATE",),
        recoverability=SmtInboundHandoffRecoverability.RETRYABLE,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.ECS_DEVICE_NOT_IDLE,
        category=SmtInboundHandoffReasonCategory.ECS,
        default_message="ECS realtime 准入结果不是 IDLE",
        available_actions=("REEVALUATE",),
        recoverability=SmtInboundHandoffRecoverability.RETRYABLE,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.SOURCE_ITEM_CLAIM_CONFLICT,
        category=SmtInboundHandoffReasonCategory.CLAIM,
        default_message="source item 并发 claim 冲突",
        available_actions=("REEVALUATE",),
        recoverability=SmtInboundHandoffRecoverability.RETRYABLE,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.INTERNAL_INBOX_ENVELOPE_INVALID,
        category=SmtInboundHandoffReasonCategory.CLAIM,
        default_message="内部 Inbox envelope 缺少可路由事件或 session/workline 归属",
        available_actions=("RETRY_SOURCE_PICK", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.MANUAL,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.SOURCE_PICK_EVENT_CREATE_FAILED,
        category=SmtInboundHandoffReasonCategory.CLAIM,
        default_message="SORTING_SOURCE_PICK_REQUESTED 内部事件创建失败",
        available_actions=("RETRY_SOURCE_PICK", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.MANUAL,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.SOURCE_PICK_COMMAND_NOT_CREATED,
        category=SmtInboundHandoffReasonCategory.PLUGIN,
        default_message="内部事件已处理但未创建或未写回 SORTING_SOURCE_PICK command evidence",
        available_actions=("RETRY_SOURCE_PICK", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.MANUAL,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.SOURCE_PICK_INBOX_DEAD_LETTER,
        category=SmtInboundHandoffReasonCategory.CLAIM,
        default_message="SORTING_SOURCE_PICK_REQUESTED Inbox 已进入死信",
        available_actions=("RETRY_SOURCE_PICK", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.MANUAL,
    ),
    SmtInboundHandoffReasonDefinition(
        code=SmtInboundHandoffReasonCode.PLUGIN_CONTRACT_INVALID,
        category=SmtInboundHandoffReasonCategory.PLUGIN,
        default_message="插件事件处理合同无效",
        available_actions=("RETRY_SOURCE_PICK", "RELEASE_HOLD"),
        recoverability=SmtInboundHandoffRecoverability.MANUAL,
    ),
)


def build_smt_inbound_handoff_reason_catalog(
    extra_reasons: Iterable[SmtInboundHandoffReasonDefinition] = (),
) -> SmtInboundHandoffReasonCatalog:
    """构建 SMT 入库 handoff 原因码 catalog。"""

    reasons = _BUILTIN_REASONS + tuple(extra_reasons)
    by_code: dict[str, SmtInboundHandoffReasonDefinition] = {}
    grouped: dict[SmtInboundHandoffReasonCategory, list[SmtInboundHandoffReasonDefinition]] = defaultdict(list)
    for reason in reasons:
        failure_code = reason.failure_code
        if failure_code in by_code:
            raise ValueError(f"duplicate SMT inbound handoff reason code: {failure_code}")
        by_code[failure_code] = reason
        grouped[reason.category].append(reason)

    return SmtInboundHandoffReasonCatalog(
        reasons=reasons,
        by_code=by_code,
        by_category={category: tuple(items) for category, items in grouped.items()},
    )


SMT_INBOUND_HANDOFF_REASON_CATALOG = build_smt_inbound_handoff_reason_catalog()


__all__ = [
    "SMT_INBOUND_HANDOFF_REASON_CATALOG",
    "SmtInboundHandoffReasonCatalog",
    "SmtInboundHandoffReasonCategory",
    "SmtInboundHandoffReasonCode",
    "SmtInboundHandoffReasonDefinition",
    "SmtInboundHandoffRecoverability",
    "build_smt_inbound_handoff_reason_catalog",
]
