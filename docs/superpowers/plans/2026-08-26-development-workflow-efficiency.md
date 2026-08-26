# 前后端开发流程效率优化 Implementation Plan

> **For agentic workers:** 按独立切片执行。不得因为计划包含多个 Task 就自动创建 Subagent 或 worktree；不得重复运行仍然有效的验证证据。

**Goal:** 在不降低最终质量门槛的前提下，删除重复验证、纠正 worktree 和纯文档路由，并清理失效 HEAVY 所有权。

**Architecture:** 默认在当前 checkout 直接工作，仅在路径重叠、共享生成物、写入范围不可控或需要独立运行环境时隔离。每个仓库和切片独立交付；实施阶段运行聚焦验证，Commit hook 与 CI 按当前强制能力承担最终门禁。

**Tech Stack:** Bash、Python 3.13、pytest、Git hooks、Jenkins、pnpm 10、Vitest、Markdown。

**Spec:** `docs/superpowers/specs/2026-08-26-development-workflow-optimization-design.md`

## Global Constraints

- 每个 Task 开始前检查目标仓库 `git status --short`，记录目标路径是否与 staged、unstaged 或 untracked 现场重叠。
- 非重叠小修改直接工作并使用精确路径；禁止 `git add .` 和 `git add -A`。
- 目标路径重叠时先确认 owner：当前任务或用户明确保留的修改继续在当前 checkout 续作；其它活动任务先协调，确需并行才隔离；owner 不明则暂停写入。不得创建 worktree 后遗漏当前任务依赖的 dirty 内容。
- 宽验证只有在当前交付必选且无法隔离时才触发 worktree；由候选 Commit 的 CI 权威承担时，本地保留聚焦证据即可。
- 验证命令、结果和对应快照只记录一次；后续变化未触及覆盖面时直接复用。
- 纯文档、规则和 Skill 不走代码式 RED/DEV/GREEN，也不创建文案测试。
- Commit、Push、PR、Merge 和 Deploy 分别授权；本计划不隐含任何 Git 或外部发布授权。
- 前后端 Task 可以独立批准、独立提交和独立进入 CI，不建立成对 Commit 或跨仓同步门槛。

---

### Task 1: 删除前端重复类型检查

**Classification:** 小型/低风险；调整现有测试，不新建测试文件。

**Files:**

- Modify: `/Users/kaizhou/codeDev/wes_frontend/package.json`
- Modify: `/Users/kaizhou/codeDev/wes_frontend/tests/unit/scripts/quality-gates.test.ts`
- Modify only if wording is missing: `/Users/kaizhou/codeDev/wes_frontend/AGENTS.md`

**Success:** `check -> lint -> lint:all`，整条命令链只调用一次 `type:check`。

- [ ] **Step 1: 冻结目标路径和现有命令链**

```bash
git -C /Users/kaizhou/codeDev/wes_frontend status --short
node -e 'const p=require("/Users/kaizhou/codeDev/wes_frontend/package.json"); console.log(p.scripts.check, p.scripts.lint, p.scripts["lint:all"])'
```

目标路径与现有修改重叠时先按 Global Constraints 确认 owner；属于本 Task 时继续手术式修改，属于其它活动任务时协调或隔离，owner 不明时才停止。不得只因其它 dirty 文件创建 worktree。

- [ ] **Step 2: 调整现有断言和命令别名**

在既有 `quality-gates.test.ts` 中断言 `check` 等于 `pnpm run lint`、`lint` 等于 `pnpm run lint:all`，且只有 `lint:all` 包含 `type:check`。随后只修改 `package.json` 的 `check`。

只有前端 `AGENTS.md` 尚未包含风险匹配、纯文档免 TDD、最小 Skill、按冲突隔离和独立授权规则时，才补充缺失措辞；不得复制后端专项规则。

- [ ] **Step 3: 运行一次聚焦验证**

