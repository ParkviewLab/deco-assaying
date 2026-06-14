# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Walker tests: gitignore, default-skip directories, size and binary filters."""

from pathlib import Path

from deco_assaying import walker


def _mk(root: Path, rel: str, content: bytes = b"x = 1\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def test_default_dir_skips(tmp_path: Path):
    _mk(tmp_path, "src/keep.py")
    _mk(tmp_path, "node_modules/leftpad/index.js")
    _mk(tmp_path, ".git/HEAD")
    _mk(tmp_path, "__pycache__/cache.pyc")
    _mk(tmp_path, ".source/cloned.py")  # our own clone-cache dir

    rels = sorted(rel for rel, _ in walker.walk(tmp_path))
    assert rels == ["src/keep.py"]


def test_gitignore(tmp_path: Path):
    _mk(tmp_path, "keep.py")
    _mk(tmp_path, "skip.py")
    _mk(tmp_path, "logs/run.log")
    _mk(tmp_path, ".gitignore", b"skip.py\nlogs/\n")

    rels = sorted(rel for rel, _ in walker.walk(tmp_path, respect_gitignore=True))
    # `.gitignore` itself is included unless ignored.
    assert "skip.py" not in rels
    assert "logs/run.log" not in rels
    assert "keep.py" in rels


def test_extra_ignore_globs(tmp_path: Path):
    _mk(tmp_path, "keep.py")
    _mk(tmp_path, "internal/secret.py")
    rels = {rel for rel, _ in walker.walk(tmp_path, extra_ignore_globs=["internal/"])}
    assert "keep.py" in rels
    assert "internal/secret.py" not in rels


def test_size_limit(tmp_path: Path):
    _mk(tmp_path, "small.py", b"x = 1\n")
    _mk(tmp_path, "huge.py", b"x" * 5000)
    rels = {rel for rel, _ in walker.walk(tmp_path, max_file_bytes=1000)}
    assert "small.py" in rels
    assert "huge.py" not in rels


def test_binary_skip(tmp_path: Path):
    _mk(tmp_path, "code.py", b"x = 1\n")
    _mk(tmp_path, "image.bin", b"\x00\x01\x02\x03binary garbage\x00\x00")
    rels = {rel for rel, _ in walker.walk(tmp_path)}
    assert "code.py" in rels
    assert "image.bin" not in rels


def test_relative_paths_use_forward_slash(tmp_path: Path):
    _mk(tmp_path, "a/b/c.py")
    rels = [rel for rel, _ in walker.walk(tmp_path)]
    assert "a/b/c.py" in rels
