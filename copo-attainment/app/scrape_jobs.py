"""
Runs a VTU marks scrape as a background job instead of inside a Flask
request - each USN needs its own captcha-solve + page round-trip
(seconds each), so a roster-sized batch would blow well past any
reasonable HTTP timeout if run synchronously. This app doesn't have
Celery/Redis/RQ (nor does it need them for anything else it does), so
jobs are just a background `threading.Thread` plus an in-memory registry
the frontend polls - simple, and enough for one coordinator/teacher
running one scrape at a time locally, which is this app's whole
deployment model (see the export features for the same "keep it simple,
this runs on one person's machine" spirit).

Job state lives only in process memory - restarting the Flask process
loses in-flight and just-finished jobs. That's an acceptable trade for
not adding a job-queue dependency; a scrape that gets lost this way is
just re-run.
"""
import threading
import time
import uuid

from app.vtu_scraper import scrape_usns

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ABORTED = "aborted"
STATUS_ERROR = "error"

_MAX_LOG_LINES = 300
_COMPLETED_JOB_TTL_SECS = 3600  # prune finished jobs after an hour so a long-lived dev server doesn't leak memory

_jobs = {}
_lock = threading.Lock()


def start_job(app, course_id, usns, results_url, headless=True):
    """Kicks off a scrape of `usns` against `results_url` in a background
    thread and returns a job_id immediately. `app` is the real Flask app
    object (not the `current_app` proxy - that only works on the request
    thread) so the background thread can push its own app context for
    logging."""
    _prune_old_jobs()

    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "course_id": course_id,
        "status": STATUS_RUNNING,
        "processed": 0,
        "total": len(usns),
        "log": [],
        "result": None,  # a ScrapeRunResult, once status != "running"
        "error": None,
        "created_at": time.time(),
        "finished_at": None,
    }
    with _lock:
        _jobs[job_id] = job

    def on_progress(index, total, usn, message):
        with _lock:
            job["processed"] = index
            job["total"] = total
            job["log"].append(f"[{usn}] {message}")
            if len(job["log"]) > _MAX_LOG_LINES:
                job["log"] = job["log"][-_MAX_LOG_LINES:]

    def run():
        with app.app_context():
            try:
                result = scrape_usns(usns, results_url, headless=headless, on_progress=on_progress)
            except Exception as e:  # noqa: BLE001 - a scrape failing outright must not crash the thread silently
                app.logger.exception("VTU scrape job %s failed", job_id)
                with _lock:
                    job["status"] = STATUS_ERROR
                    job["error"] = str(e)
                    job["finished_at"] = time.time()
                return

            with _lock:
                job["result"] = result
                job["status"] = STATUS_ABORTED if result.aborted else STATUS_DONE
                job["finished_at"] = time.time()

    threading.Thread(target=run, name=f"vtu-scrape-{job_id}", daemon=True).start()
    return job_id


def get_job(job_id):
    """A read-only snapshot of the job's current state, or None if no
    such job exists (or it's since been pruned)."""
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None


def _prune_old_jobs():
    cutoff = time.time() - _COMPLETED_JOB_TTL_SECS
    with _lock:
        stale = [
            jid for jid, j in _jobs.items()
            if j["status"] != STATUS_RUNNING and j["finished_at"] is not None and j["finished_at"] < cutoff
        ]
        for jid in stale:
            del _jobs[jid]
