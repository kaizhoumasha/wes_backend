<!-- /autoplan restore point: /Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/develop-autoplan-restore-20260513-165950.md -->

# SMT 入库满箱交换 WorkLine 插件实施计划

## Summary

新增 `smt_full_box_exchange` WorkLine 插件，处理 SMT 入库中“粗分机完成单层货架后，按整架 4 个料箱一次性评估满箱交换”的生产过程。

生产事实边界：

1. 粗分机负责把物料装入单层货架料箱。
2. 单层货架整架完成并已从粗分机移出后，WES 生成一个稳定的 `SINGLE_LAYER_RACK_RELEASED` 领域事件。
3. 满箱交换插件只消费该整架释放事件，不在单料、单箱或粗分机未移出阶段提前评估。
4. 插件判断是否需要满箱交换；如需要，通过 Runtime 标准外部请求意图调用 WMS/RCS。
5. WMS/RCS 负责 AGV + CTU 调度、交换区空位、空箱资源、排队和动作闭环；WES 负责 trace、session、timeline、等待、超时和结果归档。

计划结论：方向成立，但必须先补齐 `EXTERNAL_REQUEST` RuntimeIntent、`rack_release_id` 权威来源、WMS/RCS 状态机和最小可观测性，否则容易形成轮询拼接的私有流程，无法稳定处理重复、迟到和资源失败。

第零阶段已执行：WES/WMS/RCS 权责边界见 `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`。本计划后续实现不得让 WES 锁定五层空箱、交换库存属性或自动扣减库存。

## Scope

本计划包含：

- `smt_full_box_exchange` 插件合同、context、handler 和注册。
- Runtime 新增插件侧外部请求意图，统一落为 `EXTERNAL_HTTP` Outbox。
- 单层货架释放候选扫描服务，将粗分机完成事实转换为标准 `DEVICE_EVENT` Inbox。
- WorkLine 与虚拟设备主数据配置建议。
- WMS/RCS 满箱交换请求和回调字段约定。
- 单元测试、集成测试和 sandbox 调试路径。

前置资源模型设计见 `docs/superpowers/specs/2026-05-13-smt-execution-resource-model-design.md`。满箱交换插件依赖其中的 `RackRelease`、`RackReleaseBinSnapshot`、`RackBinMount` 和 WMS 库存引用快照边界。

本计划不包含：

- WES 直接控制 AGV/CTU。
- WES 自建库存主数据、空箱分配、库位锁定或 WMS/RCS 替代调度。
- 完整运营看板、告警中心、库存报表。
- WMS/RCS 全量接口白皮书扩展；本次只约定满箱交换所需的最小协议。
- 前端页面。

## Existing Assets

仓库中已有这些可复用能力：

- `WorklineOutbox.dispatch_type=EXTERNAL_HTTP` 和 `TargetType.HTTP_ENDPOINT`。
- `/api/v1/callback/external` 外部回调入口。
- `InboxKind.EXTERNAL_HTTP`、`SessionResolver._resolve_external_http(trace_id)`。
- `SessionStatus.WAITING_EXTERNAL` 与 timeout scanner 对 `WAITING_EXTERNAL` 的扫描。
- `WorklinePlugin.on_external_http` 默认 hook。
- `run_mode=SIMULATION` 下 `EXTERNAL_HTTP` 仍走 sandbox 调试路径。

当前缺口：

- 插件没有 `ctx.next.external_request(...)`。
- `RuntimeIntentKind` 没有 `EXTERNAL_REQUEST`。
- `RuntimeIntentEffectApplier` 不会把插件外部请求落为 Outbox 和 `WAITING_EXTERNAL`。
- 旧的 `_apply_external_decisions` 属于 legacy decisions 路径，当前主链路是 `OrchestratorResult.intents`，本次不应复活旧路径。
- 外部回调幂等当前依赖 payload hash，WMS/RCS 重发时若时间戳或附加字段变化，会变成新 Inbox。
- 粗分机“整架完成且已移出”的来源事实尚未被建模为满箱交换插件的稳定入口事件。

