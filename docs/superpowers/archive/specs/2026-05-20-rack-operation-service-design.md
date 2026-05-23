# 货架操作服务设计

日期：2026-05-20

## 背景

当前 SMT 粗分机工作线需要在物料 session 无法继续分配有效料格时，触发货架相关动作：可选地移出当前单层货架，并请求新的单层货架补入粗分机工作位。后续还会接入满箱交换、分拣机投料、分拣机出料等能力。

现有设计中，粗分机插件、货架任务、资源现状、满箱交换候选等职责容易互相渗透。为了让未发布系统尽快跑通，同时保留清晰演进路径，本次优化要建立一个低级、可复用的货架操作抽象，但不引入过重的策略引擎或复杂任务编排模型。

以下两个文档是后续任务分析材料，不纳入本次实现范围：

- `docs/superpowers/specs/2026-05-19-smt-sorter-inbound-plugin-spec.md`
- `docs/business/smt_sorter_inbound_workflow_guide.md`

## 目标

1. 让粗分机在无有效料格时，可以发起“当前工作位货架移出 + 新单层货架补入”的组合请求。
2. 新开工或当前工作位无货架时，只请求新单层货架补入，不要求移出动作。
3. 将货架运输、换面、分配并移入等低级操作沉淀为通用服务，供粗分机、满箱交换、后续分拣机插件复用。
4. 保持 `resource` 域只负责保存和查询现状，不承载 session 编排和业务策略。
5. 使用 `workline_rack_tasks` 记录货架任务生命周期、外部派发幂等和回调结果，不新增独立 operation 表。
6. 允许破坏性清理过期代码和数据模型，不保留向后兼容。

## 非目标

1. 不实现完整满箱交换业务闭环。
2. 不实现分拣机投料、出料 CTU 夹取闭环。
3. 不引入独立规则引擎、策略表或 operation 聚合表。
4. 不在 `resource` 域中保存 workline session、插件状态或业务编排状态。
5. 不兼容旧满箱交换插件、旧候选服务或旧任务模型。

## 第一性原理约束

系统需要区分三类事实：

1. 资源现状：货架在哪里、位置容量是多少、货架活动面是什么、料箱和料格里有什么。
2. 任务生命周期：系统向外部 WMS/RCS 请求了什么、是否下发、是否执行中、是否成功或失败。
3. 业务决策：某个 session 为什么需要补架、旧货架应该去哪里、新货架需要什么类型。

这三类事实不能混在一个服务里。混合后会导致粗分机、满箱交换、分拣机互相污染边界，也会让外部回调直接驱动业务状态，后续很难恢复和排错。

## 领域边界

### Resource 域

`resource` 域只保存和查询现状：

- 有哪些货架。
- 货架类型是什么。
- 货架当前在哪里。
- 货架当前活动面是什么。
- 货架上有哪些料箱。
- 料箱类型是什么。
- 每个料箱里有哪些料格。
- 料格类型是什么。
- 每个料格存储了哪些物料。

`resource` 域不决定粗分机下一步该做什么，也不保存 session 恢复状态。

### Workline 货架操作域

新增或重构后的货架操作服务只提供基础能力：接收上层已经拆好的低级货架 task 描述，并创建可追踪、可幂等、可回调的货架任务。

建议服务边界：

- `WorklineRackOperationService`：负责容量校验、任务创建、外部派发意图生成；不判断业务场景，不决定是否换架。
- `WorklineRackTaskLifecycleService`：负责任务生命周期记录，只处理 requested、dispatched、in_progress、succeeded、failed、timeout、cancelled 等状态变化。

命名重点：

- `Operation` 表达一次业务意图下的货架操作请求。
- `Task` 表达一次对外部系统的派发请求和生命周期记录。
- 第一阶段不新增 `workline_rack_operations` 表，operation 信息直接扩展在 `workline_rack_tasks` 中。

### 插件业务层

业务策略由插件自主处理。以 SMT 粗分机插件为例，插件承载以下决策：

- 粗分机 session 分配料格失败时，是否需要补架。
- 当前工作位货架是否需要移出。
- 旧货架目标角色是什么，例如满箱交换区、分拣机排队位、分拣机 station。
- 新货架需要什么货架类型和目标位置。
- 将业务决策拆成低级 `rack_tasks`，例如 `MOVE_RACK`、`ALLOCATE_AND_MOVE_RACK`。

插件不直接调用 WMS/RCS，也不直接写货架 task 表；插件只输出 runtime intent，runtime effect 调用货架操作域落库。

## 低级货架操作

通用货架操作服务第一阶段支持以下低级动作语义：

- `MOVE_RACK`：将指定货架从源位置移动到目标位置。
- `ALLOCATE_AND_MOVE_RACK`：由外部 WMS/RCS 分配符合条件的货架并移动到目标位置。
- `TURN_RACK_SIDE`：请求外部系统对指定货架执行换面。
- `MOVE_RACK_TO_POSITION_BY_POLICY`：由业务协调层给出目标角色，操作服务解析为具体可用位置。

