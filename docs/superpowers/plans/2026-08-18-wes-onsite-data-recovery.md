# WES 现场数据备份与恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为单虚拟机、纯局域网部署的 WES 建立可验证的 PostgreSQL 小时级备份、异机副本和同版本恢复演练，使服务器或虚拟机损坏后仍有独立恢复源。

**Architecture:** PostgreSQL 是 WES 业务数据的唯一权威恢复源；Redis 只承载缓存、Celery broker 和 result backend，灾难恢复默认从空 Redis 启动。第一阶段使用 custom-format `pg_dump`、globals/config 归档、SHA-256、SSH/rsync 异机复制和独立恢复演练；若正式 RPO 小于 1 小时，再单独评审 WAL/PITR，不在本计划预埋未启用框架。

**Tech Stack:** PostgreSQL 17、TimescaleDB、Docker Compose、Rocky Linux 10、systemd、rsync、Bash、Python 3.13、pytest。

**Spec:** `docs/architecture/SRS.md`

**Operational inputs:** `docs/devops/rocky-linux-server-inspection.md`、`docs/devops/rocky-linux-server-initialization.md`

**Historical decision input:** `../archive_docs/wes_backend/local-development/2026-08-18-fresh-db-migration-history/2026-08-17-onsite-data-resilience-and-runtime-stability.original.md`。该归档只提供已批准的 `RPO <= 1 小时`、`RTO <= 2 小时` 与现场约束来源，不是当前执行真源。

## Global Constraints

- 本计划在 `develop@c579b18a` 上重基线；实施时仍须从最新 `develop` 创建独立 worktree，并重新冻结 HEAD、运行镜像 digest、Alembic head、现场目录和无关 dirty 指纹。
- 默认目标为 `RPO <= 1 小时`、`RTO <= 2 小时`。目标变化属于新需求，不通过缩短本计划 timer 间隔静默扩面。
- 备份必须离开生产虚拟机。未取得支持 SSH/rsync 的异机目录、专用密钥、容量/保留策略和外部成功监控前，只允许开发与本机验证，不得宣称灾难恢复闭环。
- 仓库只保存变量名和示例，不保存备份主机地址、私钥、口令、真实 `.env.prod`、dump 或现场日志。
- 本计划只支持当前发布版本的备份与同版本恢复，不导入旧 schema，不提供兼容迁移、shim、双路径或旧 Redis 队列重放。
- PostgreSQL dump、globals、配置、manifest 和异机副本全部成功后才能发布成功标记；任一阶段失败必须非零退出并保留可诊断证据。
- 恢复测试只使用名称明确的隔离 PostgreSQL 数据库或独立实例，不连接共享开发库、TEST 或现场生产库。
- 恢复后的 WMS/ECS 对账、人工清线和业务放行属于更高层验收；技术恢复成功不能替代供应商联调或业务验收。
- 数据库角色拆分、data checksums、Alembic 单基线重置、Celery Beat、Redis、Nginx 和 Celery runtime 不属于本计划。

## File Structure

### Create

- `scripts/onsite/backup_postgresql.sh`：串行生成 dump、globals、配置归档、manifest、SHA-256、异机副本和成功标记。
- `deploy/onsite/wes-backup.env.example`：定义本机目录、保留周期、异机目标和超时的变量名，不包含真实值。
- `deploy/onsite/systemd/wes-postgresql-backup.service`：以 `oneshot` 调用唯一备份脚本。
- `deploy/onsite/systemd/wes-postgresql-backup.timer`：按小时调度并使用 `Persistent=true`。
- `tests/scripts/test_onsite_backup.py`：使用 fake `docker`、`rsync` 和临时目录验证脚本合同。
- `tests/deployment/test_onsite_backup_assets.py`：验证 systemd、示例环境和现场路径合同。
- `tests/integration/test_fresh_database_backup_restore.py`：在独立 PostgreSQL 中验证 custom dump 可列出、可恢复且 schema 可用。
- `docs/devops/wes-backup-and-recovery.md`：备份、恢复、保留、监控、演练与事故边界唯一运维真源。

### Modify

- `docs/architecture/heavy-test-impact.toml`：为运行脚本、systemd 资产和真实恢复测试建立精确映射。
- `docs/devops/rocky-linux-server-initialization.md`：把“验证备份命令”替换为新恢复真源的入口和首次备份门禁。
- `docs/architecture/file_index.md`：登记备份脚本、部署资产和恢复真源。
- `docs/superpowers/README.md`：维护本计划状态；完成并失去执行职责后外部归档。

