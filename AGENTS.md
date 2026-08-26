DO NOT send optional commentary

# WES Backend Agent Rules

> 本文件是本项目跨工具规则的主真源。目标是在不降低架构、合同和交付质量的前提下，让 Agent 按变更风险选择最小充分流程，避免重复 Review、重复测试和无效 HEAVY。

## 1. 规则优先级与按需加载

平台的 system / developer 安全与运行规则始终高于仓库规则。仓库内同一事项冲突时依次遵循：用户当前指令 → 本文件的项目硬规则 → 当前工具入口的执行差异 → 被本文件引用的专项文档。

| 工具 | 入口 | 边界 |
| --- | --- | --- |
| Codex / 通用 Agent | `AGENTS.md` | 项目硬规则主真源 |
| Claude Code / GStack | `CLAUDE.md` | Skill routing 和平台行为，项目事实以本文件为准 |
| AGY / Antigravity / Gemini | `GEMINI.md`、`.agents/rules/` | 平台所需执行视图，只保留必要摘录，项目事实以本文件为准 |

若未来在子目录增加 `AGENTS.md`，根规则与目录规则共同生效；只在同一事项冲突时以距离目标文件最近的规则为准。目录规则只写作用域差异，不能复制根规则形成分叉。

本文件只保留高频硬约束。任务触及相应范围时再读取专项文档，不要预加载所有上下文：

- 测试目录、所有权、FAST/HEAVY 边界：`tests/README.md`
- 分层、Service 调用、时区、导出细节：`.claude/context/rules.md`
- 新模块和 Zero-Code CRUD 示例：`.claude/context/howto.md`
- 架构与需求真源：`docs/architecture/SRS.md`、`docs/architecture/file_index.md`
- HEAVY 映射真源：`docs/architecture/heavy-test-impact.toml`
- 架构补充：`.claude/context/architecture.md`

维护本文件时应保持精炼，目标不超过 `24 KiB`，禁止重新粘贴上述专项文档、Skill 全文或自动生成的工具说明。

## 2. 基本工作方式

- 使用中文沟通、写文档和 Commit Comment；代码标识符、路径、字段名、状态值和协议字面量保持原文。
- 先确认任务类型和成功标准，再选择 Skill、测试与交付流程。不要把所有任务都套入同一套重流程。
- 坚持 KISS、YAGNI 和手术式修改：每个变更行都应能追溯到用户目标，不顺手重构、格式化或清理无关代码。
- 保留有价值的业务注释、设计理由和 `TODO` / `FIXME` / `HACK`；行为变化时同步更新对应注释。
- 只读 Review、诊断、实施、Commit、Push、创建 PR、Merge、Deploy 是不同授权。前一步授权不得自动扩大到后一步。
- 项目命令统一使用 `uv run ...`，不要依赖其它 Shell 已激活的虚拟环境。
- 开始写操作前检查 `git status --short`，保护用户已有的 staged、unstaged、untracked 和其他 worktree 现场。
- 不使用 `--no-verify` 绕过质量门禁；不使用破坏性 Git 或目录清理命令处理不属于本任务的内容。

## 3. Skill 与流程路由

### 3.1 通用原则

- 用户点名的 Skill 必须使用；未点名时只选择覆盖任务所需的最小 Skill 集合。
- Skill 提供通用能力，本文件定义项目边界。Skill 的通用模板与本项目冲突时，保留 Skill 的核心方法，但按本文件裁剪测试、QA、提交、部署和 Subagent 行为；不要修改本机 Skill 来解决项目差异。
- 不要机械串联多个含义重叠的 Skill。一次交付只保留一个主要实施流程、一个主要 Review 流程和一个完成验证入口。
- GStack 保持其默认自动升级行为，本项目不增加单独开关或升级步骤。

### 3.2 推荐路由

