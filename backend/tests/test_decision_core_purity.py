"""D1 purity: no clock, I/O, database, bus, Governor or Portfolio State imports."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from app.trading_intelligence import candidate_ranking, candidate_selection, decision_evidence

_MODULES = (decision_evidence, candidate_ranking, candidate_selection)
_ALLOWED_IMPORTS = {
    "__future__", "dataclasses", "datetime", "json", "typing",
    "app.trading_intelligence.candidate_contract",
    "app.trading_intelligence.candidate_eligibility",
    "app.trading_intelligence.decision_evidence",
    "app.trading_intelligence.candidate_ranking",
}
_FORBIDDEN_CALLS = {"now", "utcnow", "today", "time", "monotonic", "perf_counter", "open", "print", "uuid4", "random", "sleep"}


@pytest.mark.parametrize("module", _MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_modules_have_no_clock_io_or_wiring_imports(module) -> None:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in _ALLOWED_IMPORTS, f"{module.__name__} imports {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert node.module in _ALLOWED_IMPORTS, f"{module.__name__} imports from {node.module}"
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in _FORBIDDEN_CALLS, f"{module.__name__} calls {name}()"


def test_importing_the_core_pulls_in_no_application_services() -> None:
    backend = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import app.trading_intelligence.candidate_selection\n"
        "bad = sorted(m for m in sys.modules if m.startswith('app.') and not m.startswith('app.trading_intelligence'))\n"
        "assert not bad, bad\n"
        "assert not any(m.startswith(('sqlalchemy', 'pydantic', 'asyncio')) for m in sys.modules), 'heavy import'\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, str(backend)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_numeric_policy_constants_hide_in_ranking_or_selection() -> None:
    """No weight, threshold, minimum sample or default slot count exists as a module constant."""
    for module in (candidate_ranking, candidate_selection, decision_evidence):
        numeric = {
            name: value for name, value in vars(module).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool) and not name.startswith("__")
        }
        allowed = {"RANKING_RESULT_SCHEMA_VERSION", "SELECTION_RESULT_SCHEMA_VERSION", "MAX_ATTEMPTS_PER_CANDIDATE"}
        assert set(numeric) <= allowed, (module.__name__, numeric)
