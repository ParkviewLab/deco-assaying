<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

<!-- PARKVIEWLAB:BEGIN (managed by ParkviewLab/handbook — do not edit inside this block; run scripts/sync-agent-files.sh to update) -->
# ParkviewLab conventions

This repo follows the **[ParkviewLab handbook](https://github.com/ParkviewLab/handbook/tree/main)** —
the single source of truth for how we work. These pointer files **don't re-inline** its rules:
**read the handbook before non-trivial work**, starting with
**[`ai-collaboration.md`](https://github.com/ParkviewLab/handbook/blob/main/docs/ai-collaboration.md)**
(the behavioural contract). Only the safety-critical guardrails are summarized here.

**If present, read `docs/northstar.md` before working.** It states the project's intent and is authoritative.

## Shared-state writes need explicit authorization
- **Merging a PR into `develop` is the user's call.** A broad directive ("fix all that", "finish it") authorizes work on the branch, **not** the merge.
- **Tagging, cutting a release, force-pushing, or pushing to a protected branch each need an explicit, per-action go-ahead** — never inferred from a descriptive label (e.g. "→ v0.1.1"). One release ask covers the whole CLI release flow.

## Workflow basics
- Work in an ephemeral, **prefixed** worktree off `develop` (`feature-`/`fix-`/`doc-`/…) — don't commit on `develop`/`main` directly. Open a PR **into `develop`**.
- PRs are **squash-merged**, so the **PR title** carries the Conventional Commit prefix (`feat:`/`fix:`/`docs:`/…) the changelog is generated from.

**Everything else lives in the handbook** (don't rely on memory): branching, commits & changelogs, releases, Python tooling, CI, licensing, and the full communication norms — see <https://github.com/ParkviewLab/handbook/tree/main/docs>.
<!-- PARKVIEWLAB:END -->

<!-- Repo-specific guidance below this line is preserved by the sync script — add anything particular to this repo here. -->
