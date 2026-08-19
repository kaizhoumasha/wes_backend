# Jenkins CI/CD 配置

## 📋 环境信息

- **GitLab**：192.168.0.220:9080
- **Jenkins**：192.168.0.220（Docker）
- **Jenkins Node**：192.168.0.221（构建和部署）
- **GitHub 开发真源**：https://github.com/kaizhoumasha/wes_backend.git
- **GitLab 仓库**：http://192.168.0.220:9080/wes/wes_backend.git
- **LDAP 账号**：zhoukai / Ctt123456

## 📁 配置文件

| 文件 | 说明 |
|------|------|
| `Jenkinsfile.backend-ci` | 后端 CI 与镜像发布 |
| `Jenkinsfile.test-deploy` | TEST 环境独立部署入口 |
| `prod-release-deploy.md` | 生产环境手动发布 Runbook |
| `jenkins-setup-current-env.md` | 详细配置指南 |
| `jenkins-checklist.md` | 快速配置清单 |

## 仓库与发布权威

- GitHub `origin/develop` 是唯一代码评审与合入真源；功能、修复、CI 和文档变更都先通过 GitHub PR 合入。
- GitLab `gitlab/develop` 是 Jenkins 发布镜像，只允许接收 GitHub `origin/develop` 的精确 Commit，不接受 GitLab-only 修复。
- GitLab 推送前必须确认其当前 HEAD 是目标 GitHub Commit 的祖先；不满足时停止并治理分叉，禁止 force push、聚合 cherry-pick 或
  用时间顺序猜测真源。
- GitHub Merge 与 GitLab Push 是两次独立授权。只有 GitLab `PUSH` 触发镜像发布；GitHub PR、GitLab MR 和 Jenkins 手工构建均不发布。
- GitLab `develop` PUSH 必须从 webhook 的 `gitlabBefore` fast-forward 到 `gitlabAfter`；Jenkins 以前一 SHA 为差异基线执行 Mock
  合同与 selector 选中的 HEAVY，字段缺失、HEAD 不匹配或 ancestry 不成立时均 fail closed。
- `wes_backend-ci` 必须保持普通 Pipeline Job 并由 GitLab webhook 触发；GitLab Plugin 不向 Multibranch Pipeline 提供
  `gitlabBefore` / `gitlabAfter`，Poll SCM 和手工构建也不能替代发布触发。
- GitLab webhook 必须使用 Jenkins Job `Advanced → Secret Token → Generate` 生成的 per-project token；GitLab 与 Jenkins 两端取值
  必须一致，token 不得写入仓库、文档或日志，未认证的 `/project/wes_backend-ci` 请求不得触发发布 Job。
- RC 与现场选版只记录 immutable tag、manifest digest 和 OCI revision。`develop` channel 只用于定位最新候选，不能作为验收证据。

## 🚀 快速开始

### 1. 查看 Jenkins Node 标签

```bash
# 访问 Jenkins
http://192.168.0.220:9081

# 进入 Manage Jenkins → Manage Nodes and Clouds
# 记录 192.168.0.221 节点的 Labels
```

### 2. 核对现役 Jenkins Job

当前保留的 Jenkins Job：

- `wes_backend-ci`：后端 CI、镜像发布
- `wes_frontend-ci`：前端 CI、镜像推送
- `wes_test_deploy`：拉取 immutable 镜像并部署 TEST

已退役并删除：

- `wes_backend`：旧单体 Pipeline，已从 Jenkins 清理，不再维护

部署边界：

- TEST：由部署人员单独运行 `wes_test_deploy`，并明确选择 immutable 前后端镜像
- PROD：不依赖 Jenkins 直连生产环境，按手动部署 runbook 执行迁移、权限同步、菜单同步和首个管理员 bootstrap

### 3. 修改现役 Pipeline 脚本

```bash
# 编辑后端 CI Pipeline
vim Jenkinsfile.backend-ci

# 修改 agent label 为实际的 Node 标签
agent {
    label 'your-actual-label'  // 改为实际标签
}
```

### 4. 从 GitHub 真源发布到 GitLab

