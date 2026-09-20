# SPEC: WES 可靠恢复、业务任务隔离与 RCS/ECS 物理事实边界

## 1. 目标与范围

联调中出现了四类连锁故障：KT16 被 `RUNTIME_RESET` 停止；`RECONCILING` 只能人工恢复；`NO_BATCH`、取消货架和 completion 判定互相阻塞；历史 drain binding 异常打断整个 prepare 批次，导致 `AC5A908C3AE33400487AB78A8DEEC5043` 长期 `QUEUED`。新线程还出现 `510042` 的 `RACK_ROTATE` 已 `ACCEPTED` 但无终态回调。

本 SPEC 覆盖 WMS confirmation、TransportTask、DeviceCommand、工作线运行状态和手工拣选插件。系统未发布，不保留兼容层，不迁移旧数据；开发/测试数据可以清理后按新模型重建。

## 2. 所有权边界

### 2.1 基础能力

`src/app/execution`、`src/app/transport`、`src/app/device` 和 Celery 基础任务只负责：稳定身份、payload digest 和幂等；claim/lease、超时、退避和可靠发送；callback/Evidence 持久化与冲突检测；原身份迟到结果闭合；`DATA_CONFLICT` 记录和批次异常隔离。

基础能力不得导入 `workline_plugins`，可独立部署、运行和测试。

### 2.2 业务能力

`workline_plugins/manual-picking` 只负责创建 inbound/return/completion/drain intent、解释 WMS 结果和定义 `NO_BATCH`、取消货架、task 完成顺序。插件只依赖基础能力的 typed port，不读取原始 JSON，不实现 HTTP、claim、重试或 Transport 状态机。

### 2.3 物理事实

WES 不拥有物理资源围栏，不依据 `position_unknown`、历史 Transport、设备占用或旧 task binding 阻止新物理指令。RCS/ECS 负责物理冲突、重复指令控制、设备准入和最终位置事实。WES 只保存请求身份、因果关联和外部结果。

`picking_task_id` 只表示业务归属，不表示物理资源所有权，也不能通过创建空壳 task 绕过状态。

## 3. 统一可靠状态机

### 3.1 WMS confirmation

```text
PENDING → DISPATCHING → COMPLETED
                    └→ RECONCILING → PENDING
```

`PENDING` 可领取；`DISPATCHING` 持有有效 lease；`RECONCILING` 超时或响应冲突，保留原身份并等待恢复；`COMPLETED` 有权威 WMS Evidence 和 typed result，不再发送。`mark_reconciling()` 不得关闭重试资格；`requeue_reconciling()` 不再作为唯一恢复入口。

### 3.2 TransportTask 与 DeviceCommand

两者沿用各自现有终态字面量，补齐发送前领取、外部等待超时进入 `RECONCILING`、原身份重试、迟到终态单调闭合：

```text
PENDING/ACCEPTED → SUCCEEDED 或 FAILED/REJECTED
                 └→ RECONCILING → 原身份再次发送
```

`ACCEPTED` 无结果只代表对端已接纳，不能被 WES 当成成功或失败。RCS/ECS 的 `client_request_id`/`command_code` 是对端幂等键；WES 每次只允许一个有效 lease，回调必须匹配原 identity 和 payload digest。

## 4. 数据模型（直接替换，不做兼容）

在 `wes_biz.wms_confirmations`、`wes_runtime.transport_tasks`、`wes_biz.device_commands` 使用同一组字段；移除旧 retry 别名字段，不双写：

```text
retry_count          integer not null default 0
next_retry_at        timestamptz not null
first_reconciling_at timestamptz null
last_retry_at        timestamptz null
last_retry_reason    varchar(120) null
alert_level          smallint not null default 0
claim_token          uuid null
claim_expires_at     timestamptz null
last_dispatch_at     timestamptz null
```

`deadline_at` 是当前尝试的结果等待窗口，不是永久截止点。每次进入 `DISPATCHING` 前重算：`attempt_deadline = dispatch_started_at + result_timeout`。

默认参数：`base_delay=1s`、`multiplier=2`、`max_delay=300s`、`jitter=±20%`。`alert_level`：3 次或 5 分钟为 1，10 次或 30 分钟为 2，100 次或 4 小时为 3；只影响告警，不影响恢复。

新增基础表 `wes_runtime.reconciliation_attentions`：

```text
id bigint primary key
domain varchar(40) not null
identity_key varchar(240) not null
source_record_id bigint not null
code varchar(80) not null
detail_json jsonb not null
status varchar(20) not null -- OPEN/RESOLVED
first_seen_at timestamptz not null
last_seen_at timestamptz not null
occurrence_count integer not null default 1
resolved_at timestamptz null
unique(domain, identity_key, code)
```

本期不提供复杂管理后台；使用已有 debug/查询 API 读取，使用同一 Service 的 typed 方法标记 `RESOLVED`。禁止直接 SQL 删除 attention 或原始事实。

