# Redis 故障降级机制

## 概述

当 Redis 不可用时，系统会自动降级到直接查询数据库，确保主业务不受影响。

## 降级机制

### 1. 熔断器模式 (Circuit Breaker)

```
正常 → 失败达到阈值 → 熔断打开 → 超时后 → 半开状态 → 尝试恢复 → 正常
```

**状态说明**：

| 状态 | 说明 | 行为 |
|------|------|------|
| **CLOSED** | 正常工作 | 正常访问 Redis |
| **OPEN** | 熔断打开 | 拒绝访问 Redis，直接查询数据库 |
| **HALF_OPEN** | 半开状态 | 尝试访问 Redis，测试是否恢复 |

**参数配置**：

- `failure_threshold=5`：连续 5 次失败后熔断
- `timeout=60`：熔断 60 秒后尝试恢复
- `half_open_max_calls=3`：半开状态尝试 3 次

### 2. 自动降级行为

| 操作 | Redis 可用 | Redis 不可用 |
|------|-----------|-------------|
| **get()** | 从 Redis 获取 | 返回 None，触发数据库查询 |
| **set()** | 写入 Redis | 静默失败，不影响主业务 |
| **delete()** | 删除 Redis 缓存 | 静默失败，不影响主业务 |
| **acquire_lock()** | 获取分布式锁 | 返回 False，直接执行查询 |

## 监控指标

### 查看缓存状态

```bash
curl http://localhost:8001/api/v1/performance/metrics | jq '.cache'
```

**响应示例**：

```json
{
  "status": "active",  // active | degraded
  "prefix": "app",
  "circuit_breaker": {
    "state": "closed",  // closed | open | half_open
    "failure_count": 0,
    "failure_threshold": 5
  }
}
```

## 测试降级功能

### 快速测试

```bash
# 1. 确保应用和 Redis 都在运行
uvicorn main:app --reload

# 2. 在另一个终端，停止 Redis
docker-compose stop redis  # 或 redis-cli shutdown

# 3. 访问 API，观察日志
curl http://localhost:8001/api/v1/users/1

# 4. 查看日志，应该看到：
# - "Redis 不可用，跳过缓存读取"
# - API 正常返回（从数据库查询）

# 5. 重启 Redis
docker-compose start redis

# 6. 等待 60 秒后，缓存自动恢复
curl http://localhost:8001/api/v1/users/1
```

### 完整测试脚本

```bash
# 运行交互式测试
python tests/resilience/test_redis_degradation.py
```

## 日志示例

### 正常情况
```
10:30:39.100 | DEBUG | [abc123] | 缓存命中: user:detail:1
10:30:39.101 | INFO | [abc123] | 获取用户详情: test_user
```

### Redis 故障（降级）
```
10:30:39.100 | WARNING | [abc123] | Redis 健康检查失败: Timeout
10:30:39.101 | DEBUG | [abc123] | Redis 不可用，跳过缓存读取: user:detail:1
10:30:39.150 | INFO | [abc123] | 获取用户详情: test_user
```

### 熔断器打开
```
10:30:40.100 | ERROR | [abc123] | 熔断器已打开（失败次数: 5，阈值: 5）
10:30:40.101 | DEBUG | [abc123] | Redis 不可用，跳过缓存读取: user:detail:1
```

### 熔断器恢复
```
10:31:40.100 | INFO | [abc123] | 熔断器进入半开状态，尝试恢复
10:31:40.101 | INFO | [abc123] | 熔断器已恢复到正常状态
10:31:40.102 | DEBUG | [abc123] | 缓存命中: user:detail:1
```

## 降级策略对比

### 场景 1：Redis 短时间故障（< 60 秒）

| 时间 | 行为 | 用户体验 |
|------|------|----------|
| 0-5 秒 | 尝试连接，超时 | 略慢（超时时间） |
| 5-60 秒 | 熔断器打开，直接查 DB | 正常 |
| 60+ 秒 | 自动尝试恢复 | 正常 |

### 场景 2：Redis 长时间故障

| 时间 | 行为 | 用户体验 |
|------|------|----------|
| 持续故障 | 持续降级到 DB | 正常（但无缓存加速） |
| Redis 恢复 | 自动恢复缓存 | 性能提升 |

## 性能影响

### 正常情况（Redis 可用）
- 平均响应时间：~10ms
- 数据库负载：低

### 降级情况（Redis 不可用）
- 平均响应时间：~100ms
- 数据库负载：高（但系统可用）

**结论**：即使 Redis 故障，系统依然可用，只是性能下降。

## 最佳实践

### 1. 监控熔断器状态

```python
# 定期检查缓存状态
cache = get_cache()
status = cache.get_status()

if status['circuit_breaker_state'] == 'open':
    # 发送告警
    logger.error(f"缓存熔断器已打开！失败次数: {status['failure_count']}")
```

### 2. 设置合理的阈值

```python
# 根据业务需求调整
circuit_breaker = CircuitBreaker(
    failure_threshold=10,  # 更多容错
    timeout=30,             # 更快恢复
)
```

### 3. 记录降级事件

```python
if not await cache.is_available():
    # 记录降级指标
    metrics.increment("cache.degraded")
```

## 故障排查

### 问题：缓存一直降级

**检查**：
```bash
# 1. 检查 Redis 是否运行
redis-cli ping

# 2. 检查熔断器状态
curl http://localhost:8001/api/v1/performance/metrics | jq '.cache.circuit_breaker'

# 3. 查看应用日志
tail -f logs/app.log | grep -i redis
```

**解决**：
```bash
# 重启 Redis
docker-compose restart redis

# 等待熔断器超时后自动恢复
# 或重启应用（清空熔断器状态）
```

### 问题：熔断器频繁打开

**可能原因**：
- Redis 网络不稳定
- Redis 负载过高
- 连接池配置不当

**解决**：
- 增加 `failure_threshold`
- 优化 Redis 配置
- 检查网络连接

## 总结

✅ **系统弹性**：Redis 故障不影响主业务
✅ **自动恢复**：Redis 恢复后自动重新启用缓存
✅ **可观测性**：完整的监控指标和日志
✅ **可配置性**：根据业务需求调整参数
