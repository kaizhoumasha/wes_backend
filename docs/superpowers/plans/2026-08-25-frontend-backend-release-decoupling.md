# Frontend/Backend Release Decoupling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让前端、后端和 release checker 独立产出不可变镜像，并由独立发布作业根据实际 consumer/provider 合同与运行输入选择 FAST/FULL，而不再绑定前后端 Commit。

**Architecture:** 后端导出 provider OpenAPI 与经校验权限叶子，前端从离线 canonical 快照派生实际 required operations/permissions，独立 checker 用固定版本 `oasdiff` 和集合包含判断做方向性兼容。部署编排比较候选与当前部署的内容指纹，在维护前完成分类、兼容和业务预检；producer CI 不再触发部署。

**Tech Stack:** Python 3.13、FastAPI、TypeScript 5.9、Node.js 22、pnpm 10、Docker、Jenkins、OCI labels、OpenAPI、oasdiff、Vitest、pytest。

**Spec:** `docs/superpowers/specs/2026-08-25-frontend-backend-release-decoupling-design.md`

## Global Constraints

- 本计划是发布基础能力唯一实施计划；菜单投影、菜单表/API 删除仍由前端计划 `2026-08-20-frontend-owned-menu-convergence.md` 独立拥有。
- 前端、后端和 checker 的运行、依赖、测试与镜像相互独立；checker 不导入后端 `src/` 或前端源码。
- 普通前端 CI 完全离线消费 checked-in canonical snapshots；只有显式 `contract:freeze` 访问干净后端 checkout。
- 不增加复合 release manifest、发布数据库、动态规则引擎、旧门禁开关、shim、fallback、签名 PKI 或第二套菜单产物。
- 不复制权限扫描、CRUD feature/action 推理、API method 命名、Git diff plumbing、OpenAPI breaking-change 或 `$ref` 解析逻辑。
- 设计文档第 5 节定义的 JSON shape、严格拒绝规则、字节 hash 与 OCI label 名是唯一机器合同；producer、checker 和 deploy tests 共享 golden bytes，但不共享运行时包。
- `ERR` 自动阻断；`WARN` 只有绑定精确 frontend/backend/checker digest 与 diff hash 的单次人工理由才能继续。
- Producer CI 只发布不可变镜像，不调用部署 job。独立发布作业只允许 `FORCE_FULL`，不存在 `FORCE_FAST`。
- 维护前失败不改变运行环境；维护后失败保持入口关闭。Backend 变化必须原子重建全部后端应用服务到同一 digest。
- 所有生产行为与机器合同变更按 TDD/聚焦测试实施；纯文档不新增正文测试。
- `docs/hardware/` 禁止修改、移动或删除。Commit、Push、PR、Merge 和 Deploy 分别需要授权。

---

## File Map

### Backend provider artifacts

- Modify: `src/utils/permission_scanner.py` — 抽出经校验权限叶子唯一 helper，目录构建继续复用。
- Modify: `tests/admin/test_permission_scanner.py` — 锁定空目录、坏字段、重复名称、顺序和 helper/catalog 一致性。
- Create: `scripts/export_release_provider.py` — 原子导出 provider OpenAPI、provided permissions、expected schema head 与生产输入指纹。
- Create: `tests/scripts/test_export_release_provider.py` — 导出确定性、失败不覆盖、输入指纹和秘密边界。

### Frontend canonical inputs and consumer artifacts

- Create generated: `contracts/permissions.current.json` — 后端 exporter 输出的 canonical permission leaves。
- Modify: `scripts/freeze-backend-contract.ts`, `scripts/lib/openapi-sync.ts` — 一次显式 freeze 原子刷新 OpenAPI、permissions 与 provenance。
- Modify: `scripts/generate-permissions.ts`, `scripts/lib/permissions-codegen.ts`, `scripts/verify-permissions-sync.ts` — 普通生成/验证只读取 canonical permission snapshot。
- Modify: `tests/unit/scripts/freeze-backend-contract.test.ts`, `tests/unit/scripts/permissions-codegen.test.ts`, `tests/unit/scripts/contract-verifier.test.ts` — 离线、原子和零漂移合同。
- Modify: `scripts/generate-api-types.ts` — 导出 generated method→operation 的共享纯映射，不复制命名规则。
- Create: `scripts/lib/release-consumer.ts`, `scripts/export-release-consumer.ts`; modify `package.json` — 派生 required operations、required permissions 与 frontend 指纹，并提供唯一命令 `pnpm export:release-consumer`。
- Create: `tests/unit/scripts/release-consumer.test.ts` — 精确消费、动态调用拒绝和确定性。
- Modify: frontend `src/views/admin/api-applications/config/pageConfig.ts`, `src/views/admin/devices/config/pageConfig.ts`, `src/views/admin/menus/config/pageConfig.ts`, `src/views/admin/permissions/config/pageConfig.ts`, `src/views/admin/roles/config/pageConfig.ts`, `src/views/admin/users/config/pageConfig.ts`, `src/views/admin/worklines/config/pageConfig.ts` — 把当前整组权限改为显式 `CrudPagePermissionConfig` 叶子；不新增 registry。
- Modify: `tests/unit/components/crudPermissionBindings.test.ts`, `tests/unit/components/createCrudPageConfigFromResource.test.ts` — 显式叶子与当前 feature/action 行为一致。

