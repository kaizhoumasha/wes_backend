---
title: 工作线配置选择业务插件与安全切换设计
status: Reviewed
created_at: 2026-09-04
reviewed_at: 2026-09-04
scope: 工作线插件选择、显式装配、运行代际、空闲切换、前后端配置及目录收敛
---

# 工作线配置选择业务插件与安全切换设计

## 1. 目标与边界

部署制品提供可用业务插件，业务用户在工作线配置页面选择插件。START 冻结本次运行使用的插件、配置和设备合同；
工作线完全空闲并停用后，才允许更换插件。现场两条自动分拣线、两条人工分拣线可分别装配自动上架、自动拣料、
人工上架、人工拣料；后续退料线、拆箱线及其业务插件使用同一入口。

工作线是物理实例，插件是可复用业务行为。一个 WorkLine 拥有多个 Device，一个 Device 只属于一个 WorkLine；
每条工作线同一时刻只有一个活动插件，同一插件可服务多条兼容工作线。基础能力不内置线号、具体业务、设备厂商或默认插件。

本设计覆盖宿主标准能力、业务插件交付和 SDK 目录迁移，但三者使用独立出口。文档获批不表示生产代码、四种业务插件、
现场切换或部署已经完成。

## 2. What already exists

| 现有能力 | 复用方式 |
| --- | --- |
| `Device.work_line_id` | 直接表达一个设备只属于一条工作线，不增加资源 Claim 或设备租约 |
| `StaticPluginBinding` | 复用精确 `plugin_key + plugin_version + Fact` 路由，收敛为一个插件清单 |
| `LineRunEpoch` | 保留为一次 START 的最小冻结运行身份；复用“每条工作线最多一个 ACTIVE Epoch”的部分唯一索引 |
| `WorkLineStartService` | 复用请求幂等、WorkLine/Epoch 锁和完整 Epoch 创建 |
| `WorkLineRepository.get_unfinished_workload_summary` | 复用单 SQL 通用阻塞汇总，并补齐当前插件的业务任务 blocker |
| `PickingTask`、`MaterialExecution`、`BinExecution` | 继续作为各业务域的可靠事实，不增加通用 `PluginTask` |
| WMS Event 唯一入口及严格 DTO | 继续按 operation 接收一次，再依据持久执行身份和 Epoch 路由 |
| WorkLine 配置、启停和 START 页面 | 在现有页面改成业务插件选择与配置，不建立第二套管理页面 |
| 设备管理页与 ECS 发现抽屉 | 复用单 Endpoint 比对、状态/能力展示和显式接管；不在设备页维护 WorkLine 归属 |
| 前端 `DESIGN.md` | 复用工业琥珀、紧凑表格、`StandardDialog`/`StandardDrawer`、状态色和响应式规则 |
| `packages/wes_plugin_sdk` | 保留公开 import 与独立构建能力，仅做物理目录迁移 |

当前 `deployment/rough_sorter_composition.py`、Web 和 Celery 仍围绕粗分机装配；人工拣料 prepare 仍判断固定插件键；
Worker 仍在任一活动 Epoch 存在时拒绝启动。这些是本设计需要消除的主要偏移。

2026-09-04 现场只读检查显示：`10.24.209.28` 返回 2 台 `ROBOTIC_ARM/SORTER_ARM`，
`10.24.209.26` 返回 16 台 `SCANNER/SCAN_STATION`、4 台 `CONVEYOR/INPUT_STATION` 和
4 台 `CONVEYOR/OUTPUT_STATION`。ECS 提供设备静态描述、支持的命令/事件和实时状态，但不提供 WorkLine 归属；
这些数据只作为发现、诊断和准入快照，WorkLine 归属仍由 WES `Device.work_line_id` 管理。

## 3. NOT in scope

- 运行时扫描 Python 环境、动态 import、热安装插件或可写 registry：部署清单应明确、可审计。
- 每插件 Runtime、Worker、队列或 Beat：共享基础 Runtime 每进程只构造一次。
- 繁忙时预约切换或 `DRAINING` 状态：当前只允许工作线完全空闲后切换。
- 同一插件多版本并存：升级前先关闭使用旧版本的活动 Epoch。
- 通用资源 Claim、资源匹配引擎或位置租约：设备唯一归属由 `Device.work_line_id` 保证。
- Device Slot、WorkLineDevice 关联实体或 `(device_role, device_code)` 复合唯一键：`device_code` 已是精确全局身份，role 只描述职责。
- 通用任务基类、`PluginTask`、规则 DSL 或全局调度实体：各业务域复用自己的现有事实和队列。
- 预建退料、拆箱空插件：业务合同成熟后再交付真实插件。
- 供应商私有协议、外部 SDK Registry 或公共包仓库发布：本仓只验证现有 release provider 和 wheel 构建。
- 设备批量接管、原子批量创建或拓扑画布：设备和工作线管理是管理员低频初始配置，保留逐台显式接管。
- 后端 JSON Schema 动态表单或第二套专用配置页面：前端使用显式插件配置目录和现有弹窗。

