"""Chemins communs des apps Larc — racine du monorepo."""
import os
from pathlib import Path


def larc_root() -> Path:
    """Racine du monorepo : variable LARC_ROOT si définie, sinon le dossier
    parent de LarcCommon (D:\\projets en développement)."""
    env = os.environ.get('LARC_ROOT')
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]