### Independent images and producer CI

- Modify: backend `Dockerfile`, `Jenkinsfile.backend-ci` — 内置 provider artifacts/fingerprints 并独立发布 backend image。
- Modify: frontend `Dockerfile`, `Jenkinsfile`, `package.json` — 内置 consumer artifacts/fingerprints，`develop` 独立发布 frontend image，删除 paired inputs 和 auto deploy。
- Create: backend `tests/deployment/test_backend_ci_pipeline.py` — 锁定 provider export、镜像 artifact/label 与 producer 不部署行为。
- Modify: frontend `tests/unit/scripts/quality-gates.test.ts`, `tests/unit/scripts/artifact-publish.test.ts`.

### Independent checker

- Create: `tools/release_checker/release_checker.py` — 输入校验、permission subset、oasdiff 编排、FAST/FULL 分类与 JSON report。
- Create: `tools/release_checker/Dockerfile` — 固定 oasdiff 版本，stdlib Python runtime，无 WES application dependency。
- Create: `tools/release_checker/tests/test_release_checker.py` — 纯函数、状态、报告、WARN 和 timeout。
- Create: `tools/release_checker/tests/fixtures/` — used/unused operation 与传递 schema OpenAPI fixtures。
- Create: `Jenkinsfile.release-checker-ci` — checker 独立构建/自测/推送。
- Create: `tests/deployment/test_release_checker_ci.py` — job 隔离、固定版本和不触发部署。

### Release orchestration

- Modify: `Jenkinsfile.test-deploy` — 独立镜像选择、digest pin、candidate-vs-deployed 分类、checker、FAST/FULL 和 cutover 状态。
- Modify: `tests/deployment/test_test_deploy_cutover.py` — 替换 exact-pair 断言并扩展 pre/post-maintenance simulation。
- Verify only: `docker-compose.test-deploy.yml`, `docker-compose.deploy.yml` — 作为真实部署/拓扑指纹输入；本计划不预设 Compose 改动，不触碰 CI-HEAVY 或 frontend preview Compose。
- Modify: `docs/devops/prod-release-deploy.md`, `docs/devops/JENKINS.md`, `docs/devops/jenkins-setup-current-env.md`, `docs/devops/rocky-linux-server-initialization.md` — 当前 FAST/FULL、checker、证据和回滚路径。
- Modify: `docs/architecture/heavy-test-impact.toml`, `docs/architecture/file_index.md` — 精确 executable owner；纯文档条目不制造 HEAVY。

## Task 0: Freeze exact bases and prove the oasdiff assumption

**Files:**
- Create worktrees at the repository-standard backend/frontend worktree roots.
- Create: `tools/release_checker/tests/fixtures/consumer-used-operation.json`
- Create: `tools/release_checker/tests/fixtures/provider-compatible-unused-change.json`
- Create: `tools/release_checker/tests/fixtures/provider-breaking-unused-method.json`
- Create: `tools/release_checker/tests/fixtures/provider-breaking-used-schema.json`
- Create: `tools/release_checker/tests/fixtures/menu-new-consumer-old-provider.json`
- Create: `tools/release_checker/tests/fixtures/menu-old-consumer-new-provider.json`
- Create: `tools/release_checker/tests/test_oasdiff_operation_filter.py`

**Interfaces:**
- Consumes: fixed backend/frontend `origin/develop` SHAs and one pinned oasdiff version/digest.
- Produces: a proven projection plus one oasdiff invocation that compares only selected `{method, path}` operations while retaining their transitive schemas and refusing external refs.

- [ ] **Step 1: Create isolated worktrees and record dirty baselines**

Use each repository's existing worktree helper from the exact fetched `origin/develop`. Record main-checkout status, worktree list, remote SHA and dirty-path fingerprint before creating anything. Do not read or copy unrelated dirty diffs.

- [ ] **Step 2: Write the minimal OpenAPI fixtures and failing contract test**

The test must prove:

- changing only an unselected endpoint/schema produces no ERR/WARN;
- breaking an unselected HTTP method on the same path produces no ERR/WARN;
- deleting a selected operation produces ERR;
- breaking a response schema referenced transitively by a selected operation produces ERR;
- consumer baseline without `/auth/my.menus` against the old provider with the extra response field passes;
- old consumer requiring menu GET or `/auth/my.menus` against the new provider fails;
- base/revision order is always consumer baseline → selected provider, never the reverse.

- [ ] **Step 3: Run the fixture test against the pinned oasdiff binary**

Run: `rtk uv run pytest tools/release_checker/tests/test_oasdiff_operation_filter.py -q`

Expected: PASS only if one exact projection plus one oasdiff invocation satisfies all cases with external refs disabled. If it does not, stop this plan and evaluate one mature OpenAPI filter library; do not implement a custom ref walker or run one process per operation.

- [ ] **Step 4: Freeze the implementation manifest**

