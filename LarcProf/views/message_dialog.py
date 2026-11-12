"""Fenêtres de la messagerie : rédaction (individuel ou diffusion) et lecture.

Les envois passent par MessagesService, hors du thread d'interface (run_async) :
la fenêtre reste réactive, et une coupure Internet s'affiche dans la fenêtre, sans popup.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from larccommon.design_system import ds
from larccommon.safe_slot import safe_slot
from phibuilder.widgets import (
    ButtonVariant,
    M3Button,
    M3ComboBox,
    M3Dialog,
    M3Label,
    M3TextEdit,
    M3TextField,
)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from common.async_task import run_async
from common.messages_service import MAX_BODY, MAX_SUBJECT, Message, MessagesService, Person
from common.theme import theme_manager

BROADCAST_LABEL = "Tous les utilisateurs (diffusion)"


def format_when(dt: Optional[datetime]) -> str:
    if dt is None:
        return ''
    local = dt.astimezone() if dt.tzinfo else dt
    return local.strftime('%d/%m/%Y %H:%M')


def _base_style() -> str:
    p = theme_manager.palette
    return (f"QDialog {{ background: {p.surface}; color: {p.text_strong}; }} "
            f"QLabel {{ color: {p.text_strong}; }} "
            f"QLabel#soft {{ color: {p.text_soft}; }} "
            f"QLabel#error {{ color: {p.error}; font-weight: bold; }}")


def _label(text: str = '', name: str = '') -> M3Label:
    """M3Label stylé par la QSS de la fenêtre (_base_style, sélecteurs #objectName) : sa QSS
    propre est retirée, sinon elle masquerait celle de la fenêtre."""
    lbl = M3Label(text, theme_manager.phi_theme)
    lbl.setStyleSheet('')
    if name:
        lbl.setObjectName(name)
    return lbl


class _DialogBase(M3Dialog):
    def __init__(self, parent: QWidget | None, title: str) -> None:
        # Sans thème, M3Dialog ne pose ni mise en page ni style : on les fournit ici.
        super().__init__(parent, title=title)
        self.setMinimumWidth(ds.space_xxxl * 4)
        self.setStyleSheet(_base_style())
        self._theme = theme_manager.phi_theme
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(ds.space_lg, ds.space_lg, ds.space_lg, ds.space_md)
        self._root.setSpacing(ds.space_sm)

    def _soft(self, text: str) -> M3Label:
        return _label(text, 'soft')


# --------------------------------------------------------------------------- rédaction
class ComposeDialog(_DialogBase):
    """Nouveau message. `people` : personnel destinataire ; la diffusion n'est proposée
    qu'aux coordinateurs (`can_broadcast`), et refusée par le serveur sinon."""

    def __init__(self, parent: QWidget | None, service: MessagesService, sender_id: int,
                 people: list[Person], can_broadcast: bool,
                 recipient_id: int | None = None, subject: str = '') -> None:
        super().__init__(parent, "Nouveau message")
        self._service = service
        self._sender_id = sender_id
        self._targets: list[Optional[int]] = []
        self.sent = False

        self._root.addWidget(self._soft("À"))
        self._to = M3ComboBox(theme=self._theme)
        if can_broadcast:
            self._targets.append(None)
            self._to.addItem(BROADCAST_LABEL)
        for person in people:
            self._targets.append(person.id)
            self._to.addItem(person.name + (" (coordinateur)" if person.is_coordinator else ""))
        if recipient_id in self._targets:
            self._to.setCurrentIndex(self._targets.index(recipient_id))
        self._root.addWidget(self._to)

        self._root.addWidget(self._soft("Objet"))
        self._subject = M3TextField(subject, theme=self._theme)
        self._subject.setMaxLength(MAX_SUBJECT)
        self._root.addWidget(self._subject)

        self._root.addWidget(self._soft("Message"))
        self._body = M3TextEdit(theme=self._theme)
        self._body.setMinimumHeight(ds.space_xxxl + ds.space_xl)
        self._body.setAcceptRichText(False)
        self._body.textChanged.connect(self._on_body_changed)
        self._root.addWidget(self._body)

        self._counter = self._soft('')
        self._counter.setAlignment(Qt.AlignRight)
        self._root.addWidget(self._counter)

        self._error = _label('', 'error')
        self._error.setWordWrap(True)
        self._error.hide()
        self._root.addWidget(self._error)

        row = QHBoxLayout()
        row.addStretch(1)
        self._cancel = M3Button("Annuler", self._theme, ButtonVariant.TEXT)
        self._cancel.clicked.connect(self.reject)
        row.addWidget(self._cancel)
        self._send = M3Button("Envoyer", self._theme, ButtonVariant.FILLED)
        self._send.clicked.connect(self._on_send)
        row.addWidget(self._send)
        self._root.addLayout(row)

        self._on_body_changed()
        (self._body if subject else self._subject).setFocus()

    @safe_slot("ComposeDialog._on_body_changed")
    def _on_body_changed(self) -> None:
        n = len(self._body.toPlainText())
        self._counter.setText(f"{n} / {MAX_BODY}")
        pal = theme_manager.palette
        self._counter.setStyleSheet(f"color: {pal.error if n > MAX_BODY else pal.text_soft};")

    def _show_error(self, text: str) -> None:
        self._error.setText(text)
        self._error.setVisible(bool(text))

    @safe_slot("ComposeDialog._on_send")
    def _on_send(self) -> None:
        idx = self._to.currentIndex()
        if idx < 0 or idx >= len(self._targets):
            self._show_error("Choisissez un destinataire.")
            return
        recipient = self._targets[idx]
        subject, body = self._subject.text(), self._body.toPlainText()
        if not body.strip():
            self._show_error("Le message est vide.")
            return
        self._show_error('')
        self._send.setEnabled(False)
        self._send.setText("Envoi…")
        run_async(self, lambda: self._service.send(self._sender_id, recipient, subject, body),
                  self._on_sent, self._on_send_failed)

    @safe_slot("ComposeDialog._on_sent")
    def _on_sent(self, _message_id) -> None:
        self.sent = True
        self.accept()

    @safe_slot("ComposeDialog._on_send_failed")
    def _on_send_failed(self, message: str) -> None:
        self._send.setEnabled(True)
        self._send.setText("Envoyer")
        self._show_error(message)


# --------------------------------------------------------------------------- lecture
class ReadDialog(_DialogBase):
    """Lecture d'un message. `reply_requested` : l'appelant ouvre la rédaction pré-remplie."""

    def __init__(self, parent: QWidget | None, message: Message, mine: bool) -> None:
        super().__init__(parent, message.subject or "(sans objet)")
        self.reply_wanted = False
        who = (f"À : {message.recipient_name or BROADCAST_LABEL}" if mine
               else f"De : {message.sender_name}")
        head = _label(f"<b>{_esc(message.subject or '(sans objet)')}</b>")
        head.setWordWrap(True)
        self._root.addWidget(head)
        kind = "  ·  diffusion" if message.is_broadcast and not mine else ""
        self._root.addWidget(self._soft(f"{who}  ·  {format_when(message.created_at)}{kind}"))

        body = M3TextEdit(theme=self._theme)
        body.setReadOnly(True)
        body.setPlainText(message.body)
        body.setMinimumHeight(ds.space_xxxl + ds.space_xl)
        self._root.addWidget(body, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        if not mine:
            reply = M3Button("Répondre", self._theme, ButtonVariant.TONAL)
            reply.clicked.connect(self._on_reply)
            row.addWidget(reply)
        close = M3Button("Fermer", self._theme, ButtonVariant.FILLED)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        self._root.addLayout(row)

    @safe_slot("ReadDialog._on_reply")
    def _on_reply(self) -> None:
        self.reply_wanted = True
        self.accept()


def _esc(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
