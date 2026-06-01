# 工作线 Fast-Fail、START 准入与设备实时状态校验 SPEC

## 背景

当前 `POST /api/v1/callback/event` 在工作线处于 `RECONCILING` 时仍返回 `ACCEPTED/submitted`。
但后续 `workline_inbox` 消费会被 `WORKLINE_RECONCILING` 拦截，不会创建 session、command 或 outbox。
这会误导 Swagger 和 mock 调试：用户看到 WES 接收成功，却无法在 mock 中读取后续 command。

同时，工作线初始化完成后默认 `READY` 的语义不严谨。设备主数据和通信配置初始化完成，并不代表整线已经允许自动运行。工作线应在现场硬件切到自动模式并触发 START 后，由 ECS push 标准事件给 WES；WES 做整线准入检查，检查通过后才进入 `READY`。

## 目标

- 工作线初始化后默认为 `STOPPED`，不是 `READY`。
- 只有现场硬件 START 事件触发整线准入检查成功后，工作线才进入 `READY`。
- 生产类 event 在工作线非 `READY` 时同步 fast-fail，HTTP 返回 `409`，不落 `workline_inbox`。
- `callback_logs.ingress_outcome=ACCEPTED` 只表示事件已经被业务编排真正接受。
- command 下发前先校验 WES DB 设备投影，再实时查询设备 `/api/v1/device/status`。
- 本地 dev mock 走真实 HTTP 设备路径：WES 调用 `mock_ecs` 的 status 和 command 接口，而不是走 sandbox 派发。

## 非目标

- 不设计真实 PLC 或底层设备控制协议。
- 不把设备 `/api/v1/device/status` 反向同步成工作线状态。
- 不保留旧的 `preset` mock event 格式。
- 不创建本地 test 环境；本地只同步 dev 环境。
- 不做大规模 runtime 架构重构。

## 状态语义

### 工作线运行态

- `STOPPED`：工作线已初始化或已从异常恢复，但未收到现场硬件 START，不接收生产事件。
- `READY`：现场硬件 START 后，整线准入检查通过，可以接收生产事件并下发 command。
- `RECONCILING`：存在 runtime reconciliation 或 active blocking hold，需要人工 resolve。
- `ESTOPPED`：急停冻结，需要安全 clear。

### 设备状态

- `IDLE`：设备空闲，可接收 command。
- `RUNNING`：设备忙，正在执行任务。
- `ERROR`：设备故障，需要人工介入。
- `OFFLINE`：设备通信不可用或心跳失联。
- `MAINTENANCE`：人工维护中。

关键原则：工作线状态由 WES runtime、safety、reconciliation 生命周期维护；设备状态只用于判断具体设备是否可接 command。未收到现场硬件 START 的工作线不是 `OFFLINE`，而是 `STOPPED`。

## Event 入口规则

`POST /api/v1/callback/event` 按事件类型分流：

- 平台控制事件：`WORKLINE_START_REQUESTED` 是现场硬件 START 触发后由 ECS push 给 WES 的事件，允许在 `STOPPED` 下进入。
- 平台安全事件：`ESTOP_PRESSED` 等允许在非 `READY` 下进入。
- 插件生产事件：如 `SCAN_COMPLETED`，只有工作线 `READY` 才允许进入编排。

v1 必须在代码中显式区分：

- `PLATFORM_CONTROL_EVENTS = {"WORKLINE_START_REQUESTED"}`
- `RESERVED_RUNTIME_EVENTS = {"ESTOP_PRESSED"}`
- 插件 `supported_events` 只表达生产事件能力，不表达平台控制或安全事件能力。

因此，`WORKLINE_START_REQUESTED` 不需要出现在设备 capability 或 WorkLine 插件 supported events 中。它应先按平台控制事件处理，再解析 `device_code -> workline_id` 并执行 START 准入。否则现有 `capabilities.supports_event()` 会把 START 当成普通生产事件拒绝。

START 不新增 WES 人工操作 API。ECS 只需要上报 `device_code`，不需要知道 WorkLine；WES 通过现有设备绑定上下文解析 `device_code -> workline_id` 后执行准入检查。

生产类事件在工作线非 `READY` 时：