Record the pinned oasdiff version/digest, exact input/output behavior, all machine-contract golden bytes, both repository bases, expected changed paths, direct test owners and HEAVY owners. Report any new HIGH/CRITICAL shared consumer before Task 1.

## Task 1: Export one validated backend provider contract

**Files:**
- Modify: `src/utils/permission_scanner.py`
- Modify: `tests/admin/test_permission_scanner.py`
- Create: `scripts/export_release_provider.py`
- Create: `tests/scripts/test_export_release_provider.py`

**Interfaces:**
- Produces: `build_validated_permission_leaves(app: FastAPI) -> list[dict[str, Any]]`.
- Produces CLI: `export_release_provider.py --out-dir <dir>`.
- Outputs: `provider-openapi.json`, `provided-permissions.json`, `provider-fingerprints.json` for CI consumption; only the first two enter runtime images as raw artifacts.

- [ ] **Step 1: Add RED tests for validated leaves**

Assert empty scans, malformed names/fields and conflicting duplicate names fail through `build_validated_permission_leaves`; duplicate identical leaves collapse deterministically. Assert `build_permission_catalog` uses the same leaves and only it adds group rows.

- [ ] **Step 2: Implement the smallest shared leaf helper**

Move existing leaf validation/dedup/order into `build_validated_permission_leaves`. Keep `scan_routes_for_permissions` as route extraction and make `build_permission_catalog` consume the new helper. Do not change permission semantics or add a registry.

- [ ] **Step 3: Add RED exporter tests**

Cover byte-identical repeat output, stable Unicode JSON, OpenAPI/permission fact mutation, expected Alembic head, migration tree hash, Dockerfile-consumed dependency inputs, production recipe hash, invalid catalog, output-directory rollback and absence of secrets/absolute checkout paths.

- [ ] **Step 4: Implement atomic provider export**

Use `create_app().openapi()` and `build_validated_permission_leaves(create_app())`. Fingerprint only production inputs actually consumed by the backend Dockerfile: root `pyproject.toml`, root `uv.lock`, `packages/wes_plugin_sdk/pyproject.toml`, `workline_plugins/rough_sorter/pyproject.toml`, migration files and production recipe/entrypoint inputs. Write to a temporary sibling directory and rename only after every artifact validates.

- [ ] **Step 5: Run focused backend tests**

Run:

```bash
rtk uv run pytest tests/admin/test_permission_scanner.py tests/scripts/test_export_release_provider.py -q
rtk uv run ruff check src/utils/permission_scanner.py scripts/export_release_provider.py tests/admin/test_permission_scanner.py tests/scripts/test_export_release_provider.py
rtk uv run ruff format --check src/utils/permission_scanner.py scripts/export_release_provider.py tests/admin/test_permission_scanner.py tests/scripts/test_export_release_provider.py
```

- [ ] **Step 6: Update exact HEAVY ownership and commit if authorized**

Map the exporter and migration fingerprint behavior without mapping human documentation. Stage exact Task 1 paths and use `feat(release): 导出后端发布能力合同`.

## Task 2: Make frontend contract generation offline and atomic

**Files:**
- Create generated: `contracts/permissions.current.json`
- Modify: `scripts/freeze-backend-contract.ts`, `scripts/lib/openapi-sync.ts`
- Modify: `scripts/generate-permissions.ts`, `scripts/lib/permissions-codegen.ts`, `scripts/verify-permissions-sync.ts`
- Modify: corresponding freeze, permission and verifier unit tests.

**Interfaces:**
- Explicit freeze consumes Task 1's clean backend checkout/exporter.
- Ordinary `generate:permissions` and `permission:verify` consume `contracts/permissions.current.json` and take no backend-root input.
- Existing sync records retain backend Commit only as freeze provenance; no release gate compares it to the selected backend image.

- [ ] **Step 1: Add RED tests for the canonical permission snapshot**

Prove ordinary permission generation/verification succeeds with no backend checkout, rejects malformed or non-canonical leaves, detects generated-file drift and recomputes the snapshot SHA. Prove legacy direct Python scanning is no longer called.

- [ ] **Step 2: Add RED freeze transaction tests**

The freeze test must fake backend export and assert OpenAPI, permissions and both provenance records update together. Export failure, backend HEAD movement or validation failure must leave every existing snapshot/record byte unchanged.

- [ ] **Step 3: Implement snapshot readers and offline generators**

Replace `scanBackendPermissions` with a strict canonical snapshot reader. Keep one normalization/hash implementation in `permissions-codegen.ts`; generation and verification both call it. Remove obsolete marker parsing, `uv` child process and backend-root requirements from ordinary commands.

- [ ] **Step 4: Make explicit freeze call the backend exporter once**

Validate the backend clean `develop` checkout and stable HEAD, invoke `scripts/export_release_provider.py` into a temporary directory, validate both raw artifacts, then atomically install the two canonical snapshots and provenance records.

- [ ] **Step 5: Run focused frontend contract tests twice**

Run:

```bash
rtk pnpm test -- tests/unit/scripts/freeze-backend-contract.test.ts tests/unit/scripts/permissions-codegen.test.ts tests/unit/scripts/contract-verifier.test.ts
rtk pnpm generate:permissions
rtk pnpm permission:verify
```

Run generation a second time and require `rtk git diff --exit-code -- contracts/permissions.current.json src/api/generated/permissions .permission-sync-record.json` within the task worktree.

- [ ] **Step 6: Commit if authorized**

Stage only Task 2 snapshot/scripts/generated files and use `refactor(contract): 离线生成前端权限合同`.

## Task 3: Derive the actual frontend consumer surface

**Files:**
- Modify: `scripts/generate-api-types.ts`
- Modify: `package.json`
- Create: `scripts/lib/release-consumer.ts`, `scripts/export-release-consumer.ts`
- Create: `tests/unit/scripts/release-consumer.test.ts`
- Modify: `src/views/admin/api-applications/config/pageConfig.ts`, `src/views/admin/devices/config/pageConfig.ts`, `src/views/admin/menus/config/pageConfig.ts`, `src/views/admin/permissions/config/pageConfig.ts`, `src/views/admin/roles/config/pageConfig.ts`, `src/views/admin/users/config/pageConfig.ts`, `src/views/admin/worklines/config/pageConfig.ts`.
- Modify: `tests/unit/components/crudPermissionBindings.test.ts`, `tests/unit/components/createCrudPageConfigFromResource.test.ts`

**Interfaces:**
- Produces `RequiredOperation = { method: HttpMethod; path: string }`.
- Produces `exportReleaseConsumer() -> { requiredOperations; requiredPermissions; fingerprints }`.
- Exposes exactly one CLI entrypoint as `pnpm export:release-consumer`; Docker、CI 和菜单收敛计划都复用该命令。
- Reuses one generated-method catalog extracted from `generate-api-types.ts`; consumer export must not recompute module/method naming.

- [ ] **Step 1: Add RED consumer extraction tests**

Cover route permissions, explicit CRUD leaf mappings, custom action permissions, token refresh, direct contract endpoint, device SSE, generated method calls, alias imports, destructuring, optional chaining, static computed properties, duplicate collapse, deterministic order, `*` exclusion, unknown permission, whole-group reference, dynamic property access, dynamic method/path, unresolved barrel export and exclusion of tests/dist/non-production roots.

- [ ] **Step 2: Export the existing generated method mapping as a pure helper**

Refactor only the method→operation facts already calculated by `buildGeneratedMethodInfo`; `generate-api-types` and consumer exporter must call the same helper. Run existing generator tests before and after and require identical generated API modules.

- [ ] **Step 3: Replace seven whole-group page bindings with explicit leaves**

Update API Applications, Devices, Menus, Permissions, Roles, Users and WorkLines page configs to typed `CrudPagePermissionConfig` objects containing only currently enabled/fallback actions. Do not add a permission registry or alter feature behavior. Keep the menu page change even though the later menu plan deletes it, so the current baseline exporter is complete.

- [ ] **Step 4: Implement the deterministic consumer exporter**

Read only canonical frontend snapshots and production source. Reuse the repository's TypeScript compiler API to resolve required permission names, imports and calls; do not use regex source scanning. Resolve names to canonical method/path, union direct endpoints and generated method calls, reject unresolved/dynamic usage, and write `consumer-openapi.json`, `required-operations.json`, `required-permissions.json` plus CI-only fingerprints atomically.

Wire `scripts/export-release-consumer.ts` to the single `package.json` script `export:release-consumer`. Do not add Jenkins-only, Docker-only or menu-only wrappers.

- [ ] **Step 5: Run focused frontend tests**

Run:

```bash
rtk pnpm test -- tests/unit/scripts/generate-api-types.test.ts tests/unit/scripts/release-consumer.test.ts tests/unit/components/crudPermissionBindings.test.ts tests/unit/components/createCrudPageConfigFromResource.test.ts
rtk pnpm contract:test
rtk pnpm contract:verify
rtk pnpm permission:verify
```

- [ ] **Step 6: Commit if authorized**

Stage exact Task 3 files and use `feat(release): 导出前端实际消费合同`.

## Task 4: Build the independent release checker

**Files:**
- Create: `tools/release_checker/release_checker.py`, `Dockerfile`, tests and fixtures.
- Create: `Jenkinsfile.release-checker-ci`
- Create: `tests/deployment/test_release_checker_ci.py`
- Modify: `docs/architecture/heavy-test-impact.toml`, `docs/architecture/file_index.md`

**Interfaces:**
- CLI consumes extracted frontend/backend artifact directories, current/candidate fingerprint JSON, effective config hashes, optional `--force-full`, optional WARN approval reason and output report path.
- Outputs one deterministic `compatibility-report.json` with auto/effective mode, reasons, all three digests, artifact hashes, compatibility findings, approval evidence and final pre-cutover state.

- [ ] **Step 1: Add RED pure checker tests**

