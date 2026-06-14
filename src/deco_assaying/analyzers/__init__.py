# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""Per-language analyzer registry.

`get_analyzer(language)` returns a callable with the signature

    analyzer(source_bytes, parser_root) -> dict

producing the per-file JSON shape (minus the `file` envelope, which the
top-level analyze.py fills in). If a language has no registered analyzer,
`get_analyzer` returns the fallback that emits only chunks + module_doc.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from deco_assaying.analyzers import (
    _fallback,
    bash,
    c,
    cpp,
    csharp,
    go,
    java,
    javascript,
    php,
    python,
    ruby,
    rust,
    typescript,
)

Analyzer = Callable[[bytes, Any], dict[str, Any]]

_REGISTRY: dict[str, Analyzer] = {
    "python": python.analyze,
    "typescript": typescript.analyze,
    "tsx": typescript.analyze,
    "javascript": javascript.analyze,
    "go": go.analyze,
    "rust": rust.analyze,
    "java": java.analyze,
    "ruby": ruby.analyze,
    "c": c.analyze,
    "cpp": cpp.analyze,
    "csharp": csharp.analyze,
    "php": php.analyze,
    "bash": bash.analyze,
}


def get_analyzer(language: str) -> tuple[Analyzer, bool]:
    """Return (analyzer, has_full_support)."""
    if language in _REGISTRY:
        return _REGISTRY[language], True
    return _fallback.analyze, False
