"""Trace de débogage légère — active seulement si le fichier de trace existe.

Fichier par défaut : <racine monorepo>/trace.log. Une app peut choisir le
sien via set_trace_file() (LarcSuperviseur : LarcSuperviseur/trace.log).
"""
import os

from .paths import larc_root

_TRACE_FILE = str(larc_root() / 'trace.log')
_ENABLED = os.path.isfile(_TRACE_FILE)
_logger = None


def set_trace_file(path) -> None:
    """Change le fichier de trace ; activé seulement si le fichier existe."""
    global _TRACE_FILE, _ENABLED, _logger
    if _logger:
        _logger.close()
    _logger = None
    _TRACE_FILE = os.path.normpath(str(path))
    _ENABLED = os.path.isfile(_TRACE_FILE)


def get_trace_file() -> str:
    return _TRACE_FILE


def trace(msg: str):
    if not _ENABLED:
        return
    global _logger
    if _logger is None:
        _logger = open(_TRACE_FILE, 'a', encoding='utf-8')
    _logger.write(f"{msg}\n")
    _logger.flush()


def enable():
    global _ENABLED, _logger
    if not _ENABLED:
        _ENABLED = True
        _logger = open(_TRACE_FILE, 'a', encoding='utf-8')
        trace("[trace] ACTIVÉ")


def disable():
    global _ENABLED, _logger
    if _logger:
        _logger.close()
    _logger = None
    _ENABLED = False
