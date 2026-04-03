# Jenkins CI/CD 配置

## 📋 环境信息

- **GitLab**：192.168.0.220:9080
- **Jenkins**：192.168.0.220（Docker）
- **Jenkins Node**：192.168.0.221（构建和部署）
- **GitLab 仓库**：http://192.168.0.220:9080/wes/wes_backend.git
- **LDAP 账号**：zhoukai / Ctt123456

## 📁 配置文件

| 文件 | 说明 |
|------|------|
| `Jenkinsfile.backend-ci` | 后端 CI 与镜像发布 |
| `Jenkinsfile.test-deploy` | TEST 环境自动部署 |
| `docs/devops/jenkins-setup-current-env.md` | 详细配置指南 |
| `docs/devops/jenkins-checklist.md` | 快速配置清单 |

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

- `wes_backend-ci`：后端 CI、镜像推送、触发 TEST 部署
- `wes_frontend-ci`：前端 CI、镜像推送
- `wes_test_deploy`：拉取 immutable 镜像并部署 TEST

已退役并删除：

- `wes_backend`：旧单体 Pipeline，已从 Jenkins 清理，不再维护

部署边界：

- TEST：由 Jenkins 自动部署，默认跟随 `develop` channel 镜像
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

### 4. 提交到 GitLab

```bash
git add Jenkinsfile.backend-ci Jenkinsfile.test-deploy
git commit -m "chore(ci): align active Jenkins jobs"
git push gitlab develop
```

### 5. 配置 Jenkins Pipeline

参考 [快速配置清单](docs/jenkins-checklist.md) 完成配置。

## 🎯 Pipeline 流程

```
代码推送 → GitLab Webhook → `wes_backend-ci`
    ↓
在 Node (192.168.0.221) 上执行
    ├─ Checkout Source
    ├─ Build CI Image
    ├─ Quality Checks（并行）
    ├─ Tests（并行）
    ├─ Build Runtime Image
    ├─ Push Runtime Image（非 MR，推送 immutable + channel tag）
    └─ Trigger Test Deploy（仅 develop push）
         ↓
      `wes_test_deploy`
        ├─ 拉取 backend `develop` / frontend `develop` 镜像
        ├─ 重建 TEST 应用服务
        ├─ 从 `/opt/wes_frontend/src/router/index.ts` 同步菜单到 `wes_sys.menus`
        └─ 健康检查
```

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

- **配置指南**：[jenkins-setup-current-env.md](docs/jenkins-setup-current-env.md)
- **配置清单**：[jenkins-checklist.md](docs/jenkins-checklist.md)

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

# 查看覆盖率报告
Jenkins → wes_backend-ci → Coverage Report

# 查看 TEST 部署日志
Jenkins → wes_test_deploy → 构建号 → Console Output

# 测试健康检查
curl http://192.168.0.221:8001/api/v1/admin/performance/health
```

## 📞 需要帮助？

查看详细文档或联系技术支持。