## Architecture

```text
粗分机任务/货架事实
        |
        v
SmtFullBoxExchangeCandidateService
  - 只识别整架完成 + 已移出
  - 生成 rack_release_id
  - 幂等创建 DEVICE_EVENT Inbox
        |
        v
WorklineSession (business_key=rack_release_id)
        |
        v
smt_full_box_exchange plugin
  - 4 箱快照校验
  - 满箱策略评估
  - 无需交换: COMPLETE
  - 需要交换: EXTERNAL_REQUEST
        |
        v
WorklineOutbox(EXTERNAL_HTTP) -> WMS/RCS
        |
        v
/api/v1/callback/external
        |
        v
plugin.on_external_http
  - progress: update_context, keep WAITING_EXTERNAL
  - physical completed: write evidence, wait for projection / WMS confirmation
  - business completed: COMPLETE
  - rejected/failed: BLOCK 或 runtime reconciliation
```

## Key Decisions

| 决策 | 结论 | 原因 |
| --- | --- | --- |
| 插件标识 | `plugin_key=smt_full_box_exchange`, `contract_version=1.0` | 与现有插件注册机制一致。 |
| WorkLine 边界 | 一个满箱交换区配置一条 WorkLine | 保持交换区排队、trace 和资源失败可定位。 |
| 业务键 | `rack_release_id` | 不能用 `rack_id`，同一物理货架会多轮流转。 |
| 触发事件 | `SINGLE_LAYER_RACK_RELEASED` | 插件只处理整架释放后的稳定事实。 |
| 事件来源设备 | 虚拟设备 `RACK_RELEASE_SOURCE` | 该事件是 WES 内部派生事实，不是 AGV/CTU 设备事件。 |
| 外部动作 | `FULL_BIN_EXCHANGE` | 表达“请求 WMS/RCS 执行满箱交换”。 |
| Outbox 类型 | `EXTERNAL_HTTP` | 复用现有 dispatcher、sandbox、timeout 能力。 |
| 回调类型 | `WMS_FULL_BOX_EXCHANGE_RESULT` | 不只表达完成，还要表达排队、拒绝和失败。 |
| 4 箱假设 | v1 固定 `expected_bin_count=4`，但放在策略配置中 | 当前单层货架是 4 箱；后续货架规格变化时升级合同或配置。 |
| 交换区空位 | v1 由 WMS/RCS 判断并回传 `QUEUED`/`REJECTED_*` | WES 不重复维护库位和调度资源锁。 |

## Rack Release Authority

`rack_release_id` 是本流程的权威业务键，必须由候选服务在创建 `DEVICE_EVENT` Inbox 前生成，且每个物理货架每次释放周期唯一。

生成规则：

- 如果粗分机主链路已有稳定释放 ID，直接使用该 ID。
- 否则使用稳定事实组合生成：`source_classifier_line_code + single_layer_rack_id + source_task_batch_id + moved_out_at`。
- 如果没有 `source_task_batch_id` 或稳定 `moved_out_at`，不得猜测生成；候选服务记录诊断并跳过，直到来源事实补齐。

持久化和幂等：

- `SINGLE_LAYER_RACK_RELEASED` Inbox 的 `idempotency_key` 固定为 `smt_full_box_exchange:rack_release:<rack_release_id>`。
- Inbox `source_message_id` 使用 `rack_release_id`，便于来源侧追踪。
- Session `business_key` 使用同一个 `rack_release_id`。
- 外部请求 `dispatch_key` 使用 `external:smt_full_box_exchange:<rack_release_id>:FULL_BIN_EXCHANGE`。
- WMS/RCS 请求 payload、回调 payload 均必须携带 `rack_release_id` 和 `trace_id`。

候选服务不是业务判断器。它只负责把“整架完成且已移出”转换成标准 Inbox，不计算满箱策略，不写 Session，不写 Outbox。

