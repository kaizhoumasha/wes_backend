# Jenkins 快速配置清单

## ✅ 配置步骤（按顺序执行）

### 1. 访问 Jenkins

- [ ] 浏览器访问：`http://192.168.0.220:9081`
- [ ] 使用 LDAP 登录：`zhoukai / Ctt123456`

### 2. 验证 Jenkins Node

- [ ] 进入：**Manage Jenkins → Manage Nodes and Clouds**
- [ ] 确认 192.168.0.221 节点状态：**在线**
- [ ] 记录节点标签（例如：`test-node`）

### 3. 配置 GitLab 凭据

#### 3.1 生成 GitLab Token

- [ ] 登录 GitLab：`http://192.168.0.220:9080`
- [ ] 用户头像 → **Preferences → Access Tokens**
- [ ] 创建 Token：
  - Name: `jenkins-ci`
  - Scopes: `api`, `read_repository`, `write_repository`
- [ ] 复制并保存 Token

#### 3.2 在 Jenkins 中添加凭据

- [ ] Jenkins → **Manage Jenkins → Manage Credentials**
- [ ] 点击 **Add Credentials**
- [ ] 配置：
  ```
  Kind: Username with password
  Username: zhoukai
  Password: <GitLab Token>
  ID: gitlab-credentials
  Description: GitLab 凭据
  ```

### 4. 创建 Pipeline 项目

- [ ] Jenkins 首页 → **New Item**
- [ ] 项目名称：`wes_backend-ci`
- [ ] 类型：**Pipeline**
- [ ] 点击 **OK**

### 5. 配置 Pipeline

#### General

- [ ] **Discard old builds**: Max # of builds to keep = `10`

#### Build Triggers

- [ ] **Build when a change is pushed to GitLab**
- [ ] 记录 Webhook URL：`http://192.168.0.220:9081/project/wes_backend-ci`

#### Pipeline

- [ ] **Definition**: Pipeline script from SCM
- [ ] **SCM**: Git
- [ ] **Repository URL**: `http://192.168.0.220:9080/wes/wes_backend.git`
- [ ] **Credentials**: `gitlab-credentials`
- [ ] **Branch**: `*/develop`
- [ ] **Script Path**: `Jenkinsfile.backend-ci`

### 6. 配置 GitLab Webhook

- [ ] 登录 GitLab：`http://192.168.0.220:9080`
- [ ] 进入项目：**wes / wes_backend**
- [ ] **Settings → Webhooks**
- [ ] 配置：
  ```
  URL: http://192.168.0.220:9081/project/wes_backend-ci
  Trigger: Push events, Merge request events
  SSL verification: 取消勾选（HTTP）
  ```
- [ ] 点击 **Add webhook**
- [ ] 点击 **Test → Push events** 测试

### 7. 配置部署服务器（192.168.0.221）

#### 7.1 初始化项目目录

```bash
# SSH 登录到 192.168.0.221
ssh root@192.168.0.221

# 创建项目目录
sudo mkdir -p /opt/wes_backend
cd /opt/wes_backend

# 克隆代码
git clone http://192.168.0.220:9080/wes/wes_backend.git .

# 配置 Git 凭据
git config credential.helper store
git pull  # 输入用户名和密码后会保存
```

- [ ] 项目目录已创建：`/opt/wes_backend`
- [ ] 代码已克隆
- [ ] Git 凭据已配置

#### 7.2 创建环境文件

```bash
cd /opt/wes_backend
cp .env.example .env.test
vim .env.test
```

配置内容：

```bash
DATABASE_URL=postgresql+psycopg://wes_user:wes_password@wes_postgres:5432/wes_db
REDIS_URL=redis://wes_redis:6379/0
ENVIRONMENT=test
DEBUG=false
SECRET_KEY=<生成随机密钥>
DATETIME_TIMEZONE=Asia/Shanghai
LOG_LEVEL=INFO
```

- [ ] `.env.test` 文件已创建
- [ ] 环境变量已配置

#### 7.3 验证 Docker

```bash
docker --version
docker-compose --version
docker ps
```

- [ ] Docker 已安装
- [ ] Docker Compose 已安装

### 8. 提交现役 Pipeline

```bash
# 在本地开发机器上
cd /Users/kaizhou/codeDev/wes_backend

# 如有需要，按实际 Node 标签调整 Pipeline 中的 agent label
vim Jenkinsfile.backend-ci
vim Jenkinsfile.test-deploy

# 提交到 GitLab
git add Jenkinsfile.backend-ci Jenkinsfile.test-deploy
git commit -m "chore(ci): 更新现役 Jenkins Pipeline 配置"
git push gitlab develop
```

- [ ] Jenkinsfile.backend-ci 已核对
- [ ] Jenkinsfile.test-deploy 已核对
- [ ] agent 标签已修改
- [ ] 已提交到 GitLab

