# Authorization Catalog and Bootstrap Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将后端路由权限声明收敛为唯一权限定义真源，交付精确、事务化、可检查的权限目录与 fresh-DB bootstrap，并把权限管理 UI 直接改为只读目录。

**Architecture:** `permission_scanner.py` 只负责从 FastAPI 路由构造确定性权限定义；新的 `PermissionCatalogService` 通过 Repository 将该定义精确物化到数据库。新的 `AuthorizationBootstrapService` 复用同一目录服务，集中维护内置角色、角色权限、首个管理员及管理员角色关系；脚本只负责参数、事务和输出。前端通过重冻后的 OpenAPI 删除所有权限定义写能力，但继续使用现有角色/API Application 授权能力。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy Async、PostgreSQL、Redis 权限缓存、Alembic、pytest/pytest-asyncio、Vue 3.5、TypeScript 5.9、Vitest、pnpm 10、OpenAPI 生成链、GitNexus、RTK。

**Spec:** `docs/superpowers/specs/2026-08-20-authorization-and-menu-source-convergence-design.md`

## Global Constraints

- 基础能力与业务能力严格分离：权限目录和 bootstrap 使用最小测试应用/独立数据库验证，不用 WorkLine、Transport、WMS、ECS 或设备业务测试替代。
- 系统未发布；直接删除旧权限写接口、运行时扫描入口、静态 SQL 和前端写 UI，不保留 alias、shim、双路径、兼容响应或数据转换。
- 保留 `permissions`、`role_permissions` 和 API Application 权限关系；菜单删除属于后续计划，本计划不得提前删除菜单模型或接口。
- 不建设通用初始化框架、策略引擎、权限注册中心或插件机制；只增加权限目录与基础身份初始化所需的聚焦 Service/Repository。
- 可观察行为变更必须 TDD；纯文档、注释和归档操作不得新增正文断言测试。
- 归档文档移到 `/Users/kaizhou/codeDev/archive_docs/<repo>/`；项目内不保留副本、占位、软链接或转发文件。
- `docs/hardware/` 禁止修改、移动或删除。
- 后端命令统一使用 `rtk uv run ...` 或 `rtk ./scripts/...`；前端命令统一使用 `rtk pnpm ...`。
- 生产环境已启用 Snowflake BIGINT 主键；在任何权限目录/bootstrap 写入前，必须先把 `wes_sys.permissions.parent_id` 与仍存在的 `wes_sys.menus.parent_id` 从 INTEGER 扩容为 BIGINT。这是同版本直接替换，不引入双 schema 或兼容分支。
- Alembic revision 必须通过 `rtk uv run alembic revision -m "align tree parent ids"` 生成随机 revision ID，不得手写或复用仓库中的 revision ID。
- 实施前使用 `superpowers:using-git-worktrees` 从最新 `origin/develop` 创建隔离 worktree；不得修改或清理当前两个主工作区的用户 dirty 内容。
- 修改生产符号前，对 `sync_permissions_to_db`、`sync_builtin_role_permissions`、`PermissionService`、`TreeAPI._register_tree_routes`、`BaseAPI._register_soft_delete_routes` 和 API Application 同步端点执行 GitNexus upstream impact；HIGH/CRITICAL 结果必须先报告用户。
- 每个提交只暂存列出的精确路径；禁止 `git add -A`、`git add .`、`--no-verify`。Commit、Push、PR、Merge 和 Deploy 分别需要授权。
- 前后端是成对合同交付；允许分别评审和合入，但禁止只部署其中一端。直接替换必须使用维护态切换：先隔离外部流量并停止旧应用进程，再迁移和收敛授权，最后启动固定版本组合并通过门禁后恢复入口；任何中间失败都保持维护态，不增加兼容接口或双合同。
- 数据库授权事务提交后，用户/API Application 权限缓存失效属于切换门禁：常规路径必须精确检查每个删除结果并对失败 ID 有界重试；仍失败则命令非零退出且保持 Nginx 关闭。恢复模式只可清理 `perms:user:*` 和 `api_app:perms:*`，不得清空整个 Redis。
- 权限定义目录收敛为后端生成、管理端只读后，关闭 `PermissionService` 的通用详情/列表缓存；用户和 API Application 的有效权限缓存继续保留。不为小型管理目录再建一套同步失效规则。

---

## File Map

### Backend authorization foundation

- Modify: `src/core/mixins/tree.py` — 树节点 `parent_id` 与 Snowflake 主键使用同一 BIGINT 兼容类型。
- Modify: `src/core/base_api.py` — `gen_delete=False` 时不注册软删除写接口。
- Modify: `src/core/tree_api.py` — `gen_update=False` 时不注册移动和批量排序接口。
- Modify: `src/utils/permission_scanner.py` — 保留纯扫描、分组构造和目录校验；删除数据库同步职责。
- Create: `src/app/admin/services/permission_catalog_service.py` — 权限目录精确同步及结果类型。
- Modify: `src/app/admin/repositories/perm_repository.py` — 目录读取、批量物理删除和关联影响查询。
- Create: `src/app/admin/services/authorization_bootstrap_service.py` — 内置角色、角色权限、首个管理员和管理员角色关系的唯一基础服务。
- Modify: `src/core/rbac.py` — 用户权限缓存失效返回可检查的成功/失败结果。
- Modify: `src/app/api_auth/services/permission_service.py` — API Application 权限缓存失效返回可检查结果。
- Modify: `src/database/redis_cache.py` — `delete_pattern` 区分“无匹配键”与“Redis 不可用/删除失败”。
- Modify: `src/app/admin/repositories/role_repository.py` — 按名称读取内置角色。
- Modify: `src/app/admin/repositories/user_repository.py` — 确保用户-角色关系。
- Modify: `src/app/admin/services/__init__.py` — 导出两个新服务。
- Modify: `src/app/admin/v1/perm.py` — 权限目录只读。
- Modify: `src/app/admin/services/perm_service.py` — 保留查询和授权读取，关闭只读权限目录的通用缓存，删除不再可达的权限定义写覆盖。
- Modify: `src/app/admin/models/perm.py` — `PermissionBase.parent_id` FK 重写复用 BIGINT 兼容类型，并删除不再使用的 `PermissionCreate`、`PermissionUpdate` Schema。
- Modify: `src/app/admin/models/__init__.py` — 删除上述 Schema 导出。
- Modify: `src/app/api_auth/v1/api_application.py` — 删除运行时权限扫描端点。

### Backend commands and deployment

