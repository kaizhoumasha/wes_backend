# E2E 场景测试实施计划：流水线料盘搬运

## 概述

模拟流水线场景：料盘到达识别点 → 摄像头发送事件 → WES指挥机械臂搬运 → 机械臂回调结果

---

## 一、架构设计

### 1.1 异步事件驱动流程

```
┌─────────────────────────────────────────────────────────────────┐
│              Step 1: 设备事件上报（同步，<50ms）                  │
│                  POST /api/v1/callback/event                    │
│                  ⚡ 立即返回 ACK（不等待业务处理）                 │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ↓
┌─────────────────────────────────────────────────────────────────┐
│          DeviceCallbackService.handle_event_callback             │
│  1. 验证设备存在                                                 │
│  2. 记录事件到 device_events 表                                  │
│  3. 发布 SSE 通知                                                │
│  4. ⭐ 触发 Celery 异步任务（立即返回）                           │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    ┌────────┴────────┐
                    │                 │
                    ↓                 ↓
            [立即返回200 OK]   [Celery 异步处理]
                                        │
                    ┌───────────────────┴───────────────────┐
                    │   Step 2: Celery 异步任务处理          │
                    │   task: process_material_arrived       │
                    └───────────────────┬───────────────────┘
                                        │
                                        ↓
                    ┌───────────────────────────────────────────┐
                    │      ConveyorWorkflowService              │
                    │  1. 解析事件数据（barcode, location）     │
                    │  2. 验证 barcode 非空                     │
                    │  3. 确定目标位置（固定：SHELF-A-01）      │
                    │  4. 创建搬运指令                          │
                    │  5. 下发指令到机械臂                      │
                    └───────────────────┬───────────────────────┘
                                        │
                                        ↓
                    ┌───────────────────────────────────────────┐
                    │      DeviceCommandService.send_command    │
                    │  1. 构建指令 Payload                      │
                    │  2. HTTP POST 到机械臂                    │
                    │  3. 解析 ACK 响应                         │
                    │  4. 更新指令和设备状态                    │
                    └───────────────────────────────────────────┘
```

---

## 二、实现任务清单

### 2.1 核心业务逻辑开发（必须实现）

#### 文件1: `src/celery_app/tasks/device.py`（新建）

**职责**：处理设备事件的 Celery 任务

**关键实现**：

```python
class DeviceTask(Task):
    """设备任务基类 - 自动处理数据库会话"""
    @property
    def db(self):
        if self._db is None:
            from src.database.db import AsyncSessionLocal
            self._db = AsyncSessionLocal()
        return self._db

@celery_app.task(
    name="src.celery_app.tasks.device.process_material_arrived",
    base=DeviceTask,
    bind=True,
    max_retries=3,
)
def process_material_arrived(self, event_data: dict):
    """处理料盘到达事件"""
    # 1. 解析事件数据
    # 2. 验证 barcode
    # 3. 创建搬运指令
    # 4. 下发指令
```

#### 文件2: `src/app/device/services/device_service.py`（修改）

**修改位置**：第470行 TODO 处

**修改内容**：

```python
# 在 handle_event_callback 方法中，TODO 后添加：
if request.event_type == "MATERIAL_ARRIVED":
    from src.celery_app.tasks.device import process_material_arrived

    task_params = {
        "device_id": request.device_id,
        "event_type": request.event_type,
        "barcode": request.data.get("barcode") if request.data else None,
        "location": request.data.get("location") if request.data else None,
    }

    process_material_arrived.apply_async(
        args=[task_params],
        task_id=f"event-{event.id}",
    )
```

#### 文件3: `src/celery_app/app.py`（修改）

**修改内容**：添加设备任务模块到 include 列表

```python
include=[
    "src.celery_app.tasks.core",
    "src.celery_app.tasks.device",  # 新增
]
```

#### 文件4: `src/celery_app/config.py`（修改）

**修改内容**：添加设备任务路由配置

```python
task_routes = {
    "src.celery_app.tasks.core.*": {"queue": "default"},
    "src.celery_app.tasks.device.*": {"queue": "device"},  # 新增
}
```

### 2.2 Mock 服务开发

#### 文件5: `tests/mock/camera_mock_server.py`（新建）

**职责**：模拟摄像头设备，接收状态查询

#### 文件6: `tests/mock/robot_arm_mock_server.py`（新建）

**职责**：模拟机械臂设备，接收指令、返回ACK、回调结果

### 2.3 E2E 测试

#### 文件7: `tests/e2e/test_conveyor_robot_arm.py`（新建）

