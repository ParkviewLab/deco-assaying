"""End-to-end test: drive `jobs.start_index_repo` against a tiny local repo
and verify the output directory layout matches the plan."""

from __future__ import annotations

import json
import time
from pathlib import Path

from deco_assaying import jobs


def _wait_done(job_id: str, timeout: float = 30.0) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        snap = jobs.get_status(job_id)
        assert snap is not None
        if snap["state"] in ("done", "failed", "cancelled"):
            return snap
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_index_repo_against_local_fixture(tmp_path: Path):
    src = tmp_path / "src"
    out = tmp_path / "out"

    _write(src / "alpha.py", '"""alpha doc."""\n\ndef hello(name): return f"hi {name}"\n')
    _write(src / "pkg" / "beta.py", "class Beta:\n    def m(self): return 1\n")
    _write(src / "main.go", 'package main\n\nimport "fmt"\n\nfunc main() {\n    fmt.Println("hi")\n}\n')
    _write(src / "ts" / "util.ts", 'export const greeting = "hi";\n')
    _write(src / "tests" / "test_alpha.py", "def test_hi(): assert True\n")
    _write(src / "README.md", "# Demo\n")
    _write(src / ".gitignore", "ignored.py\n")
    _write(src / "ignored.py", "# should be skipped\n")
    _write(src / "node_modules" / "leftpad" / "index.js", "export default x => x;\n")

    job_id = jobs.start_index_repo(
        {
            "source": str(src),
            "output_dir": str(out),
        }
    )
    snap = _wait_done(job_id)
    assert snap["state"] == "done", f"job failed: {snap}"

    # Manifest exists and has the rollup we expect.
    manifest_path = Path(snap["manifest_path"])
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["file_count"] >= 5  # 5 source files + README + .gitignore
    assert "python" in manifest["languages"]
    assert "go" in manifest["languages"]
    assert "typescript" in manifest["languages"]
    assert "main.go" in manifest["entry_points"]
    assert manifest["test_file_count"] >= 1

    # Per-file artifacts mirror the source tree.
    files_dir = out / "files"
    assert (files_dir / "alpha.py.json").exists()
    assert (files_dir / "pkg" / "beta.py.json").exists()
    assert (files_dir / "main.go.json").exists()
    # gitignore filtered, node_modules pruned, .git absent.
    assert not (files_dir / "ignored.py.json").exists()
    assert not (files_dir / "node_modules").exists()

    # Per-file artifact has the documented shape.
    alpha = json.loads((files_dir / "alpha.py.json").read_text())
    assert alpha["file"]["language"] == "python"
    assert alpha["module_doc"] == "alpha doc."
    assert any(s["qualified_name"] == "hello" for s in alpha["symbols"])

    # symbols.json and languages.json wrote.
    symbols = json.loads((out / "symbols.json").read_text())
    qnames = {e["qualified_name"] for e in symbols["entries"]}
    assert "hello" in qnames
    assert any(q.startswith("Beta") for q in qnames)

    languages = json.loads((out / "languages.json").read_text())
    assert "python" in languages["languages"]

    # log.jsonl contains a stream of events.
    log_lines = [json.loads(ln) for ln in (out / "log.jsonl").read_text().splitlines() if ln.strip()]
    events = {ev["event"] for ev in log_lines}
    assert "walk_done" in events
    assert "file_done" in events
    assert "manifest_written" in events

    # tree.json lists every path the walker saw — analyzed and skipped.
    tree = json.loads((out / "tree.json").read_text())
    by_path = {e["path"]: e for e in tree["entries"]}
    assert by_path["alpha.py"]["analyzed"] is True
    # `ignored.py` is matched by .gitignore and recorded as skipped.
    assert "ignored.py" in by_path
    assert by_path["ignored.py"]["analyzed"] is False
    assert by_path["ignored.py"]["skip_reason"] == "gitignore"
    # node_modules entries don't show up — directory-level skip drops them
    # before we record per-file decisions, so tree.json stays compact.
    assert not any(p.startswith("node_modules/") for p in by_path)
    # Manifest exposes the rolled-up skip counts.
    assert manifest["tree_total"] == len(by_path)
    assert manifest["skipped_count"] >= 1
    assert manifest["skipped_by_reason"].get("gitignore", 0) >= 1


def test_index_repo_refuses_non_empty_output_without_force(tmp_path: Path):
    src = tmp_path / "src"
    out = tmp_path / "out"
    _write(src / "x.py", "x = 1\n")
    out.mkdir()
    (out / "preexisting.txt").write_text("don't touch me\n")

    job_id = jobs.start_index_repo({"source": str(src), "output_dir": str(out)})
    snap = _wait_done(job_id)
    assert snap["state"] == "failed"
    assert snap["error"] is not None
    assert "not empty" in snap["error"]


def test_index_repo_force_overwrites(tmp_path: Path):
    src = tmp_path / "src"
    out = tmp_path / "out"
    _write(src / "x.py", "x = 1\n")
    out.mkdir()
    (out / "preexisting.txt").write_text("don't touch me\n")

    job_id = jobs.start_index_repo(
        {
            "source": str(src),
            "output_dir": str(out),
            "force": True,
        }
    )
    snap = _wait_done(job_id)
    assert snap["state"] == "done", f"failed: {snap}"
    assert not (out / "preexisting.txt").exists()  # wiped by force
    assert (out / "manifest.json").exists()


def test_index_repo_rejects_relative_output_dir(tmp_path: Path):
    src = tmp_path / "src"
    _write(src / "x.py", "x = 1\n")
    job_id = jobs.start_index_repo({"source": str(src), "output_dir": "relative/path"})
    snap = _wait_done(job_id)
    assert snap["state"] == "failed"
    assert "absolute" in snap["error"]


def test_index_repo_rejects_unsafe_url(tmp_path: Path):
    out = tmp_path / "out"
    job_id = jobs.start_index_repo(
        {
            "source": "git@github.com:foo/bar.git",
            "output_dir": str(out),
        }
    )
    snap = _wait_done(job_id)
    assert snap["state"] == "failed"
    assert "unsupported" in snap["error"].lower() or "scheme" in snap["error"].lower()