这些项目是明确的非目标，不写入 `TODOS.md`。

## 4. 最小目标结构

```text
src/                         宿主基础实现
├── app/workline             WorkLine、Epoch、启停和 START
├── app/execution            通用 Fact 处理和可靠执行
├── app/wms_adapter          唯一 WMS HTTP/DTO/Event 入口
├── app/transport            Transport 独立生命周期
└── wes_plugin_sdk/          独立可安装的公开 SPI 工程

workline_plugins/            业务实现
├── rough_sorter/
├── auto_putaway/
├── auto_picking/
├── manual_putaway/
└── manual_picking/

deployment/                  唯一关联目录
└── plugins.py               显式已安装插件对象清单
```

目标只有两个实现根 `src/`、`workline_plugins/` 和一个关联目录 `deployment/`。`src` 与 SDK 不导入具体插件；
插件可依赖 SDK，插件 application 层可依赖宿主公开基础端口；`deployment` 负责最终注入。

### 4.1 一个插件对象

每个业务插件只导出一个不可变插件对象，包含本业务实际需要的共同入口：

- `plugin_key`、部署版本和展示名称；
- 对 WorkLine、Device 和配置的纯内存适用性检查；
- START plan builder；
- Fact factory、Handlers 和初始执行关联；
- WMS WAIT 后继规划与 Transport outcome 应用；
- 当前插件业务任务的关闭 blocker。

不为每项能力创建 registry，也不构造每插件 Runtime。`deployment/plugins.py` 显式列出本制品安装的插件：

```text
已安装插件对象 tuple
        │
        ├── 配置页列出兼容项
        ├── START 按 WorkLine.plugin_key 选择一个
        └── Execution 按 Epoch.plugin_key + plugin_version 路由
```

Web 和 Celery 在各自进程与事件循环内读取同一清单定义，构造一次共享 Execution、Transport、DeviceCommand 和 WMS Runtime。
新增插件只增加真实插件包，并在 `deployment/plugins.py` 增加一项显式装配。

## 5. WorkLine 配置

WorkLine 新增唯一明确字段 `plugin_key`；不保存 `plugin_version`。现有 `config` 只保存当前插件的业务配置，
不重复保存插件身份。未选择插件时允许保存草稿，但不允许激活或 START。

`line_type` 继续表示自动、人工或混合设备类别，`run_mode` 继续表示运行控制模式；两者不表示上架、拣料、退料或拆箱。
业务用户只选择插件，START 从部署清单取得精确版本并冻结到 Epoch。插件升级后，下一次 START 使用新部署版本，
无需批量改写 WorkLine。

配置页读取一次 WorkLine 和所属 Device，把现有只读对象传给全部插件做内存适用性判断；不新增快照业务实体或缓存。
START 只调用所选插件，并在事务内重新锁定 WorkLine、Device 和活动 Epoch 后校验。插件清单、配置保存、预检和 START
都由后端执行权限、乐观锁与 fail-closed 校验；事务提交后再统一失效 WorkLine 与设备列表缓存。

活动 WorkLine 禁止修改 `plugin_key`、业务配置和 Device 归属。设备归属不通过 Epoch Claim 重复表达；
设备迁移仅在原工作线停用且 Epoch 已关闭后允许。

### 5.1 页面职责与唯一写入口

```text
设备管理
├── 从 ECS 发现、比对和接管设备
├── 展示硬件类型、ECS 角色、支持的命令/事件和实时状态
└── 只读展示当前 WorkLine 归属

工作线管理
└── 业务配置
    ├── ① 当前插件、工作线状态和主要操作
    ├── ② 本线设备及未绑定设备选择
    └── ③ 所选插件的业务配置和检查结果
```

`Device.work_line_id` 只保留一个写入口：工作线业务配置。Device Create 固定创建为未绑定，Device Update 合同排除该字段；
即使绕过前端提交，后端也稳定拒绝。目标工作线只列出本线设备和未绑定设备；设备跨线调整必须先在原工作线解绑，
再在目标工作线绑定，不提供直接跨线迁移。这样不增加关联实体，也避免两套安全规则。

保存 DTO 中的 `device_code` 是目标工作线设备全集，采用 replace-all 语义。Service 先锁定目标 WorkLine，再按 Device ID 升序锁定
“当前已绑定集合 ∪ 提交集合”，随后复核 version、停用状态、无活动 Epoch、无 unfinished blocker，以及每台设备未删除且归属只能是
本线或未绑定；重复、未知或已属于其他工作线的编码均使整笔保存回滚。两个工作线并发争用同一未绑定设备时，Device 行锁串行化，
后提交者收到稳定归属冲突。由于不支持直接跨线迁移，事务不锁其他 WorkLine。

