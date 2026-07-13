# Celery Worker 单异步运行时与连接容量修复计划

> 日期：2026-07-13  
> 分支：`feature/runtime-inbox-single-source-of-truth`  
> 目标：消除 Celery prefork 子进程内跨事件循环复用 asyncpg 连接造成的事务泄漏，并建立可验证的数据库连接容量边界。

## Summary

已确认根因不是业务扫描遗漏 `commit/rollback`，而是同一 Celery 子进程中的多个模块私有事件循环共享全局 SQLAlchemy `AsyncEngine`。跨循环异常使 asyncpg 无法正常回滚或关闭连接，最终积累为 `idle in transaction` 和 PostgreSQL `too many clients already`。

修复采用 Python `asyncio.Runner`、Celery prefork 生命周期信号和 SQLAlchemy `AsyncSession per task` 模式，不引入自定义 Celery pool，不使用 `NullPool` 掩盖问题，也不提高 PostgreSQL `max_connections`。

## What already exists

- `src/database/db.py` 已提供全局 Engine、Session factory、`init_db()`、`close_db()` 和任务适用的 `get_db_context()`；本计划复用这些入口并补齐 fork 所有权合同。
- `src/database/redis_client.py` 已封装 Redis 降级、重连和跨 loop 检测；本计划把初始化改为原子发布，不新建第二套 Redis client。
- Celery 应用已使用 `worker_init`、`worker_process_init` 和 Beat 配置；本计划补充 child shutdown，并移除父进程异步资源初始化。
- RuntimeInbox 已有 claim/commit/rollback/timeout/retry 深度测试；本计划保留事务边界，只替换运行时和 Session 获取方式。
- `tests/integration/conftest.py`、RuntimeInbox PostgreSQL harness 和 Docker 验收脚本提供了隔离数据库基础；本计划扩展为真实 prefork 测试，而不另造测试框架。
- `SettingsConfigDict(env_file=...)` 已能以真实进程环境覆盖 `.env` 默认值；本计划删除 `conf.py` 中手工 `load_dotenv(..., override=True)` 的反向覆盖，恢复该标准优先级。

## Architecture

```text
Celery MainProcess
  └─ worker_init: logger only; engine/session factory MUST remain None
       └─ fork
          └─ ForkPoolWorker PID N
             ├─ worker_process_init
             │  └─ total deadline 3s (< Celery hard limit 4s)
             │     └─ CeleryAsyncRuntime NEW -> INITIALIZING -> READY
             │        ├─ one asyncio.Runner
             │        ├─ one AsyncEngine + bound AsyncSessionLocal
             │        └─ application Redis DB0 init <= 1s
             │           ├─ success: atomic publish
             │           └─ timeout/error: close locals, READY in degraded mode
             ├─ every Celery message
             │  └─ run_async(coroutine_factory, fresh Context)
             │     └─ one local get_db_context() AsyncSession
             └─ worker_process_shutdown
                ├─ cancel/await pending Runner tasks with timeout
                ├─ bounded Redis close
                ├─ bounded engine.dispose
                └─ Runner.close best-effort; supervisor grace is hard bound
```

Runtime 状态固定为 `NEW → INITIALIZING → READY → CLOSING → CLOSED`。初始化失败必须清理已创建资源、回到 `NEW` 并重新抛出；Redis DB0 不可用属于 `READY + degraded_redis`，不是运行时初始化失败。`CLOSED` 是终态，重复 shutdown 幂等。

`worker_process_init` 从进入 signal handler 起使用单调时钟执行整体 3 秒 deadline，严格小于 Celery 的 4 秒硬限制。Redis ping 最多使用其中 1 秒，timeout/cancel 后的候选资源 cleanup 也必须按剩余预算独立限时；cleanup 超时记录 warning 后进入 degraded，不得拖延 child 启动。DB 初始化当前不联网，但仍计入整体 deadline，未来增加 I/O 不能绕过该合同。

shutdown 的 pending-task、Redis 和 Engine 清理阶段各自有 timeout，某阶段失败或超时仍继续后续阶段。`asyncio.Runner.close()` 可能等待默认 executor，不能宣称进程内绝对有界；Worker runtime 自身禁止使用默认 executor，正常路径断言没有 executor 遗留后 best-effort close。dev wrapper 在 20 秒后对进程组发送 KILL，Compose 在 30 秒后提供容器级最终上限；生产 direct-Celery 没有 wrapper，以 Compose 30 秒为最终上限。连接正确性仍依赖 task-local Session 与 OS 断连回滚。