| 任务 | 首选能力 | 项目约束 |
| --- | --- | --- |
| 需求含糊、需要设计功能 | `superpowers:brainstorming`，必要时 `writing-plans` | 已批准计划、纯文档、规则或元数据调整不重复设计 |
| 已批准的 WES 实施 | `wes-implementation` | Agent 自主判定大型/高风险或小型/低风险；最终快照取最高风险 |
| Bug / 异常 | `systematic-debugging` 或 GStack `investigate` | 先定位根因，再决定是否实施 |
| 代码 Review | GStack `review` 或 `requesting-code-review` | 二选一作为主 Review，不双重完整审查 |
| 接收 Review 意见 | `receiving-code-review` | 先验证意见，再实施；不因意见来源而盲从 |
| 完成验证 | `verification-before-completion` | 只刷新被后续变更失效的证据 |
| Ship | GStack `ship` | 复用当前变更快照的有效证据，不重复完整审计 |
| Merge / Deploy | GStack `land-and-deploy` | 无部署能力或用户说明 GitHub-only 时只 Land，不臆造部署 |
| 浏览器 QA | GStack `qa` / `qa-only` | 仅前端或真实浏览器行为；后端不默认启动浏览器 QA |

### 3.3 Subagent 协议

Subagent 仅用于用户、Skill 或批准计划明确要求的独立、边界清晰且无共享写状态任务；短任务不为形式拆分。

- 主 Agent 对变更和证据负责；同一文件或执行路径只有一个实施 owner。
- Review Subagent 默认只读，不运行 QUALITY、HEAVY、迁移、Docker、Celery 或部署；输入限于固定 base/head/scope、diff manifest、合同要点、变更符号、测试/HEAVY owner 和已有证据。
- 实施 Subagent 只跑任务聚焦测试，不跑全量门禁；回传命令、结果和变更快照。
- 同轮先做一次完整 Review；后续闭环与 fresh Review 按 8.1 合并，不由 Subagent 加轮次。
- QUALITY、HEAVY 和迁移由主 Agent 按失效矩阵执行；Reviewer 不重复运行。
- Subagent 只返回结论和精确证据，不贴长日志；等待期间主 Agent 不重复 full Review 或改同一路径，证据不足、快照变化或结果可疑时才补跑。

### 3.4 已批准计划的实施协议

计划获批即进入 **Execution Lock**，以计划及其当前合同为执行真源；除非实际矛盾、缺失决策或变更面扩大，不重新设计、重读原始需求/
同义 Skill 或逐文件重论证。

首个生产代码补丁前必须输出并冻结变更面清单：生产符号/调用点、共享 helper 消费者、规范化/摘要身份、各消费者域测试/fixture、
HEAVY mapping、migration/生成物/索引归档、验证和无关 dirty 指纹。随后批量完成影响分析和必要确认；同一 base、符号和意图不重复查询。用户对本任务的
HIGH / CRITICAL 影响链作出范围授权后，清单内相同风险不再次暂停；出现清单外的新高风险影响时仍须报告。

流程按当前内聚切片推进：

- 同一内聚切片可含多个共享路径、风险和测试 owner 的验收点。大型/高风险按 TDD；小型/低风险复用现有测试或替代验证。
- 公共签名、字段或测试夹具发生机械传播时，先列全调用点，再做一次受控迁移、一次旧符号残留扫描和一次领域回归；禁止依靠整目录失败逐个发现调用点。
- 可独立的读取、影响分析和 FAST 测试应批量或并行执行；已知签名传播尚未完成时，不反复运行整目录测试。
- 修改测试期望前先把失败归类为合同变化、机械传播遗漏、环境/fixture 缺失或真实回归。只有合同证据支持时才能改变可观察语义；不得用放宽断言掩盖后三类问题。
- 最终门禁前按生产模块或机器合同闭合测试所有权：用 GitNexus tests 或精确 `rg` 枚举直接测试、fixture/helper 间接消费者、QA/回归和 HEAVY mapping；未闭合不得把聚焦测试或 QUALITY 称为最终证据。
- 解析器、规范化或摘要算法还须验证复杂度受输入长度约束：长度/exponent 边界与零值短路先于数值规模驱动的循环、幂或分配；用极小恶意输入做硬超时回归。

上下文读取面向当前决策：优先精确 `rg`、符号 context 和函数级切片，避免重叠。除 Skill 必读资源外，单批默认不超过 3 个文件、
300 行或约 12K 字符，超出先缩窄；无关 dirty 只记录路径、stat 和指纹，除非与目标重叠，禁止读取完整 diff。

## 4. 架构与领域红线

### 4.1 分层

严格遵守：

```text
API → Service → Repository → Database
```

禁止 API 直接访问数据库、直接调用 Repository，禁止任意跨层直连。新增 Service 必须在对应 `__init__.py` 导出。

### 4.2 模型与时间

