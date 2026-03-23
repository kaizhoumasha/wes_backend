# Jenkins CI/CD 配置指南 - 当前环境

## 📋 环境信息

- **GitLab 服务器**：192.168.0.220:9080（Docker）
- **Jenkins 服务器**：192.168.0.220（Docker）
- **Jenkins Node 节点**：192.168.0.221（已配置）
- **GitLab 仓库**：http://192.168.0.220:9080/wes/wes_backend.git
- **LDAP 账号**：zhoukai / Ctt123456
- **部署目标**：192.168.0.221（测试环境）

## 🚀 配置步骤

### 步骤 1：访问 Jenkins

1. 浏览器访问：`http://192.168.0.220:8080`（或 Jenkins 实际端口）
2. 使用 LDAP 账号登录：
   - 用户名：`zhoukai`
   - 密码：`Ctt123456`

### 步骤 2：验证 Jenkins Node 配置

1. 进入 **Manage Jenkins → Manage Nodes and Clouds**
2. 确认 192.168.0.221 节点状态为 **在线**
3. 查看节点标签（Labels），记录下来（例如：`test-node`、`docker` 等）

### 步骤 3：配置 GitLab 凭据

#### 3.1 生成 GitLab Personal Access Token

1. 登录 GitLab：`http://192.168.0.220:9080`
2. 用户头像 → **Preferences** → **Access Tokens**
3. 创建新 Token：
   ```
   Name: jenkins-ci
   Scopes:
     ✅ api
     ✅ read_repository
     ✅ write_repository
   ```
4. 点击 **Create personal access token**
5. **复制并保存** Token（只显示一次）

#### 3.2 在 Jenkins 中添加 GitLab 凭据

1. Jenkins → **Manage Jenkins → Manage Credentials**
2. 选择 **(global)** 域
3. 点击 **Add Credentials**
4. 配置：
   ```
   Kind: Username with password
   Scope: Global
   Username: zhoukai
   Password: <粘贴刚才的 GitLab Token>
   ID: gitlab-credentials
   Description: GitLab 凭据 - zhoukai
   ```
5. 点击 **Create**

### 步骤 4：配置 SSH 凭据（用于部署）

#### 4.1 检查 SSH 密钥

在 Jenkins 服务器（192.168.0.220）上执行：

```bash
# 进入 Jenkins 容器
docker exec -it jenkins bash

# 检查是否有 SSH 密钥
ls -la ~/.ssh/

# 如果没有，生成新密钥
ssh-keygen -t rsa -b 4096 -C "jenkins@wes-backend" -f ~/.ssh/jenkins_rsa -N ""

# 查看公钥
cat ~/.ssh/jenkins_rsa.pub
```

#### 4.2 将公钥添加到部署服务器

在部署服务器（192.168.0.221）上执行：

```bash
# 添加 Jenkins 公钥
mkdir -p ~/.ssh
chmod 700 ~/.ssh

# 将上面的公钥内容添加到 authorized_keys
cat >> ~/.ssh/authorized_keys << 'EOF'
<粘贴 Jenkins 容器中的 ~/.ssh/jenkins_rsa.pub 内容>
EOF

chmod 600 ~/.ssh/authorized_keys
```

#### 4.3 在 Jenkins 中添加 SSH 凭据

1. Jenkins → **Manage Jenkins → Manage Credentials**
2. 点击 **Add Credentials**
3. 配置：
   ```
   Kind: SSH Username with private key
   Scope: Global
   ID: ssh-test-server
   Description: SSH 私钥 - 测试服务器 (192.168.0.221)
   Username: root
   Private Key: Enter directly
     - 在 Jenkins 容器中执行：cat ~/.ssh/jenkins_rsa
     - 粘贴私钥内容（包括 BEGIN 和 END 行）
   ```
4. 点击 **Create**

#### 4.4 测试 SSH 连接

在 Jenkins 容器中测试：

