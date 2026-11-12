"""Panneau d'accueil LarcProf : Agenda partagé (14 jours) + Messages.

Disposition (validée IHM) : deux cartes côte à côte sous la frise de l'année. Tout est visible
sans clic ; les messages non lus portent une pastille. Aucune copie locale : sans Internet, les
deux cartes l'indiquent et se reconnectent seules à l'actualisation suivante.

Signaux :
    refreshed(bool)  — fin d'un cycle d'actualisation ; True si Internet a répondu (l'accueil
                       en profite pour remettre la frise à jour)
    unread_changed(int)
"""
from __future__ import annotations

import dataclasses
from datetime import date, datetime

from larccommon.design_system import ds
from larccommon.icons import icon as md3_icon
from larccommon.safe_slot import safe_slot
from phibuilder.widgets import ButtonVariant, M3Button, M3Label, M3ScrollArea
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from common import agenda_service
from common.agenda_service import AgendaEvent
from common.async_task import run_async
from common.messages_service import Message, MessagesService, Person
from common.session import session
from common.theme import theme_manager
from views.message_dialog import ComposeDialog, ReadDialog, format_when

REFRESH_MS = 5 * 60 * 1000
_QWIDGETSIZE_MAX = (1 << 24) - 1     # QWIDGETSIZE_MAX de Qt (non exporté par PySide6)

_WEEKDAYS = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']
_MONTHS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août',
           'septembre', 'octobre', 'novembre', 'décembre']


def day_label(d: date, today: date) -> str:
    text = f"{_WEEKDAYS[d.weekday()]} {d.day} {_MONTHS[d.month - 1]}"
    delta = (d - today).days
    if delta == 0:
        return f"Aujourd'hui · {text}"
    if delta == 1:
        return f"Demain · {text}"
    return text


def friendly_error(message: str) -> str:
    """Message court pour l'utilisateur ; le détail technique reste en infobulle."""
    known = ("n'est pas encore installée", "Identifiants cloud", "non configuré", "Agenda refusé",
             "adresse de l'agenda", "illisible", "trop volumineux", "Connectez-vous")
    return message if any(k in message for k in known) else "Disponible avec Internet."


def _label(text: str = '', name: str = '') -> M3Label:
    """M3Label stylé par la QSS du panneau (sélecteurs #objectName). Sa QSS propre (typo M3,
    couleur on_surface) est retirée : posée sur le widget lui-même, elle masquerait celle du
    panneau, qui suit le thème actif via _restyle()."""
    lbl = M3Label(text, theme_manager.phi_theme)
    lbl.setStyleSheet('')
    if name:
        lbl.setObjectName(name)
    return lbl


def _button(text: str, name: str) -> M3Button:
    """M3Button stylé par la QSS du panneau (#icon_btn, #tab_btn) : QSS propre retirée comme
    pour _label(), et hauteur fixe de M3Button relâchée (ces boutons gardent leur taille
    naturelle ou celle que fixe l'appelant)."""
    btn = M3Button(text, theme_manager.phi_theme, ButtonVariant.TEXT)
    btn.setStyleSheet('')
    btn.setMinimumSize(0, 0)
    btn.setMaximumHeight(_QWIDGETSIZE_MAX)
    btn.setObjectName(name)
    return btn


