"""JavaScript analyzer (shared core with TypeScript, minus TS-only constructs)."""

from parkview_codeparse.analyzers._ts_js import make_analyzer

analyze = make_analyzer(is_typescript=False)
