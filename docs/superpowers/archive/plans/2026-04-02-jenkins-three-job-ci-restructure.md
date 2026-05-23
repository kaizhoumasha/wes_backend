# Jenkins Three-Job CI Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the current monolithic Jenkins pipeline into independent frontend CI, backend CI, and test deployment jobs so `develop` pushes deploy TEST by pulling prebuilt images from GitLab Container Registry instead of rebuilding frontend on the deploy host.

**Architecture:** `wes_frontend-ci` builds and pushes a versioned frontend image. `wes_backend-ci` runs backend quality gates, builds and pushes a versioned backend image, and only triggers TEST deployment for `develop` push events. `wes_test_deploy` pulls the two immutable images from GitLab Container Registry and recreates the TEST stack on `192.168.0.221`, keeping Jenkins as the only CI/CD entry while removing frontend cold-start work from backend pushes.

**Tech Stack:** Jenkins Pipeline, GitLab webhook integration, GitLab Container Registry, Docker build/push/pull, Docker Compose, FastAPI backend, Vue/Vite frontend, Nginx.

---

### Task 1: Freeze the target file layout

**Files:**
- Create: `docs/superpowers/plans/2026-04-02-jenkins-three-job-ci-restructure.md`
- Create: `Jenkinsfile.backend-ci`
- Create: `Jenkinsfile.test-deploy`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Create: `docker-compose.test-deploy.yml`
- Create: `docker/test/api.entrypoint.sh`
- Create: `docker/test/celery.entrypoint.sh`
- Create: `docker/test/registry-login.sh`
- Create: `Jenkinsfile` in frontend repo worktree
- Modify: `Dockerfile` in frontend repo worktree

- [ ] **Step 1: Confirm the current backend repo still contains the monolithic `Jenkinsfile` and test deploy compose files**

Run: `rg --files . -g 'Jenkinsfile*' -g 'docker-compose*.yml' -g 'Dockerfile*'`
Expected: monolithic `Jenkinsfile`, backend `Dockerfile`, `docker-compose.yml`, and `docker-compose.frontend.yml` exist.

- [ ] **Step 2: Confirm frontend work must happen in a separate worktree**

Run: `git -C /Users/kaizhou/SynologyDrive/works/wes_frontend status --short`
Expected: dirty working tree so no CI files are added in the main frontend checkout.

- [ ] **Step 3: Keep the existing `Jenkinsfile` intact until the replacement files are ready**

Rationale: Jenkins jobs can point to script paths from SCM, so the migration can be staged without breaking the currently configured job immediately.

- [ ] **Step 4: Commit the planning artifact after implementation begins**

```bash
git add docs/superpowers/plans/2026-04-02-jenkins-three-job-ci-restructure.md
git commit -m "docs(ci): add three-job jenkins restructure plan"
```

### Task 2: Create an isolated frontend CI worktree

**Files:**
- Create: frontend worktree at `/Users/kaizhou/SynologyDrive/works/wes_frontend-worktrees/ci/frontend-jenkins-split`
- Modify: frontend repo files only inside the worktree

- [ ] **Step 1: Create the frontend worktree from `develop`**

Run: `git -C /Users/kaizhou/SynologyDrive/works/wes_frontend worktree add /Users/kaizhou/SynologyDrive/works/wes_frontend-worktrees/ci/frontend-jenkins-split -b ci/frontend-jenkins-split develop`
Expected: a clean frontend worktree exists on a dedicated branch.

- [ ] **Step 2: Install frontend dependencies inside the worktree**

Run: `pnpm -C /Users/kaizhou/SynologyDrive/works/wes_frontend-worktrees/ci/frontend-jenkins-split install --no-frozen-lockfile`
Expected: `node_modules` and lockfile state are ready only for the worktree.

- [ ] **Step 3: Verify the frontend baseline commands before editing**

Run: `pnpm -C /Users/kaizhou/SynologyDrive/works/wes_frontend-worktrees/ci/frontend-jenkins-split run build:dev`
Expected: baseline frontend build passes or any pre-existing failure is recorded before CI edits.

### Task 3: Add independent frontend image CI

**Files:**
- Modify: frontend worktree `Dockerfile`
- Create: frontend worktree `Jenkinsfile`

