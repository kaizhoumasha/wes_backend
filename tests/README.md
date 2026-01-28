# 测试指南

本项目提供完整的测试方案，包括单元测试、集成测试、覆盖率报告和性能测试。

## 快速开始

### 运行所有测试

```bash
# 基础测试
pytest

# 生成 HTML 报告 + 覆盖率
pytest --html=reports/report.html --self-contained-html --cov=src --cov-report=html:reports/coverage --cov-report=term-missing
```

### 测试报告

测试报告生成在 `reports/` 目录：

| 报告类型 | 文件 | 说明 |
|---------|------|------|
| **HTML 测试报告** | `reports/report.html` | 测试结果、详情、日志 |
| **覆盖率报告** | `reports/coverage/index.html` | 代码覆盖率统计和详细分析 |

### 查看报告

```bash
# macOS
open reports/report.html
open reports/coverage/index.html

# Linux
xdg-open reports/report.html
xdg-open reports/coverage/index.html
```

## 测试报告配置

项目已配置以下测试报告插件：

- **pytest-html**: 生成 HTML 格式的测试报告
- **pytest-cov**: 生成代码覆盖率报告

### 常用命令

```bash
# 只运行某个测试文件
pytest tests/test_relation_metadata.py

# 只运行某个测试类
pytest tests/test_relation_metadata.py::TestRelationMetadata

# 只运行某个测试方法
pytest tests/test_relation_metadata.py::TestRelationMetadata::test_get_relation_info_one_to_many

# 运行并显示详细输出
pytest -v -s

# 运行最慢的 10 个测试
pytest --durations=10

# 运行上次失败的测试
pytest --lf

# 运行带标记的测试
pytest -m slow
```

---

# 性能测试指南

本项目提供完整的性能测试方案，包括负载测试、压力测试和基准测试。

## 测试工具

### 1. Locust - 负载测试
**特点**：
- Python 编写，易于定制
- 分布式测试支持
- Web UI 实时监控
- 模拟真实用户行为

**安装**：
```bash
pip install locust
```

### 2. Apache Bench (ab) - 压力测试
**特点**：
- 命令行工具
- 简单快速
- 适合单接口压力测试

**安装**：
```bash
# macOS
brew install httpd

# Linux
apt-get install apache2-utils
```

## 快速开始

### 1. 启动服务器

```bash
# 确保服务器在运行
uvicorn src.register:app --reload --host 0.0.0.0 --port 8001
```

### 2. 使用测试脚本

```bash
# 查看帮助
./scripts/run_performance_test.sh help

# 健康检查
./scripts/run_performance_test.sh health

# 获取性能指标
./scripts/run_performance_test.sh metrics

# 运行完整测试套件
./scripts/run_performance_test.sh full
```

## 测试场景

### 1. Locust Web UI 模式（推荐）

```bash
./scripts/run_performance_test.sh locust-ui
```

然后访问：http://localhost:8089

**推荐配置**：

| 场景 | 用户数 | 产生速率 | 运行时间 |
|------|--------|----------|----------|
| 轻负载 | 10 | 1/秒 | 1 分钟 |
| 中负载 | 100 | 10/秒 | 2 分钟 |
| 重负载 | 500 | 50/秒 | 5 分钟 |
| 压力测试 | 1000 | 100/秒 | 10 分钟 |

### 2. Locust 无头模式

```bash
# 100 用户，每秒启动 10 个，运行 1 分钟
./scripts/run_performance_test.sh locust 100 10 1m
```

### 3. Apache Bench 压力测试

```bash
# 1000 请求，10 并发
./scripts/run_performance_test.sh ab 1000 10

# 5000 请求，50 并发
./scripts/run_performance_test.sh ab 5000 50
```

### 4. 并发测试

```bash
./scripts/run_performance_test.sh concurrent
```

## 性能指标说明

### 关键指标

| 指标 | 说明 | 良好值 |
|------|------|--------|
| **RPS** | 每秒请求数 | >100 |
| **响应时间** | 平均响应时间 | <100ms |
| **中位数** | 50% 请求响应时间 | <80ms |
| **95%** | 95% 请求响应时间 | <200ms |
| **99%** | 99% 请求响应时间 | <500ms |
| **失败率** | 请求失败比例 | <1% |

