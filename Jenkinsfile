// ==============================================
// Jenkins Pipeline - P9 WES Backend
// ==============================================
// 环境:
//   - GitLab: 192.168.0.220:9080
//   - Jenkins: 192.168.0.220 (Docker)
//   - Jenkins Node: 192.168.0.221 (构建和部署)
// ==============================================

pipeline {
    agent {
        label 'WES'
    }

    environment {
        PYTHON_VERSION = '3.13'
        DATETIME_TIMEZONE = 'Asia/Shanghai'

        // 部署配置（在 Jenkins Node 上执行拉镜像部署）
        DEPLOY_PATH = '/opt/wes_backend'
        DEPLOY_COMPOSE_FILE = 'docker-compose.deploy.yml'
        WMS_PROVIDER_PROFILE_HOST_FILE = '/etc/wes/wms-provider.yaml'
        HEALTH_CHECK_RETRIES = '5'

        // 镜像配置
        CI_BUILD_TARGET = 'testing'
        RUNTIME_BUILD_TARGET = 'production'
        REGISTRY_HOST = '192.168.0.220:5050'
        IMAGE_NAMESPACE = 'wes/wes_backend'
        REGISTRY_CREDENTIALS_ID = 'gitlab-registry-creds'
    }

    options {
        buildDiscarder(logRotator(numToKeepStr: '10'))
        timeout(time: 60, unit: 'MINUTES')
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timestamps()
    }

    stages {
        stage('Checkout Source') {
            steps {
                script {
                    String sourceBranch = env.gitlabSourceBranch ?: env.gitlabBranch ?: env.BRANCH_NAME ?: 'develop'
                    String targetBranch = env.gitlabTargetBranch ?: ''
                    String gitlabActionType = (env.gitlabActionType ?: env.GITLAB_OBJECT_KIND ?: '').trim().toUpperCase()
                    boolean hasMergeRequestId = ((env.gitlabMergeRequestId ?: '').trim()) as boolean
                    boolean isMergeRequest = gitlabActionType.contains('MERGE') || hasMergeRequestId

                    env.CI_SOURCE_BRANCH = sourceBranch
                    env.CI_TARGET_BRANCH = targetBranch
                    env.CI_EVENT_TYPE = gitlabActionType ?: 'MANUAL'
                    env.CI_IS_MERGE_REQUEST = isMergeRequest ? 'true' : 'false'

                    echo "📥 检出源码: source=${sourceBranch}, target=${targetBranch ?: '-'}, event=${env.CI_EVENT_TYPE}"

                    def extensions = [[$class: 'CleanBeforeCheckout']]
                    if (isMergeRequest && targetBranch) {
                        extensions << [
                            $class: 'PreBuildMerge',
                            options: [
                                fastForwardMode: 'FF',
                                mergeRemote: 'origin',
                                mergeTarget: targetBranch
                            ]
                        ]
                    }

                    checkout([
                        $class: 'GitSCM',
                        branches: [[name: "origin/${sourceBranch}"]],
                        userRemoteConfigs: [[
                            name: 'origin',
                            url: 'http://192.168.0.220:9080/wes/wes_backend.git',
                            credentialsId: 'gitlab-http-creds',
                            refspec: '+refs/heads/*:refs/remotes/origin/*'
                        ]],
                        extensions: extensions
                    ])

                    String fullCommit = sh(returnStdout: true, script: 'git rev-parse HEAD').trim()
                    String shortCommit = fullCommit.take(7)

                    env.SOURCE_BRANCH = sourceBranch
                    env.CI_COMMIT_SHA = fullCommit
                    env.CI_IMAGE = "wes-backend-ci:${env.BUILD_NUMBER}-${shortCommit}"
                    env.RUNTIME_VALIDATION_IMAGE = "wes-backend-runtime-check:${env.BUILD_NUMBER}-${shortCommit}"
                    env.MOCK_ECS_IMAGE = "wes-mock:${env.BUILD_NUMBER}-${shortCommit}-ecs"
                    env.MOCK_WMS_IMAGE = "wes-mock:${env.BUILD_NUMBER}-${shortCommit}-wms"
                    env.HEAVY_COMPOSE_PROJECT = "wes-backend-heavy-${env.EXECUTOR_NUMBER}-${env.BUILD_NUMBER}-${shortCommit}"
                    env.IMAGE_REPOSITORY = "${env.REGISTRY_HOST}/${env.IMAGE_NAMESPACE}"
                    env.IMMUTABLE_IMAGE_TAG = "${sourceBranch}-${env.BUILD_NUMBER}-${shortCommit}".replaceAll(/[^A-Za-z0-9_.-]/, '-')
                    env.PUBLISH_IMAGE = 'false'
                    env.DEPLOY_ENABLED = 'false'
                    env.CHANNEL_IMAGE_TAG = ''
                    env.DEPLOY_NAME = ''
                    env.DEPLOY_ENV_FILE = ''
                    env.DEPLOY_SERVICES = ''
                    env.DEPLOY_CONTAINER_NAME = ''
                    env.DEPLOY_REQUIRED_CONTAINERS = ''
                    env.RUNTIME_IMAGE = ''
                    env.CHANNEL_IMAGE = ''

                    if (!isMergeRequest) {
                        switch (sourceBranch) {
                            case 'develop':
                                env.PUBLISH_IMAGE = 'true'
                                env.DEPLOY_ENABLED = 'true'
                                env.CHANNEL_IMAGE_TAG = 'develop'
                                env.DEPLOY_NAME = 'testing'
                                env.DEPLOY_ENV_FILE = '.env.test'
                                env.DEPLOY_SERVICES = 'api celery celery-wms-fulfillment'
                                env.DEPLOY_CONTAINER_NAME = 'wes_api_test'
                                env.DEPLOY_REQUIRED_CONTAINERS = 'wes_postgres_test wes_redis_test'
                                break
                            case 'main':
                                env.PUBLISH_IMAGE = 'true'
                                env.DEPLOY_ENABLED = 'true'
                                env.CHANNEL_IMAGE_TAG = 'prod'
                                env.DEPLOY_NAME = 'production'
                                env.DEPLOY_ENV_FILE = '.env.prod'
                                env.DEPLOY_SERVICES = 'api celery celery-wms-fulfillment celery_beat flower nginx'
                                env.DEPLOY_CONTAINER_NAME = 'wes_api_prod'
                                env.DEPLOY_REQUIRED_CONTAINERS = 'wes_postgres_prod wes_redis_prod'
                                break
                        }
                    }

                    if (env.PUBLISH_IMAGE == 'true') {
                        env.RUNTIME_IMAGE = "${env.IMAGE_REPOSITORY}:${env.IMMUTABLE_IMAGE_TAG}"
                        env.CHANNEL_IMAGE = "${env.IMAGE_REPOSITORY}:${env.CHANNEL_IMAGE_TAG}"
                    }

                    echo "🐳 CI 镜像标签: ${env.CI_IMAGE}"
                    echo "📦 发布策略: publish=${env.PUBLISH_IMAGE}, branch=${env.SOURCE_BRANCH}, immutable=${env.RUNTIME_IMAGE ?: '-'}, channel=${env.CHANNEL_IMAGE ?: '-'}"
                    echo "🚀 部署策略: enabled=${env.DEPLOY_ENABLED}, target=${env.DEPLOY_NAME ?: '-'}, services=${env.DEPLOY_SERVICES ?: '-'}"
                }
            }
        }

        stage('Build CI Image') {
            steps {
                script {
                    echo '🐳 构建 CI 测试镜像...'
                    sh '''
                        set -e
                        docker build \
                            --target ${CI_BUILD_TARGET} \
                            -t ${CI_IMAGE} \
                            .
                    '''
                    echo '✅ CI 测试镜像构建完成'
                }
            }
        }

        stage('Quality Gate') {
            steps {
                script {
                    echo '🔍 运行唯一 QUALITY 门禁入口...'
                    // 默认 enforced；可用 ARCHITECTURE_GUARDRAIL_MODE=warn 临时回退。
                    sh '''
                        set -e
                        mkdir -p reports
                        docker run --rm \
                            --env-file "$WORKSPACE/.env.test" \
                            -e ARCHITECTURE_GUARDRAIL_MODE=${ARCHITECTURE_GUARDRAIL_MODE:-enforced} \
                            -v "$WORKSPACE/reports:/app/reports" \
                            ${CI_IMAGE} \
                            sh -c './scripts/git-quality-gate.sh --profile quality --bandit-json /app/reports/bandit-report.json --ci'
                    '''
                    echo '✅ QUALITY 门禁通过'
                }
            }
            post {
                always {
                    junit testResults: 'reports/fast-tests.xml', allowEmptyResults: true
                    archiveArtifacts artifacts: 'reports/**/*', allowEmptyArchive: true
                }
            }
        }

        stage('Mock Image Contracts') {
            when {
                expression {
                    env.CI_IS_MERGE_REQUEST == 'true'
                }
            }
            steps {
                script {
                    echo '🐳 验证 Mock 镜像入口合同...'
                    sh '''
                        set -eu
                        docker build -f tests/mock/Dockerfile -t ${MOCK_ECS_IMAGE} -t ${MOCK_WMS_IMAGE} .
                        docker run --rm ${MOCK_ECS_IMAGE} python -c 'import ecs_mock_server'
                        docker run --rm ${MOCK_WMS_IMAGE} python -c 'import wms_mock_server'
                        docker run --rm \
                            -e WMS_EFFECT_STATUS_VISIBILITY_SLA_SECONDS=2.5 \
                            -e WMS_EFFECT_SUBMIT_TIMEOUT_SECONDS=30.5 \
                            -e WMS_EFFECT_STATUS_TIMEOUT_SECONDS=2.5 \
                            ${MOCK_WMS_IMAGE} \
                            python -c "import asyncio; import wms_mock_server as module; contract=asyncio.run(module.northbound_contract()); assert contract['status_visibility_sla_seconds'] == 2.5; assert contract['submit_deadline_seconds'] == 30.5; assert contract['status_deadline_seconds'] == 2.5"
                    '''
                    echo '✅ Mock 镜像入口合同通过'
                }
            }
            post {
                always {
                    sh '''
                        docker image rm -f ${MOCK_ECS_IMAGE} ${MOCK_WMS_IMAGE} >/dev/null 2>&1 || true
                    '''
                }
            }
        }

        stage('HEAVY Required') {
            when {
                expression {
                    env.CI_IS_MERGE_REQUEST == 'true' && env.CI_TARGET_BRANCH?.trim()
                }
            }
            steps {
                script {
                    echo '🧭 选择并执行本次合并请求受影响的核心 HEAVY 测试...'
                    sh '''
                        set -eu
                        mkdir -p reports

                        docker run --rm \
                            -e CI_TARGET_BRANCH="${CI_TARGET_BRANCH}" \
                            -v "$WORKSPACE/.git:/app/.git:ro" \
                            ${CI_IMAGE} \
                            sh -c 'git config --global --add safe.directory /app && uv run --no-sync scripts/select_heavy_tests.py --base "origin/${CI_TARGET_BRANCH}"' \
                            > reports/heavy-tests.txt

                        if [ ! -s reports/heavy-tests.txt ]; then
                            echo '本次差异未选择核心 HEAVY 测试。'
                            exit 0
                        fi

                        docker compose \
                            -f docker-compose.ci-heavy.yml \
                            --env-file .env.test \
                            --project-name "${HEAVY_COMPOSE_PROJECT}" \
                            up -d --wait
                        docker run --rm \
                            --env-file "$WORKSPACE/.env.test" \
                            --network "${HEAVY_COMPOSE_PROJECT}_default" \
                            -e RUN_WORKLINE_INTEGRATION=1 \
                            -v "$WORKSPACE/reports/heavy-tests.txt:/artifacts/heavy-tests.txt:ro" \
                            -v "$WORKSPACE/reports:/reports" \
                            ${CI_IMAGE} \
                            sh -c '
                                set -eu
                                export INTEGRATION_DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}"
                                export INTEGRATION_REDIS_URL="redis://:${REDIS_PASSWORD}@redis:6379/0"
                                uv run --no-sync alembic upgrade head
                                uv run --no-sync scripts/run_selected_heavy_tests.py /artifacts/heavy-tests.txt /reports/heavy-required.xml
                            '
                    '''
                    echo '✅ 受影响的核心 HEAVY 测试通过'
                }
            }
            post {
                always {
                    junit testResults: 'reports/heavy-required.xml', allowEmptyResults: true
                    archiveArtifacts artifacts: 'reports/heavy-tests.txt,reports/heavy-required.xml', allowEmptyArchive: true
                    sh '''
                        docker compose \
                            -f docker-compose.ci-heavy.yml \
                            --env-file .env.test \
                            --project-name "${HEAVY_COMPOSE_PROJECT}" \
                            down --volumes --remove-orphans >/dev/null 2>&1 || true
                    '''
                }
            }
        }

        stage('Build Runtime Image') {
            steps {
                script {
                    if (env.PUBLISH_IMAGE == 'true') {
                        echo "🐳 构建运行时镜像: ${env.RUNTIME_IMAGE}"
                        sh '''
                            set -e
                            docker build \
                                --target ${RUNTIME_BUILD_TARGET} \
                                -t ${RUNTIME_IMAGE} \
                                -t ${CHANNEL_IMAGE} \
                                .
                        '''
                    } else {
                        echo "🐳 验证生产镜像构建: ${env.RUNTIME_VALIDATION_IMAGE}"
                        sh '''
                            set -e
                            docker build \
                                --target ${RUNTIME_BUILD_TARGET} \
                                -t ${RUNTIME_VALIDATION_IMAGE} \
                                .
                        '''
                    }
                    echo '✅ 运行时镜像构建完成'
                }
            }
            post {
                always {
                    script {
                        if (env.PUBLISH_IMAGE != 'true' && env.RUNTIME_VALIDATION_IMAGE?.trim()) {
                            sh '''
                                docker image rm -f ${RUNTIME_VALIDATION_IMAGE} >/dev/null 2>&1 || true
                            '''
                        }
                    }
                }
            }
        }

        stage('Publish Runtime Image') {
            when {
                expression {
                    env.PUBLISH_IMAGE == 'true'
                }
            }
            steps {
                script {
                    echo "📤 推送镜像到仓库: ${env.RUNTIME_IMAGE} / ${env.CHANNEL_IMAGE}"
                    withCredentials([
                        usernamePassword(
                            credentialsId: "${env.REGISTRY_CREDENTIALS_ID}",
                            usernameVariable: 'REGISTRY_USERNAME',
                            passwordVariable: 'REGISTRY_PASSWORD'
                        )
                    ]) {
                        sh '''
                            set -e
                            trap 'docker logout ${REGISTRY_HOST} >/dev/null 2>&1 || true' EXIT
                            echo "$REGISTRY_PASSWORD" | docker login ${REGISTRY_HOST} -u "$REGISTRY_USERNAME" --password-stdin
                            docker push ${RUNTIME_IMAGE}
                            docker push ${CHANNEL_IMAGE}
                        '''
                    }
                    echo '✅ 镜像推送完成'
                }
            }
        }

        stage('Deploy Runtime') {
            when {
                expression {
                    env.DEPLOY_ENABLED == 'true'
                }
            }
            steps {
                script {
                    echo "🚀 开始部署到${env.DEPLOY_NAME}: ${env.RUNTIME_IMAGE}"

                    withCredentials([
                        usernamePassword(
                            credentialsId: "${env.REGISTRY_CREDENTIALS_ID}",
                            usernameVariable: 'REGISTRY_USERNAME',
                            passwordVariable: 'REGISTRY_PASSWORD'
                        )
                    ]) {
                        sh '''
                            set -e
                            set -o pipefail

                            RED='\\033[0;31m'
                            GREEN='\\033[0;32m'
                            YELLOW='\\033[1;33m'
                            NC='\\033[0m'

                            echo -e "${GREEN}📂 切换到项目目录...${NC}"
                            cd ${DEPLOY_PATH}

                            echo -e "${GREEN}📥 更新部署清单...${NC}"
                            git fetch origin
                            git checkout --detach ${CI_COMMIT_SHA}
                            echo -e "${GREEN}📌 新提交: $(git log -1 --oneline)${NC}"

                            if [ -z "${WMS_PROVIDER_PROFILE_HOST_FILE:-}" ]; then
                                echo -e "${RED}❌ 未配置 WMS Provider profile 宿主机路径${NC}"
                                exit 1
                            fi
                            if [ ! -f "${WMS_PROVIDER_PROFILE_HOST_FILE}" ]; then
                                echo -e "${RED}❌ WMS Provider profile 不是普通文件: ${WMS_PROVIDER_PROFILE_HOST_FILE}${NC}"
                                exit 1
                            fi
                            if [ ! -r "${WMS_PROVIDER_PROFILE_HOST_FILE}" ]; then
                                echo -e "${RED}❌ WMS Provider profile 当前用户不可读: ${WMS_PROVIDER_PROFILE_HOST_FILE}${NC}"
                                exit 1
                            fi

                            export BACKEND_ENV_FILE=${DEPLOY_ENV_FILE}
                            export BACKEND_IMAGE=${RUNTIME_IMAGE}
                            export WMS_PROVIDER_PROFILE_HOST_FILE="${WMS_PROVIDER_PROFILE_HOST_FILE}"
                            COMPOSE_CMD="docker compose -f docker-compose.yml -f ${DEPLOY_COMPOSE_FILE} --env-file ${DEPLOY_ENV_FILE}"
                            HEALTH_ENDPOINT='http://127.0.0.1:8001/health'
                            SCHEMA_INITIALIZED=false
                            DEPLOYMENT_HEALTHY=false

                            # schema 初始化前按 Compose project 静默全部应用容器，仅保留数据库和 Redis。
                            # 通过 project/service 标签识别可覆盖任意服务重命名或删除，不维护旧服务名兼容清单。
                            quiesce_compose_application_containers() {
                                compose_project_name=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "${DEPLOY_ENV_FILE}" | tail -n 1)
                                compose_project_name=${compose_project_name:-$(basename "$PWD")}
                                application_container_ids=$(docker ps -q \
                                    --filter "label=com.docker.compose.project=${compose_project_name}")

                                for container_id in $application_container_ids; do
                                    compose_service=$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' "$container_id")
                                    case "$compose_service" in
                                    db|redis|nginx)
                                        ;;
                                        *)
                                            echo -e "${YELLOW}⏸️  停止应用容器: ${compose_service} (${container_id})${NC}"
                                            docker stop "$container_id"
                                            ;;
                                    esac
                                done
                            }

                            # 当前 schema 初始化后的任意失败都必须停止应用，覆盖容器部分启动、数据同步和健康检查失败。
                            cleanup_failed_deploy() {
                                exit_code=$?
                                if [ "$SCHEMA_INITIALIZED" = true ] && [ "$DEPLOYMENT_HEALTHY" != true ]; then
                                    echo -e "${YELLOW}⚠️  当前 schema 初始化后部署未完成，停止当前应用进程并等待前向修复${NC}"
                                    $COMPOSE_CMD stop ${DEPLOY_SERVICES} || true
                                fi
                                docker logout ${REGISTRY_HOST} >/dev/null 2>&1 || true
                                return $exit_code
                            }
                            trap cleanup_failed_deploy EXIT

                            # 始终由目标镜像中的唯一容量脚本读取 live PostgreSQL；失败由 set -e 阻断应用启动。
                            run_capacity_guard() {
                                $COMPOSE_CMD run --rm --no-deps -e DATABASE_RUNTIME_ROLE=cli -e DATABASE_POOL_SIZE=1 -e DATABASE_MAX_OVERFLOW=0 api python scripts/capacity_guard.py --services api,celery,celery-wms-fulfillment
                            }

                            echo "$REGISTRY_PASSWORD" | docker login ${REGISTRY_HOST} -u "$REGISTRY_USERNAME" --password-stdin

                            echo -e "${GREEN}🧱 启动基础设施并等待健康...${NC}"
                            $COMPOSE_CMD up -d --wait db redis

                            echo -e "${GREEN}🧱 检查基础设施容器状态...${NC}"
                            for required_container in ${DEPLOY_REQUIRED_CONTAINERS}; do
                                if ! docker inspect "$required_container" >/dev/null 2>&1; then
                                    echo -e "${RED}❌ 缺少基础设施容器: $required_container${NC}"
                                    echo -e "${YELLOW}ℹ️  基础设施启动阶段未创建目标容器，请检查 Compose 与环境配置${NC}"
                                    exit 1
                                fi

                                if [ "$(docker inspect -f '{{.State.Running}}' "$required_container")" != "true" ]; then
                                    echo -e "${RED}❌ 基础设施容器未运行: $required_container${NC}"
                                    exit 1
                                fi

                                container_health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$required_container")
                                if [ "$container_health" != "healthy" ] && [ "$container_health" != "none" ]; then
                                    echo -e "${RED}❌ 基础设施容器不健康: $required_container ($container_health)${NC}"
                                    exit 1
                                fi
                            done

                            echo -e "${GREEN}📥 拉取目标镜像...${NC}"
                            docker pull ${BACKEND_IMAGE}

                            echo -e "${GREEN}🧮 校验 live PostgreSQL 连接容量...${NC}"
                            run_capacity_guard

                            echo -e "${GREEN}🔏 校验 WMS 四角色部署一致性...${NC}"
                            bash scripts/run_wms_deployment_attestation.sh \
                                --compose-file docker-compose.yml \
                                --compose-file ${DEPLOY_COMPOSE_FILE} \
                                --env-file ${DEPLOY_ENV_FILE}

                            # 只允许目标镜像在静默应用进程后初始化当前 schema；不提供旧 schema 兼容或降级流程。
                            echo -e "${GREEN}⏸️  静默应用进程，准备初始化当前 schema...${NC}"
                            quiesce_compose_application_containers

                            echo -e "${GREEN}🗄️  使用目标镜像初始化当前 schema...${NC}"
                            $COMPOSE_CMD run --rm --no-deps -e DATABASE_RUNTIME_ROLE=cli -e DATABASE_POOL_SIZE=1 -e DATABASE_MAX_OVERFLOW=0 api alembic upgrade head
                            SCHEMA_INITIALIZED=true

                            echo -e "${GREEN}⚙️  启动新容器...${NC}"
                            $COMPOSE_CMD up -d --remove-orphans --no-build --no-deps ${DEPLOY_SERVICES} || {
                                echo -e "${RED}❌ 容器启动失败${NC}"
                                exit 1
                            }

                            echo -e "${GREEN}⏳ 等待容器启动...${NC}"
                            sleep 15

                            echo -e "${GREEN}🏥 健康检查...${NC}"
                            RETRY_COUNT=0
                            MAX_RETRIES=${HEALTH_CHECK_RETRIES}
                            HEALTH_CHECK_PASSED=false

                            while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
                                if docker exec ${DEPLOY_CONTAINER_NAME} curl -f -s -o /dev/null -w "%{http_code}" ${HEALTH_ENDPOINT} | grep -q "200"; then
                                    echo -e "${GREEN}✅ 健康检查通过 (尝试 $((RETRY_COUNT + 1))/$MAX_RETRIES)${NC}"
                                    HEALTH_CHECK_PASSED=true
                                    break
                                fi

                                RETRY_COUNT=$((RETRY_COUNT + 1))
                                echo -e "${YELLOW}⏳ 健康检查失败，等待重试... ($RETRY_COUNT/$MAX_RETRIES)${NC}"
                                sleep 5
                            done

                            if [ "$HEALTH_CHECK_PASSED" = false ]; then
                                echo -e "${RED}❌ 健康检查失败，停止新应用进程...${NC}"
                                $COMPOSE_CMD logs --tail=100 api
                                echo -e "${YELLOW}⚠️  当前 schema 初始化后部署未完成，停止当前应用进程并等待前向修复${NC}"
                                exit 1
                            fi

                            echo -e "${GREEN}🧭 校验 Celery 运行时...${NC}"
                            CELERY_HEALTH_TIMEOUT_SECONDS="${CELERY_HEALTH_TIMEOUT_SECONDS:-120}"
                            # 先校验全部受管服务均已运行，再对当前环境实际启动的 Celery 服务执行深度健康检查。
                            for service_name in ${DEPLOY_SERVICES}; do
                                service_container_ids=$($COMPOSE_CMD ps -q "$service_name")
                                if [ -z "$service_container_ids" ]; then
                                    echo -e "${RED}❌ ${service_name} 容器未就绪${NC}"
                                    exit 1
                                fi
                                for container_id in $service_container_ids; do
                                    if [ "$(docker inspect -f '{{.State.Running}}' "$container_id")" != "true" ]; then
                                        echo -e "${RED}❌ ${service_name} 容器未就绪: $container_id${NC}"
                                        exit 1
                                    fi
                                done

                                case "$service_name" in
                                celery|celery-wms-fulfillment|celery_beat)
                                    for container_id in $service_container_ids; do
                                        container_health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")
                                        health_deadline=$(( $(date +%s) + CELERY_HEALTH_TIMEOUT_SECONDS ))
                                        while [ "$container_health" != "healthy" ] && [ "$container_health" != "none" ]; do
                                            if [ "$(date +%s)" -ge "$health_deadline" ]; then
                                                echo -e "${RED}❌ ${service_name} 健康检查超时: $container_id ($container_health)${NC}"
                                                exit 1
                                            fi
                                            if [ "$(docker inspect -f '{{.State.Running}}' "$container_id")" != "true" ]; then
                                                echo -e "${RED}❌ ${service_name} 容器等待健康期间退出: $container_id${NC}"
                                                exit 1
                                            fi
                                            sleep 2
                                            container_health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")
                                        done
                                    done
                                    ;;
                                esac
                            done

                            for celery_service in celery celery-wms-fulfillment; do
                                for container_id in $($COMPOSE_CMD ps -q "$celery_service"); do
                                    if ! docker exec "$container_id" sh -c 'celery -A src.celery_app.app inspect ping -d "celery@$(hostname)" --timeout=5 | grep -q pong'; then
                                        echo -e "${RED}❌ ${celery_service} 子进程运行时未就绪: $container_id${NC}"
                                        exit 1
                                    fi
                                done
                            done

                            DEPLOYMENT_HEALTHY=true
                            echo -e "${GREEN}✅ ${DEPLOY_NAME} 部署完成${NC}"
                            $COMPOSE_CMD ps
                        '''
                    }

                    echo '✅ 部署成功'
                }
            }
        }
    }

    post {
        always {
            script {
                if (env.CI_IMAGE?.trim()) {
                    sh '''
                        docker image rm -f ${CI_IMAGE} >/dev/null 2>&1 || true
                    '''
                }
            }
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

// ==============================================
// Jenkins 配置说明
// ==============================================
// 必需的 Jenkins 插件:
//   - GitLab Plugin
//   - JUnit Plugin
//   - Git Plugin
//
// 可选插件:
//   - HTML Publisher Plugin（用于 Jenkins 页面展示覆盖率 HTML 报告）
//
// 必需的配置:
//   1. Jenkins Node (192.168.0.221) 已配置并在线
//   2. Node 标签: WES
//   3. GitLab HTTP 凭据已配置（ID: gitlab-http-creds）
//   4. Docker Registry 凭据已配置（ID: gitlab-registry-creds）
//
// 部署流程:
//   1. GitLab 推送代码触发 Webhook
//   2. Jenkins 在 Node (192.168.0.221) 上构建 CI 测试镜像
//   3. 并行执行代码检查和测试
//   4. develop 分支推送 immutable + develop 镜像并自动部署 testing
//   5. main 分支推送 immutable + prod 镜像并自动部署 production
//   6. 部署机确保基础设施健康，并对完整 API/Celery 目标拓扑执行 live PostgreSQL 容量门禁
//   7. 门禁通过后停止应用进程，并用目标镜像的一次性 CLI 容器初始化当前 schema
//   8. schema 初始化成功后以 --no-build/--no-deps 方式启动后端应用服务
//   9. 健康检查失败时停止当前应用并等待前向修复