设备发现抽屉把现有逐设备卡片改为紧凑表格。默认列为设备编码、名称、设备类型/ECS 角色、在线状态、运行状态、
WES 接管状态和操作；展开行显示 `supported_commands`、`supported_events`、当前命令及差异原因。状态使用既有标签和文字，
不使用彩色左边框卡片。移动端保留编码、名称、状态和操作，其他列通过横向滚动或展开行查看。
逐台“接管”继续复用现有创建设备表单，自动带入 `device_code`、名称、Endpoint，并以 ECS `role` 作为可修改建议值；
它不自动成为 WES `device_role` 的权威映射，管理员仍按插件职责确认。接管时不选择 WorkLine，也不增加多选或批量接口。

同一工作线允许存在多台相同 `device_role` 的设备。运行时以 `device_role` 选择职责范围，以全局唯一 `device_code` 精确选择设备；
`role_index` 只用于展示和稳定排序，不承担路由身份，也不冻结到 Epoch。Epoch 保留已有 `(epoch, device_id)`、
`(epoch, device_code)` 唯一约束，删除 `(epoch, device_role)` 唯一约束。宿主按 role 查询返回集合或要求同时提供 code，
不得再返回任意一台；插件确实要求单设备职责时，由该插件显式校验集合恰好一项。所有摘要按 `(device_role, device_code)`
稳定排序。这样即可表达 `SCAN_STATION` 下多台扫码设备，不增加 Slot 或关联实体。

ECS 返回的 `device_type`、`role`、`supported_commands`、`supported_events`、mode、status 和在线状态保持 ECS 只读事实，
不复制成 `Device` 正式字段，也不写入 `diagnostic_profile`。插件列表的初筛只使用 WES 静态 WorkLine/Device 拓扑；
配置弹窗的“检查设备”和 START 按 ECS Endpoint 分组实时读取，每个 Endpoint 一次，核对所选 `device_code`、硬件能力、
在线状态与状态时效；同一 Endpoint 下多个设备从同一响应索引匹配，禁止逐设备请求。ECS 不可用或事实不匹配时允许保存草稿，
但 START fail closed；页面显示“实时状态未知”及具体失败 Endpoint。

工作线列表保留一个“业务配置”行操作，打开现有 `StandardDialog` 的大尺寸弹窗，不增加页签、向导或独立配置页面。
弹窗按上述三个区块纵向排列，首屏优先展示“当前插件、是否运行、能否修改以及下一步操作”。活动状态下三个区块均只读，
并明确显示“停用后可修改”；停用状态下才允许选择插件、绑定设备和编辑业务配置。配置较长时允许纵向滚动，
当前阻塞和主要操作始终位于弹窗顶部，不把关键状态藏进折叠区或页签。

插件选择器只直接展示兼容插件；“其他已部署插件（N）”折叠区展示不可选插件及稳定原因码翻译后的说明。
原因必须指向可处理事实，例如缺少的 `device_role`、所需硬件能力或不匹配的 `line_type`，不能只显示“不兼容”。
未部署、已部署但不兼容和兼容但配置未完成必须是三个不同状态。

前端使用显式插件配置目录：每项包含 `plugin_key`、展示名称、配置 Schema、表单组件和只读摘要组件。
通用业务配置弹窗不包含具体插件字段，也不按插件键堆叠条件分支。新增真实插件时增加一个独立前端配置模块并显式登记；
后端可用插件清单仍决定页面实际可选项。当前不引入后端 JSON Schema 驱动的动态表单。

停用状态保留“保存”用于草稿；“启动工作线”是唯一运行入口。非重放 START 只接受 `is_active=false` 且无活动 Epoch 的 WorkLine，
在同一事务内重新锁定并检查 WorkLine、设备和安全状态，再设置 `is_active=true` 并创建新 Epoch；它不得隐式关闭旧 Epoch，
也不得调用会自行 commit 的旧 `activate`。停用操作仍在同一事务内确认全部 blocker 已清零、关闭当前 Epoch 并设置
`is_active=false`。除同一 `request_id` 的幂等重放外，`is_active` 与活动 Epoch 不一致均作为数据不变量错误 fail closed。
旧 activate API、权限、前端方法和生成合同一并删除，不保留兼容路径。

### 5.2 交互状态

