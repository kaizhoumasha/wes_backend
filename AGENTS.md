# Repository Guidelines

> 本文件适用于所有 AI Agent（Claude Code、Codex、Cursor、Windsurf 等），确保统一的架构约束和开发规范。

---

## 🚨 Critical Architecture Rules

### Layered Architecture

```
API Layer → Service Layer → Repository Layer → Database
```

**STRICTLY FORBIDDEN**:
- ❌ API layer directly accessing database (`db.execute`, `select()`)
- ❌ API layer directly calling Repository
- ❌ Any cross-layer direct calls

**Detection Commands**:
```bash
grep -r "from sqlalchemy import select" src/app/*/v1/
grep -r "db.execute(" src/app/*/v1/
```

### Mixin Inheritance Rules

**⚠️ EnterpriseMixin already includes AuditMixin + OptimisticLockMixin**

```python
# ❌ WRONG: Duplicate inheritance
class User(UserBase, AuditMixin, EnterpriseMixin, SoftDeleteMixin, table=True)

# ✅ CORRECT
class User(UserBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True)
```

**Mixin Hierarchy**:
```
EnterpriseMixin = AuditableMixin + OptimisticLockMixin
AuditableMixin = AuditMixin + StandardMixin
AuditMixin → TimestampMixin → BaseMixin
```

### Timezone Rules

| Scenario | Method | Return Type |
|----------|--------|-------------|
| Database storage | `timezone.now_for_db()` | naive UTC |
| API response | `timezone.now_utc().isoformat()` | aware ISO |
| Timestamp calculation | `timezone.now_utc().timestamp()` | Unix seconds |

**⚠️ NEVER call `.timestamp()` on naive datetime**

### Module Export

New Service MUST be exported in `__init__.py`:

```python
from .xxx_service import XxxService, xxx_service

__all__ = ["XxxService", "xxx_service"]
```

---

## Design Principles

| Principle | Application |
|-----------|-------------|
| **DRY** | Mixin for field reuse, ModelFactory for Schema generation, Base classes for CRUD |
| **KISS** | Prefer base class defaults, avoid over-abstraction |
| **SOLID** | Repository/Service/API single responsibility, Hook/Mixin for extension |
| **YAGNI** | Implement only current requirements, no speculative design |

---

## Zero-Code Development Pattern

Inherit base classes to auto-generate full CRUD capabilities:

```python
# 1. Base fields (for Schema reuse)
class UserBase(BaseMixin):
    username: str
    email: str

# 2. Database table model (Base + Mixins + table-specific fields)
class User(UserBase, DataTableMixin, EnterpriseMixin, SoftDeleteMixin, table=True):
    __tablename__ = "users"
    hashed_password: str

# 3. Schema (ModelFactory auto-generates)
class UserCreate(ModelFactory(UserBase).for_create()):
    password: str

class UserUpdate(ModelFactory(UserBase).for_update()):
    pass

# 4. Repository/Service/API (zero code)
class UserRepository(BaseRepository[User]):
    pass

class UserService(BaseService[User, UserRepository]):
    def __init__(self):
        super().__init__(UserRepository(), enable_cache=True)

user_api = BaseAPI(
    module_name="admin",
    model=User,
    service=UserService(),
    create_schema=UserCreate,
    update_schema=UserUpdate,
    response_schema=UserResponse,
    prefix="/users",
)
```

**Auto-generated Routes**:
- `POST /users` - Create (permission: `admin:user:create`)
- `PUT /users/{id}` - Update (`admin:user:update`)
- `DELETE /users/{id}` - Delete (`admin:user:delete`)
- `GET /users/{id}` - Get single (`admin:user:detail`)
- `POST /users/query` - List query (`admin:user:list`)
- `POST /users/{id}/restore` - Restore (soft delete)
- `GET /users/trash` - Trash bin (soft delete)

---

## Documentation Index

| Document | Path | Content |
|----------|------|---------|
| Project File Index | `docs/architecture/file_index.md` | Code structure, directory docs, quick find, response codes |
| Architecture Details | `.claude/context/architecture.md` | Layered architecture, Hook/Mixin system, status validation, JWT/RBAC |
| Development Rules | `.claude/context/rules.md` | Layer rules, Service calls, module export, timezone rules |
| Common Tasks | `.claude/context/howto.md` | Create module, custom logic, status validation, tree structure |
| Troubleshooting | `.claude/context/troubleshooting.md` | Cache issues, N+1 queries, architecture violations, ImportError |

---

## Project Structure & Module Organization
Core code lives in `src/`. Domain modules sit under `src/app/`; infrastructure lives in `src/core/`, `src/database/`, `src/middleware/`, `src/utils/`, and `src/celery_app/`. The FastAPI entrypoint is [`main.py`](/Users/kaizhou/SynologyDrive/works/wes_backend/main.py), and Alembic revisions live in `migrations/versions/`. Tests are grouped under `tests/`, including `api/`, `auth/`, `e2e/`, `resilience/`, `load/`, and `mock/`.

