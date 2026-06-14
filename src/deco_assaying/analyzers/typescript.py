# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""TypeScript analyzer (also used for TSX)."""

from deco_assaying.analyzers._ts_js import make_analyzer

analyze = make_analyzer(is_typescript=True)
