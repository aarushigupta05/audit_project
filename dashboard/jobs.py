"""
dashboard/jobs.py
-----------------
A small in-process background job runner for the dashboard -- UI-agnostic
(no `import streamlit`), so it is unit-tested directly (tests/test_jobs.py).

Why it exists: ledger processing takes ~45-60s and "Re-run Analysis" can take
tens of seconds. Streamlit re-executes the whole script on every widget
interaction and aborts the run in progress when one arrives, so doing that
work inside the script meant that clicking a tab, or uploading a second
file, while something was processing killed it half-way (or restarted it).
Running it here instead means:
  - the work continues no matter what the person clicks or whether the
    browser tab is refreshed or closed (job state lives in the server
    process, not in a session);
  - a file uploaded before the dashboard has finished loading is simply
    queued -- the worker picks it up, the page shows it as "queued";
  - several uploads run strictly one after another (one worker thread), so
    two jobs never write the same output files at once.

State is process-wide and in memory: if the server restarts, queued jobs are
gone (the uploaded PDFs are on disk, and the Process tab still offers a
Process button for any ledger that has no results yet).

A job is {id, kind, key, label, status ("queued"|"running"|"done"|"failed"),
stage, detail, result, error, submitted_at, started_at, finished_at}.
The caller supplies the work as a callable `fn(report)`, where
`report(stage_text)` updates the live stage line shown in the UI; whatever
`fn` returns becomes `result`, an exception becomes status "failed" with the
message in `error` (a job can never crash the worker thread).
"""
import collections
import itertools
import threading
import time
import traceback

_lock = threading.RLock()
_cv = threading.Condition(_lock)
_pending = collections.deque()          # job dicts, FIFO
_jobs = collections.OrderedDict()       # id -> job (insertion order)
_ids = itertools.count(1)
_worker = None
_completed_seq = 0                      # bumps every time any job finishes
_MAX_KEPT_FINISHED = 40                 # finished jobs kept for display


def _now():
    return time.time()


def _public(job):
    """A plain-dict copy safe to hand to another thread/UI."""
    return {k: v for k, v in job.items() if k != "fn"}


def _prune():
    finished = [j for j in _jobs.values() if j["status"] in ("done", "failed")]
    for j in finished[:-_MAX_KEPT_FINISHED]:
        _jobs.pop(j["id"], None)


def _run_job(job):
    def report(stage_text, detail=None):
        with _lock:
            job["stage"] = stage_text
            if detail is not None:
                job["detail"] = detail

    try:
        result = job["fn"](report)
        with _lock:
            job["result"] = result
            job["status"] = "done"
    except BaseException as e:  # noqa: BLE001 -- the worker must survive anything
        with _lock:
            job["status"] = "failed"
            job["error"] = f"{type(e).__name__}: {e}"
            job["traceback"] = traceback.format_exc()
    finally:
        with _lock:
            job["finished_at"] = _now()
            job["stage"] = job.get("stage")
            global _completed_seq
            _completed_seq += 1
            _prune()


def _worker_loop():
    while True:
        with _cv:
            while not _pending:
                _cv.wait()
            job = _pending.popleft()
            job["status"] = "running"
            job["started_at"] = _now()
        _run_job(job)


def _ensure_worker():
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, name="dashboard-jobs", daemon=True)
            _worker.start()


def submit(kind, key, label, fn, coalesce=False):
    """Queue `fn(report)` as a job. Returns the job id.

    Duplicate protection: if a job with the same (kind, key) is already
    queued or running, that job's id is returned and nothing is added --
    clicking "Process" twice, or a script rerun re-submitting, never runs
    the work twice. With coalesce=True only a QUEUED duplicate counts (a
    running one has already read its inputs, so a new request, e.g. "re-run
    analysis because another document just arrived", must run after it)."""
    with _lock:
        for j in _jobs.values():
            if j["kind"] == kind and j["key"] == key:
                if j["status"] == "queued" or (j["status"] == "running" and not coalesce):
                    return j["id"]
        job_id = next(_ids)
        job = {
            "id": job_id, "kind": kind, "key": key, "label": label,
            "status": "queued", "stage": "Waiting in line", "detail": None,
            "result": None, "error": None,
            "submitted_at": _now(), "started_at": None, "finished_at": None,
            "fn": fn,
        }
        _jobs[job_id] = job
        _pending.append(job)
        _ensure_worker()
        _cv.notify()
        return job_id


def snapshot():
    """{"active": [queued/running jobs, oldest first], "finished": [recent
    done/failed jobs, newest first], "seq": completed-job counter}.
    `seq` changes whenever any job finishes -- the page compares it with the
    value it last saw to know when to refresh its data."""
    with _lock:
        active = [_public(j) for j in _jobs.values() if j["status"] in ("queued", "running")]
        finished = [_public(j) for j in _jobs.values() if j["status"] in ("done", "failed")]
        finished.sort(key=lambda j: j["finished_at"] or 0, reverse=True)
        return {"active": active, "finished": finished, "seq": _completed_seq}


def is_active(kind=None, key=None):
    """True if a matching job is queued or running."""
    with _lock:
        return any(
            j["status"] in ("queued", "running")
            and (kind is None or j["kind"] == kind)
            and (key is None or j["key"] == key)
            for j in _jobs.values()
        )


def get(job_id):
    with _lock:
        j = _jobs.get(job_id)
        return _public(j) if j else None


def wait_idle(timeout=30.0):
    """Block until no job is queued or running (tests / scripts). Returns
    True if the queue drained within `timeout` seconds."""
    deadline = _now() + timeout
    while _now() < deadline:
        if not is_active():
            return True
        time.sleep(0.02)
    return not is_active()


def _reset_for_tests():
    """Forget all jobs. Only valid when nothing is running."""
    global _completed_seq
    with _lock:
        _pending.clear()
        _jobs.clear()
        _completed_seq = 0
