# 工作线配置控制台设计

## 实施状态

- **状态**：后端阶段已完成，前端配置工作台待在前端仓库接入。
- **完成日期**：2026-05-29。
- **实现分支**：`feature/workline-configuration-console`。
- **实现范围**：
  - 新增 `configuration-status`、`activate`、`deactivate` 后端接口。
  - `WorkLine.is_active` 默认未启用，普通 CRUD 禁止写入启停状态。
  - 激活复用结构化预检，停用前检查未完成运行负载 blocker。
  - 已启用工作线下保护设备拓扑字段变更。
  - `rough_sorter` 配置事实源切换为插件角色和能力，关键角色唯一。
  - 同步 role-driven 配置说明，并将“角色优先设备绑定向导”加入 P2 TODO。
- **验证结果**：
  - `uv run pytest tests/ -q`：`1448 passed, 9 skipped`
  - `uv run ruff format . && uv run ruff check .`：通过
  - `uv run bandit -r src/`：通过
  - `git diff --check`：通过
- **未完成项**：
  - 前端配置工作台、生成链路接入、前端类型检查和 Vitest 尚未在本仓库实施。
  - 未对真实数据库执行 `alembic upgrade head`。

## 背景

当前 `rough_sorter` 插件要求工作线具备 `ROUGH_SORTER_INPUT_ARM`、`ROUGH_SORTER_CONVEYOR`、`ROUGH_SORTER_OUTPUT_ARM` 三类设备角色。后端在普通编辑工作线时立即校验拓扑，导致用户无法先保存 `plugin_key=rough_sorter`，再配置设备。

实际业务流程应是：先创建工作线基础信息，选择插件合同；再绑定设备和角色；最后激活工作线。只有激活后，设备回调和运行时流程才允许进入。

## 目标设计

新增独立的“工作线配置工作台”，作为工作线从草稿到可运行的配置入口。作业线管理列表仍保留，用于搜索、查看、删除和进入配置；完整配置在独立页面完成。

推荐路由：

```text
/admin/worklines/:id/config
```

工作台分为四个区域：

1. 基础信息：工作线编码、名称、区域、类型、运行模式、描述。
2. 插件合同：插件标识、合同版本、必需设备角色、支持事件和命令。
3. 设备拓扑：按插件角色绑定设备，展示缺失角色和已绑定设备。
4. 预检与激活：集中展示配置检查结果，只有全部通过才能激活。

## 后端行为

- `WorkLine.is_active` 表示配置是否生效。新建工作线默认 `is_active=false`。
- 创建和普通编辑工作线只校验基础字段、`plugin_key` 是否存在、`contract_version` 是否匹配，不校验设备拓扑完整性。
- 激活工作线时强校验插件拓扑。`rough_sorter` 激活必须满足三个必需角色均有设备；如设备声明了能力，还要满足 manifest 的事件/命令能力。
- 未激活工作线继续被 `/callback/event` 和 `/callback/result` 拒绝，避免半配置状态进入 runtime。
- 停用工作线应保留配置，不删除设备绑定；停用后回调入口按未激活处理。
- 已激活工作线的拓扑字段受保护。设备更新如果会改变已激活工作线的 `work_line_id`、`device_role`、`is_active` 或 `capabilities_json`，后端必须阻止该变更，提示先停用工作线后再调整拓扑。

推荐新增接口：

```text
GET  /api/v1/workline/work_lines/{id}/configuration-status
POST /api/v1/workline/work_lines/{id}/activate
POST /api/v1/workline/work_lines/{id}/deactivate
```

`configuration-status` 返回工作线基础信息、插件合同、设备角色覆盖情况、预检项和 `can_activate`。激活接口复用同一套预检逻辑，预检失败时返回明确的缺失项。

## 前端行为