数据库 Engine 所有权固定为 `owner_pid + owner_loop_id + owner_role`。`init_db()` 只允许相同 owner 幂等调用；PID、event loop 或 role 任一不匹配都必须 fail-fast，禁止覆盖或跨 loop dispose。`get_db()`、`get_db_context()` 和 `close_db()` 在访问资源前校验 owner；fork 后继承的父进程 Engine 只能报告所有权错误，不能在子进程事件循环中清理。

`run_async()` 接收 `Callable[[], Awaitable[T]]`，Runner 就绪后才创建 coroutine。正常 Worker 由 signal 初始化；direct/`task_always_eager` 调用在 `NEW` 状态走同一条有界懒初始化路径。若调用线程已有运行中的 event loop，稳定抛出明确错误，不创建线程桥接或第二事件循环。

每次 `Runner.run()` 传入新的 `contextvars.Context()`。共享范围只有 event loop、Engine 和 pool；trace、request 与 BackgroundTasks 上下文不得跨 Celery 消息继承。

Redis 职责边界：

- 应用 Redis DB0 缓存允许降级。
- RuntimeInbox/Orchestrator 锁在 DB0 不可用时必须回退 PostgreSQL advisory transaction lock，禁止无锁执行。
- Celery broker DB1 和 result backend DB2 不属于应用 Redis 初始化；连接失败由 Celery 启动/消费链路 fail-fast。
- Redis DB0 初始化/重连按 owner loop 懒创建一把 `asyncio.Lock`，将 cleanup、候选 pool/client 创建、ping 和原子发布组成 single-flight；并发等待者复用首个结果。foreign-loop 调用 fail-fast，timeout/cancel/error 必须关闭尚未发布的候选资源。

## Implementation Changes

### Task 1: 影响分析与失败基线

- [x] 在编辑任何业务代码前建立“现有符号修改清单”，并逐一运行 GitNexus upstream impact。最低覆盖：`Settings`；`init_db`、`close_db`、`get_db`、`get_db_context`；`RedisManager.init_redis`、`RedisManager.reconnect` 及实际需要修改的关闭方法；`_init_worker_infra`、`_run_sync_init`、`on_worker_init`、`on_worker_process_init` 等现有 Celery 生命周期函数；五个任务模块中将删除或修改的 loop helper、Task 基类，以及所有函数体或绑定方式会变化的现有任务入口（包括 core health/cache/notification、RuntimeInbox batch、SystemOutbox dispatch、Workline scan/process 和 handling process）。
- [x] 每个实施 Task 开始前对照预期 diff 复核符号清单；若新增任何待修改的现有函数、类或方法，必须停止编辑并先补做 impact。记录 direct callers、受影响 execution flows 与风险级别；HIGH/CRITICAL 必须先向用户报告并确认，不得先编辑再补分析。
- [x] 保存当前 prefork 复现证据：跨 loop/asyncpg 错误、`idle in transaction` 数量、Worker 停止后的连接回落。

### Task 2: TDD 锁定运行时、Redis 与任务合同

- [x] 新增 `tests/deployment/test_celery_async_runtime.py`，先覆盖状态转换、signal/lazy/eager/direct 三条初始化入口、PID/fork guard、fresh Context、nested-loop 错误、异常恢复、各 shutdown 清理阶段 timeout/continue、无默认 executor 遗留和正常 Runner close。用可控单调时钟精确验证 `worker_process_init` 的 3 秒整体 deadline（仅允许明确的小容差），并分别模拟 Redis ping 与取消清理挂起，证明所有阶段共用同一 deadline 而非各自重新计时。
- [x] 扩展数据库测试，覆盖相同 pid/loop/role 幂等初始化、同 PID 跨 loop 拒绝、fork 后 owner 拒绝、非 owner get/close 拒绝，以及 owner 正常关闭后清空元数据。
- [x] 增加 Settings 优先级测试：真实 process env 必须覆盖仓库 `.env`；subprocess fixture 注入的隔离 POSTGRES/REDIS 配置不得被 `conf.py` 导入副作用改写。
- [x] 扩展 `tests/resilience/test_redis_reconnection.py`，证明 Redis pool/client 只在 ping 成功后发布；timeout、cancel、ping error 会关闭局部资源且不暴露半初始化引用；并发 init/reconnect 只创建并发布一个 pool，等待者复用结果，foreign-loop 调用被拒绝。
- [x] 新增 `tests/deployment/test_celery_task_runtime_contract.py`，守卫 `core/handling/runtime_inbox/sys/workline` 不再创建模块私有 loop、不缓存 Task Session，并保留任务名称、队列、retry/countdown、acks/time-limit 等 Celery 合同。
- [x] 更新 `tests/deployment/test_runtime_inbox_celery_cutover.py`，从 Task `_db` monkeypatch 改为注入 `get_db_context()`；保留全部 claim、commit、rollback、timeout、retry 和 SLI 断言。

