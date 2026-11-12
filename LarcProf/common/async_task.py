"""Exécution d'une fonction bloquante (réseau) hors du thread d'interface.

    run_async(self, lambda: service.inbox(uid), self._on_inbox, self._on_error)

Les callbacks s'exécutent TOUJOURS dans le thread d'interface. `owner` (un QObject) porte la vie
du résultat : s'il est détruit avant la fin de la tâche, le résultat est simplement ignoré.

Un seul distributeur, jamais détruit, reçoit tous les résultats : le thread de travail n'émet donc
jamais sur un objet que l'interface pourrait supprimer au même instant (course évitée).
"""
from __future__ import annotations

import weakref
from typing import Callable

import shiboken6
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Dispatcher(QObject):
    result = Signal(int, bool, object)      # (jeton, réussi ?, valeur ou message d'erreur)

    def __init__(self) -> None:
        super().__init__()
        self._pending: dict[int, tuple] = {}
        self._next = 0
        self.result.connect(self._dispatch)     # émis depuis un thread de travail → mis en file

    def register(self, owner: QObject, on_done, on_fail) -> int:
        self._next += 1
        self._pending[self._next] = (weakref.ref(owner), on_done, on_fail)
        return self._next

    @Slot(int, bool, object)
    def _dispatch(self, token: int, ok: bool, value) -> None:
        entry = self._pending.pop(token, None)
        if entry is None:
            return
        owner_ref, on_done, on_fail = entry
        owner = owner_ref()
        if owner is None or not shiboken6.isValid(owner):
            return                              # le propriétaire a disparu : résultat ignoré
        (on_done if ok else on_fail)(value)


_dispatcher: _Dispatcher | None = None


def _get_dispatcher() -> _Dispatcher:
    """Créé au premier appel, depuis le thread d'interface (run_async n'est appelé que de là)."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = _Dispatcher()
    return _dispatcher


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], object], dispatcher: _Dispatcher, token: int) -> None:
        super().__init__()
        self._fn = fn
        self._dispatcher = dispatcher
        self._token = token
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            ok, value = True, self._fn()
        except Exception as exc:  # noqa: BLE001 — toute erreur réseau/base est rendue à l'appelant
            ok, value = False, str(exc).strip() or type(exc).__name__
        try:
            self._dispatcher.result.emit(self._token, ok, value)
        except RuntimeError:
            pass                                # application en cours de fermeture


def run_async(owner: QObject, fn: Callable[[], object],
              on_done: Callable[[object], None],
              on_fail: Callable[[str], None]) -> None:
    dispatcher = _get_dispatcher()
    token = dispatcher.register(owner, on_done, on_fail)
    QThreadPool.globalInstance().start(_Task(fn, dispatcher, token))