- [ ] **Step 1: Make the frontend Dockerfile accept build-time API parameters and produce a registry-ready image**

```dockerfile
ARG VITE_API_BASE_URL=/api/v1
ARG VITE_API_PROXY_TARGET=http://api:8001
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_API_PROXY_TARGET=${VITE_API_PROXY_TARGET}
RUN pnpm run build
```

- [ ] **Step 2: Add a Jenkins pipeline that builds, tests, logs in to GitLab registry, and pushes immutable tags**

```groovy
environment {
    REGISTRY = '192.168.0.220:5050'
    IMAGE_REPO = '192.168.0.220:5050/wes/wes_frontend'
}
```

Pipeline responsibilities:
- checkout the branch from GitLab webhook metadata
- run `pnpm install`, `pnpm run type:check`, `pnpm run lint`, `pnpm run build:dev`
- build Docker image with `${BUILD_NUMBER}-${shortCommit}` and branch alias tags
- push commit tag always
- push `develop` alias when branch is `develop`

- [ ] **Step 3: Verify the frontend CI logic locally**

Run: `docker build -t wes-frontend:local /Users/kaizhou/SynologyDrive/works/wes_frontend-worktrees/ci/frontend-jenkins-split`
Expected: image builds successfully with bundled static assets.

- [ ] **Step 4: Commit the frontend CI split**

```bash
git -C /Users/kaizhou/SynologyDrive/works/wes_frontend-worktrees/ci/frontend-jenkins-split add Dockerfile Jenkinsfile
git -C /Users/kaizhou/SynologyDrive/works/wes_frontend-worktrees/ci/frontend-jenkins-split commit -m "feat(ci): add frontend image pipeline"
```

### Task 4: Split backend CI from deployment

**Files:**
- Create: `Jenkinsfile.backend-ci`
- Modify: `Dockerfile`
- Create: `docker/test/registry-login.sh`

- [ ] **Step 1: Copy the quality and test stages out of the current `Jenkinsfile` into `Jenkinsfile.backend-ci`**

Keep:
- webhook-aware checkout
- quality stages
- pytest stages

Remove:
- frontend clone logic
- deploy logic
- compose-based rebuild on `192.168.0.221`

- [ ] **Step 2: Extend the backend Dockerfile with a production-ready runtime image used by deployment**

```dockerfile
FROM base AS production
COPY --from=builder /opt/venv /opt/venv
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
```

Also keep the `testing` target intact so CI can still use it for lint and tests.

- [ ] **Step 3: Push backend runtime images to GitLab Container Registry from `Jenkinsfile.backend-ci`**

Required tags:
- `${BUILD_NUMBER}-${shortCommit}`
- branch alias such as `develop`

Develop push behavior:
- build and push backend image
- trigger `wes_test_deploy` with explicit parameters for backend tag and frontend tag fallback

- [ ] **Step 4: Verify backend CI syntax and image build locally**

Run: `docker build --target production -t wes-backend:local .`
Expected: backend runtime image builds without the CI-only dependencies.

- [ ] **Step 5: Commit the backend CI split**

```bash
git add Jenkinsfile.backend-ci Dockerfile docker/test/registry-login.sh
git commit -m "feat(ci): split backend ci from deploy"
```

### Task 5: Convert TEST deploy to image-based compose

**Files:**
- Create: `Jenkinsfile.test-deploy`
- Create: `docker-compose.test-deploy.yml`
- Create: `docker/test/api.entrypoint.sh`
- Create: `docker/test/celery.entrypoint.sh`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Create a deployment-only compose overlay that references registry images instead of building from source**

```yaml
services:
  api:
    image: ${BACKEND_IMAGE}
    volumes:
      - ./logs:/app/logs
  celery_worker:
    image: ${BACKEND_IMAGE}
  frontend:
    image: ${FRONTEND_IMAGE}
```

The deploy overlay must not mount frontend source code or run `pnpm install`.

- [ ] **Step 2: Add entrypoints so TEST deployment can run migrations and service startup against pulled images**

```sh
#!/bin/sh
set -e
alembic upgrade head
exec uvicorn main:app --host 0.0.0.0 --port 8001
```

and

```sh
#!/bin/sh
set -e
exec celery -A src.celery_app.app worker --loglevel=${CELERY_LOG_LEVEL:-INFO} --queues=default,celery,device
```

