# Device Ingress Live Diagnostics Frontend Implementation Plan

> 前端实施在固定 backend contract commit 后开始；功能行为按 TDD，浏览器 QA 只连接仓库本地 Mock。

**Goal:** 交付仅超级用户可见和可进入的设备诊断控制台，实时展示 RESULT/EVENT callback attempt 与 evidence apply 状态，并提供经过 preflight、不可变预览、原因和二次确认的真实 `MANUAL_DEBUG` 下发入口。

**Spec:** `/Users/kaizhou/codeDev/wes_backend/docs/superpowers/specs/2026-08-23-device-ingress-live-diagnostics-design.md`

## 1. 执行原则

- 后端最终合同 commit 未固定前，不手写或猜测 HTTP DTO。
- 普通 HTTP 只使用生成的 `deviceApiMethods`；不创建 `deviceOperations.ts`。
- 只有无限 SSE response 使用原生 `fetch` + `ReadableStream`，并复用现有 token refresh。
- 复用现有 `DataTable`、`StandardDialog`、`StandardDrawer`、`AppButton`；不创建一层只转发 props 的包装组件。
- 页面所有 ECS URL、rows、payload、draft、lifecycle 只存在当前组件会话内存。
- route 和 menu 都复用 `SUPERUSER_PERMISSION='*'`；不新增后端权限或前端路径白名单。
- “现场联调下发”是真实设备命令；界面不得使用“模拟下发”“下发成功”“设备已完成”等误导措辞。

## 2. 最小文件面

### 生成合同

- `contracts/openapi.current.json`
- `src/api/generated/**`
- `src/api/modules/device.ts`
- `.contract-sync-record.json`
- `.permission-sync-record.json`（只随既有生成链更新，不生成新的诊断权限）

### 手写生产文件

- `src/api/streaming/deviceEvidenceStream.ts`
- `src/router/routes/ops.ts`
- `src/router/routes/index.ts`
- `src/router/menu-manifest.ts`
- 现有 sidebar/menu composable 中的通用 route-meta 菜单过滤落点
- `src/views/ops/device-diagnostics/DeviceDiagnosticsPage.vue`
- `src/views/ops/device-diagnostics/DeviceEvidenceTable.vue`
- `src/views/ops/device-diagnostics/ManualDebugCommandDialog.vue`
- `src/views/ops/device-diagnostics/useDeviceEvidenceStream.ts`
- `src/views/ops/device-diagnostics/useManualDebugCommand.ts`

不创建：

- `src/api/modules/deviceOperations.ts`
- `src/types/device-diagnostics.ts`
- `DeviceEvidenceToolbar.vue`
- `EvidencePayloadDrawer.vue`
- `CommandLifecyclePanel.vue`

局部类型与单一消费者放在对应 stream/composable 文件；抽屉直接使用 `StandardDrawer`，生命周期直接放在 debug dialog 内。只有实施后单文件确实超过 300 行且职责可独立时才拆分。

### 测试

- SSE transport/parser unit test
- stream composable unit test
- manual debug composable unit test
- page/table/dialog component tests
- route guard/menu filtering/menu manifest tests
- 生成合同与浏览器 QA 证据

## 3. Task 0 — 隔离现场并冻结 backend contract

1. 记录 frontend branch、HEAD、dirty/untracked 和 worktree；不得修改、stash、reset 或清理主 checkout 的用户现场。
2. 按前端仓库规则创建或选择隔离 worktree，并安装 frozen dependencies。
3. 从固定、clean 的 backend commit 运行现有 `contract:freeze`、types/Zod/API/permission 生成链。
4. 确认生成的 `src/api/modules/device.ts` 已包含：evidence stream metadata、debug preflight、debug create reason 和 command detail。
   - 这些 superuser-only operations 不应生成新的 `ops:device:*` 权限常量；若生成结果仍有诊断权限，停止并回查后端 route metadata，而不是在前端兼容。
5. 读取并固定现有 token refresh、DataTable、StandardDialog、StandardDrawer、AppButton、route guard 和 menu tree 模式。

验证：contract provenance 绑定 backend SHA；生成链重复运行零 diff。

## 4. Task 1 — 原生 Bearer SSE client

### RED

覆盖：

