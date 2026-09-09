"""WMS 出库 PickingTask 入站事件的 OpenAPI 片段。"""

from __future__ import annotations

import json

from src.app.wms_adapter.outbound_picking.manual_bin_completed_wire import MANUAL_BIN_COMPLETED_OPERATION
from src.app.wms_adapter.outbound_picking.queue_changed_wire import PICKING_TASK_QUEUE_CHANGED_OPERATION
from src.app.wms_adapter.outbound_picking.wire import (
    BUSINESS_IDENTIFIER_PATTERN,
    PICKING_TASK_ISSUED_OPERATION,
)
from src.app.wms_adapter.wire_common import UUIDV7_PATTERN

_UUIDV7 = {"type": "string", "pattern": UUIDV7_PATTERN}
_TIMESTAMP = {
    "type": "integer",
    "format": "int64",
    "minimum": 1,
    "maximum": 2**63 - 1,
    "description": "Unix 毫秒时间戳",
}
_NONNEGATIVE_TIMESTAMP = {**_TIMESTAMP, "minimum": 0}
_POSITIVE_INTEGER = {"type": "integer", "minimum": 1, "maximum": 2**63 - 1}
_BUSINESS_IDENTIFIER = {"type": "string", "pattern": BUSINESS_IDENTIFIER_PATTERN}


def _closed(required: list[str], properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": True,
        "required": required,
        "properties": properties,
    }


PICKING_TASK_ISSUED_EVENT_REQUEST_SCHEMA = _closed(
    ["operation_id", "operation", "timestamp", "data"],
    {
        "operation_id": _UUIDV7,
        "operation": {"type": "string", "enum": [PICKING_TASK_ISSUED_OPERATION]},
        "timestamp": _TIMESTAMP,
        "data": _closed(
            ["task_id", "task_type", "queue_revision", "dispatch_sequence"],
            {
                "task_id": _BUSINESS_IDENTIFIER,
                "task_type": {"type": "string", "enum": ["MANUAL", "AUTO"]},
                "queue_revision": {"type": "integer", "minimum": 1, "maximum": 1},
                "dispatch_sequence": _POSITIVE_INTEGER,
                "not_before": _NONNEGATIVE_TIMESTAMP,
            },
        ),
    },
)

__all__ = [
    "MANUAL_BIN_COMPLETED_EVENT_EXAMPLE",
    "MANUAL_BIN_COMPLETED_EVENT_REQUEST_SCHEMA",
    "PICKING_TASK_EVENT_EXAMPLES",
    "PICKING_TASK_ISSUED_EVENT_REQUEST_SCHEMA",
    "PICKING_TASK_PLAN_DELTA_EVENT_REQUEST_SCHEMA",
    "PICKING_TASK_QUEUE_CHANGED_EVENT_REQUEST_SCHEMA",
    "PICKING_TASK_TEST_GUIDE",
]

# 计划增量保持独立 schema，公开 Event route 负责静态接入。
_RACK_FACE = {"type": "string", "minLength": 1, "maxLength": 10, "pattern": r"^[^\u0000\uD800-\uDFFF]+$"}
_PLAN_RACK = _closed(["rack_id", "rack_face"], {"rack_id": _BUSINESS_IDENTIFIER, "rack_face": _RACK_FACE})
_PLAN_SLOT = _closed(
    ["type", "rack_id", "rack_face", "slot_id"],
    {
        "type": {"type": "string", "enum": ["RACK_SLOT"]},
        "rack_id": _BUSINESS_IDENTIFIER,
        "rack_face": _RACK_FACE,
        "slot_id": _BUSINESS_IDENTIFIER,
    },
)
_PLAN_DELTA_DATA = _closed(
    ["task_id", "plan_revision"],
    {
        "task_id": _BUSINESS_IDENTIFIER,
        "plan_revision": _POSITIVE_INTEGER,
        "target_rack": _PLAN_RACK,
        "added_bin_source_racks": {"type": "array", "minItems": 1, "items": _PLAN_RACK},
        "added_direct_picks": {
            "type": "array",
            "minItems": 1,
            "items": _closed(["source_locator"], {"source_locator": _PLAN_SLOT}),
        },
    },
)
_PLAN_DELTA_DATA["oneOf"] = [
    {"properties": {"plan_revision": {"const": 1}}, "required": ["target_rack"]},
    {
        "properties": {"plan_revision": {"minimum": 2}},
        "not": {"required": ["target_rack"]},
        "anyOf": [{"required": ["added_bin_source_racks"]}, {"required": ["added_direct_picks"]}],
    },
]
PICKING_TASK_PLAN_DELTA_EVENT_REQUEST_SCHEMA = _closed(
    ["operation_id", "operation", "timestamp", "data"],
    {
        "operation_id": _UUIDV7,
        "operation": {"type": "string", "enum": ["outbound.picking_task.plan_delta@v1"]},
        "timestamp": _NONNEGATIVE_TIMESTAMP,
        "data": _PLAN_DELTA_DATA,
    },
)