这里的 policy 只表示上层传入的目标角色、货架类型和容量约束，不包含“满箱交换优先还是分拣机排队优先”这类业务优先级。业务优先级仍由插件决定。

这些动作不直接等同于 operation。一个 operation 可以包含多个 `workline_rack_tasks`，多个 task 使用同一个 `operation_key` 关联；一个 task 记录一次外部派发请求和对应生命周期。

例如粗分机场景中，插件应将“移出当前货架 + 请求新货架补入”拆成两个 task：`MOVE_RACK` 和 `ALLOCATE_AND_MOVE_RACK`，两者共享同一个 `operation_key`。货架操作服务只校验并创建这些 task。它们应在同一个事务内创建，并可以一并进入外部派发流程；系统不等待移出成功回调后才创建或下发补入任务。只有当 WES 必须在中间状态做新的业务决策时，才让后续 task 依赖前一个 task 的终态。

## 数据模型设计

### `workline_rack_positions`

位置应按容量建模。

核心字段：

- `workline_code`
- `position_code`
- `position_role`
- `capacity`
- `enabled`

语义：

- 精确工作位使用 `capacity=1`。
- 排队位、暂存区、满箱交换区可以使用 `capacity>1`。
- 不再用数据库唯一约束假设每个位置只能有一个货架。
- 容量可用性由服务结合资源现状和活动任务预约计算。

### `workline_rack_tasks`

`workline_rack_tasks` 是货架操作任务台账，负责记录外部请求和生命周期。

建议字段分组：

- 业务关联：`operation_key`、`operation_type`、`sequence_no`、`material_session_id`、`workline_id`、`workline_code`、`trace_id`。
- 任务身份：`task_key`、`task_type`、`task_status`、`parent_task_id`、`parent_task_key`。
- 货架与位置：`rack_code`、`rack_kind`、`source_position_code`、`target_position_code`、`target_position_role`。
- 外部派发：`dispatch_key`、`outbox_id`、`target_code`、`source_system`。
- 请求与结果：`actions_json`、`request_json`、`callback_json`、`result_json`。

第一阶段的 `operation_type` 可以先收敛为 `RACK_TRANSPORT`，后续再扩展。`task_type` 至少需要区分 `MOVE_RACK`、`ALLOCATE_AND_MOVE_RACK` 和 `TURN_RACK_SIDE`。

必要索引和约束：

- `task_key` 唯一。
- `dispatch_key` 唯一，用于外部下发幂等。
- `operation_key + sequence_no` 唯一，用于同一业务意图内的任务幂等。
- 按 `material_session_id + task_status` 查询活动任务。
- 按 `rack_code + task_status` 查询货架活动任务。
- 按 `target_position_code + task_status` 查询目标位置预约。

## 容量、并发与幂等

第一阶段不引入单独 reservation 表，直接用 `resource` 现状和 `workline_rack_tasks` 活动任务计算容量。

规则：

1. 目标位置在任务创建时即视为被预约，避免 AGV 长任务期间重复派发到单容量位置。
2. 当前占用来自 `resource` 现状，未来占用来自未终态 `workline_rack_tasks`。
3. 同一个 operation 内允许表达“源位置即将释放后补入新货架”的组合请求：如果 operation 内存在从位置 X 移出的 `MOVE_RACK` task，同一 operation 内目标为位置 X 的补入 task 做容量校验时，可以把该被移出的货架视为本 operation 的释放预约。
4. 释放预约只对同一个 `operation_key` 生效。其他 operation 仍然必须把位置 X 视为被当前货架占用，直到 `resource` 投影确认货架已移出。
5. 如果移出 task 失败、超时或进入人工阻断，同一 operation 内依赖该释放预约的补入 task 不得让 session 恢复；已经下发的补入任务必须进入对账或失败恢复路径，不能静默覆盖资源现状。
6. 不同 operation 不能同时预约同一个已满位置。
7. 同一个 `rack_code` 同一时间只能有一个未终态运输或换面任务。
8. 同一个 `material_session_id` 同一时间只允许一个活动货架 operation，避免 session 恢复顺序不可控。

幂等键：

- `operation_key`：业务意图幂等。
- `dispatch_key`：外部派发幂等。
- 外部任务号：用于回调匹配，不能替代内部幂等键。

## 回调与状态归属

外部回调入口只做三件事：

1. 根据 `dispatch_key` 或外部任务号找到 `workline_rack_tasks`。
2. 幂等更新任务状态和回调原文。
3. 将货架到达、移出、换面等事实交给 `resource` projection 更新现状。

回调不直接恢复 session。session 是否恢复，由 workline/session 协调逻辑在资源投影完成后判断。

同一 `operation_key` 下可以有多个 task。operation 不需要独立表，但其完成状态必须从同一 `operation_key` 下的 task 派生：

