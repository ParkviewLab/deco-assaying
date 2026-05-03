"""TypeScript analyzer (also used for TSX)."""

from parkview_codeparse.analyzers._ts_js import make_analyzer

analyze = make_analyzer(is_typescript=True)