- 作业线列表新增“配置”入口，进入独立配置工作台；“是否激活”在列表和详情中作为状态展示，不再作为普通表单开关。
- 工作台顶部展示工作线当前状态：未激活、可激活、已激活、预检失败。
- 设备拓扑区按插件角色展示卡片：角色名、数量要求、已绑定设备、缺失原因。
- 设备管理页仍展示 `work_line_id`、`device_role`、`is_active` 和 `capabilities_json`，但对已激活工作线的设备应禁用会影响拓扑的编辑，并引导用户进入配置工作台先停用再调整。
- 工作台提供角色视角的汇总、绑定入口和跳转，不在本次重写设备 CRUD 的全部字段体验。
- 激活按钮只在 `can_activate=true` 时可用；失败时展示后端返回的预检项，而不是让用户从通用错误信息里猜原因。

## 数据流

```text
工作线列表
  -> 打开配置工作台
  -> 加载 WorkLine + configuration-status
  -> 编辑基础信息 / 插件合同
  -> 绑定设备角色
  -> 刷新预检
  -> activate
  -> is_active=true
  -> 运行看板 / callback runtime 可进入
```

## 错误和边界

- 未配置插件：允许保存，预检显示“未选择插件”，不可激活。
- 插件合同版本不匹配：普通保存应失败，提示合同版本必须来自 manifest。
- 缺少设备角色：普通保存允许，预检失败，激活失败。
- 已激活工作线修改插件：要求先停用再修改，避免运行中合同切换。
- 已激活工作线解绑关键设备、修改设备角色、停用设备或修改能力声明：后端阻止保存，并返回“请先停用工作线再调整设备拓扑”。

## 测试要求

后端：

- 新建工作线默认未激活。
- 未激活工作线可保存 `plugin_key=rough_sorter`，即使没有设备。
- 激活缺少 `ROUGH_SORTER_INPUT_ARM` 时失败，并返回缺失项。
- 三个 rough sorter 角色齐全时激活成功。
- 已激活工作线的回调仍可进入，未激活工作线回调被拒绝。
- 已激活工作线下修改关键设备拓扑字段失败；停用后同样修改允许保存。

前端：

- 作业线列表能进入配置工作台。
- 配置工作台能展示未激活、缺角色、可激活、已激活状态。
- 激活按钮根据 `can_activate` 禁用或启用。
- 激活失败时展示预检缺失项。
- 设备管理页对已激活工作线的拓扑影响字段展示禁用提示，引导用户去配置工作台停用后修改。

## 明确不做

- 不把运行态 Trace、Sandbox 和配置控制台合并。
- 不重写设备 CRUD；本次只增加工作线侧的角色视角汇总。
- 不为插件引入一套新的动态配置 DSL；仍以现有 manifest 为事实来源。
- 不在作业线列表逐行请求 `configuration-status`；列表只展示已有激活状态和配置入口。
- 不用物理拓扑图作为配置事实源；本次按插件角色与设备能力做 role-driven 配置。
- 不新建独立视觉体系；配置台复用前端现有工业仓储设计系统，仅增强工作台布局。

## 设计评审补充

### 用户与设计系统

- 主要用户：现场运维/配置人员。页面文案优先回答“能否启用、缺什么、下一步去哪修”。
- 复用前端 `DESIGN.md` 的工业仓储风格：深色控制台、琥珀信号色、交通灯语义色、Inter + JetBrains Mono。
- 复用现有后台模式：作业线列表仍使用 CRUD 页面；配置台使用同一 app shell、路由、权限、Element Plus 表单/按钮/标签。
- 自定义只限三个工作台能力：状态轨、角色覆盖清单、sticky 预检/启停面板。

### 页面结构

推荐信息架构：

```text
作业线列表
  └─ 配置入口
      └─ 配置工作台 /admin/worklines/:id/config
          ├─ 顶部状态轨：当前状态 / 下一步 / version / 刷新
          ├─ 主体左侧：基础信息、插件合同、角色覆盖清单
          └─ 右侧 sticky 面板：预检摘要、blocker、activate/deactivate
```

首屏优先级：

1. 当前配置状态与下一步动作。
2. 阻止启用/停用的 blocker。
3. 基础信息、插件合同和设备角色详情。

