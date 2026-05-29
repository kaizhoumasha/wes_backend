<!-- /autoplan final-review restore point: /tmp/feature-plugin-refactoring-autoplan-final-restore-20260424-210946.md -->
<!-- /autoplan restore point: /tmp/feature-plugin-refactoring-autoplan-restore-20260424-204247.md -->

> Legacy notes: 本文记录 2026-04 阶段的旧插件状态机重构设想，已被 `RuntimeIntent` + Runtime 拓扑/Session 所有权方案取代；不要作为当前插件开发指南使用。当前开发入口是 `workline_material_flow_runtime.md`。

# WORKLINE 插件下一阶段重构计划

## 目标

把当前 `smt_classifier` 从“第一个能跑的插件”升级成后续 WORKLINE 业务模板插件的参考样板。

目标用户是后续开发新 WORKLINE 插件的工程师。这个工程师应该主要关注业务流程、业务键、设备角色、插件自有 `data` / `params` 模型，而不是重新理解 callback 包络、Session context、状态迁移、Outbox 命令投影、业务 NG 与系统异常的区别。

## 范围

下一阶段只处理 `smt_classifier` 和第二个插件真正需要的插件平台能力。

范围内：

- 执行 `docs/integration/third_party_integration_whitepaper.md` 定义的第三方 payload 两层结构。
- 扩展插件 registry metadata，让插件可以声明运行契约。
- 为 `smt_classifier` 增加显式状态机。
- 为 `smt_classifier` 增加类型化插件 context。
- 在 `WorkLine` 模型增加运行模式，并在开发/测试环境提供 sandbox 调试能力。
- 将 SMT 命令 `params` 构造收口到插件 contract helper。
- 在插件结果和 trace 中区分业务 NG 与系统异常。
- 在参考插件清理完成后，准备插件开发模板。

范围外：

- 通用流程 DSL。
- 可视化流程设计器。
- 插件市场或热加载。
- 完整 AGV/RCS 平台迁移。
- 一次性重写与插件运行无关的 callback/runtime 模块。

## 当前约束

- 第三方 `event` payload 是两层结构：顶层是协议控制字段，业务字段在 `data`。
- 第三方 `result` payload 是两层结构：顶层是协议控制字段，业务字段在 `data`，失败详情在 `error_detail`。
- WES 下发 command payload 是两层结构：顶层是协议控制字段，业务命令字段在 `params`。
- 插件负责一种 WORKLINE 业务模板，不负责某一条物理线实例。
- 新插件只在业务键、状态机、设备角色协作、业务决策模型显著不同时创建；供应商字段差异走 contract / adapter，物理线差异走 WorkLine config / device topology。
- 这是未生产发布的新系统，不考虑历史 payload、旧 session 或旧 mock 的向后兼容。
- 业务 NG 是正常业务结果，应记录为业务决策，Session 可以完成。
- 系统异常是执行或平台问题，应记录为 failure，并可能进入人工介入、超时、失败或重试。

## 建议 PR 顺序

### PR 0：修复运行态字段和 Registry 破窗

目标：先消除状态机落地前已经存在的结构性不一致，避免显式状态机变成第二套状态。

改动：

- 统一插件运行态字段为 `plugin_state`。
- 修复 `WorkLine.state_machine_class`，避免继续引用 registry 中不存在的 `definition.state_machine_class`。
- 引入统一 helper / accessor：runtime、plugin base、SMT 插件只通过该入口读写插件状态。
- `session.context_json.plugin_state` 是插件状态事实源；`session.status` 继续表示平台生命周期。
- `step_code` 如仍服务 trace/query/命令快照，必须变成 `plugin_state` 的投影字段或在迁移中改名，不能再由插件直接写。
- 清理 `stage` / `step_code` 新增写入点，并同步调整 runtime query / trace response / DeviceCommand 快照字段。
- 增加单元测试覆盖 state machine class 解析、插件状态读写、trace/query 字段投影。

预期结果：

- Runtime、SMT 插件和 registry 对“当前插件状态”只有一个事实来源。
- 后续 PR 可以在这个字段上实现状态机校验，而不是各自兜底。

### PR 1：严格协议包络边界

目标：把白皮书里的包络规则直接变成代码约束。

改动：

- `/callback/event` 顶层只允许 `device_code`、`event_type`、`timestamp`、`data`。
- `/callback/result` 顶层只允许 `command_code`、`device_code`、`result`、`finish_time`、`data`、`error_detail`。
- WES 下发 command payload 顶层只放协议字段，业务命令字段继续统一进入 `params`。
- 直接拒绝顶层拍平业务字段，例如 `PkgID`、`location`、`pkg_id`、`reel_diameter`、`actual_qty`。
- 使用 Pydantic `extra="forbid"` 或等价 validator，确保入口不会悄悄吞掉顶层业务字段。
- 同步更新 mock、e2e、fixtures，全部改为白皮书两层结构。
- 增加测试覆盖非法顶层业务字段拒绝、合法两层 payload 接收、错误信息可读。

预期结果：

- 业务 payload 在进入插件前结构一致。
- 新插件作者只需要记住一条规则：业务输入来自 `data`，命令输出进入 `params`。

### PR 1.5：第二插件薄 Spike / 压力用例设计

目标：在 manifest 和 runtime contract 落地前，用一个不同于 SMT 的 WORKLINE 场景反向约束平台原语，避免把 SMT 的偶然复杂度固化成通用能力。

改动：

- 选择第二个最小 WORKLINE 场景，但本阶段只做契约压力用例，不实现完整插件闭环。
- 定义第二插件的薄规格：
  - 业务键来源。
  - 事件输入 `data` 模型。
  - 命令输出 `params` 模型。
  - 必需设备角色和 role cardinality。
  - 至少一个等待回调。
  - 一个业务 NG。
  - 一个系统异常。
  - 一个 sandbox happy path 期望。
- 产出 fixtures 草案和“不得修改 runtime 的验收边界”。
- 如果薄 spike 需要 runtime 私有分支，先回到 PR2 / PR3 / PR4 / PR5 补平台能力，而不是在第二插件中硬编码。

预期结果：

- PR2 之后的 manifest、topology、business key resolver 不只由 SMT 推导。
- PR3 的业务结果分类和 PR5 的 sandbox 能被第二业务场景提前校验。
- PR7 仍保留完整第二插件实现，但不再等到平台全部完成后才发现抽象不适配。

### PR 2：增加最小 Manifest、设备拓扑推导和能力校验

目标：让插件运行要求可以从 registry 发现，并在绑定工作线前基于设备模型发现设备拓扑、设备能力和业务键归属问题。

改动：