Cover permission subset pass/fail, every design-specified `kind`/shape/order/unknown-field rejection, malformed/duplicate artifacts, raw-byte SHA mismatch, every exact OCI label, different revisions with compatible facts, ERR, WARN without approval, exact WARN approval binding, deterministic report schema, timeout, current evidence missing, each FULL fingerprint reason, DB head equality/ancestor/divergence, `FORCE_FULL` and absence of any force-FAST path.

- [ ] **Step 2: Implement one stdlib checker CLI**

Keep parsing, normalization, classification and report creation in focused pure functions. Project both OpenAPI documents to the full required-operation set and invoke the pinned oasdiff binary exactly once through a bounded subprocess with external refs disabled; never import backend or frontend packages. Redact config contents and response bodies from stdout/stderr/report.

- [ ] **Step 3: Build the minimal checker image**

Use a pinned oasdiff source image/binary and Python 3.13 slim runtime. Copy only checker source and the binary. The image command runs the checker CLI; it contains no WES application source or credentials.

- [ ] **Step 4: Add the independent checker CI job**

The job builds only when checker source/Dockerfile/job inputs change, runs its own tests and fixture acceptance, pushes immutable and channel tags, records digest, and never calls backend/frontend build or deploy jobs.

- [ ] **Step 5: Run checker verification**

Run:

```bash
rtk uv run pytest tools/release_checker/tests tests/deployment/test_release_checker_ci.py -q
rtk docker build -f tools/release_checker/Dockerfile -t wes-release-checker:local tools/release_checker
```

Run every Task 0 fixture through the built image, then run the current full consumer operation set in one invocation. Require a typical run under 30 seconds and a hard failure before 60 seconds.

- [ ] **Step 6: Commit if authorized**

Stage only checker/job/tests/index/mapping paths and use `feat(release): 增加独立兼容检查器`.

## Task 5: Publish self-describing frontend and backend images independently

**Files:**
- Modify backend: `Dockerfile`, `Jenkinsfile.backend-ci`; create `tests/deployment/test_backend_ci_pipeline.py`.
- Modify frontend: `Dockerfile`, `Jenkinsfile`, `package.json`, quality/artifact tests.

**Interfaces:**
- Backend and frontend `develop` pushes publish immutable commit tags independently after their own gates.
- Each image embeds Task 1/3 raw artifacts and OCI labels for their hashes and production inputs.
- Neither producer receives a peer image/Commit parameter or calls the deploy job.

- [ ] **Step 1: Add RED image identity tests**

Assert raw artifact paths exist in the correct image, labels match their bytes, revision/source labels remain, backend has no frontend fields, frontend has no target-backend Commit field, and malformed/missing build inputs fail image construction.

- [ ] **Step 2: Add RED producer pipeline tests**

Frontend tests must prove a `develop` push publishes its immutable image with no backend release parameters and contains no `Trigger Test Deploy`. Backend tests must prove provider export precedes runtime image build and the existing independent push remains.

- [ ] **Step 3: Wire backend artifacts and fingerprints**

Run Task 1 exporter once in backend CI, validate outputs, feed build args/labels, copy raw artifacts into `/opt/wes/release/`, archive provider facts, and keep HEAVY selection semantics unchanged.

- [ ] **Step 4: Wire frontend artifacts and fingerprints**

Run Task 3 exporter during frontend gates, copy its three raw artifacts into `/opt/wes/release/`, replace old backend-contract-revision label/validation with consumer hashes, and remove menu-manifest publishing only when the menu convergence task owns that deletion. Until then, menu manifest remains unrelated to compatibility and must not be read by the deploy gate.

- [ ] **Step 5: Remove paired producer behavior**

Delete frontend backend-image/backend-Commit/deploy-source parameters, paired-release branches and automatic TEST trigger. Push commit and channel tags on valid `develop` producer runs without changing MR verification behavior.

- [ ] **Step 6: Run producer gates**

Run backend focused exporter/pipeline tests and frontend release-consumer/quality/artifact tests. Build both production images locally and inspect raw artifact hashes against OCI labels.

- [ ] **Step 7: Commit each repository separately if authorized**

Use `feat(release): 发布自描述后端镜像` and `feat(release): 独立发布前端镜像`; do not create a paired commit or trigger deployment.

## Task 6: Replace exact pairing with independent FAST/FULL preflight

**Files:**
- Modify: `Jenkinsfile.test-deploy`
- Modify: `tests/deployment/test_test_deploy_cutover.py`
- Verify only: `docker-compose.test-deploy.yml`, `docker-compose.deploy.yml`; hash their bytes as inputs without changing them unless a separately reviewed executable requirement appears.

**Interfaces:**
- Inputs: `DEPLOY_SCOPE=FRONTEND|BACKEND|BOTH`; immutable candidate digest only for each selected side; deploy-source Commit; optional `FORCE_FULL`; optional exact WARN reason. Checker digest is fixed by deploy source, not an operator parameter.
- Current release evidence: previous `compatibility-report.json` plus live image/config/DB verification. For a single-side scope, the unselected peer is discovered from the live container and must equal the last successful evidence; it is not an operator input.
- Output: new report and either `PRE-CUTOVER_ABORTED`, `CUTOVER_FAILED_MAINTENANCE_HELD` or successful FAST/FULL completion.