## Event Contract

`SINGLE_LAYER_RACK_RELEASED` 的业务字段只放在 `data` 内。

必填字段：

| 字段 | 含义 |
| --- | --- |
| `rack_release_id` | 本次单层货架释放周期 ID。 |
| `single_layer_rack_id` | 物理单层货架 ID。 |
| `source_classifier_line_code` | 来源粗分机 WorkLine 编码。 |
| `source_task_batch_id` | 粗分机本次整架任务或批次 ID。 |
| `released_at` | 整架释放时间，使用来源事实时间。 |
| `moved_out_at` | 货架离开粗分机时间。 |
| `bins` | 4 个料箱快照。 |

`bins` 校验规则：

- v1 必须正好 4 个。
- `slot_code` 不可重复，建议使用 `S1`、`S2`、`S3`、`S4`。
- 每个料箱必须有 `bin_id`、`slot_code`、`status`、`usage`。
- `usage` 范围为 `0.0` 到 `1.0`。
- `status` 不在允许集合、缺箱、重复箱、usage 缺失时，插件返回 `BLOCK(PAYLOAD_INVALID)`，不静默跳过。

## Exchange Policy

默认满箱判断：

- `expected_bin_count=4`
- `full_statuses=["CLOSED"]`
- `full_usage_threshold=0.8`
- `min_exchange_bin_count=1`
- `require_all_bins=false`

即：整架 4 箱都到齐后，只要符合策略的料箱数量达到 `min_exchange_bin_count`，就发起满箱交换请求；请求 payload 携带 4 箱快照和 `exchange_bins` 命中列表。若现场必须 4 箱全部满足才交换，将 WorkLine config 改为 `require_all_bins=true`。

无需交换时：

- 插件直接 `COMPLETE`。
- context 记录 `exchange_required=false`、`exchange_policy_version`、`evaluated_bins`、`qualified_bin_count`。

需要交换时：

- 插件返回 `update_context` + `external_request`。
- context 记录 `exchange_required=true`、`exchange_status=REQUESTED`、`exchange_request_code`、`exchange_bins`。

## WMS/RCS Protocol

请求方向：WES -> WMS/RCS。

最小请求字段：

| 字段 | 含义 |
| --- | --- |
| `request_code` | 与 `dispatch_key` 同源的请求编码。 |
| `trace_id` | WES trace id。 |
| `rack_release_id` | 本次释放业务键。 |
| `single_layer_rack_id` | 单层货架 ID。 |
| `source_workline_code` | 来源粗分机 WorkLine。 |
| `exchange_area_code` | 满箱交换区编码。 |
| `bins` | 4 箱快照。 |
| `exchange_bins` | 本次建议交换的料箱槽位。 |
| `exchange_policy` | 本次评估使用的策略摘要。 |
| `callback_url` | WMS/RCS 回调 WES 的 `/api/v1/callback/external` 地址。 |

HTTP `200` 只表示 WMS/RCS 接收请求，不表示物理交换完成。物理状态必须通过外部回调进入 WES。

回调方向：WMS/RCS -> WES `/api/v1/callback/external`。

回调字段：

| 字段 | 含义 |
| --- | --- |
| `callback_type` | 固定 `WMS_FULL_BOX_EXCHANGE_RESULT`。 |
| `trace_id` | WES trace id，用于恢复 Session。 |
| `source_system` | `WMS` 或 `RCS`。 |
| `wms_rcs_task_id` | WMS/RCS 侧任务 ID。 |
| `source_event_id` | WMS/RCS 侧稳定事件 ID，必须用于幂等。 |
| `source_version` | WMS/RCS 侧单调版本或业务版本。 |
| `occurred_at` | 来源事实发生时间。 |
| `request_id` | 来源请求唯一 ID，用于重放防护。 |
| `timestamp` | 签名时间窗校验。 |
| `signature` | 按双方约定 canonical payload 计算。 |
| `rack_release_id` | 必须与 Session context 一致。 |
| `exchange_status` | 状态枚举。 |
| `post_exchange_relations` | `PHYSICAL_COMPLETED` / `RESOURCE_PROJECTED` 前必填，表示交换后 rack/bin/slot 关系。 |
| `wms_confirmation` | `WMS_CONFIRMED` 前必填，表示 WMS 库存、单据或业务版本确认。 |
| `queue_position` | 进入排队时可选。 |
| `eta_seconds` | 预计等待或执行时间，可选。 |
| `failure_code` | 失败或拒绝时必填。 |
| `failure_message` | 失败或拒绝时必填。 |

