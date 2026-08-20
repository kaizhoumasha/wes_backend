# API 权限目录模型

> 状态：current implementation contract
> 适用范围：后端 API 权限定义、物化目录、内置角色授权与初始化/修复入口。菜单仍由独立菜单模型和同步入口维护。

## 1. 唯一真源与边界

FastAPI 路由上的 `RequirePermission(...)` / `RequireAPIPermission(...)` 声明是 API 权限定义的唯一真源。
`wes_sys.permissions` 只是该代码目录的只读物化结果，不是人工维护的第二真源。

- `user_api`：内部用户 API 权限。
- `app_api`：外部 API Application 权限。
- 权限码固定为 `module:resource:action` 三段非空格式。
- `Permission` 只描述后端 API 权限，不承载菜单或按钮定义。
- 菜单可见性不能替代后端权限检查；菜单同步仍由 `sync_menus.py` 独立负责。

角色和 API Application 可以继续维护其“关联了哪些权限”的关系；这不赋予它们创建、修改或删除权限定义的能力。

## 2. 只读管理面

`/api/v1/admin/permissions` 只提供查询能力：详情、树和 `POST /query`。当前路由显式关闭：

- create / update；
- delete / trash / restore / permanent delete；
- move / sort。

权限管理页面因此只能查看物化目录。若代码路由声明有误，应修改路由声明和测试，再重新收敛目录；不得直接编辑数据库记录或增加临时写接口。

## 3. 目录构建与 fail-closed 规则

`src/utils/permission_scanner.py` 扫描应用路由并生成确定性目录：

1. 读取路由依赖中的用户或应用权限声明；
2. 校验每个叶子权限的名称、类型、分类、资源、动作、HTTP method 和 path；
3. 生成 category/resource 分组节点；
4. 按稳定规则排序并建立父子关系。

以下情况必须失败，不能产出部分目录：

- 没有扫描到任何权限；
- 权限码不是严格的三段非空格式；
- 叶子缺少必填字段；
- 同名权限指向不同的 type/method/path；
- 叶子名称与派生分组名称冲突；
- 数据库存在重复活动权限码，或待删除目录形成无效/循环图。

## 4. 精确物化语义

`PermissionCatalogService.sync()` 将代码目录精确收敛到数据库：

- 缺失节点创建；
- 字段漂移节点更新；
- 代码中已不存在的节点按叶子到父节点顺序物理删除；
- 删除前收集受影响的用户和 API Application，用于精确缓存失效；
- 任一步失败都回滚当前事务。

`AuthorizationBootstrapService` 在同一授权事务中继续收敛五个内置角色及其精确权限集合。fresh DB 初始化还创建首个超级管理员并确保其关联“系统管理员”角色。它不拥有菜单同步。

## 5. 唯一可执行入口

### 5.1 本地预览

```bash
uv run python scripts/data/sync_permissions.py --preview
```

`--preview` 只扫描代码，不连接 PostgreSQL，也不改变数据库或缓存。

### 5.2 Fresh DB

迁移完成、应用仍处于维护态时，注入 `BOOTSTRAP_ADMIN_*`，然后执行：

```bash
bash scripts/data/bootstrap_foundation.sh
uv run python scripts/data/sync_permissions.py --check
```

`bootstrap_foundation` 是 fresh DB 的唯一基础授权入口，统一拥有五个内置角色、API 权限目录、内置角色授权和首个超级管理员。成功后必须以独立的 `--check` 证明零漂移。

### 5.3 已有数据库直接替换

```bash
uv run python scripts/data/sync_permissions.py --apply
uv run python scripts/data/sync_permissions.py --check
```

`--apply` 是已有数据库切换到当前代码目录的显式 mutation；`--check` 是只读门禁。禁止保留旧同步脚本、静态 SQL、双路径或兼容入口。

## 6. 事务与缓存恢复

数据库 mutation 和缓存失效是两个阶段：

1. 授权事务先提交；
2. 仅对受影响用户、API Application 及角色关系执行精确缓存失效；
3. 缓存失效失败时命令以独占整行的 `DATABASE_COMMITTED_CACHE_INVALIDATION_FAILED`、下一行 `CACHE_INVALIDATION_FAILURE_DETAIL:` 诊断和退出码 `3` 结束。

受控发布流程只在输出包含上述精确裸 marker 行时认定数据库已经提交；`marker: detail` 等子串形式不进入恢复分支。此时不得重复 bootstrap 或 `--apply`；保持 Nginx 关闭，只执行一次 repair，再执行新的检查：

```bash
uv run python scripts/data/sync_permissions.py --repair-cache
uv run python scripts/data/sync_permissions.py --check
```

`--repair-cache` 只清理当前数据库前缀下的两个权限缓存命名空间，不扫描或修改数据库。没有专用裸 marker 行的失败不得进入 repair 分支。

## 7. 部署顺序

生产和 TEST 切换都必须遵守以下顺序：固定前后端镜像 digest 并核对 backend/frontend revision、frontend 绑定的 backend revision 与 OpenAPI/permission labels → 关闭 Nginx 并用非 `-f` 监听探针确认外部端口关闭 → 通过已检查的 Compose 标签发现停止旧应用且拒绝未知 service → 新后端镜像执行 Alembic → fresh DB bootstrap 或已有 DB `--apply` → 独立 `--check` → 启动固定版本应用并完成内部校验 → 最后只启动 Nginx。Jenkins TEST 每次部署创建唯一新数据库并先证明为空，不清空持久 volume，也不把旧 TEST 数据库冒充 fresh DB。

任何迁移、授权、缓存修复、内部 readiness 或来源校验失败，都必须保持外部入口关闭。完整命令见：

- `docs/devops/prod-release-deploy.md`
- `docs/devops/JENKINS.md`
- `docs/devops/jenkins-setup-current-env.md`

## 8. 证据边界

- `--preview`：证明代码目录可以构建，不证明数据库状态。
- `--check`：证明目标数据库与当前代码权限及内置角色授权零漂移，不证明菜单、服务 readiness 或部署完成。
- 本地/CI 测试：证明 WES 自有实现和部署契约，不证明已部署、供应商一致、现场联调或业务验收。
- 菜单同步成功：只证明菜单物化，不提升为 API 授权或业务验收证据。
