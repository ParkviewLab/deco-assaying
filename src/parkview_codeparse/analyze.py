"""Per-file analyzer.

Will be filled in next: tree-sitter parse, run language tags.scm query,
extract symbols/imports/exports/references, compute metrics, optionally
chunk via cAST. Until that lands, the public entry point raises
`NotImplementedError` so MCP callers cannot mistake stub output for a real
analysis.
"""

from __future__ import annotations

from typing import Any


def analyze_inline(
    *,
    content: str,
    filename: str = "",
    language: str = "",
    include_chunks: bool = True,
    chunk_max_tokens: int = 800,
) -> dict[str, Any]:
    """Analyze source code passed as a string.

    Not yet implemented. Once the real analyzer lands, this returns the
    per-file JSON shape documented in the plan.
    """
    raise NotImplementedError("analyze.analyze_inline is not yet implemented (skeleton commit)")
