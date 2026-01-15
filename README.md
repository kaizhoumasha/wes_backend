# P9 WES Backend

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

3. **Run Application**
   ```bash
   uvicorn main:app --reload
   ```
   The API will be available at `http://localhost:8001`.
   Swagger UI: `http://localhost:8001/api/docs`

## Configuration
Configuration is managed via `.env` file. See `.env.example` for reference.
