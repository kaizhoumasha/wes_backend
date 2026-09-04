# Transport Debug Auto Run Frontend Implementation Plan

> **2026-09-03 修订：** 现场货架、料箱和原槽位改为操作员直接录入，不再加载或校验 `RackBinMount` 基础数据。本计划中与 `MOUNTED` 查询、选择器有关的原实施步骤已被该修订取代，保留其余状态观察与安全边界。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/ops/transport-diagnostics` 的固定“510056 联调步进”替换为可选货架、按面分组选择 1～4 个料箱的“自动联调”，并从后端持久轮次恢复和观察进度。

**Architecture:** 前端只负责加载当前 MOUNTED rack-bin mounts、构建冻结配置、展示 exact payload 预览、启动/查询/终止后端 run。后端 run detail 是唯一权威状态；专用 SSE 只触发重新 GET，断线时低频查询兜底。现有 Transport task 列表和手工 debug/reset 功能继续保留。

**Tech Stack:** Vue 3 Composition API、TypeScript strict、Element Plus、Alova contract client、authenticated SSE、Vitest、Vue Test Utils、pnpm

**Spec:** `/Users/kaizhou/codeDev/wes_backend/docs/superpowers/specs/2026-09-02-transport-debug-auto-run-design.md`

## Global Constraints

- 本计划只能在后端 API 已合入干净后端 `develop` 后开始；`pnpm contract:freeze` 的 `--backend-root` 必须指向该 checkout。
- 前端仓库必须显式使用 `/Users/kaizhou/codeDev/wes_frontend`；不得用相对路径推断后端。
- 当前前端 worktree 中预存的 `docs/designs/device-diagnostics-debug-epoch-history-cleanup.md` 属于其它工作，执行者不得修改、暂存或删除。
- 不使用独立 worktree；开始前需在前端干净 `develop` 上创建 `codex/transport-debug-auto-run-ui`。若仍有上述 dirty 文件，先停下取得用户处置指示，不得擅自移动或清理。
- face 是原始不透明字符串；输入、预览、POST body 和详情显示必须逐字一致，不 trim 回写、不转数字、不做 A/B 映射。
- 每组 1～4 个料箱；同一 face 原始字符串和同一 bin 不得重复。
- 前端不创建单步 Transport task、不 reset 自动轮次 task、不根据时间或 SSE 推进。
- `NEEDS_ATTENTION` 不提供跳步、伪造扫码、强制成功或换 ID 重发。
- commit、push、PR、merge、deploy 均需各自显式授权；下面 commit 步骤仅定义审核边界。

---

### Task 1: 从干净后端 develop 冻结新合同

**Files:**
- Modify: `contracts/openapi.current.json`
- Modify: `contracts/permissions.current.json`
- Modify: `.contract-sync-record.json`
- Modify: `.permission-sync-record.json`
- Modify: `src/api/generated/openapi-types.ts`
- Create: `src/api/generated/openapi-metadata/CreateTransportDebugRunRequest.ts`
- Create: `src/api/generated/openapi-metadata/AbortTransportDebugRunRequest.ts`
- Create: `src/api/generated/openapi-metadata/TransportDebugRunResponse.ts`
- Create: `src/api/generated/openapi-metadata/TransportDebugRunPageResponse.ts`
- Create: `src/api/generated/openapi-metadata/TransportDebugRunStepResponse.ts`
- Create: `src/api/generated/openapi-metadata/TransportDebugRunBinRequest.ts`
- Create: `src/api/generated/openapi-metadata/TransportDebugRunFaceGroupRequest.ts`
- Create: `src/api/generated/openapi-metadata/TransportDebugRunBinResponse.ts`
- Create: `src/api/generated/openapi-metadata/TransportDebugRunFaceGroupResponse.ts`
- Create: `src/api/generated/openapi-metadata/TransportDebugRunUpdated.ts`
- Modify: `src/api/generated/openapi-metadata/_RackRotateData.ts`
- Modify: `src/api/generated/openapi-metadata/index.ts`
- Modify: `src/api/generated/openapi-metadata.ts`
- Modify: `src/types/generated/zod-schemas.ts`
- Create: `src/api/generated/permissions/user_api/ops/transport-debug-run.ts`
- Modify: `src/api/generated/permissions/index.ts`
- Modify: `src/api/modules/transport.ts`
- Create: `tests/unit/api/transport-contract.test.ts`
- Create: `tests/unit/api/generated-permissions.test.ts`

**Interfaces:**
- Consumes: 干净后端 `develop` 的 canonical OpenAPI/permission exporter。
- Produces: debug-run path types、response schemas、`OPS_PERMISSIONS.transportDebugRun.list|read|start|stream|abort`。

- [ ] **Step 1: 冻结 Git 基线并创建普通分支**

```bash
cd /Users/kaizhou/codeDev/wes_frontend
git status --short --branch
git switch develop
git pull --ff-only
git switch -c codex/transport-debug-auto-run-ui
```

Expected: 创建前 `develop` 与 origin 同步且工作树干净。任何已有 dirty 文件都必须先停下处理，不执行 stash/reset/clean。

- [ ] **Step 2: 冻结 canonical contract**

```bash
pnpm contract:freeze -- --backend-root /Users/kaizhou/codeDev/wes_backend
pnpm generate:types
pnpm generate:zod
pnpm generate:permissions
```

Expected: OpenAPI 包含五个 debug-run endpoints；rotate `position` union 包含 `RACK`；权限文件包含四个 debug-run leaf。

- [ ] **Step 3: 写生成合同回归测试**

在 `tests/unit/api/transport-contract.test.ts` 断言：

```typescript
type CreateRun = ContractRequestBody<'/api/v1/transport/debug-runs', 'post'>
const input: CreateRun = {
  rack_id: '510056',
  face_groups: [
    { face: '90', bins: [{ bin_id: 'A000001922', slot_id: '510056A3F2C101' }] }
  ]
}
expect(input.face_groups[0].face).toBe('90')
```

并用 `expectTypeOf` 证明 rotate API 接受 `{kind:'RACK',location_code:'510056'}`，run status 包含 `NEEDS_ATTENTION`/`ABORTED`。

- [ ] **Step 4: 运行契约门禁和再生成无差异检查**

```bash
pnpm contract:test
pnpm contract:verify
pnpm permission:verify
pnpm generate:types
pnpm generate:zod
pnpm generate:permissions
git diff --stat
```

Expected: 前三个命令 PASS；第二轮生成前后的 `git diff --stat` 完全相同，不产生新的生成漂移。

- [ ] **Step 5: Commit gate（需单独授权）**

```bash
git add contracts/openapi.current.json contracts/permissions.current.json .contract-sync-record.json .permission-sync-record.json src/api/generated/openapi-types.ts src/api/generated/openapi-metadata/CreateTransportDebugRunRequest.ts src/api/generated/openapi-metadata/AbortTransportDebugRunRequest.ts src/api/generated/openapi-metadata/TransportDebugRunResponse.ts src/api/generated/openapi-metadata/TransportDebugRunPageResponse.ts src/api/generated/openapi-metadata/TransportDebugRunStepResponse.ts src/api/generated/openapi-metadata/TransportDebugRunBinRequest.ts src/api/generated/openapi-metadata/TransportDebugRunFaceGroupRequest.ts src/api/generated/openapi-metadata/TransportDebugRunBinResponse.ts src/api/generated/openapi-metadata/TransportDebugRunFaceGroupResponse.ts src/api/generated/openapi-metadata/TransportDebugRunUpdated.ts src/api/generated/openapi-metadata/_RackRotateData.ts src/api/generated/openapi-metadata/index.ts src/api/generated/openapi-metadata.ts src/types/generated/zod-schemas.ts src/api/generated/permissions/user_api/ops/transport-debug-run.ts src/api/generated/permissions/index.ts src/api/modules/transport.ts tests/unit/api/transport-contract.test.ts tests/unit/api/generated-permissions.test.ts
git commit -m "chore(contract): 冻结自动联调接口"
```

### Task 2: 实现货架/料箱配置模型和 exact payload 预览

**Files:**
- Create: `src/views/ops/transport-diagnostics/useTransportDebugRunConfig.ts`
- Create: `tests/unit/views/ops/transport-diagnostics/useTransportDebugRunConfig.test.ts`
- Consume: `src/api/modules/rackBinMounts.ts`

**Interfaces:**
- Consumes: `rackBinMountsApi.query()` 和 generated `RackBinMountsItem`。
- Produces: `TransportDebugFaceGroupDraft`、`useTransportDebugRunConfig()`、`buildTransportDebugRunInput()`、`buildTransportDebugRunPreview()`。

- [ ] **Step 1: 写配置失败测试**

测试数据：

```typescript
const mounts = [
  { rack_code: '510056', rack_slot_code: '510056A3F2C101', bin_code: 'A000001922', mount_status: 'MOUNTED' },
  { rack_code: '510056', rack_slot_code: '510056A2F2C101', bin_code: 'A000002653', mount_status: 'MOUNTED' },
  { rack_code: 'OTHER', rack_slot_code: 'OTHER-01', bin_code: 'B-01', mount_status: 'MOUNTED' }
]
```

断言选中 `510056` 后只显示前两个料箱；每组第 5 个料箱、重复料箱、重复 exact face、空白-only face 被拒绝；`' 90 '` 在合法输入中原样进入 body 和 preview。

- [ ] **Step 2: 运行测试确认模块缺失**

```bash
pnpm vitest run tests/unit/views/ops/transport-diagnostics/useTransportDebugRunConfig.test.ts
```

Expected: import FAIL。

- [ ] **Step 3: 实现挂载分页加载和纯校验**

`loadMountedBins()` 用 `limit:100`、递增 offset 读取完 `total`，查询过滤：

```typescript
{
  filters: {
    couple: 'and',
    conditions: [{ field: 'mount_status', operator: 'equals', value: 'MOUNTED' }]
  },
  sort: [
    { field: 'rack_code', order: 'asc' },
    { field: 'rack_slot_code', order: 'asc' }
  ],
  offset,
  limit: 100
}
```

测试锁定以上 query body，不使用 `as any` 绕过 generated `SortField`。

- [ ] **Step 4: 实现 body 和预览 builder**

```typescript
export function buildTransportDebugRunInput(
  rackId: string,
  groups: readonly TransportDebugFaceGroupDraft[]
): DebugRunCreateInput {
  return {
    rack_id: rackId,
    face_groups: groups.map(group => ({
      face: group.face,
      bins: group.bins.map(bin => ({ bin_id: bin.bin_code, slot_id: bin.rack_slot_code }))
    }))
  }
}
```

preview 必须按组输出 CTU01、BIN_MOVE 去程/回架、组间 CTU02 和最终 CTU03；face 直接引用 draft 原值。固定返库 face 显示 `"90"`。

- [ ] **Step 5: 运行配置测试**

```bash
pnpm vitest run tests/unit/views/ops/transport-diagnostics/useTransportDebugRunConfig.test.ts
```

Expected: PASS。

- [ ] **Step 6: Commit gate（需单独授权）**

```bash
git add src/views/ops/transport-diagnostics/useTransportDebugRunConfig.ts tests/unit/views/ops/transport-diagnostics/useTransportDebugRunConfig.test.ts
git commit -m "feat(transport): 配置自动联调货架面"
```

### Task 3: 实现持久 run API 状态和刷新恢复

**Files:**
- Modify: `src/api/modules/transport.ts` custom methods section only if generated method names are unsuitable
- Create: `src/views/ops/transport-diagnostics/useTransportDebugRun.ts`
- Create: `tests/unit/views/ops/transport-diagnostics/useTransportDebugRun.test.ts`

**Interfaces:**
- Consumes: generated debug-run path types。
- Produces: `TransportDebugRunApiPort`、`useTransportDebugRun()`；方法 `loadRecentRuns()`、`startRun()`、`refreshRun()`、`abortRun()`。

- [ ] **Step 1: 定义稳定 typed API façade**

在 custom section 导出 path-derived types，并只包一层 `.send()`：

```typescript
export type DebugRunCreateInput = ContractRequestBody<'/api/v1/transport/debug-runs', 'post'>
export type DebugRunResult = ContractResponseData<'/api/v1/transport/debug-runs/{run_id}', 'get'>
export type DebugRunPage = ContractResponseData<'/api/v1/transport/debug-runs', 'get'>
export type DebugRunAbortInput = ContractRequestBody<'/api/v1/transport/debug-runs/{run_id}/abort', 'post'>