| 功能 | Loading | Empty | Error | Success | Partial / stale |
| --- | --- | --- | --- | --- | --- |
| 打开业务配置 | 显示三段结构骨架，所有写操作禁用 | WorkLine 不存在时关闭弹窗并刷新列表 | 保留列表上下文，显示“加载失败，重试” | 显示最新 version、插件、设备和状态 | 任一子请求未完成时对应区块标记未知，禁止启动 |
| 插件选择 | 显示“正在检查兼容插件” | 显示“当前没有兼容插件”，同时给出设备配置入口 | 区分插件清单不可用与兼容性检查失败 | 兼容项直接可选 | 不兼容项折叠显示原因；未知检查项不可选 |
| 设备绑定 | 按 Endpoint 显示读取进度 | 显示“暂无已绑定设备”；无候选时引导先到设备管理从 ECS 接管 | 失败 Endpoint 显示具体地址与重试，不清空已编辑选择 | 显示设备编码、WES 角色、归属和 ECS 当前状态 | 保留本次会话最近成功快照并标记已过期；禁止启动 |
| 保存草稿 | 保存期间锁定关闭和重复提交 | 不适用 | 行内保留用户输入；乐观锁冲突要求重新加载 | 明确显示“草稿已保存”，刷新 version | 不允许部分成功；任一配置或绑定失败时全部回滚 |
| 启动工作线 | 分步显示“检查配置 / 检查设备 / 创建运行代际” | 缺插件或设备时按钮禁用并显示原因 | 稳定拒绝显示检查项；结果未知复用同一 request_id | 显示插件、版本和 Epoch 编码 | 任一 ECS Endpoint 不可用或设备能力未知时禁止启动 |
| 停用工作线 | 显示“正在确认未结束任务”，禁止重复提交 | 已停用时直接进入可编辑状态 | 按类型展示 blocker 数量、样本、状态和处理提示 | 显示“工作线已停用，可修改配置” | blocker 刷新失败时保留上次结果并标记过期，不允许停用 |

弹窗关闭后不保留未保存编辑；存在修改时关闭需二次确认。成功保存只表示 WES 草稿一致，不表示 ECS 可用或工作线已启动。

### 5.3 操作旅程

| 步骤 | 用户操作 | 用户需要获得的感受 | 页面支持 |
| --- | --- | --- | --- |
| 1 | 浏览工作线列表 | 立即知道哪条线在运行、使用什么插件 | 固定展示工作线、插件和运行状态 |
| 2 | 打开“业务配置” | 确认自己操作的是正确工作线 | 顶部重复工作线名称、当前插件、状态和主要操作 |
| 3 | 请求停用 | 知道为什么现在能停或不能停 | 先检查 blocker；有任务时显示类型、数量、样本和处理提示 |
| 4 | 确认停用 | 明白停用会拒绝新任务 | 显示工作线、插件、Epoch 和影响；一次“确认停用” |
| 5 | 选择插件和设备 | 只看到当前可行选择 | 兼容插件优先，不兼容项折叠；设备按角色和编码选择 |
| 6 | 保存草稿 | 相信配置不会只保存一半 | 插件、配置和设备绑定一次事务提交 |
| 7 | 启动工作线 | 知道系统正在进行哪些检查 | 依次显示配置、ECS 设备和 Epoch 创建进度 |
| 8 | 查看启动结果 | 确认实际运行身份 | 显示插件、版本、Epoch 编码和启动时间 |

前 5 秒只要求用户读懂“哪条线、什么插件、是否运行、下一步按钮”；5 分钟内可在一个弹窗完成空闲切换；
长期新增退料线、拆箱线时继续沿用同一路径，不增加按设备类型分叉的页面。

### 5.4 设计系统、响应式与无障碍

复用前端 `DESIGN.md`、`CrudPageContainer`、`StandardDialog`、`StandardDrawer`、Element Plus 表单/表格和现有颜色变量；
不为本功能新增全局 token、装饰卡片、渐变或自定义导航。插件和 Epoch 编码使用等宽字体，状态必须同时使用文字和语义色。

管理操作以桌面和平板为主，但保持现有响应式基线：小于 768px 时弹窗接近全宽，三段内容和表单改为单列；
设备表保留编码、名称、状态和操作，其余内容进入横向滚动或展开行。不设计移动端专属向导。

弹窗保持焦点陷阱，关闭后焦点回到触发按钮；所有输入有可见标签，折叠区公开 `aria-expanded`，加载、保存、阻塞和
启动结果通过 `aria-live` 通知。键盘操作顺序按顶部状态、设备、插件配置、页脚动作排列；按钮和行操作点击区域不小于 44px，
正文和状态文字满足 4.5:1 对比度。错误信息与对应字段关联，不能只依赖颜色。

“保存”使用一个专用 WorkLine 业务配置 DTO，同时携带乐观锁 version、`plugin_key`、插件配置和目标设备全集。
保存、START、deactivate 都由各自的应用 Service 拥有一次 commit/rollback；Repository 和被调用的领域 Service 只 flush，禁止嵌套 commit。
任何字段或设备归属失败时全部回滚，成功提交后统一失效缓存。ECS 暂时不可用不影响保存 WES 草稿，
但“启动工作线”必须重新读取 ECS 实时事实并通过检查。

## 6. 瘦身后的 Epoch

Epoch 只承担一次 START 的稳定运行边界：

```text
WorkLine 可变配置
        │ START
        ▼
Epoch: workline + plugin/version + frozen config/topology + ACTIVE/CLOSED
        │
        ├── 多个业务任务或执行
        ├── DeviceCommand
        ├── TransportTask
        └── WMS confirmation/evidence
```

复用现有 Epoch、设备/位置 binding 和请求幂等身份，不增加资源占用表、插件会话或第二套状态机。
`WorklineSession` 仍表示单次业务链路；一次 Epoch 可以包含多个 Session，两者不合并。