- HTTP 返回 `409 Conflict`。
- 不创建 `workline_inbox`。
- 不 enqueue inbox processing。
- 记录 `callback_logs`：
  - `response_status=409`
  - `ingress_outcome=REJECTED`
  - `failure_stage=WORKLINE_GUARD`
- 记录 diagnostic，至少包含：
  - `device_code`
  - `workline_id`
  - `runtime_status`
  - `stopped_reason`
  - `request_id`
  - `trace_id`

建议响应体：

```json
{
  "code": "3012",
  "message": "资源冲突",
  "data": {
    "status": "rejected",
    "reason_code": "WORKLINE_NOT_ACCEPTING_WORK",
    "reason_message": "WorkLine is not accepting production events",
    "device_code": "RS-INPUT-ARM-01",
    "workline_id": 51,
    "runtime_status": "RECONCILING",
    "stopped_reason": "COMMAND_ACK_EXHAUSTED",
    "request_id": "24449023",
    "trace_id": "trace_xxx"
  }
}
```

## START 准入规则

收到 `/callback/event` 的 `WORKLINE_START_REQUESTED` 后，WES 执行整线准入检查。

通过条件：

- 工作线存在且启用。
- 工作线当前为 `STOPPED`。
- 无 active safety incident。
- 无 active runtime hold。
- 无 pending reconciliation session。
- 所有 active 必需设备存在且通信配置完整；该检查复用 `configuration-status` 的结构预检，并补齐 host/port/path 通信配置项。
- 所有设备实时 `/api/v1/device/status` 返回：
  - `mode=AUTO`
  - `status=IDLE`
  - `current_command_id=null`

START 准入的实时 status 查询采用 ECS endpoint 分组批量优先：

- 按 `scheme/host/port/status_path` 分组；同一 ECS Server 默认请求 `GET /api/v1/device/status`，不带 `device_code` 时返回该 ECS 下所有设备状态。
- WES 在批量响应中按 `device_code` 匹配本工作线必需设备；缺失、重复、格式错误都视为准入失败并写入 diagnostic。
- 单次 START 准入使用有上限并发，默认并发 `4`，配置覆盖必须 clamp 在 `1-8`。
- 设备 status 查询使用 `runtime_config_json.device_status_timeout_seconds`，默认 `2s`，配置覆盖必须 clamp 在 `1s-5s`。

准入通过后：

- 工作线进入 `READY`。
- 清空 `stopped_reason`。
- 写入开工或恢复时间字段。
- 返回 HTTP `200`。

准入失败后：

- 工作线保持 `STOPPED`。
- 返回 HTTP `409`。
- 记录明确 diagnostic。
- 最新 START 准入结果写入 WorkLine 稳定投影字段，供 runtime summary/detail 直接展示。

START 准入必须使用两阶段 CAS：

```text
短事务读取/锁定 STOPPED 快照
-> 释放 DB 锁，执行 ECS status 探测
-> 重新锁定 WorkLine 并复查仍为 STOPPED、无 incident/hold/reconciliation
-> 写入 READY 或失败 diagnostic
```

不得在持有 DB 行锁时等待 ECS HTTP 响应。

异常恢复路径：

```text
RECONCILING/ESTOPPED -> resolve/clear -> STOPPED -> 现场硬件 START -> 准入通过 -> READY
```

## 异常恢复规则

需要人工 resolve 或 clear 的异常：

- `CALLBACK_DEADLINE_EXPIRED`：设备可能已经动作完成、失败或卡住，WES 无法确认结果。
- `COMMAND_ACK_EXHAUSTED`：WES 无法确认设备是否收到或执行 command。
- `OUTBOX_DISPATCH_FAILED` 且已耗尽重试并进入 runtime reconciliation。
- 资源、库位、料箱投影冲突类 runtime hold。
- `ESTOPPED`：必须走安全 clear 流程。

不需要人工 resolve 的场景：