状态语义：

| 状态 | Runtime 处理 |
| --- | --- |
| `ACCEPTED` | `update_context(exchange_status=ACCEPTED)`，保持 `WAITING_EXTERNAL`。 |
| `QUEUED` | `update_context(exchange_status=QUEUED, queue_position, eta_seconds)`，保持 `WAITING_EXTERNAL`。 |
| `IN_PROGRESS` | `update_context(exchange_status=IN_PROGRESS)`，保持 `WAITING_EXTERNAL`。 |
| `PHYSICAL_COMPLETED` | 记录物理完成 evidence；缺 `post_exchange_relations` 时进入 `RECONCILING`，不完成 Session。 |
| `RESOURCE_PROJECTED` | 资源当前投影已根据可信关系更新，继续等待 WMS 确认。 |
| `WMS_CONFIRMED` | 记录 WMS 确认证据，继续等待或推进到业务完成。 |
| `BUSINESS_COMPLETED` | `COMPLETE`，记录 `wms_rcs_task_id`、资源投影和 WMS 确认摘要。 |
| `WMS_REJECTED` | `BLOCK(EXCHANGE_WMS_REJECTED)`，库存或业务确认失败，需人工确认。 |
| `REJECTED_EXCHANGE_AREA_FULL` | `BLOCK(EXCHANGE_RESOURCE_UNAVAILABLE)`，建议重试或人工确认排队策略。 |
| `REJECTED_EMPTY_BIN_UNAVAILABLE` | `BLOCK(EXCHANGE_RESOURCE_UNAVAILABLE)`，建议等待空箱资源。 |
| `FAILED_AGV` | `BLOCK(EXCHANGE_EXECUTION_FAILED)`，责任域下游设备/调度。 |
| `FAILED_CTU` | `BLOCK(EXCHANGE_EXECUTION_FAILED)`，责任域下游设备/调度。 |
| `CANCELLED` | `BLOCK(EXCHANGE_CANCELLED)`，需要人工确认实物状态。 |
| `UNKNOWN` | `BLOCK(EXCHANGE_STATUS_UNKNOWN)`，需要人工对账。 |

迟到回调：

- 如果 Session 已因 `WAITING_EXTERNAL` 超时进入 runtime reconciliation，迟到的 `BUSINESS_COMPLETED` 不自动把 Session 改成完成。
- 迟到证据应记录到诊断或 reconciliation evidence，由人工决议完成、失败或取消。

幂等与安全：

- WMS/RCS 必须发送稳定 `source_event_id` 和 `request_id`。
- WES 外部回调 Inbox 幂等应优先使用 `callback_type + trace_id + source_event_id`；没有稳定事件 ID 时不得更新资源 active 投影。
- 回调必须通过 `timestamp + signature` 的时间窗和签名校验；验签失败按契约错误拒绝并记录审计。

## Runtime Changes

目标：让插件以标准 RuntimeIntent 表达外部请求，Runtime 统一落库、等待、派发和超时。

涉及文件：

