# Transport 调试任务 API 实施计划

> **实施入口：** 使用 `wes-implementation` 按本计划实施。除非用户分别授权，不得分派 Subagent、Commit、Push、创建 PR、Merge 或 Deploy。

**目标：** 在生产 Swagger 中提供一个最小、受权限保护的 Transport 调试入口，并提供一个只读状态入口，让开发和现场运维能够创建真实 `TransportTask`、观察 WES 已持久化的异步 callback 证据，而不绕过既有 Transport 生命周期。

**成功标准：** 调试请求必须复用现有 Transport 创建、提交、ACK、callback、evidence 和终态链路；调试 outcome 不污染粗分业务发布队列；状态接口不暴露原始 callback，不承担业务查询、控制或物理验收职责。

**技术栈：** Python 3.13、FastAPI、Pydantic v2、现有 RBAC、SQLModel/SQLAlchemy、PostgreSQL、pytest、现有 Transport broker E2E。

## 已冻结决策

- 写入口：`POST /api/v1/transport/debug-tasks`，权限 `ops:transport:debug-create`。
- 读入口：`GET /api/v1/transport/tasks/{transport_task_id}`，权限 `ops:transport:read`。
- POST 只返回 `transport_task_id` 和 `client_request_id`，不得固定返回 `PENDING`；幂等 replay 可能命中已终态任务。
- 请求只允许 `RACK_MOVE | RACK_ROTATE | BIN_MOVE | BIN_EXCHANGE` 四个显式分支，全部 `extra="forbid"`。
- 调试请求不接受 `workline_id`。Route 固定构造 `TransportCaller(workline_id="TRANSPORT_DEBUG", station_id=payload.station_id)`；`station_id` 仅是可选诊断上下文。
- GET 返回当前任务投影与 `latest_evidence` 摘要；不返回 `payload_json`、callback 原文、WMS ACK 原文或业务对象。
- `latest_evidence` 指已可靠持久化的任意 `TransportEvidence`，包含 `PENDING | APPLIED | CONFLICT`，按本地 `(received_at DESC, id DESC)` 选择，不能按 WMS timestamp 或最大 `outcome_revision` 排序。
- 所有返回时间均由 Service 投影边界使用 `timezone.to_utc()` 转为 RFC3339 UTC `Z` 字符串。
- GET 复用现有 `TransportService -> TransportRepository`；不引入 Query Framework、QueryPort、新查询 Service、缓存、新表或 migration。
- 调试 outcome 由粗分部署发布器识别精确技术调用方后直接确认消费；其他缺少业务 binding 的 outcome 继续 fail closed。
- 这是未发布系统的直接合同，不增加旧路径、别名、shim、兼容字段或数据迁移逻辑。

## What already exists

| 现有能力 | 当前真源 | 本计划如何复用 |
|---|---|---|
| 四类 Transport 创建与幂等 | `src/app/transport/contracts.py`、`service.py` | POST 只把严格 HTTP DTO 转成现有领域 DTO，并调用现有 `TransportPort` |
| 生产 Transport runtime | `src/app/transport/composition.py`、`src/register.py` | Route 只读取 `app.state.transport_runtime`，不自行构造 Service/Repository |
| 任务、成员、evidence 持久化 | `models.py`、`repository.py` | GET 复用现有表和索引，不增加 schema |
| WMS callback 入口 | `src/app/wms_adapter/v1/events.py` | 不增加 callback endpoint；GET 只读取该入口已经持久化的 evidence |
| 异步提交、evidence 应用和 outcome | `src/app/transport/service.py`、Celery tasks | 调试任务走同一生产生命周期，不增加同步捷径 |
| 粗分 outcome 业务桥接 | `deployment/_rough_sorter_transport.py` | 仅增加精确的调试调用方分支，不放宽普通 missing-binding 错误 |
| RBAC、响应 envelope、全局异常处理 | `RequirePermission`、`response_builder`、`NotFoundException` | 沿用现有机制，不重复实现授权或错误协议 |
| Transport 生产接线 E2E | `tests/e2e/transport/test_transport_production_wiring.py` | 把直接 Port 创建改为真实 POST，并用 GET 观察状态 |

## NOT in scope

- 不增加 Preflight、列表查询、过滤查询、分页、取消、清理、重试、强制执行或状态修改接口；它们不是当前诊断目标。
- 不增加模拟 callback 的入口；callback 必须继续从现有 `/api/v1/wms/events` 进入。
- 不提供 CallbackLog 通用查询或 callback 原文查看；Transport evidence 已有独立所有权，原文仍按日志/数据库 Runbook 诊断。
- 不新增 Query Framework、通用任务中心、事件平台、缓存、表、索引或 migration；单任务主键查询和一条 evidence 查询已足够。
- 不给默认角色自动授予新权限；现场授权继续走现有权限管理。
- 不改变 WMS wire、RCS 协议、Worker 调度、Transport 状态机或物理安全联锁。
- 不把 Swagger `202`、WMS ACK、WES `SUCCEEDED` 或本地 E2E 当成现场设备完成和业务验收。
- 不归档 `docs/hardware/`；本计划不修改厂商原始资料。