- Create via Alembic generator: `migrations/versions/<generated_revision>_align_tree_parent_ids.py`
- Create: `scripts/data/bootstrap_foundation.py`
- Create: `scripts/data/bootstrap_foundation.sh`
- Modify: `scripts/data/sync_permissions.py` — 显式 `--check`/`--apply`/`--preview`/`--repair-cache`，复用目录服务。
- Modify: `scripts/data/sync_permissions.sh`
- Modify: `scripts/data/seed_initial_data.py` — 复用基础服务，暂时保留菜单 seed。
- Modify: `scripts/data/provision_e2e_callback_application.py` — 复用目录服务。
- Delete: `scripts/data/bootstrap_admin.py`
- Delete: `scripts/data/bootstrap_admin.sh`
- Delete: `scripts/data/init_production_base_data.sql`
- Delete: `tests/deployment/test_production_base_data_contract.py`
- Modify: `docker-compose.deploy.yml` — 增加固定前端镜像服务，并让 Nginx 等待前后端健康。
- Modify: `Jenkinsfile.test-deploy`
- Modify: `README.md`
- Modify: `docs/devops/prod-release-deploy.md`
- Modify: `docs/devops/rocky-linux-server-initialization.md`
- Modify: `docs/devops/JENKINS.md`
- Modify: `docs/devops/jenkins-setup-current-env.md`
- Modify: `docs/auth/permission-model.md`
- Modify: `docs/architecture/heavy-test-impact.toml`
- Modify: `TODOS.md`

### Backend tests

- Create: `tests/core/test_tree_primary_key_type.py`
- Modify: `tests/core/test_base_api.py`
- Create: `tests/core/test_tree_api_read_only.py`
- Modify: `tests/admin/test_permission_scanner.py`
- Create: `tests/admin/test_permission_catalog_service.py`
- Create: `tests/admin/test_authorization_bootstrap_service.py`
- Modify: `tests/core/test_rbac_cache_invalidation.py`
- Modify: `tests/admin/test_permission_service_app_cache.py`
- Modify: `tests/api_auth/test_api_app_service_cache.py`
- Create: `tests/core/test_redis_cache_delete_pattern.py`
- Replace: `tests/scripts/test_bootstrap_admin.py` → `tests/scripts/test_bootstrap_foundation.py`
- Modify: `tests/api_auth/test_api_application_routes.py`
- Modify: `tests/integration/test_dev_seed_initial_data_postgresql.py`
- Modify: `tests/deployment/test_local_development_environment.py`
- Create: `tests/deployment/test_production_frontend_compose.py`
- Create: `tests/integration/test_tree_parent_id_migration_postgresql.py`
- Create: `tests/integration/test_permission_catalog_sync_postgresql.py`
- Create: `tests/integration/test_authorization_bootstrap_postgresql.py`
- Create: `tests/admin/test_permission_api_read_only.py`

### Frontend paired contract

以下路径均相对于前端工作树 `/Users/kaizhou/codeDev/wes_frontend-worktrees/refactor-authorization-catalog`：

- Modify generated: `contracts/openapi.current.json`
- Modify generated: `src/api/generated/openapi-types.ts`
- Modify generated: `src/api/generated/openapi-metadata/index.ts`
- Delete generated: `src/api/generated/openapi-metadata/Body_admin_permissions_move_put.ts`.
- Delete generated: `src/api/generated/openapi-metadata/PermissionCreate.ts`.
- Delete generated: `src/api/generated/openapi-metadata/PermissionUpdate.ts`.
- Modify generated: `src/api/generated/permissions/**`
- Modify: `src/api/modules/permissions.ts`
- Modify: `src/api/modules/applications.ts`
- Modify: `scripts/generate-api-types.ts`
- Modify: `src/views/admin/permissions/config/pageConfig.ts`
- Modify: `src/views/admin/api-applications/components/ApiPermissionConfigDialog.vue`
- Modify: `tests/unit/views/adminPageConfigs.test.ts`
- Modify: `tests/unit/scripts/generate-api-types.test.ts`
- Create: `tests/unit/views/admin/permissions/PermissionListPage.test.ts`
- Create: `tests/unit/views/admin/api-applications/ApiPermissionConfigDialog.test.ts`
- Modify generated provenance: `.contract-sync-record.json`, `.permission-sync-record.json`

---

### Task 0: Create isolated paired worktrees and freeze the impact surface

**Files:**

- Create outside Git: `/Users/kaizhou/codeDev/wes-authorization-convergence/execution-context.env`
- Create worktrees: `/Users/kaizhou/codeDev/wes_backend-worktrees/refactor-authorization-catalog`, `/Users/kaizhou/codeDev/wes_frontend-worktrees/refactor-authorization-catalog`

**Interfaces:**

- Consumes: latest remote `develop` for each repository and the approved design spec.
- Produces: clean paired worktrees, exact base SHAs, dirty-state evidence and GitNexus impact report used by Tasks 1–9.

- [ ] **Step 1: Invoke the required worktree skill and inventory both main checkouts**

Use `superpowers:using-git-worktrees`, then run read-only checks from each main repository:

```bash
rtk git status --short --branch
rtk git worktree list
rtk git branch --show-current
rtk git rev-parse HEAD
```

Expected: existing dirty files are recorded and remain untouched.

- [ ] **Step 2: Fetch and create the two feature worktrees from remote `develop`**

```bash
rtk git fetch origin develop
rtk ./scripts/git-worktree.sh add refactor/authorization-catalog
```

Run the repository-local command once in each repository. Expected: both new worktrees are clean and live under their repository-specific `*-worktrees` root. If the helper chooses local `HEAD` rather than `origin/develop`, stop and create the worktree explicitly from the fetched remote SHA.

- [ ] **Step 3: Persist exact execution context outside Git**

Write the two worktree paths, base SHAs, branch names and current date to `execution-context.env`; do not include secrets. Every later task must re-check `git status --short` and the recorded base before editing.

- [ ] **Step 4: Run GitNexus impact analysis**

```bash
rtk npx gitnexus status
rtk npx gitnexus impact sync_permissions_to_db --direction upstream
rtk npx gitnexus impact sync_builtin_role_permissions --direction upstream
rtk npx gitnexus impact PermissionService --direction upstream
```

Also inspect `TreeAPI._register_tree_routes`, `BaseAPI._register_soft_delete_routes` and `sync_system_api_permissions`. Expected: all production callers, tests, generated contracts and HEAVY owners are captured. Report HIGH/CRITICAL paths before continuing.

### Task 1: Align tree parent IDs with production Snowflake primary keys

**Files:**