- `EnterpriseMixin` 已包含审计和乐观锁能力，不得重复继承 `AuditMixin` / `OptimisticLockMixin`。
- 数据库存储使用 `timezone.now_for_db()`；API 时间使用 `timezone.now_utc().isoformat()`；时间戳使用 `timezone.now_utc().timestamp()`。
- 禁止对 naive datetime 调用 `.timestamp()`。

### 4.3 系统所有权

- WMS 拥有业务单据、库存、分配和全局位置；WES 拥有可靠的本地执行；ECS 拥有物理事实与设备结果。
- `TransportTask` 与 `DeviceCommand` 是并行概念，不能用一个替代另一个。
- 本项目尚未发布；除用户明确要求外，不新增 v2、别名、shim、双路径、兼容 wrapper、迁移式兼容或 no-op consumer。目标合同直接替换旧合同。
- WMS 北向业务 ACL 仅位于 `src/app/wms_adapter/`；设备供应商私有协议和实现不进入 WES 核心仓库。

修改架构、共享合同或所有权边界前，必须读取对应架构/合同文档，不以历史测试为当前合同证据。

## 5. 变更分类与 TDD 边界

修改前检查实现、现有测试和影响；按 `wes-implementation` 判定风险，未知按高风险，最终快照取最高风险。

| 类型 | 是否强制 TDD | 最小验证 |
| --- | --- | --- |
| 大型/高风险功能或 Bug | 是 | RED → DEV → GREEN，必要时小范围重构 |
| 小型/低风险功能、Bug 或重构 | 否 | 复用现有测试；无合适测试时用可靠替代验证 |
| 测试治理、脚本、配置、合同自身 | 否；若改变运行时可观察行为，其实现部分按功能变更 | 聚焦验证可执行行为或解析结果 |
| 纯文档、注释、规则、Release 元数据 | 否，且不得硬套 TDD | 文档或元数据相称检查 |

纯人类可读文档、规则和 Skill 不走代码式 RED/DEV/GREEN；不得为正文、标题、路径或状态措辞创建/修改 pytest。

纯文档是人类阅读材料且不改变生产、测试工具、机器配置或可执行合同；`docs/` 下被程序/CI 读取的 TOML/CSV/YAML/JSON 仍是配置或合同。

纯文档只做审阅、格式、引用/链接、路径、大小、结构 validator 和 diff 检查；不预支后续 Review、Commit 或 Deploy 成本。规则场景演练不要求先失败且不算 TDD。

## 6. 测试所有权与 HEAVY

详细目录规则以 `tests/README.md` 为准；以下为硬约束：

- 默认 FAST 测试不得依赖真实数据库、HTTP、Celery、Redis 或容器。
- 禁止在 `tests/` 根目录新增 `test_*.py`；integration、e2e、resilience、load、mock 等重测试必须显式运行。
- 同一行为只能有一个主要测试所有者。删除测试前先建立并通过承接测试；删除人类文档内容测试可标记 `NONE`。
- 具体工作线/插件测试位于 `workline_plugins/<plugin_key>/tests/`，不进入核心默认 pytest、覆盖率、QUALITY 或 HEAVY selector。
- 供应商一致性验收在供应商 ECS/网关边界运行，不在核心仓库创建私有 `device_adapters/` 测试包。
- WMS Adapter 的 FAST 合同位于 `tests/contracts/wms_adapter/`；真实持久化/事务测试位于 `tests/integration/wms_adapter/`，不得借此证明通用传输层或 WES 最小内核。
- integration 命令只有在所需数据库、Redis、worker 和显式启用条件就绪时才执行；因环境未启用而 `skipped` 不属于通过证据，也不应为了得到 skip 结果先跑一轮名义集成测试。

`docs/architecture/heavy-test-impact.toml` 是 HEAVY 映射唯一真源：

- 新增可能影响运行时的生产模块、迁移、基础设施配置或 HEAVY 资产时，必须增加精确 mapping。
- 只有经评审确认无 HEAVY 影响时才允许 `heavy_tests = []`；未知风险必须 fail closed，不能用臆造映射掩盖。
- 新增或移动 HEAVY 测试时同步更新全部引用。
- 本地开发按差异所在区域选择 `unstaged` 或 `staged` scope；最终提交快照使用 `staged`。CI 使用 `--base origin/${CI_TARGET_BRANCH}`；真实 HEAVY 不进入本地 pre-commit QUALITY。

