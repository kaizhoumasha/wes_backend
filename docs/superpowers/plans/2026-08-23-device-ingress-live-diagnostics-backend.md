# Device Ingress Live Diagnostics Backend Implementation Plan

> 实施时使用 `wes-implementation`，按行为切片执行 TDD；Commit、Push、PR、Merge、Deploy 分别取得授权。

**Goal:** 交付仅超级用户可访问、无历史回放的 ECS callback 实时诊断 SSE，并把既有 `MANUAL_DEBUG` 收敛为基于 ECS 实时状态、带操作人审计和发送前复检的可靠联调入口。

**Spec:** `docs/superpowers/specs/2026-08-23-device-ingress-live-diagnostics-design.md`

## 1. 执行原则

- 实施前确认统一 ECS wire 与现有 `MANUAL_DEBUG` 已进入目标 base；未进入则停止，不复制散落 commit。
- 不创建第二套 Redis SSE service、ECS status client、adapter provider、权限体系或 task type registry。
- callback 持久化/ACK 属可靠链；SSE publish 属 best-effort 诊断旁路。
- 生产代码变化按 TDD；迁移使用 Alembic generator；纯文档不增加测试。
- 本地只连接仓库 Mock，不连接示例或现场真实 ECS URL。

## 2. 冻结变更面

### 生产文件

- `src/app/sys/services/event_stream_service.py`：保持 `publish(event_type, payload)` 合同，增加通用指定 channel 发布能力；提供共享 live-only subscribe iterator。
- `src/app/sys/v1/events.py`：复用共享 iterator，保持现有 sys stream 行为。
- `src/app/device/contracts.py`：diagnostic event、preflight 和 audit snapshot DTO。
- `src/app/device/ecs_adapter.py`：增加 `fetch_statuses`，保留 `fetch_status` 外部合同。
- `src/app/device/services/device_command_admission.py`：共享纯运行态准入。
- `src/app/device/services/device_command_service.py`：preflight、idempotency-first create、reason/created_by。
- `src/app/device/services/device_dispatch_service.py`：MANUAL_DEBUG send 前复检；业务派发不变量保持。
- `src/app/device/services/device_evidence_service.py`：提交后构造 update snapshot 并 best-effort publish。
- `src/app/device/services/__init__.py`：导出新增 service 能力。
- `src/app/device/models/command.py`：`execution_reason` 与条件约束。
- `src/app/device/v1/ecs_callback.py`：attempt metadata 与提交后 publish。
- `src/app/device/v1/evidence_stream.py`：superuser-only SSE route 与过滤。
- `src/app/device/v1/command.py`：superuser-only preflight/create/detail。
- `src/app/device/__init__.py`：在当前真实组合根注册 evidence stream router。
- `src/app/device/composition.py`：复用既有 provider，注入 stream publisher。
- `nginx/conf.d/default.conf`：新增 device SSE 精确 location。
- `migrations/versions/<generated_revision>_audit_manual_device_command.py`：新增 reason 及约束。

明确不修改：

- `src/app/admin/services/authorization_bootstrap_service.py`
- 现有权限目录与内置角色规则
- `APIAccessLog`
- 未被运行时使用的 `src/app/device/v1/__init__.py`

### 测试所有权

- adapter wire：`tests/contracts/device/`
- callback/SSE/debug HTTP：`tests/api/`
- command/evidence/dispatch：`tests/runtime/device_command/`
- PostgreSQL 约束：`tests/integration/device_command/`
- Nginx：`tests/deployment/`
- 生产组合：现有 device command E2E owner
- HEAVY：`docs/architecture/heavy-test-impact.toml`

## 3. Task 0 — 基线、影响分析与隔离

1. 记录 branch、HEAD、dirty/untracked、worktree 列表；不得覆盖当前三份未跟踪文档。
   - 实施基线为 `4cb941c4`；该 commit 已包含 strict JSON 与 excessive nesting/`RecursionError` 防护。实施中须保留这些行为，不得覆盖。
2. 按仓库规则决定是否创建隔离 worktree；普通单任务不机械创建。
3. 确认 `EcsAdapter.fetch_status`、`/commands/debug`、callback evidence、`EventStreamService` 和当前 router 组合根均存在。
4. 对以下生产符号执行一次 GitNexus upstream impact，并缓存结果：
   - `EventStreamService.publish`
   - `EcsAdapter.fetch_status`
   - `DeviceCommandService.create_manual_debug_command`
   - `DeviceDispatchService.dispatch_one`
   - `DeviceEvidenceService.accept_result/accept_event/process_one`
