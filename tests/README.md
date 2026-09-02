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
- 架构、脚本与机器可读治理配置合同：放入 QUALITY 目录，由质量门禁显式运行
- 需要真实服务、多组件联调、降级/断连、压测或人工参与的测试：放到 HEAVY 重测试目录并显式运行

### 目录归属矩阵

新增测试优先按业务边界和执行成本归位，不要继续把领域测试放在 `tests/` 根目录。

| 目录 | 放置内容 |
| --- | --- |
| `tests/api/` | FastAPI route、permission、response model、API facade 测试 |
| `tests/contracts/` | 跨系统/跨模块契约测试 |
| `tests/core/` | 核心框架、异常处理、RBAC、schema loader、BaseAPI/BaseService 测试 |
| `tests/database/` | Repository hook/error、TreeRepository、Redis client、relation metadata 等轻量测试 |
| `tests/sys/` | 系统域服务、审计日志、事件流与 outbox 轻量合同测试 |
| `tests/api_auth/` | API application、开放接口授权与缓存测试 |
| `tests/deployment/` | docker-compose、nginx、开发 worker/beat 配置测试 |
| `tests/utils/` | 工具函数、时间、请求解析测试 |
| `tests/workline/` | WorkLine 静态身份、物理拓扑、配置校验和 `LineRunEpoch` 等通用能力 |
| `tests/runtime/` | 与具体插件无关的最小执行对象、投影、可靠性和诊断能力 |
| `tests/architecture/` | 架构、依赖方向、缺席与测试拓扑合同；QUALITY 显式运行 |
| `tests/scripts/` | 脚本行为合同；QUALITY 显式运行 |
| `tests/integration/` | Repository/Outbox 持久化、真实数据库与多组件集成测试，默认快速回归不收集 |
| `tests/e2e/` | 显式运行的端到端测试，默认快速回归不收集 |
| `tests/resilience/` | breaker 时序、降级、断连与恢复类测试，默认快速回归不收集 |
| `tests/mock/` | mock server 和模拟器测试，默认快速回归不收集 |

`tests/` 是 WES 核心测试树，不是具体 WorkLine 插件或供应商内部协议的共享测试目录。插件使用独立二次开发包：

```text
workline_plugins/<plugin_key>/
├── pyproject.toml
├── src/
├── tests/
└── fixtures/
```

插件包自己的 `tests/` 唯一拥有具体工作线流程、Handler、业务应用协调、现场拓扑和插件级 E2E/韧性/负载场景。插件纯
Decision 子层只依赖 `wes_plugin_sdk`；插件应用层可依赖 `src` 的基础端口，但 `src` 测试不得反向导入具体插件。所有设备供应商必须
适配 WES 第三方设备统一接口（wire）；供应商内部 DTO、认证、原始 Payload、原始码转换和真实设备行为由供应商在其
ECS/网关交付边界执行一致性验收。核心 pytest、覆盖率、质量门禁和 HEAVY selector 均不发现、不映射也不运行插件测试或
外部供应商验收。

产品内唯一共享 WMS 北向 Adapter 位于 `src/app/wms_adapter/`，只验证 HTTP/JSON、operation DTO/parser、可靠派发和统一
Event route；具体工作线的请求数据、结果解释与恢复由插件测试拥有。共享跨系统 FAST 合同放在
`tests/contracts/wms_adapter/`，真实持久化与事务场景放在 `tests/integration/wms_adapter/`；两处测试不得导入具体插件来
证明基础能力，也不得用于证明 `src/core/outbound_http/` 基础传输或 WES 最小执行内核。

### 当前治理约束

本轮测试套件治理后的长期约束：

- `tests/` 根目录下不得新增 `test_*.py` 文件。
- 人类阅读文档的新增、修改、移动、归档或删除不走 TDD，也不得新增或修改 pytest/测试代码。
- 测试与质量门禁不得读取、解析或断言 `.md`、`.mdx`、`.rst`、`.txt`、`.docx`、`.pdf` 等人类阅读文档的正文、标题、路径清单、链接、状态或措辞。
- 位于 `docs/` 下但被程序或 CI 读取的 `.toml`、`.csv`、`.yaml`、`.yml`、`.json` 等机器可读文件属于配置或可执行合同，仍可测试其解析与行为。
- 既有文档内容测试按无承接测试 `NONE` 清理；文档通过格式、链接/引用、归档目标、原路径缺席及 `git diff --check` 等非测试代码方式验证。
- 默认快速回归 collect 由 `pyproject.toml` 的 `norecursedirs` 和测试文件命名规则共同决定，不在文档中固化数量。
- 如需查看实时测试文件数量，运行 `find tests -type f -name 'test_*.py' | wc -l`。
- 如需查看实时默认 collect，运行 `uv run pytest --collect-only -q -o addopts='' | tail -5`。
- 单文件超过 `3000` 行会触发测试拓扑 guardrail。

后续新增或调整测试时遵循以下约束：