- 请求包络错误、字段缺失、payload schema 错误。
- 未知设备、设备未绑定工作线、设备不支持该 event 或 command。
- 工作线 `STOPPED` 时生产事件被拒绝。
- 工作线 `RECONCILING/ESTOPPED` 时新生产事件被 fast-fail。
- 幂等重复事件。
- 下发前 DB 投影发现设备不是 `IDLE`，且还没有实际发送 command。
- 下发前实时 `/api/v1/device/status` 查询失败或返回非 `IDLE`，且仍在 retry 预算内。
- 外部依赖短暂失败，且没有造成设备动作不确定性。

分界线：只要可能已经触发设备动作，且 WES 无法确定动作结果，就进入 reconciliation，需要人工 resolve。否则应 fast-fail 或 retry。

## Command 派发规则

`DEVICE_COMMAND` 下发前执行双重校验。

第一层是 WES DB 投影校验：

- 设备不能是 `ERROR`、`OFFLINE`、`MAINTENANCE`、`RUNNING`。
- `current_command_id` 必须为空。
- command type 必须在设备能力内。

第二层是实时设备 status 校验：

- 请求 ECS 标准接口 `GET /api/v1/device/status?device_code=...`。一个 ECS Server 可以管理多台设备，WES 必须用 `device_code` 区分具体设备。
- 响应必须满足：
  - `mode=AUTO`
  - `status=IDLE`
  - `current_command_id=null`
- 校验通过后才 POST `/api/v1/device/command`。
- status 查询使用 `runtime_config_json.device_status_timeout_seconds`，默认 2s，可从工作线运行配置覆盖但必须 clamp 在 1s-5s；不得复用设备 command ACK 的长 timeout。
- 单次 dispatch 使用一个操作级 `httpx.AsyncClient`；status GET 与 command POST 共享该 client，但使用各自独立 timeout。

实时 status 查询失败、超时、非 2xx、格式错误或非 `IDLE`：

- 不 POST command。
- outbox 按派发失败进入 retry。
- retry 耗尽后进入现有 `COMMAND_ACK_EXHAUSTED` runtime reconciliation。

注意：status GET 发生在物理 command POST 之前，失败时不能等同于 command ACK 超时。只有 command POST 已经发出且 WES 无法确认设备是否收到或执行时，才使用 `OUTBOX_ACK_TIMEOUT` / ACK timeout 语义。

## 本地 Dev Mock 规则

- 粗分机 dev 工作线使用 `run_mode=AUTO`。
- 设备 host 指向 compose 域内 `mock_ecs:8010`。
- 初始化后工作线为 `STOPPED`。
- `ecs_mock` 的 `/api/v1/device/status` 返回最新契约：
  - `state.mode`
  - `state.status`
  - `state.current_command_id`
- 带 `device_code` 时返回指定设备状态；不带 `device_code` 时返回该 ECS 中所有设备状态，供 START 准入批量查询。

Swagger 调试流程：

```text
现场/Mock 触发硬件 START
-> ECS push WORKLINE_START_REQUESTED 到 /callback/event
-> WES 查询 mock status
-> 全部 AUTO + IDLE 后工作线进入 READY
-> SCAN_COMPLETED
-> WES 创建并派发 command
-> GET /api/v1/mock/commands 可看到真实下发记录
```

## 验收标准

- `STOPPED` 工作线收到生产事件返回 `409`，且不创建 `workline_inbox`。
- `RECONCILING` 工作线收到生产事件返回 `409`，且不创建 `workline_inbox`。
- START 准入成功后工作线进入 `READY`。
- START 准入失败后工作线保持 `STOPPED`。
- command 下发前实时 status 非 `IDLE` 时，不调用 `/api/v1/device/command`。
- 实时 status 查询失败在 retry 预算内不会直接进入人工 resolve。
- retry 耗尽后沿用现有 runtime reconciliation。
- dev mock 可以完整验证：`START -> READY -> event -> command history`。
- 运行态 API 暴露足够前端展示的信息：`STOPPED` 时能显示“等待现场硬件 START”以及最近 START 准入失败诊断。
- 前端按运行态合同显示 `STOPPED`，且 clear/resolve 后不再提示“已恢复接收新流程”。
- 前端普通生产 Event composer 在非 `READY` 下禁用；`WORKLINE_START_REQUESTED` 不作为普通生产 Event 发送。

## 前端合同与验证结论

