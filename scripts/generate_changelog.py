#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Generate the CHANGELOG.md section + release-body.md for the current tag.

Two-phase design so the LLM call stays a single round-trip even though
the workflow may need to commit onto a moved `main`:

    generate  — read git history at the current checkout, produce
                release-body.md (which is the new section). Calls the
                Anthropic API for the "Highlights" paragraph.
    insert    — prepend release-body.md into CHANGELOG.md, inserting
                above the [Unreleased] marker. No network, no LLM.

The release workflow runs `generate` against the tagged commit, then
switches to fresh `origin/main` and runs `insert` there before committing
CHANGELOG.md back. This decouples narrative-generation (one LLM call) from
the eventual commit destination, so a moved `main` doesn't trigger
re-generation.

Inputs (env):
  GITHUB_REF        — refs/tags/vX.Y.Z (or pass --tag).
  ANTHROPIC_API_KEY — Anthropic key for the Highlights call. If missing,
                      a placeholder paragraph is written and the release
                      still ships.

Exit codes:
  0  — success (including the API-failure fallback path).
  2  — bad args, no tag, or git-cliff not on PATH.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

# Files live at the repo root regardless of where this script is invoked
# from — resolve relative to script location (../ from scripts/).
ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
RELEASE_BODY = ROOT / "release-body.md"
PYPROJECT = ROOT / "pyproject.toml"
CLIFF_TOML = ROOT / "cliff.toml"

# Cap the commit-log slice we feed the LLM. 50k chars is roughly 12-15k
# tokens which keeps a single release call under a dollar even on Opus.
# Releases that exceed this aren't realistic for these repos; if one does,
# we truncate (with a marker) rather than fail.
MAX_LOG_CHARS = 50_000

# Claude model used for highlight generation. Pinned here so behaviour is
# stable across repos that mirror this script. Bump deliberately.
HIGHLIGHTS_MODEL = "claude-opus-4-7"

# Output cap. Three tight sentences fit comfortably in ~300 tokens; the
# extra headroom is for first-release summaries that span more ground
# (initial release of an entire project). Past 600 the LLM tends to
# wander into bullet lists despite the prompt.
HIGHLIGHTS_MAX_TOKENS = 600


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=ROOT).stdout


def previous_tag(current_tag: str) -> str | None:
    """Tag immediately preceding `current_tag`, or None for the first release."""
    try:
        out = run(["git", "describe", "--tags", "--abbrev=0", f"{current_tag}^"]).strip()
        return out or None
    except subprocess.CalledProcessError:
        return None


def categorized_section(tag: str, prev: str | None) -> str:
    """git-cliff output for this tag (categorized commit groups only — no
    H2 header, no top/bottom prose).

    Uses an explicit `prev..tag` range (or `..tag` for the first release)
    so this works for both freshly-pushed tags (go-forward releases) and
    historical tags (retroactive backfill). No `--tag` flag — the range
    is unambiguous, and `--tag` triggers a no-op WARN when the tag
    already exists in the repo (which it does, by definition, for retro)."""
    rng = f"{prev}..{tag}" if prev else f"..{tag}"
    return run(["git-cliff", "--config", str(CLIFF_TOML), rng]).strip()


def project_meta() -> tuple[str, str]:
    data = tomllib.loads(PYPROJECT.read_text())
    return data["project"]["name"], data["project"].get("description", "")


def commit_log(tag: str, prev: str | None) -> str:
    """Compact log of changes in this release — fuel for the LLM. Uses
    explicit `prev..tag` so retroactive runs don't accidentally pick up
    commits past the historical tag."""
    rng = f"{prev}..{tag}" if prev else tag
    log = run(["git", "log", rng, "--pretty=format:%s%n%b%n---"])
    if len(log) > MAX_LOG_CHARS:
        log = log[:MAX_LOG_CHARS] + "\n\n[... log truncated; release spans more than typical ...]"
    return log


