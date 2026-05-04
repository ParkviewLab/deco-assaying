"""JavaScript analyzer (shared core with TypeScript, minus TS-only constructs)."""

from deco_assaying.analyzers._ts_js import make_analyzer

analyze = make_analyzer(is_typescript=False)