| 文件 | 改动 |
| --- | --- |
| `src/workline_runtime/runtime_intent.py` | 新增 `RuntimeIntentKind.EXTERNAL_REQUEST`，字段复用或新增 `target_code`、`dispatch_key`、`source_system` 语义校验。 |
| `src/workline_runtime/plugin_next.py` | 新增 `external_request(...)` helper。 |
| `src/workline_runtime/runtime_intent_effects.py` | 支持 `EXTERNAL_REQUEST`，落 `WorklineOutbox(EXTERNAL_HTTP)`，设置等待态并写 Timeline。 |
| `src/celery_app/tasks/workline.py` | `_result_requires_outbox_dispatch` 识别 `EXTERNAL_REQUEST`，保证 Outbox 创建后立即触发派发。 |
| `src/app/workline/repositories/inbox_repository.py` | 外部回调幂等优先使用 `source_event_id` 或稳定 source message id。 |
| `src/app/workline/models/outbox.py` | 若生产 WMS/RCS URL 超过 100 字符，增加迁移扩大 `target_code` 长度；否则 v1 明确使用短 URL。 |

Effect 规则：

- `dispatch_type=EXTERNAL_HTTP`。
- `target_type=HTTP_ENDPOINT`。
- `target_code` v1 为真实 HTTP URL，因为现有 dispatcher 直接 `POST outbox.target_code`。
- `dispatch_key` 必须由插件传入且全局唯一。
- `payload_json` 为发给 WMS/RCS 的请求体。
- Session 状态设为 `WAITING_EXTERNAL`。
- `current_wait_type="EXTERNAL_HTTP"`。
- `waiting_since=now`。
- `current_wait_timeout_seconds=timeout_seconds`。
- `deadline_at=now + timeout_seconds`，外部 HTTP 没有设备 ACK 阶段，必须立即激活 deadline。
- `awaiting_command_id=None`。
- Timeline 记录 `WAIT_STARTED`，payload 包含 `wait_type=EXTERNAL_HTTP`、`dispatch_key`、`target_code`、`timeout_seconds`。

RuntimeIntent 组合校验：

- 同一次插件返回中最多一个“产生副作用等待”的 intent：`COMMAND`、带 action 的 `CONTINUE_NEXT`、`EXTERNAL_REQUEST` 三者合计最多一个。
- terminal intent 仍必须放在最后。
- terminal intent 不可跟在 `EXTERNAL_REQUEST` 之后。
- `EXTERNAL_REQUEST` 缺 `dispatch_key`、`target_code`、`payload` 或 `timeout_seconds` 时直接 Runtime 校验失败。

## Plugin Changes

新增目录：

```text
src/workline_plugins/smt_full_box_exchange/
  __init__.py
  contract.py
  context.py
  plugin.py
```

职责：

| 文件 | 职责 |
| --- | --- |
| `contract.py` | 定义释放事件、料箱快照、交换请求、WMS/RCS 回调 payload、业务键解析、策略校验。 |
| `context.py` | 定义 `SmtFullBoxExchangeContext`，集中解析 Session context。 |
| `plugin.py` | 处理 `SINGLE_LAYER_RACK_RELEASED` 和 `on_external_http`。 |
| `__init__.py` | 导出插件类和实例。 |

Manifest：

- `required_device_roles=(DeviceRoleRequirement("RACK_RELEASE_SOURCE", 1, 1, {"SINGLE_LAYER_RACK_RELEASED"}),)`
- `event_source_roles={"SINGLE_LAYER_RACK_RELEASED": "RACK_RELEASE_SOURCE"}`
- `supported_events={"SINGLE_LAYER_RACK_RELEASED"}`
- `business_key_resolver` 只从 `data.rack_release_id` 读取。
- 不声明 AGV、CTU 或五层货架为插件设备角色。

Handler：

- `handle_rack_released`
  - 校验 4 箱快照。
  - 读取 WorkLine config 中的交换策略、交换区编码、WMS/RCS URL 和超时。
  - 无需交换时 `complete`。
  - 需要交换时 `update_context` + `external_request`。