- [ ] **Step 1: Replace paired-parameter tests with independent identity tests**

Keep candidate tag→digest and each image revision verification. Remove backendCommit equality and operator-supplied OpenAPI/permission hashes. Assert `FRONTEND` rejects backend candidate input, `BACKEND` rejects frontend candidate input, `BOTH` requires both, and external parameters cannot replace image-owned artifacts/labels or the deploy-source checker digest.

- [ ] **Step 2: Add RED classification tests**

Cover ordinary code-only FAST, every backend/frontend/deploy/config FULL reason, current evidence missing, candidate-vs-deployed multi-build differences, rollback selection, config hash change, DB head rules, `FORCE_FULL`, and no force-FAST input.

- [ ] **Step 3: Add RED compatibility/cutover-order tests**

Assert current-peer live/evidence discovery, extraction, hash validation, checker and business preflight all precede maintenance. Missing/mismatched current evidence, checker failure or timeout leaves the current environment untouched. Preserve `disableConcurrentBuilds`; immediately before maintenance re-read the current peer digest and abort if it changed. After maintenance, every failure keeps Nginx closed. Different revisions with compatible operations/permissions pass.

- [ ] **Step 4: Implement the pre-maintenance path**

Pin selected candidate digest(s) and the deploy-source checker digest. Discover the unselected current peer from live containers, cross-check it against the last successful report, then extract both sides' raw artifacts to a private temporary directory. Validate exact labels and bytes, hash approved effective config files without logging contents, query DB head, call checker with a 60-second hard timeout, archive the report and clean temporary containers/files on every exit.

- [ ] **Step 5: Implement effective mode and cutover**

Use checker `auto_mode` plus optional `FORCE_FULL`. Immediately before maintenance, reverify the discovered current peer digest. FAST changes only the selected side, but backend FAST rebuilds API/Celery/WMS fulfillment/Beat/Flower together. FULL retains backup, forward migration, zero-drift authorization, admin login, exact topology and shared HTTP readiness. Preserve existing `disableConcurrentBuilds` and fail-closed EXIT trap.

- [ ] **Step 6: Run deployment simulation**

Run: `rtk uv run pytest tests/deployment/test_test_deploy_cutover.py tools/release_checker/tests/test_release_checker.py -q`

Verify every fake failure stage occurs at most once, single-side invocation needs no peer parameter, current-peer drift aborts before maintenance, no database mutation occurs before FULL maintenance, and no test equates health/readiness with supplier, callback or physical acceptance.

- [ ] **Step 7: Commit if authorized**

Stage exact deployment/checker test paths and use `refactor(release): 按方向兼容独立选择镜像`.

## Task 7: Cut over the gate once and retire the old path

**Files:**
- Modify current devops docs and indexes listed in File Map.
- Modify frontend current contract/release documentation discovered by exact reference scan.
- Do not modify completed historical checklist bodies; add narrow supersession notes only.

**Interfaces:**
- Consumes: Task 1–6 merged producer/checker/deploy capabilities.
- Produces: one active release path and a baseline report usable by later menu convergence.

- [ ] **Step 1: Run residual scans before rollout**

Search both repositories for paired-release parameters, backend-contract equality, frontend auto-deploy, operator-supplied contract hashes, old menu extraction and current documentation references. Classify each hit as delete, provenance-only or historical note.

- [ ] **Step 2: Publish the three baseline images without deploying**

Publish backend, frontend and checker immutable images from approved clean `develop` snapshots. Record their digests and prove raw artifact/label consistency. Producer success remains `PUBLISHED — NOT DEPLOYED`.

- [ ] **Step 3: Perform one authorized FULL baseline cutover**

Use `DEPLOY_SCOPE=BOTH` to select the first frontend/backend baseline digests; checker digest comes from deploy source. Because no previous new-format report exists, automatic mode must be FULL. Verify report, admin login, DB head, topology and external readiness; do not claim WMS/ECS or business acceptance.

- [ ] **Step 4: Remove the old gate in the same release line**

Delete old parameters, conditions, tests and current-doc wording; do not leave an emergency switch. Confirm the new job cannot accept legacy inputs.

- [ ] **Step 5: Update current documentation**

Document FAST/FULL inputs, reports, WARN approval, config hashes, DB rules, pre/post-maintenance states and rollback. Add supersession notes to the completed integration reliability design/plan instead of rewriting their historical completion evidence.

- [ ] **Step 6: Run final engineering gates and review**

For each repository, run focused tests, QUALITY and the exact selector HEAVY manifest on the final executable snapshot. Run one complete cross-repo Review, repair confirmed findings, refresh only invalidated evidence, then one fresh final Review to `NO_FINDINGS`.

- [ ] **Step 7: Hand off to the menu plan**

Report the baseline frontend/backend/checker digests, report hash, current DB head, effective config hashes and unverified external boundaries. The menu plan may start its ordered frontend FULL only after this baseline exists.

## Task 8: Validate the menu plan integration without absorbing it

