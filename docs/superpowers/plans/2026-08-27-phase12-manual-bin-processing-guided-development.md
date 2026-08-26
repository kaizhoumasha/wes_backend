# Phase 12 `manual_bin_processing` 用户教学实施计划

status: Planned — starts after Phase 11
implementation_owner: 用户
agent_role: 任务讲解、只读影响分析、Review 与根因诊断
depends_on: Phase 11 单一空库 Schema 基线、人工业务合同重新评审

## 1. 教学目标

由用户亲自完成一个真实人工 Bin 纵向切片，完整掌握 WES 插件和二次开发的路径：领域对象、WMS wire、设备事实、Transport、
`DeviceCommand`、migration、静态 Composition、分层测试、部署激活和验收证据。

Agent 默认不代写 Phase 12 生产代码、测试、migration 或 Composition。每个任务采用固定闭环：

```text
Agent 讲解当前调用链并给出任务卡
  → 用户实现最小切片并运行验证
  → Agent 只读 Review 与根因诊断
  → 用户修复
  → Agent fresh Review
  → 用户说明 owner、数据流、事务边界、失败语义和测试归属
  → 进入下一任务
```

只有用户对某个具体任务明确要求“请直接修改”时，Agent 才能接管该任务；授权不自动扩展到后续任务。

## 2. 边界

### 2.1 必须复用

- Phase 9：`BinExecution`、唯一 `PositionProjection`、unfinished-work aggregate；
- Phase 6：`TransportTask` 与四个通用 Transport 方法；
- Phase 7：`DeviceCommand`、设备状态、统一 Command/Status/Result/Event 合同；
- 当前可靠性：`InboundEvidence`、`WmsConfirmation`、`LineRunEpoch`；
- Phase 8：`rough_sorter` 的插件 SDK、静态 Composition、独立测试和部署激活方式。

### 2.2 禁止新增

- 第二套 Transport、Device HTTP、evidence、confirmation、retry、outbox 或 callback API；
- 通用 Task 基类、工作流 DSL、动态 registry、Service Locator 或插件自动发现；
- 基础模块反向导入 `manual_bin_processing`；
- 兼容 alias、shim、双写、旧数据迁移或预留 Phase 13 schema；
- 用 `rough_sorter` 测试替代人工业务验收。

## 3. 开始前合同重审

PR #151 的旧 Phase 9 人工合同、设备附录、OpenAPI 和实施计划已作为历史输入归档，不是当前实施真源。Phase 12 开始前由用户、WMS、
WES、RCS/ECS 责任方重新确认真实教学范围，至少冻结：

- 人工任务来源、身份、生命周期和 WMS/PDA 所有权；
- SCAN1～SCAN4、不可读码、NG、人工工作位和 RETURN_BUFFER 的真实拓扑；
- WMS → WES 事件 operation、严格 DTO、幂等 identity 和持久化 ACK；
- WES → WMS confirmation、Transport submit/result 和失败收敛；
- DeviceCommand 的 task/event、参数、终态和现场安全边界；
- 哪些旧 operation 继续采用，哪些重新命名或删除。

重新评审后的当前合同和 OpenAPI 必须直接进入项目真源；不得从归档文件复制状态为 `Approved` 而跳过联合评审。

## 4. 教学任务

### Task 0：用户完成代码导航与 Owner 说明

Agent 讲解 `rough_sorter`、Execution、Transport、Device、WMS Adapter、Composition 和测试目录。用户需要独立回答：

1. 基础能力与人工业务分别位于哪里；
2. 一个 WMS Event 如何变成持久化 evidence；
3. 一个 DeviceCommand 如何下发并通过 Result/Event 收敛；
4. Transport 与 DeviceCommand 为什么不能互相替代；
5. 插件测试为什么不能访问数据库或进入核心默认 pytest。

退出条件：用户能画出数据流和依赖方向，Agent Review 无边界误解。

### Task 1：冻结人工业务合同和严格解析

用户根据联合评审结果编写当前 Markdown 合同、OpenAPI 和严格 DTO/operation parser。纯文档不走代码式 TDD；机器合同使用解析、引用、
operation 映射和既有 contract owner 的聚焦验证。

退出条件：合同明确 ownership、identity、ACK、错误、重放、状态终态和现场验收责任，没有内部旧 Runtime/Provider 语义。

### Task 2：建立最小人工 Task 业务模型

用户先写高风险 RED，锁定 Task identity、状态机、Epoch 归属、货架面顺序、重复事件和终态不变量；再生成随机 Alembic revision 并实现最小
Model/Repository/Service。

