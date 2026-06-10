import importlib


def test_workline_resource_wait_retry_default_matches_inbox_beat_interval(monkeypatch):
    from src.app.workline import constants

    monkeypatch.delenv("WORKLINE_RESOURCE_WAIT_RETRY_SECONDS", raising=False)
    reloaded = importlib.reload(constants)
    try:
        assert reloaded.WORKLINE_RESOURCE_WAIT_RETRY_SECONDS == 10
    finally:
        importlib.reload(constants)


def test_workline_resource_wait_retry_seconds_allows_environment_override(monkeypatch):
    from src.app.workline import constants

    monkeypatch.setenv("WORKLINE_RESOURCE_WAIT_RETRY_SECONDS", "7")
    reloaded = importlib.reload(constants)
    try:
        assert reloaded.WORKLINE_RESOURCE_WAIT_RETRY_SECONDS == 7
    finally:
        monkeypatch.delenv("WORKLINE_RESOURCE_WAIT_RETRY_SECONDS", raising=False)
        importlib.reload(constants)


def test_inbox_processing_stale_floor_no_longer_depends_on_bucket_lock(monkeypatch):
    from src.app.workline import constants

    monkeypatch.setenv("WORKLINE_INBOX_PROCESSING_STALE_SECONDS", "10")
    reloaded = importlib.reload(constants)
    try:
        assert not hasattr(reloaded, "WORKLINE_INBOX_BATCH_PARALLELISM")
        assert not hasattr(reloaded, "WORKLINE_INBOX_BATCH_MAX_PARALLELISM")
        assert not hasattr(reloaded, "INBOX_BUCKET_LOCK_TTL_SECONDS")
        assert reloaded.WORKLINE_INBOX_PROCESSING_STALE_SECONDS == (
            reloaded.INBOX_PROCESS_TIMEOUT_SECONDS + reloaded.INBOX_PROCESSING_STALE_MARGIN_SECONDS
        )
        assert "WORKLINE_INBOX_BATCH_PARALLELISM" not in reloaded.__all__
        assert "INBOX_BUCKET_LOCK_TTL_SECONDS" not in reloaded.__all__
    finally:
        monkeypatch.delenv("WORKLINE_INBOX_PROCESSING_STALE_SECONDS", raising=False)
        importlib.reload(constants)