常用验证命令：

```bash
# 聚焦测试
uv run pytest <changed-test-files-or-domain> -q

# 测试拓扑变化
uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
uv run pytest --collect-only -q -o addopts='' | tail -5

# 与 pre-commit 一致的 QUALITY
./scripts/git-quality-gate.sh --profile quality

# 未暂存开发差异的 HEAVY 选择与执行
uv run scripts/select_heavy_tests.py --scope unstaged
./scripts/run_selected_heavy_local.sh --scope unstaged

# 最终已暂存快照的 HEAVY 选择与执行
uv run scripts/select_heavy_tests.py --scope staged
./scripts/run_selected_heavy_local.sh --scope staged

# Release 元数据轻量门禁
./scripts/git-quality-gate.sh --check release-metadata
```

## 7. 验证证据与失效规则

验证强度由变更面决定，不由流程名称决定。有效证据不重复运行；仅在后续变化触及其覆盖面时失效。证据记录命令、结果及对应 diff/HEAD 或工作树快照。

| 后续变化 | 需要刷新 | 不需要刷新 |
| --- | --- | --- |
| 仅人类文档、PR 文案 | 文档相称检查 | QUALITY、HEAVY、迁移 |
| 仅 `VERSION` / `CHANGELOG` 等 Release 元数据 | release-metadata 门禁 | QUALITY、HEAVY、迁移 |
| 测试、selector、测试资产 | 聚焦测试、selector 合同；按 manifest 决定 HEAVY | 无关迁移 |
| 工具脚本、机器可读配置或可执行合同 | 对应聚焦验证、按影响刷新 QUALITY/HEAVY | 无关门禁 |
| 生产代码 | 小型/低风险做定向验证；大型/高风险加必要 QUALITY/HEAVY | 无关 HEAVY |
| Migration / schema | 迁移链、新鲜临时库或等价验证、受影响 HEAVY | 无关浏览器 QA |
| 前端交互 | 聚焦测试和浏览器 QA | 后端无关 HEAVY |

执行规则：

- 完整 QUALITY、同组 HEAVY 和同一迁移链在同一有效快照各运行一次；失败无证据，聚焦通过不替代最终必选门禁。
- 小型/低风险未授权 Commit/PR/Merge 时可止于 `IMPLEMENTED - FOCUSED VERIFIED`；出现意外影响须先重新分类，不得自行扩面。
- 已授权 Commit 时先做聚焦验证，由 hook 产生完整 QUALITY。hook 失败后单独复现失败阶段，修复并确认 staged fingerprint 和 Git 元数据未漂移再重试；不得用反复 Commit 诊断。
- 可执行树（生产、测试、脚本、配置、迁移）或验证环境变化会使相关证据失效；仅创建 Commit 不会自动失效。纯文档或 Release 元数据变化不会使 QUALITY、HEAVY、迁移证据失效。
- HEAVY 只执行 selector 输出的 manifest；`NONE` 是有效结果。不得把全量 HEAVY 当作默认安心检查。
- 失败后先重跑失败项或受修复影响集合做诊断；生产、测试、脚本、配置或环境修复完成后，最终快照必须重新完整通过被失效的必选门禁。未变化的绿色快照不重复全量。
- 多组互不写共享状态的 FAST 测试可并行运行。公共接口传播完成后再运行一次对应领域集；此前只运行当前行为切片，避免用失败列表代替调用点清单。
- Migration / schema 任务复用一个独占临时 PostgreSQL 实例及 readiness，不复用污染的逻辑库状态。autogenerate、迁移链、integration/HEAVY 使用独立数据库或重建 schema；最终至少在干净逻辑库上完成规定 base 到 head。不得使用共享 dev 数据库。
- 不能把未绑定当前可执行树 fingerprint 和验证环境的历史绿灯描述为 fresh 证据。不同分支、worktree 或 Commit 只有在受验证内容 fingerprint 与环境一致时才可复用；纯文档或 Release 元数据提交不改变该 fingerprint。

## 8. Review、QA 与 Ship

### 8.1 Review