- 定义 PR2 最小必填 manifest：
  - `plugin_key`
  - `contract_version`
  - `required_device_roles`
  - `business_key_resolver`
- `state_machine_class` 和 `context_model` 先作为 manifest 可选字段进入 registry schema；到 PR6 SMT 状态机和 context 落地后，再升级为生产插件必填。
- 增加最小 topology / capability contract，但不在 manifest 中重复定义物理上下游：
  - role cardinality：每种设备角色需要几个实例。
  - event source role：哪些角色允许产生哪些事件。
  - command target role：哪些命令可发给哪些角色。
  - capability constraints：设备需要具备的能力，例如扫码、测量、搬运、输送。
- 设备上下游关系以 `Device.upstream_device_id` 为事实源，由 runtime 推导 downstream，并据此校验插件声明的角色/事件/命令关系。
- 增加最小 `WorklineTopologyView` 运行时拓扑视图：
  - 视图归插件架构/runtime 所有，插件加载只读取 manifest，不持有数据库对象。
  - 视图内容包括 `devices_by_role`、`device_by_id`、`upstream_by_device_id`、`downstream_by_device_id` 和能力摘要。
  - PR2 只保证单次 workflow/session 内复用同一份视图，减少一次业务推进中的重复查询和重复推导。
  - 暂不新增拓扑版本、长 TTL 缓存和复杂主动失效机制；已有 `workline_device_cache` 可作为设备列表缓存基础。
  - 如果 PR7 第二插件或压测证明存在真实查询瓶颈，再把 `WorklineTopologyView` 升级为带版本/哈希的 `WorklineTopologySnapshot` 缓存。
  - 设备在线状态、维护态等高频运行态不混入拓扑视图；派发前仍做即时治理校验。
- 将后续增强字段暂时保持为可选能力：
  - `supported_events`
  - `supported_commands`
  - `validate_workline_topology`
  - `config_model`
- 区分三类信息：
  - immutable contract：插件身份、契约版本、状态机、context model。
  - topology requirements：设备角色、数量、事件来源角色、命令目标角色；具体上下游从 `Device.upstream_device_id` 推导。
  - runtime adapters：业务键解析、vendor result 分类、外部 intent 构造。
- 新系统不保留旧插件豁免；所有 WORKLINE 插件都必须提供最小 manifest。
- 将 `business_key_resolver` 接入 `SessionResolver`，替换 SixInOne 私有解析分支；PR1.5 薄 spike 必须证明非 SMT 业务键不需要修改通用 resolver。

预期结果：

- Runtime 可以在 workflow 运行前基于 `Device.upstream_device_id` 校验插件接线和设备能力。
- Runtime 可以复用同一份拓扑视图完成校验、目标设备解析和插件上下文构建，减少每次状态推进中的重复查询。
- 第二插件不需要往通用 SessionResolver 增加私有分支。
- Registry 不再只是 lazy import map，同时也不承担所有运行逻辑。

### PR 3：提前区分业务 NG 与系统异常

目标：让状态机、trace、看板和人工操作从一开始就区分正常业务拒收和系统故障。

改动：

- 新增最小 `BusinessDecisionIntent` 类型和 builder 方法，避免继续把业务证据塞进裸 dict。
- PR3 的最小持久化目标先收敛为 timeline / trace 投影，不新增完整 DecisionLog 表：
  - `classification`
  - `reason_code`
  - `message`
  - `evidence`
  - `business_key`
- 增加插件级 result classifier / contract normalizer，将供应商 result 归一化为：
  - `business_decision`
  - `hardware_failure`
  - `data_invalid`
  - `system_failure`
- 将扫码 NG 和检测 NG 记录为 business decision，而不是 failure。
- 保持 payload 非法、状态不匹配、超时、设备故障、急停为 failure。
- 对 SMT 合同明确：检测 NG 可以表达为 `result=SUCCESS` 且 `data.inspection_result=NG`；设备执行失败才表达为 `result=FAILED` 且带 `error_detail`。不要把这一供应商表达硬编码为全局规则。
- 增加 timeline / trace 查询验收：业务 NG 不计入系统 failure，但可按 reason_code 查询。

预期结果：

- 业务 NG Session 可以正常完成，同时保留决策证据。
- 系统故障指标不会被正常业务拒收污染。
- 后续 SMT 状态机不会把业务拒收固化成系统失败分支。

### PR 4：收口命令 Params、设备 Adapter 和外部协同边界

目标：让 `plugin.py` 专注业务决策，而不是拼协议 dict 或直接处理设备/外部系统细节。

改动：

- 将 SMT 命令业务参数构造移动到 `smt_classifier/contract.py` 或专门的 command contract 模块。
- Helper 只返回业务 `params`，不返回完整 command 包络；顶层 command 包络仍由 runtime/outbox 负责。
- 明确 `CommandIntent.parameters`、`DeviceCommand.params`、`Outbox.payload_json.params` 的边界：
  - `CommandIntent.parameters` 是插件输出的业务参数。
  - `DeviceCommand.params` 是设备命令业务参数快照。
  - `Outbox.payload_json` 是最终派发包络，业务参数必须位于 `payload_json.params`。
- 将 vendor 字段映射放进 contract / adapter，不让 WORKLINE 插件直接复制供应商字段名。
- 增加 helper：
  - `build_measurement_reel_params(pkg_id)`
  - `build_move_forward_params(pkg_id)`
  - `build_pick_scan_ng_params(...)`
  - `build_output_to_bin_params(...)`
- 为 helper 增加单元测试。
- 明确 runtime 修改点：
  - `_normalize_vendor_command_payload`
  - `_build_command_create_payload`
  - `_build_outbox_payload`
- 验收口径：`CommandIntent.parameters == DeviceCommand.params`；最终派发包络只在 `Outbox.payload_json.params` 放业务字段。
- 用内部 service abstraction 或 external request intent 替换 SMT 随机料箱分配。
- 明确边界：
  - 内部领域计算走 `ctx.services`。
  - 跨系统副作用走 Outbox / external intent。
- 插件内禁止直接 HTTP、Repository、SQL。
- 跨系统副作用通过 Outbox 派发。
- 外部回调回到 Inbox，并恢复同一个 Session。

预期结果：

- 插件代码读起来是业务流程。
- Contract / adapter 负责供应商字段名和别名。
- 命令协议包络不会散落到插件实现里。
- 插件代码可恢复、可重放。
- 外部系统通过和设备一致的编排路径参与流程。

### PR 5：增加 WorkLine 运行模式和最小 Sandbox Effect Adapter

目标：让插件开发和测试可以走完整派发链路，但派发目标是沙箱通道，而不是真实硬件或真实外部系统。