前端代码位于 `../wes_frontend`。本计划按全栈同批交付处理：后端 API 合同与前端 STOPPED/START 展示、禁用和模板过滤必须在同一批变更中完成，避免后端语义切换后前端继续显示“稳定”或“已恢复接收”。

### 当前前端缺口

- `src/constants/runtime-safety.ts` 只有 `ESTOPPED` / `RECONCILING`，没有 `STOPPED`。
- `src/utils/runtime-display.ts` 不识别 `STOPPED`，工作线风险标签会落到“稳定”或普通 info 语义。
- `src/utils/runtime-safety.ts` 的 `getWorklineRuntimeVerdict()` 只把 `ESTOPPED`、`RECONCILING`、active incident 或 safety evidence 视为阻断状态，没有“等待现场硬件 START”的 verdict。
- `src/views/runtime/worklines/WorklineMonitorPage.vue` 和 `src/views/runtime/sandbox/SandboxWorkbenchPage.vue` 的 clear-estop 文案仍是“恢复接收 / 已恢复接收新流程”，与 clear 后回到 `STOPPED` 的新语义冲突。
- `src/components/runtime/sandbox/SandboxEventComposer.vue` 已禁止普通 sandbox 发送 `ESTOP_PRESSED`，但尚未禁止 `WORKLINE_START_REQUESTED` 作为普通生产 Event 被发送。

### 后端必须暴露的最小运行态合同

`RuntimeWorklineSummary` 或 detail summary 至少继续稳定暴露：

- `runtime_status`
- `stopped_reason`
- `stopped_at`
- `resumed_at`

并新增或等价暴露 START 准入诊断字段，供前端显示最近一次 START 失败：

- `start_admission_status`：`NOT_REQUESTED` / `CHECKING` / `PASSED` / `FAILED`
- `start_admission_message`
- `start_admission_failed_device_code`
- `start_admission_checked_at`
- `last_start_request_id`
- `last_start_trace_id`

字段可来自现有 diagnostic 聚合，但 API 响应必须是前端无需再追 callback logs 才能展示的稳定投影。

### 前端预期行为

- 工作线 `STOPPED` 时，监控页显示“等待现场硬件 START”，语义色为 warning/info，不显示“稳定”。
- 工作线 `STOPPED` 时，生产 Event 发送入口禁用，并提示“工作线未 START，等待现场硬件 START”。
- `clear_estop` 或 runtime hold resolve 成功后，提示应为“已解除冻结，等待现场硬件 START”，不能再提示“已恢复接收新流程”。
- `WORKLINE_START_REQUESTED` 不出现在普通生产 Event 模板中；dev/mock START 入口应模拟 ECS push，而不是通过 sandbox production event composer。
- `Runtime SSE` 至少在 `runtime_status=STOPPED/READY/ESTOPPED/RECONCILING` 变化时刷新工作线摘要；是否弹桌面通知可由前端另行决定。

### 设计评审补充

#### 信息层级

```text
工作线监控 / Sandbox
├── 左侧目录卡片：RuntimeStatusBadge 显示 “等待 START”，tone=warning
├── 右侧详情第一信号：DecisionStrip 显示 STOPPED 主 verdict
│   ├── label: 等待现场 START
│   ├── suggestion: 软件冻结已解除，现场 START 后才接收生产事件
│   └── dev/mock-only action: 模拟现场 START
├── 拓扑主视图：继续展示设备状态，不把 STOPPED 映射成设备 OFFLINE
└── Event composer：非 READY 时禁用生产事件，并显示同一条禁用原因
```

STOPPED 使用 `warning` 黄色。它表示“需要关注、未允许生产”，不是 `danger` 事故，也不是 `success` 稳定运行。文案必须同时说明“等待现场 START”与“不是设备故障”。

#### START 准入 UI 状态表

| 状态 | 用户看到 | Tone | 可操作性 |
|------|----------|------|----------|
| `NOT_REQUESTED` | 等待现场 START；生产 Event 禁用 | warning | dev/mock 可显示“模拟现场 START” |
| `CHECKING` | 正在检查设备 AUTO/IDLE；显示短 loading | warning | 禁用重复 START 和生产 Event |
| `PASSED` | 工作线 READY；生产 Event 可发送 | success | 隐藏 START 入口 |
| `FAILED` | START 准入失败；显示 failed device、message、request_id/trace_id | warning | 允许再次模拟 START；生产 Event 仍禁用 |