### 系统资源监控

```bash
# 实时监控 CPU 和内存
./scripts/run_performance_test.sh metrics

# 监控数据库连接
./scripts/run_performance_test.sh metrics | grep -A 10 database
```

## 测试场景详解

### 1. 缓存性能测试

**目的**：验证缓存是否生效

**步骤**：
```bash
# 1. 重置缓存
./scripts/run_performance_test.sh reset

# 2. 第一次查询（缓存未命中）
curl http://localhost:8001/api/v1/users/1

# 3. 第二次查询（缓存命中）
curl http://localhost:8001/api/v1/users/1

# 4. 查看日志，确认缓存命中
```

**预期结果**：
- 第一次查询：日志显示 "缓存未命中"
- 第二次查询：日志显示 "缓存命中"，响应时间明显降低

### 2. 缓存击穿测试

**目的**：验证分布式锁是否防止缓存击穿

**步骤**：
```bash
# 清空缓存
./scripts/run_performance_test.sh reset

# 并发查询同一热点数据
for i in {1..100}; do
  curl http://localhost:8001/api/v1/users/1 &
done
wait
```

**预期结果**：
- 只有 1 个请求查询数据库
- 其他 99 个请求从缓存获取
- 日志显示 "获取锁成功"

### 3. 缓存穿透测试

**目的**：验证空值缓存是否防止穿透

**步骤**：
```bash
# 查询不存在的用户
curl http://localhost:8001/api/v1/users/99999
curl http://localhost:8001/api/v1/users/99999  # 第二次
```

**预期结果**：
- 第一次：查询数据库，返回 404
- 第二次：直接返回 404（从缓存空值判断）

### 4. 缓存雪崩测试

**目的**：验证随机过期时间是否防止雪崩

**步骤**：
```bash
# 批量设置缓存，观察过期时间
# 在 Locust 中使用大量并发请求
./scripts/run_performance_test.sh locust 500 50 2m
```

**预期结果**：
- 缓存过期时间有随机偏移
- 不会出现大量缓存同时失效

## 测试报告

### Locust 报告

运行 Locust 测试后，报告保存在：
```
reports/locust_report.html  # HTML 报告
reports/locust_stats.csv    # CSV 数据
reports/locust_stats_history.csv  # 历史数据
```

### Apache Bench 报告

```bash
# 查看 TSV 格式的数据
cat reports/ab_plot.tsv

# 使用工具可视化
# （需要额外工具支持）
```

## 性能优化建议

### 1. 数据库优化

```sql
-- 添加索引
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
```

### 2. Redis 优化

```bash
# 修改 redis.conf
maxmemory 256mb
maxmemory-policy allkeys-lru
```

### 3. 应用优化

- 调整数据库连接池大小
- 调整 Redis 连接池大小
- 启用 gzip 压缩
- 使用异步操作

## 故障排查

### 问题：响应时间过长

**检查**：
```bash
# 1. 检查数据库连接
./scripts/run_performance_test.sh metrics | grep database

# 2. 检查 Redis 连接
./scripts/run_performance_test.sh metrics | grep redis

# 3. 检查系统资源
./scripts/run_performance_test.sh metrics | grep system
```

### 问题：失败率高

**可能原因**：
- 数据库连接池耗尽
- Redis 连接失败
- 系统资源不足

**解决**：
```bash
# 查看详细日志
tail -f logs/app.log
```

### 问题：缓存未生效

**检查**：
```bash
# 1. 检查 Redis 是否运行
redis-cli ping

# 2. 查看缓存键
redis-cli
> KEYS app:user:*

# 3. 查看 TTL
> TTL app:user:detail:1
```

## 最佳实践

1. **测试前**：
   - 重置测试数据
   - 确保服务正常运行
   - 检查系统资源

2. **测试中**：
   - 从小负载开始
   - 逐步增加负载
   - 监控系统指标

3. **测试后**：
   - 保存测试报告
   - 分析性能瓶颈
   - 优化后重新测试

## 延伸阅读

- [Locust 官方文档](https://locust.io/)
- [FastAPI 性能优化](https://fastapi.tiangolo.com/benchmarks/)
- [Redis 性能优化](https://redis.io/docs/manual/patterns/)