export const transportDebugRunApi = {
  list: (query: ContractQueryParams<'/api/v1/transport/debug-runs', 'get'>) =>
    contractMethods.get('/api/v1/transport/debug-runs', { query }).send(),
  get: (runId: string) =>
    contractMethods.get('/api/v1/transport/debug-runs/{run_id}', { params: { run_id: runId } }).send(),
  create: (body: DebugRunCreateInput) =>
    contractMethods.post('/api/v1/transport/debug-runs', { body }).send(),
  abort: (runId: string, body: DebugRunAbortInput) =>
    contractMethods.post('/api/v1/transport/debug-runs/{run_id}/abort', { params: { run_id: runId }, body }).send()
}
```

- [ ] **Step 2: 写 composable 失败测试**

覆盖：

```typescript
await run.loadRecentRuns()
expect(run.activeRun.value?.run_id).toBe('debug-run-1')

await run.startRun(input)
expect(api.create).toHaveBeenCalledOnce()
expect(run.activeRun.value?.status).toBe('RUNNING')

await run.refreshRun('debug-run-1')
expect(run.currentRun.value?.version).toBe(3)
```

并验证 older response 不覆盖 newer version、并发 create 被阻止、刷新失败保留最后持久 snapshot、abort body 精确包含 assertion/reason。

- [ ] **Step 3: 实现服务端状态管理**

`useTransportDebugRun` 只保存 API snapshot，不维护本地步骤状态机。`loadRecentRuns()` 从列表选取唯一 `RUNNING`/`NEEDS_ATTENTION` 为 active；`refreshRun` 仅在 response version 不小于当前 version 时覆盖。

- [ ] **Step 4: 运行 composable 测试**

```bash
pnpm vitest run tests/unit/views/ops/transport-diagnostics/useTransportDebugRun.test.ts
```

Expected: PASS。

- [ ] **Step 5: Commit gate（需单独授权）**

```bash
git add src/api/modules/transport.ts src/views/ops/transport-diagnostics/useTransportDebugRun.ts tests/unit/views/ops/transport-diagnostics/useTransportDebugRun.test.ts
git commit -m "feat(transport): 管理自动联调持久状态"
```

### Task 4: 实现专用 SSE 失效通知和断线恢复

**Files:**
- Create: `src/api/streaming/transportDebugRunStream.ts`
- Create: `src/views/ops/transport-diagnostics/useTransportDebugRunStream.ts`
- Create: `tests/unit/api/transportDebugRunStream.test.ts`
- Create: `tests/unit/views/ops/transport-diagnostics/useTransportDebugRunStream.test.ts`

**Interfaces:**
- Consumes: `consumeAuthenticatedSse`、`createAuthenticatedSseConnection`。
- Produces: `TransportDebugRunUpdatedEvent`、`consumeTransportDebugRunStream()`、`useTransportDebugRunStream()`。

- [ ] **Step 1: 写 stream parser 失败测试**

接受且仅接受：

```typescript
{
  type: 'transport_debug_run.updated',
  payload: {
    run_id: 'debug-run-1',
    version: 3,
    status: 'RUNNING',
    updated_at: '2026-09-02T12:00:00Z'
  }
}
```

错误 type、缺少 run_id、非数字 version、未知 status 均忽略。断线重连触发一次 `onReconnect`，scope dispose 断开。

- [ ] **Step 2: 运行测试确认模块缺失**

```bash
pnpm vitest run \
  tests/unit/api/transportDebugRunStream.test.ts \
  tests/unit/views/ops/transport-diagnostics/useTransportDebugRunStream.test.ts