### Task 3: 原子 Redis 初始化与单一 Worker runtime

- [x] 修改 `RedisManager.init_redis()`/`reconnect()`：按 owner loop 懒创建 single-flight lock；锁内用局部 pool/client 完成 cleanup、ping 和一次性发布，异常、取消和超时关闭局部资源并保持 manager unavailable；foreign-loop 调用 fail-fast。
- [x] 新增 `src/celery_app/async_runtime.py`，实现内部状态、PID 归属、Runner、`initialize()`、`run_async(factory)` 和分阶段有界资源清理；Runtime 层禁止 `to_thread`/默认 executor，`Runner.close()` 仅 best-effort。
- [x] 修改 `src/celery_app/app.py`：`worker_init` 仅初始化日志；`worker_process_init` 在整体 3 秒 deadline 内创建子进程 runtime；注册 `worker_process_shutdown`；Redis ping 最多 1 秒，取消清理按剩余预算限时。
- [x] `init_db()`、Session 获取入口和 `close_db()` 实现 `owner_pid + owner_loop_id + owner_role` 断言；父进程若已存在 Engine/Session factory，子进程初始化应 fail-fast 并输出所有权错误，不尝试跨 loop dispose 继承连接。
- [x] shutdown 的事务正确性由每任务 Session 上下文保证；`Runner.close()` 卡住或 SIGKILL 时依赖 dev wrapper 20 秒/Compose 30 秒的分层 KILL 与 OS 断连触发 PostgreSQL 回滚，不宣称 shutdown signal 或 Runner close 必达。

### Task 4: 迁移全部 Celery 任务族

- [x] 删除 `WorklineTask`、`RuntimeInboxTask`、`SystemTask`、`HandlingTask`、`_db/db/cleanup` 以及 `workline/runtime_inbox/sys/handling/core` 各任务模块的模块级 loop helper（`_WORKLINE_TASK_LOOP`、`_get_sync_event_loop`、`_lazy_init_db`、`_run_async`）；绑定任务直接使用 Celery `Task` 的 `self.retry`。
- [x] `core.py`、`runtime_inbox.py`、`sys.py`、`workline.py` 的每条同步任务只调用一次统一 `run_async(factory)`；异步函数内部使用 `async with get_db_context() as db`。
- [x] `handling.py` 当前没有异步工作，删除未使用的 loop/Session scaffolding，不强行调用 runtime。
- [x] 保留 RuntimeInbox 的 claim 独立 commit、processor timeout/error rollback、stale lease recovery 和最终 commit。
- [x] 在 `async_runtime.py` 保留一段生命周期 ASCII 图；任务模块不复制该图。

### Task 5: 连接预算、连接标识与 Celery 调度护栏