失败态诊断应放在 DecisionStrip 下方或其展开区，不新增大面积装饰卡片；使用现有工业控制台深色面板、`RuntimeStatusBadge` 和等宽 request/trace 显示。

#### clear/resolve 用户旅程

| Step | User Does | User Sees | User Should Understand |
|------|-----------|-----------|------------------------|
| 1 | 清除急停或 resolve runtime hold | 成功 toast: “已解除冻结，等待现场 START” | 软件阻断已解除，但还不能接收生产 |
| 2 | 回到监控页/沙箱页 | DecisionStrip warning: “等待现场 START” | 下一步在现场或 mock START |
| 3 | 触发 START | `CHECKING` loading，生产 Event 仍禁用 | WES 正在检查设备自动/空闲状态 |
| 4 | 准入失败 | 显示失败设备和诊断 ID | 去处理指定设备后重试 START |
| 5 | 准入成功 | 状态变 READY，DecisionStrip 转运行/稳定语义 | 生产 Event 入口恢复可用 |

#### 响应式与无障碍验收

- 窄屏时 STOPPED/START verdict 必须位于拓扑和 session board 之前；左侧目录折叠后仍能看到当前工作线 runtime badge。
- 禁用按钮必须有可见原因文本，不能只依赖 hover tooltip。
- START 状态变化区域使用 `aria-live="polite"` 或等价机制；失败诊断以文本呈现，不能只靠颜色。
- 触控目标不小于 44px；dev/mock START 按钮仅在 sandbox/dev mock 上下文出现。
- warning/danger/success 必须同时有文字 label；色盲用户不应依赖颜色判断线体是否可生产。

## 专家评审关注点

- 平台控制事件、平台安全事件、插件生产事件 v1 先用代码常量收口；除非后续出现可配置需求，否则不引入数据库配置。
- 当前系统未发布，不需要迁移保留历史 `READY` 数据；可清理/重建 dev/mock 历史数据。
- dev mock 改为 `AUTO` 后，前端 sandbox 仍可保留为调试工具，但普通 Event composer 不能绕过 `STOPPED` 生产 guard。
- 实时 status 查询失败走 retry 可能增加短期 retry 次数，但不会制造物理动作不确定性；真正需要人工 resolve 的边界仍是 command POST 已发出后结果未知。

## 工程评审修订结论（2026-06-01）

### 已确认决策

- 本计划按全栈同批交付处理：后端语义、dev mock、runtime API 合同和 `../wes_frontend` 的 STOPPED/START 展示必须一起落地。
- START 入口保留在 `/callback/event`，作为硬件/ECS 平台控制事件处理；不新增 WES 人工 START API。
- `resolve/clear` 后统一回 `STOPPED`，READY 只能由 `WORKLINE_START_REQUESTED` 准入成功写入。
- event 分类收口到 `src/workline_runtime/runtime_events.py`：平台控制事件处理 START，平台安全事件处理 ESTOP，插件 `supported_events` 只表达生产事件能力。
- 生产事件拒收必须返回真实 HTTP `409`；API route 设置协议状态，service 返回业务 body/status 决策。
- 拒收响应沿用项目数字 `ResponseCode`，业务原因放入 `data.reason_code="WORKLINE_NOT_ACCEPTING_WORK"`。
- START 准入复用 `configuration-status` 结构预检，并补齐 host/port/path 通信配置检查；不得重建平行拓扑校验。
- START 准入诊断写入 WorkLine 稳定投影字段；runtime summary/detail 直接暴露，不要求前端追 callback logs。
- START 准入采用两阶段 CAS，不在持有 DB 行锁时等待 ECS HTTP。
- status timeout 使用 `runtime_config_json.device_status_timeout_seconds`，默认 2s，clamp 到 1s-5s。
- START status 按 ECS endpoint 分组批量优先；command 前 status 保持单设备查询。
- status 探测使用有上限并发，默认 4，clamp 到 1-8。
- START 准入和单次 command dispatch 使用操作级 `httpx.AsyncClient`；GET/POST 共享 client，但使用独立 timeout。
- 当前系统未发布，不需要保留历史 `READY` 兼容数据；可清理/重建 dev/mock 历史数据。