`WorkLine.is_active=true` 必须且只能对应一个 ACTIVE Epoch。现有部分唯一索引继续保证“最多一个”，START/deactivate 事务保证
“状态一致”；迁移与发布前检查既有数据，对 active/no-Epoch 或 inactive/ACTIVE-Epoch 直接报告并阻止发布，不做静默自动修复。

初始关联、Fact factory、WMS 请求构造、后续动作、Transport 结果应用和业务 blocker 都从原执行身份与 Epoch 找 owner。
迟到 Device、Transport 或 WMS 结果继续关联原 Epoch；Epoch 已关闭时只保留权威事实、冲突和审计，不触发新业务动作，
也不重绑到 WorkLine 当前选择的插件。

## 7. WMS 任务与 Operation

WMS Adapter 继续拥有唯一 HTTP/Event 入口、严格 operation DTO、可靠接收和 ACK。每个 operation 只接收一次；
持久化以后，由已有业务任务或执行身份定位 WorkLine、Epoch 和插件。

WMS 决定业务单据与任务池优先序，WES 选择可执行 WorkLine。各业务域和插件复用自己的既有事实：

- Picking 使用现有 `PickingTask` 队列；自动与人工插件分别实现自己的适用性和 claim 规则。
- 入库上架继续使用既有 `MaterialExecution`、`BinExecution` 和设备证据链。
- 设备触发流程依据 Device 的 WorkLine 归属与活动 Epoch 定位插件，不强制进入通用任务池。

宿主提供事务、WorkLine/Epoch 锁和通用安全检查；插件提供业务选择与校验。不新增 operation-to-plugin 动态路由器或通用调度实体。
新增 WMS operation 仍按现有 `<domain_key>` 目录和静态 Event route 交付 DTO、Adapter、Handler 及确有需要的持久化能力。

Transport 保持独立标准能力。插件只提交 Transport 请求、保存原执行关联并解释结果；Transport 的身份、状态、重试、回调、
`DELIVERY_UNKNOWN/RECONCILING` 和资源围栏不进入插件，也不由 `DeviceCommand` 替代。

## 8. 只允许空闲切换

```text
运行中 ──存在任一未结束义务──> 拒绝停用并返回阻塞项
  │
  └──全部义务结束──> deactivate 事务
                       ├──锁定 WorkLine 与当前 Epoch
                       ├──重查通用 + 当前插件 blocker
                       ├──关闭 Epoch
                       └──WorkLine.is_active = false

停用 ──修改 plugin_key/config/device──> 保存草稿 ──启动工作线──> 原子启用 + 新 Epoch
```

不增加停止意图或 `DRAINING`。只要该工作线或当前 Epoch 存在任何未结束义务，就不可停用或切换，包括：

- `TransportTask`、`BinExecution`、`MaterialExecution`；
- `DeviceCommand`、WMS confirmation、待应用或待发布 evidence；
- `UNKNOWN/RECONCILING`、安全事件和未释放资源围栏；
- 当前插件拥有的 `PickingTask` 或其他真实业务任务；
- 插件定义的 FIFO/LIFO、Bin/货架退回和清场义务。

宿主通用 blocker 继续扩展现有单 SQL 汇总；只调用当前插件的业务 blocker，不枚举所有插件。汇总返回每类准确 count，
每类只取一个稳定样本，并复用各 owner 的 WorkLine/Epoch 与未结束状态索引；配置页面是低频管理入口，不增加缓存或监控投影。
`deactivate`、任务准入、START 和配置更新遵循同一 WorkLine→Epoch→Device 锁顺序。并发时，先提交的任务成为 blocker；
先完成停用的事务使后续准入看到 `is_active=false`。繁忙时直接返回可诊断阻塞项，操作员在空闲后重试。

blocker 清零后不立即停用。页面先显示一次上下文确认，包含工作线名称、当前插件、当前 Epoch，以及
“停用后将拒绝新任务，可修改插件和设备”；用户点击“确认停用”后才提交停用请求。不要求输入工作线编码。

停用被拒绝时，业务配置弹窗顶部显示阻塞类型总数，并按类型返回 `count + status + sample identity + operator hint`；
例如 `TransportTask ×1 · RECONCILING · T20260904-01`。页面不展开全部业务任务，只提供“刷新检查”，并在 Bin/物料
位置诊断适用时复用已有 `active-objects` 只读视图。宿主 blocker 与当前插件 blocker 使用同一展示 DTO，后端限制样本数量，
避免配置弹窗演变成第二套运行监控页面。

3 号线切换不能停止其他工作线。各插件实例不得保存可变的“当前工作线、当前任务或当前货架”；运行状态全部来自持久化身份。

## 9. 进程重启与插件升级

选择已部署插件不需要重启服务。Web/Celery 由同一发布制品提供相同插件清单，但在各自进程内构造依赖。