- [x] 删除 `src/core/conf.py` 对 `.env` 的 `load_dotenv(..., override=True)` 反向覆盖，统一由 Pydantic Settings 处理环境文件；进程环境始终拥有最高优先级。
- [x] 在 `Settings`/`init_db()` 增加 `DATABASE_RUNTIME_ROLE=api|celery|integration|cli`、`DATABASE_POOL_SIZE`、`DATABASE_MAX_OVERFLOW`、`DATABASE_POOL_TIMEOUT`、`DATABASE_APPLICATION_NAME` 前缀；构造 asyncpg 连接参数时合并既有 `server_settings`，只写入/覆盖 `application_name`，必须保留 `search_path` 和其他调用方设置。SQLite 继续使用 `StaticPool` 且不接收 PostgreSQL pool 参数。
- [x] Docker API 每 Uvicorn 进程 `pool_size=5`，Celery 每 prefork 子进程 `pool_size=1`，`cli` 与 `integration` 每进程 `pool_size=1`，所有角色均固定 `max_overflow=0`；`Dockerfile` 中 `--workers 4` 是预算公式 `1×4×5` 的输入，修改 worker 数必须同步更新本计划与部署门禁。
- [x] `.env.prod` 将 `API_REPLICAS` 修正为 1；当前真实拓扑为一个 API 容器内 4 个 Uvicorn worker，以及 4 个 Celery 容器、每容器 concurrency=4。
- [x] 当前生产理论基础池上限为 `1×4×5 + 4×4×1 = 36`。部署门禁读取 live PostgreSQL `max_connections`，默认保留至少 10 个非应用连接；生产迁移、容量探测等短命 `cli` 连接由该 reserve 覆盖，不重复计入基础池。预算超过 `max_connections - reserve` 时拒绝部署/scale；生产 Settings 校验拒绝 `max_overflow > 0` 或单进程 `pool_size` 超过本计划预算。`integration` 仅在隔离测试环境按实际并发单独核算，不占生产预算。
- [x] 修正 `scripts/docker-deploy-simple.sh` 中 `celery_worker=8` 的无约束扩容示例；新增 `scripts/capacity_guard.py`（或被现有部署脚本调用），读取目标 PostgreSQL `max_connections`、按本计划公式计算占用、超预算或非法配置时非零退出。
- [x] `prod up` 改为两阶段启动：先启动 DB/Redis 并等待健康，再用目标 API/Worker 拓扑运行 live 容量门禁，通过后才启动 API/Worker/Beat；门禁失败时基础设施保持在线但应用进程不得启动。`scale` 在执行扩容前对在线数据库运行同一门禁。
- [x] API、Celery 和 integration run 的 `application_name` 固定为 `<env>:<role>:<hostname>:<pid>[:<run-id>]`：role 来自 `DATABASE_RUNTIME_ROLE`，仅允许可打印 ASCII，总长度不超过 63 字符；超长时优先截断前缀/hostname，必须保留 role、PID 和可选 run-id。API 在各 Uvicorn 进程初始化 Engine 时生成，Celery 在各 prefork 子进程初始化时生成，integration run 使用唯一 run-id。
- [x] 设置 `worker_prefetch_multiplier=1`、`worker_soft_shutdown_timeout=10`、`worker_enable_soft_shutdown_on_idle=True`，与 `acks_late` 对齐；`TERM` 保持 Celery 默认 warm shutdown，`QUIT` 进入 soft/cold shutdown 链路，两者只发送给 Celery MainProcess，由 leader 协调 prefork children；外层 dev reload grace 20 秒超时后才对整个进程组发送 KILL。修改 10/20 秒任一上限时必须同步复核另一项。
- [x] 修正 `dev_worker_autoreload.sh`：保留独立进程组用于最终回收，但 TERM 只发送 leader PID，禁止正常 shutdown 信号直接打到 ForkPoolWorker；超时 KILL 才使用负 PGID。
- [x] 在 `docker-compose.yml` 与 `docker-compose.deploy.yml` 的 Worker 服务显式设置 `stop_grace_period: 30s`；配置合同固定 `worker_soft_shutdown_timeout=10 < dev wrapper grace=20 < container grace=30`。生产 direct-Celery 使用容器 30 秒作为 TERM warm shutdown 的最终硬上限。
- [x] Compose 与部署/迁移命令按服务显式注入 role；Settings 按 role 校验 API `pool_size<=5`，Celery、CLI、integration `pool_size=1`，所有角色 `max_overflow=0`，并拒绝缺失/未知 role 和非法组合。新增环境 profile 可解析、CLI reserve 归属、integration 并发预算、基础池公式、`application_name` 格式/截断/进程轮换、`server_settings` 合并保留 `search_path` 和非法配置测试。

### Task 6: 真实 prefork/PostgreSQL 回归