### What Already Exists

- `WorkLineService.configuration_status()` 已覆盖插件、合同版本、run_mode、角色、event source 和 command target 预检；本计划扩展并复用它。
- `WorkLineSafetyService.assert_accepting_work()` 已是 outbox 侧运行态 guard；callback 入口需要同步 guard，避免先落 inbox 再被 worker 拒绝。
- `DeviceCommandGateway._enforce_device_command_governance()` 已完成 DB 投影层校验；本计划在它之后、POST command 之前补实时 status。
- `tests/mock/ecs_mock_server.py` 已有 `/api/v1/device/status`、`/api/v1/device/command` 和 command history；本计划升级 status 批量/单设备合同与 START 调试流。
- `workline_diagnostics`、`callback_logs.ingress_outcome/failure_stage` 已存在，可复用来记录 START/production guard 的拒收诊断。
- `RuntimeWorklineSummary` 已暴露 `runtime_status/stopped_reason/stopped_at/resumed_at`；本计划只补 START 准入投影字段。

### NOT In Scope

- 不新增 WES 人工 START API；START 是硬件/ECS 事件。
- 不做真实 PLC 或物理急停协议设计。
- 不做厂商 status path/adapter 抽象；硬件方提供统一标准 status/command 接口。
- 不做大规模 runtime 架构重构。
- 不保留旧 preset mock event 格式。
- 不创建本地 test 环境；只同步 dev mock。
- 不做全局 HTTP client 池、worker 熔断或大规模并发调度；执行阶段只更新既有 `TODOS.md` P2 benchmark 项，补充 ECS 批量 status、bounded concurrency 和 operation-scoped client 对比。

### Test Coverage Diagram

```text
CODE PATHS                                           USER / DEBUG FLOWS
[+] WorkLine runtime state                           [+] Operator recovery
  ├── [GAP] STOPPED enum + DB CHECK + default          ├── [GAP] clear ESTOP -> STOPPED, wait START
  ├── [GAP] START snapshot fields in summary/detail    └── [GAP] resolve hold -> STOPPED, wait START
  └── [GAP] migration/schema contract for STOPPED

[+] /callback/event ingress                         [+] Swagger/mock callback
  ├── [GAP] [->E2E] real HTTP 409 for production guard ├── [GAP] no inbox / no enqueue / WORKLINE_GUARD
  ├── [GAP] START bypasses production capability path  └── [GAP] START -> READY -> SCAN -> command history
  ├── [GAP] ESTOP bypasses production READY guard
  └── [GAP] numeric code + data.reason_code contract

[+] START admission                                  [+] START race/error states
  ├── [GAP] communication config shared precheck       ├── [GAP] one device non-IDLE names failed device
  ├── [GAP] grouped batch status success/failure       └── [GAP] final CAS blocks ESTOP/RECONCILING drift
  ├── [GAP] timeout/non-2xx/bad JSON/missing device
  └── [GAP] bounded concurrency + timeout clamp

[+] DeviceCommandGateway                             [+] Physical command safety
  ├── [EXISTS] DB projection governance                ├── [GAP] status failure never POSTs command
  ├── [GAP] single-device status full failure matrix   └── [GAP] retry exhausted uses existing reconciliation
  └── [GAP] OK status then POST command

[+] ../wes_frontend STOPPED contract                 [+] User-visible UI
  ├── [GAP] runtime-safety/display STOPPED verdict     ├── [GAP] copy says wait START, not “恢复接收”
  ├── [GAP] monitor/sandbox view copy                  └── [GAP] composer disables production event unless READY
  └── [GAP] START excluded from production templates

COVERAGE: existing tests cover DB projection/outbox safety guards; new START/status/409/frontend branches are gaps.
QUALITY TARGET: every GAP above needs unit/API/frontend coverage; mock flow needs one e2e-style dev test.
```

### Required Tests