改动：

- 在 `WorkLine` 模型增加默认运行模式。WORKLINE 级运行模式只允许：
  - `AUTO`：真实自动运行，允许真实设备和外部副作用。
  - `MANUAL`：人工确认/人工介入模式。
  - `SIMULATION`：sandbox 模拟模式，派发到沙箱通道，由调试人员手工处理回调。
- 现有 `WorklineSession.RunMode` 中的 `REPLAY` 不应成为 WorkLine 运行模式；如果仍需保留 payload fixture 诊断，应放在插件级诊断工具里，而不是 Session/WorkLine runtime mode。
- Session 创建时从 `WorkLine.run_mode` 快照到 `WorklineSession.run_mode`，后续单个 Session 不受 WorkLine 配置变更影响。
- 增加一等环境配置，例如 `APP_ENV=dev|test|prod`，不要用 `APP_DEBUG` 推断是否生产。
- `SIMULATION` 只能在开发/测试环境开启；生产环境创建或启用 `SIMULATION` WorkLine 必须失败。
- Runtime 根据 `run_mode` 选择 effect adapter：
  - live adapter：写 Outbox 并真实派发。
  - sandbox adapter：写 Outbox / timeline，并派发到沙箱设备队列或沙箱外部请求队列。
- sandbox 应尽量少改变消息行为：DeviceCommand / Outbox 的业务 payload 不增加 sandbox 标志字段。
- 派发分流由运行环境和 `WorklineSession.run_mode` 决定，而不是由消息 payload 决定。
- 增加沙箱待处理查询能力，让调试人员看到待处理设备命令/外部请求、payload、wait token、期望 callback schema。
- 沙箱派发结果等待调试人员通过 callback API 手工回传，回传仍进入 Inbox 并恢复同一个 Session。
- 手工 callback 走调试接口或调试身份认证，操作者从 auth 上下文记录，不要求设备消息 payload 增加 sandbox 标志。
- timeline / trace 可从 `run_mode` 和操作者上下文展示沙箱调试信息，不要求消息数据本身做标志区分。
- 插件代码不得直接判断环境变量或开发环境；只通过 `ctx.run_mode` / runtime capability 感知当前运行能力。

预期结果：

- 插件开发可以用同一套 runtime 流程调试。
- sandbox 会真实产生“待处理指令/外部请求”，消息结构尽量等同 live，只是派发出口换成沙箱工作台。
- sandbox 行为由平台控制，不散落在插件实现里。
- 测试环境可以安全验证命令、外部协同、业务 NG 和系统异常分流。

### PR 6：增加 SMT 显式状态机和类型化 Context

目标：移除通过裸 `context_json` key 和分散 `@step` 检查形成的隐式状态机。

改动：

- 新增 `src/workline_plugins/smt_classifier/state_machine.py`。
- 新增 `src/workline_plugins/smt_classifier/context.py`。
- 定义 SMT 状态和迁移：
  - 状态：`IDLE`、`WAITING_MEASUREMENT`、`WAITING_CONVEYOR`、`WAITING_OUTPUT`、`WAITING_PICK_PLACE`、`MANUAL_HOLD`、`COMPLETED`、`ERROR`
  - 迁移先以当前插件实际触发器为准：`scan_ok`、`scan_ng`、`measurement_ng`、`pick_ok`、`pick_ng`、`inspection_ng`、`conveyor_ok`、`output_ok`、`manual_hold`、`timeout`、`fail`
- 如需把 `pick_ok` 改名为 `measurement_ok` 或其他更准确名称，必须在 PR6 中提供旧触发器到新触发器的迁移表和测试，不能让 transition validator 上线后直接拒绝现有路径。
- 定义 `SmtClassifierContext`，包含：
  - `plugin_state`
  - `barcode` / `pkg_id`
  - `barcodes`
  - `location`
  - `device_code`
  - `reel_diameter`
  - `reel_thickness`
  - `bin_location`
  - `ng_reason`
  - `manual_hold_reason_code`
  - `manual_hold_reason_message`
- 替换 `plugin.py` 中关键业务字段的裸 `ctx.session.context_json.get(...)` 读取。
- 增加插件级诊断入口，输入单条 Inbox payload，输出 normalized input、parsed context、selected handler、`PluginResult`。该入口只用于插件 handler / context / 状态机诊断，不作为 WORKLINE 级调试方案。

预期结果：

- 非法迁移由 runtime 捕获。
- 插件 context 结构显式、可测试。
- 插件级诊断可以反向验证状态机、context 和 handler 选择是否可解释。

### PR 7：第二个最小 WORKLINE 插件 Spike

目标：验证平台契约是否真的能支撑“每扩一个 WORKLINE，只新增插件目录，不改 runtime”。

改动：

- 选择一个最小但不同于 SMT 的 WORKLINE 场景，新增第二个插件 spike。
- 只允许新增：
  - `plugin.py`
  - `contract.py`
  - `context.py`
  - `state_machine.py`
  - tests 和 fixtures
- 禁止为第二插件修改 callback、orchestrator、outbox、dispatcher、session resolver。
- 如果必须改 runtime，必须回到前置 PR 补成平台能力，而不是给第二插件开私有分支。
- 第二插件必须覆盖至少一个事件、一个命令、一个等待回调、一个业务 NG、一个系统异常、一个 sandbox happy path。

预期结果：

- 平台抽象不再只由 SMT 推导。
- 模板最终基于两个插件的交集沉淀，而不是复制 SMT 的偶然复杂度。

### PR 8：基于两个插件沉淀模板和 Sandbox 调试指南

目标：降低第二个 WORKLINE 插件开发成本。

改动：

- 基于 SMT 和第二个最小插件的共同结构，增加插件骨架生成器或文档模板：
  - `plugin.py`
  - `contract.py`
  - `context.py`
  - `state_machine.py`
  - tests 和 fixtures
- 增加 sandbox 调试指南和 fixtures，覆盖“事件输入 → 沙箱派发 → 调试人员手工 callback → Session 推进”的完整流程。
- 可保留插件级 payload fixture / handler 诊断工具，但明确它不用于 WORKLINE 级业务闭环调试。
- 更新 `docs/plugin_development_guide.md`，用中文对齐 `data` / `params`、manifest、context、state machine、business decision / failure。

预期结果：

- 新插件开发从已测试结构开始。
- 新插件可以在没有真实硬件的情况下，通过 sandbox 完整调试 WORKLINE 业务链路。

## 测试策略

必须覆盖的测试层：