```bash
ssh -i ~/.ssh/jenkins_rsa root@192.168.0.221 "echo 'SSH 连接成功'"
```

### 步骤 5：创建 Jenkins Pipeline 项目

#### 5.1 创建项目

1. Jenkins 首页 → **New Item**
2. 输入项目名称：`wes-backend`
3. 选择 **Pipeline**
4. 点击 **OK**

#### 5.2 配置项目

##### General 配置

- ✅ **Discard old builds**
  - Strategy: Log Rotation
  - Max # of builds to keep: `10`

- ✅ **GitLab Connection**（如果安装了 GitLab 插件）
  - GitLab connection: 选择或创建 GitLab 连接

##### Build Triggers

选择以下之一：

**方式 1：GitLab Webhook（推荐）**
- ✅ **Build when a change is pushed to GitLab**
- GitLab webhook URL: `http://192.168.0.220:8080/project/wes-backend`
- 记录这个 URL，稍后在 GitLab 中配置

**方式 2：Poll SCM（备选）**
- ✅ **Poll SCM**
- Schedule: `H/5 * * * *`（每 5 分钟检查一次）

##### Pipeline 配置

- **Definition**: Pipeline script from SCM
- **SCM**: Git
  - **Repository URL**: `http://192.168.0.220:9080/wes/wes_backend.git`
  - **Credentials**: 选择 `gitlab-credentials`
  - **Branches to build**: `*/develop`（或 `*/main`）
- **Script Path**: `Jenkinsfile`

#### 5.3 保存配置

点击 **Save** 保存配置。

### 步骤 6：配置 GitLab Webhook（推荐）

#### 6.1 在 GitLab 中配置 Webhook

1. 登录 GitLab：`http://192.168.0.220:9080`
2. 进入项目：**wes / wes_backend**
3. 左侧菜单 → **Settings → Webhooks**
4. 配置 Webhook：
   ```
   URL: http://192.168.0.220:8080/project/wes-backend
   Secret token: (留空或设置一个密钥)
   Trigger:
     ✅ Push events
     ✅ Merge request events
   SSL verification:
     ⚠️ Enable SSL verification（如果使用 HTTPS）
     或取消勾选（如果使用 HTTP）
   ```
5. 点击 **Add webhook**

#### 6.2 测试 Webhook

1. 在 Webhook 列表中找到刚创建的 Webhook
2. 点击 **Test → Push events**
3. 查看响应：
   - **HTTP 200**：成功
   - **其他状态码**：检查 Jenkins 日志

### 步骤 7：配置部署服务器（192.168.0.221）

#### 7.1 初始化项目目录

在 192.168.0.221 上执行：

```bash
# 创建项目目录
sudo mkdir -p /opt/wes_backend
sudo chown -R root:root /opt/wes_backend

# 克隆代码
cd /opt/wes_backend
git clone http://192.168.0.220:9080/wes/wes_backend.git .

# 配置 Git 凭据（避免每次输入密码）
git config credential.helper store
git pull  # 输入一次用户名和密码后会保存
```

#### 7.2 创建环境文件

```bash
cd /opt/wes_backend

# 复制环境文件模板
cp .env.example .env.test

# 编辑环境文件
vim .env.test
```

配置示例：

```bash
# 数据库配置
DATABASE_URL=postgresql+psycopg://wes_user:wes_password@wes_postgres:5432/wes_db

# Redis 配置
REDIS_URL=redis://wes_redis:6379/0

# 应用配置
ENVIRONMENT=test
DEBUG=false
SECRET_KEY=<生成一个随机密钥>

# 时区配置
DATETIME_TIMEZONE=Asia/Shanghai

# 日志配置
LOG_LEVEL=INFO
```

#### 7.3 验证 Docker 和 Docker Compose

```bash
# 检查 Docker
docker --version
docker ps

# 检查 Docker Compose
docker-compose --version
```

### 步骤 8：调整 Jenkinsfile（使用 Node 节点）

