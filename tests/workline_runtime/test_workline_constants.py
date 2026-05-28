import importlib


def test_inbox_processing_stale_seconds_is_clamped_above_processing_timeout(monkeypatch):
    from src.app.workline import constants

    monkeypatch.setenv("WORKLINE_INBOX_PROCESSING_STALE_SECONDS", "10")
    reloaded = importlib.reload(constants)
    try:
        assert (
            reloaded.WORKLINE_INBOX_PROCESSING_STALE_SECONDS
            >= reloaded.INBOX_BUCKET_LOCK_TTL_SECONDS + reloaded.INBOX_PROCESSING_STALE_MARGIN_SECONDS
        )
    finally:
        monkeypatch.delenv("WORKLINE_INBOX_PROCESSING_STALE_SECONDS", raising=False)
        importlib.reload(constants)
