# Phase 9—14 基础收敛与教学式插件开发重排设计

status: Approved
decision_date: 2026-08-26
scope: 开发流程、运输接入诊断、Phase 9—14 阶段边界、教学式插件开发和规划 worktree 收敛
implementation_status: backend planning baseline 已闭合于 `50bd9ac2098005b346d48b10ad9d78ac0ae5d982`；frontend planning baseline 已闭合于 `63489e7c89aa0fb758e7a08ea97a8000a3b843fc`；Gate A 已完成；Gate B 已分别合入 backend `41ab69bf`、frontend `e103b692`，未部署、未现场验收；Phase 9 计划已于 2026-08-28 批准，生产实施未开始

## 1. 决策摘要

后续交付不再把最小执行基础和人工 Bin 业务插件绑定在同一个 Phase 9 中。目标顺序调整为：

```text
开发流程优化形成统一执行规则
  → 运输接入诊断独立交付
  → Phase 9 最小执行基础闭合
  → 发布四目标账本静默门禁
  → Phase 10 旧平台原子删除
  → Phase 11 当前产品干净 Schema 基线
  → Phase 12 manual_bin_processing 教学式开发
  → Phase 13 automatic_putaway / automatic_picking
  → Phase 14 当前交付范围系统验收
```

Phase 9、Phase 10 和 Phase 11 可由 Agent 按获批计划自主实施。Phase 12 由用户亲自修改代码、测试、migration 和 Composition，
Agent 只负责教学说明、任务拆解、只读影响分析、Review 和诊断；除非用户对某个具体切片另行授权，Agent 不代写 Phase 12 生产代码。

`rough_sorter` 继续作为当前插件 SDK、静态 Composition、独立测试和部署激活的参考实现，但不能替代 `BinExecution`、人工任务、
RETURN_BUFFER、WMS 人工业务 wire 或未来插件的业务验收。

## 2. 为什么不能把完整 Phase 9 推迟

Phase 10 删除旧 Runtime、Intent、Outbox、Hold 和 Provider 路径前，必须已经存在目标内核和能承接当前生产消费者的最小 successor。至少包括：

- `BinExecution` 和唯一 `PositionProjection`；
- WorkLine unfinished-work target aggregate；
- `ESTOP_PRESSED` final event router；
- E03/E07 `WmsConfirmation` barrier；
- 最小 WMS target configuration；
- OpenTelemetry HTTP lifecycle owner 的明确处置。

`BinExecution` 与 `PositionProjection` 已由 SRS 和最小执行架构批准为核心对象，不是因人工/自动插件提前建设的预留 schema。Phase 9
必须交付其领域不变量、Repository/Service、测试 owner 和 HEAVY mapping；只有表或空模型不算完成。它们不要求
`manual_bin_processing`、`automatic_putaway` 或 `automatic_picking` 已有生产消费者。

如果把这些基础能力也推迟到 Phase 11 之后，Phase 10 会在目标内核不完整时删除旧路径；Phase 11 也会固化缺少已批准核心对象的
metadata。因此可推迟的是业务插件，不是已批准的最小执行内核。

## 3. 目标与非目标

### 3.1 目标

- 让 Phase 9 只承担 Phase 10 真正需要的最小基础闭合，不夹带人工或自动业务。
- 在 Phase 10 前完成运输诊断和四目标账本静默门禁，保护已有现场诊断与物理执行。
- 让 Phase 11 基线只包含已批准且已实现的最小执行内核和当时真实运行的业务模型，不为未来插件预留表、字段或 operation。
- 把 `manual_bin_processing` 变成用户可完整参与的二次开发教学样板。
- 形成唯一 backend/frontend `develop` 规划基线，避免不同 worktree 继续维护冲突真源。

### 3.2 非目标

- 不使用 `rough_sorter` 证明所有未来插件业务正确。
- 不把人工业务模型、RETURN_BUFFER 或 PDA/WMS 业务提前塞入 Phase 9 基础层。
- 不为 Phase 12/13 预建通用工作流、动态 registry、DSL、兼容层或空 schema。
- 不机械合并全部 worktree，也不直接合并已明显落后且被新设计取代的旧阶段分支。
- 不修改或归档 `docs/hardware/` 厂商原始资料。

## 4. 已排计划的定位

| 计划输入 | 当前定位 | 对阶段重排的约束 |
| --- | --- | --- |
| 开发流程优化 | 所有后续阶段的执行规则基线 | 风险匹配测试、文档不走代码式 TDD、单一 Review/QUALITY/HEAVY owner、证据按变更失效 |
| 运输接入诊断 | 已完成仓内实现、验证与合入；未部署、未现场验收 | Phase 10 必须把已交付的 Transport 查询、SSE、Nginx、前端页面和测试 owner 标记为 `RETAIN` |
| 旧 Phase 9 人工 Bin 计划 | 已拆分并归档，不再直接执行 | 基础任务由 Phase 9 Foundation 计划承接；人工业务任务由 Phase 12 教学计划承接；旧合同重新评审后才可成为当前真源 |
| Phase 10 旧路径清理 | 继续作为 target-only 原子切换计划 | 入口从“全部业务插件完成”改为“最小基础 successor、运输诊断 RETAIN、四表门禁和 `UNRESOLVED=0`” |
| Phase 11 Schema 基线 | 当前产品首个干净基线 | 不包含 manual/automatic 预留；Phase 12/13 使用正常向前 migration |