- Registry 和运行态字段测试，证明 `WorkLine.state_machine_class` 可解析，统一状态字段可读写，trace/query 不再依赖插件直接写 `step_code`。
- Manifest topology / capability 校验测试，证明设备角色、事件来源、命令目标、能力约束可在绑定前失败。
- 第二插件薄 spike fixture 测试，证明非 SMT 业务键、事件 `data`、命令 `params`、业务 NG、系统异常不会要求修改 callback/runtime/outbox。
- 第二个最小插件实现测试，证明新增 WORKLINE 插件不需要修改 callback/runtime/outbox。
- Callback API 顶层包络 allowlist 测试。
- Callback API 严格拒绝顶层业务字段测试。
- Runtime 插件 manifest 解析和状态机加载测试。
- BusinessDecisionIntent builder、result classifier、timeline 投影和查询测试。
- WorkLine `run_mode` 配置、开发/测试环境门禁、Session 快照、sandbox effect adapter 测试。
- Sandbox 派发测试，证明命令 payload 不增加 sandbox 标志，仍进入沙箱队列，并可由调试人员手工 callback 推进同一个 Session。
- SMT context 解析和命令 params helper 单元测试。
- SMT 成功流、扫码 NG、检测 NG、人工介入、payload 非法、超时集成测试。
- Outbox 投影测试，证明命令业务字段只在 `params`。
- 插件级诊断测试，证明单条 payload 可解释 handler / context / `PluginResult`；sandbox 测试证明只派发到沙箱通道。
- Trace/timeline 测试，证明业务 NG 和系统异常可区分。

## 成功标准

- `smt_classifier` 拥有显式状态机和类型化 context。
- 第二个最小 WORKLINE 插件可以只新增插件目录、tests 和 fixtures，不修改 runtime。
- Callback ingress 直接拒绝拍平业务字段。
- Command outbox payload 保持白皮书包络结构。
- WorkLine `SIMULATION` 模式只能在开发/测试环境开启；消息 payload 不增加 sandbox 标志；派发到沙箱通道后由调试人员手工回调，并能推进同一个 Session。
- 业务 NG 表达为 typed business decision evidence，而不是系统 failure。
- `WorkLine.state_machine_class` 可以从插件 metadata 正确解析，且 `state_machine_class` / `context_model` 在 PR6 后成为生产插件必填。
- 下一个插件可以从清晰模板开始，不需要复制 SMT 私有 helper，也不需要真实硬件就能在 sandbox 跑通第一个 WORKLINE happy path。

---

## /autoplan 评审报告

### Intake

- Base branch: `develop`
- 当前分支: `feature/plugin-refactoring`
- UI 范围: 否，跳过设计评审
- DX 范围: 是，插件平台和开发模板直接影响后续插件开发者体验
- Restore point: `/tmp/feature-plugin-refactoring-autoplan-restore-20260424-202049.md`

### 已确认前提

用户已确认以下前提：

1. 插件对应 WORKLINE 业务模板，而不是物理线实例。
2. 先把 SMT 做成参考插件，再开发第二个插件。
3. 白皮书两层 payload 结构应成为 runtime 约束。
4. 业务 NG 与系统异常必须区分。
5. 显式状态机和类型化 context 是架构要求，不是可选优化。
6. 下一阶段不做 DSL、可视化流程设计器、热加载、插件市场。

## Phase 1：CEO Review

### 0A. 前提挑战

| 前提 | 评估 | 风险 | 决策 |
|------|------|------|------|
| SMT 是参考插件 | 成立，但必须先清理试验期遗留写法 | 如果样板不干净，第二个插件会复制坏模式 | 保留 |
| Callback 顶层字段应严格 allowlist | 成立，且系统未发布，应现在锁定正式契约 | 入口如果不强约束，插件作者会继续承担协议清洗成本 | 直接严格拒绝 |
| 每个插件必须有状态机 | 成立 | 如果只要求 SMT，有新插件继续写隐式状态机会回到原点 | Manifest 中标记必填 |
| 类型化 context 是必要条件 | 成立 | 如果只给 SMT 加 context，runtime 不解析/校验，收益有限 | 增加 runtime context model hook |
| 业务 NG 与系统异常区分 | 成立 | 如果只改插件不改 trace/decision schema，报表仍无法区分 | PR 3 需要包含 trace/timeline 查询口径 |

### 0B. 现有代码可复用能力

| 子问题 | 已有能力 | 差距 |
|--------|----------|------|
| 插件路由 | `src/workline_runtime/plugin_base.py` 支持 `@on_event`、`@on_command`、typed handler | 状态机仍偏弱，context 未类型化 |
| 标准化输入 | `src/workline_runtime/plugin_sdk/normalizers/input_normalizer.py` 已产出 `NormalizedDeviceEvent` / `NormalizedCommandResult` | callback 入口没有严格 forbid extra |
| 命令 `params` 包络 | `src/celery_app/tasks/workline.py::_normalize_vendor_command_payload` 已把业务字段收口到 `params` | 插件 API 仍叫 `parameters`，容易让作者误解 |
| 插件 registry | `src/workline_plugin_registry.py` 可解析 plugin class 和 contract_version | `WorkLine.state_machine_class` 目前引用不存在的 `definition.state_machine_class` |
| 拓扑解析 | `Device.upstream_device_id` 是设备上下游事实源，`src/workline_runtime/device_target_resolver.py` 可按 scope/role 推导下游目标设备 | manifest 还不能声明角色、事件来源、命令目标、能力要求 |
| SMT 协议模型 | `src/workline_plugins/smt_classifier/contract.py` 已有 event/result data 模型 | command params builder 还散落在 `plugin.py` |

### 0C. Dream State Delta

```text
当前状态
  第一个插件能跑
  但业务流程、context、命令 params、NG/异常边界仍混在插件实现里

下一阶段计划
  SMT 成为参考插件
  Runtime 提供 manifest、状态机入口、payload 包络约束、typed context hook

12 个月理想状态
  新 WORKLINE 插件由模板生成
  开发者只写 contract/context/state_machine/plugin/tests
  sandbox 可完整调试 WORKLINE 业务闭环
  插件级诊断工具只用于定位 handler/context/状态机问题
  业务 NG、系统异常、外部协同都可追踪和恢复
```

### 0C-bis. 方案对比

| 方案 | 内容 | 优点 | 缺点 | 决策 |
|------|------|------|------|------|
| A. 只重构 SMT | 只把 SMT 拆成 state/context/contract | 风险低，短期快 | 第二个插件仍会踩 runtime 坑 | 拒绝 |
| B. SMT 样板 + 最小 runtime hooks | 增加 manifest、state_machine、context_model、payload 边界 | 最符合“第二个插件”目标 | 需要触碰 runtime 和 callback | 采用 |
| C. 建通用流程平台 | DSL、设计器、热加载、统一模板全做 | 长期想象空间大 | 当前阶段过度扩张，难验证 | 拒绝 |