- `on_external_http`
  - 解析 `NormalizedExternalCallback` 或原始 payload。
  - 校验 `callback_type`、`trace_id`、`rack_release_id`。
  - 处理 progress 状态：只更新 context，保留等待。
  - 处理阶段状态：`PHYSICAL_COMPLETED` 只记录 evidence，`RESOURCE_PROJECTED` 只表示资源投影完成，`WMS_CONFIRMED` 只表示 WMS 确认完成。
  - 处理 terminal 状态：仅 `BUSINESS_COMPLETED` -> `complete`，失败/拒绝/取消/未知 -> `block`。

## Candidate Service

新增服务建议：`src/app/workline/services/smt_full_box_exchange_candidate_service.py`。

职责：

- 查询来源粗分机的完成事实和货架移出事实。
- 只在“整架完成 + 已移出 + 4 箱快照完整 + 未创建同 `rack_release_id` Inbox”时创建 Inbox。
- 使用 `inbox_service` 或 repository 创建 `DEVICE_EVENT`，不直接写 Session/Outbox。
- 所有数据访问放在 Service/Repository 层，遵守 API -> Service -> Repository -> Database 分层规则。

Celery：

- 在 `src/celery_app/tasks/workline.py` 增加 `scan_smt_full_box_exchange_candidates_batch`。
- 在 `src/celery_app/config.py` 配置低频 beat，例如 30-60 秒，具体值由现场吞吐决定。
- 该扫描是补偿入口；长期更理想的是粗分机主链路在提交整架完成事务时直接产出释放事实。

如果来源事实目前不足：

- 不做“最佳猜测”。
- 先补齐粗分机释放事实来源，或增加最小 release ledger，再接入候选服务。

## Master Data

### WorkLine

建议字段：

| 字段 | 值 |
| --- | --- |
| `line_code` | `WL-SMT-FULL-BOX-EXCHANGE-01` |
| `line_name` | `SMT入库满箱交换区#1` |
| `line_type` | `AUTO` |
| `plugin_key` | `smt_full_box_exchange` |
| `contract_version` | `1.0` |
| `run_mode` | `AUTO`，测试环境可用 `SIMULATION` |

建议 `config`：

```json
{
  "source_classifier_line_codes": ["WL-SMT-CLASSIFIER-01"],
  "exchange_area_code": "SMT_FULL_BOX_EXCHANGE_A",
  "exchange_policy": {
    "expected_bin_count": 4,
    "full_statuses": ["CLOSED"],
    "full_usage_threshold": 0.8,
    "min_exchange_bin_count": 1,
    "require_all_bins": false
  },
  "external_endpoints": {
    "wms_rcs_full_box_exchange_url": "http://wms-rcs/api/full-box-exchange"
  },
  "timeouts": {
    "external_exchange_seconds": 1800
  }
}
```

### 虚拟设备

建议字段：

| 字段 | 值 |
| --- | --- |
| `device_code` | `SMT_FULL_EXCHANGE_TRIGGER_01` |
| `device_name` | `SMT满箱交换触发源#1` |
| `device_role` | `RACK_RELEASE_SOURCE` |
| `vendor_type` | `SYSTEM` |
| `workline_id` | 满箱交换 WorkLine ID |
| `status` | 可用状态 |

建议 `capabilities_json`：

```json
{
  "virtual": true,
  "events": ["SINGLE_LAYER_RACK_RELEASED"],
  "notes": "由 WES 候选服务派生整架释放事件"
}
```

AGV、CTU、五层货架、空箱资源不配置为本插件设备。它们属于 WMS/RCS 调度域；WES 通过外部请求和回调感知结果。

## Minimal Observability

本次不做完整前端看板，但必须保证后端 trace 可查：

- Session context 至少包含 `rack_release_id`、`single_layer_rack_id`、`exchange_required`、`exchange_status`、`exchange_request_code`、`exchange_task_id`、`queue_position`、`eta_seconds`、`exchange_bins`。
- Timeline 至少记录：释放事件进入、策略评估、外部请求发起、外部等待开始、progress 回调、terminal 回调、block/reconciliation。
- 失败 block 必须有稳定 `reason_code` 和 `suggested_action`。
- runtime query/trace API 能通过已有 Session、Timeline、Outbox、Diagnostic 查询到该流程，不新增专用页面。