_QUEUE_CHANGED_DATA = _closed(
    ["task_id", "queue_revision"],
    {
        "task_id": _BUSINESS_IDENTIFIER,
        "queue_revision": {**_POSITIVE_INTEGER, "minimum": 2},
        "dispatch_sequence": _POSITIVE_INTEGER,
        "not_before": _NONNEGATIVE_TIMESTAMP,
    },
)
_QUEUE_CHANGED_DATA["anyOf"] = [{"required": ["dispatch_sequence"]}, {"required": ["not_before"]}]
PICKING_TASK_QUEUE_CHANGED_EVENT_REQUEST_SCHEMA = _closed(
    ["operation_id", "operation", "timestamp", "data"],
    {
        "operation_id": _UUIDV7,
        "operation": {"type": "string", "enum": [PICKING_TASK_QUEUE_CHANGED_OPERATION]},
        "timestamp": _NONNEGATIVE_TIMESTAMP,
        "data": _QUEUE_CHANGED_DATA,
    },
)

MANUAL_BIN_COMPLETED_EVENT_REQUEST_SCHEMA = _closed(
    ["operation_id", "operation", "timestamp", "data"],
    {
        "operation_id": _UUIDV7,
        "operation": {"type": "string", "enum": [MANUAL_BIN_COMPLETED_OPERATION]},
        "timestamp": _TIMESTAMP,
        "data": _closed(
            ["task_id", "bin_code", "result", "completed_at"],
            {
                "task_id": _BUSINESS_IDENTIFIER,
                "bin_code": _BUSINESS_IDENTIFIER,
                "result": {"type": "string", "enum": ["NORMAL", "NG"]},
                "completed_at": _TIMESTAMP,
            },
        ),
    },
)

MANUAL_BIN_COMPLETED_EVENT_EXAMPLE = {
    "08_manual_bin_completed": {
        "summary": "8. 人工工作位 Bin 完成决定",
        "description": "引用已进入手工出库联调的 MANUAL PickingTask；completed_at 不得晚于 timestamp。",
        "value": {
            "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804e2",
            "operation": MANUAL_BIN_COMPLETED_OPERATION,
            "timestamp": 1786060807000,
            "data": {
                "task_id": "PICK-SWAGGER-001",
                "bin_code": "BIN-SWAGGER-001",
                "result": "NORMAL",
                "completed_at": 1786060806900,
            },
        },
    }
}

