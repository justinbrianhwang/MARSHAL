"""Pin the audit scripts to the published protocol-v2 graded scorer.

The benchmark scorer computes the final credit natively under protocol v2. The audit scripts compare credit versions
by patching the published v2 functions, so they load the recorded copy (published_v2_graded_scoring.py) under the
scorer's module name. Import this module before any other marshal_bench graded-scorer import.
"""
import importlib.util
import sys
from pathlib import Path

import marshal_bench.criteria as _criteria

NAME = "marshal_bench.criteria.graded_episode_scoring"
SRC = Path(__file__).resolve().with_name("published_v2_graded_scoring.py")

if getattr(sys.modules.get(NAME), "__file__", None) != str(SRC):
    _spec = importlib.util.spec_from_file_location(NAME, SRC)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[NAME] = _mod
    _spec.loader.exec_module(_mod)
    _criteria.graded_episode_scoring = _mod
