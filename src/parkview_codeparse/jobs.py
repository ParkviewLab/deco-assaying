"""In-memory job table + orchestration.

`start_index_repo` is the orchestration entry point and is not yet
implemented (it raises NotImplementedError so MCP callers know the indexer
is unavailable). The surrounding plumbing — status reads, cancellation flag,
log-file tailing, stats, eviction — is implemented and tested by the
`/admin/*` endpoints; once `start_index_repo` lands it will populate this
table and write `log.jsonl` itself.

Security checklist for the implementer (per plan §"Security smells"):

- Validate `output_dir` is absolute, contains no `..` segments, is not a
  symlink, and is not a system path; resolve with strict=False before use.
- Validate `source` URLs match `https://github.com/<owner>/<repo>(.git)?`
  exactly; reject `git@`, `ssh://`, `file://`, and anything with shell
  metacharacters.
- Validate `git_ref` matches `[A-Za-z0-9._/-]+` and is < 250 chars; pass
  after `--` to git invocations.
- Always shell out with `subprocess.run([...], shell=False)`.
- On `force=true`, refuse to remove an `output_dir` that is a symlink; do
  not follow symlinks during cleanup.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from threading import Lock
from typing import Any

from parkview_codeparse.config import JOB_HISTORY_MAX

_lock = Lock()
_jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
_started_at = time.time()

_files_parsed_total = 0
_parse_error_total = 0
_files_by_language: dict[str, int] = {}

_TERMINAL_STATES = frozenset({"done", "failed", "cancelled"})


def start_index_repo(arguments: dict[str, Any]) -> str:
    """Register and start an indexing job.

    Not yet implemented. The skeleton commit wires the MCP tool through to
    here so the server boots and returns a clear error to callers; the next
    commit will add the worker pool, walker, source resolver, and rollups.
    """
    raise NotImplementedError("jobs.start_index_repo is not yet implemented (skeleton commit)")


def _public_view(job: dict[str, Any]) -> dict[str, Any]:
    """Snapshot a job dict, dropping private (underscore-prefixed) fields."""
    return {k: v for k, v in job.items() if not k.startswith("_")}


def get_status(job_id: str) -> dict[str, Any] | None:
    """Return the public view of a job, in the shape promised by the plan.

    Plan §"Tool surface": `get_job_status` returns
    `{ state, progress: {files_done, files_total}, errors_count, output_dir,
      manifest_path }`.
    """
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return {
            "job_id": job["job_id"],
            "source": job["source"],
            "output_dir": job["output_dir"],
            "state": job["status"],
            "progress": {
                "files_done": job["files_done"],
                "files_total": job["files_total"],
            },
            "errors_count": job["errors_count"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "manifest_path": job["manifest_path"],
            "log_path": job["log_path"],
            "error": job["error"],
        }


def cancel(job_id: str) -> bool:
    """Cooperatively cancel a job: flag it; the worker transitions state.

    Per plan, cancellation is cooperative — we set `_cancel=True` and the
    worker loop checks it between files, then writes the terminal status
    itself. This function never flips state directly, so a worker mid-write
    is not raced.
    """
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return False
        if job["status"] in _TERMINAL_STATES:
            return True
        job["_cancel"] = True
        return True


def list_jobs(limit: int = JOB_HISTORY_MAX, status: str | None = None) -> list[dict[str, Any]]:
    """Return up to `limit` job summaries, newest first.

    `status` is matched against the public `state` field.
    """
    limit = max(1, min(limit, JOB_HISTORY_MAX))
    with _lock:
        snapshots = [_public_view(j) for j in reversed(list(_jobs.values()))]
    items = [
        {
            "job_id": s["job_id"],
            "source": s["source"],
            "output_dir": s["output_dir"],
            "state": s["status"],
            "progress": {
                "files_done": s["files_done"],
                "files_total": s["files_total"],
            },
            "errors_count": s["errors_count"],
            "started_at": s["started_at"],
            "finished_at": s["finished_at"],
        }
        for s in snapshots
    ]
    if status:
        items = [j for j in items if j["state"] == status]
    return items[:limit]


def read_log(job_id: str, *, from_offset: int = 0, limit: int = 1000) -> dict[str, Any] | None:
    """Tail `log.jsonl` for a job, returning newline-delimited JSON events.

    Reads raw bytes so the returned `next_offset` is a real byte offset into
    the file (decode/re-encode round-tripping would drift on malformed
    UTF-8). A trailing partial line is left unconsumed so the next poll
    picks it up once the writer flushes.
    """
    limit = max(1, min(limit, 100_000))
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        return None
    log_path = job.get("log_path")
    if not log_path:
        return {"events": [], "next_offset": from_offset}
    try:
        with open(log_path, "rb") as f:
            f.seek(max(0, from_offset))
            data = f.read()
    except FileNotFoundError:
        return {"events": [], "next_offset": from_offset}

    events: list[dict[str, Any]] = []
    consumed = 0
    for line in data.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break  # don't consume a partially-written final line
        consumed += len(line)
        stripped = line.strip()
        if stripped:
            try:
                events.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
        if len(events) >= limit:
            break
    return {"events": events, "next_offset": from_offset + consumed}


def stats() -> dict[str, Any]:
    """Process-level counters since startup."""
    with _lock:
        all_jobs = list(_jobs.values())
    return {
        "jobs_total": len(all_jobs),
        "jobs_done": sum(1 for j in all_jobs if j["status"] == "done"),
        "jobs_failed": sum(1 for j in all_jobs if j["status"] == "failed"),
        "jobs_cancelled": sum(1 for j in all_jobs if j["status"] == "cancelled"),
        "files_parsed_total": _files_parsed_total,
        "parse_error_total": _parse_error_total,
        "files_by_language": dict(_files_by_language),
        "started_at": _started_at,
    }


def _evict_if_full(now_inserting_id: str) -> None:
    """Drop the oldest *terminal* job when the table exceeds the cap.

    Called under `_lock`. Active jobs (pending/running) are pinned so a
    long-running index can't be silently evicted while its worker is still
    writing files.
    """
    cap = max(1, JOB_HISTORY_MAX)
    while len(_jobs) > cap:
        for jid, job in _jobs.items():
            if jid != now_inserting_id and job["status"] in _TERMINAL_STATES:
                del _jobs[jid]
                break
        else:  # nothing terminal to evict — keep all active jobs
            return