PICKING_TASK_TEST_GUIDE = """
WMS 调用 WES 的唯一 Event 入口，按 `operation` 选择严格合同。
支持 Transport 位置/结果、入库恢复决定，以及下方 PickingTask 发布、队列调整和计划增量。

### Examples 的范围

Request body 的 Examples 仅用于 WMS → WES 事件：1–4 为 PickingTask，5–6 为 Transport 回报，
7 为入库对账后继续执行，8 为人工工作位 Bin 完成决定。共覆盖当前入口支持的 7 种 operation，编号不是跨领域的连续业务流程。
Transport 示例必须引用已存在的任务与真实设备事实；入库恢复示例必须引用实际待对账执行与证据。
prepare、inbound_batch、material.decide、completion_confirm 等由 WES 调用 WMS，
正常返回见下方「WMS 正常业务响应」，不属于此 Event 入口的请求 Examples。

### 开始测试

1. 使用双方约定的隔离联调环境；`Try it out → Execute` 会发送真实请求并保存业务数据。
2. 在 **Examples** 中选择样例；如果尚未进入编辑模式，先点 **Try it out**。每轮新测试更换 `task_id`，每个新消息使用新的小写 UUIDv7
   `operation_id`，`timestamp` 使用 UTC Unix 毫秒。货架、面、储位必须换成双方约定的实际业务编码。
3. `Content-Type` 为 `application/json`，Body 上限 256 KiB，忽略冗余字段，校验已定义字段。WMS Event 当前采用隔离局域网 NONE
   认证，无需管理端 Bearer Token；401 表示入站策略未就绪，503 也可能表示对应运行时未就绪。

### PickingTask 顺序测试

| 步骤 | 操作 | 前置条件与预期 |
| --- | --- | --- |
| 1 | 选择「1. 发布新的 PickingTask」 | 新 `task_id`、`queue_revision=1`；首次 `202 / RECEIVED`，任务进入 QUEUED |
| 2（可选） | 选择「2. 调整队列」 | 同一任务仍为 QUEUED；使用新 identity 和更高 queue_revision，首次 `202 / RECEIVED` |
| 3 | 等待 WES 发起 `outbound.picking_task.prepare@v1` | WES 已通过实际业务入口选中任务和 WorkLine；WMS 在 decisions 端点返回 `202 / PREPARE_ACCEPTED`、相同 operation_id 和 `data={}` |
| 4 | 选择「3. 首批计划」发布 revision 1 | 同一任务已绑定且 WES 保存匹配的 prepare 成功响应；必须带 target_rack，成功 `202 / RECEIVED` |
| 5 | 选择「4. 追加来源」发布 revision 2 | revision 1 已获成功 ACK；只追加新来源，不再带 target_rack，成功 `202 / RECEIVED` |

**测试边界：** 发布任务不会自动证明工作线已启动。零业务插件环境可以验证 issued/queue_changed，
但不能仅靠这些 Swagger 请求让任务进入 PREPARING。步骤 3 未具备时，请先由 WES 联调人员确认实际 prepare 触发入口；
不要直接对 QUEUED 任务发送计划来模拟 prepare 成功。Operation 基础接入不等于工作线完整业务流程已启用。

### 预期正常结果（执行前对照）

下方 **Responses → 202 → Examples** 为每个请求提供对应的完整正常响应；
**Responses → 200 → Examples** 为同一请求提供原样重放的预期响应。
请选择与 Request body 相同的步骤名称；Swagger 不会自动联动两个选择器。
点击 Execute 后，实际结果显示在 **Server response**，可与预期 Example Value 对照。

| 请求样例 | 首次正常响应 | WES 应保存的结果 |
| --- | --- | --- |
| 1. 发布新的 PickingTask | `202 / RECEIVED`，`data={}` | 创建一个 QUEUED 任务，queue_revision=1、dispatch_sequence=10 |
| 2. 调整队列 | `202 / RECEIVED`，`data={}` | 同一任务仍为 QUEUED，queue_revision=2、dispatch_sequence=20、not_before=0（无延后准入） |
| 3. 首批计划 | `202 / RECEIVED`，`data={}` | 在 prepare 成功前提下原子保存 revision 1、接料架面和来源，任务进入 EXECUTING |
| 4. 追加来源 | `202 / RECEIVED`，`data={}` | last_applied_plan_revision=2，新来源只追加一次，原接料架面保持不变 |
| 上述任一步成功后原样重放 | `200 / DUPLICATE`，`data={}` | 不重复创建、不重复应用，保留首次接收时间 |

响应 `operation_id` 必须与所选请求相同，`timestamp` 是 WES 首次接收时间；示例时间不要求与实际值相等。
任务状态和计划成员是持久化结果，不额外出现在 ACK 的 `data` 中，由 WES 联调人员核对。
以上编码和序号按下方未修改的请求样例列出，修改请求后应按实际输入核对。

PickingTask 成功 ACK 的 `data` 为 `{}`，例如首次成功（响应 operation_id 必须匹配请求）：

```json
{"operation_id":"019f3400-0e17-7d2a-b944-3cf7953804da","code":"RECEIVED","timestamp":1786060800100,"data":{}}
```

### WMS 侧接口与后续流程

以下端点由 **WMS** 实现，不能在当前 WES 主机上 Execute。WES 调用固定的 WMS_BASE_URL；
WMS 工程师应在自己的服务中准备响应，并与 WES 联调人员观察真实请求。

| WES → WMS operation | WMS Method / Path | 成功响应与触发事实 |
| --- | --- | --- |
| `outbound.picking_task.prepare@v1` | POST `/api/v1/wes/decisions` | `202 / PREPARE_ACCEPTED`；随后 WMS 发布 plan_delta |
| `outbound.return_rack.arrival_report@v1` | POST `/api/v1/wes/facts` | 退料架确定到位后 `200 / RECORDED` |
| `outbound.bin.inbound_batch@v1` | POST `/api/v1/wes/decisions` | `200 / DECIDED`：READY / NO_BATCH / RACK_FACE_DONE |
| `outbound.bin.work_plan@v1` | POST `/api/v1/wes/decisions` | Bin 工作位扫码后 READY / NO_WORK / WAIT |
| `outbound.material.decide@v1` | POST `/api/v1/wes/decisions` | 料盘完整扫码后 ACCEPT / REJECT / WAIT |
| `outbound.source.empty_decide@v1` | POST `/api/v1/wes/decisions` | 确定空取后 RETRY / WAIT / SOURCE_DONE |
| `outbound.material.movement_report@v1` | POST `/api/v1/wes/facts` | 确定 PUT 或单盘 NG 放置后 `200 / RECORDED`；原样重放 DUPLICATE |
| `outbound.bin.return_batch@v1` | POST `/api/v1/wes/decisions` | 退箱 FIFO 候选 READY / NO_BATCH |
| `outbound.rack.departure_decide@v1` | POST `/api/v1/wes/decisions` | 货架满足离场条件后 READY / WAIT |

表中 decision 除 prepare 外均为 `200 / DECIDED`，分支值在 `data.result`。
WAIT / NO_BATCH 闭合本次可靠义务；后续业务重求值使用新 identity，时机由插件决定。
`outbound.picking_task.completion_confirm@v1` 已接入基础能力；本地完成条件与任务状态推进由插件负责。
上述流程依赖插件与实际设备证据；WMS ACK、READY 和 Swagger 请求成功均不证明设备已搬运或物料已放置。
"""

