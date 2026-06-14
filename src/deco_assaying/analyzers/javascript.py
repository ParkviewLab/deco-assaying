# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""JavaScript analyzer (shared core with TypeScript, minus TS-only constructs)."""

from deco_assaying.analyzers._ts_js import make_analyzer

analyze = make_analyzer(is_typescript=False)