```bash
cd /Users/kaizhou/codeDev/wes_frontend
pnpm vitest run tests/unit/scripts/quality-gates.test.ts
pnpm run check
git diff --check -- package.json tests/unit/scripts/quality-gates.test.ts AGENTS.md
```

如果无关前端可执行差异导致 `pnpm run check` 无法归因，只保留聚焦测试结果并把完整检查交给候选 Commit 的 CI；不得为获得一份干净本地日志而迁移无关现场。

- [ ] **Step 4: 精确暂存和提交（仅已授权时）**

```bash
git add -- package.json tests/unit/scripts/quality-gates.test.ts
# 仅在本 Task 实际修改 AGENTS.md 时执行：git add -- AGENTS.md
git diff --cached --name-only
git diff --cached --check
git commit -m "perf(dev): 删除前端重复类型检查"
```

### Task 2: 手术式修正后端 Agent 规则

**Classification:** 纯文档；不运行 pytest、QUALITY 或 HEAVY。

**Files:**

- Modify: `AGENTS.md`

**Success:** 保留现有 24 KiB 目标和全部架构红线，只纠正与本次流程审计直接相关的措辞。

- [ ] **Step 1: 核对现有规则而不全文重写**

```bash
git status --short
wc -c AGENTS.md
rg -n "24 KiB|普通单任务不创建 worktree|纯人类可读|只有后续变化触及|计划文档表达" AGENTS.md
```

- [ ] **Step 2: 只修改必要规则**

规则必须明确：默认直接工作；只有路径重叠、共享生成物、写入范围不可控、独立运行环境或长线现场才使用 worktree；流程以当前内聚切片判定；纯文档不继承后续 Review、Commit 或 Deploy 的流程成本；有效证据不重复运行。

不得把 `AGENTS.md` 重写到任意 16 KiB，也不得顺手调整无关章节和命令。

- [ ] **Step 3: 文档相称检查**

```bash
uv run pymarkdown -d md013 scan AGENTS.md
git diff --check -- AGENTS.md
git diff -- AGENTS.md
wc -c AGENTS.md
```

Expected: 文件仍不超过 24 KiB，diff 只包含上述规则。

- [ ] **Step 4: 精确暂存和提交（仅已授权时）**

```bash
git add -- AGENTS.md
git diff --cached --name-only
git diff --cached --check
git commit -m "docs(agent): 收敛任务隔离与证据复用规则"
```

### Task 3: 修复 `.txt` 文档门禁误分类

**Classification:** 高风险质量门禁 Bug；使用最小 RED → DEV → GREEN，并复用现有测试所有者。

**Files:**

- Modify: `.githooks/pre-commit`
- Modify: `tests/scripts/test_git_quality_gate.py`

**Success:** `docs/**/*.txt` 可以走文档门禁；`src/`、`scripts/`、`tests/` 和依赖清单中的 `.txt` 必须进入完整质量 fallback。

- [ ] **Step 1: RED——扩展既有 pre-commit 路由测试**

复用 `_run_pre_commit_hook` 和现有 `test_pre_commit_uses_docs_gate_for_human_readable_document`、`test_pre_commit_keeps_quality_gate_for_machine_readable_contract` 所有权增加路径分类场景，不新建测试文件。至少覆盖 `docs/note.txt`、`src/runtime/contract.txt`、`scripts/input.txt`、`tests/integration/fixture.txt` 和 `requirements.txt`。

- [ ] **Step 2: 运行 RED**

```bash
uv run pytest tests/scripts/test_git_quality_gate.py -q
```

Expected: 仅执行目录或依赖清单 `.txt` 被错误归入 docs-only 的场景失败。

- [ ] **Step 3: DEV——收紧 hook 的 `.txt` 路径规则**

保留现有人类文档后缀；`.txt` 只有位于 `docs/` 时进入 docs-only 和 release-metadata 文档分支。其它 `.txt` 默认进入完整质量 fallback，不新增共享分类框架。