PICKING_TASK_EVENT_EXAMPLES = {
    "01_issued": {
        "summary": "1. 发布新的 PickingTask（首次 202，原样重放 200）",
        "value": {
            "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804da",
            "operation": PICKING_TASK_ISSUED_OPERATION,
            "timestamp": 1786060800000,
            "data": {
                "task_id": "PICK-SWAGGER-001",
                "task_type": "MANUAL",
                "queue_revision": 1,
                "dispatch_sequence": 10,
            },
        },
    },
    "02_queue_changed": {
        "summary": "2. 调整队列（同一任务必须仍为 QUEUED）",
        "value": {
            "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804db",
            "operation": PICKING_TASK_QUEUE_CHANGED_OPERATION,
            "timestamp": 1786060801000,
            "data": {"task_id": "PICK-SWAGGER-001", "queue_revision": 2, "dispatch_sequence": 20, "not_before": 0},
        },
    },
    "03_plan_first": {
        "summary": "3. 首批计划（先由 WES 发起并保存 prepare 成功）",
        "value": {
            "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804dc",
            "operation": "outbound.picking_task.plan_delta@v1",
            "timestamp": 1786060802000,
            "data": {
                "task_id": "PICK-SWAGGER-001",
                "plan_revision": 1,
                "target_rack": {"rack_id": "TARGET-RACK-01", "rack_face": "A"},
                "added_bin_source_racks": [{"rack_id": "SOURCE-RACK-01", "rack_face": "A"}],
            },
        },
    },
    "04_plan_append": {
        "summary": "4. 追加来源（revision 1 成功后再发送，不重复 target_rack）",
        "value": {
            "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804dd",
            "operation": "outbound.picking_task.plan_delta@v1",
            "timestamp": 1786060803000,
            "data": {
                "task_id": "PICK-SWAGGER-001",
                "plan_revision": 2,
                "added_direct_picks": [
                    {
                        "source_locator": {
                            "type": "RACK_SLOT",
                            "rack_id": "DIRECT-RACK-01",
                            "rack_face": "B",
                            "slot_id": "B-01",
                        }
                    }
                ],
            },
        },
    },
}

