# SMT Classifier 插件工作流调试经验

## 问题总结

### 1. Celery Worker 数据库初始化问题
**现象**: Worker 日志显示 "数据库未初始化"，但初始化代码已执行
**原因**: `WorklineTask` 使用类属性 `_db` 被多个 worker 共享
**修复**: 改为实例属性，在 `__init__` 中初始化
```python
class WorklineTask(Task):
    def __init__(self) -> None:
        super().__init__()
        self._db: Any | None = None  # 实例属性而非类属性
```

### 2. Outbox 派发失败 - 错误的 service 调用
**现象**: Outbox 状态为 FAILED，日志显示 "Dispatch failed"
**原因**: `_dispatch_device_command` 尝试调用 `device_service.send_command`，但该方法不存在
**修复**: 删除错误的调用，直接使用 HTTP 发送给设备

### 3. URL 协议拼接错误
**现象**: "Request URL has an unsupported protocol 'deviceprotocol.http://'"
**原因**: `str(DeviceProtocol.HTTP)` 返回的字符串包含额外内容
**修复**: 简化 scheme 获取逻辑
```python
protocol_value = getattr(device, "protocol", None)
if protocol_value:
    scheme = str(protocol_value).lower()
    if scheme not in ("http", "https"):
        scheme = "http"
```

### 4. 请求体格式不匹配 - 缺少 timestamp
**现象**: HTTP 422 Unprocessable Content
**原因**: mock 服务期望 `DeviceCommandPayload` 需要 `timestamp` 字段
**修复**: 添加 timestamp
```python
"timestamp": int(timezone.now_utc().timestamp() * 1000)
```

### 5. 设备配置问题
**现象**: 无法连接到 mock 服务
**原因**: ARM01 设备的 host 配置为 `127.0.0.1`，在 Docker 容器内指向容器本身
**修复**: 更新数据库，将 host 改为 `wes_mock_robot_arm_dev`

## 数据流验证

完整的 smt_classifier 工作流：

```
Scan Event → Pipeline API → Inbox → Plugin → Session → Command → Outbox → Device
   OK: target_type=PIPELINE_PLATFORM
   NG: target_type=NG_PLATFORM, reason=SCAN_NG
```

关键表状态检查：
- `wes_biz.workline_inbox` - PROCESSED
- `wes_biz.workline_sessions` - WAITING_DEVICE_RESULT / FAILED (timeout)
- `wes_biz.device_commands` - PENDING
- `wes_biz.workline_outbox` - SENT
- `wes_biz.workline_timelines` - DECISION_MADE, COMMAND_SENT, WAIT_STARTED
