# 联调发布可靠性改进实施计划

**目标：** 让本机检查、TEST/现场发布与恢复入口绑定同一源码、标准 Compose 拓扑、可用超级管理员凭据和真实 HTTP readiness；任一门禁失败时入口保持关闭。

**设计真源：** `docs/superpowers/specs/2026-08-24-integration-release-reliability-design.md`。

**实施状态：** 本文是待执行计划；所有 checkbox 均不表示已完成。

## Global Constraints

- 项目尚未发布：不增加旧 schema、旧菜单、旧接口、服务别名、shim、fallback 或双路径。
- 菜单唯一真源仅由 `/Users/kaizhou/codeDev/wes_frontend/docs/superpowers/plans/2026-08-20-frontend-owned-menu-convergence.md` 实施；不得扩展 `sync_menus.py` 或暂时恢复菜单 exact-sync。
- PostgreSQL 备份、`0600`、SHA-256、异机副本和隔离恢复演练仅由 `docs/superpowers/plans/2026-08-18-wes-onsite-data-recovery.md` 实施；不得新增 backup helper。
- Beat、Redis/THP、Nginx nofile/logrotate 与 PostgreSQL `idle_in_transaction_session_timeout` 仅由 `docs/superpowers/plans/2026-08-18-wes-onsite-runtime-hardening.md` 实施。
- 管理员门禁只做登录验证并撤销本次会话；不得创建管理员、修改密码或调用 `logout-all`。密码、token、Cookie、`.env.prod` 内容和完整响应不得进入输出、异常、Jenkins 日志、测试快照或发布记录。
- 当前 Compose 渲染服务集合是唯一期望拓扑；不接受 `ALLOW_MISSING_SERVICES`、忽略列表或运行时服务别名。
- `dev-env.sh` 不得 stash、checkout、pull、fast-forward 或覆盖 frontend dirty 文件；对 frontend 的身份验证只比较实际 `/app` bind mount 与调用方解析的 root。
- 运行时行为按 TDD；纯文档只做 `git diff --check` 与引用/残留检查，不新增正文断言。脚本、配置和测试变更按 `docs/architecture/heavy-test-impact.toml` 选择 HEAVY，未知影响 fail closed。
- 不修改、移动或删除 `docs/hardware/`。Bash/zsh 不以 `path` 命名变量，使用 `url_path`、`file_path` 等名称。
- Commit、Push、PR、Merge 和 Deploy 均需分别授权；计划中的提交只是提交边界，不构成授权。

## File Map and Owners

| 范围 | 文件 | 唯一所有者与边界 |
| --- | --- | --- |
| HTTP readiness | 新增 `scripts/wait_for_http.py`、`tests/scripts/test_wait_for_http.py` | 版本化、无凭据 HTTP 重试 helper；FAST 注入测试和 cutover simulation 共同拥有。 |
| 管理员登录 | 新增 `scripts/check_bootstrap_admin_login.py`、`tests/scripts/test_check_bootstrap_admin_login.py` | 对公开 Auth HTTP 合同做一次登录/登出验证；不复制 auth domain。 |
| 本机 frontend 身份 | `scripts/dev-env.sh`、`tests/deployment/test_local_development_environment.py`、`docs/devops/local-development-environment.md` | 只确认运行 frontend `/app` mount 与解析后的 `WES_FRONTEND_ROOT` 相同。 |
| fail-closed 发布 | `Jenkinsfile.test-deploy`、`tests/deployment/test_test_deploy_cutover.py`、`docs/devops/prod-release-deploy.md`、`docs/devops/rocky-linux-server-initialization.md`、`docs/devops/jenkins-setup-current-env.md` | 复用两个新 helper，负责管理员门禁、pre/final exact topology 与入口恢复顺序。 |
| 发布记录与映射 | `docs/architecture/heavy-test-impact.toml`、`docs/architecture/file_index.md` | 精确 HEAVY 所有权和既有 Runbook 内的最小非秘密发布记录，不创建第二套 manifest。 |
| 外部计划 | frontend menu、onsite data recovery、runtime hardening 三个计划 | 菜单、备份恢复、宿主加固各自独占；本计划只在 Task 6 核对并消除重叠。 |

## Tasks

### Task 0 — 冻结执行基线与既有计划所有权

**行为目标：** 从最新 `develop` 的隔离 backend worktree 开始，冻结本计划实际变更面和外部计划边界。

**边界：** 不触碰主 checkout/frontend dirty 状态，不执行现场修复；若先决条件已经在当前 `develop` 解决，则移除对应切片而非重复实现。

