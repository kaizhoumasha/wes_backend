# P9 WES Backend

**Version**: 0.1.0.0 - 初始生产版本

P9 WES Backend 是基于 FastAPI + SQLModel + SQLAlchemy 2.0 的快速开发框架，专为 WMS/WES 系统设计。采用分层架构和零代码开发模式。

**核心特性**：
- **零代码 CRUD**：继承 BaseAPI 自动生成 REST API
- **ModelFactory**：自动生成 Create/Update Schema
- **Hook 系统**：Repository 层业务逻辑扩展
- **Mixin 组合**：复用模型字段和行为
- **RBAC 权限**：基于角色的访问控制
- **TimescaleDB**：时序数据存储

## Environment Setup

### Prerequisites
- Python 3.13+
- Docker & Docker Compose
- `uv` (recommended for dependency management)

### Development

1. **Start Infrastructure**
   Start TimescaleDB (Postgres 17) and Redis (8.x) using Docker Compose:
   ```bash
   docker-compose up -d
   ```

2. **Install Dependencies**
   ```bash
   uv sync
   ```

3. **Run Database Migrations**
   Apply database schema migrations:
   ```bash
   ./scripts/migrate.sh upgrade
   ```
   See [Database Migration Guide](docs/database_migration.md) for more details.

4. **Run Application**
   ```bash
   uvicorn main:app --reload
   ```
   The API will be available at `http://localhost:8001`.
   Swagger UI: `http://localhost:8001/api/docs`

## Configuration
Configuration is managed via `.env` file. See `.env.example` for reference.