### 0D. 范围决策

- 调整为 PR0、PR1、PR1.5、PR2 到 PR8 的分阶段结构。
- PR 1 改为“严格协议包络边界”，直接拒绝顶层拍平业务字段。
- PR 1.5 增加第二插件薄 spike / 压力用例设计，用来约束 PR2 之后的平台原语。
- PR 2 应提前定义 manifest 的 PR2 最小必填集合：`plugin_key`、`contract_version`、`required_device_roles`、`business_key_resolver`；`state_machine_class` 和 `context_model` 到 PR6 再升级为生产插件必填。
- PR 3 先落 typed business decision，避免 SMT 状态机把业务 NG 固化成系统失败。
- PR 5 提供 WorkLine 默认运行模式和 sandbox effect adapter；PR 6 提供 SMT state/context 和插件级诊断；PR 8 完善 sandbox 调试指南和模板。

### 0E. 时间审问

| 时间点 | 应发生什么 | 风险 |
|--------|------------|------|
| 第 1 小时 | 入口包络测试、状态字段和 registry 缺口明确 | 如果测试仍按旧拍平 payload 写，需要同步改为白皮书结构 |
| 第 1 天 | PR 0/1 能独立落地 | 如果 manifest 设计过大，会拖慢后续 |
| 第 1 周 | SMT 状态机和 context 可通过现有集成测试 | 如果状态机一次性过度抽象，会影响业务闭环 |
| 第 1 月 | 第二个插件能从模板启动，并能在 sandbox 下跑通 happy path | 如果没有 sandbox 派发和手工 callback，开发者仍依赖硬件联调 |
| 第 6 月 | 多插件共享同一 runtime 约束 | 如果业务 NG 和系统异常未统一，运营报表会失真 |

### CEO Findings

| Severity | Finding | Fix |
|----------|---------|-----|
| High | PR 1 如果不直接严格拒绝顶层业务字段，会把协议清洗成本推给每个插件 | 入口 Pydantic / validator 直接 forbid extra，并提供清晰错误 |
| High | Manifest 计划列出的字段过多，但没有区分 MVP 必填与后续增强 | 明确最小 manifest：`plugin_key`、`contract_version`、`state_machine_class`、`context_model`、`required_device_roles` |
| Medium | PR 6 如果只说替换“风险最高”的裸 context 读取，完成标准不够硬 | 要求 `smt_classifier/plugin.py` 不再直接写关键业务字段的裸 dict key，统一通过 context model helper |
| Medium | PR 5 如果只加 `run_mode`，不做沙箱派发队列和手工 callback 闭环，调试人员仍无法验证“指令下发 → 手工回调 → Session 推进” | 在 WorkLine 上增加 `run_mode`，Session 快照，runtime 按 mode 派发到 live/sandbox adapter |
| Medium | PR 3 如果只加 decisions，不改 trace/timeline 查询口径，业务 NG 仍不容易被运营识别 | 加入 trace/timeline 展示与查询验收：业务 NG 不计入 failure，但可按 reason_code 查询 |
| Medium | PR 4 “service abstraction 或 external request intent”二选一不清楚 | 区分内部领域计算走 `ctx.services`，跨系统副作用走 Outbox/external intent |

### CEO 双声音共识表

| 维度 | Primary Review | Codex | 共识 |
|------|----------------|-------|------|
| 前提有效？ | 有效，PR 1 可以直接严格收紧 | 有效，但必须先修状态字段和 registry 破窗 | 成立，新增 PR 0 |
| 是否解决正确问题？ | 是，目标用户是后续插件开发者 | 不能只做 SMT 样板，必须定义最小可复制契约 | 目标改为“SMT 样板 + 最小平台契约” |
| 范围是否正确？ | 正确，但 manifest MVP 要收窄 | Manifest 需要拆 contract、topology、runtime adapters | 收窄 MVP，避免万能 registry |
| 替代方案是否充分？ | 需要保留“不做 DSL”的明确拒绝 | 还需要第二插件压力用例 | 保留三方案对比，并要求 PR 前补第二插件压力用例 |
| 6 个月风险是否覆盖？ | 部分覆盖，需补 trace 查询和 sandbox 闭环 | 还缺业务 decision 类型、外部协同边界和沙箱调试闭环 | 将 sandbox、typed decision、external intent 纳入验收 |

### CEO 阶段结论

计划方向成立，采用“SMT 样板 + 最小 runtime hooks”的路线。计划已调整为 PR 0 到 PR 8：先修状态字段和 registry，再严格执行协议包络，随后落 manifest、基于 `Device.upstream_device_id` 的拓扑校验、typed business decision、命令/外部协同边界、sandbox、SMT 状态机、第二插件 spike 和模板。

## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|----------------|-----------|-----------|----------|
| 1 | CEO | 保留“SMT 样板 + 最小 runtime hooks”路线 | Mechanical | P1/P5 | 能覆盖第二个插件真实开发痛点，又避免通用流程平台过度扩张 | 只重构 SMT、直接建设 DSL |
| 2 | CEO | PR 1 改为严格协议包络边界 | Mechanical | P1/P3 | 系统未发布，入口越早强约束，后续插件心智负担越低 | 告警观察模式 |
| 3 | CEO | Manifest MVP 先收窄为运行必需字段 | Mechanical | P5 | 先解决状态机、context、设备角色校验，避免 registry 设计膨胀 | 一次性实现全部 metadata |
| 4 | CEO | 新增 PR 0 修复状态字段和 registry 破窗 | Mechanical | P0/P1 | 当前 runtime 读取 `stage`，SMT 写 `step_code`，且 `state_machine_class` 字段不存在；不先修会污染后续状态机设计 | 直接进入 SMT 状态机重构 |
| 5 | CEO | `decisions` 改为 typed business decision | Mechanical | P1/P3 | 裸 dict 无法支撑业务 NG 证据、timeline 投影和查询口径 | 继续约定 dict 结构 |
| 6 | Eng/DX | 增加 WorkLine 默认运行模式和 sandbox effect adapter | Mechanical | P1/P3 | WORKLINE 调试需要覆盖派发和回调闭环，但不能触发真实硬件或外部副作用 | 只做插件级诊断 |

## Office-hour 辅助决策

### 强制问题

1. 这是不是正确问题？
   - 是。真正问题不是“让 SMT 更干净”，而是“让每扩一个 WORKLINE 时，插件作者只面对业务契约，不重复理解 runtime 隐性规则”。