```bash
# 先在功能分支提交并通过 GitHub PR 合入 origin/develop，然后刷新两个远端。
git fetch origin
git fetch gitlab
release_sha="$(git rev-parse origin/develop)"

# 只允许 fast-forward 发布；失败时停止，不得强推。
git merge-base --is-ancestor gitlab/develop "$release_sha"
git push gitlab "$release_sha:refs/heads/develop"
```

推送前应再次核对 `release_sha` 是已经批准的 GitHub merge SHA；不得直接从本地功能分支或仅存在于 GitLab 的提交发布。

### 5. 配置 Jenkins Pipeline

参考 [快速配置清单](jenkins-checklist.md) 完成配置。

## 🎯 Pipeline 流程

```text
代码推送 → GitLab Webhook → `wes_backend-ci`
    ↓
在 Node (192.168.0.221) 上执行
    ├─ Checkout Source
    ├─ Build CI Image
    ├─ Quality Gate（唯一 QUALITY profile）
    ├─ Compose Contracts（主机端渲染生产与 TEST 部署配置）
    ├─ RuntimeInbox PostgreSQL Acceptance
    ├─ Mock Image Contracts（MR 与已验证的 develop PUSH）
    ├─ HEAVY Required（MR 按目标分支、develop PUSH 按 gitlabBefore 差异选择）
    ├─ Build Runtime Image
    └─ Push Runtime Image（仅门禁通过的 GitLab develop PUSH，推送 immutable + channel tag）
```

`wes_backend-ci` 只构建和发布后端镜像，不自动选择前端版本或触发 `wes_test_deploy`。需要 TEST/现场部署时，由部署人员单独运行
部署任务并明确选择前后端镜像；MR、其他分支 PUSH 和 Jenkins 手工构建只验证，不发布镜像。

生产环境建议顺序：

```bash
./scripts/migrate.sh upgrade
bash scripts/data/sync_permissions.sh
bash scripts/data/sync_menus.sh --frontend-path /opt/wes_frontend

export BOOTSTRAP_ADMIN_USERNAME=admin
export BOOTSTRAP_ADMIN_PASSWORD='StrongPassw0rd!'
export BOOTSTRAP_ADMIN_FULL_NAME='系统管理员'
export BOOTSTRAP_ADMIN_EMAIL='admin@example.com'
bash scripts/data/bootstrap_admin.sh
```

其中：

- `scripts/data/seed_initial_data.py` 仅用于 dev/test/demo，不用于生产
- `.env.prod` 与 `.env.frontend.prod` 分离维护即可，不要求合并
- 生产建议开启 `USE_SNOWFLAKE_ID=true`
- `bootstrap_admin.sh` 依赖上述 `BOOTSTRAP_ADMIN_*` 环境变量，建议由部署环境注入真实值

## 📖 详细文档

- **配置指南**：[jenkins-setup-current-env.md](jenkins-setup-current-env.md)
- **配置清单**：[jenkins-checklist.md](jenkins-checklist.md)
- **生产发布 Runbook**：[prod-release-deploy.md](prod-release-deploy.md)

## ⚠️ 注意事项

1. **Node 标签**：确保 `Jenkinsfile.backend-ci` 和 `Jenkinsfile.test-deploy` 中的 `label` 与实际的 Node 标签一致
2. **部署目录**：确保 `/opt/wes_backend` 已初始化；`wes_test_deploy` 会在部署前强制对齐到目标 commit
3. **前端源码目录**：确保部署机存在 `/opt/wes_frontend`，供 `wes_test_deploy` 在部署后自动同步菜单
4. **环境文件**：确保 `.env.test` 已配置；CI 测试会显式覆盖为非调试日志级别
5. **Docker 权限**：确保 Jenkins 用户有 Docker 权限

## 🔧 常用命令

```bash
# 查看后端 CI 日志
Jenkins → wes_backend-ci → 构建号 → Console Output

# 查看测试报告
Jenkins → wes_backend-ci → Test Result

# 查看归档产物（Bandit、FAST JUnit、HEAVY manifest/JUnit）
Jenkins → wes_backend-ci → Artifacts

# 查看 TEST 部署日志
Jenkins → wes_test_deploy → 构建号 → Console Output

# 测试健康检查
curl http://192.168.0.221:8001/health
```

## 📞 需要帮助？

查看详细文档或联系技术支持。
