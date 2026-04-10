# Mock 服务增强功能说明

## 概述

SMT 粗分机 Mock 服务已增强，提供详细的业务流程日志和硬件错误模拟能力。

## 新增功能

### 1. 详细业务流程日志

Mock 服务现在会在关键业务节点打印详细日志，便于调试和验证流程。

#### ARM Mock 日志示例

```
============================================================
[进料机械臂] 收到 WES 命令
============================================================
  命令编号: CMD-20260409-PICK_AND_PLACE-123456
  任务类型: PICK_AND_PUT
  优先级: 1
  超时: 30000ms
  参数: {'barcode': 'LOTABC123', 'source_type': 'INPUT_PLATFORM', 'source_loc': 'STATION_INPUT1', ...}
============================================================
[进料机械臂] 开始异步执行命令...
[进料机械臂] 开始执行: CMD-20260409-PICK_AND_PLACE-123456
  任务类型: PICK_AND_PUT
  源位置: STATION_INPUT1 (INPUT_PLATFORM)
  目标位置: STATION_PIPELINE1_INPUT1 (PIPELINE_PLATFORM)
  执行时间: 2.0s
  条码: LOTABC123
[进料机械臂] 执行中...
[进料机械臂] 执行完成: CMD-20260409-PICK_AND_PLACE-123456
  结果: SUCCESS
  错误码: 0
  耗时: 2.0s
============================================================
[进料机械臂] 回调结果到 WES
============================================================
  命令编号: CMD-20260409-PICK_AND_PLACE-123456
  设备编号: ARM01
  执行结果: SUCCESS
  错误码: 0
  错误信息:
  回调地址: http://localhost:8001/api/v1/callback/result
============================================================
```

#### Pipeline Mock 日志示例

```
============================================================
[SMT 粗分机流水线] 收到 WES 命令
============================================================
  命令编号: CMD-20260409-PROCESS-789012
  任务类型: MOVE_FORWARD
  优先级: 1
  超时: 30000ms
  参数: {'barcode': 'LOTABC123', 'source_type': 'PIPELINE_PLATFORM', 'target_type': 'PIPELINE_PLATFORM'}
============================================================
[SMT 粗分机流水线] 开始异步执行命令...
[SMT 粗分机流水线] 开始执行: CMD-20260409-PROCESS-789012
  任务类型: MOVE_FORWARD
  源位置: STATION_PIPELINE1_INPUT1 (PIPELINE_PLATFORM)
  目标位置: STATION_PIPELINE1_OUTPUT1 (PIPELINE_PLATFORM)
  执行时间: 1.5s
[SMT 粗分机流水线] 执行中...
[SMT 粗分机流水线] 执行完成: CMD-20260409-PROCESS-789012
  结果: SUCCESS
  耗时: 1.5s
============================================================
[SMT 粗分机流水线] 回调结果到 WES
============================================================
  命令编号: CMD-20260409-PROCESS-789012
  设备编号: PIPELINE01
  执行结果: SUCCESS
  源位置: STATION_PIPELINE1_INPUT1
  目标位置: STATION_PIPELINE1_OUTPUT1
  回调地址: http://localhost:8001/api/v1/callback/result
============================================================
```

### 2. 硬件错误模拟

Mock 服务支持多种方式模拟硬件约定的错误码：

#### 支持的错误码（硬件约定）

| 错误码 | 说明 | 使用场景 |
|-------|------|---------|
| `1001` | 料盘尺寸检测异常 | 进料流程尺寸检测失败 |
| `1002` | 料盘厚度检测异常 | 进料流程厚度检测失败 |
| `2001` | 扫码异常 | 扫码失败 |
| `2002` | 搬运失败 | 机械臂搬运失败 |
| `2003` | 料箱已满 | 出料失败 |

#### 方式 1：智能条码模式（推荐用于测试）

Mock 服务会自动识别特殊条码模式并触发对应错误：

| 条码模式 | 自动触发的错误码 | 说明 |
|---------|----------------|------|
| 包含 `SIZENG` | `1001` | 尺寸检测异常 |
| 包含 `THICKNESSNG` | `1002` | 厚度检测异常 |

**使用示例**：

```python
# 自动触发尺寸检测失败（无需额外参数）
scan_event = {
    "device_code": "ARM01",
    "event_type": "SCAN_COMPLETED",
    "data": {
        "location": "STATION_INPUT1",
        "LotCode": "LOTSIZENG",  # 自动触发错误码 1001
    },
}

response = await wes_client.post("/api/v1/callback/event", json=scan_event)
# Mock 会自动返回 error_code=1001
```

#### 方式 2：显式 error_code 参数

在发送命令时，通过 `params.error_code` 指定错误码：

```python
import httpx
import time

# 模拟尺寸检测失败（错误码 1001）
command_payload = {
    "command_code": f"CMD-{int(time.time())}",
    "task_type": "PICK_AND_PUT",
    "priority": 1,
    "timeout": 30000,
    "params": {
        "barcode": "LOTTEST001",
        "source_type": "INPUT_PLATFORM",
        "target_type": "PIPELINE_PLATFORM",
        "source_loc": "STATION_INPUT1",
        "target_loc": "STATION_PIPELINE1_INPUT1",
        "error_code": "1001",  # 关键：模拟尺寸检测异常
    },
    "timestamp": int(time.time() * 1000),
}

async with httpx.AsyncClient() as client:
    response = await client.post(
        "http://127.0.0.1:8006/api/v1/device/command",
        json=command_payload,
    )
    print(response.json())
```