- [x] 新增 `tests/integration/test_celery_async_runtime_postgresql.py`；fixture 从安全校验后的 `INTEGRATION_DATABASE_URL/INTEGRATION_REDIS_URL` 显式生成 Worker 的 `POSTGRES_*/REDIS_*` 环境，并在 Worker ready 后查询/记录实际目标 host、database、role 和 run-id，证明进程环境未被仓库 `.env` 覆盖且未误连开发库。测试 observer 使用独立 `cli` role 和 `<run-id>-observer` application name，不得复用 Worker 标识。
- [x] prefork fixture 使用 `start_new_session=True` 建立独立进程组，并为每次 run 配置生产者与 Worker 共用的唯一 hostname、queue、application run-id，以及 Celery broker/result backend `global_keyprefix`；测试环境使用显式的短 `visibility_timeout`。teardown 按场景只向 Celery MainProcess PID 发送 TERM/QUIT并等待其协调 children，超时后才向整个进程组发送 KILL；随后等待所有 descendants 退出，清理该 run 的 queue 与 Redis key prefix，并有界轮询该 run-id 连接归零。不得仅靠 purge/delete queue 隔离 Redis DB 全局的 `unacked`/`unacked_index` 状态。失败保留日志，成功才删除临时日志。
- [x] 场景一使用 `concurrency=2` 和独立 queue，交替执行 core health、RuntimeInbox、Workline scan、SystemOutbox 与 handling task；从日志确认两个 PID 各自初始化独立 Runner/Engine。
- [x] 场景二使用 `max-tasks-per-child=1`，证明子进程轮换后 Engine/SessionFactory identity 和 application name 更新，旧连接收敛为 0。
- [x] 场景三拆成两条真实信号路径：发送 `TERM` 验证 warm shutdown、事务释放以及 dev 20 秒/容器 30 秒兜底；发送 countdown/retry 后 `QUIT`，验证 10 秒 soft shutdown 后进入有界 cold shutdown，并在测试专用短 `visibility_timeout` 后观察消息最终重新投递。恢复合同为 at-least-once：允许重复投递，但必须通过幂等约束证明最终数据库状态正确；不要求 QUIT 后立即恢复或恰好执行一次。
- [x] 增加真实 prefork 降级场景：让 Redis ping/cleanup 持续挂起，证明 `worker_process_init` 在 3 秒总预算内结束初始化、子进程存活并可继续处理不依赖应用 Redis 的任务；日志必须记录超时阶段和 degraded 状态。
- [x] 按 Worker 的完整 `application_name` 有界轮询 `pg_stat_activity`，并防御性排除 observer 的 `pg_backend_pid()`：无 `idle in transaction`，无跨 loop/protocol/termination 错误，Worker 退出后目标标识连接数严格为 0；不得用“允许残留 1 条”掩盖 observer 自计数或真实泄漏。

### Task 7: 本机 Docker 冷启动验收

- [x] 暂停 Beat/Worker；仅 purge dev Worker 实际消费的 `default,celery` 队列；删除旧 Beat/Worker 容器以清理调度状态。
- [x] rebuild API/Worker/Beat；先冷启动 DB/Redis 并等待健康，容量门禁通过后执行迁移，再启动 API、Worker、Beat。
- [x] 连续至少 10 分钟采集按 `application_name` 分组的连接状态；`idle in transaction=0`，连接数不随 Beat 周期单调增长。
- [x] 日志断言使用显式 shell 条件处理 `rg` 的“无匹配退出码 1”，避免把成功的无错误日志误报为命令异常。

建议命令：

```bash
docker compose --profile dev stop celery_beat celery_worker
docker compose run --rm --no-deps celery_worker \
  celery -A src.celery_app.app purge -f -Q default,celery
docker compose rm -f celery_beat celery_worker
docker compose --profile dev build api celery_worker celery_beat
docker compose --profile dev up -d db redis
uv run python scripts/capacity_guard.py --services api,celery_worker
./scripts/migrate.sh upgrade
docker compose --profile dev up -d api celery_worker celery_beat
```

### Task 8: 文档、TODO 与质量门禁