由于您已经配置了 Jenkins Node（192.168.0.221），需要调整 Jenkinsfile 以在 Node 上执行构建和测试。

创建 `Jenkinsfile.node` 文件：

```groovy
pipeline {
    agent {
        label 'test-node'  // 使用您的 Node 标签
    }

    environment {
        PYTHON_VERSION = '3.13'
        DATETIME_TIMEZONE = 'Asia/Shanghai'
        DEPLOY_HOST = '192.168.0.221'
        DEPLOY_USER = 'root'
        DEPLOY_PATH = '/opt/wes_backend'
        DEPLOY_ENV_FILE = '.env.test'
        DEPLOY_COMPOSE_FILE = 'docker-compose.yml'
        DEPLOY_PROFILE = 'test'
        HEALTH_CHECK_URL = 'http://localhost:8001/api/health'
        HEALTH_CHECK_RETRIES = '5'
    }

    options {
        buildDiscarder(logRotator(numToKeepStr: '10'))
        timeout(time: 30, unit: 'MINUTES')
        disableConcurrentBuilds()
        timestamps()
    }

    stages {
        stage('Prepare') {
            steps {
                script {
                    echo '📦 安装开发依赖...'
                    sh 'uv sync --frozen'
                    echo '✅ 依赖安装完成'
                }
            }
        }

        stage('Quality Checks') {
            parallel {
                stage('Format Check') {
                    steps {
                        script {
                            echo '🔍 检查代码格式 (Ruff Format)...'
                            sh 'uv run ruff format --check .'
                            echo '✅ 代码格式检查通过'
                        }
                    }
                }

                stage('Lint Check') {
                    steps {
                        script {
                            echo '🔍 检查代码质量 (Ruff Lint)...'
                            sh 'uv run ruff check . --output-format=github || true'
                            echo '✅ 代码质量检查完成'
                        }
                    }
                }

                stage('Security Check') {
                    steps {
                        script {
                            echo '🔍 安全检查 (Bandit)...'
                            sh '''
                                uv run bandit -r src/ -f json -o bandit-report.json || true
                                uv run bandit -r src/ -f screen
                            '''
                            echo '✅ 安全检查完成'
                        }
                    }
                    post {
                        always {
                            archiveArtifacts artifacts: 'bandit-report.json', allowEmptyArchive: true
                        }
                    }
                }
            }
        }

        stage('Tests') {
            parallel {
                stage('Unit Tests') {
                    steps {
                        script {
                            echo '🧪 运行单元测试...'
                            sh '''
                                uv run pytest tests/ -v --tb=short \
                                    --cov=src \
                                    --cov-report=term-missing \
                                    --cov-report=html:reports/coverage \
                                    --cov-report=xml:reports/coverage.xml \
                                    --junitxml=reports/junit.xml
                            '''
                            echo '✅ 单元测试通过'
                        }
                    }
                    post {
                        always {
                            junit 'reports/junit.xml'
                            publishHTML([
                                allowMissing: false,
                                alwaysLinkToLastBuild: true,
                                keepAll: true,
                                reportDir: 'reports/coverage',
                                reportFiles: 'index.html',
                                reportName: 'Coverage Report'
                            ])
                            archiveArtifacts artifacts: 'reports/**/*', allowEmptyArchive: true
                        }
                    }
                }

                stage('API Signature Tests') {
                    steps {
                        script {
                            echo '🔐 测试 API 签名认证...'
                            sh 'uv run pytest tests/api/test_signature.py -v --tb=short'
                            echo '✅ API 签名测试通过'
                        }
                    }
                }
            }
        }

        stage('Deploy to Testing') {
            when {
                branch 'develop'
            }
            steps {
                script {
                    echo "🚀 开始部署到测试环境 (${DEPLOY_HOST})..."

                    sh '''
                        set -e
                        set -o pipefail

                        # 颜色输出
                        RED='\\033[0;31m'
                        GREEN='\\033[0;32m'
                        YELLOW='\\033[1;33m'
                        NC='\\033[0m'

                        echo -e "${GREEN}📂 切换到项目目录...${NC}"
                        cd ${DEPLOY_PATH}

                        echo -e "${GREEN}📥 更新代码...${NC}"
                        PREVIOUS_COMMIT=$(git rev-parse HEAD)
                        echo "📌 当前提交: $PREVIOUS_COMMIT"

                        git fetch origin
                        git checkout ${GIT_COMMIT}

                        echo -e "${GREEN}📌 新提交: $(git log -1 --oneline)${NC}"

                        echo -e "${GREEN}🐳 构建 Docker 镜像...${NC}"
                        docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} build --no-cache

                        echo -e "${GREEN}🔄 停止旧容器...${NC}"
                        docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} --profile ${DEPLOY_PROFILE} down || true

                        echo -e "${GREEN}⚙️  启动新容器...${NC}"
                        docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} --profile ${DEPLOY_PROFILE} up -d

                        echo -e "${GREEN}⏳ 等待容器启动...${NC}"
                        sleep 15

                        echo -e "${GREEN}🗄️  运行数据库迁移...${NC}"
                        docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} exec -T api alembic upgrade head || true

                        echo -e "${GREEN}🏥 健康检查...${NC}"
                        RETRY_COUNT=0
                        MAX_RETRIES=${HEALTH_CHECK_RETRIES}
                        HEALTH_CHECK_PASSED=false

                        while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
                            if curl -f -s -o /dev/null -w "%{http_code}" ${HEALTH_CHECK_URL} | grep -q "200"; then
                                echo -e "${GREEN}✅ 健康检查通过 (尝试 $((RETRY_COUNT + 1))/$MAX_RETRIES)${NC}"
                                HEALTH_CHECK_PASSED=true
                                break
                            else
                                RETRY_COUNT=$((RETRY_COUNT + 1))
                                echo -e "${YELLOW}⏳ 健康检查失败，等待重试... ($RETRY_COUNT/$MAX_RETRIES)${NC}"
                                sleep 5
                            fi
                        done

                        if [ "$HEALTH_CHECK_PASSED" = false ]; then
                            echo -e "${RED}❌ 健康检查失败，开始回滚...${NC}"
                            docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} --profile ${DEPLOY_PROFILE} logs --tail=100 api

                            echo -e "${YELLOW}🔄 回滚到上一个提交: $PREVIOUS_COMMIT${NC}"
                            git checkout $PREVIOUS_COMMIT
                            docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} build
                            docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} --profile ${DEPLOY_PROFILE} down
                            docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} --profile ${DEPLOY_PROFILE} up -d
                            exit 1
                        fi

                        echo -e "${GREEN}🧹 清理未使用的 Docker 资源...${NC}"
                        docker system prune -f --volumes || true

                        echo -e "${GREEN}✅ 测试环境部署完成${NC}"
                        docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} --profile ${DEPLOY_PROFILE} ps
                    '''

                    echo '✅ 部署成功'
                }
            }
        }
    }

    post {
        always {
            cleanWs()
        }
        success {
            echo '✅ Pipeline 执行成功'
        }
        failure {
            echo '❌ Pipeline 执行失败'
        }
    }
}
```