```

Expected: import FAIL。

- [ ] **Step 3: 实现 authenticated SSE 消费**

```typescript
await consumeAuthenticatedSse(
  {
    path: '/api/v1/transport/debug-runs/stream',
    signal: options.signal,
    parseEvent: parseTransportDebugRunEvent,
    onOpen: options.onOpen,
    onEvent: options.onEvent,
    baseUrl: options.baseUrl
  },
  dependencies
)
```

composable 复用现有 connection state 语义。SSE event 只调用 `refreshRun(payload.run_id)`；重连调用 `loadRecentRuns()`。

- [ ] **Step 4: 实现可见时低频 GET 兜底**

当 dialog 打开且 stream 非 CONNECTED/RECONNECTED 时，每 15 秒调用一次 `refreshRun(activeRunId)`；关闭 dialog、恢复连接或 scope dispose 时清除 timer。测试用 fake timers 证明同一时刻最多一个 timer，CONNECTED 时为 0。

- [ ] **Step 5: 运行 stream 测试**

```bash
pnpm vitest run \
  tests/unit/api/transportDebugRunStream.test.ts \
  tests/unit/views/ops/transport-diagnostics/useTransportDebugRunStream.test.ts
```

Expected: PASS。

- [ ] **Step 6: Commit gate（需单独授权）**

```bash
git add src/api/streaming/transportDebugRunStream.ts src/views/ops/transport-diagnostics/useTransportDebugRunStream.ts tests/unit/api/transportDebugRunStream.test.ts tests/unit/views/ops/transport-diagnostics/useTransportDebugRunStream.test.ts
git commit -m "feat(transport): 订阅自动联调进度"
```

### Task 5: 用自动联调 Dialog 替换固定步进器

**Files:**
- Delete: `src/views/ops/transport-diagnostics/useTransportDebugLoop.ts`
- Delete: `tests/unit/views/ops/transport-diagnostics/useTransportDebugLoop.test.ts`
- Rename/Rewrite: `src/views/ops/transport-diagnostics/TransportDebugLoopDialog.vue` → `src/views/ops/transport-diagnostics/TransportDebugRunDialog.vue`
- Create: `tests/unit/views/ops/transport-diagnostics/TransportDebugRunDialog.test.ts`

**Interfaces:**
- Consumes: `useTransportDebugRunConfig()`、`useTransportDebugRun()`、`useTransportDebugRunStream()`。
- Produces: `TransportDebugRunDialog` expose `{ open(launcher?), close() }`。

- [ ] **Step 1: 写配置态组件失败测试**

Vue Test Utils 断言：

- 标题为“自动联调”。
- 货架选项按 rack code 去重。
- 选择货架后可新增面组、输入原始 face、选择该货架 MOUNTED bins。
- 每组已选达到 4 个后其它 option disabled。
- 同一 bin 在其它组 disabled。
- 非法配置时“启动自动联调” disabled。
- preview 显示 `RACK 510056 → RACK_POSITION KT16`、`CTU02 target_face="270"`、最终 `RACK 510056 → ZONE WH01 / CTU03 / "90"`。

- [ ] **Step 2: 写运行态组件失败测试**

给定 server snapshot：

```typescript
const snapshot = {
  run_id: 'debug-run-1',
  status: 'RUNNING',
  rack_id: '510056',
  current_group_index: 1,
  current_phase: 'WAIT_SCAN12',
  current_task_id: 'transport-3',
  observed_bin_ids: ['A000002653'],
  pending_bin_ids: ['A000003001'],
  version: 7
}
```

断言表单冻结、显示“第 2 面 / WAIT_SCAN12”、task link、已扫/待扫列表。`NEEDS_ATTENTION` 显示 reason 和诊断链接，无强制推进按钮；只有 abort permission 且所有服务端 precondition 满足时显示“终止轮次”。

- [ ] **Step 3: 实现配置态 UI**

使用现有 `StandardDialog`、`AppButton`、Element Plus form/select。组件打开时先 `loadMountedBins()` 和 `loadRecentRuns()`；发现 active run 时直接进入观察态。启动调用一次 `startRun(buildTransportDebugRunInput(selectedRackId.value, groups.value))`，成功后使用响应 snapshot，不再调用单步 debug task API。

- [ ] **Step 4: 实现观察态和 abort 二次确认**

观察态展示 frozen config、phase、task、SCAN12 progress、attention。abort 表单固定发送：

```typescript
{
  assertion: 'PHYSICAL_STATE_VERIFIED',
  reason: abortReason
}
```

reason 必填；文案明确 abort 不会取消远端任务或返库。服务端 409 时保持 dialog 和原 snapshot。

- [ ] **Step 5: 运行组件测试**

```bash
pnpm vitest run tests/unit/views/ops/transport-diagnostics/TransportDebugRunDialog.test.ts
```

Expected: PASS。

- [ ] **Step 6: 删除旧固定步进器并验证无引用**

```bash
rg -n "510056 现场联调步进|useTransportDebugLoop|confirmAndReset" src tests
```

Expected: 无旧步进器引用；示例资产只允许出现在新测试 fixture/preview 中。

- [ ] **Step 7: Commit gate（需单独授权）**

```bash
git add src/views/ops/transport-diagnostics/TransportDebugRunDialog.vue src/views/ops/transport-diagnostics/useTransportDebugLoop.ts tests/unit/views/ops/transport-diagnostics/useTransportDebugLoop.test.ts tests/unit/views/ops/transport-diagnostics/TransportDebugRunDialog.test.ts
git commit -m "feat(transport): 替换固定现场联调步进器"
```

### Task 6: 集成现有诊断页和权限边界

**Files:**
- Modify: `src/views/ops/transport-diagnostics/TransportDiagnosticsPage.vue`
- Modify: `src/views/ops/transport-diagnostics/TransportDiagnosticsPage.css`
- Modify: `tests/unit/views/ops/transport-diagnostics/TransportDiagnosticsPage.test.ts`

**Interfaces:**
- Consumes: `TransportDebugRunDialog` 和 generated `OPS_PERMISSIONS.transportDebugRun` leaves。
- Produces: 页面“自动联调”入口、恢复观察、Transport/Device diagnostics links。

- [ ] **Step 1: 更新页面失败测试**

把旧测试改为：

```typescript
expect(button.text()).toContain('自动联调')
await button.trigger('click')
expect(runDialogMocks.open).toHaveBeenCalledOnce()
```

权限矩阵：list+start 才能看到启动入口；read 决定详情/恢复观察；stream permission 决定专用 SSE；abort permission 只决定终止入口。现有 task read/create/reset 权限不再决定自动联调权限。

- [ ] **Step 2: 实现页面集成**

替换 import/ref/tag：

```vue
<TransportDebugRunDialog
  ref="runDialogRef"
  :can-start="canStartDebugRun"
  :can-abort="canAbortDebugRun"
  :can-stream="canStreamDebugRun"