- [x] TODOS.md 新增“全仓 Redis fail-open/fail-closed/fallback 审计”。
- [x] 扩展现有“统一运营看板、告警与 Runbook” TODO，加入连接池 checkout wait/timeout、连接预算占用和按 application_name 的 idle transaction 告警。
- [x] 新增“API 容器横向扩容拓扑” TODO；当前 API_REPLICAS=1，未来需移除固定 container_name/host port 并通过 Nginx service discovery 扩容。
- [x] 运行 GitNexus detect changes、测试拓扑守卫、默认收集、Ruff 和项目 quality gate。

验证命令：

```bash
uv run pytest tests/deployment/test_celery_async_runtime.py \
  tests/deployment/test_celery_task_runtime_contract.py \
  tests/deployment/test_runtime_inbox_celery_cutover.py \
  tests/resilience/test_redis_reconnection.py \
  tests/database/test_db_pool_configuration.py -q

RUN_WORKLINE_INTEGRATION=1 uv run pytest \
  tests/integration/test_celery_async_runtime_postgresql.py -q

uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q
uv run pytest --collect-only -q -o addopts='' | tail -5
uv run ruff format --check .
uv run ruff check .
./scripts/git-quality-gate.sh --profile quality
```

## Test Coverage and Failure Modes

```text
worker_init
  ├─ parent engine/session factory absent                 [unit]
  └─ logger setup failure                                 [unit, fail-fast]
worker_process_init
  ├─ DB + Redis success                                   [unit + prefork]
  ├─ Redis timeout/cancel -> clean degraded state         [unit]
  ├─ hanging ping/cleanup -> shared 3s deadline           [unit + prefork]
  ├─ DB/init failure -> partial cleanup -> NEW            [unit]
  ├─ repeated signal / PID change                         [unit + prefork recycle]
  └─ same PID / foreign loop or role -> fail-fast         [unit]
run_async(factory)
  ├─ signal initialized                                   [unit]
  ├─ direct/eager lazy initialized                        [unit]
  ├─ fresh ContextVar                                     [unit]
  ├─ factory/task error; next task still healthy          [unit]
  └─ nested running loop -> stable error                  [unit]
task execution
  ├─ core/runtime/workline/sys use one Runner + Session   [contract + prefork]
  ├─ handling remains synchronous                         [contract]
  └─ Redis lock outage -> PostgreSQL advisory lock        [integration]
shutdown
  ├─ normal ordered cleanup                               [unit + prefork]
  ├─ stage timeout/error continues remaining cleanup      [unit]
  ├─ pending task cancellation                            [unit]
  ├─ no default executor + normal Runner close             [unit]
  └─ TERM warm / QUIT soft / outer KILL / recycle converge [prefork]
fixture ownership
  ├─ leader TERM/QUIT -> coordinated descendants exit      [integration]
  ├─ coordination timeout -> process-group KILL             [integration]
  ├─ run-scoped queue + broker/backend key prefix cleanup  [integration]
  ├─ QUIT -> visibility timeout -> idempotent redelivery   [integration]
  └─ observer excluded + Worker connections strictly zero  [integration]
capacity
  ├─ PG/SQLite engine parameters                          [unit]
  ├─ role pool limits + replica × process × pool budget   [deployment contract]
  ├─ CLI reserve + isolated integration concurrency       [deployment contract]
  ├─ merged server_settings preserves search_path         [unit]
  └─ application_name state convergence                  [prefork + Docker]
```

关键生产失败模式均有测试或错误处理：

