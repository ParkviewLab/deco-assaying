# SPDX-FileCopyrightText: 2026 Gary Frattarola <garyf@parkviewlab.ai>
#
# SPDX-License-Identifier: MIT OR Apache-2.0

"""C analyzer."""

from deco_assaying.analyzers.c_family import make_analyzer

analyze = make_analyzer(is_cpp=False)