- Modify: `src/core/mixins/tree.py`
- Modify: `src/app/admin/models/perm.py`
- Create via Alembic generator: `migrations/versions/<generated_revision>_align_tree_parent_ids.py`
- Create: `tests/core/test_tree_primary_key_type.py`
- Create: `tests/integration/test_tree_parent_id_migration_postgresql.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: the current initial migration, `SQL_COMPAT_BIGINT`, `USE_SNOWFLAKE_ID=true`, `Menu`'s inherited tree field, and `PermissionBase`'s FK-bearing `parent_id` override.
- Produces: `Permission.parent_id` and `Menu.parent_id` with the same BIGINT-compatible SQL type as their primary keys, plus a predecessor-to-head PostgreSQL migration proven with a parent ID above signed INT32 range.

- [ ] **Step 1: Add a failing metadata/type contract test**

Under `USE_SNOWFLAKE_ID=true`, assert both `Permission` and `Menu` map `id` and `parent_id` to BIGINT-compatible SQL types. Generate an ID through the production ID path and assert it exceeds `2_147_483_647`; do not replace the real generator with a hand-written large integer in this unit contract.

- [ ] **Step 2: Run the focused test and verify the mismatch**

Run: `rtk uv run pytest tests/core/test_tree_primary_key_type.py -q`

Expected: FAIL because the primary key is BIGINT-compatible while the inherited `TreeMixin.parent_id` and `PermissionBase`'s field override are currently inferred as INTEGER.

- [ ] **Step 3: Generate the Alembic revision and verify a single head**

```bash
rtk uv run alembic heads
rtk uv run alembic revision -m "align tree parent ids"
rtk uv run alembic heads
```

Expected: the generated filename ends in `_align_tree_parent_ids.py`, uses a newly generated revision ID, points to the prior single head, and leaves exactly one head. Stop rather than inventing a merge revision if the pre-change repository already has multiple heads.

- [ ] **Step 4: Align the model and migration**

Change `TreeMixin.parent_id` to reuse the repository's existing `SQL_COMPAT_BIGINT` SQL type. Because `PermissionBase` redeclares that field to attach `foreign_key="wes_sys.permissions.id"` and an index, add the same explicit `sa_type=SQL_COMPAT_BIGINT` to that override while preserving its FK/index; do not assume the inherited type survives a SQLModel field redeclaration. Do not duplicate primary-key generation or add a second tree mixin. In the generated migration, alter both `wes_sys.permissions.parent_id` and `wes_sys.menus.parent_id` from INTEGER to BIGINT with explicit PostgreSQL `USING parent_id::bigint` conversion.

The downgrade must never truncate: abort with a clear error if either column contains a value outside signed INT32 range; only otherwise narrow both columns with explicit casts.

- [ ] **Step 5: Add a real PostgreSQL predecessor-to-head test**

Create an exclusive temporary PostgreSQL database at the migration predecessor, insert a parent `Permission` and parent `Menu` whose BIGINT primary keys exceed signed INT32 range while `parent_id` is null, then migrate to head. Insert children referencing those large parent IDs and assert the relations round-trip. Also create fresh multi-level Permission and Menu trees through the production Snowflake ID path after migration.

Run: `rtk uv run pytest tests/integration/test_tree_parent_id_migration_postgresql.py -q`

Expected: PASS with no skip. SQLite or model metadata alone is not migration evidence.

- [ ] **Step 6: Map HEAVY ownership and commit the schema repair if authorized**

Add exact production-to-test ownership for `src/core/mixins/tree.py`, the `PermissionBase` override and the generated migration, then run the selector contract tests named by `tests/README.md`.

```bash
rtk git add src/core/mixins/tree.py src/app/admin/models/perm.py migrations/versions/<generated_revision>_align_tree_parent_ids.py tests/core/test_tree_primary_key_type.py tests/integration/test_tree_parent_id_migration_postgresql.py docs/architecture/heavy-test-impact.toml
rtk git commit -m "fix(db): 对齐树父节点与 Snowflake 主键类型"
```

### Task 2: Make generic CRUD flags actually produce a read-only API

**Files:**

- Modify: `src/core/base_api.py`
- Modify: `src/core/tree_api.py`
- Modify: `tests/core/test_base_api.py`
- Create: `tests/core/test_tree_api_read_only.py`

**Interfaces:**

- Consumes: existing `gen_create`, `gen_update`, `gen_delete` constructor flags.
- Produces: the invariant that all write routes are absent when their matching flag is false; Task 6 relies on this for `perm_api`.

- [ ] **Step 1: Add a failing soft-delete read-only route test**

Add a test that constructs a soft-delete `BaseAPI` with all three flags false and asserts the registered methods contain only detail/list reads:

```python
write_methods = {"POST", "PUT", "PATCH", "DELETE"}
assert not any(route.methods & write_methods for route in api.router.routes)
```

- [ ] **Step 2: Run the focused test and verify the existing trash/restore routes make it fail**

Run: `rtk uv run pytest tests/core/test_base_api.py -q`

Expected: FAIL because `_register_soft_delete_routes()` ignores `gen_delete=False`.

- [ ] **Step 3: Gate soft-delete routes with `gen_delete`**

At the start of `_register_soft_delete_routes`, return unless both conditions are true:

```python
if not self.supports_soft_delete or not self.gen_delete:
    return
```

- [ ] **Step 4: Add a failing TreeAPI read-only route test**

Construct a `TreeAPI` with `gen_update=False` and assert `PUT /move` and `PUT /batch-sort` are absent while GET tree routes remain.

- [ ] **Step 5: Run the tree test and verify it fails for the two write routes**

Run: `rtk uv run pytest tests/core/test_tree_api_read_only.py -q`

Expected: FAIL with `/move` and `/batch-sort` still registered.

- [ ] **Step 6: Register tree write routes only when `gen_update=True`**

Keep tree/sibling/ancestor/children reads unconditional. Move only the `/move` and `/batch-sort` registration under the existing update capability flag; do not add new constructor options.

- [ ] **Step 7: Run focused core tests**

Run: `rtk uv run pytest tests/core/test_base_api.py tests/core/test_tree_api_read_only.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the semantic flag fix if Commit is authorized**

```bash
rtk git add src/core/base_api.py src/core/tree_api.py tests/core/test_base_api.py tests/core/test_tree_api_read_only.py
rtk git commit -m "fix(core): 让只读 CRUD 标志覆盖全部写接口"
```

### Task 3: Build a fail-closed exact permission catalog service

**Files:**

- Modify: `src/utils/permission_scanner.py`
- Modify: `src/app/admin/repositories/perm_repository.py`
- Create: `src/app/admin/services/permission_catalog_service.py`
- Modify: `src/app/admin/services/__init__.py`
- Modify: `tests/admin/test_permission_scanner.py`
- Create: `tests/admin/test_permission_catalog_service.py`
- Create: `tests/integration/test_permission_catalog_sync_postgresql.py`

