# Mock 设备服务

用于 E2E 测试的模拟设备服务。

## 快速启动（本地开发）

```bash
# 1. 确保 WES 服务运行
docker compose --env-file .env.dev --profile dev up -d

# 2. 启动 Mock 服务（自动检测 WES）
./tests/mock/smt_classifier/start_local.sh
```

启动的服务：
- PIPELINE01 (8005) - 流水线
- ARM01 (8006) - 进料机械臂
- ARM02 (8007) - 出料机械臂

## 服务列表

| 服务 | 端口 | 描述 |
|------|------|------|
| 摄像头 Mock | 8003 | 模拟流水线识别点摄像头（含传感器模拟） |
| 机械臂 Mock | 8004 | 模拟搬运机械臂设备（含调试接口） |
| SMT 流水线 Mock | 8005 | 模拟 SMT 粗分机流水线 |
| SMT 进料臂 Mock (ARM01) | 8006 | 模拟进料机械臂（扫码、检测、搬运、NG 放置） |
| SMT 出料臂 Mock (ARM02) | 8007 | 模拟出料机械臂（从流水线到 BIN） |

## SMT 粗分机 Mock 服务

### ARM01 - 进料机械臂

支持的任务类型：
- `PICK_AND_PUT` - 抓取放置
- `PICK_NG` - NG 放置

支持的位置类型：
- 源：`INPUT_PLATFORM`, `PIPELINE_PLATFORM`
- 目标：`PIPELINE_PLATFORM`, `NG_PLATFORM`

调试接口：
- `POST /debug/scan-completed` - 模拟扫码完成事件
- `POST /debug/inspection-completed` - 模拟检测完成事件
- `POST /debug/execute` - 手动执行命令
- `GET /debug/executions` - 获取执行历史

简化调用示例：
```bash
# 执行默认搬运（INPUT_PLATFORM → PIPELINE_PLATFORM）
curl -X POST http://localhost:8006/debug/execute -H 'Content-Type: application/json' -d '{}'

# 仅指定条码
curl -X POST http://localhost:8006/debug/execute -H 'Content-Type: application/json' -d '{"barcode": "TEST-001"}'

# 模拟扫码完成（最小化）
curl -X POST http://localhost:8006/debug/scan-completed -H 'Content-Type: application/json' -d '{"barcode": "TEST-001"}'

# 模拟扫码 NG
curl -X POST http://localhost:8006/debug/scan-completed -H 'Content-Type: application/json' -d '{"barcode": "TEST-NG", "result": "NG"}'

# 模拟检测完成（最小化，OK 结果）
curl -X POST http://localhost:8006/debug/inspection-completed -H 'Content-Type: application/json' -d '{}'

# 模拟检测 NG
curl -X POST http://localhost:8006/debug/inspection-completed -H 'Content-Type: application/json' -d '{"result": "NG"}'

# NG 放置流程
curl -X POST http://localhost:8006/debug/execute -H 'Content-Type: application/json' -d '{"target_type": "NG_PLATFORM"}'
```

### ARM02 - 出料机械臂

支持的任务类型：
- `PICK_AND_PUT` - 抓取放置
- `OUTPUT` - 出料到 BIN

支持的位置类型：
- 源：`PIPELINE_PLATFORM`
- 目标：`BIN`

简化调用示例：
```bash
# 执行默认出料（PIPELINE_PLATFORM → BIN）
curl -X POST http://localhost:8007/debug/execute -H 'Content-Type: application/json' -d '{}'

# 执行 OUTPUT 任务
curl -X POST http://localhost:8007/debug/execute -H 'Content-Type: application/json' -d '{"task_type": "OUTPUT"}'
```

### PIPELINE01 - 流水线

支持的任务类型：
- `MOVE_FORWARD` - 向前传输