2. 如果不做会怎样？
   - 第二个插件会复制 SMT 当前的偶然复杂度：裸 context key、命令 params 拼装、NG/failure 混淆、外部协同临时逻辑。
3. 当前代码已经部分解决了什么？
   - `@on_event` / `@on_command` handler 选择、input normalizer、Outbox 命令投影、设备目标解析、SMT contract data 模型已经可复用。
4. 最窄切入点是什么？
   - 不是直接做模板生成器，而是 PR 0 到 PR 5：修状态字段、收紧包络、manifest MVP、业务决策、命令边界、WorkLine run_mode/sandbox。
5. 哪个方案 6 个月后最不后悔？
   - “SMT 样板 + 最小 runtime hooks”。它会留下平台契约，不会过早进入 DSL/设计器。

### 方案选择

| 方案 | Effort | Risk | 适用场景 | 结论 |
|------|--------|------|----------|------|
| A. 只重构 SMT | M | Low | 只需要当前线跑得更清楚 | 不选，不能降低第二插件心智负担 |
| B. SMT 样板 + 最小 runtime hooks | L | Medium | 下一阶段每扩一条 WORKLINE 都要写插件 | 采用 |
| C. 通用流程平台 / DSL | XL | High | 多业务线流程已高度抽象稳定 | 不选，当前证据不足 |

决策：采用 B。A 不解决平台复制问题，C 超出当前证据；B 能把白皮书协议、状态机、context、业务 NG、外部协同边界落成可验证契约。

## Phase 2：设计评审

本阶段无 UI 改动，跳过视觉设计评审。

## Phase 3：工程评审

### 目标架构

```text
/callback/event,result
  -> Callback envelope validator
  -> WorklineInbox
  -> SessionResolver / business_key_resolver
  -> Orchestrator
     -> PluginRegistry / Manifest
     -> ContextModel parser
     -> WorklinePlugin handler
     -> StateMachine validator
     -> PluginResult(commands, business_decisions, wait, failure)
  -> Effect applier
     -> run_mode adapter: live / sandbox
     -> Session / Timeline / Decision
     -> DeviceCommand / SystemOutbox
  -> OutboxDispatchService
  -> Device or External System
  -> callback/result or external callback
```

### 工程问题

| Severity | Finding | Fix |
|----------|---------|-----|
| High | 状态字段存在事实不一致：runtime 有状态机时读取 `context_json['stage']`，SMT 当前写 `step_code` | PR 0 统一为 `plugin_state` 并删除分散写法 |
| High | `WorkLine.state_machine_class` 读取 registry 中不存在的字段 | PR 0 修复 registry definition 或插件 metadata 解析路径 |
| High | callback 入口当前只做最小包络校验，无法阻止业务字段拍平到顶层 | PR 1 直接 forbid extra，非法 payload 在入口失败 |
| High | `PluginResult.decisions` 是 `list[dict]`，不能承载业务 NG 的稳定语义 | PR 3 新增 `BusinessDecisionIntent`、builder API、timeline 投影 |
| Medium | Manifest 字段过多会模糊 contract、topology、runtime adapter 生命周期 | PR 2 只落 MVP，后续字段按类型分层 |
| Medium | WorkLine 缺少默认运行模式时，sandbox 只能作为工具参数存在，无法进入真实 runtime | PR 5 增加 `WorkLine.run_mode`、开发/测试环境门禁、Session 快照、effect adapter 分流，并派发到沙箱通道 |
| Medium | 命令 helper 如果返回完整 command 包络，会重新泄漏协议细节 | PR 4 helper 只返回业务 `params` |
| Medium | SMT 随机料箱分配容易成为坏模板 | PR 4 先用 service abstraction / external intent 替换 |
| Medium | 文档仍可能教旧写法 | PR 8 同步更新 `docs/plugin_development_guide.md` |

### 工程验收清单

- PR 0：`state_machine_class` 解析测试、`plugin_state` 读写测试通过。
- PR 1：非法顶层业务字段直接拒绝，合法 `data` / `params` 两层结构通过。
- PR 2：SMT manifest MVP 可被 registry 读取；缺少必填项有清晰错误。
- PR 3：业务 NG 不进入 system failure 指标，但 timeline 可按 reason 查询。
- PR 4：Outbox payload 中业务字段只出现在 `params`；插件代码不直接 HTTP、Repository、SQL。
- PR 5：`WorkLine.run_mode` 可配置；生产环境禁止 `SIMULATION`；Session 创建时快照；sandbox 派发到沙箱通道并支持手工 callback。
- PR 6：SMT 状态迁移非法时失败可诊断；插件级诊断输出 handler 和 result。
- PR 7：第二插件只新增插件目录、tests 和 fixtures，不修改 runtime。
- PR 8：模板可从两个插件沉淀，并用 sandbox 指南跑通第一个 WORKLINE happy path。

## Phase 3.5：开发体验评审

### 目标开发者体验

目标用户：后续开发 WORKLINE 插件的后端工程师或现场集成工程师。

目标指标：

- 新插件骨架生成时间：小于 10 分钟。
- 第一个插件级 handler/context 诊断：小于 2 小时。
- 第一个硬件联调前的 WORKLINE happy path：不需要真实设备，可通过 WorkLine `SIMULATION` 派发到沙箱并手工回调。
- 业务 NG / 系统异常分类：不需要读 runtime 源码即可理解。

### 开发体验问题

| Severity | Finding | Fix |
|----------|---------|-----|
| High | 当前插件开发者必须读 runtime、SMT plugin、callback 文档才能知道 `data` / `params` 边界 | PR 8 更新中文插件开发指南，并把包络规则前置到模板 checklist |
| High | 没有 sandbox 派发和手工 callback 时，新插件的 WORKLINE 级调试仍会被硬件环境绑定 | PR 5 提供 WorkLine run_mode 和沙箱派发/手工回调闭环，PR 8 完善 sandbox 调试指南 |
| Medium | manifest、context、state machine 如果只靠约定，错误会在运行期才暴露 | 模板生成默认测试和 fixtures，缺字段报错给出 cause/fix |
| Medium | 业务 NG 和系统异常的术语需要在文档中固定 | 新增“业务结果分类表”：business decision、hardware failure、data invalid、system failure |

### 插件开发 Checklist