角色覆盖区采用紧凑清单，不做装饰性大卡片网格：

```text
角色                 要求       已绑定设备          能力状态      操作
INPUT_ARM            1/1        ARM-01              通过          查看设备
CONVEYOR             0/1        -                   缺失          去绑定
OUTPUT_ARM           2/1        ARM-02, ARM-03      重复          调整
```

### 状态矩阵

| 区域 | Loading | Empty | Error | Success | Partial |
|------|---------|-------|-------|---------|---------|
| 基础信息 | 骨架行，不显示空白表单 | 工作线不存在时返回列表入口 | 加载失败，显示重试 | 显示编码、名称、类型、运行模式 | version 过期时提示刷新 |
| 插件合同 | 插件信息骨架 | 未选择插件，提示先选择插件 | 合同版本不匹配，提示重新选择 | 显示角色要求、事件、命令 | 插件合法但缺设备时进入预检 blocker |
| 角色清单 | 行骨架 | 无设备绑定，显示“去设备管理绑定角色” | 设备加载失败，保留重试 | 每个角色显示通过状态 | 缺失、重复、能力不匹配逐行标记 |
| 预检面板 | 禁用启停按钮并显示检查中 | 无插件/无设备时显示配置引导 | API 失败显示重试，不允许启用 | `can_activate=true` 时显示可启用 | 有 blocker 时按 severity 排序展示 |
| 启用/停用 | 按钮 loading，防重复点击 | 不适用 | 结构化错误进入 blocker 列表 | 成功后刷新状态轨和预检 | 停用有未完成负载时显示 blocker 摘要 |

### 用户旅程

```text
STEP | 用户动作                         | 用户感受                 | 页面支撑
-----|----------------------------------|--------------------------|-------------------------------
1    | 从作业线列表进入配置              | 想快速知道是否可运行      | 顶部状态轨直接显示当前状态
2    | 选择插件合同并保存草稿            | 不希望被设备缺失卡住      | 草稿保存不跑拓扑强校验
3    | 查看角色覆盖清单                  | 想知道差哪个设备          | 按角色逐行显示缺失/重复/能力
4    | 修复设备角色后刷新预检            | 需要确认是否已达标        | sticky 预检面板持续可见
5    | 点击启用                          | 需要理解生产影响          | 摘要确认弹窗展示检查结果
6    | 停用并调整拓扑                    | 担心影响运行中负载        | blocker 摘要先拦截未完成负载
```

### 启停确认

- `activate`：只在 `can_activate=true` 时可点击；点击后弹出摘要确认，展示工作线、插件合同、角色检查通过数和“启用后设备回调将进入运行时”。
- `deactivate`：点击前先刷新 blocker；无未完成负载时弹出摘要确认，展示“停用后回调将按未激活拒绝，配置保留”。
- 有 blocker 时不弹无效确认，直接在预检面板展示 blocker 类型、数量、可修复动作。

### 响应式与可访问性

- 桌面：主体两栏，右侧预检/启停面板 sticky。
- 平板：预检面板置于主体顶部或可折叠，不遮挡角色清单。
- 移动端：单栏布局，启停操作进入底部固定操作区；触控目标不小于 44px。
- 键盘顺序：状态轨 → 基础信息 → 插件合同 → 角色清单 → 预检面板 → 启停确认。
- 错误与 blocker 区使用 `aria-live`；启停失败后焦点移动到 blocker 列表。
- 禁用按钮和禁用字段必须有可读原因，不能只靠颜色表达。

### 后续 TODO 候选

- 角色优先的设备绑定向导：当前只提供角色视角汇总和跳转，完整绑定/换绑向导作为后续 P2 项。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | - | not run |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | - | not run |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | CLEAR | 16 issues resolved into plan decisions, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | CLEAR | score: 5/10 -> 9/10, 7 decisions |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | - | not run |

- **UNRESOLVED:** 0
- **VERDICT:** 后端阶段已实现并完成本地验证；前端配置工作台待在前端仓库继续实施。