**优先级规则**：
- 显式 `params.error_code` 参数优先级高于智能条码模式
- 如果同时存在，使用显式指定的错误码

#### Mock 服务处理逻辑

当 Mock 服务收到包含 `error_code` 的命令时：

1. 提取 `params.error_code` 参数
2. 根据 error_code 映射错误信息：
   - `"1001"` → `"料盘尺寸检测异常"`
   - `"1002"` → `"料盘厚度检测异常"`
   - `"2002"` → `"搬运失败"`
3. 返回 FAILED 结果，携带对应的 error_detail

#### 错误日志示例

```
[进料机械臂] 模拟错误码: 1001
[进料机械臂] 执行完成: CMD-20260409-PICK_AND_PLACE-123456
  结果: FAILED
  错误码: 1001
  耗时: 2.0s
============================================================
[进料机械臂] 回调结果到 WES
============================================================
  命令编号: CMD-20260409-PICK_AND_PLACE-123456
  设备编号: ARM01
  执行结果: FAILED
  错误码: 1001
  错误信息: 料盘尺寸检测异常
  回调地址: http://localhost:8001/api/v1/callback/result
============================================================
```

### 3. 测试用例示例

#### 测试尺寸检测 NG 流程

```python
@pytest.mark.asyncio
async def test_size_detection_ng_flow(
    wes_client: httpx.AsyncClient,
    arm01_client: httpx.AsyncClient,
) -> None:
    """测试尺寸检测 NG 流程（错误码 1001）"""

    # 1. 上报扫码事件
    scan_event = {
        "device_code": "ARM01",
        "event_type": "SCAN_COMPLETED",
        "timestamp": int(time.time() * 1000),
        "data": {
            "location": "STATION_INPUT1",
            "LotCode": "LOTSIZENG",
        },
    }

    response = await wes_client.post("/api/v1/callback/event", json=scan_event)
    assert response.status_code == 200

    # 2. 等待插件处理（WES 会下发命令到 Mock）
    await asyncio.sleep(8)

    # 3. 验证数据库记录
    # 应该看到：进料命令失败（error_code=1001）→ NG 移料命令
```

## 完整业务流程测试

### 运行所有测试

```bash
# 运行完整的端到端业务流程测试
uv run pytest tests/e2e/smt_classifier/test_full_business_flows.py -v

# 运行单个测试
uv run pytest tests/e2e/smt_classifier/test_full_business_flows.py::TestInputOkFlow::test_full_ok_flow -xvs

# 查看详细日志
uv run pytest tests/e2e/smt_classifier/test_full_business_flows.py -xvs --log-cli-level=INFO
```

### 测试覆盖场景

1. ✅ **进料 OK 流程（尺寸检测 OK）**
   - 扫码 OK → 进料 → 移料 → 出料
   - 会话状态：COMPLETED
   - 命令序列：ARM01 → PIPELINE01 → ARM02

2. ✅ **进料 NG 流程（扫码 NG）**
   - 条码格式错误 → 立即失败
   - 会话状态：FAILED
   - 失败原因：DATA/BARCODE_INVALID

3. ⏭️ **进料 NG 流程（尺寸检测/测厚 NG）**
   - 需要 Mock 返回错误码 1001 或 1002
   - 测试已更新，但需要配置 Mock 参数

## 调试技巧

### 查看 Mock 服务日志

Mock 服务是独立进程，日志输出到终端。

如果使用 pytest 运行测试，Mock 服务日志会被捕获，可通过以下方式查看：

```bash
# 方式 1: 使用 pytest 的日志捕获
uv run pytest tests/e2e/smt_classifier/test_full_business_flows.py -xvs --log-cli-level=INFO

# 方式 2: 手动启动 Mock 服务并查看日志
python tests/mock/smt_classifier/run_all.py
# 在另一个终端运行测试
uv run pytest tests/e2e/smt_classifier/test_full_business_flows.py -xvs
```

### 查看数据库记录

```bash
# 查询最新会话状态
docker exec wes_postgres_dev psql -U wes_user -d wes_db -c \
  "SELECT id, status, step_code FROM wes_biz.workline_sessions ORDER BY id DESC LIMIT 1;"

# 查询命令执行记录
docker exec wes_postgres_dev psql -U wes_user -d wes_db -c \
  "SELECT dc.id, dc.command_code, dc.task_type, d.device_code, dc.status, dc.result
   FROM wes_biz.device_commands dc
   JOIN wes_biz.devices d ON dc.device_id = d.id
   WHERE dc.session_id = 'SESSION_ID'
   ORDER BY dc.id;"
```

## 相关文件

- Mock 服务代码：`tests/mock/smt_classifier/arm_mock.py`, `tests/mock/smt_classifier/pipeline_mock.py`
- 测试用例：`tests/e2e/smt_classifier/test_full_business_flows.py`
- 硬件接口规范：`docs/hardware/SMT粗分机接口调用说明书20260321-v1.md`

## 参考文档

- 硬件说明书 9.11 节：完整业务流程
- 错误码定义：硬件说明书 7.2 节
