# Phase 9 Minimum Execution Foundation 实施计划

status: ReviewRequired
owner: WES 基础执行能力
depends_on: Gate A 开发流程基线、Gate B 运输接入诊断、Phase 8 backend RC
blocks: Phase 10 Execution Lock、Phase 11 Schema 基线

## 1. 目标

只交付 Phase 10 删除旧平台前必须存在的最小执行内核和当前生产 successor：

- `BinExecution`；
- 唯一活动管辖期 `PositionProjection`；
- WorkLine unfinished-work target aggregate；
- `ESTOP_PRESSED` final router；
- E03/E07 `WmsConfirmation` barrier；
- 最小 WMS target configuration；
- OpenTelemetry HTTP owner 裁决；
- 当前 operation consumer 的 `RETAIN / SWITCH / DELETE → NONE` 清单。

本阶段不实现 `manual_bin_processing`、RETURN_BUFFER、人工任务、PDA/WMS 人工业务 wire、自动上架或自动拣货，不为 Phase 12/13
预建表、字段、operation、空包或 Composition。

## 2. 当前基线与复用边界

当前基础对象和待替换 owner：

| 当前事实 | Phase 9 处置 |
| --- | --- |
| `MaterialExecution` | 保留其物料执行语义；不得用它顶替 `BinExecution` |
| `TransportPositionProjection` | 直接收敛为唯一 `PositionProjection`，Transport 改为消费者 |
| `RuntimeInbox` unfinished 查询 | 切换为读取最终可靠对象的 target aggregate，不建立第二套聚合 |
| `WorkLineSafetyService.handle_estop()` | 作为最终 ESTOP 事务 owner，由设备事件 final router 调用 |
| `WmsConfirmation` | 承接 E03/E07 双义务、互斥、拒绝、歧义和推进 barrier |
| WMS Provider/Profile/29-operation registry | 仅保留当前真实 consumer；后置业务 operation 统一 `DELETE → NONE` |
| `RuntimeOpenTelemetryHttpExporter` | 决定保留并迁入唯一 Client 生命周期 owner，或完整删除 backend |

必须复用现有 `TransportTask`、`DeviceCommand`、`InboundEvidence`、`WmsConfirmation`、`LineRunEpoch`、Phase 2 HTTP factory、
typed WMS Adapter/Service 和现有 HEAVY selector，不新增 registry、workflow、outbox 或兼容层。

## 3. 开发与测试策略

本阶段跨模型、事务、状态机、Composition 和 migration，分类为大型/高风险，生产行为采用 RED → DEV → GREEN。计划、清单和当前态文档
不走代码式 TDD。

- 同一行为只有一个主要测试 owner。
- PostgreSQL 约束、锁序、事务和 migration 使用独立数据库验证。
- HEAVY 由 `docs/architecture/heavy-test-impact.toml` 精确选择，未知影响 fail closed。
- 每个切片只运行聚焦测试；最终候选快照再运行 QUALITY、selector 选中的 HEAVY 和 migration 验证。
- 不以 Phase 12 人工插件、旧 Runtime 测试或 `rough_sorter` 业务测试证明本阶段基础不变量。

## 4. 实施任务

### Task 0：冻结执行清单与影响范围

1. 从最新 `develop` 建立实施分支，记录 HEAD、dirty 指纹、数据库和验证环境。
2. 对下列生产符号批量执行 GitNexus upstream impact：
   `TransportPositionProjection`、`WorkLineRepository.get_unfinished_workload_summary()`、ESTOP 当前 route、
   `WmsConfirmationService`、WMS runtime factory 和 `RuntimeOpenTelemetryHttpExporter`。
3. 枚举直接/间接测试、fixture/helper、migration metadata、Composition、Celery、部署配置和 HEAVY mapping。
4. 形成每个消费者的 `RETAIN / SWITCH / DELETE → NONE / UNRESOLVED` 清单；`UNRESOLVED` 非零时停止。

退出证据：首个生产补丁前冻结完整调用点、测试 owner、migration/HEAVY owner 和无关 dirty 指纹。

### Task 1：建立 `BinExecution` 与唯一 `PositionProjection`

RED 锁定：

- 同一 `bin_id` 只能存在一个活动执行；
- 执行关闭原因封闭且单调；
- NG 原因只能首次设置；
- 位置更新必须引用可靠 evidence；
- 同一活动管辖期只有一个当前位置真源；
- RETURN 查询按本线 `positioned_at,id` 保持稳定连续顺序，但不实现 RETURN_BUFFER 业务。