## 5. 公共 dispatcher 协议

所有基础 dispatcher 使用同一事务协议：

1. `SELECT ... FOR UPDATE SKIP LOCKED` 领取 `PENDING` 或 `RECONCILING` 且 `next_retry_at <= now()` 的记录。
2. 仅当 `claim_expires_at is null or claim_expires_at < now()` 时可领取。
3. 写入新的 `claim_token`、`claim_expires_at=now()+60s`、`last_dispatch_at=now()`、`status=DISPATCHING`、`retry_count=retry_count+1`。
4. 提交事务后调用外部系统，禁止持有数据库锁做 HTTP/RCS/ECS 调用。
5. 回写时必须匹配 `claim_token`；lease 过期的旧 worker 不得覆盖新状态。
6. timeout worker 将 `DISPATCHING` 转为 `RECONCILING`，写入 `last_retry_reason` 和 `next_retry_at`。
7. 迟到 callback 只按原 identity 写入 Evidence；response/payload 冲突写 `DATA_CONFLICT`，不覆盖第一次事实。

基础任务入口：

| 对象 | 发送/恢复服务 | 超时与回调 |
|---|---|---|
| WMS | `WmsConfirmationService` / `src/celery_app/tasks/wms_confirmation.py` | `record_delivery_unknown()` / `complete()` |
| Transport | `TransportService` / `src/celery_app/tasks/transport.py` | Transport deadline worker / outcome processor |
| DeviceCommand | `DeviceDispatchService` / `src/celery_app/tasks/device_command.py` | Device deadline worker / `DeviceEvidenceService` |

三个对象各自保存事实，不抽象成业务插件可见的万能 `call` 接口。

## 6. task 隔离与 `position_unknown`

`TransportDecisionBinding` 查询必须带 `workline_id + picking_task_id + step`。历史 task 的 binding 不得进入当前 task 的业务判定。相同物理货架是否可接受新指令由 RCS/ECS 决定，WES 不增加物理围栏 task。

| 消费者 | 新语义 |
|---|---|
| position projection / Transport outcome / audit / monitor | 只记录和展示事实 |
| `BatchRepository.occupied_source_rack_ids()`、`fenced_source_rack_ids()` | 删除作为物理提交门禁的查询，只保留 task 业务 binding 查询 |
| `rack_ready` | 只检查当前 task 的业务 Decision 和最新权威 Transport result |
| `scan_flow` | 只按当前 operation/result/evidence 推进，不扫描历史物理占用 |
| `completion_repository` | 只检查当前 task 的未闭业务面、料箱和可靠决定 |

RCS/ECS 明确 `REJECTED/FAILED` 时，WES 保存该终态并交给业务插件解释，不改写为成功。

## 7. 异常隔离与手工拣选

### 7.1 `DATA_CONFLICT`

`DrainRepository`、Transport outcome processor 和 prepare coordinator 对 identity/evidence/owner mismatch 必须在单条记录 savepoint 内写 attention，回滚该条业务副作用，然后继续同批下一个 workline/task。不得让异常冒泡终止整个 Celery 批次。

### 7.2 业务规则

- 取消来源货架可以先发起 `return_batch`。
- `NO_BATCH` 是确定终态，不重试，继续 CTU02/CTU03。
- 已取消来源货架不阻塞 `completion_confirm`。
- 当前 task 无待执行料箱、无执行中料箱、无未闭业务货架面和无未闭可靠决定时，请求 `outbound.picking_task.completion_confirm@v1`。
- 无 active PickingTask 且有 READY 退料箱时创建 drain。
- drain 超时继续用原 identity 自动恢复。

### 7.3 RUNTIME_RESET

`wes_runtime.workline_runtime_status_projections` 保存 `source`、`stopped_reason`、`stopped_at`、`resumed_at` 和 `resume_evidence_id`。`RUNTIME_RESET` 不是业务 `STOPPED`。恢复 Service 将状态设为 `READY` 后，在 `celery` 队列投递一次 `activate_picking_task_plans_batch`；workline advisory lock 保证幂等，失败只写 attention，下一 tick 重试。

## 8. 生产异常验收样本

| 样本 | 复现 | 通过判据 |
|---|---|---|
| 510042 | 创建 `RACK_ROTATE`，保存 `ACCEPTED`，不写终态 Evidence，推进到 deadline | 原 Transport 进入 `RECONCILING`，`retry_count` 增加，生成同 identity 的下一次发送；其它 task/prepare 行数不减少 |
| AC5A... | 插入 drain binding 与 READY Evidence 不匹配 | 产生一条 `DATA_CONFLICT` attention；该 task 不创建伪造 prepare；其它 task 的 prepare confirmation 正常生成 |
| KT16 | `A000001905`、510050、510055 按 `NO_BATCH` 路径运行 | CTU03、completion_confirm、drain 依次有权威记录，取消货架不阻塞完成 |
| 迟到回调 | 自动重试后发送第一次 identity 的迟到终态 | 原记录闭合；不创建第二个业务结果；冲突响应进入 attention |