- [ ] 1. 记录 backend/frontend status、worktree、`origin/develop`；从该精确 base 建独立 worktree，初始化依赖与 hooks，并确认原现场未变化。
- [ ] 2. 重新核实：已有 superadmin 不改密码、Jenkins 是否仍有本地 readiness loop、`dev-env.sh` 是否仍信任调用方 frontend、以及当前菜单同步/持久化调用点。
- [ ] 3. 冻结 File Map 每项的 stat/SHA、直接测试 owner 与 HEAVY mapping；不读无关 dirty diff。除非范围进入 `src/`，不做 GitNexus production-symbol impact。
- [ ] 4. 仅记录两项外部 remediation：已发现 dump 的 `0600` 收紧和现场 7-service Compose 与批准渲染拓扑的偏差；不在本地实施时改服务器。

**验收与验证：** 隔离 worktree 干净、变更面/owner 可追溯、两项外部风险标为待单独授权。

### Task 1 — 增加可复用 HTTP readiness 门禁

**行为目标：** 提供唯一、版本化、无凭据的 HTTP readiness helper；成功仅接受 `200..399`，耗尽尝试后失败。

**边界：** 只使用标准库；不读取凭据、不读响应 body、不触及数据库、worker、迁移或业务语义。

- [ ] 5. 先为重试至成功、持续非成功、`URLError` 耗尽、尝试/超时/间隔非法、非 HTTP、非绝对 URL、userinfo、query/fragment 和 CLI 脱敏写 FAST 契约测试。
- [ ] 6. 运行该聚焦测试，确认因 helper 缺失而 RED。
- [ ] 7. 最小实现 `wait_for_http` 与 CLI：限制为无 userinfo/query/fragment 的绝对 HTTP(S) URL，精确 probe 次数、可注入 opener/sleeper，成功输出无 body，失败输出有界且不泄露 URL 凭据。
- [ ] 8. 运行聚焦 pytest、Ruff check 和 format check 至 GREEN。
- [ ] 9. 为 helper 增加经审查的精确 HEAVY mapping（仅在 FAST/cutover owner 已闭合且确认无 HEAVY 影响时允许空集合）；取得授权后才暂存并提交该切片。

**验证命令：** `uv run pytest tests/scripts/test_wait_for_http.py -q`；`uv run ruff check scripts/wait_for_http.py tests/scripts/test_wait_for_http.py`；`uv run ruff format --check scripts/wait_for_http.py tests/scripts/test_wait_for_http.py`。

### Task 2 — 增加真实 bootstrap 管理员登录门禁

**行为目标：** 用部署环境用户名/密码对新 API 做一次真实登录，验证超级管理员身份后立即登出本次会话，只返回 username/user ID。

**边界：** 复用公开 Auth HTTP 合同，不导入密码哈希、Repository 或 `UserService`；bootstrap 不轮换密码，失败不自动恢复凭据。

- [ ] 10. 先写 fake-HTTP 契约：login 与 logout 路径/headers/body、HTTP 401、业务 code、缺 token、非 superuser、用户名不符、超 256 KiB、非法 JSON、logout 失败、缺失/过短环境密码，以及 stdout/stderr 脱敏。
- [ ] 11. 运行聚焦测试，确认因门禁模块缺失而 RED。
- [ ] 12. 最小实现基于绝对 `http://host[:port]` origin 的登录/登出门禁：请求 compact UTF-8 JSON，先限长再解析；同时要求 HTTP 200、`code == "1000"`、配置用户名、整数 ID、`is_superuser is True`、非空 access token 与 `revoked_count == 1`；CLI 仅输出 `ADMIN_LOGIN_GATE_OK` 的 username/user ID，失败不输出服务器 body 或秘密。
- [ ] 13. 运行聚焦 pytest、Ruff check 和 format check 至 GREEN。
- [ ] 14. 增加精确 HEAVY mapping 并运行 selector contract；取得授权后才暂存并提交该切片。

**验证命令：** `uv run pytest tests/scripts/test_check_bootstrap_admin_login.py -q`；`uv run ruff check scripts/check_bootstrap_admin_login.py tests/scripts/test_check_bootstrap_admin_login.py`；`uv run ruff format --check scripts/check_bootstrap_admin_login.py tests/scripts/test_check_bootstrap_admin_login.py`。

### Task 3 — 将 `dev-env.sh check` 绑定至运行中 frontend 源码

**行为目标：** `up/check` 在 HTTP、seed 与数据检查前拒绝 frontend `/app` bind mount 与解析后 `WES_FRONTEND_ROOT` 不一致的情况；版本输出包含绝对 root、commit 和 branch/detached。