DEV：新增 `BinExecution` 的 Model/Repository/Service；将 `TransportPositionProjection` 一次性直接替换为 `PositionProjection`，同步
迁移 Transport 调用者、导出、fixture、metadata 和 migration，不保留 alias、双表、回填或 downgrade。

GREEN：运行 execution/transport 聚焦 FAST、PostgreSQL 唯一约束和受影响 Transport HEAVY；扫描生产代码中旧投影符号零残留。

### Task 2：切换 WorkLine unfinished-work target aggregate

RED 锁定 START、STOP、deactivate 和查询在下列状态下不会漏报未完成工作：活动 `LineRunEpoch`、`MaterialExecution`、`BinExecution`、
`DeviceCommand`、`TransportTask` 和 `WmsConfirmation`。

DEV：在现有 WorkLine Repository/Service 边界内替换 RuntimeInbox 查询；不得创建新的通用 workload 表、缓存或兼容 fallback。

GREEN：运行 WorkLine START/状态投影、Execution、Device、Transport 和 WMS confirmation 的聚焦测试，并证明旧 RuntimeInbox 不再是
新 admission 的生产 owner。

### Task 3：闭合 `ESTOP_PRESSED` final router

RED 锁定设备事件持久化后必须调用保留的 `WorkLineSafetyService.handle_estop()`；已发送命令/搬运保持原 identity 收敛，不盲取消、重发或
改写结果；clear-estop 不自动恢复旧 Epoch 编排。

DEV：在当前设备事件 final routing/composition 中建立唯一调用路径，删除只记录 evidence 而不进入安全事务的旧分支。

GREEN：运行设备事件、WorkLine Safety、API 和部署装配测试，扫描 ESTOP 双路由和旧 Runtime side effect 零残留。

### Task 4：闭合 E03/E07 `WmsConfirmation` barrier

RED 锁定 `confirm_inbound` 与 `notify_pkg_binding` 的创建、互斥、响应冲突、拒绝、重试资格、歧义和 execution 推进只能由
`WmsConfirmation`/typed service 在原 execution identity 上处理。

DEV：补齐必要的 Epoch 关联、锁序和具体 follow-up owner；不得复用 RuntimeIntent、generic Hold、ReconciliationCase 或旧 Provider status lane。

GREEN：运行 `WmsConfirmationService`、execution decision、WMS Adapter、dispatcher 和 PostgreSQL 事务测试。

### Task 5：建立最小 WMS target configuration 与 OpenTelemetry 裁决

1. 以当前获批事实冻结 `WMS_BASE_URL`、Transport submit path、auth=`NONE` 和当前 typed consumers。
2. 复用唯一 WMS Client/Phase 2 HTTP factory；目标配置不读取 Provider Profile、Manifest、credential registry 或旧 effect/query lane。
3. 对同步 OpenTelemetry HTTP backend 作二选一裁决：迁入批准的唯一生命周期 owner，或同时删除 backend、配置、注册和测试。
4. 更新 API/worker/Beat/Compose/Jenkins 当前候选的 config digest 与 readiness，不保留双配置。

验证：运行 WMS Adapter、HTTP boundary、Composition 和部署配置聚焦测试；生产 raw HTTP Client constructor 满足现有边界门禁。

### Task 6：关闭 operation consumer 与 Phase 10 handoff

1. 枚举当前 operation 的真实生产 consumer；Transport submit、粗分确认和其它已交付能力指向具体 typed owner。
2. `manual_bin_processing`、自动上架和自动拣货等尚无当前消费者的 operation 裁决为 `DELETE → NONE`。
3. 更新 Phase 10 cleanup matrix 和 successor 清单，不新增 Phase 9 专属 registry。
4. 证明 `UNRESOLVED=0`，并冻结 Phase 10 使用的 target-only candidate 输入。

## 5. 最终验证

最终快照必须同时满足：

1. `BinExecution` 和 `PositionProjection` 有 Model、Repository、Service、领域测试、PostgreSQL 约束和精确 HEAVY owner；
2. unfinished-work、ESTOP、E03/E07、WMS target config 和 OpenTelemetry 均有唯一生产 owner；
3. `manual_bin_processing`、RETURN_BUFFER、人工/自动业务表和 operation 未提前进入生产代码或 migration；
4. 旧 projection、RuntimeInbox admission、双 ESTOP route 和旧 Provider consumer 不再承接新业务；
5. 聚焦 FAST、migration、QUALITY、staged selector 与必选 HEAVY 全部有效；
6. 唯一主 Review 零未关闭意见；
7. Phase 10 清单 `UNRESOLVED=0`，但 Phase 10 生产删除尚未提前执行。

满足以上条件后，才能把 Phase 9 标记为完成并开启 Phase 10 Execution Lock。
