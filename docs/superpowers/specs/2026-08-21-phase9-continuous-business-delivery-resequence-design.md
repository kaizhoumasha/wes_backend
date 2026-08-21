# Phase 9 后持续业务交付阶段重排设计

status: Approved
decision_date: 2026-08-21
scope: Phase 9 至最终验收的阶段边界、旧平台清理时机与数据库初始基线策略

## 1. 决策摘要

当前 Phase 9 不再一次性交付全部分拣业务插件。目标阶段按真实产品持续开发顺序线性重排为：

```text
Phase 8   粗分机后端 RC（已关闭）
   ↓
Phase 9   最小 Bin 执行基础与人工 Bin 业务纵向闭环
   ↓
Phase 10  旧 Runtime/通用平台生产路径原子删除
   ↓
Phase 11  首个干净产品 Schema 初始基线
   ↓
Phase 12  自动上架与自动拣货插件持续交付
   ↓
Phase 13  当前交付范围系统验收
```

不采用 `Phase 9.1 → Phase 10 → Phase 11 → Phase 9.2` 的倒序编号。Phase 11 之后的业务能力属于后续线性阶段，
以正常向前 Alembic revision 演进，不在全部插件完成后再次重置初始基线。

## 2. 目标与非目标

### 2.1 目标

- 先用一个真实、最小、可交付的人工 Bin 流转闭环验证目标执行基础。
- 在后续自动插件开发前删除旧通用 Runtime 热路径，避免新代码继续复制旧平台模式。
- 将未发布历史 migration 一次性收敛为首个干净产品基线，后续按正常产品节奏增加真实业务 revision。
- 严格区分可独立部署、运行和测试的基础能力，与只能依赖基础能力运行的业务插件。
- 保持代码、合同、测试和文档只有一个当前 owner，不保留兼容入口、别名、shim、双路径或旧数据迁移。
- 让后续初级开发人员能够按“基础对象 → 一个业务插件 → 一个明确合同”的顺序理解和扩展系统。

### 2.2 非目标

- 不把人工入库和人工出库拆成两个 WES 插件。
- 不让 WES 接管 PDA、物料子任务、库存事务、来源/目标储位和 WMS 业务完成裁决。
- 不为 Phase 12 预建通用工作流、动态注册表、能力平台或推测性扩展点。
- 不把 `BIN_EXCHANGE` 或“复杂出库”预先认定为独立插件。
- 不在本次阶段设计中修改生产代码、数据库或现有部署。
- 不清理或归档 `docs/hardware/` 下的厂商原始资料。

## 3. 不可突破的系统边界

### 3.1 基础能力

基础能力必须在没有具体业务插件时仍可安装、启动和独立测试。它只拥有执行可靠性和物理事实，不理解人工入库、人工出库、
自动上架或自动拣货业务语义。

本阶段涉及的基础对象至少包括：

- `LineRunEpoch`：冻结一次工作线运行的插件、配置、设备与拓扑版本。
- `BinExecution`：管理一个 Bin 在活动工作线中的物理执行管辖、资源围栏和闭合。
- `PositionProjection`：只表达有证据支持的当前位置；位置未知时不得推断或沿用旧执行投影。
- `TransportTask`：管理 WES 经 WMS/RCS 发起的搬运提交、接纳、最终结果和 `RECONCILING`。
- `DeviceCommand`：管理设备命令身份、ACK、CALLBACK、设备槽位和结果证据。
- `WmsConfirmation`：可靠提交 WES 已形成的业务事实，并区分 `RECORDED | DUPLICATE | WAIT` 等合同结果。

上述对象保持相互独立。`TransportTask` 不能替代 `DeviceCommand`，业务任务完成不能释放未闭合的物理执行，设备 ACK 不能替代
物理 CALLBACK。

### 3.2 业务能力

业务插件只能依赖公开的基础端口、最小 SDK 和封闭 Decision，禁止直接访问数据库、Repository、HTTP、Celery 或基础对象内部
状态机。插件包独立拥有自己的 fixture、单元测试、集成测试和 E2E，不进入核心默认 pytest、核心覆盖率或核心 HEAVY。

人工入库和人工出库在 WES 中统一使用 `manual_bin_processing`：

```text
WMS 发布 task 并冻结本批 Bin
→ WES 将 Bin 可靠送达人工工作位
→ 操作员在 WMS PDA 完成放入或拣出物料
→ WMS 持久化物料结果并下发 Bin 级释放决定
→ WES 将 Bin 送入 RETURN_BUFFER FIFO
→ WMS 分配精确回库位置
→ WES 通过 Transport 闭合 Bin 物理流转
```

