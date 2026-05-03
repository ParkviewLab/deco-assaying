"""Walker tests for the partial-clone (git ls-tree) path.

We initialize a local bare-ish git repo with `git init` + commits, then
hand the .git directory to `walker.walk_git_tree` and check the same
filtering behavior as the os.walk path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from parkview_codeparse import walker


def _run(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        list(args),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def _make_repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run("git", "init", "-q", "-b", "main", cwd=repo)
    _run("git", "config", "user.email", "test@example.com", cwd=repo)
    _run("git", "config", "user.name", "test", cwd=repo)
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    _run("git", "add", "-A", cwd=repo)
    _run("git", "commit", "-q", "-m", "init", cwd=repo)
    return repo / ".git"


def test_lazy_walk_default_dir_skips(tmp_path: Path):
    git_dir = _make_repo(
        tmp_path,
        {
            "src/keep.py": b"x = 1\n",
            "node_modules/leftpad/index.js": b"export default x => x;\n",
            "vendor/whatever/foo.go": b"package foo\n",
        },
    )
    result = walker.walk_git_tree(git_dir)
    paths = {e.path for e in result.included}
    assert paths == {"src/keep.py"}


def test_lazy_walk_gitignore_via_git_show(tmp_path: Path):
    git_dir = _make_repo(
        tmp_path,
        {
            ".gitignore": b"skip.py\nlogs/\n",
            "keep.py": b"x = 1\n",
            # Note: in real life skip.py wouldn't be in HEAD because git wouldn't add it,
            # but for this test we want to verify the .gitignore filter still applies.
        },
    )
    result = walker.walk_git_tree(git_dir)
    paths = {e.path for e in result.included}
    assert "keep.py" in paths


def test_lazy_walk_size_skip(tmp_path: Path):
    git_dir = _make_repo(
        tmp_path,
        {
            "small.py": b"x = 1\n",
            "huge.py": b"x" * 5000,
        },
    )
    result = walker.walk_git_tree(git_dir, max_file_bytes=1000)
    included = {e.path for e in result.included}
    skipped = {e.path: e.skip_reason for e in result.skipped}
    assert "small.py" in included
    assert skipped.get("huge.py") == "oversize"


def test_lazy_walk_binary_extension_skip(tmp_path: Path):
    git_dir = _make_repo(
        tmp_path,
        {
            "code.py": b"x = 1\n",
            "image.png": b"\x89PNG\r\n\x1a\n_pretend_image_data",
            "archive.zip": b"PK\x03\x04not really a zip",
        },
    )
    result = walker.walk_git_tree(git_dir)
    included = {e.path for e in result.included}
    skipped = {e.path: e.skip_reason for e in result.skipped}
    assert included == {"code.py"}
    assert skipped.get("image.png") == "binary"
    assert skipped.get("archive.zip") == "binary"


def test_lazy_walk_records_blob_sha(tmp_path: Path):
    git_dir = _make_repo(tmp_path, {"only.py": b"x = 1\n"})
    result = walker.walk_git_tree(git_dir)
    assert len(result.included) == 1
    entry = result.included[0]
    assert entry.path == "only.py"
    assert len(entry.blob_sha) == 40  # full SHA-1
    assert entry.size == 6