# 仅用于 Swagger 展示；不是运行时 operation 分派表。
_PICKING_TARGET = {"type": "RACK_SLOT", "rack_id": "TARGET-RACK-01", "rack_face": "A", "slot_id": "A-05"}
_RACK_DESTINATION = {"type": "RACK_POSITION", "location_code": "RACK-PARK-01"}
PICKING_TASK_WMS_RESPONSE_DATA = {
    "outbound.picking_task.completion_confirm@v1": [
        (200, "DECIDED", {"result": "COMPLETED"}),
        (200, "DECIDED", {"result": "PLAN_REVISION_STALE", "current_plan_revision": 6}),
        (200, "DECIDED", {"result": "BUSINESS_IN_PROGRESS", "retry_after_ms": 1000}),
    ],
    "outbound.picking_task.prepare@v1": [
        (202, "PREPARE_ACCEPTED", {}),
    ],
    "outbound.return_rack.arrival_report@v1": [
        (200, "RECORDED", {}),
        (200, "DUPLICATE", {}),
    ],
    "outbound.bin.inbound_batch@v1": [
        (
            200,
            "DECIDED",
            {
                "result": "READY",
                "bins": [
                    {
                        "bin_code": "BIN-001",
                        "source_locator": {
                            "type": "RACK_BIN_SLOT",
                            "rack_id": "SOURCE-RACK-01",
                            "rack_face": "A",
                            "slot_id": "A-01",
                        },
                    },
                ],
            },
        ),
        (200, "DECIDED", {"result": "NO_BATCH", "retry_after_ms": 1000}),
        (200, "DECIDED", {"result": "RACK_FACE_DONE"}),
    ],
    "outbound.bin.work_plan@v1": [
        (200, "DECIDED", {"result": "READY", "cell_ids": ["CELL-03", "CELL-04"]}),
        (200, "DECIDED", {"result": "NO_WORK"}),
        (200, "DECIDED", {"result": "WAIT", "retry_after_ms": 1000}),
    ],
    "outbound.material.decide@v1": [
        (200, "DECIDED", {"result": "ACCEPT", "target_locator": _PICKING_TARGET, "next_source_action": "CONTINUE"}),
        (200, "DECIDED", {"result": "ACCEPT", "target_locator": _PICKING_TARGET, "next_source_action": "SOURCE_DONE"}),
        (
            200,
            "DECIDED",
            {
                "result": "ACCEPT",
                "target_locator": _PICKING_TARGET,
                "next_source_action": "CONTINUE",
                "target_preparation": {"mode": "ROTATE"},
            },
        ),
        (
            200,
            "DECIDED",
            {
                "result": "ACCEPT",
                "target_locator": _PICKING_TARGET,
                "next_source_action": "CONTINUE",
                "target_preparation": {"mode": "REPLACE", "rack_destination": _RACK_DESTINATION},
            },
        ),
        (
            200,
            "DECIDED",
            {
                "result": "REJECT",
                "business_exception_code": "MATERIAL_REJECTED",
                "ng_locator": {"type": "NG_ZONE", "zone_code": "MATERIAL_NG_01"},
                "source_disposition": "CONTINUE",
            },
        ),
        (
            200,
            "DECIDED",
            {
                "result": "REJECT",
                "business_exception_code": "MATERIAL_REJECTED",
                "ng_locator": {"type": "NG_ZONE", "zone_code": "MATERIAL_NG_01"},
                "source_disposition": "CLOSE",
            },
        ),
        (
            200,
            "DECIDED",
            {
                "result": "REJECT",
                "business_exception_code": "SOURCE_CELL_MISMATCH",
                "ng_locator": {"type": "NG_ZONE", "zone_code": "CELL_NG_01"},
                "source_disposition": "CLOSE",
            },
        ),
        (200, "DECIDED", {"result": "WAIT", "retry_after_ms": 1000}),
    ],
    "outbound.source.empty_decide@v1": [
        (200, "DECIDED", {"result": "RETRY"}),
        (200, "DECIDED", {"result": "WAIT", "retry_after_ms": 1000}),
        (200, "DECIDED", {"result": "SOURCE_DONE"}),
    ],
    "outbound.material.movement_report@v1": [
        (200, "RECORDED", {}),
        (200, "DUPLICATE", {}),
    ],
    "outbound.bin.return_batch@v1": [
        (
            200,
            "DECIDED",
            {
                "result": "READY",
                "moves": [
                    {
                        "sequence_no": 1,
                        "bin_code": "BIN-001",
                        "target": {
                            "type": "RACK_BIN_SLOT",
                            "rack_id": "RETURN-RACK-01",
                            "rack_face": "A",
                            "slot_id": "A-01",
                        },
                    },
                ],
            },
        ),
        (200, "DECIDED", {"result": "NO_BATCH", "retry_after_ms": 1000}),
    ],
    "outbound.rack.departure_decide@v1": [
        (200, "DECIDED", {"result": "READY", "rack_destination": _RACK_DESTINATION}),
        (200, "DECIDED", {"result": "WAIT", "retry_after_ms": 1000}),
    ],
}
_PICKING_ERROR_RESPONSE_DATA = [
    (503, "UNAVAILABLE", {}),
    *[
        (409, "CONFLICT", {"reason_code": reason})
        for reason in (
            "IDEMPOTENCY_CONFLICT",
            "REVISION_CONFLICT",
            "STATE_CONFLICT",
            "REFERENCE_CONFLICT",
        )
    ],
    (422, "REJECTED", {"reason_code": "INVALID_ENVELOPE"}),
    (422, "REJECTED", {"reason_code": "UNSUPPORTED_OPERATION"}),
    (422, "REJECTED", {"reason_code": "INVALID_DATA", "field_path": "/data"}),
]
PICKING_TASK_WMS_RESPONSE_EXAMPLES = {
    operation: [
        (
            status,
            {
                "operation_id": "019f3400-0e17-7d2a-b944-3cf7953804de",
                "code": code,
                "timestamp": 1786060800100,
                "data": data,
            },
        )
        for status, code, data in variants
    ]
    for operation, variants in {
        **PICKING_TASK_WMS_RESPONSE_DATA,
        "通用错误响应（适用于上述 Operation）": _PICKING_ERROR_RESPONSE_DATA,
    }.items()
}
PICKING_TASK_TEST_GUIDE += """
### WMS 正常业务响应（WES → WMS 请求的返回值）

以下每个代码块都是完整响应体，可供 WMS 实现或 Mock 使用。下方只展开正常业务返回；
示例 operation_id 必须替换为当前 WES 请求的 operation_id，timestamp 为 WMS 响应时间。
这些不是下方 WMS → WES Event 接口的 Responses，不应粘贴到 Event 的请求框。

- decisions 原样重放应返回首次完整业务响应，不能以空 data 或 DUPLICATE 替代决定。
  facts 首次 RECORDED、重放 DUPLICATE；prepare 的 PREPARE_ACCEPTED 和 facts 的 data 按合同为空。
- inbound_batch 的 READY 返回 1–4 个唯一 Bin 和来源槽位，不能超过请求 max_bin_count，rack_id/rack_face 必须匹配请求。
  return_batch 的 READY 返回请求候选的 FIFO 前缀，sequence_no 从 1 连续，目标架面匹配请求且槽位唯一。
- work_plan 的 cell_ids 非空且不重复。departure 的目的地必须不同于当前货架位置。
- material ACCEPT 无准备动作时省略 target_preparation；ROTATE 使用目标架面，REPLACE 另带旧架离场目的地。
  Cell 来源可 CONTINUE 或 SOURCE_DONE；DirectPick 只能 SOURCE_DONE。
- empty_decide 的 RETRY 是重新尝试原来源空取；SOURCE_DONE 表示来源处理结束。
- completion_confirm 的 COMPLETED 表示 WMS 确认当前任务业务完成；本地触发条件和任务状态推进仍由插件负责。
"""
for _operation, _variants in PICKING_TASK_WMS_RESPONSE_EXAMPLES.items():
    if _operation.startswith("通用错误"):
        continue
    PICKING_TASK_TEST_GUIDE += f"\n#### {_operation}\n"
    for _status, _response in _variants:
        if _response["code"] == "DUPLICATE" or _response["data"].get("result") in {
            "WAIT",
            "NO_BATCH",
            "REJECT",
            "PLAN_REVISION_STALE",
            "BUSINESS_IN_PROGRESS",
        }:
            continue
        _data = _response["data"]
        _branch = _data.get("result", _data.get("reason_code", _response["code"]))
        PICKING_TASK_TEST_GUIDE += (
            f"\n**HTTP {_status} / {_response['code']} — {_branch}**\n\n"
            f"```json\n{json.dumps(_response, ensure_ascii=False, indent=2)}\n```\n"
        )

PICKING_TASK_TEST_GUIDE += """
### 其他返回（简要参考）

WAIT / NO_BATCH 表示暂时无可执行业务；REJECT 表示业务拒绝。这里不展开这些分支的 JSON。
原样重放不重复执行业务；技术重试保持原 operation_id 和完整请求。
409 / 422 为确定拒绝，503 或传输结果不明时保留原请求重试；具体错误格式见下方 Responses。
计划增量必须连续，冲突后应对账，不要通过跳版本或更换 ID 绕过。
"""