## 架构与数据流

```text
开发/运维 Swagger
       |
       | POST /transport/debug-tasks [debug-create]
       v
transport/v1/tasks.py --严格四分支 DTO--> TransportRuntime.port
       |                                      |
       |                                      v
       |                               TransportService
       |                                      |
       |                                      v
       |                          PostgreSQL TransportTask
       |                                      |
       |                              submit worker -> WMS/RCS
       |                                      |
       |     /wms/events -> TransportEvidence | -> evidence worker
       |                                      v
       |                              TransportTask 终态
       |
       | GET /transport/tasks/{id} [transport-read]
       v
TransportRuntime.service -> TransportRepository
       |                     |- task by unique task id
       |                     `- latest evidence by received_at,id
       v
只读状态投影（无 raw payload）

终态 outcome -> RoughSorterTransportOutcomePublisher
                    |- caller == TRANSPORT_DEBUG -> 确认消费，不桥接业务
                    `- 其他 caller -> 必须存在粗分 binding，否则失败
```

边界必须保持：

```text
API -> Service -> Repository -> Database
```

- POST 只调用 `runtime.port`；GET 只调用 `runtime.service.get_task_snapshot()`。
- API 不导入 Repository、数据库模型、WMS Client 或粗分部署适配器。
- 基础 Transport Service 不依赖粗分业务；粗分部署适配器依赖基础 `TransportOutcome` 和技术调用方常量。
- 流程不复杂，不在生产代码加入装饰性 ASCII 注释；本节是唯一流程图真源。

## HTTP 合同

### POST `/api/v1/transport/debug-tasks`

公共字段只有：

```text
client_request_id: canonical lowercase UUIDv7
station_id?: 1..100 非空文本
kind: 四个固定字面量之一
data: 对应 kind 的闭集对象
```

四个 `data` 分支：

```text
RACK_MOVE:     rack_id + source:RACK_POSITION + target:RACK_POSITION + target_face
RACK_ROTATE:   rack_id + position:RACK_POSITION + target_face
BIN_MOVE:      moves[1..4] {bin_id + source/target:RACK_BIN_SLOT|HANDOFF_POSITION}
BIN_EXCHANGE:  exchange_pairs[1..2] {left_bin_id + left_location + right_bin_id + right_location}
```

使用两个私有 Pydantic 基类即可：`_StrictApiModel(extra="forbid")` 和 `_DebugTaskRequestBase(client_request_id, station_id)`。具体四个分支继续显式定义；不增加动态 schema registry。

成功为 HTTP `202`、code `1004`：

```json
{
  "transport_task_id": "transport-...",
  "client_request_id": "019..."
}
```

不得返回 `status`，不得接受 `workline_id`、`transport_task_id`、`operation_id`、`timestamp`、`force` 或自由字典。Swagger 必须提供且仅提供四个可校验示例。

### GET `/api/v1/transport/tasks/{transport_task_id}`

成功为 HTTP `200`、code `1000`，data 固定为：

```text
transport_task_id
client_request_id
submit_operation_id
kind
status: PENDING | ACCEPTED | REJECTED | SUCCEEDED | FAILED | RECONCILING
reason_code?
created_at: RFC3339 UTC Z
updated_at: RFC3339 UTC Z
latest_evidence?:
  operation
  operation_id
  outcome_revision?
  status: PENDING | APPLIED | CONFLICT
  conflict_code?
  received_at: RFC3339 UTC Z
  processed_at?: RFC3339 UTC Z
```

任务存在但尚无 callback 时 `latest_evidence=null`。不存在的任务由 Service 抛出 `NotFoundException(resource_type="TransportTask", resource_id=...)`，沿用全局 `404 / 3000` 响应；runtime 缺失或已关闭返回 `503`。

## 失败模式