**职责**：端到端测试完整流程

---

## 三、关键文件路径汇总

### 需要创建的文件

| 文件路径                                 | 说明                           |
| ---------------------------------------- | ------------------------------ |
| `src/celery_app/tasks/device.py`       | 设备事件处理Celery任务（核心） |
| `tests/mock/camera_mock_server.py`     | 摄像头Mock服务                 |
| `tests/mock/robot_arm_mock_server.py`  | 机械臂Mock服务                 |
| `tests/e2e/test_conveyor_robot_arm.py` | E2E测试脚本                    |
| `tests/mock/__init__.py`               | Mock模块初始化                 |

### 需要修改的文件

| 文件路径                                      | 修改内容                            |
| --------------------------------------------- | ----------------------------------- |
| `src/app/device/services/device_service.py` | 第470行TODO：添加Celery任务触发逻辑 |
| `src/celery_app/app.py`                     | 添加设备任务模块到include列表       |
| `src/celery_app/config.py`                  | 添加设备任务路由配置                |
| `src/celery_app/tasks/__init__.py`          | 导出新任务                          |

---

## 四、测试验证步骤

### 4.1 环境准备

```bash
# 1. 启动基础设施（PostgreSQL + Redis）
docker-compose up -d

# 2. 运行数据库迁移
./scripts/migrate.sh upgrade

# 3. 启动WES服务（终端1）
uvicorn main:app --reload

# 4. 启动Celery Worker（终端2）
uv run celery -A src.celery_app.app worker --loglevel=info --pool=solo --queues=default,celery,device

# 5. 启动Mock服务（终端3、4）
python tests/mock/camera_mock_server.py
python tests/mock/robot_arm_mock_server.py
```

### 4.2 设备注册

```bash
# 注册摄像头
curl -X POST http://localhost:8000/api/v1/devices \
  -H "Content-Type: application/json" \
  -d '{"device_id": "CAMERA-CONVEYOR-01", "device_name": "流水线识别点摄像头", "device_type": "CAMERA", "ip_address": "127.0.0.1", "port": 8001}'

# 注册机械臂
curl -X POST http://localhost:8000/api/v1/devices \
  -H "Content-Type: application/json" \
  -d '{"device_id": "ROBOT-ARM-01", "device_name": "搬运机械臂", "device_type": "ROBOTIC_ARM", "ip_address": "127.0.0.1", "port": 8002}'
```

### 4.3 模拟料盘到达事件

```bash
curl -X POST http://localhost:8000/api/v1/callback/event \
  -H "Content-Type: application/json" \
  -d '{"device_id": "CAMERA-CONVEYOR-01", "event_type": "MATERIAL_ARRIVED", "timestamp": 1709097600000, "data": {"location": "CONVEYOR-STATION-01", "barcode": "PKG20250228001"}}'
```

### 4.4 预期结果

1. 摄像头回调立即返回 `{"code": 200, "message": "ACK"}`
2. Celery Worker 日志显示任务执行
3. 机械臂 Mock 服务收到指令
4. `device_commands` 表有记录，status=ACKED
5. 机械臂回调结果后，status=COMPLETED

---

## 五、数据库状态验证

### 5.1 设备状态

```sql
SELECT device_id, device_name, status, is_online, current_command_id
FROM devices
WHERE device_id IN ('CAMERA-CONVEYOR-01', 'ROBOT-ARM-01');
```

### 5.2 事件记录

```sql
SELECT id, device_id, event_type, is_processed, created_at
FROM device_events
ORDER BY created_at DESC;
```

### 5.3 指令记录

```sql
SELECT command_id, device_id, task_type, status, sent_at, acked_at, completed_at
FROM device_commands
ORDER BY created_at DESC;
```

---

## 六、验证检查点

- [ ] 设备注册成功（数据库中有两条记录）
- [ ] 摄像头上报事件成功（device_events 表有记录）
- [ ] Celery任务执行成功（Worker日志显示任务完成）
- [ ] 搬运指令创建成功（device_commands 表有记录，status=ACKED）
- [ ] 机械臂收到指令（Mock服务日志显示收到请求）
- [ ] 机械臂返回ACK（Mock服务返回200）
- [ ] 机械臂回调结果成功（status=COMPLETED）
- [ ] 设备状态正确更新（ROBOT-ARM-01: IDLE→RUNNING→IDLE）

---

## 七、Celery 任务监控（可选）

```bash
# 启动 Flower 监控
celery -A src.celery_app.app flower
# 访问 http://localhost:5555 查看任务状态
```