插件不判断当前人工动作是入库还是出库。WMS/PDA 拥有物料动作、业务校验、库存事务和业务完成；WES 只拥有 Bin 的可靠位置、
缓存占用、搬运结果和物理清场义务。

## 4. Phase 9：最小 Bin 执行基础与人工业务闭环

### 4.1 入口条件

- Phase 8 后端 RC 已关闭；其供应商一致性和现场验收继续作为独立外部活动。
- 人工任务发布、Bin 批次、工作位到位、Bin 释放、回库位置分配和任务完成的 operation 与严格 DTO 已联合批准。
- 人工线设备角色、位置角色、缓存容量、两面货架、统一 NG 位置及 activation 配置已冻结。
- WMS/WES 的幂等、身份不匹配、跨任务 FIFO、容量背压、外部等待和清场用例已冻结。
- 停线排空所需共同货架面决定 wire 未获批时，对应路径继续保持 `ReviewRequired/BLOCKED`，不得由实现自行补齐。

### 4.2 实施顺序

Phase 9 内部按依赖顺序交付，但不再增加新的正式子阶段编号：

1. 先补齐并独立验证 `BinExecution`、活动执行期位置投影和资源围栏等最小基础能力。
2. 再实现只依赖公开基础端口的 `manual_bin_processing` 插件。
3. 最后完成显式 Composition Root、独立插件构建、真实 WES 加本机 WMS/ECS Mock 的纵向验收。

基础测试只证明对象、端口和可靠性不变量；插件测试只证明人工 Bin 业务。两者不得互相代证。

### 4.3 退出门禁

- 基础能力在未激活人工插件时可独立安装、启动和测试，不生成虚假业务任务。
- 人工插件不包含 PDA、物料、Cell、库存或机械臂业务模型，不创建机械臂 `DeviceCommand`。
- 同一 `task_id + bin_id` 不重复进站；Transport 最终成功和实扫身份匹配后才建立活动 Bin 管辖。
- 未收到 WMS Bin 级释放决定时不因超时自动退箱或送 NG。
- 正常释放 Bin 进入 Epoch 级 `RETURN_BUFFER` FIFO；原任务完成或取消不删除物理义务。
- 位置未知或 Transport 为 `RECONCILING` 时资源围栏不释放，不以新 identity 自动重发。
- WMS 业务完成、WES Bin 物理闭合和 Epoch 清场分别拥有明确完成条件。
- 直接被本阶段替代的旧 owner 已在同一原子切换中删除；跨阶段残余形成 Phase 10 精确清单。

## 5. Phase 10：旧平台生产路径原子删除

### 5.1 定位

Phase 10 不实现新业务，也不是清理垃圾代码的时间盒。它只删除已经拥有最终 successor 或明确 `NONE` 的旧生产 owner，并用机器
缺席门禁阻止 Phase 12 重新依赖旧平台。

### 5.2 入口条件

- Phase 9 的基础对象、人工插件、WMS ACL 和生产装配已经完成验收。
- 每个旧 owner 的全部直接与间接消费者已枚举，并分类为 `DELETE → successor`、`DELETE → NONE` 或合法 `RETAIN`。
- `DELETE → successor` 的最终测试 owner 已先通过；不存在未完成的原子交接。
- Phase 12 的插件架构已明确只能依赖最终基础端口，不需要保留旧 Runtime 作为推测性后备路径。

### 5.3 清理规则

重点审计 `RuntimeInbox`、`ExecutionSession`、`RuntimeIntent`、Effect、`SystemCapability`、`SystemOutbox`、`RuntimeHold`、
Recovery、Reconciliation、Reservation、旧 Provider Profile、裸 HTTP Client、重复连接池和无合同依据的认证 fallback。

清理按 owner 和消费者判断，禁止按 `Runtime`、`replay`、`hold`、`reconciliation` 等关键词批量删除。仍有当前明确职责的对象必须以
合法 `RETAIN` 记录 owner；不能为追求零命中而误删可靠性行为，也不能保留兼容 wrapper、转发 import 或 no-op consumer。

未来 Phase 12 若出现当前没有的真实基础需求，应以最小 TDD 切片扩展最终基础端口，不得恢复旧平台或提前建设通用框架。

### 5.4 退出门禁

- 生产代码、配置、Celery、脚本和部署只装配最终基础对象、WMS Adapter、设备统一接口和明确业务插件。
- 旧通用 Runtime 热路径、重复 HTTP 能力、旧配置键和无依据认证零活动引用。
- 每个删除测试都有 successor 或 `NONE` 理由；不新增读取人类文档正文的 pytest。
- 过期过程文档已移出项目目录，项目内不保留副本、占位、软链接或转发页。

## 6. Phase 11：首个干净产品 Schema 初始基线

### 6.1 语义调整