- [ ] **Step 4: GREEN——只刷新失效证据**

```bash
uv run pytest tests/scripts/test_git_quality_gate.py -q
git diff --check -- .githooks/pre-commit tests/scripts/test_git_quality_gate.py
```

Commit 已授权时精确暂存这两个文件，由 hook 按当前规则产生最终门禁证据；不提前手工重复完整 QUALITY。

### Task 4: 清理失效 HEAVY mapping

**Classification:** 小型测试治理变更；调整现有测试或断言，不新建测试文件。

**Files:**

- Modify: `docs/architecture/heavy-test-impact.toml`
- Modify only if existing assertion requires: `tests/scripts/test_select_heavy_tests.py`

**Success:** 已退役 `tests/integration/test_callback_external_payload_limit.py` 不再出现在 mapping，未知候选仍 fail closed。

- [ ] **Step 1: 冻结事实和验证范围**

```bash
test ! -e tests/integration/test_callback_external_payload_limit.py
rg -n "test_callback_external_payload_limit" docs/architecture/heavy-test-impact.toml tests
git status --short
```

如果 checkout 还存在无关机器配置或生产代码差异，`--scope unstaged` 不能作为本 Task 的证据。只有 selector 验证是当前交付必选、无法隔离且 index 也无法安全精确暂存时，才为本 Task 使用 worktree；CI 将对候选 Commit 权威执行时，不为本地宽验证日志隔离。

- [ ] **Step 2: 删除 mapping 和对应旧断言**

删除完整失效条目。只有现有测试明确要求该条目存在时才修改相应断言；不得新增同义 fail-closed 测试。

- [ ] **Step 3: 运行一次 selector 测试所有者**

```bash
uv run pytest tests/scripts/test_select_heavy_tests.py -q
git diff --check -- docs/architecture/heavy-test-impact.toml tests/scripts/test_select_heavy_tests.py
```

不得在运行完整文件后再次单独运行其中两个同义用例。

- [ ] **Step 4: 由唯一 owner 执行 HEAVY**

Commit 已授权且 index 可安全隔离时，精确暂存目标路径并运行 `select_heavy_tests.py --scope staged`。manifest 为 `NONE` 时结束；选出 HEAVY 时由 CI 运行一次，除非 CI 不可用且当前交付明确要求本地 Merge-ready 证据。

## Deferred / NOT in scope

### 轻量 Commit profile

当前前置条件未满足，本计划不修改 hook、质量脚本或测试。未来重新立项前只读核验：

```bash
gh api repos/kaizhoumasha/wes_backend/branches/develop/protection
```

还必须确认实际权威合并入口、禁止直接推送、完整 QUALITY 必选、selector HEAVY 必选以及 CI Commit 与候选 Commit 一致。只要 GitHub `develop` 仍返回 `Branch not protected`，或 Jenkins 结果不能阻止权威合并，现有 pre-commit `quality` fallback 保持不变。

全部前提未来成立后，基于当时门禁和耗时重新测量并创建独立计划；不得执行本计划历史版本中的 `commit` profile 代码片段。

## Completion Criteria

- 每个切片记录仓库、Commit 或 diff 指纹、命令、结果和覆盖范围；后续只修改纯文档、PR 文案或无关仓库时，不刷新代码测试、QUALITY 或 HEAVY。
- 前端 CI 验证 Task 1；后端 hook/CI 分别验证实际提交的 Task 2–4。一个仓库未完成不阻止另一个独立切片报告结果。
- 最终只报告修改、已复用或新运行的验证、跳过原因、残余风险和 CI 状态。没有相应授权时，不执行 Push、PR、Merge 或 Deploy，也不把 CI 通过描述为现场验收。
- 本计划完成不依赖 Deferred 项，也不得把 Deferred 项的只读核验包装成待完成 checkbox。
