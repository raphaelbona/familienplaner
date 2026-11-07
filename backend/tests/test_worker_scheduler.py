"""Every scheduled worker job must actually run.

In APScheduler 3 an explicit ``next_run_time=None`` adds a job *paused*, which silently
stopped recurring tasks, timetable events, recurring calendar events and the CalDAV sync.
"""

from datetime import UTC, datetime, timedelta

from app.worker import sync_worker

EXPECTED_JOBS = {
    "sync_google_calendars",
    "sync_caldav_calendars",
    "generate_timetable_events",
    "generate_recurring_task_instances",
    "generate_recurring_calendar_events",
}


def _jobs():
    return {job.func.__name__: job for job in sync_worker.build_scheduler().get_jobs()}


def test_all_expected_jobs_are_registered():
    assert set(_jobs()) == EXPECTED_JOBS


def test_no_job_is_paused():
    paused = [name for name, job in _jobs().items() if job.next_run_time is None]
    assert paused == []


def test_every_job_runs_soon_after_a_worker_start():
    # The worker is restarted on every deployment; with 6 hour intervals the jobs would otherwise
    # never get a turn if the worker restarts more often than that.
    now = datetime.now(UTC)
    for name, job in _jobs().items():
        assert job.next_run_time <= now + timedelta(minutes=5), name


def test_first_runs_are_staggered():
    first_runs = [job.next_run_time for job in _jobs().values()]
    assert len(set(first_runs)) == len(first_runs)
