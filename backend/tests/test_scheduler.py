"""Background jobs run in exactly one process (app/scheduler.py): with N
gunicorn workers each job used to run N times a minute."""
import pytest

from app import scheduler


def test_only_one_process_takes_the_scheduler_lock(tmp_path, monkeypatch):
    pytest.importorskip("fcntl")  # file locks are what the Linux server uses
    monkeypatch.setenv("SCHEDULER_LOCK_FILE", str(tmp_path / "scheduler.lock"))
    scheduler.get_settings.cache_clear()
    try:
        first = scheduler.claim()
        assert first is not None
        assert scheduler.claim() is None  # a second worker skips
        scheduler.release(first)
        again = scheduler.claim()  # the holder is gone: the next one takes over
        assert again is not None
        scheduler.release(again)
    finally:
        scheduler.get_settings.cache_clear()


def test_one_failing_job_does_not_stop_the_others(monkeypatch):
    ran = []

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler, "JOBS", (("broken", broken), ("next", lambda: ran.append("next"))))
    scheduler.run_jobs_once()
    assert ran == ["next"]
