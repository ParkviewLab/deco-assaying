# parkview-codeparse-server

MCP server that performs tree-sitter-based source code analysis for the
Cobgrind LLM-Wiki daemon.

## Run

```bash
uv sync
uv run python -m parkview_codeparse
```

The server listens on `PORT` (default `35832`) with:

- `POST /sse` — MCP Streamable HTTP transport.
- `GET /health` — liveness probe.
- `GET /admin/*` — read-only JSON ops endpoints.
- `GET /docs` — OpenAPI / Swagger UI for the HTTP API.

## MCP tools

- `analyze_file(content, filename?, language?, options?)` — parse a single
  file passed inline; returns structural JSON.
- `index_repo(source, output_dir, options?)` — start a job that indexes a
  whole repo (local path or GitHub URL) and writes per-file artifacts plus a
  manifest under `output_dir`. Returns `{ job_id }`.
- `get_job_status(job_id)` — poll a running or completed job.
- `cancel_job(job_id)` — cooperative cancel.
- `list_supported_languages()` — capability discovery.
- `detect_language(path)` — extension/shebang detection helper.

See `/Users/gary/.claude/plans/cobgrind-is-a-daemon-fluttering-sutton.md` for
the full design.
