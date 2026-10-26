"""LarcCommon ne dépend d'aucune application.

larccommon et phibuilder sont importés par toutes les apps : un import vers
une app (ex. LarcSuperviseur) crée une dépendance circulaire et casse les
apps qui ne l'embarquent pas. Analyse statique (ast) : imports de tête,
locaux (dans une fonction) et dans les try/except.
"""
import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGES = ("larccommon", "phibuilder")
_APPS = ("LarcSuperviseur", "LarcProf", "LarcMat", "LarcSecretaire",
         "LarcHub", "LarcRH", "LarcCompta", "LarcConfig")


def _python_files():
    for pkg in _PACKAGES:
        yield from sorted((_ROOT / pkg).rglob("*.py"))


def _app_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            if name.split(".")[0] in _APPS:
                found.append(f"{path.relative_to(_ROOT)}:{node.lineno} → {name}")
    return found


def test_packages_found():
    assert any(True for _f in _python_files())


@pytest.mark.parametrize("path", list(_python_files()),
                         ids=lambda p: str(p.relative_to(_ROOT)))
def test_no_import_of_an_app(path):
    assert _app_imports(path) == []
