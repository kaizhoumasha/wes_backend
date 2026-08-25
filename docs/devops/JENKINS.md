# Jenkins CI/CD 配置

## 环境信息

- **GitLab**：192.168.0.220:9080
- **Jenkins**：192.168.0.220（Docker）
- **Jenkins Node**：192.168.0.221（构建和部署）
- **GitHub 开发真源**：https://github.com/kaizhoumasha/wes_backend.git
- **GitLab 仓库**：http://192.168.0.220:9080/wes/wes_backend.git

## 配置文件与现役 Job

| Pipeline 文件 | Job 职责 |
| --- | --- |
| `Jenkinsfile.backend-ci` | 后端质量门禁、provider 制品导出、runtime 镜像构建与发布 |
| 前端仓库 `Jenkinsfile` | 前端质量门禁、consumer 制品导出、frontend 镜像构建与发布 |
| `Jenkinsfile.release-checker-ci` | release checker 独立测试、构建与不可变镜像发布 |
| `Jenkinsfile.test-deploy` | TEST 独立 release orchestrator；唯一环境变更入口 |

生产环境不由 Jenkins 直接连接；使用与 `Jenkinsfile.test-deploy` 相同合同的受控 orchestrator，详见 [生产发布 Runbook](prod-release-deploy.md)。

## 仓库与发布权威

- GitHub `origin/develop` 是唯一代码评审与合入真源。
- GitLab `gitlab/develop` 只接收 GitHub `origin/develop` 的精确 Commit；推送前必须证明 fast-forward，禁止 GitLab-only 修复或 force push。
- GitHub Merge 与 GitLab Push 是两次独立授权。只有已验证的 GitLab `develop` PUSH 可以发布 immutable producer 镜像。
- `wes_backend-ci` 必须是由 GitLab webhook 触发的普通 Pipeline Job，以获得 `gitlabBefore` / `gitlabAfter`；Poll SCM 和手工构建只验证、不发布。
- RC 与现场选版记录 immutable digest 和 OCI revision。Commit、tree 和 digest 用于身份审计，不要求前后端 Commit 相等。

## 独立 producer 边界

三个 producer 互不调用，也不触发 `wes_test_deploy`：

- backend producer 导出 `/opt/wes/release/provider-openapi.json` 和 `/opt/wes/release/provided-permissions.json`，校验对应 `org.wes.release.*` label 后发布 backend digest；
- frontend producer 从冻结快照导出 consumer OpenAPI、required operations 和 required permissions，校验自身 label 后发布 frontend digest；
- checker producer 从 `tools/release_checker/` 构建固定 `oasdiff` 版本的独立 checker 镜像，不导入后端应用或前端代码；
- producer 成功状态只能是 `BUILD_VERIFIED` 或 `PUBLISHED`，不能写成 `DEPLOYED`。

前后端镜像仍保留自身 OCI revision/source identity，但不得记录或要求“目标对端 Commit”。任何 producer 发布都不能因对端仓库、对端镜像或部署作业不可用而失败。

## Release orchestrator

`wes_test_deploy` 必须禁用并发构建，并接受以下接口：

- `DEPLOY_SCOPE=FRONTEND|BACKEND|BOTH`；
- `FRONTEND_CANDIDATE_DIGEST`：只在 `FRONTEND`、`BOTH` 提供；
- `BACKEND_CANDIDATE_DIGEST`：只在 `BACKEND`、`BOTH` 提供；
- `DEPLOY_SOURCE_COMMIT_SHA`；
- 可选 `FORCE_FULL`；
- 可选 `WARN_APPROVAL_REASON`。

Checker digest 由 deploy-source 固定，不是 Jenkins 参数。单侧发布的未选 peer 从 live container 和最近成功报告自动发现；不接受人工 peer 输入。

Orchestrator 在维护前完成 candidate digest 固定、当前 peer 交叉验证、镜像制品/label 校验、配置 hash、DB head、checker 和 WMS Provider profile 配置校验。兼容检查比较 required operations/permissions 与 backend provider 能力，不比较前后端 Commit；该配置校验不探测真实 WMS/ECS。