---

### Task 1: Freeze the Recovery Contract and External Inputs

**Files:**

- Inspect: `docker-compose.yml`
- Inspect: `docker-compose.deploy.yml`
- Inspect: `.env.prod`
- Inspect: `docs/devops/rocky-linux-server-inspection.md`
- Inspect: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: 最新 `develop`、现场版本/digest、PostgreSQL/TimescaleDB版本、备份端资产和 RPO/RTO 决策。
- Produces: 已批准的目录、变量、保留策略、异机复制和验收边界清单。

- [ ] **Step 1:** 从最新 `develop` 创建 `codex/onsite-data-recovery` worktree，运行 `./scripts/init-env.sh dev`、`uv sync --dev` 和 `./scripts/install-git-hooks.sh`，确认 tracked 状态干净。
- [ ] **Step 2:** 记录 HEAD、当前 `VERSION`、Alembic heads、Compose PostgreSQL服务名、数据目录、现场镜像 digest 和部署目录；不得复用历史 `0.12.0.5` 路径或镜像。
- [ ] **Step 3:** 由项目负责人提供并批准异机目标、专用 SSH key path、目标主机指纹验证方式、本机/异机保留期、磁盘加密或不可变保留能力、容量和外部最近成功监控。
- [ ] **Step 4:** 枚举新增脚本/资产的直接测试 owner、真实恢复测试和 HEAVY mapping。异机输入缺失时把部署与灾难恢复验收标为 `EXTERNAL BLOCKED`，但允许继续实现本机合同。

### Task 2: Implement a Fail-closed Backup Artifact

**Files:**

- Create: `tests/scripts/test_onsite_backup.py`
- Create: `scripts/onsite/backup_postgresql.sh`
- Create: `deploy/onsite/wes-backup.env.example`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: Task 1 冻结的变量、容器服务名、目录和保留策略。
- Produces: 一个成功时才出现完整 manifest 与远端成功标记的原子备份目录。

- [ ] **Step 1:** 先写脚本合同测试，覆盖必填变量缺失、`flock` 冲突、dump/globals/config/rsync 任一步失败、`.partial` 清理、`0600` 权限、SHA-256、`pg_restore --list`、原子 rename、本机保留和异机成功标记顺序。
- [ ] **Step 2:** 运行 `uv run pytest tests/scripts/test_onsite_backup.py -q`，确认因脚本/示例资产不存在而 RED，不修改 pytest 配置掩盖失败。
- [ ] **Step 3:** 实现最小脚本：只使用明确环境变量；custom-format dump、globals、非秘密配置和 manifest 写入唯一 `.partial` 目录；本机校验成功后原子改名，再复制到异机临时目录并原子发布。
- [ ] **Step 4:** 运行 `bash -n scripts/onsite/backup_postgresql.sh` 与聚焦测试，确认失败不会生成远端成功标记或删除最近一个已验证备份。
- [ ] **Step 5:** 为脚本和示例配置增加精确 HEAVY mapping；未知影响保持 fail closed，不使用无依据的 `heavy_tests = []`。
- [ ] **Step 6:** 获得 Commit 授权后精确暂存上述文件并提交 `feat(ops): 建立 PostgreSQL 异机备份合同`。

### Task 3: Schedule Backups and Prove Restoreability

**Files:**

- Create: `deploy/onsite/systemd/wes-postgresql-backup.service`
- Create: `deploy/onsite/systemd/wes-postgresql-backup.timer`
- Create: `tests/deployment/test_onsite_backup_assets.py`
- Create: `tests/integration/test_fresh_database_backup_restore.py`
- Modify: `docs/architecture/heavy-test-impact.toml`

**Interfaces:**

- Consumes: Task 2 的脚本、示例环境和备份目录格式。
- Produces: 可安装的小时 timer 和可在隔离 PostgreSQL 重放的恢复证据。

