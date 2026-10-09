"""真实 Celery Beat Scheduler 经 broker 唤醒原核心维护任务与 prefork worker。

复用既有 prefork_services 独占数据库与 worker生命周期；显式
RUN_WORKLINE_INTEGRATION=1、INTEGRATION_DATABASE_URL、INTEGRATION_REDIS_URL。
"""

import asyncio
from datetime import timedelta

import asyncpg
import pytest
from celery.beat import Scheduler
from sqlalchemy.engine import make_url

from src.celery_app.config import beat_schedule, task_routes
from src.utils.timezone import timezone
from tests.integration.test_celery_async_runtime_postgresql import PreforkWorker, prefork_services

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


def test_beat_entry_dispatches_cleanup_to_real_default_worker(prefork_services, monkeypatch):
    worker = PreforkWorker(prefork_services)
    worker.queue = "default"
    producer = worker.configure_producer()
    # start 的 readiness probe 会首次使用并缓存 producer router。
    producer.conf.task_routes = task_routes
    producer.conf.beat_schedule = {"cleanup-api-access-logs": beat_schedule["cleanup-api-access-logs"]}
    worker.start()
    success = False
    try:
        with asyncio.Runner() as runner:
            connection = runner.run(
                asyncpg.connect(
                    make_url(prefork_services["database_url"])
                    .set(drivername="postgresql")
                    .render_as_string(hide_password=False)
                )
            )
            scheduler = None
            try:
                runner.run(
                    connection.execute(
                        "INSERT INTO wes_sys.api_access_logs (id, created_at, system_id, direction, method, path) VALUES ($1, $2, 'wms', 'INBOUND', 'POST', '/events')",
                        987654320,
                        timezone.now_for_db() - timedelta(days=8),
                    )
                )
                scheduler = Scheduler(app=producer)
                entry = scheduler.schedule["cleanup-api-access-logs"]
                entry.last_run_at = timezone.now_utc() - timedelta(seconds=61)
                results = []
                apply_async = scheduler.apply_async

                def capture(*args, **kwargs):
                    result = apply_async(*args, **kwargs)
                    results.append(result)
                    return result

                monkeypatch.setattr(scheduler, "apply_async", capture)
                scheduler.tick()
                assert len(results) == 1
                assert worker.result(results[0]) == 1
                assert (
                    runner.run(
                        connection.fetchval("SELECT count(*) FROM wes_sys.api_access_logs WHERE id = $1", 987654320)
                    )
                    == 0
                )
                assert worker.result(producer.send_task("src.celery_app.tasks.core.cleanup_api_access_logs")) == 0
            finally:
                try:
                    if scheduler is not None:
                        scheduler.close()
                finally:
                    runner.run(connection.close())
        success = True
    finally:
        worker.stop(success=success)