Worker 异常重启时允许存在活动 Epoch：启动阶段验证所有活动 Epoch 的精确插件 binding，随后恢复持久化 evidence 和 claim 扫描。
`DELIVERY_UNKNOWN/RECONCILING` 继续冻结对账；不得因为进程重启换身份重发物理命令。缺失 binding、重复插件键或不完整插件对象
使相关执行 fail closed，并提供明确启动诊断。

升级插件 `X` 前，必须关闭所有仍使用 `X` 旧版本的活动 Epoch。其他版本未变化的插件可保持活动并依赖安全重启恢复。
不支持同一插件多版本并存，也不按 `plugin_key` 回退到最新版。发布门禁必须查询活动 Epoch 并阻止不安全升级。

## 10. SDK 目录收敛

SDK 移至 `src/wes_plugin_sdk/`，保留其独立 `pyproject.toml` 和内部 `src/wes_plugin_sdk/` 标准包布局。
公开安装包名 `wes-plugin-sdk` 与 import `wes_plugin_sdk` 不变，不保留旧路径 shim、软链接或第二导入路径。

这是独立机械切片，需要同步根项目路径依赖、lock、Docker COPY/mount、开发热更新、类型检查、架构门禁、SDK 测试、
release provider 和 HEAVY 精确映射。迁移前后分别验证 wheel 内容与安装导入，并扫描旧路径残留。SDK 不加入数据库、HTTP、
Celery、Repository、operation DTO 或工作线业务流程。

## 11. 失败模式

| 路径 | 生产失败 | 计划内测试 | 用户可见处理 |
| --- | --- | --- | --- |
| 插件选择 | 保存未知或不兼容插件 | API/Service + 前端测试 | 返回具体配置检查项 |
| 设备归属 | Device 通用 CRUD 绕过工作线安全检查 | API/Service + PostgreSQL 并发测试 | 拒绝直接修改，提示从工作线配置操作 |
| 设备争用 | 两条工作线并发绑定同一未绑定设备 | PostgreSQL 双事务测试 | 一个提交，另一个返回稳定归属冲突 |
| 多设备角色 | 同一角色存在多台设备但 binding 假设唯一 | Epoch 约束 + 插件设备选择测试 | 按 `device_role + device_code` 精确选择 |
| ECS 检查 | 一个或多个 Endpoint 不可用、状态过期或能力不匹配 | ECS 合同 + START 测试 | 草稿可保存，启动拒绝并显示失败 Endpoint |
| START | 部署清单缺少插件或 active/Epoch 状态不一致 | START 合同 + PostgreSQL 原子性测试 | 拒绝启动，不修复、不使用默认插件 |
| START 提交 | Epoch 已创建但 WorkLine active 或缓存未同步 | 故障注入 + 缓存测试 | 数据库整笔回滚；提交后才失效缓存 |
| 停用 | 遗漏活动 PickingTask 或其他业务任务 | blocker 矩阵 + 插件合同测试 | 返回对象类型与样本身份 |
| 并发 | 任务准入与停用同时发生 | PostgreSQL 并发测试 | 一个提交，另一个得到稳定阻塞结果 |
| Worker 恢复 | 活动 Epoch binding 缺失 | 真实 Worker 启动测试 | Worker 拒绝处理相关执行并报警 |
| 物理结果 | 旧 Epoch 关闭后收到迟到结果 | WMS/Transport/Device 集成测试 | 持久化并审计，不触发新动作 |
| 多线运行 | 插件对象泄漏某条线的可变状态 | 两线并行 E2E | 当前线失败可诊断，不影响其他线 |
| SDK 迁移 | Docker 或 wheel 仍引用旧路径 | 构建、导入和残留扫描 | 构建门禁直接失败 |

所有失败路径都要求测试及明确错误，不接受静默 fallback；当前计划没有未处理的静默关键缺口。

## 12. 测试覆盖图与验收

```text
CODE PATHS                                             USER FLOWS
[EXISTING] request_id 幂等 + ACTIVE Epoch 唯一           [PLAN→Vitest] 打开业务配置的 loading/empty/error/stale
  ├─ [PLAN→API] 删除独立 activate 合同                    ├─ [PLAN→E2E] 逐台接管设备，归属只读
  ├─ [PLAN→UNIT] Device CRUD 拒绝 work_line_id            ├─ [PLAN→E2E] 保存插件、配置和设备全集
  ├─ [PLAN→PG] WorkLine→Device 升序锁与 replace-all       ├─ [PLAN→E2E] 3 号线人工上架切人工拣料
  │   ├─ 两线争用同一未绑定设备                            ├─ [PLAN→E2E] blocker 阻止停用并可刷新
  │   └─ 任一步失败整笔回滚                                └─ [PLAN→E2E] 切换 3 号线不影响其他线
  ├─ [PLAN→UNIT] role 返回集合，code 精确选择
  ├─ [PLAN→CONTRACT] ECS 按 Endpoint 一次读取
  ├─ [PLAN→PG] START: inactive/no Epoch → active + Epoch
  │   ├─ 同 request_id 重放
  │   ├─ active/Epoch 不一致 fail closed
  │   └─ 事务失败无半完成、提交后失效缓存
  └─ [PLAN→PG] blocker=0 → close Epoch + inactive

当前覆盖：既有 START 幂等和 Epoch 唯一基础可复用；本设计改变的路径均须随对应切片补测。
LLM integration: 不涉及，无 eval。
```