### 9. 测试后端 CI Pipeline

- [ ] Jenkins → **wes_backend-ci** → **Build Now**
- [ ] 查看构建日志
- [ ] 验证各阶段：
  - [ ] Checkout Source
  - [ ] Build CI Image
  - [ ] Quality Gate（格式、Lint、Bandit、架构、脚本合同与 FAST）
  - [ ] Compose Contracts（主机端渲染生产与 TEST 部署配置）
  - [ ] RuntimeInbox PostgreSQL Acceptance
  - [ ] Mock Image Contracts（MR）
  - [ ] HEAVY Required（MR）
  - [ ] Build Runtime Image
  - [ ] Push Runtime Image（非 MR）
  - [ ] Trigger Test Deploy（仅 develop push）

### 10. 测试 TEST 部署 Pipeline

- [ ] Jenkins → **wes_test_deploy** → 查看是否被 `wes_backend-ci` 自动触发
- [ ] 查看部署日志
- [ ] 验证 TEST 环境健康检查
- [ ] 验证日志中出现“同步 TEST 环境菜单”且 `TEST 环境菜单数量` 大于 0

### 11. 验证部署

```bash
# 测试健康检查
curl http://192.168.0.221:8001/health

# 预期响应
{"status": "healthy"}

# 可选：确认 TEST 数据库菜单已同步
ssh root@192.168.0.221 \
  "docker exec wes_postgres_test psql -U wes_user -d wes_db_test -tAc 'select count(*) from wes_sys.menus'"
```

- [ ] 健康检查通过
- [ ] 应用正常运行
- [ ] TEST 环境菜单数量大于 0

## 🔧 关键配置点

### Jenkins Node 标签

在 `Jenkinsfile.backend-ci` 或 `Jenkinsfile.test-deploy` 中修改对应 Job 的节点标签：

```groovy
agent {
    label 'test-node'  // 改为实际的 Node 标签
}
```

查看实际标签：
1. Jenkins → **Manage Jenkins → Manage Nodes and Clouds**
2. 点击 192.168.0.221 节点
3. 查看 **Labels** 字段

### GitLab Connection（可选）

如果使用 GitLab 插件的高级功能：

1. Jenkins → **Manage Jenkins → Configure System**
2. 找到 **GitLab** 部分
3. 添加 GitLab 连接：
   ```
   Connection name: gitlab
   GitLab host URL: http://192.168.0.220:9080
   Credentials: gitlab-credentials
   ```

## 🐛 常见问题

### Q1: Jenkins Node 离线

**检查**：
```bash
# 在 192.168.0.221 上检查 Jenkins agent
ps aux | grep jenkins
```

**解决**：
1. Jenkins → **Manage Nodes → 192.168.0.221 → Launch agent**
2. 查看节点日志排查问题

### Q2: GitLab Webhook 失败

**检查**：
- GitLab Webhook 日志：Settings → Webhooks → Recent Deliveries
- Jenkins 日志：Jenkins → Manage Jenkins → System Log

**解决**：
- 确认 URL 正确：`http://192.168.0.220:9081/project/wes_backend-ci`
- 确认 Jenkins 可以从 GitLab 访问

### Q3: 构建失败 - uv 命令未找到

**检查**：
```bash
# 在 192.168.0.221 上检查
which uv
uv --version
```

**解决**：
```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Q4: Docker 权限错误

**检查**：
```bash
# 在 192.168.0.221 上检查
docker ps
```

**解决**：
```bash
# 将 Jenkins 用户添加到 docker 组
sudo usermod -aG docker jenkins
```

## 📊 验证清单总结

- [ ] Jenkins 可访问并登录
- [ ] Jenkins Node 在线
- [ ] GitLab 凭据已配置
- [ ] Pipeline 项目已创建
- [ ] GitLab Webhook 已配置并测试通过
- [ ] 部署服务器已初始化
- [ ] `Jenkinsfile.backend-ci` 与 `Jenkinsfile.test-deploy` 已提交
- [ ] 手动构建测试通过
- [ ] 所有阶段执行成功
- [ ] 部署成功并健康检查通过

## 🎯 完成后

配置完成后，您的 CI/CD 流程将自动运行：

1. **开发人员推送代码** → GitLab
2. **GitLab 触发 Webhook** → Jenkins
3. **Jenkins 在 Node 上执行**：
   - 构建 CI 镜像
   - 代码检查（并行）
   - 单元测试（并行）
   - 非 MR 推送 backend immutable tag 和 channel tag
   - 仅 develop 自动触发 TEST 部署
4. **TEST 部署健康检查**
5. **通知结果**（可选配置）

## 📞 需要帮助？

参考详细文档：
- [当前环境配置指南](jenkins-setup-current-env.md)
- [Jenkins 总览与现役 Job 说明](JENKINS.md)