**Files:**
- Verify: frontend `docs/superpowers/plans/2026-08-20-frontend-owned-menu-convergence.md`.
- Verify: backend `docs/superpowers/specs/2026-08-20-authorization-and-menu-source-convergence-design.md`.

**Interfaces:**
- Release foundation owns checker/artifacts/jobs.
- Menu plan owns route projection, menu API/table removal and its irreversible migration.

- [ ] **Step 1: Verify the frontend-first FULL contract**

Before backend menu removal, use `DEPLOY_SCOPE=FRONTEND` with only the new frontend candidate; discover the current backend automatically. Require compatibility PASS/WARN approval, no browser menu API requests, route-projected menus and fail-closed permission loading.

- [ ] **Step 2: Verify the backend FULL contract**

With the new frontend already running, use `DEPLOY_SCOPE=BACKEND` with only the new backend candidate; discover the deployed frontend automatically. Require migration backup/head evidence, compatibility PASS, menu tables/API/`/auth/my.menus` absence, permission zero drift, admin login, final topology and menu/RBAC browser QA.

- [ ] **Step 3: Verify forbidden ordering and rollback evidence**

Prove old frontend + new backend is rejected before maintenance. Frontend-stage failure may restore the previous frontend digest. Backend-stage post-migration failure must hold maintenance and use forward-fix or verified DB restore; no automatic old-image-only rollback.

- [ ] **Step 4: Close documentation and acceptance boundaries**

Ensure current menu/release docs describe independent FULL stages, no paired Commit wording remains, and all supplier/WMS/ECS/physical/business layers remain explicitly `NOT VERIFIED` unless separately proven.

## Completion Criteria

- Backend/frontend/checker producers are independent and never auto-deploy.
- A frontend producer succeeds without backend checkout/job/image access; a backend producer succeeds without frontend checkout/job/image access; checker publication is not a producer prerequisite.
- Ordinary frontend CI is offline from backend and reproduces generated outputs from canonical snapshots.
- Required permissions/operations represent actual frontend consumption; unresolved dynamic usage fails the build.
- Checker reuses pinned oasdiff once per check, compares permissions by name subset, rejects noncanonical machine contracts/external refs, produces bounded deterministic reports and has no WES application imports.
- Candidate-vs-deployed fingerprints and live DB/config evidence choose FAST/FULL; unknown is FULL and no force-FAST path exists.
- Pre-maintenance failures leave runtime unchanged; post-maintenance failures keep entry closed; backend services never run mixed digests.
- Old exact-pair parameters, commit equality, frontend auto-deploy and compatibility paths are absent.
- Single-side deployment takes only that side's candidate and verifies an auto-discovered current peer; menu convergence can execute as ordered frontend FULL then backend FULL without duplicating release infrastructure.

## Engineering Review Evidence

### What already exists

- Backend `create_app().openapi()` and `permission_scanner.py` already own provider facts; the plan extracts and reuses them instead of creating a registry.
- Frontend `generate-api-types.ts`, canonical OpenAPI snapshot and generated permission constants already own naming/codegen; the consumer exporter reuses their pure mappings instead of inferring a second convention.
- Frontend `createRoutes()` already assembles the production route tree, and `permissionInitializedState` already distinguishes “empty but loaded” from “not loaded”; the menu plan now reuses both.
- Current deploy pipeline already has `disableConcurrentBuilds`, fail-closed maintenance handling, topology checks and shared readiness helpers; the plan preserves them and replaces only paired identity inputs.

### NOT in scope

- Contract registry, release database or shared runtime package — raw image artifacts, exact labels and archived reports are sufficient for this single-site LAN deployment.
- Menu sync CLI/service — static route projection removes the duplicated menu fact; synchronizing it would recreate the coupling being removed.
- Backward-compatible artifact readers or dual deployment gates — the system is unreleased and the cutover deletes the old path once.
- Custom OpenAPI `$ref` traversal — operation filtering must be proven with pinned mature tooling or the design is reopened.
- WMS/ECS callback, physical completion and business acceptance — these remain higher-layer release evidence, not checker success criteria.

### Test coverage diagram

```text
CODE PATHS                                              USER / RELEASE FLOWS
[GAP→PLANNED] backend provider export                   [GAP→PLANNED] Frontend producer publication
  ├── canonical bytes + strict shape                      ├── no backend checkout/job/image
  ├── exact OCI labels                                    └── PUBLISHED never means DEPLOYED
  └── atomic rollback — test_export_release_*           [GAP→PLANNED] Backend producer publication
[GAP→PLANNED] frontend consumer export                    └── no frontend checkout/job/image
  ├── TypeScript AST static-call extraction             [GAP→E2E] FRONTEND single-side FULL
  ├── dynamic/unresolved use fails closed                 ├── only frontend candidate input
  └── byte-identical — release-consumer.test.ts           ├── discover/reverify current backend
[GAP→PLANNED] compatibility checker                       └── browser makes no menu API request
  ├── strict artifacts + permission subset              [GAP→E2E] BACKEND single-side FULL
  ├── one method-aware oasdiff invocation                 ├── only backend candidate input
  └── bounded report — test_release_checker.py            ├── discover/reverify deployed frontend
[GAP→PLANNED] static route menu projection                └── migration while maintenance held
  ├── RouteMeta + createRoutes + permissions            [GAP→PLANNED] Failure and rollback
  └── hidden/pruned/sorted — menu-tree/useMenu tests      ├── preflight failure changes nothing
                                                         └── post-migration failure holds entry closed

CURRENT NEW-BEHAVIOR COVERAGE: 0/9 path groups implemented; 9/9 have explicit planned tests.
E2E-WORTHY: two ordered menu FULL flows and browser network absence.
```