- 核心单元/API：插件对象完整性、Device CRUD 禁写归属、删除 activate 路由与权限、重复/未知 binding、同角色多设备集合、
  `device_code` 精确选择、WorkLine 配置、START 选择与原 Epoch 路由。
- PostgreSQL 集成：配置 replace-all 原子回滚、两线争用同一设备、active/Epoch 不变量、START 原子提交与并发重放、
  完整 blocker 矩阵、PickingTask 业务 blocker、停用关闭 Epoch，以及准入与停用竞争。
- ECS 合同：同 Endpoint 多设备只请求一次，设备缺失、过期、离线、能力不匹配和部分 Endpoint 不可用均阻止 START。
- 真实 Worker：活动 Epoch 安全恢复、精确版本缺失拒绝、已有 evidence 恢复且不盲目重发。
- 插件测试：各插件的业务决策、任务匹配、设备兼容、FIFO/LIFO 和退出义务；不进入核心默认测试集合。
- 前端 Vitest：插件目录、配置表单、设备发现表格、归属只读、active 禁改、乐观锁、阻塞项、重复提交，
  以及删除独立 activate 调用后的生成合同与权限一致性。
- 浏览器 E2E：空闲切换、四线独立运行、3 号线人工上架切人工拣料，以及另一条线不中断。
- SDK：独立 wheel 构建/安装、Docker、路径残留、架构边界与 selector。

当前相反测试 `test_execution_worker_fails_closed_while_an_epoch_is_active` 必须替换为安全恢复回归测试。
聚焦测试、QUALITY、HEAVY、迁移和浏览器 QA 按各切片最终快照运行；供应商一致性、真实设备和业务验收单独记录。

## 13. 实施切片与出口

| 切片 | 范围 | 独立出口 |
| --- | --- | --- |
| A 合同 | 同步 SRS 与四个业务插件身份；冻结已有 WMS wire 和业务所有权 | 文档/合同联合批准；不虚构未批准 operation |
| B 宿主装配 | 单一插件对象、`deployment/plugins.py`、Web/Celery 共享 Runtime | 粗分现有行为不变；真实 Worker 与多 binding 测试通过 |
| C 配置与切换 | WorkLine.plugin_key、列表/预检、完整 blocker、原子停用、Epoch 安全恢复 | 后端 API/迁移/并发/HEAVY 通过，可独立交付宿主标准件 |
| D 前端 | 业务插件选择、配置、检查项、阻塞原因和切换流程 | 合同、权限、Vitest 与浏览器 QA 通过 |
| E 业务插件 | 自动上架/拣料、人工上架/拣料按已批准合同交付 | 每个插件独立业务测试；四线与切换验收通过后才标记可启动 |
| F SDK 目录 | 纯机械迁移及全部路径消费者更新 | wheel、导入、Docker、门禁与 release provider 通过 |

宿主标准件、每个业务插件和 SDK 迁移分别报告状态。某个业务 wire 未批准，只阻止对应插件进入可启动清单，
不阻止已批准的宿主能力或其他插件交付。

### 并行执行

| Lane | 模块 | 依赖 |
| --- | --- | --- |
| A | `src/app/workline`、`src/app/execution`、`deployment`、后端 API | 合同切片 |
| B | `wes_frontend/src/views/admin/worklines` 与前端合同 | A 的 API 合同冻结 |
| C | 各 `workline_plugins/<plugin>` | A 的插件对象合同；各插件之间可并行 |
| D | `src/wes_plugin_sdk`、构建和路径门禁 | 插件对象的 SDK 合同冻结 |

先顺序完成合同与宿主最小接口。随后前端、互不重叠的业务插件和 SDK 迁移可在独立 worktree 并行；
最终合并后统一执行跨仓合同、真实 Worker、四线 E2E 和 selector 选中的 HEAVY。共享生成物及 lock 文件只由一个 lane 更新。

## 14. Implementation Tasks

> **后端实施状态（2026-09-05）：** T1–T5 已在 `codex/workline-plugin-selection-switching` 完成；最终 QUALITY
> `2585 passed, 5 skipped`，selector 选中的 HEAVY `211 passed`，Fresh Review clean。T6–T8 保持独立后续切片。

- [x] **T1 (P1, human: ~1d / CC: ~2h)** — 合同 — 同步 SRS、四个插件身份、WMS/WES 所有权和独立出口
  - Surfaced by: Architecture — 旧人工统一插件约定与目标冲突
  - Files: `docs/architecture/SRS.md`、相关顶层设计与合同文档
  - Verify: 文档引用、operation 和 owner 一致性检查