**Interfaces:**

- Consumes: `scan_routes_for_permissions(app: FastAPI)` and current `PermissionRepository`.
- Produces: `PermissionCatalogError`, `build_permission_catalog(app: FastAPI) -> list[dict[str, Any]]`, and `PermissionCatalogService.sync(app, db, *, dry_run: bool) -> PermissionCatalogSyncResult`; no method commits.

```python
@dataclass(frozen=True, slots=True)
class PermissionCatalogSyncResult:
    created: int
    updated: int
    deleted: int
    unchanged: int
    total: int
    affected_user_ids: frozenset[int] = frozenset()
    affected_app_ids: frozenset[int] = frozenset()
```

- [ ] **Step 1: Add failing scanner validation tests**

Cover these exact cases with a tiny FastAPI app:

```python
with pytest.raises(PermissionCatalogError, match="未扫描到权限"):
    build_permission_catalog(FastAPI())

with pytest.raises(PermissionCatalogError, match="重复权限码"):
    build_permission_catalog(app_with_conflicting_duplicate_permissions)
```

Also assert repeated scans return the same ordered names and deterministic HTTP method.

- [ ] **Step 2: Run scanner tests and verify they fail**

Run: `rtk uv run pytest tests/admin/test_permission_scanner.py -q`

Expected: FAIL because empty scans currently return an empty list and duplicates are silently skipped.

- [ ] **Step 3: Implement the pure catalog builder**

Add `PermissionCatalogError` and `build_permission_catalog(app)`. It must validate non-empty output, detect a permission name attached to different `(type, method, path)` tuples, sort deterministically, and return leaf plus generated group payloads. Keep database imports out of this pure scanner module.

- [ ] **Step 4: Add failing exact-sync service tests**

Using mocked Repository methods, assert:

```python
assert result == PermissionCatalogSyncResult(
    created=1, updated=1, deleted=1, unchanged=1, total=3,
    affected_user_ids=frozenset({7}),
    affected_app_ids=frozenset({9}),
)
db.commit.assert_not_awaited()
```

Add separate tests proving `dry_run=True` performs no create/update/delete and that a scanner exception performs no Repository mutation.

- [ ] **Step 5: Run the service tests and verify the new symbols are missing**

Run: `rtk uv run pytest tests/admin/test_permission_catalog_service.py -q`

Expected: FAIL on import of `PermissionCatalogService` or `PermissionCatalogSyncResult`.

- [ ] **Step 6: Implement minimal Repository operations and exact sync**

The Repository must expose focused catalog methods; the Service must:

1. build the validated desired catalog;
2. load active and soft-deleted rows, then collect affected user/API Application IDs before changes;
3. reconcile every desired active node in the existing parent-to-child order: category groups, resource groups, then leaves; treat a same-name tombstone as absent, resolve each `parent_id` from the already-active desired parent, and update only `_SYNC_FIELDS` drift;
4. flush so every desired resource group and leaf references its active desired parent before any tombstone is deleted;
5. form one deletion set from active nodes absent from the desired catalog plus every soft-deleted node; repeatedly delete the current leaves of that set, defined from the actual `parent_id` graph, and flush before processing the next parent layer;
6. raise `PermissionCatalogError` and roll back if a non-empty deletion set has no leaf, because that proves a cycle; never sort deletion by semantic node type or derived `level/tree_path`;
7. rely on FK cascade for each deleted node's role/API Application links; replacement nodes never inherit tombstone IDs or authorization links;
8. return affected IDs for post-commit cache invalidation;
9. flush but never commit.

Do not keep the old dict-returning `sync_permissions_to_db` wrapper.

- [ ] **Step 7: Add PostgreSQL integration coverage**

Create one test that seeds a stale permission plus role/API Application relations, runs exact sync, and asserts the stale permission and both relations are gone. Create a second test where a desired catalog name already has a soft-deleted row with role/API Application relations; assert the old row and both relations are deleted, a new active row is created with a different ID, and no old authorization link is transferred. Create a third test with soft-deleted desired category and resource groups still referenced as `category group → resource group → active desired leaf`; assert sync creates both replacement groups, reparents the active leaf through the new hierarchy before deleting either tombstone, removes both old groups, and completes without an FK violation. Create a fourth test with same-type stale nodes linked as parent and child; assert the actual `parent_id` graph makes the child delete before the parent. Create a fifth test that injects a Repository failure after an update, rolls back the surrounding transaction and proves the pre-test database state remains.

- [ ] **Step 8: Run catalog unit and integration tests**

Run unit: `rtk uv run pytest tests/admin/test_permission_scanner.py tests/admin/test_permission_catalog_service.py -q`

Run integration only against the plan-owned PostgreSQL database: `rtk uv run pytest tests/integration/test_permission_catalog_sync_postgresql.py -q`

Expected: PASS; integration must execute, not skip.

- [ ] **Step 9: Commit the catalog slice if authorized**

```bash
rtk git add src/utils/permission_scanner.py src/app/admin/repositories/perm_repository.py src/app/admin/services/permission_catalog_service.py src/app/admin/services/__init__.py tests/admin/test_permission_scanner.py tests/admin/test_permission_catalog_service.py tests/integration/test_permission_catalog_sync_postgresql.py
rtk git commit -m "refactor(auth): 收敛代码所有的权限目录"
```

### Task 4: Introduce the single authorization bootstrap owner

**Files:**

- Create: `src/app/admin/services/authorization_bootstrap_service.py`
- Modify: `src/app/admin/repositories/role_repository.py`
- Modify: `src/app/admin/repositories/user_repository.py`
- Modify: `src/app/admin/services/__init__.py`
- Modify: `src/core/rbac.py`
- Modify: `src/app/api_auth/services/permission_service.py`
- Modify: `src/database/redis_cache.py`
- Create: `tests/admin/test_authorization_bootstrap_service.py`
- Modify: `tests/core/test_rbac_cache_invalidation.py`
- Modify: `tests/admin/test_permission_service_app_cache.py`
- Modify: `tests/api_auth/test_api_app_service_cache.py`
- Create: `tests/core/test_redis_cache_delete_pattern.py`
- Create: `tests/integration/test_authorization_bootstrap_postgresql.py`

**Interfaces:**

