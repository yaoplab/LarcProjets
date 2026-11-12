"""Tests du panneau agenda/messages de l'accueil : actualisation asynchrone, états hors ligne."""
from __future__ import annotations

import os
from datetime import date, datetime, time, timezone

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import views.home_info_panel as hip  # noqa: E402
from common.agenda_service import AgendaEvent, AgendaUnavailable  # noqa: E402
from common.messages_service import Message, MessagesUnavailable, Person  # noqa: E402
from common.session import session  # noqa: E402

NOW = datetime.now(timezone.utc)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeService:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.read: list[int] = []
        self.inbox_msgs = [
            Message(1, 2, "Efua Mensah", None, "", "Réunion", "Jeudi 8h", NOW, False),
            Message(2, 3, "Ama Kossi", 1, "Vous", "Question", "Une question", NOW, True)]

    def _check(self):
        if self.fail:
            raise MessagesUnavailable("connection refused")

    def inbox(self, uid, n):
        self._check()
        return self.inbox_msgs

    def sent(self, uid, n):
        self._check()
        return []

    def people(self, uid):
        self._check()
        return [Person(2, "Efua Mensah", True)]

    def can_broadcast(self, uid):
        self._check()
        return False

    def mark_read(self, uid, ids):
        self.read += ids

    def close(self):
        pass


def _refresh_and_wait(panel, timeout_ms: int = 5000) -> bool:
    got = []
    loop = QEventLoop()
    panel.refreshed.connect(lambda ok: (got.append(ok), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    panel.refresh()
    loop.exec()
    return got[0] if got else None


@pytest.fixture()
def logged_in():
    old = getattr(session, 'user_id', 0)
    session.user_id = 1
    yield
    session.user_id = old


def test_online_cycle_fills_both_cards(app, logged_in, monkeypatch):
    today = date.today()
    monkeypatch.setattr(hip, "agenda_service_call", lambda: (
        [AgendaEvent(today, time(8), time(9), "Réunion", "Salle 1")], 14))
    panel = hip.HomeInfoPanel(service=FakeService())
    assert _refresh_and_wait(panel) is True
    assert panel._messages.unread_count() == 1
    assert panel._messages._badge.text() == "1"
    assert panel._agenda._data[0][0].title == "Réunion"
    assert "Aujourd'hui" in hip.day_label(today, today)


def test_offline_cycle_shows_friendly_state_and_disables_compose(app, logged_in, monkeypatch):
    def offline():
        raise AgendaUnavailable("Agenda injoignable (pas d'Internet ?)")
    monkeypatch.setattr(hip, "agenda_service_call", offline)
    panel = hip.HomeInfoPanel(service=FakeService(fail=True))
    assert _refresh_and_wait(panel) is False          # ni agenda ni messages : hors ligne
    assert panel._messages.state.text() == "Disponible avec Internet."
    assert "connection refused" in panel._messages.state.toolTip()      # détail technique en infobulle
    assert not panel._messages._compose.isEnabled()
    assert panel._messages.unread_count() == 0


def test_not_logged_in_asks_to_connect(app, monkeypatch):
    session.user_id = 0
    monkeypatch.setattr(hip, "agenda_service_call", lambda: ([], 14))
    panel = hip.HomeInfoPanel(service=FakeService())
    _refresh_and_wait(panel)
    assert "Connectez-vous" in panel._messages.state.text()


def test_opening_unread_message_marks_it_read(app, logged_in, monkeypatch):
    monkeypatch.setattr(hip, "agenda_service_call", lambda: ([], 14))
    svc = FakeService()
    panel = hip.HomeInfoPanel(service=svc)
    _refresh_and_wait(panel)
    monkeypatch.setattr(hip.ReadDialog, "exec", lambda self: 0)          # pas de fenêtre modale en test
    panel._open_message(svc.inbox_msgs[0], False)
    loop = QEventLoop()
    QTimer.singleShot(300, loop.quit)                                    # laisse partir la tâche
    loop.exec()
    assert panel._messages.unread_count() == 0
    assert svc.read == [1]


def test_agenda_not_configured_is_not_a_crash(app, logged_in, monkeypatch):
    def unconfigured():
        raise AgendaUnavailable("Agenda non configuré (section [Agenda] IcsUrl de config.ini).")
    monkeypatch.setattr(hip, "agenda_service_call", unconfigured)
    panel = hip.HomeInfoPanel(service=FakeService())
    assert _refresh_and_wait(panel) is True           # les messages, eux, ont répondu
    assert "non configuré" in panel._agenda.state.text()
