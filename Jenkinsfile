// ==============================================
// Jenkins Pipeline - P9 WES Backend
// ==============================================
// 环境:
//   - GitLab: 192.168.0.220:9080
//   - Jenkins: 192.168.0.220 (Docker)
//   - Jenkins Node: 192.168.0.221 (构建和部署)
// ==============================================

pipeline {
    // 在 Jenkins Node 上执行（192.168.0.221）
    agent {
        label 'WES'  // 请根据实际的 Node 标签修改
    }

    // 环境变量
    environment {
        // Python 版本
        PYTHON_VERSION = '3.13'
        // 时区配置
        DATETIME_TIMEZONE = 'Asia/Shanghai'
        // 部署配置（本地部署，因为在 Node 上执行）
        DEPLOY_PATH = '/opt/wes_backend'
        DEPLOY_ENV_FILE = '.env.test'
        DEPLOY_COMPOSE_FILE = 'docker-compose.yml'
        DEPLOY_PROFILE = 'test'
        // 健康检查配置
        HEALTH_CHECK_URL = 'http://localhost:8001/api/health'
        HEALTH_CHECK_RETRIES = '5'
    }

    // 触发器配置
    triggers {
        // GitLab Webhook 触发（推荐）
        gitlab(
            triggerOnPush: true,
            triggerOnMergeRequest: true,
            branchFilterType: 'All'
        )
    }

    // 构建选项
    options {
        // 保留最近 10 次构建
        buildDiscarder(logRotator(numToKeepStr: '10'))
        // 超时配置
        timeout(time: 30, unit: 'MINUTES')
        // 禁用并发构建
        disableConcurrentBuilds()
        // 时间戳
        timestamps()
        // GitLab 连接
        gitLabConnection('gitlab')
    }

    stages {
        // ==============================================
        // 准备阶段：安装依赖
        // ==============================================
        stage('Prepare') {
            steps {
                script {
                    echo '📦 安装开发依赖...'
                    sh 'uv sync --frozen'
                    echo '✅ 依赖安装完成'
                }
            }
        }

        // ==============================================
        // 质量阶段：代码检查（并行执行）
        // ==============================================
        stage('Quality Checks') {
            parallel {
                // 代码格式检查
                stage('Format Check') {
                    steps {
                        script {
                            echo '🔍 检查代码格式 (Ruff Format)...'
                            sh 'uv run ruff format --check .'
                            echo '✅ 代码格式检查通过'
                        }
                    }
                }

                // 代码质量检查
                stage('Lint Check') {
                    steps {
                        script {
                            echo '🔍 检查代码质量 (Ruff Lint)...'
                            sh 'uv run ruff check . --output-format=github || true'
                            echo '✅ 代码质量检查完成'
                        }
                    }
                }

                // 安全检查
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
                            // 归档安全报告
                            archiveArtifacts artifacts: 'bandit-report.json', allowEmptyArchive: true
                        }
                    }
                }
            }
        }

        // ==============================================
        // 测试阶段：单元测试（并行执行）
        // ==============================================
        stage('Tests') {
            parallel {
                // 单元测试
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
                            // 发布测试报告
                            junit 'reports/junit.xml'
                            // 发布覆盖率报告
                            publishHTML([
                                allowMissing: false,
                                alwaysLinkToLastBuild: true,
                                keepAll: true,
                                reportDir: 'reports/coverage',
                                reportFiles: 'index.html',
                                reportName: 'Coverage Report'
                            ])
                            // 归档报告
                            archiveArtifacts artifacts: 'reports/**/*', allowEmptyArchive: true
                        }
                    }
                }

                // API 签名测试
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

        // ==============================================
        // 部署阶段：部署到测试环境（本地部署）
        // ==============================================
        stage('Deploy to Testing') {
            when {
                branch 'develop'
            }
            steps {
                script {
                    echo "🚀 开始部署到测试环境..."

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

                        # 拉取最新代码
                        git fetch origin
                        git checkout ${GIT_COMMIT}

                        echo -e "${GREEN}📌 新提交: $(git log -1 --oneline)${NC}"

                        echo -e "${GREEN}🔧 配置 Docker 镜像加速器...${NC}"
                        if [ ! -f /etc/docker/daemon.json ] || ! grep -q "docker.happyjack.cn" /etc/docker/daemon.json; then
                            sudo mkdir -p /etc/docker
                            sudo tee /etc/docker/daemon.json > /dev/null << 'DOCKER_EOF'
{
  "registry-mirrors": ["https://docker.happyjack.cn"]
}
DOCKER_EOF
                            sudo systemctl restart docker
                            sleep 5
                            echo -e "${GREEN}✅ Docker 镜像加速器配置完成${NC}"
                        else
                            echo -e "${YELLOW}⏭️  Docker 镜像加速器已配置，跳过${NC}"
                        fi

                        echo -e "${GREEN}🐳 构建 Docker 镜像...${NC}"
                        docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} build --no-cache || {
                            echo -e "${RED}❌ Docker 镜像构建失败${NC}"
                            echo -e "${YELLOW}🔄 回滚到上一个提交: $PREVIOUS_COMMIT${NC}"
                            git checkout $PREVIOUS_COMMIT
                            exit 1
                        }

                        echo -e "${GREEN}🔄 停止旧容器...${NC}"
                        docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} --profile ${DEPLOY_PROFILE} down || true

                        echo -e "${GREEN}⚙️  启动新容器...${NC}"
                        docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} --profile ${DEPLOY_PROFILE} up -d || {
                            echo -e "${RED}❌ 容器启动失败${NC}"
                            echo -e "${YELLOW}🔄 回滚到上一个提交: $PREVIOUS_COMMIT${NC}"
                            git checkout $PREVIOUS_COMMIT
                            docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} build
                            docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} --profile ${DEPLOY_PROFILE} up -d
                            exit 1
                        }

                        echo -e "${GREEN}⏳ 等待容器启动...${NC}"
                        sleep 15

                        echo -e "${GREEN}🗄️  运行数据库迁移...${NC}"
                        docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} exec -T api alembic upgrade head || {
                            echo -e "${YELLOW}⚠️  数据库迁移失败或已跳过${NC}"
                        }

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
                        echo -e "${GREEN}📊 容器状态：${NC}"
                        docker compose -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE} --profile ${DEPLOY_PROFILE} ps

                        echo -e "${GREEN}📈 资源使用情况：${NC}"
                        docker stats --no-stream --format "table {{.Name}}\\t{{.CPUPerc}}\\t{{.MemUsage}}"
                    '''

                    echo '✅ 部署成功'
                }
            }
        }
    }

    // ==============================================
    // 后置处理
    // ==============================================
    post {
        always {
            // 清理工作空间
            cleanWs()
        }
        success {
            echo '✅ Pipeline 执行成功'
            // GitLab 状态更新
            updateGitlabCommitStatus name: 'build', state: 'success'
        }
        failure {
            echo '❌ Pipeline 执行失败'
            // GitLab 状态更新
            updateGitlabCommitStatus name: 'build', state: 'failed'
        }
    }
}

// ==============================================
// Jenkins 配置说明
// ==============================================
// 必需的 Jenkins 插件:
//   - GitLab Plugin
//   - JUnit Plugin
//   - HTML Publisher Plugin
//   - Git Plugin
//
// 必需的配置:
//   1. Jenkins Node (192.168.0.221) 已配置并在线
//   2. Node 标签: test-node（请根据实际标签修改）
//   3. GitLab 凭据已配置（ID: gitlab-credentials）
//   4. GitLab Connection 已配置（Name: gitlab）
//
// 必需的服务（在 Jenkins Node 上）:
//   - PostgreSQL (Docker 容器)
//   - Redis (Docker 容器)
//   - Docker 和 Docker Compose
//
// 触发方式:
//   1. GitLab Webhook（推荐）
//   2. 手动触发: Jenkins → wes-backend → Build Now
//
// 部署流程:
//   1. GitLab 推送代码触发 Webhook
//   2. Jenkins 在 Node (192.168.0.221) 上执行构建
//   3. 安装依赖
//   4. 并行执行代码检查和测试
//   5. 在本地（Node）部署到测试环境
//   6. 拉取最新代码
//   7. 构建 Docker 镜像
//   8. 停止旧容器，启动新容器
//   9. 运行数据库迁移
//   10. 健康检查（5 次重试）
//   11. 失败时自动回滚到上一个版本
//
// 优势:
//   - 无需 SSH 连接（直接在 Node 上执行）
//   - 更快的部署速度
//   - 更简单的配置
//   - 更好的日志输出
//
// 注意事项:
//   - 确保 Jenkins Node 标签正确（agent { label 'test-node' }）
//   - 确保 Node 上已安装 uv、Docker、Docker Compose
//   - 确保 /opt/wes_backend 目录已初始化
//   - 确保 .env.test 文件已配置
//   - 生产环境部署需要单独配置
