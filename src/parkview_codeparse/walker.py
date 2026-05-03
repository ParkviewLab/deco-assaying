"""Repository walker.

Yields the (relative_path, absolute_path) of every file we want the
analyzer to look at, applying a layered set of skips:

- The walker's own bookkeeping: `.git/` (always) and `.source/` (our clone
  cache, never indexed as if it were source).
- Vendored / generated directories that bloat the index without value:
  `node_modules`, `vendor`, `target`, `dist`, `build`, `.venv`,
  `__pycache__`, `.pytest_cache`, `.ruff_cache`, `.ty_cache`,
  `.next`, `.cache`, `.idea`, `.vscode`.
- The repository's own `.gitignore` (and nested `.gitignore` files,
  pathspec-style) when `respect_gitignore=True`.
- Caller-supplied `extra_ignore_globs` (additional gitignore-style patterns).
- File size: anything over `max_file_bytes` is skipped.
- Binary files: a NUL-byte sniff over the first 8 KB.

Returned paths are relative to `root` and use forward slashes regardless
of platform, so the artifact filenames are stable across hosts.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pathspec

DEFAULT_DIR_SKIPS: frozenset[str] = frozenset(
    {
        ".git",
        ".source",
        "node_modules",
        "vendor",
        "target",
        "dist",
        "build",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".ty_cache",
        ".mypy_cache",
        ".next",
        ".nuxt",
        ".cache",
        ".idea",
        ".vscode",
    }
)

_SNIFF_BYTES = 8192


def walk(
    root: Path,
    *,
    respect_gitignore: bool = True,
    extra_ignore_globs: list[str] | None = None,
    max_file_bytes: int = 2 * 1024 * 1024,
) -> Iterator[tuple[str, Path]]:
    """Yield `(relative_posix_path, absolute_path)` for every analyzable file."""
    root = root.resolve()
    spec_root = _load_gitignore_spec(root) if respect_gitignore else None
    extra_spec = pathspec.GitIgnoreSpec.from_lines(extra_ignore_globs) if extra_ignore_globs else None

    for dirpath, dirnames, filenames in os.walk(root):
        cur = Path(dirpath)
        rel_dir = cur.relative_to(root)

        # Prune directories in place so os.walk doesn't recurse into them.
        dirnames[:] = [d for d in dirnames if not _skip_dir(rel_dir / d, d, spec_root, extra_spec)]

        for fname in filenames:
            rel = (rel_dir / fname).as_posix() if str(rel_dir) != "." else fname
            full = cur / fname
            if _skip_file(rel, full, spec_root, extra_spec, max_file_bytes):
                continue
            yield rel, full


def _load_gitignore_spec(root: Path) -> pathspec.GitIgnoreSpec | None:
    """Load `.gitignore` from the repo root.

    We deliberately only honor the *root* `.gitignore` here. Nested
    `.gitignore` files are rare in repos worth indexing and supporting them
    properly means walking with per-directory specs — out of scope for v1.
    """
    gi = root / ".gitignore"
    if not gi.exists():
        return None
    try:
        with open(gi, encoding="utf-8", errors="replace") as f:
            return pathspec.GitIgnoreSpec.from_lines(f)
    except OSError:
        return None


def _skip_dir(
    rel_path: Path,
    name: str,
    spec_root: pathspec.GitIgnoreSpec | None,
    extra_spec: pathspec.GitIgnoreSpec | None,
) -> bool:
    if name in DEFAULT_DIR_SKIPS:
        return True
    rel = rel_path.as_posix() + "/"
    if spec_root is not None and spec_root.match_file(rel):
        return True
    return bool(extra_spec is not None and extra_spec.match_file(rel))


def _skip_file(
    rel: str,
    full: Path,
    spec_root: pathspec.GitIgnoreSpec | None,
    extra_spec: pathspec.GitIgnoreSpec | None,
    max_file_bytes: int,
) -> bool:
    if spec_root is not None and spec_root.match_file(rel):
        return True
    if extra_spec is not None and extra_spec.match_file(rel):
        return True
    try:
        size = full.stat().st_size
    except OSError:
        return True
    if size > max_file_bytes:
        return True
    return _looks_binary(full)


def _looks_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(_SNIFF_BYTES)
    except OSError:
        return True
    return b"\x00" in head


def summarize_skips(root: Path) -> dict[str, Any]:
    """Diagnostic helper used by tests/admin tooling.

    Counts how many entries in `root` would be skipped by name and how
    many by .gitignore, without materializing the full file list.
    """
    spec_root = _load_gitignore_spec(root)
    by_dir_name = 0
    by_gitignore = 0
    for dirpath, dirnames, filenames in os.walk(root):
        cur = Path(dirpath)
        rel_dir = cur.relative_to(root)
        for d in dirnames:
            if d in DEFAULT_DIR_SKIPS:
                by_dir_name += 1
            elif spec_root is not None and spec_root.match_file((rel_dir / d).as_posix() + "/"):
                by_gitignore += 1
        for f in filenames:
            rel = (rel_dir / f).as_posix() if str(rel_dir) != "." else f
            if spec_root is not None and spec_root.match_file(rel):
                by_gitignore += 1
    return {"skipped_by_name": by_dir_name, "skipped_by_gitignore": by_gitignore}
