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
| `Jenkinsfile` | Jenkins Pipeline 配置（在 Node 上执行） |
| `docs/jenkins-setup-current-env.md` | 详细配置指南 |
| `docs/jenkins-checklist.md` | 快速配置清单 |

## 🚀 快速开始

### 1. 查看 Jenkins Node 标签

```bash
# 访问 Jenkins
http://192.168.0.220:9081

# 进入 Manage Jenkins → Manage Nodes and Clouds
# 记录 192.168.0.221 节点的 Labels
```

### 2. 修改 Jenkinsfile

```bash
# 编辑 Jenkinsfile
vim Jenkinsfile

# 修改第 13 行的 label 为实际的 Node 标签
agent {
    label 'your-actual-label'  // 改为实际标签
}
```

### 3. 提交到 GitLab

```bash
git add Jenkinsfile
git commit -m "chore(ci): 配置 Jenkins Pipeline"
git push gitlab develop
```

### 4. 配置 Jenkins Pipeline

参考 [快速配置清单](docs/jenkins-checklist.md) 完成配置。

## 🎯 Pipeline 流程

```
代码推送 → GitLab Webhook → Jenkins
    ↓
在 Node (192.168.0.221) 上执行
    ├─ Checkout Source
    ├─ Build CI Image
    ├─ Quality Checks（并行）
    ├─ Tests（并行）
    ├─ Build Runtime Image（develop/main）
    ├─ Publish Runtime Image（develop/main）
    └─ Deploy Runtime（develop/main）
        ├─ 仅滚动后端应用服务
        ├─ 不升级 db/redis/nginx
        ├─ 数据库迁移
        ├─ API 健康检查
        └─ 失败时回滚到上一个镜像
```

## 📖 详细文档

- **配置指南**：[jenkins-setup-current-env.md](docs/jenkins-setup-current-env.md)
- **配置清单**：[jenkins-checklist.md](docs/jenkins-checklist.md)

## ⚠️ 注意事项

1. **Node 标签**：确保 Jenkinsfile 中的 `label` 与实际的 Node 标签一致
2. **部署目录**：确保 `/opt/wes_backend` 已初始化
3. **环境文件**：确保 `.env.test` 已配置
4. **Docker 权限**：确保 Jenkins 用户有 Docker 权限

## 🔧 常用命令

```bash
# 查看构建日志
Jenkins → wes-backend → 构建号 → Console Output

# 查看测试报告
Jenkins → wes-backend → Test Result

# 查看覆盖率报告
Jenkins → wes-backend → Coverage Report

# 测试健康检查
curl http://192.168.0.221:8001/api/v1/performance/health
```

## 📞 需要帮助？

查看详细文档或联系技术支持。
