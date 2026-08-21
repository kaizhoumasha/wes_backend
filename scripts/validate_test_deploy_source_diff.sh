#!/usr/bin/env bash
set -Eeuo pipefail

runtime_commit="${1:-}"
deploy_commit="${2:-}"

for commit in "${runtime_commit}" "${deploy_commit}"; do
    if [[ ! "${commit}" =~ ^[0-9a-f]{40}$ ]] || ! git cat-file -e "${commit}^{commit}" 2>/dev/null; then
        echo "TEST 部署提交无效或当前仓库不可达: ${commit}" >&2
        exit 2
    fi
done

if ! git merge-base --is-ancestor "${runtime_commit}" "${deploy_commit}"; then
    echo "DEPLOY_SOURCE_COMMIT_SHA 必须是后端运行提交的后继" >&2
    exit 2
fi

while IFS= read -r -d '' changed_path; do
    case "${changed_path}" in
        Jenkinsfile.backend-ci | \
            Jenkinsfile.test-deploy | \
            docker-compose.test-deploy.yml | \
            docker/test/* | \
            docs/architecture/heavy-test-impact.toml | \
            docs/devops/* | \
            scripts/classify_runtime_inbox_acceptance.py | \
            scripts/run_runtime_inbox_postgresql_acceptance.py | \
            scripts/run_runtime_inbox_postgresql_acceptance_ci.sh | \
            tests/deployment/* | \
            tests/scripts/test_select_heavy_tests.py | \
            tests/scripts/test_select_heavy_tests_regression_2.py | \
            scripts/validate_test_deploy_source_diff.sh)
            ;;
        *)
            echo "DEPLOY_SOURCE_COMMIT_SHA 包含运行时或未批准路径: ${changed_path}" >&2
            exit 2
            ;;
    esac
done < <(git diff --name-only -z "${runtime_commit}..${deploy_commit}")

if [[ "${runtime_commit}" == "${deploy_commit}" ]]; then
    echo "TEST_DEPLOY_SOURCE_DIFF=identical"
else
    echo "TEST_DEPLOY_SOURCE_DIFF=delivery-only"
fi