class _ElidedLabel(QLabel):
    """Étiquette d'une ligne qui coupe proprement avec « … » au lieu d'élargir la carte."""

    def __init__(self, text: str = '', parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ''
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        one_line = self._full.replace('\n', ' ')
        self.setText(QFontMetrics(self.font()).elidedText(one_line, Qt.ElideRight, max(self.width(), 1)))
        self.setToolTip(self._full if self.text() != one_line else '')


class _Card(QFrame):
    """Carte : en-tête (titre + actions) et zone défilante ou message d'état."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName('info_card')
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(ds.space_md, ds.space_sm, ds.space_md, ds.space_sm)
        outer.setSpacing(ds.space_xs)

        self.header = QHBoxLayout()
        self.header.setSpacing(ds.space_xs)
        self.title = _label(title, 'card_title')
        self.title.setMinimumHeight(ds.space_lg + ds.space_xs)      # en-têtes alignés d'une carte à l'autre
        self.header.addWidget(self.title)
        self.header.addStretch(1)
        outer.addLayout(self.header)

        # Heure de la dernière donnée obtenue AVEC succès (utile hors ligne : on sait depuis
        # quand c'est figé). Ne se met à jour qu'au succès — mark_updated() n'est jamais appelée
        # depuis set_error(), donc l'heure reste affichée telle quelle pendant les échecs suivants.
        self.updated_lbl = _label('', 'updated_at')
        outer.addWidget(self.updated_lbl)

        self.state = _label('', 'state')
        self.state.setWordWrap(True)
        self.state.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.state, 1)

        self.scroll = M3ScrollArea(theme_manager.phi_theme)
        self.scroll.setStyleSheet('')       # fond transparent : QSS du panneau
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.body.setObjectName('card_body')
        self.rows = QVBoxLayout(self.body)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(ds.space_xxs)
        self.scroll.setWidget(self.body)
        outer.addWidget(self.scroll, 1)

    def icon_button(self, icon_name: str, tip: str) -> QPushButton:
        btn = _button('', 'icon_btn')
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(ds.space_lg, ds.space_lg)
        btn.setProperty('icon_name', icon_name)
        self.restyle_icon(btn)
        return btn

    @staticmethod
    def restyle_icon(btn: QPushButton) -> None:
        btn.setIcon(md3_icon(btn.property('icon_name'), theme_manager.palette.text_soft, ds.space_md))

    def mark_updated(self) -> None:
        self.updated_lbl.setText(f"Mise à jour à {datetime.now():%H:%M}")

    def show_state(self, text: str, tip: str = '') -> None:
        self.state.setText(text)
        self.state.setToolTip(tip)
        self.state.show()
        self.scroll.hide()

    def show_rows(self) -> None:
        self.state.hide()
        self.scroll.show()

    def clear_rows(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()


# --------------------------------------------------------------------------- Agenda
class AgendaCard(_Card):
    refresh_requested = Signal()

    def __init__(self) -> None:
        super().__init__("Agenda")
        self._data: tuple[list[AgendaEvent], int] | None = None
        self._refresh = self.icon_button('refresh', "Actualiser l'agenda")
        self._refresh.clicked.connect(self.refresh_requested)
        self.header.addWidget(self._refresh)
        self.show_state("Chargement…")

    def set_events(self, events: list[AgendaEvent], days: int) -> None:
        self._data = (events, days)
        self.title.setText(f"Agenda · {days} prochains jours")
        self.clear_rows()
        if not events:
            self.show_state(f"Aucun événement dans les {days} prochains jours.")
            return
        today, last_day = date.today(), None
        for ev in events:
            if ev.day != last_day:
                head = _label(day_label(ev.day, today), 'day_head')
                self.rows.addWidget(head)
                last_day = ev.day
            self.rows.addWidget(self._event_row(ev))
        self.rows.addStretch(1)
        self.show_rows()

    def rerender(self) -> None:
        if self._data:
            self.set_events(*self._data)

    def set_error(self, message: str) -> None:
        self._data = None
        self.title.setText("Agenda")
        self.clear_rows()
        self.show_state(friendly_error(message), message)

    @staticmethod
    def _event_row(ev: AgendaEvent) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(ds.space_xs, 0, 0, 0)
        h.setSpacing(ds.space_sm)
        when = ("Journée" if ev.all_day else
                f"{ev.start:%H:%M}" + (f"–{ev.end:%H:%M}" if ev.end else ''))
        t = _label(when, 'row_time')
        t.setFixedWidth(ds.space_xxxl - ds.space_md)
        h.addWidget(t)
        title = _ElidedLabel(ev.title + (f"  ·  {ev.location}" if ev.location else ''))
        title.setObjectName('row_title')
        h.addWidget(title, 1)
        return row


# --------------------------------------------------------------------------- Messages
class _MessageRow(QFrame):
    clicked = Signal(object)

    def __init__(self, message: Message, mine: bool) -> None:
        super().__init__()
        self.message = message
        self.setObjectName('msg_row')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        unread = not mine and not message.is_read
        v = QVBoxLayout(self)
        v.setContentsMargins(ds.space_xs, ds.space_xxs, ds.space_xs, ds.space_xxs)
        v.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(ds.space_xs)
        dot = _label('●' if unread else '', 'dot')
        dot.setFixedWidth(ds.space_sm)
        top.addWidget(dot)
        who = (f"À {message.recipient_name or 'tous'}" if mine else message.sender_name)
        name = _ElidedLabel(who + ("  (diffusion)" if message.is_broadcast else ''))
        name.setObjectName('row_unread' if unread else 'row_title')
        top.addWidget(name, 1)
        when = _label(format_when(message.created_at), 'row_time')
        top.addWidget(when)
        v.addLayout(top)

        subject = message.subject or message.body
        preview = _ElidedLabel(subject if message.subject else message.body)
        preview.setObjectName('row_soft')
        preview.setContentsMargins(ds.space_sm + ds.space_xs, 0, 0, 0)
        v.addWidget(preview)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.message)
        super().mousePressEvent(event)


class MessagesCard(_Card):
    compose_requested = Signal()
    refresh_requested = Signal()
    message_clicked = Signal(object, bool)     # (message, mine)

    def __init__(self) -> None:
        super().__init__("Messages")
        self._inbox: list[Message] = []
        self._sent: list[Message] = []
        self._mine = False
        self._has_data = False

        self._badge = _label('', 'badge')
        self._badge.setAlignment(Qt.AlignCenter)
        self._badge.setFixedHeight(ds.space_md)
        self._badge.setMinimumWidth(ds.space_md)
        self._badge.hide()
        self.header.insertWidget(1, self._badge, 0, Qt.AlignVCenter)

        self._btn_in = self._tab_button("Reçus", False)
        self._btn_out = self._tab_button("Envoyés", True)
        self.header.insertWidget(2, self._btn_in)
        self.header.insertWidget(3, self._btn_out)
        self._btn_in.setChecked(True)

        self._refresh = self.icon_button('refresh', "Actualiser les messages")
        self._refresh.clicked.connect(self.refresh_requested)
        self.header.addWidget(self._refresh)
        self._compose = M3Button("Nouveau", theme_manager.phi_theme, ButtonVariant.TONAL)
        self._compose.setFixedHeight(ds.space_lg + ds.space_xs)
        self._compose.setIcon(md3_icon('edit', theme_manager.palette.primary, ds.space_md))
        self._compose.clicked.connect(self.compose_requested)
        self._compose.setEnabled(False)
        self.header.addWidget(self._compose)
        self.show_state("Chargement…")

    def _tab_button(self, text: str, mine: bool) -> QPushButton:
        btn = _button(text, 'tab_btn')
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda _checked=False, m=mine: self._select(m))
        return btn

    @safe_slot("MessagesCard._select")
    def _select(self, mine: bool) -> None:
        self._mine = mine
        self._btn_in.setChecked(not mine)
        self._btn_out.setChecked(mine)
        self._render()

    def set_data(self, inbox: list[Message], sent: list[Message]) -> None:
        self._inbox, self._sent, self._has_data = inbox, sent, True
        self._compose.setEnabled(True)
        self._render()

    def replace_inbox_message(self, message: Message) -> None:
        self._inbox = [message if m.id == message.id else m for m in self._inbox]
        self._render()

    def set_error(self, message: str) -> None:
        self._has_data = False
        self._compose.setEnabled(False)
        self._badge.hide()
        self.clear_rows()
        self.show_state(friendly_error(message), message)

    def unread_count(self) -> int:
        return sum(1 for m in self._inbox if not m.is_read)

    def rerender(self) -> None:
        if self._has_data:
            self._render()

    def _render(self) -> None:
        unread = self.unread_count()
        self._badge.setText(str(unread) if unread < 100 else '99+')
        self._badge.setVisible(unread > 0)
        self.clear_rows()
        items = self._sent if self._mine else self._inbox
        if not items:
            self.show_state("Aucun message envoyé." if self._mine else "Aucun message reçu.")
            return
        for m in items:
            row = _MessageRow(m, self._mine)
            row.clicked.connect(lambda msg, mine=self._mine: self.message_clicked.emit(msg, mine))
            self.rows.addWidget(row)
        self.rows.addStretch(1)
        self.show_rows()


# --------------------------------------------------------------------------- Panneau
class HomeInfoPanel(QWidget):
    refreshed = Signal(bool)
    unread_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None, service: MessagesService | None = None) -> None:
        super().__init__(parent)
        self._service = service or MessagesService()
        self._people: list[Person] = []
        self._can_broadcast = False
        self._pending = 0
        self._any_ok = False
        self._generation = 0
        self._started = False

        # Réduit (revue IHM du 27/09, cf. home_window.py) : les cartes défilent déjà en interne,
        # pas besoin de réserver autant de hauteur pour tenir sur un écran sans faire défiler la fenêtre.
        self.setMinimumHeight(ds.space_xxxl + ds.space_sm)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(ds.space_md)
        self._agenda = AgendaCard()
        self._messages = MessagesCard()
        row.addWidget(self._agenda, 5)
        row.addWidget(self._messages, 6)

        self._agenda.refresh_requested.connect(self.refresh)
        self._messages.refresh_requested.connect(self.refresh)
        self._messages.compose_requested.connect(self._compose)
        self._messages.message_clicked.connect(self._open_message)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self.refresh)

        self._apply_style()
        ds.theme_changed.connect(self._restyle)

    # ------------------------------------------------------------------ style
    @property
    def _STYLE(self) -> str:
        p = theme_manager.palette
        s = theme_manager.font_size
        return f"""
            QFrame#info_card {{ background: {p.surface}; border: 1px solid {p.outline_variant};
                                border-radius: {ds.radius_md}px; }}
            QWidget#card_body {{ background: transparent; }}
            QScrollArea {{ background: transparent; border: none; }}
            QLabel {{ background: transparent; border: none; color: {p.text_strong}; }}
            QLabel#card_title {{ font-size: {s(14)}px; font-weight: bold; }}
            QLabel#state {{ color: {p.text_soft}; font-size: {s(12)}px; }}
            QLabel#updated_at {{ color: {p.text_soft}; font-size: {s(12)}px; }}
            QLabel#day_head {{ color: {p.primary}; font-size: {s(12)}px; font-weight: bold;
                               padding-top: {ds.space_xs}px; }}
            QLabel#row_time {{ color: {p.text_soft}; font-size: {s(12)}px; }}
            QLabel#row_title {{ font-size: {s(13)}px; }}
            QLabel#row_unread {{ font-size: {s(13)}px; font-weight: bold; }}
            QLabel#row_soft {{ color: {p.text_soft}; font-size: {s(12)}px; }}
            QLabel#dot {{ color: {p.primary}; font-size: {s(10)}px; }}
            QLabel#badge {{ background: {p.error}; color: white; font-size: {s(11)}px; font-weight: bold;
                            border-radius: {ds.radius_sm}px; padding: 0 {ds.space_xs}px; }}
            QFrame#msg_row {{ background: transparent; border: none; border-radius: {ds.radius_xs}px; }}
            QFrame#msg_row:hover {{ background: {p.primary_container}; }}
            QPushButton#icon_btn {{ background: transparent; border: none; border-radius: {ds.radius_xs}px; }}
            QPushButton#icon_btn:hover {{ background: {p.primary_container}; }}
            QPushButton#tab_btn {{ background: transparent; color: {p.text_soft}; border: none;
                                   border-bottom: 2px solid transparent; font-size: {s(12)}px;
                                   padding: {ds.space_xxs}px {ds.space_xs}px; }}
            QPushButton#tab_btn:checked {{ color: {p.primary}; border-bottom: 2px solid {p.primary};
                                           font-weight: bold; }}
        """

    def _apply_style(self) -> None:
        self.setStyleSheet(self._STYLE)

    @safe_slot("HomeInfoPanel._restyle")
    def _restyle(self) -> None:
        self._apply_style()
        for btn in self.findChildren(QPushButton, 'icon_btn'):
            _Card.restyle_icon(btn)
        self._agenda.rerender()
        self._messages.rerender()

    # ------------------------------------------------------------------ cycle de vie
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._started:
            self._started = True
            self.refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._service.close()
        super().closeEvent(event)

    # ------------------------------------------------------------------ actualisation
    @safe_slot("HomeInfoPanel.refresh")
    def refresh(self) -> None:
        if self._pending:            # un cycle est déjà en cours
            return
        self._generation += 1
        gen = self._generation
        self._pending, self._any_ok = 2, False

        run_async(self, agenda_service_call,
                  lambda res, g=gen: self._on_agenda(g, res),
                  lambda msg, g=gen: self._on_agenda_failed(g, msg))

        uid = getattr(session, 'user_id', 0) or 0
        if not uid:
            self._messages.set_error("Connectez-vous pour lire vos messages.")
            self._cycle_done()
            return
        svc = self._service

        def load():
            return {'inbox': svc.inbox(uid, 40), 'sent': svc.sent(uid, 40),
                    'people': svc.people(uid), 'can_broadcast': svc.can_broadcast(uid)}
        run_async(self, load,
                  lambda res, g=gen: self._on_messages(g, res),
                  lambda msg, g=gen: self._on_messages_failed(g, msg))

    def _cycle_done(self, ok: bool = False) -> None:
        self._any_ok = self._any_ok or ok
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self.refreshed.emit(self._any_ok)

    @safe_slot("HomeInfoPanel._on_agenda")
    def _on_agenda(self, gen: int, result) -> None:
        if gen != self._generation:
            return
        events, days = result
        self._agenda.set_events(events, days)
        self._agenda.mark_updated()      # ici, pas dans set_events : rerender() le réutilise au restyle
        self._cycle_done(True)

    @safe_slot("HomeInfoPanel._on_agenda_failed")
    def _on_agenda_failed(self, gen: int, message: str) -> None:
        if gen != self._generation:
            return
        self._agenda.set_error(message)
        self._cycle_done(False)

    @safe_slot("HomeInfoPanel._on_messages")
    def _on_messages(self, gen: int, data: dict) -> None:
        if gen != self._generation:
            return
        self._people, self._can_broadcast = data['people'], data['can_broadcast']
        self._messages.set_data(data['inbox'], data['sent'])
        self._messages.mark_updated()
        self.unread_changed.emit(self._messages.unread_count())
        self._cycle_done(True)

    @safe_slot("HomeInfoPanel._on_messages_failed")
    def _on_messages_failed(self, gen: int, message: str) -> None:
        if gen != self._generation:
            return
        self._messages.set_error(message)
        self.unread_changed.emit(0)
        self._cycle_done(False)

    # ------------------------------------------------------------------ actions
    def _uid(self) -> int:
        return getattr(session, 'user_id', 0) or 0

    @safe_slot("HomeInfoPanel._compose")
    def _compose(self, recipient_id: int | None = None, subject: str = '') -> None:
        if not isinstance(recipient_id, int):      # le signal clicked() transmet un bool
            recipient_id = None
        dlg = ComposeDialog(self.window(), self._service, self._uid(), self._people,
                            self._can_broadcast, recipient_id, subject)
        dlg.exec()
        if dlg.sent:
            self._pending = 0        # nouvelle génération : les résultats du cycle précédent seront ignorés
            self.refresh()

    @safe_slot("HomeInfoPanel._open_message")
    def _open_message(self, message: Message, mine: bool) -> None:
        dlg = ReadDialog(self.window(), message, mine)
        if not mine and not message.is_read:
            read = dataclasses.replace(message, is_read=True)
            self._messages.replace_inbox_message(read)
            self.unread_changed.emit(self._messages.unread_count())
            uid = self._uid()
            run_async(self, lambda: self._service.mark_read(uid, [message.id]),
                      lambda _r: None, lambda _e: None)
        dlg.exec()
        if dlg.reply_wanted:
            subject = message.subject or ''
            self._compose(message.sender_id, subject if subject.lower().startswith('re:') else f"Re: {subject}")


def agenda_service_call():
    """(événements, nombre de jours) — bloquant, exécuté dans un thread de travail."""
    events = agenda_service.load_agenda()
    return events, agenda_service.load_settings()[1]