5. HIGH/CRITICAL 影响在首个生产补丁前报告并取得范围授权。

验证：`git status --short --branch`、`npx gitnexus status`、精确 `rg` 调用点和 HEAVY mapping。

## 4. Task 1 — 复用 adapter 增加设备列表

### RED

增加合同测试覆盖：

- 无 `device_code` 时 query 为空，并按 wire 顺序返回多台设备。
- 列表拒绝空 devices、重复 device identity、非 JSON object 和超限响应。
- 单设备查询继续拒绝零条、多条和 identity mismatch。
- 所有既有 `fetch_status` 调用者签名和返回类型不变。

### GREEN

- 新增 `fetch_statuses(device_code: str | None = None)`。
- 抽取一个共享 decoder，复用现有 response limit、Content-Type、JSON 与 Pydantic 校验。
- `fetch_status` 调用 `fetch_statuses(device_code)` 后执行 exactly-one 与 identity check。

验证：adapter contract tests + 既有 dispatch 聚焦回归 + `fetch_status(` 调用点残留扫描。

## 5. Task 2 — 提取共享运行态准入

### RED

表驱动覆盖 identity、offline、非 AUTO、非 IDLE、已有 command 和 unsupported task type 的精确失败码；同时固定现有业务 dispatch 的 binding、contract、freshness 行为。

### GREEN

- 新建一个无 I/O 的运行态准入模块。
- 业务 `ensure_admissible` 保留 binding/contract/freshness 后调用共享运行态检查。
- MANUAL_DEBUG 在共享运行态检查后额外校验 `supported_commands`。
- 不新增 policy class、registry、provider 或配置项。

验证：new admission tests + existing dispatch tests。

## 6. Task 3 — Superuser preflight 与审计创建

### RED

API 测试固定：

- 非 superuser 对 preflight/create/detail 均为 403。
- superuser 的 preflight 返回全部统一 status DTO；endpoint 无效为 400，ECS 不可用为 503。
- create 缺失、空白、超长 reason 被拒绝。
- API 从认证上下文传递 `request.state.user_id`。

Service 测试固定：

- 相同 `client_request_id` 与相同不可变字段直接返回原命令，且不访问 ECS。
- 同 identity 但 payload、reason 或 created_by 不同返回冲突。
- 仅新 identity 执行远程 preflight；失败不写命令。
- preflight 成功后返回事务二次锁 identity/device slot，避免竞态重复创建。

### GREEN

- preflight 通过已有 `DeviceEndpointAdapterProvider.get_adapter(endpoint)` 调用 `fetch_statuses()`。
- preflight/create/detail route 删除原 `[ops:device:*]` summary 标记与 `RequirePermission` 依赖，只保留 `require_superuser`；生成权限目录中不得继续产生这些诊断权限。
- create 增加 `execution_reason`、`created_by`；`payload_digest` 不混入审计字段。
- 新 migration 由 generator 生成随机 revision。
- PostgreSQL 条件约束：MANUAL_DEBUG reason trim 后非空且既有 `created_by` 非空；非 MANUAL_DEBUG reason 必须为 NULL。
- `created_by` 复用 `EnterpriseMixin` 既有字段。

验证：API/service 聚焦测试 + 在独占干净 PostgreSQL 上验证 predecessor→head、fresh-head 和约束。

## 7. Task 4 — MANUAL_DEBUG 发送前复检

### RED

覆盖：

- `fetch_status` 一定发生在 `submit_command` 之前。
- status 不可用沿既有 retryable-not-sent/deadline 路径，绝不 submit。
- offline/mode/state/busy/unsupported 以精确失败码结束，绝不 submit。
- status 查询前或查询后 deadline 到期都绝不 submit。
- 非 MANUAL_DEBUG 业务派发回归完全不变。

### GREEN

删除 manual branch 直接 submit 的路径，复用 Task 2 准入函数；每次状态查询后重新锁定 claimed row，再持久化判断或生成 submit snapshot。

验证：dispatch 聚焦测试 + 生产 wiring Mock E2E；不得访问真实 LAN。

## 8. Task 5 — 参数化现有 SSE 基础能力

### RED

在现有 sys stream tests 上增加：

- 默认 channel 和现有 sys stream 输出不变。
- 指定 device channel 时 publish/subscribe 使用 `device:evidence:stream`。
- malformed message 被跳过；cancel 时 unsubscribe/close。
- Redis 缺失或 publish 失败安全降级。

