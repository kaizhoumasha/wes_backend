"""退料架整架任务完成的 OpenAPI，直接复用严格 DTO。"""

from .wire import RETURN_RACK_TASK_COMPLETED_OPERATION, ReturnRackTaskCompletedEvent

RETURN_RACK_TASK_COMPLETED_SCHEMA = ReturnRackTaskCompletedEvent.model_json_schema()
RETURN_RACK_TASK_COMPLETED_SCHEMA["properties"]["data"] = RETURN_RACK_TASK_COMPLETED_SCHEMA.pop("$defs")[
    "ReturnRackTaskCompletedData"
]
RETURN_RACK_TASK_COMPLETED_SCHEMA["properties"]["operation"]["enum"] = [RETURN_RACK_TASK_COMPLETED_OPERATION]
RETURN_RACK_TASK_COMPLETED_SCHEMA["properties"]["timestamp"].update(
    format="int64", description="Unix 毫秒时间戳", minimum=1
)
RETURN_RACK_TASK_COMPLETED_EXAMPLE = {
    "summary": "退料货架整架业务完成（Evidence 接收 ACK）",
    "value": {
        "operation_id": "019f3404-a100-7b01-8b01-000000000099",
        "operation": "outbound.return_rack.task_completed@v1",
        "timestamp": 1788390200000,
        "data": {
            "task_id": "PICK-SWAGGER-001",
            "rack_id": "R1",
            "through_plan_revision": 2,
            "completed_at": 1788390199000,
        },
    },
}