简化调用示例：
```bash
# 执行默认传输
curl -X POST http://localhost:8005/debug/execute -H 'Content-Type: application/json' -d '{}'

# 模拟传输失败
curl -X POST http://localhost:8005/debug/execute -H 'Content-Type: application/json' -d '{"simulate_failure": true}'
```

## Docker 使用

### 构建镜像

```bash
docker build -t wes-mock:latest -f tests/mock/Dockerfile .
```

### 使用 Docker Compose 启动

```bash
# 启动 E2E 测试环境（包含 Mock 服务）
docker compose --profile e2e --env-file .env.test up -d

# 仅启动 Mock 服务
docker compose --profile e2e up mock_camera mock_robot_arm

# 查看日志
docker compose --profile e2e logs -f mock_camera mock_robot_arm

# 停止服务
docker compose --profile e2e down
```

### 使用启动脚本

```bash
# 启动 E2E 环境
./scripts/start_e2e_env.sh up

# 查看状态
./scripts/start_e2e_env.sh status

# 查看日志
./scripts/start_e2e_env.sh logs

# 运行测试
./scripts/start_e2e_env.sh test

# 停止环境
./scripts/start_e2e_env.sh down
```

## 直接运行（开发环境）

```bash
# 运行摄像头 Mock
python tests/mock/camera_mock_server.py
# 或
uv run python tests/mock/camera_mock_server.py

# 运行机械臂 Mock
python tests/mock/robot_arm_mock_server.py
# 或
uv run python tests/mock/robot_arm_mock_server.py
```

## API 接口

### 摄像头 Mock (端口 8003)

#### 设备接口

- `GET /` - 健康检查
- `GET /api/v1/device/status` - 设备状态查询

#### 传感器模拟接口

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/v1/sensor/trigger` | POST | 手动触发传感器检测物料到达 |
| `/api/v1/sensor/auto/start` | POST | 启动自动触发 |
| `/api/v1/sensor/auto/stop` | POST | 停止自动触发 |
| `/api/v1/sensor/status` | GET | 获取传感器状态 |
| `/api/v1/sensor/events` | GET | 获取事件历史记录 |

#### 手动触发传感器

```bash
curl -X POST http://localhost:8003/api/v1/sensor/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "barcode": "PKG-TEST-001",
    "location": "CONVEYOR-STATION-01"
  }'
```

**请求参数**：
- `barcode` (可选): 条码，不提供则自动生成
- `location` (可选): 位置，默认 `CONVEYOR-STATION-01`
- `simulate_scan` (可选): 是否模拟扫码，默认 `true`

**响应示例**：
```json
{
  "event_id": "EVT-20250302123456-000",
  "device_id": "CAMERA-CONVEYOR-01",
  "event_type": "MATERIAL_ARRIVED",
  "barcode": "PKG-TEST-001",
  "location": "CONVEYOR-STATION-01",
  "timestamp": 1709397600000,
  "reported_at": "2025-03-02T12:34:56"
}
```

#### 启动自动触发

```bash
curl -X POST http://localhost:8003/api/v1/sensor/auto/start \
  -H "Content-Type: application/json" \
  -d '{
    "interval_seconds": 10,
    "barcode_prefix": "PKG",
    "location": "CONVEYOR-STATION-01",
    "max_triggers": 5
  }'
