<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# Dependency update automation — deferred

**Status:** considered, not yet wired up. This doc captures the tradeoff
space so future-us can pick it up cold.

## What's already on

GitHub's repo settings already give us the **reactive** side:

- **Dependabot security updates** are on (`automated-security-fixes` is
  enabled; the setting is at
  <https://github.com/parkviewlab/deco-assaying/settings/security_analysis>).
  When a dep we use lands in the GitHub Advisory Database with a CVE,
  Dependabot opens a PR bumping it to a fixed version.
- **Secret scanning and push protection** are disabled.

The piece *not* yet wired up is **proactive version updates** — auto-PRs
that bump deps to new versions on a schedule whether or not there's a
security advisory.

## The decision space

Three options, in order of effort:

### Option 1: Dependabot for `github-actions` + `docker` only

Drop a `.github/dependabot.yml` covering just the two ecosystems
Dependabot handles flawlessly. Skip Python.

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    groups:
      actions:
        patterns: ["*"]
  - package-ecosystem: "docker"
    directory: "/"
    schedule:
      interval: "weekly"
```

- **Pro:** zero caveats; both ecosystems Just Work; handles `release.yml`
  action bumps + `Dockerfile` base-image bumps.
- **Con:** Python deps in `pyproject.toml` / `uv.lock` go un-monitored
  for proactive version updates (security updates still fire via the
  separate toggle).

### Option 2: Option 1 + Dependabot for `pip` (Python)

Add a third `package-ecosystem: pip` block to the YAML above.

- **Pro:** Python deps get proactive bumps too.
- **Con:** the drawback recorded here earlier, that Dependabot cannot
  update `uv.lock`, no longer holds. The issue that tracked it,
  <https://github.com/dependabot/dependabot-core/issues/10478>, was
  closed as completed on 2025-04-12. Which `package-ecosystem` entry
  (`pip` or a separate `uv` one) is the right configuration for
  `uv.lock` has not been checked.

### Option 3: Renovate for everything

Install [Renovate Bot](https://github.com/apps/renovate) on the repo.
Renovate has first-class `uv.lock` support (since 2024) — it bumps
`pyproject.toml` and regenerates `uv.lock` in the same PR.

- **Pro:** cleanest Python story; handles the uv lockfile correctly;
  also covers `github-actions` + `docker`. Configurable enough that
  most knobs you'd want are exposed (auto-merge for patch bumps, group
  related updates, schedule windows, etc.).
- **Con:** different tool from Dependabot; one more app installed on
  the GitHub account; Renovate's config DSL has its own learning curve
  (`renovate.json` instead of `.github/dependabot.yml`); somewhat
  noisier defaults than Dependabot until tuned.

### Status quo: nothing

Manual `uv sync --upgrade && uv lock` periodically. Fine for a small
project; falls behind on a busy one.

## Recommendation when we revisit

1. **First pass:** Option 1. Cheap, immediate value, zero risk.
   If we never get further, this still keeps Actions + Docker fresh.
2. **Second pass, when Python deps start drifting:** Option 3
   (Renovate). Its native `uv.lock` handling was the deciding factor;
   Dependabot now supports `uv.lock` as well (see Option 2), so that
   no longer distinguishes the two.
3. **Skip Option 2** entirely. The reason given for this, the
   lockfile-mismatch failure mode, no longer holds (see Option 2).

## How to act

Option 1 is a one-file commit:

```bash
mkdir -p .github
# paste the YAML above into .github/dependabot.yml
git add .github/dependabot.yml
git commit -m "Dependabot: weekly version updates for actions + docker"
git push
```

Dependabot picks up the file on the next sync (a few minutes) and
starts opening PRs on the configured schedule.

Option 3 is a couple of clicks at <https://github.com/apps/renovate>
plus a `renovate.json` in the repo root. Save for the next round.
