# 测试指南

本项目提供完整的测试方案，包括单元测试、集成测试、覆盖率报告和性能测试。

## 快速开始

### 默认快速回归集

默认快速回归集用于日常开发、本地自测和提交前检查，目标是：

- 覆盖高价值单元测试、核心服务测试、关键 API 测试
- 避免依赖长时间运行、外部环境、人工交互或高波动性能基线
- 保持 `pytest` 默认执行成本可控，适合作为日常回归入口

当前默认快速回归集（FAST）的收集规则：

- 收集范围：`tests/` 下符合 `test_*.py` 的测试文件
- 除以下 QUALITY 或 HEAVY 目录外，默认收集所有未被忽略的测试目录：
  - `tests/architecture/`（QUALITY：显式架构与治理检查）
  - `tests/scripts/`（QUALITY：脚本合同检查）
  - `tests/e2e/`
  - `tests/integration/`
  - `tests/resilience/`
  - `tests/load/`
  - `tests/mock/`

放置新测试时建议遵循：

- 日常回归价值高、执行快、依赖少的测试：放入 FAST 默认快速回归集
- 架构、文档、脚本与质量门禁合同：放入 QUALITY 目录，由质量门禁显式运行
- 需要真实服务、多组件联调、降级/断连、压测或人工参与的测试：放到 HEAVY 重测试目录并显式运行

### 目录归属矩阵

新增测试优先按业务边界和执行成本归位，不要继续把领域测试放在 `tests/` 根目录。

| 目录 | 放置内容 |
| --- | --- |
| `tests/api/` | FastAPI route、permission、response model、API facade 测试 |
| `tests/contracts/` | 跨系统/跨模块契约测试 |
| `tests/core/` | 核心框架、异常处理、RBAC、schema loader、BaseAPI/BaseService 测试 |
| `tests/database/` | Repository、TreeRepository、Redis client、relation metadata 测试 |
| `tests/sys/` | 系统域服务、审计日志、事件流、outbox 测试 |
| `tests/api_auth/` | API application、开放接口授权与缓存测试 |
| `tests/deployment/` | docker-compose、nginx、开发 worker/beat 配置测试 |
| `tests/utils/` | 工具函数、时间、请求解析测试 |
| `tests/architecture/` | 架构、依赖方向、缺席与测试拓扑合同；QUALITY 显式运行 |
| `tests/scripts/` | 脚本行为合同；QUALITY 显式运行 |
| `tests/integration/` | 多组件集成测试，默认快速回归不收集 |
| `tests/e2e/` | 显式运行的端到端测试，默认快速回归不收集 |
| `tests/resilience/` | 降级、断连、恢复类测试，默认快速回归不收集 |
| `tests/mock/` | mock server 和模拟器测试，默认快速回归不收集 |

### 当前治理约束

本轮测试套件治理后的长期约束：

- `tests/` 根目录下不得新增 `test_*.py` 文件。
- 默认快速回归 collect 由 `pyproject.toml` 的 `norecursedirs` 和测试文件命名规则共同决定，不在文档中固化数量。
- 如需查看实时测试文件数量，运行 `find tests -type f -name 'test_*.py' | wc -l`。
- 如需查看实时默认 collect，运行 `uv run pytest --collect-only -q -o addopts='' | tail -5`。
- 单文件超过 `3000` 行会触发测试拓扑 guardrail。

后续新增或调整测试时遵循以下约束：

