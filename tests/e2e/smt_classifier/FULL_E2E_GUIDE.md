# SMT 粗分机完整端到端测试指南

## 完整链路说明

### 真实的端到端流程

```
1. 设备事件 → WES /api/v1/callback/event
2. 写入 Inbox → Celery task 触发
3. 插件处理 → 生成 CommandIntent
4. 创建命令 → 写入 Outbox
5. 发送到设备 → Mock 服务接收
6. Mock 执行 → 回调 WES /api/v1/callback/result
7. 更新命令状态 → 触发下一步流程
```

### 当前测试覆盖情况

| 测试类型 | 文件 | 覆盖范围 | 说明 |
|---------|------|---------|------|
| **Mock 交互** | `test_e2e_smt_classifier.py::TestSmtClassifierE2EMockInteractions` | Mock 服务 ↔ 测试代码 | ⚠️ 不经过 WES |
| **插件逻辑** | `test_e2e_smt_classifier.py::TestSmtClassifierE2EFlows` | 插件处理逻辑 | ✅ 单元测试 |
| **完整链路** | `test_full_e2e_chain.py` (新增) | WES → Mock → WES | ✅ 端到端集成 |

## 运行完整端到端测试

### 前提条件

```bash
# 1. 启动基础设施
docker-compose up -d

# 2. 运行数据库迁移
./scripts/migrate.sh upgrade

# 3. 初始化测试数据
uv run python scripts/data/seed_e2e_test_data.py

# 4. 启动 WES Backend
uv run uvicorn main:app --reload --port 8001

# 5. 启动 Celery Worker (另一个终端)
uv run celery -A src.celery_app.app worker --loglevel=info

# 6. 启动 Mock 服务 (另一个终端)
python tests/mock/smt_classifier/run_all.py
```

### 运行测试

```bash
# 运行完整端到端测试
uv run pytest tests/e2e/smt_classifier/test_full_e2e_chain.py -xvs

# 运行所有 E2E 测试
uv run pytest tests/e2e/smt_classifier/ -v -m e2e
```

## 验证完整链路

### 方法 1: 查看 Mock 服务日志

Mock 服务日志应该显示：
```
[时间] INFO [Arm Mock (ARM01)] 收到指令: CMD-xxx
[时间] INFO [Arm Mock (ARM01)] 回调结果到 WES: result=SUCCESS
[时间] INFO [Arm Mock (ARM01)] HTTP Request: POST /api/v1/callback/result "200 OK"
```

### 方法 2: 查看 WES Backend 日志

WES Backend 日志应该显示：
```
[时间] INFO 收到设备事件上报: ARM01
[时间] INFO 设备事件已写入 Inbox
[时间] INFO 插件处理完成: transition=scan_ok
[时间] INFO 指令已发送: CMD-xxx → ARM01
[时间] INFO 收到指令结果回调: CMD-xxx
[时间] INFO 指令结果处理完成
```

### 方法 3: 查看 Celery Worker 日志

Celery Worker 日志应该显示：
```
[时间] INFO Received task: process_inbox_batch
[时间] INFO 插件执行完成: SimplifiedSmtPlugin
[时间] INFO 命令已创建并写入 Outbox
```

## 手动测试完整流程

### 测试 1: 扫描事件触发流程

```bash
# 发送扫描事件
curl -X POST http://localhost:8001/api/v1/callback/event \
  -H "Content-Type: application/json" \
  -H "X-API-App-ID: app_Gqnvr3dpjGwlrjtO" \
  -H "X-API-Timestamp: $(date +%s)" \
  -H "X-API-Nonce: nonce-$(date +%s)" \
  -H "X-API-Signature: <计算签名>" \
  -d '{
    "device_code": "ARM01",
    "event_type": "SCAN_COMPLETED",
    "timestamp": '$(date +%s000)',
    "data": {
      "location": "STATION_INPUT1",
      "LotCode": "TESTLOT001",
      "DateCode": "20260409",
      "Qty": "100",
      "ProductNo": "PROD001"
    }
  }'
```

### 测试 2: 命令结果回调

```bash
# 模拟设备回调命令结果
curl -X POST http://localhost:8001/api/v1/callback/result \
  -H "Content-Type: application/json" \
  -H "X-API-App-ID: app_Gqnvr3dpjGwlrjtO" \
  -d '{
    "device_code": "ARM01",
    "command_code": "CMD-20260409-001",
    "result": "SUCCESS",
    "finish_time": '$(date +%s000)',
    "data": {
      "actual_qty": 1,
      "location": "STATION_PIPELINE1_INPUT1"
    }
  }'
```

## 常见问题

### Q1: 为什么测试没有看到完整的链路日志？

**原因**: 可能缺少 Celery Worker 或数据库未初始化。

**解决**:
```bash
# 确保所有服务都在运行
docker-compose ps  # 检查基础设施
ps aux | grep celery  # 检查 Celery Worker
ps aux | grep uvicorn  # 检查 WES Backend
```

### Q2: Mock 服务日志只显示收到指令，没有回调？

**原因**: Mock 服务配置的回调地址不正确。

**解决**: 检查环境变量
```bash
echo $WES_RESULT_CALLBACK_URL
# 应该输出: http://localhost:8001/api/v1/callback/result
```

### Q3: WES 收到事件但没有生成命令？

**原因**: Celery Worker 未运行或插件未正确注册。

**解决**:
```bash
# 检查插件注册
uv run python -c "from src.workline_plugin_registry import PLUGIN_REGISTRY; print(PLUGIN_REGISTRY.list_plugins())"

# 重启 Celery Worker
uv run celery -A src.celery_app.app worker --loglevel=info
```

## 下一步

要实现真正的端到端测试，需要：

1. ✅ 创建测试数据（workline, device, plugin_key）
2. ✅ 实现 API 签名计算
3. ✅ 验证完整链路的每个环节
4. ⏭️ 添加断言验证数据库状态
5. ⏭️ 添加超时和重试机制
6. ⏭️ 支持并发测试场景