- Migration/schema tests: Alembic upgrade creates `STOPPED` runtime status constraint and START admission columns while keeping `native_enum=False`.
- WorkLine model/service tests: new WorkLine default is `STOPPED`; initialization/reset/clear-estop/runtime hold release return `STOPPED`, not `READY`.
- Callback API route tests: production event on `STOPPED/RECONCILING/ESTOPPED` returns real HTTP 409, numeric code, `data.reason_code`, logs `REJECTED/WORKLINE_GUARD`, creates no inbox, enqueues nothing.
- Callback service tests: `WORKLINE_START_REQUESTED` resolves WorkLine by `device_code`, bypasses production capability path, runs START admission, success writes `READY`, failure keeps `STOPPED`; `ESTOP_PRESSED` still bypasses production READY guard.
- START admission tests: happy path, ordinary admission failure, ECS timeout/non-2xx/bad JSON/missing device, bounded concurrency/timeout clamp, and final CAS drift to `ESTOPPED/RECONCILING`.
- Configuration tests: communication config incomplete blocks `configuration-status` and START admission through the same check.
- Device gateway tests: timeout, non-2xx, bad JSON, `mode != AUTO`, `status != IDLE`, `current_command_id != null` never POST command; status OK then POST; retry exhaustion enters existing reconciliation.
- Mock/dev tests: `ecs_mock` supports batch and single-device status response; rough sorter seeds `run_mode=AUTO`, host `mock_ecs:8010`, initial `STOPPED`; `START -> READY -> SCAN_COMPLETED -> command history` works against real HTTP path.
- Frontend tests in `../wes_frontend`: `runtime-safety` / `runtime-display` cover `STOPPED`; monitor/sandbox copy no longer says “恢复接收新流程”；production composer disables non-READY events；START is excluded from production templates.
- Frontend design acceptance tests: START status changes expose visible disabled reasons and `aria-live`/equivalent announcements; dev/mock START button appears only in STOPPED sandbox/dev context; narrow viewport keeps STOPPED DecisionStrip before topology/session content; touch targets meet 44px minimum.

### Failure Modes

| Codepath | Production failure | Expected handling | Test |
|----------|--------------------|-------------------|------|
| START event | ECS sends START from unbound device | 4xx rejected diagnostic, no READY | required |
| START admission | Required device missing from batch status | 409, keep STOPPED, diagnostic names device | required |
| START admission CAS | WorkLine becomes ESTOPPED during status probe | final recheck refuses READY | required |
| Production event guard | SCAN arrives while STOPPED | HTTP 409, no inbox/enqueue | required |
| ESTOP event | ESTOP arrives while STOPPED | still enters safety flow | required |
| Status GET before command | timeout/non-2xx/bad JSON/non-IDLE | no command POST, retry budget applies | required |
| Command POST after status OK | ACK timeout after possible physical side effect | existing ACK timeout reconciliation | existing + regression |
| resolve/clear | user expects running immediately | returns STOPPED; frontend says wait START | required |
| Frontend composer | operator tries SCAN while STOPPED | disabled with wait START message | required in `../wes_frontend` |

Critical silent gaps after this review: 0. Every listed failure mode has planned handling and a required test.

### TODO Decision

- Accepted: update existing `TODOS.md` P2 “Workline worker 吞吐 benchmark 与队列/连接策略调优” during execution, adding ECS batch status, bounded START probe concurrency, operation-scoped client, and future global client pool comparison.
- This review did not edit `TODOS.md` because it is already staged user work in the current tree.

### Implementation Tasks

- [ ] **T1 (P1, human: ~3h / CC: ~30min)** — WorkLine state — add `STOPPED` runtime status, migration, schema contract, defaults, and reset semantics.
  - Surfaced by: Architecture/Test Review — default READY is semantically wrong for unstarted lines.
  - Files: `src/app/workline/models/`, runtime reset/query services, migrations, dev seed scripts.
  - Verify: model/service/migration tests plus dev seed tests.