- 所有必需 task 成功，且 `resource` 投影确认目标位置状态可用后，operation 才可视为成功。
- 任一必需 task 失败、超时或进入人工阻断时，operation 不可视为成功，相关 session 不得自动恢复。
- 中间回调可以更新单个 task 状态和可信资源事实，但不能让整个 operation 提前成功。
- `sequence_no` 只用于同一 operation 内的幂等和可读排序，不表示必须等前一个 task 成功后才能下发后一个 task。

状态约束：

- 重复回调必须幂等。
- 乱序回调只能合法推进状态。
- `SUCCEEDED` 表示外部货架任务完成，不直接表示业务完成。
- 资源投影成功后，业务层才可以判断 session 是否继续。

## 粗分机场景流程

### 新开工或当前工作位无货架

1. 物料 session 请求分配料格。
2. 没有可用工作位货架或无有效料格。
3. SMT 业务协调服务发起 `ALLOCATE_AND_MOVE_RACK`。
4. 目标位置为粗分机单层货架工作位。
5. 任务成功回调后，`resource` 更新货架位置。
6. session 重新尝试分配新货架上的有效料格。

### 当前工作位有货架但无有效料格

1. 物料 session 请求分配料格失败。
2. SMT 业务协调服务确定旧货架目标角色。
3. 在同一个 `operation_key` 下创建两个 task：`MOVE_RACK` 当前货架到目标位置，`ALLOCATE_AND_MOVE_RACK` 新单层货架到粗分机工作位。
4. 两个 task 在同一个事务内创建，并可以一并进入外部派发流程，不等待移出成功后才创建补入 task。
5. 两个 task 均成功回调且 `resource` 更新旧货架和新货架现状后，operation 才可视为成功。
6. session 重新尝试分配料格。

当前工作位货架移出是可选动作。当前工作位没有货架时，不执行移出。

## 满箱交换和分拣机预留边界

本次设计为后续能力预留接口，但不实现完整业务闭环。

满箱交换后续应由独立业务协调服务承载，负责判断：

- 哪些单层货架料箱符合满箱定义。
- 五层货架工作位是否有可用货架。
- 活动面是否有空箱。
- 需要换面还是更换五层货架。

分拣机插件后续也应复用 `workline_sessions` 管理物料/料盘 session，并复用货架操作服务请求五层货架、排队位、station 移动。当前两个插件没有 CTU，不需要料箱 session。

## 失败恢复

第一阶段不做复杂自动补偿，只保证状态清晰、可重试、可人工处理。

- 下发失败：任务进入 `FAILED`，记录失败原因，session 不自动继续。
- 外部执行失败：任务进入 `FAILED`，`resource` 不盲目更新位置。
- 超时：任务进入 `TIMEOUT`，不推断货架最终位置。
- 重试：不复用已终态失败任务，新建 task，但保留业务上下文便于追踪。
- 人工修正：允许开发环境清理或修正资源现状与任务数据。

只有任务成功且资源投影确认目标位置状态可用后，session 才能重新进入料格分配。

## 过期代码清理

允许破坏性清理：

- 旧满箱交换插件。
- 旧满箱交换候选服务。
- 与旧满箱交换插件绑定的测试。
- 旧的 rack supply 硬编码回溯数量逻辑。
- 与 `capacity=1` 绑定的旧位置模型约束。

清理后不提供旧 API、旧插件注册或旧数据迁移兼容。

## 验收标准

1. 粗分机在无有效料格时，可以创建货架补充任务。
2. 当前工作位无货架时，只请求新单层货架补入。
3. 当前工作位有货架但无有效料格时，可以创建“移出旧货架 + 补入新货架”的组合请求。
4. 位置容量按 `capacity` 校验，活动任务会占用未来容量。
5. 同一 session 不会产生多个活动货架 operation。
6. 同一货架不会同时产生多个活动运输或换面任务。
7. 重复请求和重复回调不会产生重复活动任务。
8. `resource` 域只保存现状，不承载 session 编排职责。
9. 旧满箱交换插件和旧候选模型被移除。
10. 相关单元测试和目标回归测试通过。

## 验证方式

实现完成后至少执行：

- `rtk uv run ruff format <changed files>`
- `rtk uv run ruff check <changed files>`
- 货架任务服务相关单元测试。
- 粗分机插件补架流程相关测试。
- callback 编排相关测试。
- `rtk git diff --cached --check`
- 提交前执行 GitNexus `detect_changes`，确认影响范围符合预期。

## 风险

1. 位置容量如果只看资源现状，不看活动任务预约，会重复派发到单容量工作位。
2. 回调如果直接恢复 session，会把通用货架任务服务污染成业务编排服务。
3. 组合请求如果拆成多个等待回调的 task，会让 AGV 长任务下的工作线等待时间变长。
4. 如果 `resource` 域开始承载策略，后续满箱交换和分拣机插件会继续扩大边界。
5. 未发布系统允许破坏性清理，但测试夹具和迁移必须同步更新，否则会留下旧语义假阳性。