运输接入诊断计划和阶段重排设计已进入 `develop`。旧 Phase 9 中仍有效的内容已分别由
`plans/2026-08-27-phase9-minimum-execution-foundation.md` 和
`plans/2026-08-27-phase12-manual-bin-processing-guided-development.md` 承接，旧过程文档与未重新评审的合同已移至项目外归档。
运输诊断已完成仓内实现、验证与合入，但未部署、未执行现场物理动作或业务验收；Phase 9 计划已批准但尚未实施，
Phase 10–14 仍是后续计划，不得描述为已经实施。

## 5. 阶段设计

### 5.1 Gate A：开发流程优化

backend 和 frontend 的开发流程优化分别合入各自 `develop`，形成后续实施共同规则。两个仓库分别记录 baseline SHA；不伪造跨仓库
原子 Commit。

### 5.2 Gate B：运输接入诊断

运输诊断独立交付：

- 后端提供最近 Transport 任务、规范化详情和持久化结果查询；
- Transport SSE 只发送通知，数据库投影才是状态真源；
- 前端诊断页面复用通用认证 SSE transport；
- 同步闭合 Nginx、合同生成、FAST 和精确 HEAVY；
- 不增加来源过滤、周期轮询、Preflight 或第二套 callback-result API。

当前状态：backend `41ab69bf` 与 frontend `e103b692` 已完成上述仓内交付和各自验证；部署、真实 WMS/RCS/设备动作、供应商一致性与现场业务验收不属于本 Gate 的已完成证据。

Phase 10 Execution Lock 开始后，不允许再并行修改 Transport Composition、WMS Event route、SSE 或 Nginx。若 Gate B 尚未交付，
Phase 10 不得顺手创建诊断能力；若已经交付，则必须作为 target `RETAIN` consumer 进入影响清单。

### 5.3 Phase 9：Minimum Execution Foundation Closure

Phase 9 只交付：

- `BinExecution`；
- 唯一 `PositionProjection`；
- unfinished-work target aggregate；
- ESTOP final router；
- E03/E07 `WmsConfirmation` barrier；
- 最小 WMS target configuration；
- OpenTelemetry HTTP owner 裁决。

Phase 9 不实现 `manual_bin_processing`、RETURN_BUFFER、人工任务、INGRESS 计数、PDA/WMS 人工业务 wire、自动上架或自动拣货。
未被 Transport、Device、Execution、WMS Adapter 或 `rough_sorter` 当前真实消费的旧 operation 裁决为 `DELETE → NONE`，不为未来插件保留。

### 5.4 发布四目标账本静默门禁

`DeviceCommand`、`TransportTask`、`InboundEvidence` 和 `WmsConfirmation` 的发布静默门禁继续作为独立高风险切片，在首次 Phase 10
cutover 前完成 RED、DEV、GREEN、Review 和不可变 candidate 集成。门禁不读取 legacy 表，也不自动取消、重试、claim 或修复状态。

### 5.5 Phase 10：旧平台原子删除

Phase 10 的入口改为：

- Gate A、Gate B 和 Phase 9 已完成；
- 已交付的运输诊断能力登记为 `RETAIN`；
- 当前真实 target consumer 和测试 owner 已冻结；
- 所有 legacy owner 都是 `DELETE → successor`、`DELETE → NONE`、`RETAIN` 或 `schema-deferred`；
- `UNRESOLVED=0`；
- 四目标账本静默门禁已经进入不可变 candidate。

首次切换仍遵守：candidate ready、关闭 admission/Beat、旧 worker 排空、legacy stable zero、四表连续 `READY`、停止旧 worker、激活
target-only candidate、重开 admission。Phase 10 不删除 schema/revision，也不恢复任何旧平台作为未来插件后备路径。

### 5.6 Phase 11：当前产品首个干净 Schema 基线

Phase 11 基线只包含 Phase 9 已实现并验收的最小执行内核，以及当时真实运行的：

```text
最小执行基础
+ rough_sorter
+ Transport / DeviceCommand
+ InboundEvidence / WmsConfirmation
+ WMS Adapter
+ 当前部署、审计与必要基础设施模型
```

不包含 manual/automatic 插件表、占位字段或 operation。Phase 12/13 按正常产品节奏生成向前 migration，不再次重置初始基线。

### 5.7 Phase 12：manual_bin_processing 教学式开发