### 步骤 9：提交 Jenkinsfile 到 GitLab

```bash
# 提交 Jenkinsfile
git add Jenkinsfile
git commit -m "chore(ci): 添加 Jenkins Pipeline 配置"
git push gitlab develop
```

### 步骤 10：测试 Pipeline

#### 10.1 手动触发构建

1. Jenkins → **wes-backend** 项目
2. 点击 **Build Now**
3. 查看构建日志：点击构建号 → **Console Output**

#### 10.2 验证各阶段

检查以下阶段是否成功：

- ✅ **Prepare**：依赖安装
- ✅ **Quality Checks**：格式检查、代码质量、安全检查
- ✅ **Tests**：单元测试、API 签名测试
- ✅ **Deploy to Testing**：部署到测试环境

#### 10.3 查看报告

- **测试报告**：Jenkins → wes-backend → **Test Result**
- **覆盖率报告**：Jenkins → wes-backend → **Coverage Report**
- **安全报告**：Jenkins → wes-backend → **Artifacts** → bandit-report.json

### 步骤 11：验证部署

```bash
# 在任意机器上测试
curl http://192.168.0.221:8001/api/health

# 预期响应
{"status": "healthy"}
```

## 🔧 故障排查

### 问题 1：GitLab 凭据认证失败

**症状**：`Authentication failed` 或 `Permission denied`

