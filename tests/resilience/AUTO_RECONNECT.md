# Redis 自动重连机制

## 概述

系统现在支持 Redis 自动重连功能：

| 场景 | 行为 |
|------|------|
| **启动时 Redis 不可用** | 应用正常启动，缓存降级 |
| **运行时 Redis 故障** | 缓存自动降级，继续服务 |
| **Redis 恢复后** | 自动检测并重连，恢复缓存功能 |

## 工作原理

### 1. 连接管理层 (`redis_client.py`)

```python
class RedisManager:
    async def ensure_connection(self) -> bool:
        """
        确保 Redis 连接可用
        - 如果连接正常 → 返回 True
        - 如果连接中断 → 自动重连
        """
```

**重连机制**：
- 限制重连频率：30 秒间隔
- 自动清理旧连接
- 重连成功后更新客户端引用

### 2. 缓存服务层 (`redis_cache.py`)

```python
async def _check_health(self) -> bool:
    """
    健康检查时自动触发重连
    - Redis 为 None → 尝试重连
    - ping 失败 → 尝试重连
    """
```

**触发时机**：
- 每次缓存操作前（最多 5 秒检查一次）
- 熔断器从 OPEN 进入 HALF_OPEN
- 性能监控 API 调用时

## 测试自动重连

### 场景 1: 启动时 Redis 不可用 → 运行时恢复

```bash
# 1. 确保Redis 未运行
docker-compose stop redis

# 2. 启动应用（会警告但成功）
uvicorn src.register:app --reload

# 3. 启动 Redis
docker-compose start redis

# 4. 等待 30 秒后，访问 API
curl http://localhost:8001/api/v1/users/1

# 5. 查看日志，应该看到：
# "🔄 尝试重新连接 Redis..."
# "✅ Redis 重连成功，缓存功能已恢复"
```

### 场景 2: 运行时 Redis 故障 → 恢复

```bash
# 1. 应用和 Redis 都在运行
uvicorn src.register:app --reload &
APP_PID=$!

# 2. 访问 API（缓存命中）
curl http://localhost:8001/api/v1/users/1

# 3. 停止 Redis
docker-compose stop redis

# 4. 再次访问 API（降级到 DB）
curl http://localhost:8001/api/v1/users/1
# 日志: "Redis 不可用，跳过缓存读取"

# 5. 重启 Redis
docker-compose start redis

# 6. 等待 30 秒后访问 API
sleep 30
curl http://localhost:8001/api/v1/users/1

# 7. 查看日志，应该看到：
# "检测到 Redis 未初始化，尝试重连..."
# "✅ Redis 重连成功，缓存服务已恢复"

kill $APP_PID
```

### 场景 3: 使用测试脚本

```bash
# 运行自动重连测试
python tests/resilience/test_redis_reconnection.py
```

## 重连配置

### 参数调整

```python
# redis_client.py
self._reconnect_interval: int = 30  # 重连间隔（秒）
```

**调整建议**：
- 生产环境：60-120 秒（减少重连频率）
- 开发环境：10-30 秒（更快恢复）
- 测试环境：5-10 秒（快速测试）

### 熔断器配置

```python
# redis_cache.py
CircuitBreaker(
    failure_threshold=5,    # 失败阈值
    timeout=60,             # 熔断打开时长（秒）
    half_open_max_calls=3   # 半开状态尝试次数
)
```

## 监控重连状态

### 查看缓存状态

```bash
curl http://localhost:8001/api/v1/performance/metrics | jq '.cache'
```

**状态示例**：

```json
{
  "status": "degraded",         // active | degraded
  "redis_available": false,      // true | false
  "circuit_breaker": {
    "state": "closed",           // closed | open | half_open
    "failure_count": 0,
    "failure_threshold": 5
  }
}
```

### 日志监控

**正常启动**：
```
✓ Redis 连接成功
```

**降级启动**：
```
⚠️  Redis 连接失败: ...
   应用将以降级模式运行（无缓存）
   系统将自动检测 Redis 恢复并重连
```

**自动重连**：
```
🔄 尝试重新连接 Redis...
✅ Redis 重连成功，缓存功能已恢复
```

**运行时中断**：
```
Redis 连接中断，尝试重连...
🔄 尝试重新连接 Redis...
✅ Redis 重连成功，缓存服务已恢复
```

## 故障排查

### 问题：重连未触发

**检查**：
```bash
# 1. 查看日志是否有重连尝试
tail -f logs/app.log | grep -i "重连"

# 2. 检查重连间隔
curl http://localhost:8001/api/v1/performance/metrics | jq '.cache.circuit_breaker'
```

**解决**：
- 等待重连间隔（30 秒）
- 手动调用 API 触发检查

### 问题：重连失败

**可能原因**：
- Redis 配置错误
- 网络问题
- Redis 未正确启动

**检查**：
```bash
# 1. 检查 Redis 是否运行
docker-compose ps redis

# 2. 检查 Redis 日志
docker-compose logs redis

# 3. 测试 Redis 连接
redis-cli ping
```

### 问题：频繁重连

**可能原因**：
- Redis 不稳定
- 网络问题
- 负载过高

**解决**：
- 增加 `_reconnect_interval`
- 检查 Redis 配置
- 检查网络连接

## 最佳实践

### 1. 生产环境配置

```python
# 降低重连频率，避免资源浪费
self._reconnect_interval = 60  # 60 秒
```

### 2. 监控告警

```python
# 监控降级事件
if not await cache.is_available():
    metrics.increment("cache.degraded")

# 监控重连事件
if reconnected:
    metrics.increment("cache.reconnected")
```

### 3. 优雅降级

```python
# 不要让重连阻塞主业务
try:
    await ensure_redis_connection()
except Exception:
    logger.warning("重连失败，继续保持降级")
    # 继续处理请求...
```

## 总结

✅ **自动重连**：Redis 恢复后自动检测并重连
✅ **无感知恢复**：不需要重启应用
✅ **频率限制**：避免过度重连
✅ **状态同步**：重连后更新所有引用
✅ **监控可见**：完整的日志和指标

现在系统具备了完整的弹性能力：
- **启动时降级**：Redis 不可用也能启动
- **运行时降级**：Redis 故障时自动降级
- **自动恢复**：Redis 恢复时自动重连
