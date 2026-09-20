<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# Contributing

> The authoritative, org-wide version of these conventions is the
> [ParkviewLab handbook](https://github.com/ParkviewLab/handbook).

This repo follows the ParkviewLab conventions. The essentials:

## Branch & PR flow

- Branch off **`develop`** into an ephemeral worktree named with a prefix:
  `feature-`, `bug-`/`fix-`, `doc-`, `test-`, `ops-`, `ci-`, `build-`, `release-`
  (hyphen, not slash). See the handbook's `branching.md`.
- Open a PR into **`develop`**. The repo is **squash-only**, so the merge button
  can only squash; **merging is the maintainer's action.**
- Releases are cut from **`main`** via the CLI (`git merge --no-ff develop`, then
  bump + tag) — not a PR. See the handbook's `releases.md`.

## Commit / PR-title convention (this is what the changelog reads)

Because PRs are squash-merged, **the PR title becomes the commit subject**, and
the changelog is generated from it (by dev-tools' `generate-changelog`, run by the release workflow at a pinned release). Prefix every PR title with a [Conventional
Commit](https://www.conventionalcommits.org/) type:

| Title | Group in the notes | Notes |
|---|---|---|
| any type with `!` after it (`feat!:`), or a breaking-change footer | Breaking changes | listed there once, whatever its type |
| `feat:` | Features | user-visible |
| `fix:` | Bug fixes | user-visible |
| `perf:` | Performance | user-visible |
| `refactor:` | Refactor | |
| `docs:` | Docs | |
| `test:` | Tests | |
| `revert:` | Reverts | GitHub's Revert button titles a PR `Revert "…"`, which has no type |
| `build:` / `chore:` / `ci:` / `style:` | Maintenance | |
| any other title | Other changes | the whole title |
| a commit with no pull request | Direct commits | its subject and short hash |

A title without a recognised type is not dropped: it is listed whole under Other changes. So prefix your PR titles, and correct a title before the merge, since retitling afterwards does not change the commit. The groups appear in the order above, and an empty group is left out.

## Local checks before opening a PR

Run the same checks CI requires, so the PR is green on arrival:

```bash
uv sync
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check
uv run pytest -m "not network and not docling" -q
uvx --from "reuse[charset-normalizer]" reuse lint
```

A PR **can't be merged until the required checks pass** (lint, format, types,
tests, REUSE, the version guard — see the handbook's `ci.md`). Push after each
commit. See also `python-tooling.md` and `testing.md`.

## Versioning

The version lives in **`pyproject.toml` only**; never hard-code it elsewhere, and
never type it on a `git tag` line — use `git bump` / `git release` from
[`dev-tools`](https://github.com/ParkviewLab/dev-tools). See `releases.md`.

## AI contributors

Read `docs/northstar.md` first, and follow the behavioural contract in the
handbook's `ai-collaboration.md` (notably: merging/tagging/releasing need an
explicit, per-release go-ahead).