**解决**：

1. 确认 GitLab Personal Access Token 是否正确
2. 确认 Token 权限包含 `api`、`read_repository`、`write_repository`
3. 在 Jenkins 中重新配置凭据

### 问题 2：Jenkins Node 离线

**症状**：构建失败，提示 `No available executors`

**解决**：

1. Jenkins → **Manage Jenkins → Manage Nodes and Clouds**
2. 检查 192.168.0.221 节点状态
3. 如果离线，点击节点 → **Launch agent**
4. 查看节点日志排查问题

### 问题 3：SSH 连接失败

**症状**：部署阶段失败，提示 `Permission denied`

**解决**：

```bash
# 在 Jenkins 容器中测试
docker exec -it jenkins bash
ssh -i ~/.ssh/jenkins_rsa root@192.168.0.221 "echo 'SSH 连接成功'"

# 如果失败，检查：
# 1. 公钥是否正确添加到 192.168.0.221
# 2. 私钥权限是否正确（600）
# 3. authorized_keys 权限是否正确（600）
```

### 问题 4：Docker 构建失败

**症状**：`docker: command not found` 或权限错误

**解决**：

```bash
# 在 Jenkins Node (192.168.0.221) 上检查
docker --version
docker ps

# 确保 Jenkins 用户有 Docker 权限
sudo usermod -aG docker jenkins
```

### 问题 5：健康检查失败

**症状**：部署后健康检查超时

**解决**：

```bash
# 在 192.168.0.221 上检查
cd /opt/wes_backend
docker-compose -f docker-compose.yml --env-file .env.test --profile test ps
docker-compose -f docker-compose.yml --env-file .env.test --profile test logs api

# 检查端口是否监听
netstat -tuln | grep 8001

# 手动测试健康检查
curl http://localhost:8001/api/health
```

## 📊 验证清单

完成以下检查确保配置成功：

- [ ] Jenkins 可以访问（http://192.168.0.220:8080）
- [ ] LDAP 登录成功（zhoukai/Ctt123456）
- [ ] Jenkins Node (192.168.0.221) 状态在线
- [ ] GitLab 凭据已配置
- [ ] SSH 凭据已配置
- [ ] SSH 连接测试通过
- [ ] Pipeline 项目已创建
- [ ] GitLab Webhook 已配置
- [ ] Jenkinsfile 已提交到 GitLab
- [ ] 手动构建测试通过
- [ ] 所有阶段执行成功
- [ ] 测试报告正常显示
- [ ] 部署到测试环境成功
- [ ] 健康检查通过

## 🎯 下一步优化

1. **配置通知**：
   - 安装 Email Extension 插件
   - 配置构建失败邮件通知

2. **多分支 Pipeline**：
   - 创建 Multibranch Pipeline
   - 自动发现和构建所有分支

3. **生产环境部署**：
   - 添加生产环境部署阶段
   - 需要手动确认才能部署

4. **性能优化**：
   - 配置 Docker 卷缓存
   - 使用 Jenkins 缓存插件

## 📞 支持

如有问题，请联系：
- 技术支持：dev-team@example.com
- 文档反馈：提交 Issue 到 GitLab 项目