- Consumes: `PermissionCatalogService.sync`, `BootstrapFoundationConfig(username, password, full_name, email)`.
- Produces: `BUILTIN_ROLE_SPECS`, `BootstrapFoundationConfig`, `AuthorizationSyncResult`, `FoundationBootstrapResult`, `AuthorizationCacheInvalidationError`, `AuthorizationBootstrapService.converge_authorization(app, db, *, dry_run=False) -> AuthorizationSyncResult`, `AuthorizationBootstrapService.bootstrap(app, db, config) -> FoundationBootstrapResult`, `AuthorizationBootstrapService.invalidate_caches(result, cache) -> None`, and `AuthorizationBootstrapService.repair_permission_cache_namespaces(cache) -> None`; no convergence/bootstrap method commits. Both invalidation methods return normally only after Redis confirms success and otherwise raise `AuthorizationCacheInvalidationError`.

```python
@dataclass(frozen=True, slots=True)
class BootstrapFoundationConfig:
    username: str
    password: str
    full_name: str | None = None
    email: str | None = None

@dataclass(frozen=True, slots=True)
class AuthorizationSyncResult:
    roles: dict[str, int]
    permissions: PermissionCatalogSyncResult
    role_permissions: dict[str, int]
    affected_user_ids: frozenset[int]

@dataclass(frozen=True, slots=True)
class FoundationBootstrapResult:
    authorization: AuthorizationSyncResult
    admin_action: str
    admin_username: str
    admin_role_added: bool
```

- [ ] **Step 1: Add failing service tests for built-in roles and administrator ownership**

Assert the exact role names are:

```python
{"系统管理员", "管理员", "运营人员", "财务人员", "普通用户"}
```

Test fresh creation, description repair, existing-superuser reuse, missing system-role link insertion and second-run idempotence. Assert unrelated user-created roles and role links remain untouched. When an existing built-in role gains or loses any `role_permissions` link, assert `affected_user_ids` contains every current user assigned to that role; when no link changes, those users must not be reported. Add cache tests proving exact user/API Application deletions are checked, only failed IDs are retried for three total attempts, and any remaining failure raises with the failed IDs.

- [ ] **Step 2: Run the unit tests and verify the service is absent**

Run: `rtk uv run pytest tests/admin/test_authorization_bootstrap_service.py -q`

Expected: FAIL on import.

- [ ] **Step 3: Add focused Repository methods**

Add only the methods required by the service: active roles by name and an idempotent user-role link insert. Reuse the existing role-member query for affected-user collection instead of issuing SQL from the Service; add a focused Repository method only if the current query cannot accept the resolved built-in role IDs. Repository methods flush but do not commit.

- [ ] **Step 4: Implement the bootstrap service**

The service sequence is fixed:

```python
authorization = await self.converge_authorization(app, db, dry_run=False)
admin = await self.ensure_first_superuser(db, config)
admin_role_added = await self.ensure_system_admin_role(db, admin)
```

It must not create development users, menus or business facts. Built-in role permission matching moves here from `permission_scanner.py` and is exact for all current catalog names. Before changing a built-in role's permission links, determine whether that role has any add/remove delta; for each changed role, collect all assigned user IDs and union them with `PermissionCatalogSyncResult.affected_user_ids`.

Change the existing user and API Application invalidation helpers to return Redis deletion success instead of discarding it. `invalidate_caches` runs only after the caller's successful commit, invalidates exactly that union plus the catalog result's affected API Application IDs, and retries only failed IDs for three total attempts with fixed short backoff. A rollback, `--check` or `--preview` performs no cache invalidation. If any ID still fails, raise `AuthorizationCacheInvalidationError`; never print success merely because the helper was invoked.

For the exceptional operator recovery path, `repair_permission_cache_namespaces` deletes only `perms:user:*` and `api_app:perms:*`. Change `RedisCache.delete_pattern` to return `None` for unavailable/error and an integer (including zero for no matching keys) for a successful operation, so recovery can distinguish failure without adding a second generic cache API. Either namespace failure raises and is safely retryable.

- [ ] **Step 5: Add fresh-DB and rollback integration tests**

On a clean PostgreSQL schema assert all five roles, the exact permission set, the superuser and its system role link exist. Seed users on two built-in roles, force one role-permission add/remove delta and assert only members of changed roles are returned for invalidation. Run a second time and assert all change counts and affected-ID sets are empty. Inject a failure before the final flush and assert the caller rollback leaves no partial roles, permissions or user.

- [ ] **Step 6: Run unit and integration tests**

Run: `rtk uv run pytest tests/admin/test_authorization_bootstrap_service.py tests/core/test_rbac_cache_invalidation.py tests/admin/test_permission_service_app_cache.py tests/api_auth/test_api_app_service_cache.py tests/core/test_redis_cache_delete_pattern.py tests/integration/test_authorization_bootstrap_postgresql.py -q`

Expected: PASS with no skips.

- [ ] **Step 7: Commit the bootstrap service if authorized**

```bash
rtk git add src/app/admin/services/authorization_bootstrap_service.py src/app/admin/repositories/role_repository.py src/app/admin/repositories/user_repository.py src/app/admin/services/__init__.py src/core/rbac.py src/app/api_auth/services/permission_service.py src/database/redis_cache.py tests/admin/test_authorization_bootstrap_service.py tests/core/test_rbac_cache_invalidation.py tests/admin/test_permission_service_app_cache.py tests/api_auth/test_api_app_service_cache.py tests/core/test_redis_cache_delete_pattern.py tests/integration/test_authorization_bootstrap_postgresql.py
rtk git commit -m "feat(auth): 建立唯一基础授权初始化服务"
```

### Task 5: Replace all bootstrap and permission-sync entry points

**Files:**

- Create: `scripts/data/bootstrap_foundation.py`
- Create: `scripts/data/bootstrap_foundation.sh`
- Modify: `scripts/data/sync_permissions.py`
- Modify: `scripts/data/sync_permissions.sh`
- Modify: `scripts/data/seed_initial_data.py`
- Modify: `scripts/data/provision_e2e_callback_application.py`
- Delete: `scripts/data/bootstrap_admin.py`
- Delete: `scripts/data/bootstrap_admin.sh`
- Replace: `tests/scripts/test_bootstrap_admin.py` → `tests/scripts/test_bootstrap_foundation.py`
- Modify: `tests/integration/test_dev_seed_initial_data_postgresql.py`
- Modify: `tests/deployment/test_local_development_environment.py`

**Interfaces:**

- Consumes: Task 3/4 services.
- Produces: one fresh-DB command, one explicit reconciliation command, and one exceptional permission-cache recovery mode; development seed retains its dev-only users and current menu data until the next plan.

- [ ] **Step 1: Add failing command tests**

Test that `bootstrap_foundation` commits once after a complete result, invokes cache invalidation only after that commit, never invalidates after rollback, and validates the existing `BOOTSTRAP_ADMIN_*` environment variables. A post-commit cache failure must produce a distinct non-zero result without claiming the already committed database transaction was rolled back.