- 必须先建立目标对象测试并通过，再删除对应旧测试；不得反向。
- 同一行为只有一个主要测试所有者。
- 删除测试的 Commit message 或 PR 描述必须标注承接的目标测试路径或 `NONE`。
- 不得按 `replay`、`legacy`、`reconciliation` 等关键词批量删除测试。
- 默认 `pytest` 收集路径下的 `test_*.py` 不得依赖真实数据库、HTTP、Celery、Redis、容器等真实服务；该边界只由目录位置和 `norecursedirs` 共同保证，不使用 AST 或 import 黑名单扫描。
- 新增领域测试默认不要放在 `tests/` 根目录，优先按上方目录归属矩阵归位。
- 单个测试文件目标低于 `1000` 行；超过 `3000` 行会触发测试拓扑 guardrail。
- API 测试文件只覆盖 route、permission、response contract 和 API facade 行为。
- service、projection、builder 等测试放回对应领域目录。
- 共享 fixture 和 mock builder 优先放到领域内 `conftest.py` 或 `support/`，避免跨文件复制同名 `mock_db`、`mock_session`、`mock_workline`。
- Active production code and active gates must not introduce numbered phase/wave names, lane labels, or cleanup milestone wording; use stable domain names instead. Historical docs, archived plans, and Alembic revision filenames are allowed.
- Active guardrail IDs, test filenames, script functions, and production comments must use stable domain names such as `AUTHORITY_METADATA_BOUNDARY`, `DEVICE_COMMAND_BOUNDARY`, `CAPABILITY_IMPLEMENTATION_IMPORT`, `INBOUND_NORMALIZER_OWNERSHIP`, and `LEGACY_RUNTIME_IMPORT`; old restructuring shorthand like `C3`, `C4`, `R-I3c`, `R-WLR`, or `wlr` is only allowed in historical records.

### 运行默认快速回归

```bash
# FAST：默认快速回归（不包含 architecture / scripts / e2e / integration / resilience / mock / load）
uv run pytest

# 默认快速回归 + HTML 报告 + 覆盖率
uv run pytest --html=reports/report.html --self-contained-html --cov=src --cov-report=html:reports/coverage --cov-report=term-missing
```

### 运行 QUALITY 与速度预算

QUALITY 由质量门禁显式运行架构测试和一次 FAST 套件。JUnit 使用 `xunit2`，预算为套件 60 秒、单例 1 秒；`tests/unit/` 与 `tests/workline_plugins/` 在每目录 N≥30 时 p95 不超过 100 毫秒。N<30 时静默跳过目录 p95 检查。

当前默认 FAST 基线尚待后续测试所有权收敛，质量门禁仅以 `--report-only` 记录实际预算超限，绝不会因 60 秒预算失败；脚本省略该参数时会强制以非零状态退出。CI 达标基线为固定 2 vCPU / 4 GB 配额。

```bash
uv run pytest -q --junitxml=reports/fast-tests.xml
uv run python scripts/check_fast_test_budget.py reports/fast-tests.xml --report-only
./scripts/git-quality-gate.sh --profile quality
```

### 运行重测试

```bash
# E2E 测试（默认不会被 pytest 自动收集）
pytest tests/e2e/

# 韧性/降级测试（默认不会被 pytest 自动收集）
pytest tests/resilience/

# 集成、负载或 mock 相关测试（默认不会被 pytest 自动收集）
pytest tests/integration/
pytest tests/load/
pytest tests/mock/

# Workline 扩展真实 PostgreSQL 性能预算（必须显式配置安全的 integration admin/test URL）
INTEGRATION_DATABASE_URL='postgresql+asyncpg://.../postgres' \
  pytest tests/integration/workline_capabilities/test_runtime_extension_performance_budget_postgresql.py -q -s
```

### 推荐使用方式

```bash
# 1) 日常开发：跑默认快速回归集
pytest

# 2) 改动集中在某个模块：跑对应文件/目录
pytest tests/auth/
pytest tests/api/
pytest tests/admin/test_menu_service_tree.py

# 3) 改动涉及系统稳定性或多服务联调：显式补跑重测试
pytest tests/integration/
pytest tests/resilience/
pytest tests/e2e/
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
pytest tests/database/test_relation_metadata.py

# 显式运行集成、E2E 或韧性测试目录
pytest tests/integration/
pytest tests/e2e/test_conveyor_robot_arm.py
pytest tests/resilience/test_redis_degradation.py

# 只运行某个测试类
pytest tests/database/test_relation_metadata.py::TestRelationMetadata

# 只运行某个测试方法
pytest tests/database/test_relation_metadata.py::TestRelationMetadata::test_get_relation_info_one_to_many

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
uvicorn main:app --reload --host 0.0.0.0 --port 8001
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