```

**请求参数**：
- `interval_seconds` (可选): 触发间隔（秒），默认 `10`
- `barcode_prefix` (可选): 条码前缀，默认 `PKG`
- `location` (可选): 位置，默认 `CONVEYOR-STATION-01`
- `max_triggers` (可选): 最大触发次数，不限制则为 `null`

#### 停止自动触发

```bash
curl -X POST http://localhost:8003/api/v1/sensor/auto/stop
```

#### 获取传感器状态

```bash
curl http://localhost:8003/api/v1/sensor/status
```

**响应示例**：
```json
{
  "is_auto_triggering": false,
  "trigger_count": 10,
  "current_config": {
    "wes_callback_url": "http://localhost:8001/api/v1/callback/event",
    "default_interval": 10,
    "barcode_prefix": "PKG"
  }
}
```

#### 获取事件历史

```bash
curl http://localhost:8003/api/v1/sensor/events?limit=50
```

### 机械臂 Mock (端口 8004)

#### 设备接口

| 端点 | 方法 | 描述 |
|------|------|------|
| `GET /` | - | 健康检查 |
| `GET /api/v1/device/status` | - | 设备状态查询 |
| `POST /api/v1/device/command` | - | 接收设备指令 |
| `POST /api/v1/device/cancel` | - | 取消执行指令 |

#### 调试接口（新增）

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/v1/robot/execute` | POST | 手动执行搬运指令 |
| `/api/v1/robot/auto/start` | POST | 启动自动执行 |
| `/api/v1/robot/auto/stop` | POST | 停止自动执行 |
| `/api/v1/robot/status` | GET | 获取执行状态 |
| `/api/v1/robot/executions` | GET | 获取执行历史记录 |

#### 手动执行指令

```bash
curl -X POST http://localhost:8004/api/v1/robot/execute \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "PICK_AND_PLACE",
    "source_loc": "CONVEYOR-STATION-01",
    "target_loc": "SHELF-A-01",
    "barcode": "PKG-TEST-001"
  }'
```

**请求参数**：
- `task_type` (可选): 任务类型，默认 `PICK_AND_PLACE`
- `source_loc` (可选): 源位置，默认 `CONVEYOR-STATION-01`
- `target_loc` (可选): 目标位置，默认 `SHELF-A-01`
- `barcode` (可选): 条码，不提供则自动生成
- `simulate_failure` (可选): 是否模拟失败，默认 `false`
- `execution_time` (可选): 执行时间（秒），默认 `2.0`

**响应示例**：
```json
{
  "execution_id": "EXEC-20250302123456-000",
  "command_id": "CMD-20250302123456-001",
  "task_type": "PICK_AND_PLACE",
  "source_loc": "CONVEYOR-STATION-01",
  "target_loc": "SHELF-A-01",
  "barcode": "PKG-TEST-001",
  "result": "SUCCESS",
  "error_detail": null,
  "started_at": "2025-03-02T12:34:56",
  "finished_at": "2025-03-02T12:34:58",
  "duration_ms": 2000
}
```

#### 启动自动执行

```bash
curl -X POST http://localhost:8004/api/v1/robot/auto/start \
  -H "Content-Type: application/json" \
  -d '{
    "interval_seconds": 5,
    "source_location": "CONVEYOR-STATION-01",
    "target_locations": ["SHELF-A-01", "SHELF-B-01"],
    "max_executions": 3
  }'
```

**请求参数**：
- `interval_seconds` (可选): 执行间隔（秒），默认 `5`
- `source_location` (可选): 源位置，默认 `CONVEYOR-STATION-01`
- `target_locations` (可选): 目标位置列表，默认 `["SHELF-A-01", "SHELF-B-01"]`
- `barcode_prefix` (可选): 条码前缀，默认 `PKG`
- `max_executions` (可选): 最大执行次数，不限制则为 `null`

#### 停止自动执行

```bash
curl -X POST http://localhost:8004/api/v1/robot/auto/stop
```

#### 获取执行状态

```bash
curl http://localhost:8004/api/v1/robot/status
```

**响应示例**：
```json
{
  "is_auto_executing": false,
  "execution_count": 10,
  "success_count": 9,
  "failure_count": 1,
  "current_command": null,
  "current_config": {
    "wes_callback_url": "http://localhost:8001/api/v1/callback/result",
    "default_interval": 5,
    "barcode_prefix": "PKG"
  }
}
```

#### 获取执行历史

```bash
curl http://localhost:8004/api/v1/robot/executions?limit=10
```

#### 模拟失败场景