| 新路径 | 生产失败方式 | 测试 | 处理与现场可见性 |
|---|---|---|---|
| POST schema | kind、UUIDv7、长度、位置联合或额外字段非法 | API 参数化测试 | FastAPI `422`，Port 不得被调用；清晰可见 |
| POST dispatch | 幂等 payload 冲突或活动资源冲突 | API + Service 既有测试 | `409`；清晰可见 |
| POST dispatch | 领域 DTO 违反现有不变量 | API 测试 | `400`；清晰可见，不复制领域校验 |
| POST/GET runtime | runtime 缺失或 `closed=True` | API 测试 | `503`；清晰可见 |
| GET task | task id 不存在 | API + Service 测试 | `404 / 3000`；清晰可见 |
| GET evidence | callback 已持久化但尚未处理 | Service + E2E | 返回 `PENDING`，不能误报未收到 |
| GET evidence | 最新 callback 发生语义冲突 | Service 测试 | 返回 `CONFLICT + conflict_code`，不暴露 raw payload |
| GET ordering | 同一时刻多条 evidence 顺序不稳定 | PostgreSQL Repository 集成测试 | 以 `received_at DESC, id DESC` 唯一确定 |
| GET timestamps | PostgreSQL 返回 UTC naive datetime | Repository + API 测试 | Service `to_utc()` 后输出 `Z`；不产生无时区字符串 |
| 异步生命周期 | WMS 无 callback、提交结果不确定 | 既有 Transport 测试 + E2E | 进入 `RECONCILING` 并可由 GET 观察；不是物理完成 |
| outcome 发布 | 调试任务无粗分 binding | 部署发布器测试 + E2E | 精确技术 caller 被确认；普通 missing binding 仍失败 |
| 未知异常 | 程序缺陷或数据库异常 | 全局异常处理既有覆盖 | 交给全局 `500`，Route 不吞异常 |

无“无测试 + 无处理 + 静默失败”的关键路径；计划完成后 critical gap 为 0。

## 测试所有权

```text
tests/api/test_transport_tasks.py
  |- RBAC、OpenAPI 四分支/examples、严格结构边界
  |- POST 202 身份响应（无 status）和异常映射
  `- GET 200/404/503、UTC Z、无 raw payload

tests/runtime/transport/test_transport_service.py
  |- snapshot 映射、无 evidence
  |- latest PENDING/APPLIED/CONFLICT
  `- missing task -> NotFoundException

tests/integration/transport/test_transport_repository.py
  `- PostgreSQL received_at/id 确定排序与真实 naive datetime

tests/deployment/test_rough_sorter_plugin_startup.py
  |- TRANSPORT_DEBUG 无 binding 可确认
  `- 普通 caller 无 binding 仍抛错

tests/e2e/transport/test_transport_production_wiring.py
  POST -> task -> submit -> callback -> evidence worker -> GET terminal
  `- publish batch 不反复领取 debug outcome