### GREEN

- `EventStreamService.publish(event_type, payload)` 的签名和默认频道行为保持不变；新增通用 `publish_to(channel, event_type, payload)`。
- 抽出共享 live-only Redis subscribe iterator；sys route 与 device route 共用。
- 基础层只处理 event envelope、channel、heartbeat/cancel/cleanup，不理解 device filter 或业务 DTO。
- 不创建 `DeviceIngressStreamService`。

验证：现有 sys SSE tests + 新 channel 聚焦测试。

## 9. Task 6 — callback attempt 与 evidence update

### RED

覆盖 RESULT/EVENT 的 ACCEPTED、DUPLICATE、CONFLICT、unknown identity、非法 JSON、DTO invalid 和 413：

- 每次请求有独立 UUID7 `request_id`。
- duplicate 可共享 evidence_id，但仍是独立 attempt。
- 合法 DTO 才携带解析对象；非法/超限 `raw_payload=null`。
- 413 只上报 `observed_body_bytes`，不冒充完整 Content-Length。
- publish failure 不改变原 HTTP status/body。
- update 仅在 APPLIED/RECONCILING 提交后发布，失败不回滚。

### GREEN

- callback body reader 返回 validated DTO、parsed object 和 observed bytes；拒绝异常只携带安全摘要。
- 在现有 strict UTF-8、非标准常量、非有限浮点数和 excessive nesting/`RecursionError` 防护上增量扩展，不重写或删除这些当前行为与回归测试。
- route 在 service 返回或 rejection 确定后调用参数化 `EventStreamService`。
- `process_one` 在事务外发布捕获的最终 snapshot。
- RESULT raw payload 严格遵循当前 wire，不接受或展示虚构的 `source_event_id`。

验证：callback API + evidence service tests。

## 10. Task 7 — Superuser SSE route 与 Nginx

### RED

- 无 token 为 401；authenticated non-superuser 为 403；superuser 可订阅。
- query token 被忽略。
- filters 只接受合同 enum/token，并同时过滤 attempt/update。
- 输出不含 SSE `id`；heartbeat、disconnect 和 header 正确。
- Nginx 对 exact device stream path 禁用 buffering/cache/gzip，timeout 大于 heartbeat。

### GREEN

- 新建 `src/app/device/v1/evidence_stream.py`，依赖 `require_superuser`。
- device route 只负责 DTO/filter/SSE framing，订阅复用 Task 5 基础 iterator。
- 在 `src/app/device/__init__.py` 注册 router；不得创建不存在的 `v1/router.py` 第三组合根。
- Nginx 复制现有 SSE exact location 的必要配置；当前只有两个路径，不抽象 include。

验证：SSE API + sys stream regression + Nginx deployment tests。

## 11. Task 8 — 合同、所有权与最终门禁

1. 更新 `docs/architecture/device-command-contract.md`、`docs/integration/third_party_integration_whitepaper.md`；只在实际被索引时修改 file index。
2. 闭合所有 `fetch_statuses`、admission、reason、superuser route、device channel 的直接/间接测试 owner。
3. 更新 `heavy-test-impact.toml`，只运行 selector 选中的 manifest。
4. 对最终 executable tree 依次完成：

```bash
uv run pytest <本计划列出的聚焦测试> -q
./scripts/git-quality-gate.sh --profile quality
uv run scripts/select_heavy_tests.py --scope unstaged
./scripts/run_selected_heavy_local.sh --scope unstaged
git diff --check
npx gitnexus detect-changes --scope unstaged --repo "$PWD"
```

5. 使用 `./scripts/dev-env.sh up` 与 `check`，仅对仓库 Mock 验证 callback、SSE、preflight、create 与生命周期；结束使用 `down`，不得带 `-v`。
6. 固定 backend commit 后，前端才允许执行 `contract:freeze`。Commit 仍需单独授权。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | ---: | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 本轮未运行 |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | 本轮未运行 |
| Eng Review | `/plan-eng-review` | Architecture & tests | 1 | CLEAR | 后端 10 项问题均已折叠，0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 本轮未运行 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 本轮未运行 |

- **VERDICT:** ENG CLEARED — 当前后端计划可实施。
- **SCOPE:** 功能不缩水；删除重复 SSE service、全局 RBAC 改造和错误 router 路径。
- **LOAD-BEARING DECISION:** 参数化既有 `EventStreamService`，诊断 route 统一 `require_superuser`，create 保持 idempotency-first。

NO UNRESOLVED DECISIONS
