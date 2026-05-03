"""Indexing-job orchestration.

Public entry point: `start_index_repo(arguments)` returns a job_id and
spawns a background thread that drives the work. The thread:

1. Validates `output_dir` and `source` (security boundary lives in
   `parkview_codeparse.source`).
2. Resolves the source — for a GitHub URL, shallow-clones into
   `output_dir/.source/`; for a local path, validates and uses it in
   place.
3. Walks the tree (`parkview_codeparse.walker`), respecting `.gitignore`
   plus our hard-coded skip list and a binary/size sniff.
4. Submits per-file analysis to a `ProcessPoolExecutor`. Each worker
   parses with tree-sitter and runs the language-specific analyzer; the
   result is the per-file JSON shape documented in the plan.
5. As completions arrive, atomically writes
   `output_dir/files/<rel>.json`, appends an event to
   `output_dir/log.jsonl`, and updates the live job entry's counters.
6. On finish: builds the rollups (`manifest.json`, `symbols.json`,
   `languages.json`, `errors.json`) and flips status to `done`.

Cancellation is cooperative: `cancel(job_id)` sets `_cancel=True`. The
orchestrator stops submitting new files between completions; the workers
already in flight finish naturally. The terminal status (`cancelled`) is
written by the orchestrator, never by the cancel-call itself, so a worker
mid-write is never raced.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from parkview_codeparse import analyze, manifest, source, walker
from parkview_codeparse.config import JOB_HISTORY_MAX

log = logging.getLogger(__name__)

_lock = threading.Lock()
_jobs: OrderedDict[str, dict[str, Any]] = OrderedDict()
_started_at = time.time()

_files_parsed_total = 0
_parse_error_total = 0
_files_by_language: dict[str, int] = {}

_TERMINAL_STATES = frozenset({"done", "failed", "cancelled"})


# ---------------------------------------------------------------------------
# Public entry points (called by routes.py)


def start_index_repo(arguments: dict[str, Any]) -> str:
    """Register and start an indexing job; return its id immediately."""
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    job: dict[str, Any] = {
        "job_id": job_id,
        "source": arguments["source"],
        "output_dir": arguments["output_dir"],
        "git_ref": arguments.get("git_ref") or "",
        "options": _options_from_args(arguments),
        "status": "pending",
        "files_done": 0,
        "files_total": 0,
        "errors_count": 0,
        "started_at": now,
        "finished_at": None,
        "manifest_path": None,
        "log_path": None,
        "error": None,
        "_cancel": False,
    }
    with _lock:
        _jobs[job_id] = job
        _evict_if_full(now_inserting_id=job_id)

    thread = threading.Thread(
        target=_run_job,
        args=(job_id,),
        name=f"index-job-{job_id}",
        daemon=True,
    )
    thread.start()
    return job_id


def get_status(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return _public_view(job)


def cancel(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return False
        if job["status"] in _TERMINAL_STATES:
            return True
        job["_cancel"] = True
        return True


def list_jobs(limit: int = JOB_HISTORY_MAX, status: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(limit, JOB_HISTORY_MAX))
    with _lock:
        snapshots = [_public_view(j) for j in reversed(list(_jobs.values()))]
    if status:
        snapshots = [j for j in snapshots if j["state"] == status]
    return [
        {k: v for k, v in s.items() if k not in ("manifest_path", "log_path", "error")}
        for s in snapshots[:limit]
    ]


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
            break
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
    with _lock:
        all_jobs = list(_jobs.values())
        files_parsed = _files_parsed_total
        parse_errors = _parse_error_total
        by_lang = dict(_files_by_language)
    return {
        "jobs_total": len(all_jobs),
        "jobs_done": sum(1 for j in all_jobs if j["status"] == "done"),
        "jobs_failed": sum(1 for j in all_jobs if j["status"] == "failed"),
        "jobs_cancelled": sum(1 for j in all_jobs if j["status"] == "cancelled"),
        "files_parsed_total": files_parsed,
        "parse_error_total": parse_errors,
        "files_by_language": by_lang,
        "started_at": _started_at,
    }


# ---------------------------------------------------------------------------
# Internals


def _options_from_args(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "force": bool(arguments.get("force", False)),
        "respect_gitignore": bool(arguments.get("respect_gitignore", True)),
        "extra_ignore_globs": list(arguments.get("extra_ignore_globs") or []),
        "max_file_bytes": int(arguments.get("max_file_bytes", 2 * 1024 * 1024)),
        "include_chunks": bool(arguments.get("include_chunks", True)),
        "chunk_max_tokens": int(arguments.get("chunk_max_tokens", 800)),
    }


def _public_view(job: dict[str, Any]) -> dict[str, Any]:
    """Plan-shape view of a job (state + nested progress)."""
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


def _set_status(job_id: str, status: str, *, error: str | None = None) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job["status"] = status
        if status in _TERMINAL_STATES:
            job["finished_at"] = time.time()
        if error is not None:
            job["error"] = error


def _is_cancelled(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job["_cancel"])


def _evict_if_full(*, now_inserting_id: str) -> None:
    """Drop the oldest *terminal* job; pin active jobs."""
    cap = max(1, JOB_HISTORY_MAX)
    while len(_jobs) > cap:
        for jid, job in _jobs.items():
            if jid != now_inserting_id and job["status"] in _TERMINAL_STATES:
                del _jobs[jid]
                break
        else:
            return


def _run_job(job_id: str) -> None:
    """Background-thread entry point. Owns the executor + log writer."""
    try:
        with _lock:
            job = _jobs[job_id]
            options = job["options"]
            src_arg = job["source"]
            output_dir_arg = job["output_dir"]
            git_ref = job["git_ref"]

        output_dir = source.validate_output_dir(output_dir_arg, force=options["force"])
        files_dir = output_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / "log.jsonl"

        with _lock:
            _jobs[job_id]["log_path"] = str(log_path)

        with open(log_path, "a", encoding="utf-8") as log_fh:
            _set_status(job_id, "running")
            root = source.resolve_source(
                source=src_arg,
                output_dir=output_dir,
                git_ref=git_ref,
            )
            _emit(log_fh, {"event": "source_resolved", "root": str(root)})

            files = list(
                walker.walk(
                    root,
                    respect_gitignore=options["respect_gitignore"],
                    extra_ignore_globs=options["extra_ignore_globs"],
                    max_file_bytes=options["max_file_bytes"],
                )
            )
            with _lock:
                _jobs[job_id]["files_total"] = len(files)
            _emit(log_fh, {"event": "walk_done", "file_count": len(files)})

            file_summaries = _process_files(
                job_id=job_id,
                root=root,
                files=files,
                files_dir=files_dir,
                log_fh=log_fh,
                options=options,
            )

            elapsed = time.time() - _jobs[job_id]["started_at"]

            if _is_cancelled(job_id):
                _emit(log_fh, {"event": "cancelled"})
                _set_status(job_id, "cancelled")
                return

            with _lock:
                job_snapshot = dict(_jobs[job_id])
            job_snapshot["finished_at"] = time.time()
            manifest.write(
                output_dir=output_dir,
                job=job_snapshot,
                file_summaries=file_summaries,
                elapsed_seconds=elapsed,
            )
            with _lock:
                _jobs[job_id]["manifest_path"] = str(output_dir / "manifest.json")

            _emit(log_fh, {"event": "manifest_written", "elapsed_seconds": elapsed})
            _set_status(job_id, "done")

    except source.SourceError as e:
        log.warning("job %s rejected: %s", job_id, e)
        _set_status(job_id, "failed", error=str(e))
    except Exception as e:
        log.exception("job %s failed: %s", job_id, e)
        _set_status(job_id, "failed", error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


def _process_files(
    *,
    job_id: str,
    root: Path,
    files: list[tuple[str, Path]],
    files_dir: Path,
    log_fh: Any,
    options: dict[str, Any],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    if not files:
        return summaries

    with ProcessPoolExecutor() as pool:
        futures = {}
        for rel, full in files:
            if _is_cancelled(job_id):
                break
            fut = pool.submit(
                _worker,
                rel,
                str(full),
                options["include_chunks"],
                options["chunk_max_tokens"],
            )
            futures[fut] = rel

        for fut in as_completed(futures):
            rel = futures[fut]
            try:
                rel_path, result = fut.result()
            except Exception as e:
                _emit(log_fh, {"event": "file_failed", "path": rel, "error": str(e)})
                with _lock:
                    j = _jobs[job_id]
                    j["errors_count"] += 1
                    j["files_done"] += 1
                continue
            artifact_path = files_dir / (rel_path + ".json")
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            _write_artifact(artifact_path, result)
            summaries.append(_summarize_for_manifest(rel_path, result))

            global _files_parsed_total, _parse_error_total
            with _lock:
                j = _jobs[job_id]
                j["files_done"] += 1
                if not result["parse"]["ok"]:
                    j["errors_count"] += 1
                _files_parsed_total += 1
                if not result["parse"]["ok"]:
                    _parse_error_total += 1
                lang = result["file"]["language"] or "unknown"
                _files_by_language[lang] = _files_by_language.get(lang, 0) + 1
            _emit(
                log_fh,
                {
                    "event": "file_done",
                    "path": rel_path,
                    "language": result["file"]["language"],
                    "n_symbols": len(result["symbols"]),
                    "parse_ok": result["parse"]["ok"],
                    "bytes": result["file"]["bytes"],
                },
            )
    return summaries


def _worker(
    rel_path: str,
    abs_path: str,
    include_chunks: bool,
    chunk_max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    """ProcessPoolExecutor worker: read file, analyze, return."""
    with open(abs_path, "rb") as f:
        content_bytes = f.read()
    text = content_bytes.decode("utf-8", errors="replace")
    result = analyze.analyze_inline(
        content=text,
        filename=rel_path,
        include_chunks=include_chunks,
        chunk_max_tokens=chunk_max_tokens,
    )
    return rel_path, result


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def _summarize_for_manifest(rel: str, result: dict[str, Any]) -> dict[str, Any]:
    f = result["file"]
    return {
        "path": rel,
        "language": f["language"],
        "bytes": f["bytes"],
        "loc": f["loc"],
        "is_test": f["is_test"],
        "is_generated": f["is_generated"],
        "is_config": f["is_config"],
        "has_main_guard": result["metrics"]["has_main_guard"],
        "parse_ok": result["parse"]["ok"],
        "error_nodes": result["parse"]["error_nodes"],
        "missing_nodes": result["parse"]["missing_nodes"],
        "parse_reason": result["parse"].get("reason", ""),
    }


def _emit(fh: Any, event: dict[str, Any]) -> None:
    """Append a single event line to log.jsonl with a wall-clock timestamp."""
    event = dict(event)
    event.setdefault("ts", time.time())
    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    fh.flush()