- 开始前固定 base、head 和 staged/unstaged/untracked 范围；Review 前闭合适用的 3.4 清单、聚焦验证和 `git diff --check`，不得把 Reviewer 当遗漏清单生成器。只读 Review 不写文件、不提交、不推送。
- Reviewer 从 diff manifest、生产符号和合同缩窄读取，独立枚举直接/间接测试、QA/回归及 HEAVY mapping；不得凭实施者绿灯清单宣布闭合，也不重复其测试。
- 首轮做完整 Review。生产代码、机器合同或运行时配置修复后，原 Reviewer 用一轮评审同时完成旧意见闭环和当前 diff 的 fresh full Review，禁止拆成两轮。
- 只有断言语义、测试所有者和可观察行为均不变，且残留扫描闭合时，fixture/期望/调用点传播才算机械变更并做定向 Review；否则升级为完整 Review。纯文档或 Release 元数据只核对对应 diff。
- Review 结论必须区分代码证据、测试证据、运行时证据和部署/业务验收，不能互相替代。

### 8.2 后端 QA 路由

- API 路由变化：优先 ASGI/HTTP 合同测试；仅在需要真实进程或中间件行为时启动服务。
- Service、Repository、模型、迁移：使用聚焦单元/集成/迁移验证，不启动浏览器。
- Celery 注册、队列路由、序列化、重试或 worker/定时 wiring 变化必须使用真实 worker；纯 handler/service 逻辑按风险决定，并在 PR 中注明边界。
- 仅前端页面或明确的端到端浏览器场景使用 GStack 浏览器 QA。

### 8.3 Ship

推荐顺序：

1. 固定最终代码/测试快照并完成聚焦验证。
2. 完成唯一一次主 Review 与反馈闭环。
3. 对该快照运行 QUALITY、selector 选中的 HEAVY 和必要迁移验证。
4. 最后修改 `VERSION`、`CHANGELOG`、PR 文案等 Release 元数据。
5. 只运行 release-metadata 门禁并复用第 3 步证据。
6. Commit、Push、PR、Merge、Deploy 分别确认授权和结果。

PR 描述保持精炼，只包含行为变化、合同/配置/迁移影响、验证命令与结果、未验证边界。除非用户明确要求，不做全仓文档盘点，不为消灭零散 TODO 扩大变更范围。

GitHub-only、无需部署或仓库没有部署能力时，Merge 后报告 `MERGED — NOT DEPLOYED`。DDL 后重建所有访问受影响关系的长期进程，清除连接池/prepared statement 缓存，以迁移后会话和真实查询验收；`/health`、`/ready` 不足以证明数据库路径可用。

## 9. Git、分支与 Worktree

- 日常 base 为 `develop`；feature/fix/chore 分支默认从更新后的 `develop` 创建，PR 默认指向 `develop`。
- 默认直接工作；仅当路径重叠、共享生成物、写入范围不可控、需独立运行环境或保留长线现场时使用 worktree。
- 后端主仓库：`/Users/kaizhou/codeDev/wes_backend`；worktree 根目录：`/Users/kaizhou/codeDev/wes_backend-worktrees`。
- worktree 名使用 branch slug（`/` 替换为 `-`），并独立维护 `.env`、`.venv`、`.pytest_cache` 和 hooks；不得复用其它 worktree 的本地状态。
- 主工作区未提交的 `AGENTS.md` 不会自动进入既有 worktree。新任务在创建/同步后确认版本；任务中途不追随普通规则变化，新增安全/质量红线则暂停、报告并重新确认基线。
- 新 worktree 先运行 `./scripts/init-env.sh dev`、`uv sync --dev`，需要提交门禁时运行 `./scripts/install-git-hooks.sh`。
- `pyproject.toml`、`uv.lock` 或环境 profile 变化后重新初始化环境和依赖。
- 创建临时仓库的测试或工具必须从子进程环境中删除 `git rev-parse --local-env-vars` 返回的全部变量，不得只维护单个变量白名单。
- Commit、hook 和嵌套 Git 测试前后核对 worktree 判定、`core.bare`、`.git/config`、index 和 status 指纹；发生漂移时，只恢复本次工具造成的 Git 元数据变化，再继续实施。
- Commit 使用 Conventional Commits，主题简洁、中文，schema 变化必须说明 migration。Commit 与 Push 是两次独立授权。

## 10. GitNexus 与影响分析

本项目使用 GitNexus 导航和控制符号级变更风险：