## 9. Acceptance Criteria 与测试映射

| AC | 通过条件 | Owner |
|---|---|---|
| AC1 | 三类事实表都按原 identity 自动恢复，digest 不变 | 基础 FAST + integration |
| AC2 | `SKIP LOCKED` 下同一记录只有一个有效 lease，过期 lease 可重领 | 基础 integration |
| AC3 | deadline 每次按当前尝试重算，超过告警阈值仍继续恢复 | 基础 FAST |
| AC4 | 510042 场景不影响其它 task/prepare | Transport integration |
| AC5 | 单条 DATA_CONFLICT 不打断 prepare 批次，attention 唯一且可查询 | 基础 integration |
| AC6 | 历史 task binding 不进入当前 task 查询 | Binding FAST |
| AC7 | `position_unknown` 不再作为物理提交门禁，RCS reject 仍原样保存 | Transport/plugin integration |
| AC8 | `NO_BATCH` 后继续 CTU03，取消货架不阻塞 completion | manual-picking FAST |
| AC9 | 无 active task + READY 料箱自动 drain，timeout 自动原身份恢复 | manual-picking integration |
| AC10 | RUNTIME_RESET 恢复后产生一次幂等 driver tick | workline integration |

基础能力测试放 `tests/` 对应核心域；手工拣选行为放 `workline_plugins/manual-picking/tests/`；真实 Celery/数据库放 integration；同步更新 `docs/architecture/heavy-test-impact.toml`。纯文档修改不新增测试。

## 10. 依赖与实施顺序

```text
#1 基础模型与状态机
 ├─> #2 claim/lease dispatcher
 ├─> #3 WMS / Transport / DeviceCommand 接入
 └─> #4 DATA_CONFLICT 与批次隔离
       └─> #5 task 隔离与 position_unknown 消费者收敛
             └─> #6 手工拣选 completion/drain
                   └─> #7 KT16 联调验收
```

基础能力先完成并独立测试；业务插件最后接入，禁止用插件测试证明基础 dispatcher 正确。

## 11. 文件与交付边界

| File | Change |
|---|---|
| `src/app/execution/services/wms_confirmation_service.py` | 状态机、claim/lease、deadline 重算 |
| `src/app/execution/models/wms_confirmation.py` | retry/lease 字段 |
| `src/app/transport/models.py` | Transport retry/lease 字段 |
| `src/app/device/models/command.py` | Device retry/lease 字段 |
| `src/celery_app/tasks/wms_confirmation.py` | due dispatcher |
| `src/celery_app/tasks/transport.py` | due dispatcher |
| `src/celery_app/tasks/device_command.py` | due dispatcher |
| `src/app/execution/repositories/transport_decision_binding_repository.py` | task-scoped 查询 |
| `src/app/wms_integration/outbound_picking/services/picking_task_prepare_batch.py` | 单条异常隔离 |
| `workline_plugins/manual-picking/src/manual_picking/application/batch_repository.py` | 移除物理资源门禁 |
| `workline_plugins/manual-picking/src/manual_picking/application/drain_repository.py` | attention 与错误隔离 |
| `workline_plugins/manual-picking/src/manual_picking/application/completion_repository.py` | 当前 task 完成判定 |
| `src/app/workline/services/workline_configuration_service.py` | reset/resume 语义 |
| `docs/architecture/device-command-contract.md` | 顶层超时合同 |
| `docs/architecture/SRS.md` | WES/RCS/ECS 所有权合同 |
| `docs/architecture/heavy-test-impact.toml` | 运行时模块 HEAVY mapping |

不得新增兼容 wrapper、旧字段双写、旧数据迁移脚本或平行业务路径。修改生产符号前先做 GitNexus upstream impact；提交前执行 staged detect-changes、对应 FAST、QUALITY 和 selector 选中的 HEAVY。

## 12. 回滚

未发布系统不做旧版本数据迁移。代码回滚即恢复代码；开发/测试库可清理重建。联调中如需暂停恢复，只停止 due-record 领取，不删除请求、Evidence、Transport、DeviceCommand 或 Binding；已发送请求继续等待原 callback。恢复后由 lease 过期和 `next_retry_at` 重新领取。

## 13. Out of Scope

- 修改 WMS、RCS、ECS 内部控制逻辑或供应商协议。
- WES 实现物理资源调度、占用判断或重复搬运保护。
- 创建空壳 PickingTask 绕过历史状态。
- 前端监控页面；本期提供 Service/API 可查询数据和告警。
- 旧版本兼容、旧数据迁移和兼容别名。

## 14. Effort Estimate

基础模型与状态机 1.5 天；dispatcher 2 天；三类可靠对象接入 2 天；attention/批次隔离 1 天；task/position 消费者收敛 1.5 天；手工拣选 completion/drain 1 天；测试和联调 2 天，总计约 11 天。
