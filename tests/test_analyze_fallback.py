# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

from deco_assaying import analyze


def test_unknown_extension_returns_no_parser_envelope():
    r = analyze.analyze_inline(content="hello world", filename="thing.xyz")
    assert r["file"]["language"] == ""
    assert r["parse"]["ok"] is False
    assert r["parse"].get("reason") == "no_parser"
    assert r["chunks"] == []
    assert r["symbols"] == []


def test_unsupported_language_uses_fallback_analyzer():
    # YAML has tree-sitter coverage but no full-support analyzer (and never
    # will — it isn't a programming language), so we get the fallback
    # envelope: parse succeeds, chunks are produced, symbols stay empty.
    src = "name: example\nversion: 1\nlist:\n  - a\n  - b\n"
    r = analyze.analyze_inline(content=src, filename="config.yaml")
    assert r["file"]["language"] == "yaml"
    assert r["parse"]["ok"] is True
    assert r["symbols"] == []
    assert r["chunks"]  # chunking is language-agnostic


def test_string_literals_extracted_in_typescript():
    src = 'const url = "https://example.com/x";\n'
    r = analyze.analyze_inline(content=src, filename="x.ts")
    urls = [lit for lit in r["literals_of_interest"] if lit["kind"] == "url"]
    assert any(u["value"] == "https://example.com/x" for u in urls)