- LF/CRLF、字段跨 chunk、多 frame、multiline data 和 heartbeat。
- unknown event、malformed JSON、EOF、abort 和 non-2xx。
- filters 正确编码；token 只进入 Authorization header，不进入 URL/错误文本。
- 首次 401 复用现有 refresh 一次；第二次 401 失败，不循环；abort 不 refresh。

### GREEN

- 一个 streaming `TextDecoder` 和一个小 frame buffer。
- 只解析 `event` 与 `data`；忽略 `id`，不发送 `Last-Event-ID`。
- 使用 generated types 或与 generated contract 同文件的最小 discriminated guard；不建重复全局 types 文件。
- 不经过会调用 `response.text()` 的现有 Alova adapter，不引入 EventSource polyfill。

验证：SSE client unit tests。

## 5. Task 2 — Live-only bounded stream composable

### Row 模型

每行至少包含：

- `rowKey`、`requestId`、`evidenceId`、`gap`
- `payloadBytes`
- `attempt: DeviceIngressAttemptEvent | null`
- `latestUpdate: DeviceEvidenceUpdatedEvent | null`

不得使用一个 `event` union 字段让 update 覆盖 attempt 原始 payload。

### RED

- 初始 DISCONNECTED；首次打开 CONNECTED；曾连接成功后的异常断开并重连为 RECONNECTED。
- 仅在活动连接可能漏消息时插入一个 gap；首次连接失败、手动断开和 unmount 不插入。
- attempt 按接收顺序追加；update 更新当前内存中全部相同 evidence_id 的 attempt 行。
- 无关联 attempt 的 update 创建无 payload 状态行。
- 第 201 行淘汰最旧行；估算 payload 总量超过 16 MiB 时持续淘汰最旧行。
- `clear()` 清 rows/计数但不关闭活动连接；filter 变化 abort 旧连接并建立新连接。

### GREEN

使用 refs/computed、单一 `AbortController` 和上限 10 秒的有界指数退避；不使用 Pinia、IndexedDB、replay cursor 或后台持久连接。

验证：stream composable unit tests + fake timers 无遗留 timer。

## 6. Task 3 — 生成 API 驱动的 MANUAL_DEBUG 状态机

### RED

- preflight 只调用 generated `deviceApiMethods`，设备和 task type 只来自后端响应。
- row 打开只保存候选 device_code；preflight 响应中存在时才默认选中。global 打开不预选。
- `params` 必须解析为非 array JSON object。
- preview 固定 endpoint/device/timeout/task_type/params/client_request_id；任一编辑都返回 EDITING。
- reason trim 后 1–500 字符；显式二次确认后才能 submit；禁止重复 submit。
- `202` 进入 TRACKING，但文案只表示 WES 已创建 PENDING。
- 命令详情每 2 秒轮询，terminal 或 dialog close 时停止；RECONCILING 保持原义。

### GREEN

- 状态固定为 `EDITING | PREVIEW | SUBMITTING | TRACKING`。
- 复用项目现有 UUID7 helper；只有确认不存在时才增加一个 page-local helper 和聚焦测试。
- 不复制后端准入逻辑，前端只展示后端返回的 admissibility/reason。
- RESULT SSE 只在入站表关联展示，不再作为 command polling wake-up；持久化 command detail 是唯一生命周期真源。

验证：manual debug composable unit tests。

## 7. Task 4 — 页面、表格与真实下发弹窗

### 页面与表格

- toolbar 直接放在 page：连接状态、filters、清空、重连、全局“现场联调下发”。
- `DeviceEvidenceTable` 复用 DataTable；展示时间、RESULT/EVENT、device、command/event、disposition、apply、HTTP status 和详情。
- gap 使用跨列提示，明确“期间可能存在消息缺口”。
- parsed payload 使用 `StandardDrawer` 和 escaped `<pre>`；标题明确“解析 JSON（非字节级原始 body）”。

### Debug dialog

- 复用 `StandardDialog` 和 `AppButton`。
- ECS URL 必填后才可 preflight；设备与 task type 下拉来自 response。
- preview 只读且不含 Authorization；reason 与明确的真实动作确认必填。
- 提交按钮固定为“确认创建真实设备命令”。
- 生命周期区直接展示 persisted snapshot：attempt、ACK、callback、failure/reconciliation 和时间。
- 无 force、override 或 cancel 控件；SUBMITTING 时防止 backdrop 误关；关闭后恢复 launcher focus 并清 session state。