/>
```

按钮文案改为“自动联调”。保留现有 `TransportDebugTaskDialog`、Transport task list/detail、reset dialog 和原 evidence stream。

- [ ] **Step 3: 增加诊断链接**

current task link 调用现有 `selectTask(transport_task_id)` 并关闭或并排展示 run dialog；Device Evidence link 指向现有 Device diagnostics 页面并带 `device_code=SCAN12` 的可用查询状态。若当前路由不支持 query 初始化，只提供明确导航和复制筛选值，不修改 Device 页面。

- [ ] **Step 4: 运行页面测试**

```bash
pnpm vitest run tests/unit/views/ops/transport-diagnostics/TransportDiagnosticsPage.test.ts
```

Expected: PASS；旧 task diagnostics 行为不回归。

- [ ] **Step 5: Commit gate（需单独授权）**

```bash
git add src/views/ops/transport-diagnostics/TransportDiagnosticsPage.vue src/views/ops/transport-diagnostics/TransportDiagnosticsPage.css tests/unit/views/ops/transport-diagnostics/TransportDiagnosticsPage.test.ts
git commit -m "feat(transport): 接入自动联调诊断入口"
```

### Task 7: 完成前端门禁和浏览器 QA

**Files:**
- Modify: only files produced by formatter/generator that are already in this plan
- Test: `tests/unit/views/ops/transport-diagnostics/*`

**Interfaces:**
- Consumes: 全部前端实现。
- Produces: 可提交的 UI/contract/permission evidence；不包含部署或现场动作。

- [ ] **Step 1: 运行 focused tests**

```bash
pnpm vitest run \
  tests/unit/api/transport-contract.test.ts \
  tests/unit/api/transportDebugRunStream.test.ts \
  tests/unit/views/ops/transport-diagnostics
```

Expected: PASS。

- [ ] **Step 2: 运行完整前端门禁**

```bash
pnpm test
pnpm lint
pnpm build
pnpm contract:test
pnpm contract:verify
pnpm permission:verify
```

Expected: 全部 exit 0。

- [ ] **Step 3: 本地联调浏览器 QA**

由后端唯一编排器启动：

```bash
WES_FRONTEND_ROOT=/Users/kaizhou/codeDev/wes_frontend /Users/kaizhou/codeDev/wes_backend/scripts/dev-env.sh up
/Users/kaizhou/codeDev/wes_backend/scripts/dev-env.sh check
```

浏览器验证：选择货架、两面各 1～4 箱、exact face 预览、启动后冻结、刷新恢复、SSE 断开提示、GET 兜底、NEEDS_ATTENTION、权限隐藏、窄 abort 409/成功路径。只使用 Mock/测试数据，不连接现场 RCS。

- [ ] **Step 4: 保存 UI 截图和差异证据**

至少保存配置态、WAIT_SCAN12、NEEDS_ATTENTION 三张截图到 PR 描述所用的本地证据目录；不把临时截图提交到源码，除非项目已有固定截图目录并获得授权。

- [ ] **Step 5: 最终 scope 检查**

```bash
git diff --check
git status --short
git diff --stat "$(git merge-base origin/develop HEAD)" HEAD
```

Expected: 不包含 `docs/designs/device-diagnostics-debug-epoch-history-cleanup.md` 或任何其它预存 dirty 文件，不包含部署、现场配置或后端源码。

- [ ] **Step 6: Final commit gate（需单独授权）**

本 Task 只执行门禁和浏览器验证，正常情况下不应产生新的源码改动或额外 commit。若格式化器确实改动文件，返回对应实现 Task，逐个审阅并仅暂存该 Task 明确列出的文件；禁止目录级或全仓 `git add`。

完成后停在前端 Review/commit/push/PR 授权门槛；Merge 不等于 Deploy，部署后健康检查也不等于 SCAN12/RCS 现场物理验收。