- [ ] **Step 1:** 先写 deployment 合同测试，锁定 `oneshot`、专用环境文件、绝对工作目录、`OnCalendar=hourly`、`Persistent=true`、并发禁止和非零失败传播。当前现场使用 rootful Docker，因此 host system service 以 root 调用 Docker，但环境文件、脚本与备份目录必须 root-owned、最小权限并设置 `UMask=0077`；容器内数据库命令仍使用数据库服务账号，不把现场账号加入 `docker` group。若以后批准 rootless Docker，须另行冻结 daemon 与 unit 身份，不能静默切换。
- [ ] **Step 2:** 写真实恢复测试：生成当前空库数据、运行备份、校验 manifest/`pg_restore --list`，在新的独立数据库恢复 globals/database，验证 Alembic head、TimescaleDB扩展和代表性权威表。
- [ ] **Step 3:** 分别运行 deployment 与 integration 测试确认 RED；integration 所需 PostgreSQL 未就绪时停止并报告，不把 skip 当通过。
- [ ] **Step 4:** 添加最小 systemd unit/timer；服务只调用 Task 2 脚本，不复制备份逻辑。
- [ ] **Step 5:** 运行 `uv run pytest tests/deployment/test_onsite_backup_assets.py -q`，再在独占临时 PostgreSQL 上运行 `RUN_WORKLINE_INTEGRATION=1 uv run pytest tests/integration/test_fresh_database_backup_restore.py -q`，两者必须非 skip 通过。
- [ ] **Step 6:** 更新 mapping 并只运行 selector 选中的 HEAVY；获得授权后提交 `test(ops): 验证 PostgreSQL 定时备份与恢复`。

### Task 4: Publish the Recovery Runbook

**Files:**

- Create: `docs/devops/wes-backup-and-recovery.md`
- Modify: `docs/devops/rocky-linux-server-initialization.md`
- Modify: `docs/architecture/file_index.md`
- Modify: `docs/superpowers/README.md`

**Interfaces:**

- Consumes: Tasks 2–3 最终机器合同、现场目录和真实恢复证据。
- Produces: 初级现场工程师可执行的唯一备份与恢复真源。

- [ ] **Step 1:** 写清备份安装、首次手工触发、timer/最近成功/容量/远端副本检查、同版本恢复、空 Redis、失败停止、RPO/RTO记录和恢复后 WMS/ECS 对账边界。
- [ ] **Step 2:** 将初始化手册中“只验证命令、不是正式备份”的段落替换为首次成功备份门禁和新真源引用，不复制完整恢复流程。
- [ ] **Step 3:** 更新文件索引与文档生命周期状态；只归档被新真源完整替代的过程文档，保持 `docs/hardware/` 不变。
- [ ] **Step 4:** 运行 `git diff --check`、精确引用扫描和路径存在性检查；纯文档部分不新增 pytest。
- [ ] **Step 5:** 获得授权后提交 `docs(ops): 发布 WES 备份与恢复真源`。

### Task 5: Final Gates and Separately Authorized Rollout

**Files:**

- Verify: Tasks 2–4 全部文件
- Update only after authorized rollout: `docs/devops/upgrade-records/<date>-<host>-<version>.md`

**Interfaces:**

- Consumes: 最终 staged 快照、固定镜像 digest、异机目标和独立恢复环境。
- Produces: 可审计的合入证据；现场部署仍需独立授权。

- [ ] **Step 1:** 在最终代码/配置快照上运行聚焦测试、`./scripts/git-quality-gate.sh --profile quality`、staged selector 和 manifest 中的 HEAVY；同一绿色 fingerprint 不重复全量门禁。
- [ ] **Step 2:** 运行 `npx gitnexus detect-changes --scope staged --repo "$PWD"`、`git diff --cached --check` 和完整只读 Review，修复后只刷新被失效的证据。
- [ ] **Step 3:** 生成不含私钥、真实环境、dump 或日志的交付包与 SHA-256；在空目录解压并验证脚本、systemd 资产和文档完整。
- [ ] **Step 4:** 另行取得部署授权后，先在现场生成并异机校验回退备份，再安装 timer；不得在同一窗口顺带修改 Redis、Nginx、数据库角色或 schema。
- [ ] **Step 5:** 在独立实例完成真实恢复演练并记录 RPO/RTO、dump/hash、镜像 digest、Alembic head、TimescaleDB版本和未验证业务边界。

## Definition of Done

- 最近成功备份在本机和异机均存在，manifest、SHA-256 与 `pg_restore --list` 一致。
- 至少一次独立同版本恢复演练真实通过，实测 `RPO <= 1 小时`、`RTO <= 2 小时`。
- timer、脚本失败和外部监控均 fail closed，不把仅有本机 dump 描述为灾难恢复。
- 恢复 runbook 是唯一运维真源，并明确 Redis 空启动、WMS/ECS 对账和业务验收边界。
- 当前快照要求的 QUALITY、selector HEAVY、真实恢复和文档检查全部有效；`docs/hardware/` 未变化。
