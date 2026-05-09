# SimplifiedSmtPlugin 快速验证指南

## 📋 现有 WorkLine

已找到可用的测试 WorkLine：

```sql
ID: 30
名称: 测试流水线作业线
当前插件: smt_classifier
状态: ACTIVE
```

绑定的设备：

| device_code | device_name   | device_type  |
|-------------|---------------|-------------|
| ARM01       | 进料机械臂     | ROBOTIC_ARM |
| PIPELINE01  | 粗分机流水线   | CONVEYOR    |
| ARM02       | 出料机械臂     | ROBOTIC_ARM |

## 🚀 快速验证步骤

### 步骤1: 临时切换到简化插件

```bash
# 连接到数据库
docker exec -it wes_postgres_dev psql -U wes_user -d wes_db

# 切换到简化插件
UPDATE wes_biz.work_lines
SET plugin_key = 'smt_classifier'
WHERE id = 30;

# 验证切换成功
SELECT id, line_name, plugin_key, is_active
FROM wes_biz.work_lines
WHERE id = 30;
```

预期输出：
```
 id |      line_name      |   plugin_key    | is_active
----+---------------------+-----------------+-----------
 30 | 测试流水线作业线    | smt_classifier  | t
```

### 步骤2: 发送测试事件

使用以下命令发送扫码事件（通过 callback 接口，系统根据 `device_code` 自动路由到绑定的 WorkLine）：

```bash
curl -X POST http://localhost:8001/api/v1/callback/event \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "ARM01",
    "event_type": "SCAN_COMPLETED",
    "data": {
      "barcode": "ABC123",
      "location": "LOC01",
      "scan_result": "OK"
    }
  }'
```

### 步骤3: 查看处理结果

```bash
# 等待几秒后，查看 Session 状态
docker exec -it wes_postgres_dev psql -U wes_user -d wes_db -c "
  SELECT
    s.id,
    s.status,
    s.plugin_state,
    s.context_json->>'barcode' as barcode
  FROM wes_biz.workline_sessions s
  ORDER BY s.id DESC
  LIMIT 1;
"
```

预期输出（简化插件处理成功）：
```
  id  | status | plugin_state           | barcode
------+--------+---------------------+--------
  456 | RUNNING| WAITING_INSPECTION  | ABC123
```

### 步骤4: 发送检测完成事件

```bash
curl -X POST http://localhost:8001/api/v1/callback/event \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "ARM01",
    "event_type": "INSPECTION_COMPLETED",
    "data": {
      "barcode": "ABC123",
      "inspection_result": "OK",
      "reel_diameter": 210.5
    }
  }'
```

### 步骤5: 发送命令结果

```bash
# 模拟进料机械臂抓取成功
curl -X POST http://localhost:8001/api/v1/callback/result \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "ARM01",
    "command_type": "PICK_AND_PUT",
    "result": "SUCCESS"
  }'

# 模拟流水线传输成功
curl -X POST http://localhost:8001/api/v1/callback/result \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "PIPELINE01",
    "command_type": "MOVE_FORWARD",
    "result": "SUCCESS"
  }'

# 模拟出料机械臂成功
curl -X POST http://localhost:8001/api/v1/callback/result \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "ARM02",
    "command_type": "OUTPUT",
    "result": "SUCCESS"
  }'
```

### 步骤6: 验证完成状态

```bash
docker exec -it wes_postgres_dev psql -U wes_user -d wes_db -c "
  SELECT
    s.id,
    s.status,
    s.plugin_state
  FROM wes_biz.workline_sessions s
  ORDER BY s.id DESC
  LIMIT 1;
"
```

预期输出（会话完成）：
```
  id  | status    | plugin_state
------+-----------+----------------
  456 | COMPLETED | WAITING_OUTPUT
```

### 步骤7: 恢复原插件

测试完成后，恢复为传统插件：

```bash
docker exec -it wes_postgres_dev psql -U wes_user -d wes_db -c "
  UPDATE wes_biz.work_lines
  SET plugin_key = 'smt_classifier'
  WHERE id = 30;
"
```

## 🔍 验证对比

### 对比 Timeline 记录

```bash
# 查看简化插件的 Timeline
docker exec -it wes_postgres_dev psql -U wes_user -d wes_db -c "
  SELECT
    seq_no,
    stage,
    action_type,
    payload->>'transition' as transition,
    payload->>'context_patch' as context_patch
  FROM wes_biz.workline_timelines
  WHERE session_id = (
    SELECT id FROM wes_biz.workline_sessions
    ORDER BY id DESC LIMIT 1
  )
  ORDER BY seq_no;
"
```

预期输出：
```
 seq_no |   stage    |    action_type     | transition | context_patch
--------+------------+--------------------+------------+---------------
      1 | DECISION   | DECISION_MADE      | scan_ok    | {"barcode": "ABC123"}
      2 | WAIT       | WAIT_STARTED       |            | ...
      3 | DECISION   | DECISION_MADE      | pick_ok    | ...
```

## ✅ 验证检查清单

- [ ] 扫码事件 → 状态迁移到 WAITING_INSPECTION
- [ ] 检测事件 → 状态迁移到 WAITING_CONVEYOR（OK）或 WAITING_PICK_PLACE（NG）
- [ ] 命令结果 → 对应的状态迁移
- [ ] Timeline 记录包含正确的 transition
- [ ] 错误场景（条码无效、设备失败）正确处理

## 🐛 常见问题

### 问题1: 事件未处理

**症状**: 发送事件后没有状态变化

**排查**:
```bash
# 检查 Inbox 状态
docker exec -it wes_postgres_dev psql -U wes_user -d wes_db -c "
  SELECT id, kind, status, error_message
  FROM wes_biz.workline_inboxes
  ORDER BY id DESC
  LIMIT 5;
"
```

### 问题2: 状态未迁移

**症状**: Session 状态不变

**排查**:
```bash
# 检查 Celery Worker 日志
docker logs wes_backend-celery_worker-1 --tail 50
```

### 问题3: 插件加载失败

**症状**: 错误日志显示插件未找到

**解决**:
```bash
# 检查插件注册表
grep -r "smt_classifier" src/workline_plugin_registry.py

# 如果没有，添加插件定义
# 参考 src/workline_plugin_registry.py
```

## 📝 测试结果记录

记录你的测试结果：

| 测试场景 | 预期结果 | 实际结果 | 状态 |
|---------|---------|---------|------|
| 扫码OK | WAITING_INSPECTION | | |
| 检测OK | WAITING_CONVEYOR | | |
| 检测NG | WAITING_PICK_PLACE | | |
| 条码无效 | 错误 | | |
| 抓取失败 | 错误 | | |

## 🎯 下一步

验证通过后：
1. ✅ 确认简化插件功能正常
2. ✅ 对比性能指标
3. ✅ 生成验证报告
4. ✅ 部署到生产环境

---

**准备好开始验证了吗？** 🚀