Phase 11 不再等待全部未来业务插件完成。它冻结的是：

```text
最终最小基础能力
+ Phase 8 rough_sorter
+ Phase 9 manual_bin_processing
+ 当时已批准且仍活动的 Adapter/部署模型
```

这是一条可持续开发的产品初始基线，不是“以后永远不再增加 revision”的最终 schema。

### 6.2 入口条件

- Phase 10 零旧生产路径门禁通过。
- 当时全部活动模型和 PostgreSQL/TimescaleDB 专有对象已独立冻结并评审。
- 没有旧表、旧字段或旧 revision 的活动消费者。
- 仅使用名称明确的隔离数据库；开发和测试数据允许清理重建。

### 6.3 实施与退出门禁

- 使用 Alembic generator 生成随机 revision ID，`down_revision = None`。
- 先由完整 schema manifest 验证旧 chain 建出的当前结构，再用同一 manifest 验证新初始基线。
- 删除未发布历史 revision 及只验证旧 revision 的测试；不保留数据转换、回填、桥接表、downgrade 或兼容 schema。
- 空 PostgreSQL/TimescaleDB 一次 `upgrade head` 成功，metadata、schema、约束、索引和专有对象一致。
- Phase 12 只新增正常向前 revision；全部插件完成后不再执行第二次初始基线重置。

## 7. Phase 12：自动业务插件持续交付

Phase 9—12 新增业务插件闭集为：

- `manual_bin_processing`
- `automatic_putaway`
- `automatic_picking`

Phase 8 已交付的 `rough_sorter` 继续保留，但不在 Phase 9—12 重复实现。

Phase 12 分别按已批准合同实现 `automatic_putaway` 和 `automatic_picking`，每个插件拥有独立包、fixture、测试、构建产物、
Composition Root 和部署 activation。两个插件可以复用稳定的基础端口和经过 Rule of Three 证明的小型技术 helper，但不得合并为
通用工作流、动态注册平台或业务 DSL。

`BIN_EXCHANGE` 是否形成独立插件，必须由独立激活条件、生命周期、部署组合和合同证明；在此之前只把它视为明确的交换执行与
`TransportTask` 协作。复杂出库优先作为 `automatic_picking` 的业务切片，除非真实合同证明它必须拥有独立插件生命周期。

每个 Phase 12 插件都以 Phase 11 基线为起点，通过正常向前 migration 交付自己的持久模型。不得修改 Phase 11 初始 revision，
不得为未交付插件预留空表、可空字段、占位 operation 或兼容路径。

## 8. Phase 13：当前范围系统验收

Phase 13 从空环境验证 Phase 11 初始基线及 Phase 12 后续 revision，分别证明：

- 基础能力独立安装、运行和测试；
- WMS Adapter、Transport、DeviceCommand 和统一 ECS 接口拥有唯一生产路径；
- 每个业务插件独立构建、装配、测试和验收；
- 供应商一致性、现场联调和业务验收保持独立证据，不由本机 Mock 或核心测试代替；
- 旧平台、重复 HTTP 能力、兼容 schema 和过期过程文档缺席；
- `docs/hardware/` 厂商原始资料完整保留。

## 9. 文档真源与归档策略

本设计获批后，后续实施计划必须完成一次文档真源收敛：

- 更新十二阶段总控为新的 Phase 9—13 线性顺序，并同步 README、文件索引和当前状态摘要。
- 裁决“自动、人工、满箱交换、复杂出库四插件”与 Phase 9—12 新增三插件模型的冲突；未经合同证明，不保留旧四插件表述。
- 保留仍表达当前业务所有权和流程的顶层 SPEC、SRS 与 ReviewRequired 合同，并更新其阶段引用。
- 将被本设计取代的旧 Phase 9 分组表述、旧 Phase 11 最终基线过程计划及其它过期过程文档完整移至
  `../archive_docs/wes_backend/`，不在项目内保留转发页。
- `docs/hardware/` 不参与归档或清理。

## 10. 实施计划前的批准门禁

进入实施计划前必须确认：

1. 本文状态为 `Approved`。
2. 人工流程所有权保持为 WMS/PDA 业务、WES Bin 物理执行，不拆成人工入库/出库两个插件。
3. Phase 11 后使用正常向前 migration，且不再二次重置初始基线。
4. Phase 12 当前只预批准 `automatic_putaway` 与 `automatic_picking` 两个后续插件方向；具体生产实施仍分别受合同、设备附录、
   拓扑和详细计划批准约束。
5. Phase 10 只能删除已有 successor/`NONE` 的旧 owner，不为未来插件预建基础能力。

本文只批准阶段架构和边界。它不批准人工 wire、自动上架、自动出库、满箱交换或复杂出库的生产实现。