- 定义 `contract_version`。
- 定义 event `data` 模型和 command `params` helper。
- 定义 context model 和状态字段。
- 定义 state machine transition table。
- 定义 WorkLine 默认 `run_mode`，确认 `SIMULATION` 只允许开发/测试环境，且 sandbox 下只派发到沙箱通道。
- 定义 required device roles、事件来源角色、命令目标角色和能力要求；上下游由 `Device.upstream_device_id` 推导，不在插件中重复配置。
- 使用 runtime 提供的 `WorklineTopologyView`，不要在插件内缓存数据库设备对象或自行查询拓扑。
- 定义 business key resolver。
- 定义 result classifier：业务 NG、硬件失败、数据非法、系统异常。
- 准备 happy path、业务 NG、系统异常、timeout fixtures。
- 运行插件级诊断和 sandbox 调试，验证 sandbox 派发、手工 callback 和 Session 推进。

## 最终关卡

结论：批准进入下一阶段重构，但执行计划按本文修订版落地。

下一阶段优先级：

1. PR 0：状态字段和 registry 破窗修复。
2. PR 1：严格协议包络边界。
3. PR 1.5：第二插件薄 spike / 压力用例设计。
4. PR 2：Manifest MVP 和最小拓扑视图。
5. PR 3：BusinessDecisionIntent 和 result classifier。
6. PR 4：命令 params、设备 adapter、外部协同边界。
7. PR 5：WorkLine 运行模式和 sandbox effect adapter。
8. PR 6：SMT 状态机、typed context、插件级诊断。
9. PR 7：第二个最小 WORKLINE 插件实现。
10. PR 8：基于两个插件沉淀模板和 sandbox 调试指南。

这样先锁定平台契约和调试运行边界，再用两个插件证明它真的可复制，最后沉淀模板和文档。

---

## GSTACK REVIEW REPORT

### /autoplan 复审状态

- 执行时间：2026-04-24
- Base branch：`develop`
- 当前分支：`feature/plugin-refactoring`
- Restore point：`/tmp/feature-plugin-refactoring-autoplan-restore-20260424-204247.md`
- UI scope：否，设计评审跳过
- DX scope：是，插件架构、sandbox、模板和开发指南都直接影响插件开发者
- 说明：本节是 `$autoplan docs/business/workline_plugin_refactor_next_phase_plan.md` 的最新复审结论。前文较早的 `/autoplan 评审报告` 中旧 PR 编号如有冲突，以本节和“建议 PR 顺序”为准。

### CEO 复审结论

核心判断：计划方向成立，但原计划仍过度围绕 `smt_classifier`。真正目标不是“把 SMT 整理干净”，而是让第二、第三个 WORKLINE 插件可以在不修改 runtime 的情况下交付。

| Severity | Finding | Decision |
|----------|---------|----------|
| Critical | 只基于 SMT 沉淀模板会复制 SMT 的偶然复杂度 | 保留第二插件 spike，但移动到平台原语之后、模板之前 |
| Critical | `state_machine_class` 和状态源破窗会阻断任何显式状态机 | PR0 必须端到端修 registry、manifest、`plugin_state`、trace/query |
| High | 业务 NG 如果晚于状态机落地，会把错误语义固化进 SMT 状态机 | BusinessDecisionIntent 提前到 PR3 |
| High | sandbox 不是插件诊断工具，必须覆盖派发和手工 callback 闭环 | WorkLine `SIMULATION` 和 sandbox adapter 作为 PR5 前置能力 |

### 工程复审结论

| Severity | Finding | Decision |
|----------|---------|----------|
| High | PR 编号曾经自相矛盾，且第二插件验证点过晚或过早都会失真 | 已统一为 PR0、PR1、PR1.5、PR2 到 PR8：先做薄 spike 约束平台，再做完整第二插件验收 |
| High | `/callback/event` 和 `/callback/result` 当前不会 forbid extra | PR1 明确使用 `extra="forbid"` 或等价 allowlist validator |
| High | `DeviceCommand.params` 与 `Outbox.payload_json.params` 边界不清 | PR4 明确三层命令参数职责 |
| High | sandbox 不能靠消息 payload 标志区分 | PR5 改为由环境门禁 + `WorklineSession.run_mode` 分流，消息 payload 尽量等同 live |
| Medium | `business_key_resolver` 不能长期可选 | PR2 纳入最小 manifest，避免第二插件修改通用 resolver |

### DX 复审结论

| Severity | Finding | Decision |
|----------|---------|----------|
| High | 当前开发指南仍可能误导新插件作者复制旧 `step_code` / 单文件插件写法 | PR8 必须重写中文快速开始和 checklist |
| High | 没有 sandbox 派发和手工 callback 时，WORKLINE 级调试仍绑定真实硬件 | PR5 必须提供沙箱待处理查询和手工 callback 闭环 |
| Medium | fixtures 不是一等资产 | PR7/PR8 要求两个参考插件都提供 happy path、business NG、system failure、timeout、invalid envelope fixtures |
| Medium | 插件级诊断要降级为定位工具 | PR6 只用于 handler/context/状态机诊断，不声称能调试完整工作线业务 |

### 最新执行顺序

1. PR0：状态源和 registry 破窗修复。
2. PR1：严格协议包络边界。
3. PR1.5：第二插件薄 spike / 压力用例设计。
4. PR2：最小 manifest、基于 `Device.upstream_device_id` 的 topology 校验、capability、business key resolver、最小 `WorklineTopologyView`。
5. PR3：BusinessDecisionIntent 和 result classifier。
6. PR4：命令 params、设备 adapter、外部协同边界。
7. PR5：WorkLine 运行模式和 sandbox effect adapter。
8. PR6：SMT 状态机、typed context、插件级诊断。
9. PR7：第二个最小 WORKLINE 插件实现。
10. PR8：基于两个插件沉淀模板、fixtures、sandbox 调试指南。

### 测试计划

| PR | 必测内容 |
|----|----------|
| PR0 | `WorkLine.state_machine_class` 可解析；`plugin_state` 是唯一状态源；`stage` / `step_code` 不再新增写入；trace/query 字段投影清晰 |
| PR1 | 顶层 `PkgID`、`location`、`pkg_id`、`reel_diameter`、`actual_qty` 被拒绝；合法 `data` / `params` 通过 |
| PR1.5 | 第二插件薄规格包含业务键、事件 `data`、命令 `params`、设备角色、等待回调、业务 NG、系统异常和 sandbox happy path；不得要求 runtime 私有分支 |
| PR2 | 缺 PR2 manifest 必填字段失败；`business_key_resolver` 接入 SessionResolver；设备角色数量、事件来源、命令目标、capability、由 `Device.upstream_device_id` 推导的上下游校验失败路径；单次 workflow 内拓扑视图复用 |
| PR3 | business decision 不计入 system failure；timeline/trace 可按 reason_code 查询 |
| PR4 | `CommandIntent.parameters == DeviceCommand.params`；业务字段只在 `Outbox.payload_json.params`；插件不能直接 HTTP、Repository、SQL |
| PR5 | `APP_ENV=prod` 禁止 `SIMULATION`；dev/test 可开启；sandbox 派发到沙箱通道；手工 callback 推进同一 Session；消息 payload 不增加 sandbox 标志 |
| PR6 | SMT 非法迁移失败可诊断；当前实际触发器和迁移表被测试覆盖；typed context 覆盖关键字段；插件级诊断输出 handler/context/result |
| PR7 | 第二插件只新增插件目录、tests、fixtures，不修改 callback/runtime/outbox/dispatcher/session resolver |
| PR8 | 模板能生成骨架；两个插件的 fixtures 可驱动文档示例和 sandbox happy path |

