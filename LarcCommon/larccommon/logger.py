import os
from datetime import datetime

LOG_TO_FILE = True

LOG_FILENAME = 'larc.log'

# Rotation : au-delà de 5 Mo, rotation .1/.2/.3 (elarc.log atteignait 92 Mo)
_MAX_BYTES = 5 * 1024 * 1024
_KEEP = 3

# Ancien emplacement commun à toutes les apps : repli si LOCALAPPDATA absent
# ou non inscriptible.
_LEGACY_LOG_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'superviseur.log'
))

# True dès qu'une app a fixé son fichier via set_log_filename (ex. LarcProf →
# elarc.log) : set_log_file() (appelé par init_app) ne l'écrase alors pas.
_EXPLICIT = False


def _app_log_path(filename: str) -> str:
    """%LOCALAPPDATA%\\Larc\\logs\\<filename>, sinon l'ancien superviseur.log."""
    base = os.environ.get('LOCALAPPDATA')
    if base:
        path = os.path.join(base, 'Larc', 'logs', filename)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'a', encoding='utf-8'):
                pass
            return os.path.normpath(path)
        except OSError:
            pass  # non inscriptible : repli ci-dessous
    return _LEGACY_LOG_PATH


_LOG_PATH = _app_log_path(LOG_FILENAME)


def log(msg: str, level: str = "INFO", exc: BaseException | None = None) -> None:
    """Log fichier plat (rétro-compatible) + remontée au reporter pour
    ERROR/CRITICAL. Ne lève jamais."""
    if LOG_TO_FILE:
        try:
            _rotate_if_needed()
            with open(_LOG_PATH, 'a', encoding='utf-8') as f:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                f.write(f"[{timestamp}] {msg}\n")
        except Exception:
            log('erreur ignoree')
            pass
    if level in ("ERROR", "CRITICAL"):
        _report(msg, level, exc)


def log_error(msg: str, exc: BaseException | None = None) -> None:
    """Alias : log(msg, 'ERROR', exc) — remonte aussi vers error_log."""
    log(msg, "ERROR", exc)


def _report(msg: str, level: str, exc: BaseException | None) -> None:
    try:
        from .error_reporting import get_reporter
        rep = get_reporter()
        if rep is None:
            return
        tb = ''
        if exc is not None:
            import traceback
            tb = ''.join(traceback.format_exception(
                type(exc), exc, exc.__traceback__))
        rep.report(level, str(msg), traceback=tb)
    except Exception:
        log('erreur ignoree')
        pass


def _rotate_if_needed() -> None:
    if not os.path.isfile(_LOG_PATH):
        return
    try:
        if os.path.getsize(_LOG_PATH) <= _MAX_BYTES:
            return
        for i in range(_KEEP - 1, 0, -1):
            src = f"{_LOG_PATH}.{i}"
            dst = f"{_LOG_PATH}.{i + 1}"
            if os.path.isfile(src):
                os.replace(src, dst)
        os.replace(_LOG_PATH, f"{_LOG_PATH}.1")
    except Exception:
        log('erreur ignoree')
        pass


def set_log_to_file(value: bool) -> None:
    global LOG_TO_FILE
    LOG_TO_FILE = value


def get_log_path() -> str:
    return _LOG_PATH


def set_log_file(app_name: str) -> None:
    """Fichier log par app : %LOCALAPPDATA%\\Larc\\logs\\<app_name>.log.
    Appelé par init_app. Sans effet si l'app a déjà choisi son fichier
    (set_log_filename)."""
    global _LOG_PATH, LOG_FILENAME
    if _EXPLICIT:
        return
    LOG_FILENAME = f"{app_name}.log"
    _LOG_PATH = _app_log_path(LOG_FILENAME)


def set_log_filename(name: str) -> None:
    global _LOG_PATH, LOG_FILENAME, _EXPLICIT
    _EXPLICIT = True
    LOG_FILENAME = name
    _LOG_PATH = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', name
    ))