**边界：** macOS `/host_mnt/...` 只做路径规范化，不增加第二份配置；不得改变 frontend service 的必要挂载或自动处理 Git 状态。

- [ ] 15. 先扩展 Docker fake，覆盖 Linux/Docker Desktop 同路径、mismatch 及其 stderr 的 normalized expected/actual；断言 mismatch 后没有 HTTP 或 `seed_initial_data.py --check`。
- [ ] 16. 为 detached frontend worktree 写版本输出测试，同时保留 backend branch/commit 输出。
- [ ] 17. 运行本地环境聚焦测试并确认 RED。
- [ ] 18. 最小加入 mount path normalization、running `/app` mount 获取、identity comparison 与 fail-fast 调用顺序；将 expected root 规范化，输出 detached 与 `root=`。
- [ ] 19. 运行聚焦测试；对当前实际挂载的 frontend checkout 执行一次真实 `check`，再以不同 checkout 验证在 seed 前失败。
- [ ] 20. 更新既有本机开发指南：`check` 校验实际 bind mount，变更 `WES_FRONTEND_ROOT` 要先用 `up` 重建 frontend；不固化任何 worktree 绝对路径或第二个 Compose 入口。
- [ ] 21. 取得授权后才暂存并提交脚本、测试和本机指南切片。

**验证命令：** `uv run pytest tests/deployment/test_local_development_environment.py -q`；`WES_FRONTEND_ROOT=<当前待验收 frontend checkout> ./scripts/dev-env.sh check`。

### Task 4 — 将登录、拓扑与 readiness 接入 fail-closed cutover

**行为目标：** `MAINTENANCE_MODE=false` 前依次完成应用启动、管理员登录门禁、pre-entrypoint 精确拓扑、外部入口、HTTP health/frontend readiness、final 精确拓扑。

**边界：** 保留既有 `fail_cutover` 和 EXIT trap；Jenkins、生产 Runbook 与 Rocky 初始化手册只调用共享 helper，不复制 retry loop；凭据仅以环境变量名注入一次性 exec。

- [ ] 22. 先写阶段顺序与凭据脱敏测试，锁定 application start、admin login、pre topology、external entrypoint、final topology 均先于解除维护态。
- [ ] 23. 扩展 fake cutover，分别覆盖 login 失败、pre topology 缺服务、Nginx health 失败、HTTP helper 耗尽和 final topology 出现未知服务；每例必须停止 Nginx、保持维护态且不运行后续阶段。
- [ ] 24. 运行 deployment 聚焦测试并确认 RED。
- [ ] 25. 在应用 readiness 后、Nginx 恢复前执行管理员 login helper；禁止再次 bootstrap 或添加 reset fallback。
- [ ] 26. 在入口前和最终阶段分别对排序后的 `compose config --services` 与 running services 做 exact comparison；失败只输出有界服务差异，不输出容器环境/config。
- [ ] 27. 以 `compose up --wait` 恢复 Nginx，再以共享 `wait_for_http.py` 验证 `/health` 与 `/`；删除 Jenkins-local wait function。
- [ ] 28. 更新两份 Runbook 复用同一 login/topology/readiness 门禁，并在 Jenkins 指南说明既有 bootstrap credential 只注入 bootstrap 与一次 login-gate exec，绝不进入 Compose config/log。
- [ ] 29. 运行两个 helper 与 cutover 聚焦测试并执行 `git diff --check`，确认成功/失败 simulation 与秘密脱敏。
- [ ] 30. 取得授权后才分别提交 executable（Jenkins/test）与文档（Runbook/Jenkins guide）边界。

**验证命令：** `uv run pytest tests/scripts/test_wait_for_http.py tests/scripts/test_check_bootstrap_admin_login.py tests/deployment/test_test_deploy_cutover.py -q`；`git diff --check`。

### Task 5 — 在既有 Runbook 记录发布证据，不创建第二个 manifest

**行为目标：** 定义最小非秘密记录，绑定前后端 revision/digest/source tree、前端绑定后端 revision、OpenAPI/permission SHA、Alembic head、Compose rendered/running 服务、备份证据、授权/admin/HTTP 门禁和验证边界。

**边界：** 记录保存在受控主机目录和项目外运维归档，不在 Git 内按日期复制；不得保存密码、token、Cookie、`.env.prod` 或业务 payload。