- [ ] **Step 3: Add `Jenkinsfile.test-deploy` to log in to GitLab registry on `192.168.0.221`, pull images, and recreate only TEST services**

Inputs:
- `BACKEND_IMAGE_TAG`
- `FRONTEND_IMAGE_TAG`
- `SOURCE_BRANCH`

Behavior:
- choose `develop` alias when explicit tag is absent
- `docker login 192.168.0.220:5050`
- `docker compose --env-file .env.test -f docker-compose.yml -f docker-compose.test-deploy.yml pull`
- `docker compose ... up -d --force-recreate`
- run health checks for `api`, `nginx`, and `/`

- [ ] **Step 4: Verify deployment compose rendering locally**

Run: `docker compose -f docker-compose.yml -f docker-compose.test-deploy.yml --env-file .env.test config`
Expected: `api`, `celery_worker`, and `frontend` resolve to image-based services with no source mounts.

- [ ] **Step 5: Commit the deployment split**

```bash
git add Jenkinsfile.test-deploy docker-compose.test-deploy.yml docker/test/api.entrypoint.sh docker/test/celery.entrypoint.sh
git commit -m "feat(ci): add image-based test deploy pipeline"
```

### Task 6: Wire Jenkins jobs and remote TEST host

**Files:**
- Modify: Jenkins job configuration on `192.168.0.220`
- Modify: TEST deploy checkout at `/opt/wes_backend` on `192.168.0.221` if needed

- [ ] **Step 1: Create Jenkins job `wes_frontend-ci`**

Configuration:
- Pipeline script from SCM
- repo: `http://192.168.0.220:9080/wes/wes_frontend.git`
- credentials: `gitlab-http-creds`
- script path: `Jenkinsfile`
- agent label: frontend-capable Jenkins node

- [ ] **Step 2: Create Jenkins job `wes_backend-ci`**

Configuration:
- Pipeline script from SCM
- repo: `http://192.168.0.220:9080/wes/wes_backend.git`
- credentials: `gitlab-http-creds`
- script path: `Jenkinsfile.backend-ci`
- trigger on push and merge request webhook

- [ ] **Step 3: Create Jenkins job `wes_test_deploy`**

Configuration:
- Pipeline script from SCM
- repo: `http://192.168.0.220:9080/wes/wes_backend.git`
- credentials: `gitlab-http-creds`
- script path: `Jenkinsfile.test-deploy`
- build with parameters enabled
- only backend CI triggers it automatically for `develop` push

- [ ] **Step 4: Verify the remote deploy host has registry access**

Run on `192.168.0.221`: `docker login 192.168.0.220:5050`
Expected: deploy host can pull both frontend and backend images.

### Task 7: End-to-end validation

**Files:**
- No new files beyond the implementation set

- [ ] **Step 1: Run backend local validation**

Run: `docker build --target testing -t wes-backend-ci:local .`
Expected: backend CI image still builds.

- [ ] **Step 2: Run frontend local validation**

Run: `pnpm -C /Users/kaizhou/SynologyDrive/works/wes_frontend-worktrees/ci/frontend-jenkins-split run build:dev`
Expected: frontend build passes in the worktree after CI edits.

- [ ] **Step 3: Push both feature branches to GitLab for Jenkins pickup**

```bash
git -C /Users/kaizhou/SynologyDrive/works/wes_frontend-worktrees/ci/frontend-jenkins-split push gitlab ci/frontend-jenkins-split
git -C /tmp/wes_backend-jenkins-ci-fix push gitlab ci/fix-jenkins-healthcheck
```

- [ ] **Step 4: Manually run the three Jenkins jobs once in order**

Order:
1. `wes_frontend-ci`
2. `wes_backend-ci`
3. `wes_test_deploy`

Expected:
- frontend image exists in registry
- backend image exists in registry
- TEST deploy completes without cloning frontend or installing pnpm dependencies

- [ ] **Step 5: Confirm the final TEST chain on `192.168.0.221`**

Run: `docker compose -f docker-compose.yml -f docker-compose.test-deploy.yml --env-file .env.test ps`
Expected: `api`, `celery_worker`, `frontend`, `nginx`, `db`, and `redis` are up, and nginx serves the frontend with backend API proxying intact.