不得把任务字段塞入 `BinExecution`，不得建立通用 Task 基类或计划 DSL。开发/测试数据可清理，不编写旧数据迁移和 downgrade。

退出条件：单元、PostgreSQL 约束、migration 和测试所有权通过 Review。

### Task 3：建立人工业务模块与纯逻辑插件

用户创建依赖公开基础端口的业务应用模块，以及只依赖 Plugin SDK 的纯逻辑插件：

- 业务模块拥有事务、Task、可靠对象协调和窄 `ManualPolicyPort`；
- 插件只接收不可变 Fact/Snapshot，返回封闭 Decision；
- 业务模块不导入插件包，插件不导入 `src`、数据库、HTTP、Celery 或 Repository；
- Composition 用固定 Handler tuple 显式注入，不新增通用插件 runtime。

退出条件：import boundary、插件独立测试、业务聚焦测试和激活测试通过。

### Task 4：接入 WMS 事件与 confirmation

用户复用现有 WMS Event route、`InboundEvidence` 和 `WmsConfirmation`：

- 入站先持久化再 ACK；
- 重复 identity 幂等，冲突 fail closed；
- 业务推进只消费 claimable evidence；
- WES 出站 confirmation 保留原 identity，在拒绝、超时和歧义时进入既有收敛状态；
- 不接回旧 RuntimeInbox、SystemOutbox、Provider Profile 或 29-operation registry。

退出条件：合同 FAST、业务事务测试和真实 PostgreSQL owner 通过 Review。

### Task 5：实现扫描、人工工作位与设备命令

用户按当前设备合同逐个完成 SCAN1～SCAN4、不可读码、NG 和人工工作位切片。每个切片先锁定：

- evidence identity 与 `BinExecution` 创建/推进；
- `DeviceCommand` task/event 和 params；
- 命令接受、结果、设备事件和物理完成的分层状态；
- 未收到 ACK/Result 时保持原 command identity 并按现有 reconciliation 处理，不盲重发；
- WMS/PDA 不直接控制 ECS。

退出条件：Device 基础测试保持原 owner，人工业务只测试自己的映射和推进；现场物理验收仍单独记录。

### Task 6：实现 Transport、RETURN FIFO 与业务闭合

用户复用 `TransportTask` 和 Phase 9 `PositionProjection`，完成任务内搬运、RETURN_BUFFER 连续前缀、离场释放、WMS 记录和
`BinExecution` 关闭。需要重点解释：

- FIFO 为什么由位置事实而不是第二张队列表表达；
- Transport success、WMS `RECORDED|DUPLICATE` 和执行关闭为什么是不同门禁；
- `DELIVERY_UNKNOWN` 为什么不能创建新 identity 重发；
- STOP 为什么只阻止新业务，不改写已发送物理任务。

退出条件：事务、并发、幂等、Transport HEAVY 和跨任务边界场景通过。

### Task 7：静态 Composition、部署与验收

用户亲自完成模块导出、静态 Composition、Celery/queue、migration、配置、插件安装和部署激活，不增加动态发现。

最终验证按当前差异运行聚焦 FAST、插件独立测试、migration、QUALITY、selector 选中的 HEAVY 和必要的 Mock/E2E。供应商一致性、现场联调、
业务验收分别记录，不用 `/health`、本机 Mock 或页面可达性代替。

## 5. 每个任务的 Review 模板

Agent 的只读 Review 必须回答：

1. 变更是否越过 API → Service → Repository → Database；
2. 是否重复实现 Transport、Device、Evidence、Confirmation 或插件 runtime；
3. 状态机、事务、并发、幂等和 identity 是否有明确 owner；
4. 测试是否放在正确层级并复用既有 owner；
5. migration、HEAVY mapping、Composition 和 current docs 是否闭合；
6. 用户能否用自己的语言解释本任务的数据流和失败语义。

存在 P0～P2 或用户无法解释核心 owner 时，不进入下一任务。

## 6. 完成标准

Phase 12 只有同时满足以下条件才完成：

- 用户亲自完成并能解释全部生产切片；
- 人工业务合同、OpenAPI、代码、migration、Composition 和测试一致；
- 基础模块没有人工插件反向依赖；
- 没有第二套可靠性、动态 registry、兼容路径或 Phase 13 预留；
- 聚焦验证、QUALITY、必选 HEAVY 和唯一主 Review 有效；
- 真实供应商、现场和业务验收状态被如实区分；
- 完成后的过程计划按项目规则归档，当前合同和运行手册继续保留。