```bash
curl -X POST http://localhost:8004/api/v1/robot/execute \
  -H "Content-Type: application/json" \
  -d '{
    "source_loc": "LOC-001",
    "target_loc": "LOC-002",
    "simulate_failure": true
  }'
```

## 完整数据流

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           传感器触发流程                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  1. 触发源                                                              │
│     ├─ 手动: POST /api/v1/sensor/trigger                               │
│     └─ 自动: 定时循环 (asyncio.create_task)                            │
│                                                                         │
│  2. 传感器模拟器                                                         │
│     ├─ 生成条码 (PKG20250302001)                                        │
│     ├─ 构建事件数据                                                     │
│     └─ 调用 WES 回调接口                                                │
│                                                                         │
│  3. WES 事件接收                                                         │
│     ├─ POST /api/v1/callback/event                                     │
│     ├─ 立即返回 ACK (200 OK)                                           │
│     └─ 写入 WorklineInbox 并触发 process_inbox_batch                    │
│                                                                         │
│  4. WES 业务处理                                                         │
│     ├─ 恢复 WorklineSession / 调用插件                                  │
│     ├─ 创建搬运指令记录                                                 │
│     └─ 下发指令到机械臂                                                  │
│                                                                         │
│  5. 机械臂执行                                                           │
│     ├─ 接收指令 (POST /api/v1/device/command)                          │
│     ├─ 模拟执行 (2 秒)                                                  │
│     └─ 回调结果 (POST /api/v1/callback/result)                          │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## 环境变量

| 变量 | 默认值 | 描述 |
|------|--------|------|
| `MOCK_CAMERA_PORT` | 8003 | 摄像头 Mock 服务端口 |
| `MOCK_ROBOT_ARM_PORT` | 8004 | 机械臂 Mock 服务端口 |
| `WES_EVENT_CALLBACK_URL` | http://localhost:8001/api/v1/callback/event | WES 事件回调地址 |
| `WES_CALLBACK_URL` | http://localhost:8001/api/v1/callback/result | WES 结果回调地址 |
| `API_APP_ID` | app_Gqnvr3dpjGwlrjtO | 设备 API 应用 ID（用于回调认证） |
| `API_APP_SECRET` | sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao | 设备 API 应用密钥（用于签名计算） |
| `SENSOR_AUTO_TRIGGER_DEFAULT_INTERVAL` | 10 | 传感器默认触发间隔（秒） |
| `ROBOT_AUTO_EXECUTE_DEFAULT_INTERVAL` | 5 | 机械臂默认执行间隔（秒） |
| `SENSOR_BARCODE_PREFIX` | PKG | 条码生成前缀 |
| `ROBOT_BARCODE_PREFIX` | PKG | 机械臂条码前缀 |

### API 认证说明

Mock 服务使用 `API_APP_ID` 和 `API_APP_SECRET` 对 WES 回调接口进行签名认证：

**签名计算方式**：
```python
import hmac
import hashlib
import time

# 签名字符串格式
sign_string = f"{app_id}{timestamp}{method}{path}"

# 使用 HMAC-SHA256 计算
signature = hmac.new(
    app_secret.encode("utf-8"),
    sign_string.encode("utf-8"),
    hashlib.sha256
).hexdigest()

# 请求 Header
headers = {
    "X-App-ID": app_id,
    "X-Timestamp": str(int(time.time())),  # 秒级时间戳
    "X-Signature": signature,
}
```

**示例**：
```python
# 调用 POST /api/v1/callback/result
app_id = "app_Gqnvr3dpjGwlrjtO"
app_secret = "sec_fqYNIij1ZD8aekbn0AONhk_H7VAzj5gEpcMC9d__tao"
timestamp = "1702627200"
method = "POST"
path = "/api/v1/callback/result"

sign_string = f"{app_id}{timestamp}{method}{path}"
# = "app_Gqnvr3dpjGwlrjtO1702627200POST/api/v1/callback/result"

signature = hmac.new(
    app_secret.encode("utf-8"),
    sign_string.encode("utf-8"),
    hashlib.sha256
).hexdigest()
```

