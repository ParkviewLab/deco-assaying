"""C++ analyzer."""

from parkview_codeparse.analyzers.c_family import make_analyzer

analyze = make_analyzer(is_cpp=True)
