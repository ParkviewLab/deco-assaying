"""Source-resolution tests: validation rules, no actual GitHub clone."""

from pathlib import Path

import pytest

from deco_assaying import source

# --- output_dir validation ----------------------------------------------


def test_output_dir_must_be_absolute():
    with pytest.raises(source.SourceError, match="absolute"):
        source.validate_output_dir("relative/path", force=False)


def test_output_dir_rejects_dotdot():
    with pytest.raises(source.SourceError, match=r"\.\."):
        source.validate_output_dir("/tmp/foo/../bar", force=False)


def test_output_dir_rejects_root():
    with pytest.raises(source.SourceError, match="refuses"):
        source.validate_output_dir("/", force=False)


def test_output_dir_rejects_non_empty_without_force(tmp_path: Path):
    (tmp_path / "thing.txt").write_text("x")
    with pytest.raises(source.SourceError, match="not empty"):
        source.validate_output_dir(str(tmp_path), force=False)


def test_output_dir_force_clears_non_empty(tmp_path: Path):
    (tmp_path / "thing.txt").write_text("x")
    out = source.validate_output_dir(str(tmp_path), force=True)
    assert out == tmp_path.resolve()
    assert list(out.iterdir()) == []


def test_output_dir_creates_missing_parents(tmp_path: Path):
    target = tmp_path / "new" / "place"
    out = source.validate_output_dir(str(target), force=False)
    assert out.exists() and out.is_dir()


# --- local source validation --------------------------------------------


def test_local_source_must_exist(tmp_path: Path):
    with pytest.raises(source.SourceError, match="does not exist"):
        source.validate_local_source(str(tmp_path / "no-such-dir"))


def test_local_source_accepts_existing_dir(tmp_path: Path):
    (tmp_path / "x.py").write_text("x = 1")
    p = source.validate_local_source(str(tmp_path))
    assert p == tmp_path.resolve()


# --- git_ref validation -------------------------------------------------


def test_git_ref_blank_is_ok():
    assert source.validate_git_ref("") == ""


def test_git_ref_basic_branch():
    assert source.validate_git_ref("main") == "main"
    assert source.validate_git_ref("release/v1.2.3") == "release/v1.2.3"


def test_git_ref_rejects_shell_metachars():
    for bad in (";", "$(whoami)", "main; rm -rf /", "a b", ""):
        if bad == "":
            continue
        with pytest.raises(source.SourceError):
            source.validate_git_ref(bad)


# --- source URL routing -------------------------------------------------


def test_is_github_url_recognizes_canonical_form():
    assert source.is_github_url("https://github.com/octocat/hello-world")
    assert source.is_github_url("https://github.com/octocat/hello-world.git")


def test_is_github_url_rejects_anything_else():
    for bad in (
        "git@github.com:octocat/hello-world.git",
        "ssh://git@github.com/octocat/hello-world",
        "https://gitlab.com/octocat/hello-world",
        "file:///etc/passwd",
        "/local/path",
    ):
        assert not source.is_github_url(bad)


def test_resolve_source_rejects_non_https_url(tmp_path: Path):
    with pytest.raises(source.SourceError, match="unsupported"):
        source.resolve_source(source="git@github.com:foo/bar.git", output_dir=tmp_path)