## 健康检查

```bash
# 检查摄像头服务
curl http://localhost:8003/api/v1/device/status

# 检查摄像头传感器状态
curl http://localhost:8003/api/v1/sensor/status

# 检查机械臂服务
curl http://localhost:8004/api/v1/device/status

# 检查机械臂执行状态
curl http://localhost:8004/api/v1/robot/status
```

## 环境准备

在运行 E2E 测试前，需要先初始化测试数据：

```bash
# 方式 1：使用便捷脚本
bash scripts/data/seed_e2e_test_data.sh

# 方式 2：直接运行 Python 脚本
uv run python scripts/data/seed_e2e_test_data.py

# 方式 3：在 Docker 容器内运行
docker compose exec api uv run python scripts/data/seed_e2e_test_data.py
```

这将创建以下 E2E 测试数据：
- **作业线**: `WL-CONVEYOR-01` - 测试流水线作业线
- **设备**:
  - `CAMERA-CONVEYOR-01` - 流水线识别点摄像头 (127.0.0.1:8003)
  - `ROBOT-ARM-01` - 搬运机械臂 (127.0.0.1:8004)
- **API 应用**: `app_Gqnvr3dpjGwlrjtO` - Mock 服务器认证凭据

> 💡 此脚本可重复运行，已存在的数据会被跳过（幂等设计）。
>
> ⚠️ 系统初始化数据（用户/角色/权限）请使用 `scripts/data/seed_initial_data.py`

## 测试用例

### 摄像头传感器测试

#### 单次触发测试

```bash
# 1. 启动所有服务
./scripts/start_e2e_env.sh up

# 2. 手动触发传感器
curl -X POST http://localhost:8003/api/v1/sensor/trigger \
  -H "Content-Type: application/json" \
  -d '{"barcode": "PKG-TEST-001"}'

# 3. 查看传感器状态
curl http://localhost:8003/api/v1/sensor/status

# 4. 查看事件历史
curl http://localhost:8003/api/v1/sensor/events
```

#### 自动触发测试

```bash
# 启动自动触发（3次，间隔5秒）
curl -X POST http://localhost:8003/api/v1/sensor/auto/start \
  -H "Content-Type: application/json" \
  -d '{"interval_seconds": 5, "max_triggers": 3}'

# 查询状态
curl http://localhost:8003/api/v1/sensor/status

# 停止自动触发
curl -X POST http://localhost:8003/api/v1/sensor/auto/stop
```

### 机械臂调试测试

#### 手动执行测试

```bash
# 1. 启动所有服务
./scripts/start_e2e_env.sh up

# 2. 手动执行搬运指令
curl -X POST http://localhost:8004/api/v1/robot/execute \
  -H "Content-Type: application/json" \
  -d '{
    "source_loc": "CONVEYOR-STATION-01",
    "target_loc": "SHELF-A-01",
    "barcode": "PKG-TEST-001"
  }'

# 3. 查看执行状态
curl http://localhost:8004/api/v1/robot/status

# 4. 查看执行历史
curl http://localhost:8004/api/v1/robot/executions
```

#### 自动执行测试

```bash
# 启动自动执行（5次，间隔3秒）
curl -X POST http://localhost:8004/api/v1/robot/auto/start \
  -H "Content-Type: application/json" \
  -d '{
    "interval_seconds": 3,
    "source_location": "CONVEYOR-STATION-01",
    "target_locations": ["SHELF-A-01", "SHELF-B-01", "SHELF-C-01"],
    "max_executions": 5
  }'

# 查询状态
curl http://localhost:8004/api/v1/robot/status

# 停止自动执行
curl -X POST http://localhost:8004/api/v1/robot/auto/stop
```