## Failure Handling

| 场景 | 处理 |
| --- | --- |
| 释放事件缺字段、4 箱不完整、重复 slot | 插件 `BLOCK(PAYLOAD_INVALID)`。 |
| WorkLine config 缺 WMS/RCS URL 或 timeout | Runtime/plugin 校验失败并阻断，诊断指向主数据配置。 |
| 满箱策略不命中 | `COMPLETE(exchange_required=false)`。 |
| WMS/RCS HTTP 派发失败 | Outbox retry；重试耗尽后走现有 dispatch failure/reconciliation 路径。 |
| WMS/RCS 回 `QUEUED` | 更新 context，继续等待。 |
| WMS/RCS 回 `PHYSICAL_COMPLETED` 但缺交换后关系 | 记录 evidence，进入 `RECONCILING`，不更新 active mount。 |
| WMS/RCS 回 `WMS_CONFIRMED` 但缺 WMS 确认引用 | `BLOCK(EXCHANGE_WMS_CONFIRMATION_INVALID)`。 |
| WMS/RCS 回资源不足拒绝 | `BLOCK(EXCHANGE_RESOURCE_UNAVAILABLE)`。 |
| WMS/RCS 回 AGV/CTU 执行失败 | `BLOCK(EXCHANGE_EXECUTION_FAILED)`。 |
| 超过 `external_exchange_seconds` 无 terminal 回调 | timeout scanner 创建 `TIMER_TIMEOUT`，进入 runtime reconciliation。 |
| 超时后迟到成功 | 记录为迟到证据，不自动完成 Session。 |
| 重复回调 | 使用稳定事件 ID 幂等；已处理重复回调不产生二次状态推进。 |

## Implementation Steps

1. Runtime 外部请求能力
   - 修改 `runtime_intent.py`、`plugin_next.py`、`runtime_intent_effects.py`。
   - 更新 `_result_requires_outbox_dispatch`。
   - 补 `EXTERNAL_REQUEST` 合同、组合校验、effect、timeout 和 timeline 测试。

2. 外部回调幂等增强
   - 调整 external callback Inbox 幂等策略，优先稳定 `source_event_id` 或 source message id。
   - 补 API 与 repository 测试。

3. 候选服务与 Celery 入口
   - 增加候选服务，封装粗分机释放事实适配。
   - 增加 beat task 和配置。
   - 补“整架完成 + 已移出 + 幂等创建 Inbox”测试。

4. 插件实现
   - 新增 `src/workline_plugins/smt_full_box_exchange/`。
   - 实现合同、context、event handler、external callback handler。
   - 注册到 `src/workline_plugin_registry.py` 和 `src/workline_plugins/__init__.py`。

5. 主数据校验与文档
   - 验证 `RACK_RELEASE_SOURCE` 拓扑绑定。
   - 在插件开发指南或本计划后续实现文档中补 master data 示例。

6. 集成验证
   - 用 `run_mode=SIMULATION` 验证事件 -> 外部请求 sandbox -> 手工 external callback -> Session 完成/阻断。
   - 验证 timeout scanner 对 `WAITING_EXTERNAL` 生效。

## Test Plan

