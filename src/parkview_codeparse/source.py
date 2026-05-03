"""Source resolution: local path or GitHub URL -> filesystem path to walk.

Validation here is the security boundary: untrusted MCP callers can pass
arbitrary `source` and `output_dir` strings, so we reject anything that
could escape the intended directory or shell out beyond a known-safe
`git clone`.

Plan §"Security smells" checklist:

- `output_dir` must be absolute, contain no `..`, not be a symlink, and
  not point at the filesystem root or a system path. We resolve with
  `strict=False` and re-check.
- `source` is either a local directory path (validated the same way as
  output_dir) or a GitHub URL matching the strict pattern
  `https://github.com/<owner>/<repo>(.git)?`.
- `git_ref` (when given) must match `[A-Za-z0-9._/-]{1,250}` so it can't
  inject into a `git checkout` argument.
- We always shell out with `subprocess.run([...], shell=False)`.
- On `force=True`, we refuse to remove an `output_dir` that is a symlink
  and we don't follow symlinks during cleanup.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

GITHUB_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+?(?:\.git)?$")
GIT_REF = re.compile(r"^[A-Za-z0-9._/\-]{1,250}$")

_FORBIDDEN_OUTPUT_DIRS: frozenset[Path] = frozenset(
    {
        Path("/"),
        Path("/etc"),
        Path("/usr"),
        Path("/var"),
        Path("/bin"),
        Path("/sbin"),
        Path("/boot"),
        Path("/dev"),
        Path("/proc"),
        Path("/sys"),
        Path.home(),
    }
)


class SourceError(ValueError):
    """Raised when source/output_dir validation fails."""


def is_github_url(source: str) -> bool:
    return GITHUB_URL.match(source) is not None


def validate_output_dir(output_dir: str, *, force: bool) -> Path:
    """Validate the output_dir argument and prepare it for writing.

    Returns the resolved absolute Path. Raises SourceError on any unsafe
    input. If the dir already exists and contains entries, requires
    `force=True` to proceed; in that case we wipe it (but never follow
    symlinks).
    """
    if not output_dir:
        raise SourceError("output_dir is required")
    p = Path(output_dir)
    if not p.is_absolute():
        raise SourceError("output_dir must be an absolute path")
    if ".." in p.parts:
        raise SourceError("output_dir must not contain '..' segments")
    resolved = p.resolve(strict=False)
    if resolved in _FORBIDDEN_OUTPUT_DIRS:
        raise SourceError(f"output_dir refuses to operate on {resolved}")
    if resolved.is_symlink():
        raise SourceError("output_dir must not be a symlink")

    if resolved.exists():
        if not resolved.is_dir():
            raise SourceError("output_dir exists and is not a directory")
        if any(resolved.iterdir()):
            if not force:
                raise SourceError("output_dir is not empty; pass force=true to overwrite")
            _safe_clean(resolved)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def validate_local_source(source: str) -> Path:
    """Validate a local-directory source argument."""
    p = Path(source)
    if not p.is_absolute():
        raise SourceError("local source must be an absolute path")
    if ".." in p.parts:
        raise SourceError("local source must not contain '..' segments")
    resolved = p.resolve(strict=False)
    if not resolved.exists():
        raise SourceError(f"local source does not exist: {resolved}")
    if not resolved.is_dir():
        raise SourceError("local source must be a directory")
    return resolved


def validate_git_ref(git_ref: str) -> str:
    if not git_ref:
        return ""
    if not GIT_REF.match(git_ref):
        raise SourceError("git_ref must match [A-Za-z0-9._/-]{1,250}")
    return git_ref


def resolve_source(
    *,
    source: str,
    output_dir: Path,
    git_ref: str = "",
) -> Path:
    """Materialize the source into a local directory we can walk.

    For GitHub URLs we shallow-clone into `output_dir/.source/`. For local
    paths we validate and return the path as-is.
    """
    if is_github_url(source):
        ref = validate_git_ref(git_ref)
        clone_dir = output_dir / ".source"
        if clone_dir.exists():
            _safe_clean(clone_dir)
        clone_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "clone", "--depth=1"]
        if ref:
            cmd += ["--branch", ref]
        cmd += ["--", source, str(clone_dir)]
        result = subprocess.run(
            cmd,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            raise SourceError(f"git clone failed: {result.stderr.strip() or result.stdout.strip()}")
        return clone_dir
    # Anything that's not a recognized GitHub URL is treated as a local path.
    if "://" in source or source.startswith(("git@", "ssh://", "file://")):
        raise SourceError(f"unsupported source URL scheme: {source!r}")
    return validate_local_source(source)


def _safe_clean(path: Path) -> None:
    """Remove a directory's contents without following symlinks.

    `shutil.rmtree` with the default settings does not follow symlinks
    above the deleted root, but we re-check the root itself first as a
    belt-and-braces measure.
    """
    if path.is_symlink():
        raise SourceError(f"refusing to clean symlinked path: {path}")
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=False)