Test that `sync_permissions` requires exactly one of `--check`, `--apply`, `--preview`, or `--repair-cache`; only a successful `--apply` invalidates the exact user/API Application IDs returned by the Service. `--repair-cache` must call only `repair_permission_cache_namespaces`, must not connect to PostgreSQL or rebuild the catalog, and must exit non-zero if either fixed namespace cannot be confirmed deleted.

- [ ] **Step 2: Run command tests and verify they fail on missing entry points/options**

Run: `rtk uv run pytest tests/scripts/test_bootstrap_foundation.py -q`

Expected: FAIL because the new script does not exist.

- [ ] **Step 3: Implement the thin bootstrap command**

`bootstrap_foundation.py` must load config, call `create_app()`, open one database context, invoke `AuthorizationBootstrapService.bootstrap`, commit once, then invoke `invalidate_caches`. Exceptions before commit roll back; cache invalidation failure after commit reports `DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED` and exits non-zero without a false rollback claim or success banner. The shell wrapper only resolves the repository root and runs the Python command.

- [ ] **Step 4: Replace permission CLI modes**

Use a mutually exclusive required argparse group:

```python
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument("--check", action="store_true")
mode.add_argument("--apply", action="store_true")
mode.add_argument("--preview", action="store_true")
mode.add_argument("--repair-cache", action="store_true")
```

`--check` calls `converge_authorization(..., dry_run=True)` and exits non-zero if any role, permission or role-permission change count is non-zero; `--apply` commits once and invalidates the affected caches after commit, using the same distinct post-commit failure status as bootstrap; `--preview` never connects to the database; `--repair-cache` never connects to the database and succeeds only after both fixed permission-cache namespaces are deleted. The recovery mode is not part of a successful normal run and must not clear `*` or unrelated cache prefixes.

- [ ] **Step 5: Rewire dev seed and E2E provisioning**

Move `ROLE_SEEDS` ownership to the bootstrap service. Dev seed calls the shared authorization convergence inside its existing single transaction, then creates only dev users/user-role links and temporarily keeps menu convergence. E2E provisioning calls `PermissionCatalogService.sync` before assigning its callback permission.

- [ ] **Step 6: Delete old bootstrap files and run focused tests**

Run: `rtk uv run pytest tests/scripts/test_bootstrap_foundation.py tests/integration/test_dev_seed_initial_data_postgresql.py tests/deployment/test_local_development_environment.py -q`

Expected: PASS; the integration test must execute. Then run `rtk rg -n "bootstrap_admin|ROLE_SEEDS|sync_permissions_to_db|sync_builtin_role_permissions" src scripts tests` and resolve every obsolete owner or reference.

- [ ] **Step 7: Commit the entry-point replacement if authorized**

```bash
rtk git add scripts/data/bootstrap_foundation.py scripts/data/bootstrap_foundation.sh scripts/data/sync_permissions.py scripts/data/sync_permissions.sh scripts/data/seed_initial_data.py scripts/data/provision_e2e_callback_application.py scripts/data/bootstrap_admin.py scripts/data/bootstrap_admin.sh tests/scripts/test_bootstrap_admin.py tests/scripts/test_bootstrap_foundation.py tests/integration/test_dev_seed_initial_data_postgresql.py tests/deployment/test_local_development_environment.py
rtk git commit -m "refactor(auth): 统一权限同步与基础初始化入口"
```

### Task 6: Make the permission catalog read-only and remove runtime scanning

**Files:**

- Modify: `src/app/admin/v1/perm.py`
- Modify: `src/app/admin/models/perm.py`
- Modify: `src/app/admin/models/__init__.py`
- Modify: `src/app/admin/services/perm_service.py`
- Modify: `src/app/api_auth/v1/api_application.py`
- Modify: `tests/api_auth/test_api_application_routes.py`
- Create: `tests/admin/test_permission_api_read_only.py`

**Interfaces:**

- Consumes: Task 2 read-only flag semantics and Task 3 catalog service.
- Produces: read-only permission discovery routes, no runtime catalog mutation endpoint, and no stale generic detail/list cache between catalog reconciliation and management reads.

- [ ] **Step 1: Add failing OpenAPI route assertions**

Assert the permission resource exposes GET tree/detail and POST query, but none of these operation families: create, update, delete, trash, restore, permanent delete, move or batch sort. Assert `/api/v1/api_auth/applications/available-permissions/sync` is absent. Assert the constructed `PermissionService` has generic entity/list caching disabled while `get_user_permissions` and API Application permission-cache behavior remain covered by their dedicated tests.

- [ ] **Step 2: Run focused API tests and verify current write routes fail the assertions**

Run: `rtk uv run pytest tests/admin/test_permission_api_read_only.py tests/api_auth/test_api_application_routes.py -q`

Expected: FAIL with current write and runtime sync operations present.

- [ ] **Step 3: Configure `perm_api` as read-only**

Pass `create_schema=None`, `update_schema=None`, `gen_create=False`, `gen_update=False`, and `gen_delete=False`. Construct `PermissionService` with `enable_cache=False` and remove now-unused generic permission cache configuration imports; do not alter the separate effective-permission caches in `src/core/rbac.py` or `src/app/api_auth/services/permission_service.py`. Remove `PermissionCreate`/`PermissionUpdate` and dead PermissionService write/cache-hook code that has no remaining caller; keep permission queries and role/API Application assignment behavior.

- [ ] **Step 4: Delete the API Application runtime sync endpoint**

Remove only `available-permissions/sync`; retain `available-permissions` read and assignment endpoints. Remove its now-dead permission code through normal scanner/codegen convergence rather than keeping a constant.

- [ ] **Step 5: Run API, permission and API Application regressions**