- 修改生产代码中的函数、类或方法前，必须对计划内目标符号运行 upstream impact analysis，并查看直接调用者、执行流程和风险级别；同一批次应并行查询并缓存结果。
- HIGH / CRITICAL 风险必须在修改前向用户说明影响范围并确认；LOW / MEDIUM 可按既定任务继续。任务范围授权和重新确认规则见 3.4。
- 探索陌生代码优先按概念查询执行流程；需要完整上下文时查询 symbol context。
- 生产符号重命名使用图谱感知的 rename。协议字段、测试数据和字符串字面量使用限定路径的机械替换，并以旧值残留扫描和聚焦测试收口；禁止无边界全仓替换。
- Worktree 中先运行 `npx gitnexus status`；仅在索引 stale 时运行一次 `npx gitnexus analyze`。Commit 前固定使用 `npx gitnexus detect-changes --scope staged --repo "$PWD"`，不临时探索 `--help` 或其它变体。
- `analyze` 可能改写 Agent 入口；执行前后对比 `AGENTS.md` 和 `CLAUDE.md`，只恢复工具生成的超范围变化。若与用户现有变更重叠，停止并报告，不得覆盖。工具不可用时，明确降级为 `rg`、调用点、测试和 diff。

测试函数、测试 fixture、测试辅助、文档、规则和一次性工具脚本默认不做逐符号 impact；使用测试所有权、精确调用点、聚焦测试和 diff
评估。共享测试基础设施或 CI 工具存在广泛消费者时才升级为符号影响分析。Commit 前的变更范围检查仍适用。

QUALITY、HEAVY、Commit hook 和 GitNexus 等长输出默认使用 RTK 摘要；失败时只对失败阶段使用 `rtk proxy <cmd>` 展开原始日志，不重新读取全链路输出。需要评估节省时使用 `rtk gain`。

## 11. 项目命令与配置

```bash
./scripts/dev-env.sh up
./scripts/dev-env.sh check
./scripts/dev-env.sh logs api celery frontend
./scripts/dev-env.sh down
uv run ruff format --check .
uv run ruff check .
uv run bandit -r src/
```

- 本机完整前后端调试以 `scripts/dev-env.sh` 和 `docs/devops/local-development-environment.md` 为唯一入口；编排归后端仓库所有，不复用前端独立 Compose。
- `up` 必须完成迁移、幂等基础数据初始化和健康检查；后端核心/SDK/插件与前端 Vite 均支持代码热更新。依赖或镜像输入变化仍须重建。
- `down` 不得携带 `-v`/`--volumes`；初始化数据不得伪造 WorkLine、库存、设备或 Transport 业务事实。本机 Mock/健康检查不等于真实联调或业务验收。
- 新 Alembic migration 必须由 `uv run alembic revision -m "<message>"` 或仓库 wrapper 生成随机 revision，再编辑生成文件；不得手写模板式 revision ID。
- `.env.dev`、`.env.test`、`.env.prod` 生成运行时 `.env`，不得提交 secrets。
- Python 目标版本为 3.13；Ruff 使用双引号、120 字符行宽和项目既有规则。

## 12. 文档规划与归档

- 计划文档表达目标、架构决策、业务约定、任务边界、验收标准、风险和验证方式；禁止粘贴完整类/函数/大段测试或把计划写成可执行脚本。
- 归档文档必须移出项目，统一放到 `../archive_docs/wes_backend/`；项目内不保留副本、占位、软链接或转发文档。
- 归档前更新当前态引用并确认目标不存在；保留原文件名和完整内容，不覆盖重名文件。
- `docs/hardware/` 的厂商原始协议和联调资料是外部输入，不按历史设计归档，也不能替代 WES 核心架构合同或能力测试。

## 13. 完成标准

小型/低风险可报告 `IMPLEMENTED - FOCUSED VERIFIED`，但不是 `MERGE READY` 或现场验收。各层级完成还须：

- diff 只包含授权范围，用户原有变更未被覆盖；
- 架构、合同、测试所有权和注释规则得到满足；
- 当前最终快照要求的必选门禁均已通过；必选门禁被阻塞只能报告未完成，非必选或未触发验证须说明边界；
- GitNexus 影响与变更范围检查按触发条件完成；
- 未把 Merge、Deploy、健康检查或历史绿灯夸大为业务验收；
- 未在无授权情况下 Commit、Push、创建 PR、Merge 或 Deploy。