- 必须先建立目标对象测试并通过，再删除对应旧测试；不得反向。清理人类阅读文档内容测试时按 `NONE` 删除。
- 同一行为只有一个主要测试所有者。
- 删除测试的 Commit message 或 PR 描述必须标注承接的目标测试路径或 `NONE`。
- 不得按 `replay`、`legacy`、`reconciliation` 等关键词批量删除测试。
- 默认 `pytest` 收集路径下的 `test_*.py` 不得依赖真实数据库、HTTP、Celery、Redis、容器等真实服务；该边界只由目录位置和 `norecursedirs` 共同保证，不使用 AST 或 import 黑名单扫描。
- 核心仓库不得存在 `tests/workline_plugins/` 或 `tests/device_adapters/`；具体插件或供应商内部协议测试不得改名后寄存在
  `tests/contracts/`、`tests/runtime/` 或核心 HEAVY 目录。
- 核心测试不得导入仓库根目录 `workline_plugins` 二次开发包；通用 SPI/SDK 只能使用不含真实工作线或供应商规则的最小 fake 验证。
- `tests/workline_runtime/` 中的存量测试必须按语义收敛：通用不变量改写到最终核心对象，具体插件行为移出，旧平台装配测试删除；不得继续把该目录当作长期目标所有者。
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

QUALITY 由质量门禁显式运行架构测试和一次 FAST 套件。JUnit 使用 `xunit2`，当前临时预算为套件 180 秒、单例 12 秒；`tests/unit/` 在 N≥30 时 p95 不超过 100 毫秒。N<30 时静默跳过目录 p95 检查。插件包拥有自己的预算，不计入核心 FAST。

FAST 的 180 秒总预算、12 秒单例预算与 `tests/unit/` p95 预算均为强制门禁；任一预算超限时质量流程立即以非零状态退出。预算恢复条件由根目录 `TODOS.md` 的「FAST 测试执行时间优化与 60 秒预算恢复」跟踪；CI 参考环境固定为 2 vCPU / 4 GB 配额。

```bash
uv run pytest -q --junitxml=reports/fast-tests.xml
uv run python scripts/check_fast_test_budget.py reports/fast-tests.xml
./scripts/git-quality-gate.sh --profile quality
```

### 运行重测试

HEAVY 测试影响选择由 `scripts/select_heavy_tests.py` 和机器可读真源
`docs/architecture/heavy-test-impact.toml` 共同治理。selector 先识别直接修改的 HEAVY 测试，再排除人类阅读文档，
随后处理候选 mapping，最后才处理其余 ignore；候选路径没有 mapping 或显式 NONE 时会 fail closed。

维护要求：

- 新增可能影响运行时的生产模块、迁移或基础设施配置时，同步补精确 `[[mapping]]`；只有评审确认无 HEAVY 影响时才填写空 `heavy_tests`。
- 新增、删除或移动 HEAVY 测试时，同步更新引用它的 `heavy_tests`。
- HEAVY 目录内的 conftest、fixture、support、模拟器和负载场景，以及 `tests/fixtures/**`、共享 conftest、`tests/support/**` 都属于候选资产。
- 当前没有已验收权威 HEAVY 测试的既有候选路径保持未映射并 fail closed，待独立业务交接后再补 mapping，不得使用 NONE 或旧测试猜测绕过。
- 具体工作线/插件 HEAVY 场景只存在于对应 `workline_plugins/<plugin_key>/tests/`；供应商真实设备异常和恢复场景属于外部
  一致性验收。两者都不得加入核心 selector 的 `heavy_tests` 映射。

```bash
# 一键执行未暂存差异命中的 HEAVY：使用独立临时容器，自动迁移并在结束时清理
./scripts/run_selected_heavy_local.sh --scope unstaged

# 一键执行已暂存差异命中的 HEAVY
./scripts/run_selected_heavy_local.sh --scope staged

# 本地未暂存改动（默认 scope 也是 unstaged）
uv run scripts/select_heavy_tests.py --scope unstaged

# 本地已暂存改动
uv run scripts/select_heavy_tests.py --scope staged

# CI 提交差异；MR 使用目标分支，develop PUSH 使用 webhook 的 gitlabBefore
uv run scripts/select_heavy_tests.py --base "${CI_DIFF_BASE}"

# selector 永久 QUALITY 合同测试
uv run pytest tests/scripts -q
```

一键入口只启动当前进程专属的 PostgreSQL/Redis Compose 项目，端口由 Docker 动态分配；测试结束或失败时都会删除容器和数据卷。退出 0 且有输出表示 selector 每行输出一个应运行的 HEAVY 测试；退出 0 且无输出表示改动只命中 ignore 或显式 NONE；非零表示 selector 为避免漏测而 fail closed。`Jenkinsfile.backend-ci` 的唯一 `Quality Gate` 在所有构建中运行 selector 合同测试；`HEAVY Required` 在 MR 中使用目标分支作为差异基线，在 GitLab `develop` PUSH 中使用经校验的 `gitlabBefore`，两种路径均只执行 selector 输出的 manifest。

```bash
# E2E 测试（默认不会被 pytest 自动收集）
pytest tests/e2e/

# 韧性/降级测试（默认不会被 pytest 自动收集）
pytest tests/resilience/

# 集成、负载或 mock 相关测试（默认不会被 pytest 自动收集）
pytest tests/integration/
pytest tests/load/
pytest tests/mock/

```

### 推荐使用方式

```bash
# 1) 日常开发：跑默认快速回归集
pytest

# 2) 改动集中在某个模块：跑对应文件/目录
pytest tests/auth/
pytest tests/api/

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

# 显式运行集成或韧性测试目录
pytest tests/integration/
pytest tests/resilience/

# 运行 Redis 人工降级演练
uv run python scripts/manual/redis_degradation_drill.py

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