模式与终态：

- 内容、部署输入或运行配置变化，证据缺失，或首次基线时自动 FULL；只有普通代码变化且所有相关指纹稳定时才允许 FAST；
- `FORCE_FULL` 只能升级模式，不存在 force-FAST；
- `WARN` 必须由获授权操作员提供理由，并绑定本次 frontend/backend/checker digest 与 diff hash；
- 维护前失败为 `PRE_CUTOVER_ABORTED`，不改变环境；
- 进入维护后失败为 `CUTOVER_FAILED_MAINTENANCE_HELD`，Nginx 保持关闭；
- 成功归档 `/srv/wes/releases/${RELEASE_ID}/compatibility-report.json` 和 Jenkins artifact。

FAST 只切选择的一侧；backend FAST 仍须同时重建 API、Celery、WMS fulfillment、Beat 和 Flower。FULL 保留数据库备份、仅向前 migration、权限零漂移、管理员真实登录、精确 topology 和共享 HTTP readiness。

## 后端 Pipeline 流程

```text
GitLab webhook → wes_backend-ci
    ├─ Checkout Source
    ├─ Build CI Image
    ├─ Classify Required HEAVY
    ├─ Quality Gate
    ├─ Compose Contracts
    ├─ RuntimeInbox PostgreSQL Acceptance
    ├─ Mock Image Contracts
    ├─ HEAVY Required
    ├─ Export Provider Release Artifacts
    ├─ Build Runtime Image
    ├─ Verify Embedded Artifacts And OCI Labels
    └─ Push Runtime Image（仅已验证的 develop PUSH）
```

HEAVY selector 只负责测试选择，不能作为 FAST/FULL 发布模式真源。

## 首次空站点与日常发布

`bootstrap_foundation.sh` 只用于确认空数据库的首次站点初始化，并要求部署环境注入 `BOOTSTRAP_ADMIN_*`。日常 FULL 必须对当前数据库先备份再向前迁移，不得为每次发布创建 fresh DB，也不得使用 `seed_initial_data.py`。

权限 mutation 成功或一次精确 post-commit cache repair 后，都必须重新执行独立 `sync_permissions.py --check`。菜单不属于发布编排：不从 frontend 镜像提取 menu manifest，不运行菜单同步，不以菜单表数量作为部署门禁。

## 快速配置

1. 核对 Jenkins Node 标签和 Docker 权限。
2. 为三个 backend-owned Pipeline 分别建立 Job，并把 Script Path 指向上表对应文件。
3. 为 producer 配置 GitLab webhook、per-project secret token 和 Registry 凭据；token 不得写入仓库或日志。
4. 为 `wes_test_deploy` 配置部署 SSH、Registry 和 bootstrap 管理员凭据；秘密只能通过 Jenkins credentials 注入。
5. 使用 [当前环境配置指南](jenkins-setup-current-env.md) 和 [快速配置清单](jenkins-checklist.md) 验证参数、阶段与发布边界。

## 注意事项

1. `Jenkinsfile.backend-ci`、`Jenkinsfile.release-checker-ci` 和 `Jenkinsfile.test-deploy` 的 agent label 必须匹配实际 Node。
2. `/opt/wes_backend` 只对齐到 `DEPLOY_SOURCE_COMMIT_SHA`，用于固定 orchestrator、Compose 和 checker；它不代表候选 backend revision。
3. 部署机无需 `/opt/wes_frontend` 源码目录，也不执行菜单同步。
4. `.env.test`、`.env.prod` 与 frontend 环境文件继续分离维护；只归档 hash，不记录秘密。
5. producer PUBLISHED、Cloudflare preview、容器 healthy 或 HTTP readiness 均不能替代真实联调、设备或业务验收。

## 相关文档

- [当前环境配置指南](jenkins-setup-current-env.md)
- [快速配置清单](jenkins-checklist.md)
- [生产发布 Runbook](prod-release-deploy.md)
- [发布解耦当前设计](../superpowers/specs/2026-08-25-frontend-backend-release-decoupling-design.md)
