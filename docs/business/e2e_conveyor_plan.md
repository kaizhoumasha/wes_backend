# E2E 场景测试实施计划：流水线料盘搬运（当前实现版）

## 概述

模拟流水线场景：料盘到达识别点 -> 摄像头发送事件 -> WES 指挥机械臂搬运 -> 机械臂回调结果。

## 字段约定（必须统一）

- 外部接口（HTTP 回调、Mock 设备、处理器决策输出）统一使用 `device_code`。
- 内部数据库关联（`device_commands.device_id`、`wes_runtime.runtime_inbox.device_id`）统一使用 `device_id`（整数主键）。
- Celery 在 ACT 阶段负责将 `device_code` 解析为内部 `device_id` 后再创建设备指令。

---

## 一、架构设计

### 1.1 异步事件驱动流程

```
Step 1 设备事件上报（同步 ACK，快速返回）
POST /api/v1/callback/event
  -> callback_event:
     1) 记录回调日志和审计日志
     2) 写入 RuntimeInbox(kind=DEVICE_EVENT)
     3) 提交 Celery 任务 process_runtime_inbox_batch
     4) 立即返回 Event received

Step 2 统一编排异步处理（process_runtime_inbox_batch）
  INGRESS:
    1) 消费 RuntimeInbox
    2) 解析 device -> workline -> plugin
    3) 基于 workline / upstream_device_id 恢复或创建 WorklineSession
  DECIDE:
    4) 插件根据当前事件和 Session 状态做业务决策
    5) 解析下游设备，生成 DeviceCommand / SystemOutbox
  ACT:
    6) OutboxDispatchService 下发设备命令
    7) 持久化 Session / Timeline / DeviceCommand / Outbox

Step 3 设备结果回调
POST /api/v1/callback/result
  -> callback_result:
     1) 先按 command_code 命中既有 DeviceCommand，继承 correlation_id
     2) 写入 RuntimeInbox(kind=COMMAND_RESULT)
     3) 同步更新 DeviceCommand 结果状态（控制流证据）
     4) 再次提交 Celery 任务 process_runtime_inbox_batch
```

---

## 二、关键实现清单（当前代码）

### 2.1 核心任务链路

- `src/app/callback/v1/callback.py`
  - `/event`：写 `RuntimeInbox(DEVICE_EVENT)` 并触发 `src.celery_app.tasks.runtime_inbox.process_runtime_inbox_batch`
  - `/result`：写 `RuntimeInbox(COMMAND_RESULT)`、更新 `DeviceCommand` 并触发统一编排
- `src/celery_app/tasks/runtime_inbox.py`
  - `process_runtime_inbox_batch`：统一编排主流程入口
  - `_load_related_entities`：解析 `device -> workline -> session` 归属
- `src/app/device/services/device_command_service.py`
  - `send_command`：下发指令并更新 ACK 状态
  - `handle_callback_result`：落单条命令控制流结果

### 2.2 Celery 配置

- `src/celery_app/app.py`
  - include `src.celery_app.tasks.workline`
- `src/celery_app/config.py`
  - 作业线任务路由到 `celery` 队列

### 2.3 E2E 与 Mock

- `tests/mock/camera_mock_server.py`
- `tests/mock/robot_arm_mock_server.py`
- `tests/e2e/test_conveyor_robot_arm.py`

---

## 三、测试验证步骤

### 3.1 环境准备

```bash
# 1. 启动基础设施（PostgreSQL + Redis）
docker-compose up -d

# 2. 运行数据库迁移
./scripts/migrate.sh upgrade

# 3. 启动 WES 服务（终端1）
uv run uvicorn main:app --reload --port 8001

# 4. 启动作业线 Celery Worker（终端2）
uv run celery -A src.celery_app.app worker --loglevel=info --pool=solo --queues=default,celery

# 5. 启动 Mock 服务（终端3、4）
uv run python tests/mock/camera_mock_server.py
uv run python tests/mock/robot_arm_mock_server.py
```

### 3.2 设备注册（外部统一用 device_code）

```bash
# 注册摄像头（输送线识别点）
curl -X POST http://localhost:8001/api/v1/devices \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "CAMERA-CONVEYOR-01",
    "device_name": "流水线识别点摄像头",
    "device_type": "CONVEYOR",
    "host": "127.0.0.1",
    "port": 8003
  }'

# 注册机械臂
curl -X POST http://localhost:8001/api/v1/devices \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "ROBOT-ARM-01",
    "device_name": "搬运机械臂",
    "device_type": "ROBOTIC_ARM",
    "host": "127.0.0.1",
    "port": 8004
  }'
```

### 3.3 模拟料盘到达事件（外部回调）

```bash
curl -X POST http://localhost:8001/api/v1/callback/event \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "CAMERA-CONVEYOR-01",
    "event_type": "MATERIAL_ARRIVED",
    "timestamp": 1709097600000,
    "data": {
      "location": "CONVEYOR-STATION-01",
      "LotCode": "SMTLOT20250228001",
      "DateCode": "20250228",
      "Qty": "100",
      "ProductNo": "PN001",
      "MfrPN": "MFR002",
      "PONumber": "PO20250228001"
    }
  }'
```

### 3.4 预期结果

1. 回调接口立即返回（`message=Event received`，`status=submitted`）。
2. Celery Worker 日志显示 `process_runtime_inbox_batch` 被执行。
3. 机械臂 Mock 服务收到 `/api/v1/device/command` 请求。
4. `device_commands` 有记录，状态到达 `ACK_RECEIVED`。
5. 机械臂回调结果后，状态更新为 `COMPLETED`。

---

## 四、数据库状态验证

### 4.1 设备状态

```sql
SELECT id, device_code, device_name, device_status, is_active, current_command_id
FROM wes_biz.devices
WHERE device_code IN ('CAMERA-CONVEYOR-01', 'ROBOT-ARM-01');
```

### 4.2 事件入口记录（内部 `device_id`，联表看 `device_code`）

```sql
SELECT wi.id, wi.device_id, d.device_code, wi.kind, wi.status, wi.received_at,
       wi.payload_json ->> 'event_type' AS event_type
FROM wes_runtime.runtime_inbox wi
JOIN wes_biz.devices d ON d.id = wi.device_id
WHERE wi.kind = 'DEVICE_EVENT'
ORDER BY wi.id DESC;
```

### 4.3 指令记录（内部 `device_id`，联表看 `device_code`）

```sql
SELECT dc.command_code, dc.device_id, d.device_code, dc.task_type, dc.status, dc.sent_at, dc.ack_received_at, dc.completed_at
FROM wes_biz.device_commands dc
JOIN wes_biz.devices d ON d.id = dc.device_id
ORDER BY dc.id DESC;
```

---

## 五、验证检查点

- [ ] 设备注册成功（`devices` 表有两条记录，`device_code` 正确）
- [ ] 摄像头上报事件成功（`callback_logs` 与 `wes_runtime.runtime_inbox` 表有记录）
- [ ] Celery 任务执行成功（Worker 日志显示任务完成）
- [ ] 搬运指令创建成功（`device_commands` 有记录，状态到 `ACK_RECEIVED`）
- [ ] 机械臂收到指令（Mock 服务日志显示收到请求）
- [ ] 机械臂回调结果成功（状态变为 `COMPLETED`）

---

## 六、可选监控

```bash
celery -A src.celery_app.app flower
# 访问 http://localhost:5555 查看任务状态
```
