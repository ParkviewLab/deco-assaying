<!--
SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>

SPDX-License-Identifier: MIT OR Apache-2.0
-->

# Changelog

All notable changes to this project are recorded here. Each release entry
has two parts:

- **Highlights** — a 2-3 sentence "what's new" paragraph generated at
  release time by an Anthropic-API call (see
  `scripts/generate_changelog.py`).
- **Categorized changes** — a list of merged commits since the previous
  tag, grouped by [Conventional Commit](https://www.conventionalcommits.org/)
  prefix, produced by [git-cliff](https://git-cliff.org/) using
  `cliff.toml`.

The release workflow on every tag push regenerates both, commits the new
section here, and uses the same content as the GitHub Release body.

<!--
  Keep-a-Changelog ordering: [Unreleased] at the top, then newest
  released version, then older versions. generate_changelog.py inserts
  new "## [vX.Y.Z] - YYYY-MM-DD" sections directly below [Unreleased].
  Don't remove the marker.
-->

## [Unreleased]

## [v0.3.4] - 2026-06-24

### Highlights

This is a maintenance release with no user-facing changes; GitHub Actions pins were bumped to their Node 24 floors to align with internal CI policy.

### Docs

- V0.3.3 [skip ci] (c589bc5)

## [v0.3.3] - 2026-06-24

### Highlights

This is a maintenance release that updates the release workflow's action pins (checkout@v6, setup-uv@v8.1.0) to align with the handbook template and clear Node 24 deprecation warnings. There are no user-facing behavior changes.

### Docs

- V0.3.2 [skip ci] (c74446f)

## [v0.3.2] - 2026-06-24

### Highlights

This is a maintenance release with no user-facing code changes. The forward-looking wishlist has been moved from a root scratchpad into docs/future_features.md, and indirect dependencies (cryptography, pydantic-settings, python-multipart, pyjwt) have been bumped.

### Docs

- V0.3.1 [skip ci] (48b508f)
- Add docs/future_features.md, remove humans_notes.md (#16) (cf6633b)

## [v0.3.1] - 2026-06-14

### Highlights

This release floors the starlette dependency at >=1.0.1 to pick up the fix for GHSA-86qp-5c8j-p5mr, a Host-header validation gap that could poison request.url.path and bypass path-based security checks — relevant to deco's path-based download API. Other changes are internal: adopting the latest handbook conventions for AI pointer files and adding an on-demand dev-release workflow.

### Bug fixes

- Upgrade starlette to >=1.0.1 (GHSA-86qp-5c8j-p5mr host-header validation) (#13) (05d4dde)

### Docs

- V0.3.0 [skip ci] (df816bb)

## [v0.3.0] - 2026-06-14

### Highlights

This release relicenses the project to MIT OR Apache-2.0 with REUSE-compliant per-file SPDX headers and updates stale `garycoding` references to the `ParkviewLab` namespace, including the published Docker image location (now `ghcr.io/parkviewlab/deco-assaying`), README clone and registry links, and the launchd label. Internal changes include adoption of ParkviewLab handbook CI workflows and conventions, and an indirect bump of `idna` to 3.15.

### Bug fixes

- Point stale garycoding references at the ParkviewLab namespace (3ed745f)

### Docs

- V0.2.1 [skip ci] (4c09e60)

## [v0.2.1] - 2026-06-12

### Highlights

This release is primarily maintenance, adding automated CHANGELOG.md generation and GitHub Release creation on tag push, with the changelog backfilled across all prior tags. The README now links to the CHANGELOG and GitHub Releases so readers can find per-version notes.

### Docs

- Link CHANGELOG.md and GitHub Releases from README (b3dd7ac)

## [v0.2.0] - 2026-05-18

### Highlights

This release blocks the GPL-3.0-licensed ebnf grammar from being loaded by tree-sitter-language-pack, preventing GPL code from being written into the runtime cache alongside the MIT-licensed server. The block is applied at the get_parser and get_language entry points, with the rationale documented inline.

### Features

- Block ebnf grammar loading to keep server MIT-clean (a7de363)

## [v0.1.7] - 2026-05-17

### Highlights

This release adds a `--transport stdio` CLI flag that lets MCP clients such as Claude Desktop spawn deco-assaying as a subprocess and communicate over JSON-RPC on stdin/stdout. HTTP remains the default transport, so existing LaunchAgent, systemd, and Docker deployments continue to work without changes.

### Features

- Add --transport stdio CLI flag for MCP stdio transport (3d24dd6)

## [v0.1.6] - 2026-05-06

### Highlights

This release is internal-only, covering CI release-flow hardening (a new gate job that verifies the tag matches pyproject.toml and is reachable from origin/main before either GHCR or PyPI publishing runs) along with README updates pointing to dev-tools helpers and deferred notes on dependency-update automation. No user-facing changes to the MCP server itself.

### Docs

- Update Releasing section to use dev-tools helpers (9a221d9)

## [v0.1.5] - 2026-05-04

### Highlights

The symbols artifact is split into `all_symbols.json` and `top_level_symbols.json`, exposed via two MCP tools (`get_all_symbols`, `get_top_level_symbols`) with the same prefix/kind/file_prefix filters; the prior `get_symbols` / `symbols.json` is removed. A new `analysis_index.json` and `get_analysis_index` tool list every artifact with byte sizes and absolute download URLs (configurable via the new `PUBLIC_BASE_URL` env var), letting agents decide what fits in context before fetching, and two MCP prompts (`analyze_repo`, `explore_finished_job`) ship the recommended workflow to clients directly. Responses now go through gzip compression when clients send `Accept-Encoding`, and the README has been rewritten with five deployment recipes (uvx, uv tool install, launchd, systemd user, Docker compose).

## [v0.1.4] - 2026-05-03

### Highlights

This release adds a top-level MIT LICENSE file and declares the license via PEP 639 metadata in pyproject.toml, so the built wheel now ships License-Expression and License-File entries. PyPI and SPDX-aware scanners will now report the project's license correctly.

## [v0.1.3] - 2026-05-03

### Highlights

The get_manifest response now includes a languages_by_count list, presenting the same per-language data pre-sorted by file count so callers don't need to sort it themselves; the on-disk manifest.json is unchanged. The get_tree response gains total_size_bytes for the returned slice and total_size_bytes_in_repo for the unfiltered total, making it easier to judge a subtree's size before drilling in. Tool descriptions were updated to document the new fields.

## [v0.1.2] - 2026-05-03

### Highlights

This release adds eight MCP tools for fetching finished-job artifacts inline over /sse, so remote LLMs can read manifests, trees, symbols, languages, errors, file listings, per-file analyses, and log events directly instead of receiving unreachable host-side paths. The larger rollups accept narrowing arguments (prefixes, globs, sections, offsets) to keep responses within context-window limits on large repos. Correspondingly, index_repo now returns only {job_id} and get_job_status no longer includes output_path, manifest_path, or log_path, and the analyze_file and index_repo tool descriptions have been tightened.

## [v0.1.1] - 2026-05-03

### Highlights

This is a maintenance release that fixes the Docker build, which previously failed because the image did not include README.md when uv sync tried to install the project itself. No functional changes ship alongside the fix.

## [v0.1.0] - 2026-05-03

### Highlights

Initial release of an MCP server that performs tree-sitter-based source code analysis across twelve fully-supported languages (Python, TypeScript, JavaScript, Go, Rust, Java, Ruby, C, C++, C#, PHP, Bash), with fallback handling for other grammars. The `index_repo` tool ingests local paths or GitHub/GitLab URLs, using a Trees-API pre-flight and either a size-bounded partial clone or bin-packed streaming fetch to keep peak source-side disk near 100 MB regardless of repo size, and writes per-file artifacts plus manifest/tree/symbols/languages/errors rollups under a server-managed `OUTPUT_ROOT/{job_id}/`. Outputs are retrievable over HTTP via `/outputs/{job_id}/...` (single files, directory listings, glob/bulk ZIP streaming, DELETE), with a background sweeper honoring `OUTPUT_EXPIRY_DAYS`. Ships as a `uv tool` / `uvx` install on PyPI and a multi-arch (amd64 + arm64) container on GHCR, driven by a tag-based release workflow.