## Build, Test, and Development Commands
Use `uv` locally.

- `./scripts/init-env.sh dev`: generate or refresh `.env` from the selected environment profile before first run or when switching environments.
- `docker-compose up -d`: start Postgres/TimescaleDB and Redis.
- `uv sync --dev`: install runtime and dev dependencies from `pyproject.toml` and `uv.lock`.
- `./scripts/migrate.sh upgrade`: apply the latest database migrations.
- `uv run uvicorn main:app --reload --host 0.0.0.0 --port 8001`: run the API locally.
- `uv run celery -A src.celery_app.app worker --loglevel=info --queues=default,celery`: start the async worker used by callback and workline flows.
- `sh src/celery_app/dev_worker_autoreload.sh`: run the Celery worker with source watching during active backend development.
- `./scripts/install-git-hooks.sh`: enable the repo-managed `pre-commit` quality gate for the current worktree.
- `./scripts/git-quality-gate.sh --profile quality`: run the same local quality gate used by the tracked `pre-commit` hook.
- `uv run pytest tests/`: run the full test suite.
- `uv run ruff format . && uv run ruff check .`: match the formatter and linter used in CI.
- `uv run bandit -r src/`: run the same security scan used by Jenkins.

## Worktree Workflow
Each git worktree must keep its own local runtime state. Do not reuse another worktree's `.venv`, `.env`, `.pytest_cache`, or other local temp files.

- Create a new worktree with `git worktree add ../wes_backend-<branch> -b <branch> develop`
- Enter the worktree and run `./scripts/init-env.sh dev`
- Run `uv sync --dev` inside that worktree to create or refresh its own `.venv`
- Run `./scripts/install-git-hooks.sh` inside that worktree if you want the tracked `pre-commit` quality gate active there
- Use `uv run ...` for all project commands instead of relying on a previously activated shell
- If `pyproject.toml`, `uv.lock`, or environment profile files change after switching branches, rerun `./scripts/init-env.sh dev` and `uv sync --dev`
- When a worktree is no longer needed, remove it with `git worktree remove <path>` and then `git worktree prune`

## Coding Style & Naming Conventions
Target Python 3.13. Ruff enforces formatting, imports, and lint rules: spaces, double quotes, and a 120-character line limit. Keep the existing layering: `v1/` for routes, `services/` for business logic, `repositories/` for data access, and `models/` for schemas. Use `snake_case` for files, functions, and variables, and `PascalCase` for classes.

## Comment Preservation Rules
When modifying existing code, **preserve all valuable comments** — they are critical for long-term maintainability.

- **Preserve**: Section headers (`# ===`), business logic explanations, parameter descriptions, inline clarifications, design rationale notes.
- **Preserve**: `TODO` / `FIXME` / `HACK` markers with their context.
- **Update**: When code behavior changes, update the corresponding comment to reflect the new behavior rather than deleting it.
- **Add**: When introducing non-obvious logic (workarounds, edge cases, performance considerations), add a brief comment explaining *why*, not just *what*.
- **Format**: Follow the project's existing comment style (e.g., `# ===` section dividers in config files).

**Examples**:
```python
# ✅ CORRECT: Preserve and update comment when modifying code
# Outbox 消息派发 - 将命令下发给设备（兜底）
# 正常流程由编排完成后即时 send_task 触发，Beat 仅处理遗漏/重试
"dispatch-outbox-batch": {
    "task": "src.celery_app.tasks.workline.dispatch_outbox_batch",
    "schedule": 10.0,  # 兜底轮询（原 1s，优化后 10s）
},

# ❌ WRONG: Stripping comments when modifying code
"dispatch-outbox-batch": {
    "task": "src.celery_app.tasks.workline.dispatch_outbox_batch",
    "schedule": 10.0,
},
```

## Testing Guidelines
Pytest uses `test_*.py`, `Test*`, and `test_*` discovery from `pyproject.toml`. Add unit tests near the affected domain and use `tests/e2e/` or `tests/resilience/` for flows that span APIs, queues, or device integrations. CI publishes coverage reports; changes should cover both success and failure paths.

## Commit & Pull Request Guidelines
Recent history follows Conventional Commits with scopes, for example `feat(auth,menu): ...` and `fix(user,tree): ...`. Keep subjects imperative and concise, and mention migrations when schema changes are included. PRs should summarize behavior changes, list local verification steps, link the issue, and call out config, migration, or API contract impacts.

## Configuration & Background Jobs
Environment profiles such as `.env.dev`, `.env.test`, and `.env.prod` feed the runtime `.env` generated by `./scripts/init-env.sh`; do not commit secrets. If a change touches callbacks, device events, or scheduled tasks, validate it with a running Celery worker and note that in the PR.

For git worktrees, treat `.env` as worktree-local state. Generating or editing `.env` in one worktree does not update any other worktree.