Run: `rtk uv run pytest tests/admin/test_permission_api_read_only.py tests/admin/test_permission_service_app_cache.py tests/api_auth/test_api_application_routes.py tests/api_auth/test_api_app_service_cache.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the read-only contract if authorized**

```bash
rtk git add src/app/admin/v1/perm.py src/app/admin/models/perm.py src/app/admin/models/__init__.py src/app/admin/services/perm_service.py src/app/api_auth/v1/api_application.py tests/admin/test_permission_api_read_only.py tests/api_auth/test_api_application_routes.py
rtk git commit -m "refactor(auth): 禁止运行时修改权限定义"
```

### Task 7: Regenerate the frontend contract and remove permission write UI

本任务所有命令均在 `/Users/kaizhou/codeDev/wes_frontend-worktrees/refactor-authorization-catalog` 执行；开始前必须核对当前工作目录
和 Task 0 冻结的前端工作树一致，不得在前端主工作区执行。

**Files:**

- Modify generated contract and permission files listed in File Map.
- Modify: `src/api/modules/permissions.ts`
- Modify: `src/api/modules/applications.ts`
- Modify: `scripts/generate-api-types.ts`
- Modify: `src/views/admin/permissions/config/pageConfig.ts`
- Modify: `src/views/admin/api-applications/components/ApiPermissionConfigDialog.vue`
- Modify: `tests/unit/views/adminPageConfigs.test.ts`
- Modify: `tests/unit/scripts/generate-api-types.test.ts`
- Create: `tests/unit/views/admin/permissions/PermissionListPage.test.ts`
- Create: `tests/unit/views/admin/api-applications/ApiPermissionConfigDialog.test.ts`

**Interfaces:**

- Consumes: clean backend candidate Commit from Task 6, the existing `createReadonlyCrudRequestAdapterFromMethods` helper and frontend contract-generation scripts.
- Produces: generator capability `kind: 'readonly'`, `ReadonlyInput = Record<string, never>`, and a frontend that can query permissions and assign API Application permissions but has no permission-definition write controls or runtime rescan button.

- [ ] **Step 1: Add a failing read-only resource generator test**

Build a synthetic resource containing only `GET /{id}` and `POST /query`. Assert `classifyCrudCapabilities` returns `kind: 'readonly'` and generated module source imports/reuses `createReadonlyCrudRequestAdapterFromMethods` without emitting create/update/delete input types.

- [ ] **Step 2: Run the generator test and verify it fails**

Run: `rtk pnpm test -- tests/unit/scripts/generate-api-types.test.ts`

Expected: FAIL because current capability classification returns `none` and emits an unusable resource module.

- [ ] **Step 3: Implement read-only generation by reusing the existing adapter**

Extend `CrudCapabilities.kind` with `readonly`. Classify exact detail+query resources as read-only, emit the existing read-only adapter import and `ReadonlyInput` alias, and keep write endpoint generation absent. Do not create a second adapter.

- [ ] **Step 4: Create a clean local contract checkout at the backend candidate Commit**

Clone the backend worktree without shared local state into `/Users/kaizhou/codeDev/wes_backend-contract-authorization`, switch its local branch name to `develop`, verify the candidate SHA and require an empty status. Do not modify the backend feature worktree to satisfy the frontend gate.

- [ ] **Step 5: Write failing frontend visibility tests**

Assert resolved permission page features have `create/edit/delete/trash/restore/permanentDelete/move/sort/createChild` disabled. Mount `ApiPermissionConfigDialog` and assert no “同步权限” button or sync request exists.

- [ ] **Step 6: Run the focused tests and verify current controls make them fail**

Run: `rtk pnpm test -- tests/unit/views/admin/permissions/PermissionListPage.test.ts tests/unit/views/admin/api-applications/ApiPermissionConfigDialog.test.ts`

Expected: FAIL because current pages expose writes and runtime sync.

- [ ] **Step 7: Freeze and regenerate from the clean candidate checkout**

```bash
rtk pnpm contract:freeze -- --backend-root /Users/kaizhou/codeDev/wes_backend-contract-authorization
rtk pnpm generate:types
rtk pnpm generate:zod
rtk pnpm generate:permissions -- --backend-root /Users/kaizhou/codeDev/wes_backend-contract-authorization
```

Expected: generated permission write operations and `sync_permissions` disappear; no hand edits occur inside generated regions.

- [ ] **Step 8: Make the permission page explicitly read-only**

Set all write `CrudPageFeatures` to `false`, retain refresh/detail/tree presentation, use `ReadonlyInput = Record<string, never>` for both form generics, and change the subtitle to “查看后端路由生成的 API 权限目录”. Remove the API Application sync button and method while retaining permission assignment.

- [ ] **Step 9: Run frontend focused and contract tests**

```bash
rtk pnpm test -- tests/unit/scripts/generate-api-types.test.ts tests/unit/views/admin/permissions/PermissionListPage.test.ts tests/unit/views/admin/api-applications/ApiPermissionConfigDialog.test.ts tests/unit/views/adminPageConfigs.test.ts
rtk pnpm contract:test
rtk pnpm contract:verify
rtk pnpm permission:verify -- --backend-root /Users/kaizhou/codeDev/wes_backend-contract-authorization
```

Expected: PASS.

- [ ] **Step 10: Regenerate a second time and assert no diff**

Run the four generation commands again, then `rtk git status --short`. Expected: no new changes beyond the intended first-generation diff.

- [ ] **Step 11: Commit the paired frontend contract if authorized**

Stage only the generated contract/provenance files, permission page, API Application dialog and their tests; commit:

```bash
rtk git commit -m "refactor(auth): 将权限目录界面改为只读"
```

### Task 8: Remove the dead static SQL and update executable deployment ownership

**Files:**

- Delete: `scripts/data/init_production_base_data.sql`
- Delete: `tests/deployment/test_production_base_data_contract.py`
- Modify: `docker-compose.deploy.yml`
- Create: `tests/deployment/test_production_frontend_compose.py`
- Modify: `Jenkinsfile.test-deploy`, `README.md`, listed devops/auth docs, HEAVY mapping and `TODOS.md`.

**Interfaces:**

- Consumes: `bootstrap_foundation` and explicit permission CLI modes.
- Produces: one documented fresh-DB owner and no executable/static references to the stale SQL.

- [ ] **Step 1: Replace deployment commands**

For fresh initialization use:

```bash
bash scripts/data/bootstrap_foundation.sh
uv run python scripts/data/sync_permissions.py --check
```

For an existing deployed database use `--apply` followed by `--check`. Jenkins test deploy must run bootstrap on its fresh test database and fail if the subsequent check reports drift. `--repair-cache` is documented only as recovery from `DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED`, never as a routine replacement for exact invalidation.

Add the production `frontend` service to `docker-compose.deploy.yml` by reusing the existing test-deploy service shape: require
`${FRONTEND_IMAGE:?FRONTEND_IMAGE is required}`, expose port `5173` only on `wesp9-network`, and keep its health check. In the same overlay,
extend `nginx.depends_on` so Nginx waits for both `api` and `frontend` to be healthy. Do not create another production overlay or build frontend
source on the server; the approved immutable frontend image is the only production input.

The paired cutover order is mandatory:

1. pull and pin the approved backend/frontend image pair;
2. put the external entry point into maintenance mode, then use Compose project/service labels to stop every old application container while leaving only PostgreSQL/Redis running;
3. run Alembic from the new backend image as a one-shot command;
4. run `bootstrap_foundation` for a fresh database, or permission `--apply` for an existing database, then require permission `--check` to report zero drift; if the database committed but exact cache invalidation failed, keep the entry point closed, run `--repair-cache`, and repeat `--check` before continuing;
5. start the pinned `api`, `celery`, `celery-wms-fulfillment`, `celery_beat`, `flower` and `frontend` containers, then verify backend readiness, frontend asset availability and the frozen OpenAPI/permission provenance;
6. restore the external entry point only after every gate passes; any failure leaves maintenance mode active and reports the failed stage.

Do not recreate exposed application services before Steps 3–4 finish, and do not add compatibility endpoints or dual schema paths for rolling deployment. For the current topology, "maintenance mode" means stopping Nginx and verifying its external port is closed before old application processes are stopped; a failed gate leaves Nginx stopped rather than requiring a new maintenance-page subsystem.
Application discovery must reuse the Compose label inspection from `docs/devops/prod-release-deploy.md`, not container names. Before Steps 3–4,
the current Compose project may retain only `db` and `redis`; an unknown remaining application service fails the cutover closed. Nginx stays stopped
until Step 6.

- [ ] **Step 2: Delete static SQL and its text-content tests**

Delete both files directly. Do not archive the SQL and do not create a replacement text assertion; Tasks 3–5 own executable behavior.

- [ ] **Step 3: Update machine-readable HEAVY ownership**

Remove the old SQL source mapping and add exact mappings for the new services/scripts and PostgreSQL integration tests. Run the selector contract tests named by `tests/README.md` and ensure unknown new production files do not fall through unowned.

- [ ] **Step 4: Update current documentation and backlog status**

Document route-owned permission definitions, read-only permission UI, `bootstrap_foundation`, explicit sync modes and evidence boundaries. The current Reliability TODO combines multiple gaps: split or rewrite it so the tree-ID repair and initialization-owner portions are marked resolved by Tasks 1 and 3–5, while the unrelated cross-process readiness item remains active.

- [ ] **Step 5: Verify stale references are absent**

```bash
rtk rg -n "init_production_base_data|bootstrap_admin|available-permissions/sync|sync_permissions_to_db|sync_builtin_role_permissions" .
rtk git diff --check
```

Expected: no obsolete runtime/document references; valid migration history mentions outside this search scope are not rewritten.

- [ ] **Step 6: Run deployment and topology tests**

```bash
rtk uv run pytest tests/deployment tests/scripts/test_bootstrap_foundation.py tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
rtk uv run pytest --collect-only -q -o addopts='' | rtk tail -5
```

Expected: PASS; no replacement SQL-content test exists.

The Jenkins/deployment tests must also assert migration and authorization convergence occur while the external entry point is in maintenance mode, application services start only after a zero-drift check, and every failed pre-exposure stage leaves the entry point closed. Cover the post-commit Redis failure explicitly: it must not rerun database mutation blindly, must not start application services, and may continue only after the two-namespace repair command succeeds and a fresh `--check` reports zero drift.
Create `tests/deployment/test_production_frontend_compose.py` as the single owner of the production frontend Compose contract. It must assert that
`frontend.image` comes from required `FRONTEND_IMAGE`, shares `wesp9-network`, has a health check, and is a healthy dependency of Nginx.

- [ ] **Step 7: Commit executable deployment changes and docs separately if authorized**

The executable deployment commit includes `docker-compose.deploy.yml` and
`tests/deployment/test_production_frontend_compose.py`. Commit scripts/Compose/Jenkins/tests/mapping first as
`refactor(deploy): 统一基础授权初始化入口`; then commit Markdown/TODOS updates as `docs(auth): 更新权限初始化唯一真源`.

### Task 9: Final cross-repository verification and immutable handoff

**Files:**

- Verify all changed files in both worktrees; no new production edits expected.

**Interfaces:**

- Consumes: completed Tasks 1–8.
- Produces: review-ready paired Commits and evidence explicitly bounded below deployment/business acceptance.

- [ ] **Step 1: Freeze final backend and frontend diffs**

Record `git status --short`, base/head SHA, changed-path manifest and staged fingerprint separately. Confirm no original main-checkout dirty file entered either worktree.

- [ ] **Step 2: Run backend focused aggregate**

```bash
rtk uv run pytest tests/core/test_tree_primary_key_type.py tests/core/test_base_api.py tests/core/test_tree_api_read_only.py tests/core/test_rbac_cache_invalidation.py tests/core/test_redis_cache_delete_pattern.py tests/admin/test_permission_scanner.py tests/admin/test_permission_catalog_service.py tests/admin/test_authorization_bootstrap_service.py tests/admin/test_permission_service_app_cache.py tests/admin/test_permission_api_read_only.py tests/api_auth/test_api_application_routes.py tests/api_auth/test_api_app_service_cache.py tests/scripts/test_bootstrap_foundation.py -q
```

Expected: PASS.

- [ ] **Step 3: Run backend migration/fresh-DB and quality gates**

Use an exclusive temporary PostgreSQL database for these three suites:

```bash
rtk uv run pytest tests/integration/test_tree_parent_id_migration_postgresql.py tests/integration/test_permission_catalog_sync_postgresql.py tests/integration/test_authorization_bootstrap_postgresql.py -q
```

Then run:

```bash
rtk ./scripts/git-quality-gate.sh --profile quality
rtk uv run scripts/select_heavy_tests.py --scope unstaged
rtk ./scripts/run_selected_heavy_local.sh --scope unstaged
```

Expected: QUALITY passes and exactly the selector-owned HEAVY manifest passes; `NONE` is acceptable only if the mapping resolves it explicitly.

- [ ] **Step 4: Run frontend gates**

```bash
rtk pnpm test
rtk pnpm lint
rtk pnpm contract:test
rtk pnpm contract:verify
rtk pnpm permission:verify -- --backend-root /Users/kaizhou/codeDev/wes_backend-contract-authorization
```

Expected: PASS.

- [ ] **Step 5: Perform one full review and close findings**

Use one project review workflow against the frozen base/head. After fixes, rerun only invalidated evidence, then perform one fresh full review. Distinguish code/test evidence from deployment and business acceptance.

- [ ] **Step 6: Re-freeze provenance after backend merge, before any paired deployment**

From a clean real backend `develop`, rerun frontend contract/permission freeze. Expected: generated semantic content is unchanged; only provenance SHA may change after squash/merge. Any semantic diff reopens review.

- [ ] **Step 7: Hand off the pair**

Report backend Commit, frontend Commit, OpenAPI SHA, permission SHA, tests, selector manifest and unverified boundaries. Status before separately authorized deployment must be `IMPLEMENTED — NOT DEPLOYED`; never claim WMS/ECS/site/business acceptance.