```text
Contract tests
  -> release event validation
  -> business_key_resolver
  -> exchange policy
  -> WMS/RCS callback validation

Plugin tests
  -> no exchange COMPLETE
  -> need exchange EXTERNAL_REQUEST
  -> ACCEPTED/QUEUED progress callback
  -> PHYSICAL_COMPLETED evidence callback
  -> WMS_CONFIRMED evidence callback
  -> BUSINESS_COMPLETED terminal callback
  -> REJECTED/FAILED/CANCELLED block

Runtime tests
  -> RuntimeIntent EXTERNAL_REQUEST contract
  -> PluginNext.external_request
  -> Effect creates EXTERNAL_HTTP Outbox
  -> Session WAITING_EXTERNAL with immediate deadline_at
  -> invalid intent combinations rejected
  -> _result_requires_outbox_dispatch enqueues dispatch

Ingress/resolver tests
  -> external callback trace_id resolves waiting session
  -> stable source_event_id idempotency
  -> late callback after reconciliation does not auto-complete

Candidate service tests
  -> complete + moved_out creates one Inbox
  -> incomplete rack skipped
  -> missing stable release facts skipped with diagnostic
  -> duplicate scan returns existing Inbox

Master data tests
  -> plugin registry lists smt_full_box_exchange
  -> missing RACK_RELEASE_SOURCE rejected
  -> virtual source device accepted
```

建议验证命令：

```bash
uv run pytest -q tests/workline_runtime/test_runtime_intent_contract.py tests/workline_runtime/test_plugin_next.py
uv run pytest -q tests/workline_runtime/test_runtime_intent_effects.py tests/workline_runtime/test_timeout_scanner.py
uv run pytest -q tests/workline_runtime/test_session_resolver.py tests/api/test_callback_api.py tests/api/test_callback_idempotency.py
uv run pytest -q tests/workline_plugins/test_smt_full_box_exchange_plugin.py
uv run pytest -q tests/workline_runtime/test_smt_full_box_exchange_candidate_service.py
uv run pytest -q tests/test_workline_service_plugin_validation.py
uv run ruff check src/workline_runtime src/workline_plugins/smt_full_box_exchange src/app/workline/services src/celery_app tests
```

## Risks

- GitNexus 索引当前为空且滞后。实施前必须先重建索引或记录不可用风险，并在修改函数/类前做影响分析。
- 如果粗分机侧没有稳定 release fact，本插件不能直接落地；必须先补 `rack_release_id` 来源。
- 如果 WMS/RCS 无法提供稳定 `source_event_id`，重复回调不能更新 active 投影，只能作为 evidence 进入对账。
- 如果 WMS/RCS URL 超过 `WorklineOutbox.target_code` 当前长度，必须先做 schema 迁移。
- 如果现场希望 WES 自己维护交换区空位和排队，本计划的 WMS/RCS 边界需要调整，并新增资源模型与排队状态。

## Review Notes

`$autoplan` 评审已采纳以下意见：

- CEO 视角：不能只做插件，要定义入库满箱交换的业务闭环、排队语义、资源边界和成功指标。
- Engineering 视角：`rack_release_id` 必须有权威来源；外部请求必须走 RuntimeIntent 主路径；`WAITING_EXTERNAL` 必须立即设置 `deadline_at`；WMS/RCS 状态机和回调幂等要补齐。
- DX 视角：计划必须让实现者知道文件职责、主数据配置、sandbox 调试和测试路径。
- Claude subagent 视角：本环境未使用 subagent，因为当前工具策略只允许在用户明确要求代理/并行代理时 spawn。

## Acceptance Criteria

- 单层货架释放事件重复扫描不会创建重复 Session。
- 同一物理货架下一轮释放会生成新的 `rack_release_id`，不会复用旧终态 Session。
- 需要交换时插件只产生一个 `EXTERNAL_REQUEST`，Runtime 创建一个幂等 `EXTERNAL_HTTP` Outbox。
- Session 进入 `WAITING_EXTERNAL` 后有明确 `deadline_at`，timeout scanner 能进入 runtime reconciliation。
- `QUEUED` / `IN_PROGRESS` 回调不会错误完成流程。
- 只有 `BUSINESS_COMPLETED` 回调能完成 Session；`PHYSICAL_COMPLETED` 和 `WMS_CONFIRMED` 只能推进 evidence / projection / confirmation 阶段。
- `run_mode=SIMULATION` 能完整演练外部请求和回调闭环。
- 所有修改遵守 API -> Service -> Repository -> Database 分层，不在 API 或插件中直连数据库。
