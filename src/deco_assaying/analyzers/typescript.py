"""TypeScript analyzer (also used for TSX)."""

from deco_assaying.analyzers._ts_js import make_analyzer

analyze = make_analyzer(is_typescript=True)