```

API 结构约束需参数化覆盖：未知/缺失 `kind`、非 canonical UUIDv7、空白/101 字符文本、`moves` 0/5、`exchange_pairs` 0/3、错误 position union、各层额外字段，以及禁止的身份字段。相同 source/target、重复资源、rack face 等领域不变量继续由既有 Transport 合同测试唯一拥有，不在 API 测试重复实现。

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~45min / Codex: ~10min)** — 合同真源 — 冻结本地运维观察能力
  - Surfaced by: Outside Voice — GET 与 Approved Transport 合同/SRS 的“首版无状态查询”冲突。
  - Files: `docs/contracts/transport-fulfillment-contract.md`、`docs/architecture/SRS.md`、`docs/runbooks/transport-operations.md`
  - Verify: `git diff --check`；人工核对三份文档均区分本地观察、北向合同和物理验收。

- [ ] **T2 (P1, human: ~2h / Codex: ~25min)** — API 合同 — 先建立 POST/GET 失败测试
  - Surfaced by: Architecture/Code Quality/Test Review — 四分支闭集、独立权限、真实 HTTP 状态、时间与错误边界需冻结。
  - Files: `tests/api/test_transport_tasks.py`、`tests/architecture/test_transport_boundaries.py`
  - Verify: `uv run pytest tests/api/test_transport_tasks.py tests/architecture/test_transport_boundaries.py -q` 首次因路由/投影不存在而 RED；不得放宽断言取得 GREEN。

- [ ] **T3 (P1, human: ~2h / Codex: ~30min)** — Transport API — 实现最小创建和状态路由
  - Surfaced by: Architecture/Code Quality Review — 只需一个 `tasks.py`、两个简单私有基类和一个 runtime 检查 helper。
  - Files: `src/app/transport/v1/__init__.py`、`src/app/transport/v1/tasks.py`、`src/app/transport/contracts.py`、`src/register.py`
  - Verify: T2 测试 GREEN；OpenAPI 只有四个 POST 分支和两个精确权限。

- [ ] **T4 (P1, human: ~2h / Codex: ~25min)** — 状态投影 — 复用 Service/Repository 查询最新 evidence
  - Surfaced by: Architecture/Test/Outside Voice — 禁止 API 直查 DB，必须冻结 evidence 范围、排序和 UTC Z。
  - Files: `src/app/transport/service.py`、`src/app/transport/repository.py`、`tests/runtime/transport/test_transport_service.py`、`tests/integration/transport/test_transport_repository.py`
  - Verify: `uv run pytest tests/runtime/transport/test_transport_service.py -q`；PostgreSQL 就绪时运行 `uv run pytest tests/integration/transport/test_transport_repository.py -q`，skip 不算通过。

- [ ] **T5 (P1, human: ~1h / Codex: ~15min)** — outcome 边界 — 防止调试终态污染业务发布队列
  - Surfaced by: Outside Voice — 全局 outcome 批次会反复领取无粗分 binding 的调试任务。
  - Files: `deployment/_rough_sorter_transport.py`、`tests/deployment/test_rough_sorter_plugin_startup.py`
  - Verify: 聚焦测试证明 `TRANSPORT_DEBUG` 被确认、普通 missing binding 仍 fail closed。

- [ ] **T6 (P1, human: ~2h / Codex: ~25min)** — 生产接线 — 用公开 API 闭合异步生命周期与 HEAVY ownership
  - Surfaced by: Test Review — 现有 E2E 绕过 API 且未执行 outcome publisher。
  - Files: `tests/e2e/transport/test_transport_production_wiring.py`、`docs/architecture/heavy-test-impact.toml`
  - Verify: E2E 使用 POST 创建、GET 观察 `PENDING/APPLIED/terminal`，并执行发布批次；selector 只运行命中的 HEAVY manifest。

## 实施顺序与验证

Sequential implementation, no parallelization opportunity. 所有任务共享 Transport API、Service、Repository、生产接线或同一 E2E 快照，拆 worktree 会增加合同漂移和合并成本。

实施顺序：`T1 -> T2(RED) -> T3/T4/T5(GREEN) -> T6 -> 最终门禁`。

首个生产补丁前：

1. 运行 `git status --short`、`npx gitnexus status`，记录无关 dirty 指纹。
2. 对 `register_routers`、四个 `TransportPort` 方法、`TransportService.get_task_snapshot` 新调用点、`TransportRepository` 新查询和 `RoughSorterTransportOutcomePublisher.publish` 做一次批量影响分析。
3. HIGH/CRITICAL 影响链在修改前报告；GitNexus 不可用时以精确 `rg`、调用点、测试所有权和 HEAVY mapping 降级。

TDD 与聚焦验证：T2、T4、T5、T6 各自先写该行为切片的失败测试，再做最小实现；不能用后续整目录失败代替调用点和测试所有权清单。

```bash
uv run pytest tests/api/test_transport_tasks.py tests/architecture/test_transport_boundaries.py -q
uv run pytest tests/runtime/transport/test_transport_service.py -q
uv run pytest tests/deployment/test_rough_sorter_plugin_startup.py -q
```

PostgreSQL 与 E2E 所需环境显式就绪后再运行；不能先跑名义 skip：

```bash
uv run pytest tests/integration/transport/test_transport_repository.py -q
uv run pytest tests/e2e/transport/test_transport_production_wiring.py -q
```

更新 `docs/architecture/heavy-test-impact.toml`：新 `src/app/transport/v1/{__init__.py,tasks.py}` 精确映射生产接线 E2E；其他生产文件复用或补齐其真实影响 mapping。最终可执行快照运行：

```bash
git diff --check
./scripts/git-quality-gate.sh --profile quality
uv run scripts/select_heavy_tests.py --scope unstaged
./scripts/run_selected_heavy_local.sh --scope unstaged
npx gitnexus detect-changes --scope staged --repo "$PWD"  # 仅在获得 Commit 授权并形成 staged 快照后
```

只有聚焦测试、QUALITY、selector 命中的 HEAVY 和必要 PostgreSQL/E2E 在同一最终可执行快照通过，才能报告代码实施完成。文档实施部分不新增正文断言测试。

## Plan Self-Review

- 基础/业务边界：Transport API/Service/Repository 可独立运行和测试；粗分发布器只在部署层消费 outcome。
- KISS/YAGNI：两个 endpoint、一个路由模块、现有 Service/Repository、零新表、零通用框架。
- DRY：复用 TransportPort、RBAC、响应 envelope、NotFoundException、时间工具和现有 callback evidence。
- 测试所有权：API、Service、Repository、部署发布器、E2E 各自只验证所属语义。
- 现场边界：GET 证明 WES 任务与 callback evidence 状态，不证明 RCS 物理动作或 WMS 业务完成。
- TODO：0 项；没有值得延期的新能力，所有必要问题已进入本计划，其他想法明确列入 NOT in scope。
- 外部独立审查：发现 4 项 P1，均已由用户选择推荐方案并折入计划；无未解决冲突。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 3 | CLEAR | 14 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**VERDICT:** ENG CLEARED — ready to implement

NO UNRESOLVED DECISIONS