def generate_highlights(tag: str, prev: str | None, log: str, name: str, desc: str) -> str:
    """Call Anthropic for the 2-3 sentence Highlights paragraph.

    Returns a marked placeholder string if the call fails or the API key
    is missing — release still ships, with a TODO for the maintainer."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return "_Highlights generation skipped: `ANTHROPIC_API_KEY` not set in release environment._"

    try:
        import anthropic
    except ImportError as e:
        return f"_Highlights generation failed: anthropic SDK not importable ({e})._"

    is_first = prev is None
    framing = (
        f"This is the FIRST tagged release of {name}."
        if is_first
        else f"This release ({tag}) follows {prev}."
    )
    prompt = (
        f"You are writing the 'Highlights' paragraph for the CHANGELOG entry of {name} {tag}.\n\n"
        f"Project: {name} — {desc}\n"
        f"{framing}\n\n"
        f"The commit log for this release is below. Write a TIGHT prose paragraph summarising "
        f"what is most USER-VISIBLE in this release. Constraints:\n"
        f"- Two or three sentences. Hard maximum: four sentences.\n"
        f"- Prose only. No bullet points. No headings. No code fences.\n"
        f"- Do not restate the version number in the prose.\n"
        f"- Do not invent features that aren't in the log.\n"
        f"- If the release is purely internal/maintenance, say so honestly in one sentence.\n"
        f"- Plain factual tone. No marketing language ('exciting', 'powerful', 'seamless', etc).\n"
        f"- For an initial release summarising a lot of foundational work, prefer compressing "
        f"into one dense sentence over running long — better to undersell than to truncate.\n\n"
        f"=== COMMIT LOG ===\n{log}\n=== END LOG ===\n\n"
        f"Output ONLY the paragraph, nothing else."
    )

    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model=HIGHLIGHTS_MODEL,
            max_tokens=HIGHLIGHTS_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        return text or "_Highlights generation returned empty text._"
    except Exception as e:  # noqa: BLE001 — release must ship even if the LLM is down
        print(f"warning: highlights generation failed: {e}", file=sys.stderr)
        return f"_Highlights generation failed at release time: {e}. See categorized changes below._"


def assemble(tag: str, date: str, highlights: str, categorized: str) -> str:
    """Compose the full section that goes into both CHANGELOG.md and release-body.md."""
    return (
        f"## [{tag}] - {date}\n\n"
        f"### Highlights\n\n"
        f"{highlights.strip()}\n\n"
        f"{categorized}\n"
    )


def do_generate(tag: str) -> int:
    if not shutil.which("git-cliff"):
        print("error: git-cliff not on PATH", file=sys.stderr)
        return 2
    if not CLIFF_TOML.exists():
        print(f"error: {CLIFF_TOML} not found", file=sys.stderr)
        return 2

    name, desc = project_meta()
    prev = previous_tag(tag)
    log = commit_log(tag, prev)
    categorized = categorized_section(tag, prev)
    highlights = generate_highlights(tag, prev, log, name, desc)
    # Use the tagged commit's date so the section reflects when the
    # release was actually cut, not when the workflow ran.
    date = run(["git", "log", "-1", "--pretty=format:%cs", tag]).strip()
    section = assemble(tag, date, highlights, categorized)
    RELEASE_BODY.write_text(section)
    print(f"generate: wrote {RELEASE_BODY.name} for {tag} (prev={prev or 'none'})")
    return 0


def do_insert() -> int:
    if not RELEASE_BODY.exists():
        print(f"error: {RELEASE_BODY} not found — run --mode=generate first", file=sys.stderr)
        return 2
    section = RELEASE_BODY.read_text().rstrip() + "\n"

    if CHANGELOG.exists():
        text = CHANGELOG.read_text()
    else:
        text = (
            "# Changelog\n\n"
            "All notable changes to this project are recorded here.\n\n"
            "## [Unreleased]\n\n"
        )

    # Insert the new section above the [Unreleased] marker if present,
    # otherwise immediately after the H1 header, otherwise at the very
    # top. (This is robust across freshly-bootstrapped CHANGELOG.md and
    # hand-edited ones.)
    unreleased = re.search(r"^## \[Unreleased\][^\n]*\n+", text, flags=re.MULTILINE)
    if unreleased:
        # Section goes BELOW [Unreleased] (most-recent-first ordering, which
        # matches Keep-a-Changelog and what readers expect).
        head = text[: unreleased.end()]
        tail = text[unreleased.end() :]
        new = head + "\n" + section + "\n" + tail
    else:
        h1 = re.search(r"^# [^\n]+\n+", text, flags=re.MULTILINE)
        if h1:
            new = text[: h1.end()] + "\n" + section + "\n" + text[h1.end() :]
        else:
            new = section + "\n" + text

    # Collapse any 3+ run of newlines to exactly two — keeps section
    # boundaries to a single blank line regardless of how many times
    # `insert` has run before. Idempotent.
    new = re.sub(r"\n{3,}", "\n\n", new)

    CHANGELOG.write_text(new)
    print(f"insert: prepended new section into {CHANGELOG.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--mode",
        choices=("generate", "insert", "both"),
        default="both",
        help="generate writes release-body.md; insert updates CHANGELOG.md; both runs them in order",
    )
    ap.add_argument("--tag", help="vX.Y.Z (defaults to the GITHUB_REF tag)")
    args = ap.parse_args()

    if args.mode in ("generate", "both"):
        tag = args.tag or os.environ.get("GITHUB_REF", "").removeprefix("refs/tags/")
        if not tag or not tag.startswith("v"):
            print("error: no tag (pass --tag or run from a tag-push workflow)", file=sys.stderr)
            return 2
        rc = do_generate(tag)
        if rc != 0:
            return rc

    if args.mode in ("insert", "both"):
        rc = do_insert()
        if rc != 0:
            return rc

    return 0


if __name__ == "__main__":
    sys.exit(main())
