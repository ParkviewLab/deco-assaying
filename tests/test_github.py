"""Unit tests for the GitHub Trees API helper.

We mock `urllib.request.urlopen` so the tests don't hit github.com.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from parkview_codeparse import github


def _mock_response(payload: dict | list):
    body = json.dumps(payload).encode()

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return body

    return _Ctx()


def test_parse_github_url():
    assert github.parse_github_url("https://github.com/octocat/hello-world") == ("octocat", "hello-world")
    assert github.parse_github_url("https://github.com/octocat/hello-world.git") == ("octocat", "hello-world")
    assert github.parse_github_url("git@github.com:octocat/hello-world.git") is None
    assert github.parse_github_url("https://gitlab.com/foo/bar") is None
    assert github.parse_github_url("/local/path") is None


def test_fetch_blob_sizes_with_explicit_ref():
    tree_response = {
        "sha": "abc",
        "tree": [
            {"path": "README.md", "type": "blob", "size": 1024},
            {"path": "src/main.py", "type": "blob", "size": 2048},
            {"path": "src", "type": "tree"},  # ignored
            {"path": "image.png", "type": "blob", "size": 50000},
        ],
        "truncated": False,
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(tree_response)):
        sizes = github.fetch_blob_sizes("octocat", "hello", git_ref="main")
    assert sizes == {"README.md": 1024, "src/main.py": 2048, "image.png": 50000}


def test_fetch_blob_sizes_resolves_default_branch():
    repo_info = {"default_branch": "trunk"}
    tree_response = {"sha": "abc", "tree": [{"path": "x.py", "type": "blob", "size": 10}], "truncated": False}
    responses = [_mock_response(repo_info), _mock_response(tree_response)]
    with patch("urllib.request.urlopen", side_effect=responses):
        sizes = github.fetch_blob_sizes("octocat", "hello")
    assert sizes == {"x.py": 10}


def test_fetch_blob_sizes_returns_none_on_truncated():
    tree_response = {
        "sha": "abc",
        "tree": [{"path": "a", "type": "blob", "size": 1}],
        "truncated": True,
    }
    with patch("urllib.request.urlopen", return_value=_mock_response(tree_response)):
        sizes = github.fetch_blob_sizes("octocat", "hello", git_ref="main")
    assert sizes is None


def test_fetch_blob_sizes_returns_none_on_network_error():
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("nope")):
        sizes = github.fetch_blob_sizes("octocat", "hello", git_ref="main")
    assert sizes is None


def test_fetch_blob_sizes_returns_none_on_timeout():
    with patch("urllib.request.urlopen", side_effect=TimeoutError):
        sizes = github.fetch_blob_sizes("octocat", "hello", git_ref="main")
    assert sizes is None


def test_fetch_blob_sizes_authenticated_sets_authorization_header():
    tree_response = {"sha": "abc", "tree": [], "truncated": False}
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.headers)
        return _mock_response(tree_response)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        github.fetch_blob_sizes("octocat", "hello", git_ref="main", token="ghp_test123")
    # urllib normalizes header keys to title-case.
    assert captured["headers"].get("Authorization") == "Bearer ghp_test123"


@pytest.mark.network
def test_fetch_blob_sizes_against_real_github():
    """Real network call: fetch sizes for this project's own public repo."""
    sizes = github.fetch_blob_sizes("garycoding", "parkview-codeparse-server")
    if sizes is None:
        pytest.skip("github API unavailable (rate limit or network)")
    assert "pyproject.toml" in sizes
    assert sizes["pyproject.toml"] > 0
    assert all(v >= 0 for v in sizes.values())