#### 失败模拟测试

```bash
# 模拟执行失败
curl -X POST http://localhost:8004/api/v1/robot/execute \
  -H "Content-Type: application/json" \
  -d '{
    "source_loc": "LOC-001",
    "target_loc": "LOC-002",
    "simulate_failure": true
  }'

# 查看失败记录
curl http://localhost:8004/api/v1/robot/executions
```

### E2E 测试

```bash
# 运行完整流程测试
pytest tests/e2e/test_conveyor_robot_arm.py::test_full_conveyor_workflow -v -s

# 运行自动触发测试
pytest tests/e2e/test_conveyor_robot_arm.py::test_sensor_auto_trigger -v -s

# 运行事件历史测试
pytest tests/e2e/test_conveyor_robot_arm.py::test_sensor_events_history -v -s

# 运行所有测试
pytest tests/e2e/test_conveyor_robot_arm.py -v -s
```

## 白皮书对应

本实现遵循《第三方设备接入白皮书》第 3 节"设备接口规范"：

- **3.1 节**：设备端接口（指令接收、状态查询）
- **3.2 节**：WES 回调接口（事件上报、结果回调）
- **3.3 节**：传感器触发模式

## 架构设计

### 摄像头 Mock 服务架构

```
CameraMockServer
    ├── FastAPI 应用
    │   ├── 设备接口
    │   │   ├── GET /api/v1/device/status
    │   │   └── GET /
    │   └── 传感器接口
    │       ├── POST /api/v1/sensor/trigger
    │       ├── POST /api/v1/sensor/auto/start
    │       ├── POST /api/v1/sensor/auto/stop
    │       ├── GET /api/v1/sensor/status
    │       └── GET /api/v1/sensor/events
    └── SensorSimulator
        ├── trigger_material_arrival()  # 触发单个事件
        ├── start_auto_trigger()        # 启动自动触发
        ├── stop_auto_trigger()         # 停止自动触发
        ├── _generate_barcode()         # 生成条码
        ├── _report_event_to_wes()      # 上报事件到 WES
        ├── get_status()                # 获取状态
        └── get_events()                # 获取事件历史
```

### 机械臂 Mock 服务架构

```
RobotArmMockServer
    ├── FastAPI 应用
    │   ├── 设备接口（白皮书标准）
    │   │   ├── POST /api/v1/device/command  # 接收指令
    │   │   ├── POST /api/v1/device/cancel   # 取消指令
    │   │   ├── GET /api/v1/device/status    # 状态查询
    │   │   └── GET /
    │   └── 调试接口（新增）
    │       ├── POST /api/v1/robot/execute      # 手动执行
    │       ├── POST /api/v1/robot/auto/start   # 启动自动执行
    │       ├── POST /api/v1/robot/auto/stop    # 停止自动执行
    │       ├── GET /api/v1/robot/status        # 获取状态
    │       └── GET /api/v1/robot/executions    # 获取历史
    ├── RobotSimulator（新增）
    │   ├── execute_command()              # 执行单个指令
    │   ├── start_auto_execution()         # 启动自动执行
    │   ├── stop_auto_execution()          # 停止自动执行
    │   ├── _callback_to_wes()             # 回调结果到 WES
    │   ├── _generate_command_id()         # 生成指令 ID
    │   ├── get_status()                   # 获取状态
    │   └── get_executions()               # 获取执行历史
    └── 指令执行逻辑（WES 下发指令专用）
        ├── execute_command()              # 异步执行指令
        └── send_result_to_wes()           # 回调结果到 WES
```

## 版本历史

| 日期 | 版本 | 变更内容 |
|------|------|----------|
| 2025-03-02 | v2.0.0 | 机械臂 Mock 服务添加调试接口（手动/自动执行、状态查询、执行历史） |
| 2025-03-01 | v1.0.0 | 摄像头 Mock 服务添加传感器模拟接口 |