| Failure | Test | Handling | Visibility |
|---|---|---|---|
| Redis init 超时/取消 | unit | 关闭局部资源，DB0 degraded | warning + health degraded |
| Redis 并发 init/reconnect | unit | loop-local single-flight，等待者复用唯一结果 | debug/health state |
| child init 或取消清理卡住 | unit + prefork | 整体 3 秒 deadline，cleanup 使用剩余预算 | startup warning/degraded |
| Engine 在父进程已初始化 | unit/prefork | child fail-fast | startup error |
| Engine 在同 PID 的其他 loop/role 被访问 | unit | DB 入口 owner assertion fail-fast | ownership error |
| coroutine 跨 Context 污染 | unit | fresh Context per message | regression failure |
| task 异常留下事务 | unit/prefork | get_db_context close/rollback | task retry/error log |
| shutdown 某阶段卡住 | unit | stage timeout + continue | stage-specific error |
| Runner.close 等待 executor | unit + prefork | runtime 禁用默认 executor；close best-effort，dev 20 秒/容器 30 秒 KILL | warning + process exit |
| prefork 测试异常遗留 child/queue/Redis 全局状态/连接 | integration fixture | leader TERM/QUIT 协调关闭；超时 group KILL；唯一 queue/key prefix/run-id 回收 | retained failure log |
| QUIT 后消息未立即恢复或重复投递 | integration | 短 visibility timeout 后验证最终重投与幂等状态，采用 at-least-once 合同 | task state + final DB assertion |
| subprocess 隔离环境被 `.env` 覆盖 | settings + integration | 移除 override=True，process env 优先；ready 后核验实际 DB/Redis 目标 | fail-fast safety error |
| Worker 横向扩容超预算 | deployment contract | deployment rejected | budget calculation output |
| 冷启动时 DB 尚未就绪 | deployment contract | 先启动基础设施并等待健康；门禁失败不启动应用 | deploy error + infra status |
| application_name 过长或进程间冲突 | unit + prefork | ASCII 清洗、63 字符上限并保留 role/PID/run-id | config/test failure |
| observer 被误计为 Worker 泄漏连接 | integration | observer 使用独立 CLI 标识、精确匹配 Worker 并排除当前 backend PID | strict zero assertion |
| application_name 覆盖现有 search_path | unit | 合并 server_settings，只覆盖 application_name | connection settings assertion |
| 进程角色与 pool 预算不匹配 | settings + deployment contract | 显式 runtime role，按 role 拒绝超限 pool/overflow | startup/config error |
| SIGKILL 未触发 shutdown | prefork/manual | task-local Session + OS socket close | connection convergence check |

无“无测试、无错误处理且静默”的关键失败路径。

## Public Interfaces and Compatibility

- HTTP API、数据库 schema、Celery task name、queue、参数和返回结构不变。
- 新增内部 `CeleryAsyncRuntime`、`run_async(factory)` 与 DB Engine owner metadata；不作为跨模块业务 API。
- 删除未发布系统中的自定义 Task 资源基类，不保留 facade/alias。
- 新增数据库 runtime role、pool/application-name 和部署容量配置；环境 profile 必须同步更新。
- 配置优先级恢复为 process environment > `.env` > code defaults；属于既有注释所声明语义的修复。

## NOT in scope

- 全仓 Redis 安全/协调调用点审计：加入 TODO，本 PR 只锁定 Worker 与 RuntimeInbox 的降级边界。
- API 多容器横向扩容：加入 TODO；本 PR 修正为真实可运行的单容器、4 Uvicorn worker 拓扑。
- 数据库连接池生产看板与告警实现：并入现有运营 TODO；本 PR 只提供 application_name、预算门禁和验收查询。
- 提高 PostgreSQL `max_connections`：不作为修复手段。
- HTTP API、业务状态机、数据库迁移或 RuntimeInbox 事实源逻辑变化。

## Execution Strategy

Sequential implementation, no parallelization opportunity. Redis manager、Celery runtime、任务模块、连接配置和 prefork fixture 共用生命周期边界；并行 worktree 会增加冲突并模糊失败归因。

执行顺序：失败测试 → Redis 原子初始化 → 单 Runtime/任务迁移 → prefork 根因证明 → 容量/调度护栏 → 多进程与轮换证明 → Docker 冷启动验收。

建议提交：

1. `test(celery): 增加跨事件循环事务泄漏回归`
2. `fix(redis): 原子化异步客户端初始化`
3. `fix(celery): 统一 prefork 子进程异步运行时`
4. `fix(database): 建立拓扑感知连接预算`
5. `test(celery): 覆盖多进程轮换与优雅关闭`
6. `docs(todo): 记录 Redis 审计与部署扩容后续项`

## Implementation Tasks

- [x] **T1 (P1, human: ~2h / CC: ~20min)** — Redis — 原子化 RedisManager 初始化
  - Surfaced by: Outside voice/Code quality — timeout/cancel 可遗留半初始化 pool，并发 reconnect 可覆盖候选 pool
  - Files: `src/database/redis_client.py`, `tests/resilience/test_redis_reconnection.py`
  - Verify: Redis success/timeout/cancel/error、并发 single-flight 与 foreign-loop 测试全部通过