### RED/GREEN

组件测试覆盖过滤映射、badge、gap、drawer、安全转义、键盘、窄屏、row/global dialog、invalid JSON、immutable preview、二次确认和 truthful lifecycle，再实现最小 UI。

验证：page/table/dialog component tests。

## 8. Task 5 — Superuser route 与通用菜单过滤

### RED

- 带 `'*'` 的超级用户可进入 `/ops/device-diagnostics`。
- 普通 authenticated user 直接输入 URL 被送到 403。
- 超级用户看到 ops 父子菜单；普通用户同时看不到 child 和空 parent。
- manifest 中 `ops:system:menu` 与 `ops:device-diagnostics:menu` 的 permission 均为 `'*'`。
- 现有非 ops 菜单在相同用户权限集合下保持原可见性，避免通用过滤误伤已有入口。

### GREEN

- `/ops` parent 与诊断 child route 都设置 `meta.permission = SUPERUSER_PERMISSION`。
- menu name 显式填写，不依赖 `'*'` 推导。
- 增加一个通用纯函数：根据 `router.resolve(menu.path).meta.permission` 调用现有 `hasPermission()` 递归过滤 menu tree。
- 过滤函数不包含 `/ops`、device diagnostics、角色名或用户 ID 等白名单。
- parent 没有可见 child 且自身不可导航时移除，避免空菜单。
- 继续用 `generate:menu` 生成 artifact，不手改生成物。

这项改动只消费现有 route permission 元数据，不改变后端 permission catalog 或角色分配。

验证：route guard + menu filter + menu manifest tests。

## 9. Task 6 — 合同、门禁与浏览器 QA

1. 重跑 contract/types/Zod/API/menu/permission 生成，确认零漂移。
2. 对最终 tree 一次性运行聚焦 tests、type check、lint、全量 unit test、build 和 `git diff --check`。
3. 使用 paired backend worktree 的 `./scripts/dev-env.sh up` 与 `check` 启动本地 Mock。
4. 浏览器 QA 覆盖：
   - 超级用户菜单/直达可用，普通用户菜单不可见且直达 403。
   - 1440 px 与 768 px、键盘、focus restore、dark/light。
   - RESULT/EVENT、filters、drawer、clear、reconnect 和 gap。
   - row/global dialog、invalid params、不可准入设备、preview、reason 和二次确认。
   - `202` 仍为 PENDING；Mock ACK/result、RECONCILING/timeout 文案真实。
   - Network 中 Bearer 只在 header；refresh/unmount 终止旧 stream。
5. QA 不使用真实 ECS URL，不声明物理动作、供应商或生产验收。

建议最终命令按前端仓库实际 scripts 执行：

```bash
pnpm contract:verify
pnpm contract:test
pnpm permission:verify
pnpm vitest run <本计划聚焦测试>
pnpm type:check
pnpm lint
pnpm test
pnpm build
git diff --check
```

## 10. Handoff

- 固定 frontend diff/HEAD、backend contract SHA、生成记录、测试与浏览器 QA 证据。
- 只 stage File Map 中实际变化的路径；不得 `git add -A` 或 `git add .`。
- Commit、Push、PR、Merge、Deploy 均需单独授权。
- 交付报告必须分别写明 local Mock、supplier conformance、onsite physical、deployment 与 business acceptance 边界。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | ---: | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 本轮未运行 |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | 本轮未运行 |
| Eng Review | `/plan-eng-review` | Architecture & tests | 1 | CLEAR | 前端 7 项问题均已折叠，0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 本轮未运行 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 本轮未运行 |

- **VERDICT:** ENG CLEARED — 当前前端计划可实施。
- **SCOPE:** 功能不缩水；删除重复 handwritten API、冗余组件和 SSE wake-up 轮询。
- **LOAD-BEARING DECISION:** route/menu 共用 `SUPERUSER_PERMISSION='*'`，普通 HTTP 只使用 generated device API。

NO UNRESOLVED DECISIONS
