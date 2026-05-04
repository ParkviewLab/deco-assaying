"""Source-resolution tests: validation rules, no actual GitHub clone."""

from pathlib import Path

import pytest

from deco_assaying import source

# --- prepare_output_dir (always-managed mode) ---------------------------


def test_prepare_output_dir_creates_under_root(tmp_path: Path):
    out = source.prepare_output_dir(tmp_path / "output", "abc123")
    assert out == (tmp_path / "output" / "abc123").resolve()
    assert out.exists() and out.is_dir()


def test_prepare_output_dir_creates_missing_parent(tmp_path: Path):
    root = tmp_path / "deep" / "missing" / "output"
    out = source.prepare_output_dir(root, "job1")
    assert out.exists()
    assert root.exists()


def test_prepare_output_dir_accepts_pre_existing_empty_dir(tmp_path: Path):
    root = tmp_path / "output"
    target = root / "job1"
    target.mkdir(parents=True)
    out = source.prepare_output_dir(root, "job1")
    assert out == target.resolve()


def test_prepare_output_dir_rejects_non_empty_collision(tmp_path: Path):
    root = tmp_path / "output"
    target = root / "job1"
    target.mkdir(parents=True)
    (target / "leftover.txt").write_text("x")
    with pytest.raises(source.SourceError, match="already exists"):
        source.prepare_output_dir(root, "job1")


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
    for bad in (";", "$(whoami)", "main; rm -rf /", "a b"):
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


def test_is_repo_url_accepts_github_and_gitlab():
    assert source.is_repo_url("https://github.com/octocat/hello-world")
    assert source.is_repo_url("https://gitlab.com/group/sub/repo")
    assert not source.is_repo_url("https://bitbucket.org/foo/bar")


def test_resolve_source_rejects_non_https_url(tmp_path: Path):
    with pytest.raises(source.SourceError, match="unsupported"):
        source.resolve_source(source="git@github.com:foo/bar.git", output_dir=tmp_path)