### Failure modes

| Path | Realistic failure | Planned test/error handling/user signal |
|------|-------------------|-----------------------------------------|
| Artifact export | partial write or noncanonical JSON | atomic rollback test; producer fails with named artifact |
| Consumer extraction | dynamic call silently omitted | AST fixture rejects build; exact source location reported |
| OpenAPI compatibility | unused method creates false block | same-path/different-method fixture; one projected comparison |
| Checker | subprocess hangs or external `$ref` needs network | hard timeout/external-ref rejection; `PRE-CUTOVER_ABORTED` |
| Current peer discovery | live digest differs from evidence | preflight block; explicit drift message |
| Cutover | peer changes after preflight | no-concurrency plus immediate pre-maintenance recheck |
| Menu bootstrap | permission API temporarily unavailable | no protected menu/route; dedicated retry page |
| Backend menu migration | migration/readiness fails after entry closes | maintenance remains held; forward-fix or verified DB restore |

No reviewed failure mode is both silent and without a planned test/error path; critical gaps after plan repair: 0.

### Worktree parallelization

| Step | Modules touched | Depends on |
|------|-----------------|------------|
| Provider contract | backend scanner/exporter | machine-contract freeze |
| Consumer contract | frontend scripts/contracts | machine-contract freeze; provider golden fixture |
| Checker | backend tools/release_checker | machine-contract and oasdiff fixture freeze |
| Producer images | both repository Docker/CI paths | provider + consumer contracts |
| Deploy orchestration | backend deployment/tests | checker + producer images |
| Menu convergence | frontend router/auth/menu; backend auth/admin/migrations | release baseline |

- Lane A: backend provider contract.
- Lane B: frontend consumer contract, using frozen golden provider bytes.
- Lane C: independent checker and oasdiff fixtures.
- Merge A+B+C, then Lane D: producer images and deployment orchestration.
- After the release baseline, execute menu frontend then menu backend sequentially; their contract direction forbids parallel deployment.
- Conflict flag: Lane A and Lane C both live in the backend repository but use disjoint modules; coordinate `heavy-test-impact.toml` edits during merge.

### Implementation Tasks

Synthesized from this review's findings; these are review deltas already placed in the numbered tasks above.

- [ ] **T1 (P1, human: ~4h / CC: ~35min)** — release contracts — implement exact JSON shapes, label names, raw-byte hashes, strict readers and golden tests.
- [ ] **T2 (P1, human: ~1d / CC: ~1h)** — deployment — implement candidate-only `FRONTEND|BACKEND` scopes and live/evidence current-peer discovery.
- [ ] **T3 (P1, human: ~3h / CC: ~25min)** — frontend contracts — prove freeze-once then offline build/test/publish; remove merge-triggered re-freeze.
- [ ] **T4 (P1, human: ~1d / CC: ~1h)** — checker — project the full required operation set and run one method-aware oasdiff with external refs disabled.
- [ ] **T5 (P2, human: ~4h / CC: ~40min)** — consumer exporter — use the existing TypeScript compiler API and reject unresolved production calls.
- [ ] **T6 (P2, human: ~2h / CC: ~20min)** — menu/router — add one `RouteMeta` augmentation and reuse existing permission initialization state.
- [ ] **T7 (P1, human: ~3h / CC: ~25min)** — menu/auth QA — prove bootstrap/refresh/retry/token/logout flows issue no menu requests.
- [ ] **T8 (P2, human: ~2h / CC: ~20min)** — release evidence — separate `BUILD_VERIFIED`, `PUBLISHED` and `DEPLOYED` in tests and Runbooks.
- [ ] **T9 (P1, human: ~3h / CC: ~25min)** — cutover safety — preserve non-concurrency and reverify the discovered peer immediately before maintenance.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | User retained the complete release-checker architecture; scope reduction was rejected |
| Codex Review | external Claude read-only review | Independent 2nd opinion | 1 | UNAVAILABLE | Process returned no review content within the bounded wait and was terminated; no result was claimed |
| Eng Review | `/plan-eng-review` | Architecture, failure safety, performance and tests | 2 | CLEAR | 9 actionable gaps folded into the spec and both plans; 0 open critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | Navigation behavior is covered by the menu plan; no visual redesign is in scope |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | Exact contracts and one canonical exporter path are specified; separate DX review not required |

**VERDICT:** ENG CLEARED — implementation can meet independent producer publication and candidate-only single-side deployment, provided every machine-contract and current-peer test in this revised plan is implemented before the old paired gate is removed.

NO UNRESOLVED DECISIONS