- [x] **T2 (P1, human: ~4h / CC: ~40min)** — Celery runtime — 建立每子进程单 Runner 生命周期
  - Surfaced by: Architecture — 五个任务模块共享 Engine 却使用多套 loop
  - Files: `src/celery_app/async_runtime.py`, `src/celery_app/app.py`, deployment tests
  - Verify: lifecycle、Context、PID/loop/role ownership、分阶段 timeout、无默认 executor 与 best-effort close matrix 通过
- [x] **T3 (P1, human: ~4h / CC: ~40min)** — Celery tasks — 删除 Task Session 缓存并迁移全部任务族
  - Surfaced by: Architecture/Code quality — module-local loop 与重复 Task 基类
  - Files: `src/celery_app/tasks/`, task contract tests
  - Verify: task names/retries/transactions 不变且静态守卫通过
- [x] **T4 (P1, human: ~4h / CC: ~45min)** — Database/deployment — 建立拓扑感知连接预算
  - Surfaced by: Performance/outside voice — worker replicas × concurrency 与 Dockerfile worker 数未钉死，手工 dotenv 还会覆盖部署注入值
  - Files: database settings, Compose/env profiles, `Dockerfile`, deployment guard/tests
  - Verify: process env 优先于 `.env`；prod compose 可解析，四类 role 与 pool 上限匹配，CLI 归 reserve、integration 单独核算，当前生产预算 36；冷启动先完成 infra/live guard 再启动应用，超预算 up/scale 均被拒绝；application_name 可按 role/PID/run-id 精确过滤且不会覆盖 search_path
- [x] **T5 (P1, human: ~4h / CC: ~45min)** — Integration — 覆盖多 PID、轮换和 PostgreSQL 连接收敛
  - Surfaced by: Test/outside voice — concurrency=1 无法证明真实 fork 隔离
  - Files: `tests/integration/`, integration fixtures
  - Verify: prefork 场景无 idle transaction/跨 loop 错误；成功、失败和超时 teardown 后无 descendants、queue/key-prefix 残留，observer 不参与 Worker 计数且目标连接严格为 0
- [x] **T6 (P2, human: ~2h / CC: ~20min)** — Celery shutdown — 分别验证 TERM warm 与 QUIT soft shutdown
  - Surfaced by: Performance/TODO review — TERM 不触发 soft timeout，acks_late 与 ETA retry 需要按真实信号语义验证关闭恢复
  - Files: Celery config and integration tests
  - Verify: `10 < 20 < 30` 配置合同成立；TERM 路径完成事务释放并受 dev/容器 supervisor 兜底；QUIT 路径进入 10 秒 soft window，并在短 visibility timeout 后证明 at-least-once 重投与幂等最终状态
- [x] **T7 (P1, human: ~2h / CC: ~30min)** — Docker acceptance — 冷启动并持续观测连接
  - Surfaced by: Incident acceptance — 本机 `wes_postgres_dev` 连接耗尽
  - Files: no product code; evidence/log output only
  - Verify: 10 分钟无 idle transaction、连接增长或 forbidden logs
- [x] **T8 (P3, human: ~1h / CC: ~10min)** — Follow-up governance — 更新三个已批准 TODO
  - Surfaced by: TODO review — Redis 全仓语义、DB pool observability、API horizontal scaling
  - Files: `TODOS.md`
  - Verify: 每项含 What/Why/Context/Dependencies/Scope

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 后端事故修复，无需产品范围评审 |
| Outside Voice | `/plan-eng-review` fallback | Independent challenge | 3 | ISSUES FOLDED | Claude outside voice 提出 8 项加固，均经用户逐项批准并写入计划 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 4 | CLEAR | 本轮 18 项决策全部固化，0 个未解决项，0 个关键测试缺口 |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 无 UI 范围 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 本轮无需独立 DX 评审 |

**CROSS-MODEL:** 主审与 Claude outside voice 一致认可单 Worker-child 单异步运行时方案；outside voice 补齐了 Celery leader 信号语义、容器 grace、Redis broker 全局状态隔离、角色连接预算、at-least-once 恢复、精确启动 deadline、observer 隔离和 `server_settings` 合并合同，均已纳入实施与测试任务。

**VERDICT:** ENG CLEARED — 可进入实施。

NO UNRESOLVED DECISIONS