- [ ] 31. 在现有生产 Runbook 加入上述 release identity、拓扑、备份、门禁、操作人/时间字段，保留字段为最小类别而非新发布框架。
- [ ] 32. 规定受保护的 `/srv/wes/releases/${RELEASE_ID}/` 生命周期（目录 `0700`、文件 `0600`）和项目外归档；schema head 仅使用限定的 `wes_sys.alembic_version` 查询。
- [ ] 33. 将 engineering gates、deployment technical gates、supplier conformance、真实 ECS/WMS callback loop、physical completion、business acceptance 分开记录；本计划只能标记前两层，其他均为 `NOT VERIFIED`。
- [ ] 34. 更新文件索引，将两个脚本登记为唯一 helper source，并扫描 helper 引用及任何 credential/token 示例残留；不为 prose 新增测试。
- [ ] 35. 取得授权后才暂存并提交 Runbook/索引切片。

**验证命令：** `git diff --check`；`rg -n "wait_for_http.py|check_bootstrap_admin_login.py" docs/devops docs/architecture/file_index.md`；`rg -n "BOOTSTRAP_ADMIN_PASSWORD=|access_token|refresh_token" docs/devops/prod-release-deploy.md`。

### Task 6 — 跨计划验证与无部署交接

**行为目标：** 对同一最终 staged executable snapshot 完成所有者闭合、局部/QUALITY/selected HEAVY/Review 证据，并在不部署的情况下交接。

**边界：** 不把本机、Jenkins、健康检查或部署技术门禁夸大为 supplier、callback、physical 或 business acceptance；菜单、备份和 runtime 计划各自保留唯一所有权。

- [ ] 36. 扫描 Jenkins-local wait loop、旧 Nginx recovery 命令、login gate、frontend mount identity 及其直接测试 owner，关闭重复实现和残留引用。
- [ ] 37. 运行两个 scripts、cutover 和本地环境测试的聚焦 aggregate。
- [ ] 38. 将当前待验收 frontend checkout 显式作为生命周期输入，运行 `dev-env.sh up/check`；用 container `/app` mount 的 normalized absolute root、Git commit 与 branch/detached 输出证明 identity。不得硬编码已退休或尚未创建的 checkout 路径；菜单计划执行完成后，以其实际 checkout 重新执行本项生命周期验证。
- [ ] 39. 在最终 executable staged snapshot 上运行一次 QUALITY、staged HEAVY selector 和 selector 输出的 manifest；仅 selector 产生的 `NONE` 有效，不为安心运行全量 HEAVY。
- [ ] 40. 固定 base/head/staged paths，完成一次完整只读 Review；验证后只修复已确认问题，刷新失效证据，再做一轮包含旧意见闭环的 fresh full review 至 `NO_FINDINGS`。
- [ ] 41. 执行前重新核对 frontend menu 计划：它在 `scripts/dev-env.sh` 只删除 menu seed dependency，必须保留 Task 3 mount identity；其 Jenkins、两份 Runbook 改动保留 admin login/logout gate、versioned `wait_for_http`、pre/final exact topology 与 fail-closed 阶段，且不复制 helper。
- [ ] 42. 执行前重新核对 onsite 计划：data recovery 仅拥有 backup/restore、`0600`、SHA-256、off-host copy 和 restore drill；runtime hardening 仅拥有 Beat、Redis/THP、Nginx nofile/logrotate、PostgreSQL timeout。两者不得复制或改写 shared wait helper、admin login gate、exact topology 或 cutover 阶段。
- [ ] 43. 交接 Commit SHA、变更发布阶段、聚焦/QUALITY/HEAVY/Review 结果与未验证外部层；未获单独部署授权前状态为 `IMPLEMENTED — NOT DEPLOYED`。

**验证命令：** `uv run pytest tests/scripts/test_wait_for_http.py tests/scripts/test_check_bootstrap_admin_login.py tests/deployment/test_test_deploy_cutover.py tests/deployment/test_local_development_environment.py -q`；`./scripts/git-quality-gate.sh --profile quality`；`uv run scripts/select_heavy_tests.py --scope staged`；`./scripts/run_selected_heavy_local.sh --scope staged`。

## Completion Criteria

- 错误管理员凭据在外部入口恢复前失败；正确凭据的验证会话被撤销，且日志无秘密。
- Nginx 必须先通过 Compose health 再通过唯一 versioned HTTP helper；失败保持入口关闭。
- rendered/running Compose 服务集合在 pre/final 均完全一致；`dev-env.sh check` 不能用 A checkout 验证 B 的运行容器。
- Runbook 仅引用共享 helper，并保存非秘密发布证据；菜单、备份恢复、宿主加固没有平行实现或所有者重叠。
- 最终 staged fingerprint 上的聚焦测试、QUALITY、selector HEAVY 与 fresh Review 均通过；这些仅证明工程/部署技术层，不代表现场业务验收。
