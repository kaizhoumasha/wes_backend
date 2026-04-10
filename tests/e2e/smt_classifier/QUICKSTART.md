# 完整端到端测试快速启动指南

## 一键启动（推荐）

```bash
# 自动检查所有依赖并运行测试
./tests/e2e/smt_classifier/run_full_e2e.sh
```

## 手动启动步骤

### 终端 1: 基础设施
```bash
docker-compose up -d
```

### 终端 2: WES Backend
```bash
uv run uvicorn main:app --reload --port 8001
```

### 终端 3: Celery Worker
```bash
uv run celery -A src.celery_app.app worker --loglevel=info
```

### 终端 4: Mock 服务
```bash
python tests/mock/smt_classifier/run_all.py
```

### 终端 5: 运行测试
```bash
uv run pytest tests/e2e/smt_classifier/test_full_e2e_chain.py -xvs
```

## 预期结果

### Mock 服务日志应该显示：

```
[时间] INFO [Arm Mock (ARM01)] 收到指令: CMD-xxx
[时间] INFO [Arm Mock (ARM01)] 开始执行: STATION_INPUT1 → STATION_PIPELINE1_INPUT1
[时间] INFO [Arm Mock (ARM01)] 执行成功
[时间] INFO [Arm Mock (ARM01)] 回调结果到 WES: result=SUCCESS
[时间] INFO HTTP Request: POST http://localhost:8001/api/v1/callback/result "200 OK"
```

### WES Backend 日志应该显示：

```
[时间] INFO 收到设备事件上报: ARM01
[时间] INFO 设备事件已写入 Inbox
[时间] INFO 触发 Celery task 处理
[时间] INFO 插件处理完成: transition=scan_ok
[时间] INFO 创建命令: PICK_AND_PUT → ARM01
[时间] INFO 收到指令结果回调: CMD-xxx
[时间] INFO 指令结果处理完成
```

### Celery Worker 日志应该显示：

```
[时间] INFO Received task: process_inbox_batch
[时间] INFO Task succeeded: process_inbox_batch
```

## 测试覆盖

- ✅ WES Backend 健康检查
- ✅ 完整链路：创建指令 → Mock 执行 → 回调
- ✅ 扫描事件触发插件流程

## 故障排查

### 测试被跳过？

**原因**: WES Backend 或 Celery Worker 未运行

**解决**: 使用 `run_full_e2e.sh` 脚本检查所有依赖

### Mock 服务日志只显示收到指令？

**原因**: 这说明测试直接调用了 Mock，绕过了 WES

**解决**: 运行 `test_full_e2e_chain.py` 而不是 `test_e2e_smt_classifier.py`

### 没有看到回调日志？

**原因**: Mock 服务的回调地址配置不正确

**解决**: 检查环境变量
```bash
echo $WES_RESULT_CALLBACK_URL
# 应该输出: http://localhost:8001/api/v1/callback/result
```