- [x] **T2 (P1, human: ~2d / CC: ~4h)** — 装配 — 用单一插件对象清单替换粗分专用 Runtime
  - Surfaced by: Code Quality Q1 — Web/Celery 直接导入粗分组合根
  - Files: `deployment/`、`src/register.py`、`src/celery_app/`、`src/app/execution/`
  - Verify: 聚焦 binding/composition 测试及真实 Worker 启动
- [x] **T3 (P1, human: ~2d / CC: ~4h)** — WorkLine — 增加 plugin_key、设备全集配置和原子 START
  - Surfaced by: Architecture D2/D8 and Performance P1
  - Files: `src/app/workline/`、`src/app/device/`、API、migration
  - Verify: Device CRUD 禁写归属、replace-all 与两线争用、role 集合 + code 精确选择、active/Epoch 不变量、
    删除 activate 合同，以及 START 原子提交/回滚/幂等 PostgreSQL 测试
- [x] **T4 (P1, human: ~2d / CC: ~5h)** — 切换 — 将完整 blocker、Epoch 关闭和停用放入同一事务
  - Surfaced by: Architecture D4/D5/D7 — 任一未结束通用或插件业务任务阻止切换
  - Files: WorkLine Service/Repository、Execution/Transport 查询、当前插件 blocker
  - Verify: 每类 blocker 的 count/sample、任务准入与停用竞争、停用原子提交/回滚及缓存失效测试
- [x] **T5 (P1, human: ~2d / CC: ~5h)** — 恢复 — 允许活动 Epoch 下安全重启并增加插件升级门禁
  - Surfaced by: Architecture D3/D6 and Test T1
  - Files: Celery startup、execution recovery、deployment validation
  - Verify: 真实 Worker 重启、缺失版本及禁止盲目重发测试
- [ ] **T6 (P1, human: ~2d / CC: ~5h)** — 前端 — 将粗分配置改为工作线业务插件选择与空闲切换
  - Surfaced by: Design review — 页面职责、状态覆盖、设备发现密度和恢复流程缺口
  - Files: `wes_frontend/src/views/admin/worklines/`、`wes_frontend/src/views/admin/devices/`、API 生成合同和权限
  - Verify: 单弹窗三段结构、显式插件配置目录、设备发现表格、Vitest、合同/权限门禁和浏览器 E2E
- [ ] **T7 (P1, per plugin human: ~3-5d / CC: ~1-2d)** — 业务插件 — 交付四个真实业务插件并复用既有业务实体
  - Surfaced by: Architecture D7 — 各业务域负责自己的任务选择与解释
  - Files: `workline_plugins/` 及已批准的 WMS integration 域
  - Verify: 每插件测试、两线并行及四线业务验收
- [ ] **T8 (P2, human: ~1d / CC: ~3h)** — SDK — 将 SDK 机械迁移到 src 下并更新全部路径消费者
  - Surfaced by: Scope/root convergence
  - Files: `src/wes_plugin_sdk/`、根依赖、Docker、scripts、tests、HEAVY mapping
  - Verify: wheel 构建/安装、导入、Docker、残留扫描和 selector

## 15. 完成标准

- 宿主、业务插件和 SDK 三个出口分别闭合，不互相借用绿灯。
- WorkLine 只保存 `plugin_key`；Epoch 冻结精确版本和运行配置。
- Device 只保存 WES 身份、唯一 WorkLine 归属和拓扑；ECS 硬件/运行信息保持实时只读。
- 设备归属只从工作线业务配置写入；同角色多设备按 `device_role + device_code` 精确选择。
- Device 配置采用全集替换且不支持直接跨线迁移；两线并发争用同一设备时仅一个事务成功。
- 保存草稿不产生部分成功；非重放 START 仅从 inactive/no Epoch 原子设置 active 并创建 Epoch，且不隐式关闭旧 Epoch。
- `WorkLine.is_active=true` 与存在一个活动 Epoch 保持一致；迁移/发布前检查既有数据，不静默修复不一致状态。
- 任一未结束 Transport、BinExecution、MaterialExecution、DeviceCommand、WMS 确认、evidence 或插件业务任务都阻止切换。
- 多条工作线可同时运行相同或不同插件；切换一条线不停止其他独立工作线。
- Web/Celery 不再导入粗分专用组合根，核心和 SDK 不导入具体插件。
- WMS operation 和 Transport 各自保持可靠身份、严格合同及原 Epoch 关联。
- 当前最终快照要求的聚焦测试、QUALITY、HEAVY、迁移、真实 Worker 和浏览器 QA 全部通过。
- 未把 Merge、Deploy、健康检查、Mock 或历史测试结果描述为现场业务验收。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | Running under Codex; nested pass skipped |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | CLEAR | Final delta review: 15 findings folded, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | CLEAR | Score 4/10 → 10/10, 13 decisions |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**VERDICT:** ENG + DESIGN CLEARED — ready for implementation authorization by independent slice.

NO UNRESOLVED DECISIONS