Phase 12 用一个真实人工 Bin 纵向切片教会用户完成后续插件和二次开发。用户亲自完成生产代码、测试、migration、Composition 和命令；
Agent 不直接代写生产代码。

每个任务采用固定教学闭环：

```text
Agent 任务卡与现有调用链讲解
  → 用户实现并运行最小验证
  → Agent 只读 Review 与根因诊断
  → 用户亲自修复
  → fresh Review
  → 用户说明 owner、数据流、失败语义和测试归属
  → 进入下一任务
```

高风险状态机、数据库、可靠性和并发切片使用 RED → DEV → GREEN；低风险修改复用现有测试或可靠替代验证；纯文档永远不走代码式
TDD。只有用户对某个具体切片明确要求“请直接修改”时，Agent 才能接管该切片。

### 5.8 Phase 13—14

Phase 13 按真实合同分别交付 `automatic_putaway` 和 `automatic_picking`，继续复用经过 Phase 12 验证的教学与插件边界。Phase 14 从
干净环境验证基础、Adapter、设备统一接口、各插件、部署和缺席门禁；供应商一致性、现场联调和业务验收继续保持独立证据。

## 6. rough_sorter 的证明边界

`rough_sorter` 可以证明：

- 插件包和 SDK 的基本边界；
- 静态 Composition 与 activation；
- 插件测试独立于核心默认 pytest；
- 类型化 Fact、Decision 和恢复事实；
- 插件制品和部署激活路径。

它不能证明：

- `BinExecution` 或人工/自动 Bin 生命周期；
- 人工 Task、RETURN_BUFFER、PDA/WMS 业务闭环；
- 未来插件 schema/migration；
- 所有设备角色、资源围栏或业务异常；
- 供应商和现场业务验收。

因此 Phase 9 可以依靠它验证“插件机制仍存在”，但 Phase 12/13 仍必须由各自业务插件拥有测试和验收。

## 7. 规划 worktree 收敛

规划阶段完成后形成新的 backend/frontend `develop` baseline，但合并的是获批成果，不是 worktree 本身。

### 7.1 当前现场原则

- backend 主工作区原有运输诊断 staged 文档已固定文件 hash、迁入当前规划分支并清理主工作区现场。
- backend 开发流程优化、运输诊断计划和阶段重排成果已在当前分支集中整理，完成审查后通过一个 PR 合入。
- frontend 开发流程优化分支独立同步、审查和合入。
- 旧 Phase 9 规划分支没有整体 rebase/cherry-pick；仍有效内容已经按新阶段语义提取到当前规划分支。

### 7.2 规划基线顺序

```text
冻结规划写入
  → 合入 backend/frontend 开发流程优化
  → 收敛并合入运输诊断设计与计划
  → 新建 Phase 9 Foundation 与 Phase 12 教学计划
  → 更新 master、SRS、索引、Phase 10/11 和相关业务合同
  → 归档被取代的旧 Phase 9 过程计划
  → 独立 Review 与文档相称验证
  → 记录 backend/frontend planning baseline SHA
```

后续实施 worktree 必须从对应 planning baseline 创建，且 Phase 10 Execution Lock 期间不允许其它 worktree 并行修改共享执行路径。

### 7.3 worktree 退休门禁

只有 worktree clean、成果已合入、与新 `develop` tree-equivalent 或全部唯一内容已有明确承接、无活动进程/容器且 ignored 产物已处置时，
才允许退休。不得用 reset、强制删除或“全部合并”处理不确定现场。

## 8. 文档生命周期

规划收敛必须同步更新 master、SRS、文件索引、文档生命周期索引、Phase 10/11 计划和 WMS 场景入口。旧 Phase 9 人工实施计划中的
基础任务迁入 Phase 9 Foundation 计划，人工业务任务迁入 Phase 12 教学计划；引用闭合后原过程计划移至
`../archive_docs/wes_backend/`，项目内不留副本、占位、软链接或转发页。

人工业务合同和 OpenAPI 是否继续保持已批准状态，要按 Phase 12 的真实教学范围重新审查；阶段推迟不等于合同自动失效，也不能把
未完成 Review 描述为实施真源。

## 9. 完成标准

本设计的规划实施只有同时满足以下条件才算完成：

1. backend/frontend 开发流程优化均已进入各自 `develop`；
2. 运输诊断设计与计划进入当前真源，原 staged 现场未丢失；
3. Phase 9 只包含最小基础 successor，Phase 12 明确为用户主导教学式插件开发；
4. Phase 10/11 入口、RETAIN、静默门禁和 schema 边界与新顺序一致；
5. master、SRS、索引和相关合同没有旧阶段双真源；
6. 被取代过程文档已归档，`docs/hardware/` 未修改；
7. backend/frontend planning baseline SHA 已记录；
8. 旧 worktree 仅在安全退休门禁满足后处理。