- [ ] **T2 (P1, human: ~3h / CC: ~25min)** — Event taxonomy and callback 409 — centralize platform event classification and return real HTTP 409 with numeric code + `data.reason_code`.
  - Surfaced by: Code Quality/Test Review — START/ESTOP strings and response contracts must not diverge.
  - Files: `src/workline_runtime/runtime_events.py`, callback API/service modules.
  - Verify: callback route tests for START, ESTOP, production guard, numeric response body.
- [ ] **T3 (P1, human: ~5h / CC: ~45min)** — START admission — implement shared communication precheck, WorkLine snapshot fields, grouped batch status, bounded concurrency, timeout clamp, and two-stage CAS.
  - Surfaced by: Architecture/Performance Review — START must be race-safe and fast without holding DB locks over HTTP.
  - Files: `src/app/workline/services/`, WorkLine model/runtime summary, callback orchestration boundary.
  - Verify: START admission matrix, CAS drift tests, API summary/detail tests.
- [ ] **T4 (P1, human: ~3h / CC: ~25min)** — Recovery transitions — make clear-estop and runtime hold resolve return `STOPPED`.
  - Surfaced by: Architecture Review — READY only comes from hardware START admission.
  - Files: workline safety and runtime hold release services.
  - Verify: safety service and hold release tests.
- [ ] **T5 (P1, human: ~4h / CC: ~35min)** — Device dispatch status — add single-device realtime status GET before command POST with operation-scoped client and independent timeout.
  - Surfaced by: Performance/Test Review — command must not POST after realtime status failure.
  - Files: device command gateway and outbox dispatch tests.
  - Verify: full status failure matrix, success POST, ACK timeout regression.
- [ ] **T6 (P1, human: ~3h / CC: ~30min)** — Dev mock — upgrade `mock_ecs` to batch/single status contract and real START-to-command debug flow.
  - Surfaced by: Scope Challenge — Swagger/mock must show real command history after START.
  - Files: mock ECS server/tests and dev sync scripts.
  - Verify: mock tests and dev sync script tests.
- [ ] **T7 (P1, human: ~4h / CC: ~40min, frontend repo)** — Frontend STOPPED contract — update DecisionStrip hierarchy, warning tone, START state table, recovery copy, composer disabling, dev/mock START entry, and a11y/responsive behavior.
  - Surfaced by: Scope and Design Review — full-stack same batch prevents stale UI semantics and makes STOPPED readable as “waiting START, not fault”.
  - Files: frontend runtime constants/utils/views/composer.
  - Verify: frontend unit tests plus monitor/sandbox smoke test for desktop and narrow viewport.
- [ ] **T8 (P2, human: ~20min / CC: ~5min)** — TODO follow-up — update existing Workline HTTP benchmark TODO with ECS batch status and client strategy details.
  - Surfaced by: TODO decision — future benchmark must include START recovery path, not only command POST.
  - Files: `TODOS.md`.
  - Verify: documentation review only.

### Parallelization Strategy

| Step | Modules touched | Depends on |
|------|-----------------|------------|
| State/migration/recovery | `src/app/workline/models`, `src/app/workline/services` | — |
| Callback/event taxonomy | `src/app/callback`, `src/workline_runtime` | State enum |
| START admission/runtime API | `src/app/workline/services`, runtime summary models | State/migration |
| Device status dispatch | `src/app/workline/services`, device dispatch tests | Status contract |
| Dev mock + seed | `tests/mock`, `scripts/data` | State/migration, status contract |
| Frontend STOPPED contract | `../wes_frontend/src` | Runtime API contract |

- Lane A: State/migration/recovery -> START admission/runtime API.
- Lane B: Event taxonomy/callback can start after state enum lands.
- Lane C: Device status dispatch and dev mock can run in parallel once status response contract is fixed.
- Lane D: Frontend STOPPED contract starts after runtime API fields are stable.
- Conflict flag: Lane A, B, and C all touch `src/app/workline/services`; prefer one backend branch or careful worktree coordination over fully independent backend worktrees.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | Not run for this spec |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | Not run |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | CLEAR | full: 15 issues; design delta: 1 issue; 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | CLEAR | score: 6/10 → 9/10, 6 decisions |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | Not run |

- **UNRESOLVED:** 0
- **VERDICT:** ENG + DESIGN CLEARED — ready to implement.