### Cross-phase Themes

| Theme | Phases | Decision |
|-------|--------|----------|
| 唯一状态源 | CEO / Eng / DX | PR0 端到端统一为 `plugin_state` |
| 插件可复制性 | CEO / DX | 第二插件必须作为模板前的验收门槛 |
| sandbox 真实闭环 | Eng / DX | sandbox 必须派发到沙箱通道并支持手工 callback，不是 dry-run |
| 消息契约收敛 | Eng / DX | 包络、params、business decision 都要变成 typed contract |

### Final Gate

建议批准当前修订版计划。没有未解决的 User Challenge。

Taste decision 已调整：第二插件“薄 spike / 压力用例设计”前置到 PR1.5，完整第二插件实现仍放在 PR7。这样 PR2 到 PR5 的平台契约会被第二业务场景约束，但不会在平台原语缺失时被迫修改 runtime。

---

## /autoplan 终审报告

### 终审范围

- 执行时间：2026-04-24
- Base branch：`develop`
- 当前分支：`feature/plugin-refactoring`
- Restore point：`/tmp/feature-plugin-refactoring-autoplan-final-restore-20260424-210946.md`
- UI scope：否
- DX scope：是
- 审查准则：使用 `$autoplan` 的 CEO / Eng / DX 终审路径，并用 `$karpathy-guidelines` 约束计划方案本身：少抽象、可验证、按 PR 外科式推进。

### 终审结论

批准进入下一阶段，但以本终审后的 PR 顺序和验收口径为准。核心调整有三点：

1. 第二插件验证不能等到平台全部做完，新增 PR1.5 薄 spike / 压力用例设计。
2. PR2 的拓扑缓存收敛为 `WorklineTopologyView`，先做单次 workflow 复用，不先上长 TTL 版本化缓存。
3. PR2 manifest 的强制字段收窄；`state_machine_class` / `context_model` 到 PR6 SMT 落地后再升级为生产插件必填。

### CEO 终审

| Severity | Finding | Decision |
|----------|---------|----------|
| High | 计划解决的是正确问题：把 runtime 隐性规则变成插件契约，而不是只把 SMT 写干净 | 保留“SMT 样板 + 最小 runtime hooks”路线 |
| High | “每扩一个 WORKLINE 都新增插件”需要准入标准，否则会把物理线差异误建成插件 | 已补充：只有业务键、状态机、设备协作、业务决策显著不同才新增插件 |
| High | 只等 PR7 才验证第二插件太晚 | 新增 PR1.5 薄 spike，完整实现仍在 PR7 |
| Medium | 通用 DSL、设计器、热加载仍然是过度扩张 | 继续明确排除 |

### 工程终审

| Severity | Evidence | Decision |
|----------|----------|----------|
| High | `WorkLine.state_machine_class` 读取不存在的 `definition.state_machine_class`，而 registry 只有 plugin class / contract module | PR0 先修 registry 和状态源破窗 |
| High | runtime 有状态机时读 `context_json['stage']`，SMT 当前写 `step_code` | PR0 定义 `plugin_state` SSOT；`step_code` 只能是投影或迁移目标 |
| High | PR2 原先要求 `state_machine_class` / `context_model`，但 SMT 对应文件在 PR6 才新增 | PR2 先可选，PR6 后升级为生产插件必填 |
| High | `SessionResolver` 当前仍偏 SixInOne 私有解析 | PR2 必须接入 manifest `business_key_resolver`，PR1.5 用非 SMT fixture 验证 |
| Medium | PR6 计划迁移表和当前 SMT 触发器不一致，当前有 `pick_ok` / `pick_ng` | PR6 必须测试当前触发器清单和旧新映射 |
| Medium | PR4 需要精确落到 `_normalize_vendor_command_payload`、`_build_command_create_payload`、`_build_outbox_payload` | 已加入 PR4 runtime 修改点和验收口径 |
| Medium | PR5 生产禁用 `SIMULATION` 不能靠 `APP_DEBUG` 推断 | 已加入 `APP_ENV=dev|test|prod` |

### DX 终审

| Severity | Finding | Decision |
|----------|---------|----------|
| High | 新插件开发者最怕的是要读 callback、orchestrator、outbox、SMT 插件才能写业务 | PR8 必须重写中文插件开发指南和 checklist |
| High | 没有 sandbox 派发和手工 callback，WORKLINE 级调试仍绑定真实硬件 | PR5 保持真实派发链路，但出口切到沙箱通道 |
| Medium | 插件级诊断容易被误当成 WORKLINE 级调试 | PR6 明确只诊断 handler/context/状态机；完整业务闭环走 sandbox |
| Medium | 模板如果来自一个插件，会复制 SMT 特例 | PR8 只能基于 SMT + 第二插件交集沉淀 |

### 最终执行顺序

1. PR0：状态源和 registry 破窗修复。
2. PR1：严格协议包络边界。
3. PR1.5：第二插件薄 spike / 压力用例设计。
4. PR2：最小 manifest、business key resolver、topology/capability 校验、`WorklineTopologyView`。
5. PR3：`BusinessDecisionIntent` 和 result classifier。
6. PR4：命令 params、设备 adapter、外部协同边界。
7. PR5：WorkLine 运行模式和 sandbox effect adapter。
8. PR6：SMT 状态机、typed context、插件级诊断。
9. PR7：第二个最小 WORKLINE 插件完整实现。
10. PR8：模板、fixtures、sandbox 调试指南和中文插件开发指南。

### Final Gate

终审批准当前修订版计划。没有未解决的 User Challenge。

需要特别关注的 taste decision 有两个：

1. 第二插件验证前置到 PR1.5，但完整实现仍在 PR7。
2. 拓扑缓存先做 `WorklineTopologyView`，不先做长 TTL 版本化缓存；等第二插件或压测证明有必要，再升级为 `WorklineTopologySnapshot`。
