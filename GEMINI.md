# P9 WES Backend - Project Instructions (GEMINI.md)

## Project Overview
P9 WES Backend is a high-performance, developer-friendly backend framework specialized for **WMS (Warehouse Management Systems)** and **WES (Warehouse Execution Systems)**. It is built on **FastAPI**, **SQLModel**, and **SQLAlchemy 2.0**, leveraging **TimescaleDB** for time-series data and **Redis** for caching and task orchestration.

### Key Architectural Pillars
- **Layered Architecture**: `API Layer → Service Layer → Repository Layer → Database`. Strictly enforced via linting and conventions.
- **Zero-Code CRUD**: Developers inherit from `BaseAPI`, `BaseService`, and `BaseRepository` to auto-generate fully functional REST APIs for domain models.
- **ModelFactory**: Automatically generates `Create`, `Update`, and `Response` Pydantic schemas from SQLModel definitions.
- **Hook & Mixin System**: Business logic is extended via Repository hooks (e.g., `before_create`, `after_update`) and reusable Mixins (e.g., `EnterpriseMixin` for auditing + optimistic locking).
- **Domain-Driven Design (DDD)**: Logic is organized into domain modules under `src/app/` (e.g., `admin`, `workline`, `device`, `wms_integration`).
- **WORKLINE Runtime**: A specialized system for orchestrating real-time warehouse equipment, including diagnostics, tracing, and hardware simulation.

---

## Building and Running

### Prerequisites
- **Python**: 3.13+
- **Infrastructure**: Docker & Docker Compose (for Postgres/TimescaleDB and Redis)
- **Dependency Manager**: `uv` (strongly recommended)

### Core Commands
| Action | Command |
| :--- | :--- |
| **Setup Env** | `./scripts/init-env.sh dev` |
| **Install Deps** | `uv sync --dev` |
| **Infra Up** | `docker-compose up -d` |
| **Migrations** | `./scripts/migrate.sh upgrade` |
| **Dev Server** | `uvicorn main:app --reload --port 8001` |
| **Celery Worker** | `uv run celery -A src.celery_app.app worker --loglevel=info` |
| **Quality Gate** | `./scripts/git-quality-gate.sh --profile quality` |
| **Test Suite** | `uv run pytest tests/` |
| **Token Savings** | `rtk gain` |
| **Impact Analysis**| `npx gitnexus impact --target <symbol>` |

---

## Development Conventions

### 1. The Layering Rule (CRITICAL)
- **API Layer**: Handles routing, validation (via `BaseAPI`), and permissions. **NEVER** access the DB directly here.
- **Service Layer**: Handles business logic, transaction boundaries, and caching.
- **Repository Layer**: Handles raw data access and persistence logic.

### 2. Zero-Code Pattern
To create a new module, define:
1. **Base Model**: `class UserBase(BaseMixin): ...`
2. **Table Model**: `class User(UserBase, EnterpriseMixin, table=True): ...`
3. **Schemas**: Generated via `ModelFactory`.
4. **Service/Repo/API**: Inherit from `BaseService`, `BaseRepository`, and `BaseAPI`.

### 3. Safety & Tooling
- **GitNexus Impact**: Before modifying any function or class, **MUST** run `gitnexus_impact` to evaluate side effects.
- **RTK Optimization**: All shell commands are automatically proxied via RTK. Use `rtk proxy` only if raw output is required.

### 4. Data Integrity & Mixins
- **EnterpriseMixin**: Standard for business tables. Includes `AuditMixin` (who/when) and `OptimisticLockMixin` (versioning).
- **SoftDeleteMixin**: Use for data that should be archivable.
- **Timezones**: Always use `timezone.now_for_db()` (naive UTC) for storage and `timezone.now_utc().isoformat()` for API responses.

### 4. Testing & Quality
- **TDD**: Highly encouraged. Use `pytest` for unit/integration tests and `tests/mock/` for hardware simulators.
- **Linting**: Ruff is the primary linter and formatter. Configured in `pyproject.toml` with strict rules for `async` and `security`.
- **Type Checking**: BasedPyright is used for static analysis. Use `scripts/basedpyright-local.sh` to check types locally.

### 5. Git & Commit Style
- **Conventional Commits**: Use `feat:`, `fix:`, `refactor:`, etc.
- **Quality Gate**: Run `./scripts/install-git-hooks.sh` to enable pre-commit checks.
- **Chinese/English**: Chinese is the preferred language for internal documentation, communication, and commit comments within this project context.

---

## Technical Context for AI Agents
- **Metadata Discovery**: If exploring a new domain under `src/app/`, check its `models/` for field definitions and `v1/` for API patterns.
- **Circular Dependencies**: Use `from __future__ import annotations` and avoid cross-domain Repository imports; prefer Service-to-Service calls.
- **Repository Hooks**: Look for `_before_create`, `_after_update`, etc., in `BaseRepository` subclasses to find custom business logic.
