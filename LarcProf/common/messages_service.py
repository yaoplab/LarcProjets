"""Messagerie LarcProf — accès au cloud (Supabase) uniquement.

Pas de copie locale : sans Internet, la messagerie est indisponible et les anciens
messages ne sont pas visibles (choix produit). La connexion est propre à ce module :
elle ne change pas le mode serveur (Intranet/Cloud) du reste de l'application.

Modèle (DbInit/cloud_messages.sql) :
  * message individuel : recipient_id renseigné ;
  * diffusion          : recipient_id NULL, réservée aux coordinateurs, lue par tout le personnel.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .logger import log as _log

try:
    import psycopg2
    from psycopg2 import errors as pg_errors
except ImportError:  # pragma: no cover - psycopg2 est une dépendance dure de LarcProf
    psycopg2 = None
    pg_errors = None

STAFF_FLAGS = ("type_teacher", "type_coordonator", "type_supervisor",
               "type_secretary", "type_director")
_STAFF_SQL = "(" + " OR ".join(f"u.{f}" for f in STAFF_FLAGS) + ")"

MAX_SUBJECT = 200
MAX_BODY = 4000


class MessagesUnavailable(Exception):
    """Cloud injoignable (pas d'Internet, identifiants absents, tables non créées)."""


class MessageRefused(Exception):
    """Envoi refusé (message vide, diffusion par un non-coordinateur…)."""


@dataclass(frozen=True)
class Message:
    id: int
    sender_id: int
    sender_name: str
    recipient_id: Optional[int]      # None = diffusion
    recipient_name: str              # '' pour une diffusion
    subject: str
    body: str
    created_at: datetime
    is_read: bool

    @property
    def is_broadcast(self) -> bool:
        return self.recipient_id is None


@dataclass(frozen=True)
class Person:
    id: int
    name: str
    is_coordinator: bool


def _display_name(first: str | None, last: str | None, username: str | None = None) -> str:
    name = f"{(first or '').strip()} {(last or '').strip()}".strip()
    return name or (username or '').strip() or '?'


def default_connect() -> "psycopg2.extensions.connection":
    """Connexion Supabase à partir de config.ini ([SupabaseDatabase])."""
    if psycopg2 is None:
        raise MessagesUnavailable("psycopg2 non installé")
    from .database import db
    params = db._pg_params('SupabaseDatabase')
    if not params.get('password'):
        raise MessagesUnavailable("Identifiants cloud absents de config.ini")
    params['sslmode'] = 'require'
    params['application_name'] = 'LarcProf-messages'
    conn = psycopg2.connect(**params)
    conn.autocommit = True
    return conn


class MessagesService:
    """Toutes les méthodes sont bloquantes : à appeler depuis un thread de travail."""

    def __init__(self, connect: Callable[[], object] = default_connect) -> None:
        self._connect = connect
        self._conn = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ connexion
    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception as exc:  # noqa: BLE001 — connexion déjà morte : rien à faire de plus
                _log(f"messages: fermeture de la connexion : {exc}")
            self._conn = None

    def _run(self, fn, retry: bool = True):
        """Exécute fn(cursor). Si le lien est tombé, une reconnexion est tentée une fois
        (jamais pour un envoi : on ne risque pas un message en double)."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None or getattr(self._conn, 'closed', 0):
                        self._conn = self._connect()
                    with self._conn.cursor() as cur:
                        return fn(cur)
                except (MessagesUnavailable, MessageRefused):
                    raise
                except pg_errors.InsufficientPrivilege as exc:
                    raise MessageRefused(
                        "Seul un coordinateur peut envoyer un message à tous.") from exc
                except pg_errors.UndefinedTable as exc:
                    raise MessagesUnavailable(
                        "La messagerie n'est pas encore installée sur le serveur cloud.") from exc
                except pg_errors.IntegrityError as exc:
                    raise MessageRefused("Message refusé par le serveur (destinataire invalide ?).") from exc
                except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
                    self._close_locked()
                    if attempt == 2 or not retry:
                        raise MessagesUnavailable(str(exc).strip() or type(exc).__name__) from exc
                except psycopg2.Error as exc:
                    raise MessagesUnavailable(str(exc).strip() or type(exc).__name__) from exc

    # ------------------------------------------------------------------ lecture
    _SELECT = """
        SELECT m.id, m.sender_id, s.first_name, s.last_name, s.username,
               m.recipient_id, r.first_name, r.last_name, r.username,
               m.subject, m.body, m.created_at,
               (rd.message_id IS NOT NULL) AS is_read
        FROM larc_message m
        JOIN larcauth_aecuser s ON s.id = m.sender_id
        LEFT JOIN larcauth_aecuser r ON r.id = m.recipient_id
        LEFT JOIN larc_message_read rd ON rd.message_id = m.id AND rd.user_id = %(uid)s
    """

    @staticmethod
    def _to_message(row) -> Message:
        (mid, sid, sf, sl, su, rid, rf, rl, ru, subject, body, created, is_read) = row
        return Message(
            id=mid, sender_id=sid, sender_name=_display_name(sf, sl, su),
            recipient_id=rid,
            recipient_name='' if rid is None else _display_name(rf, rl, ru),
            subject=subject or '', body=body, created_at=created, is_read=bool(is_read))

    def inbox(self, user_id: int, limit: int = 50) -> list[Message]:
        """Messages reçus : individuels + diffusions (personnel seulement), du plus récent au plus ancien.
        Les messages que l'on a soi-même diffusés n'apparaissent pas ici (voir sent())."""
        def q(cur):
            cur.execute(
                self._SELECT + f"""
                WHERE m.deleted_at IS NULL AND m.sender_id <> %(uid)s
                  AND (m.recipient_id = %(uid)s
                       OR (m.recipient_id IS NULL AND EXISTS (
                            SELECT 1 FROM larcauth_aecuser u
                            WHERE u.id = %(uid)s AND u.is_active AND {_STAFF_SQL})))
                ORDER BY m.created_at DESC, m.id DESC LIMIT %(lim)s""",
                {'uid': user_id, 'lim': limit})
            return [self._to_message(r) for r in cur.fetchall()]
        return self._run(q)

    def sent(self, user_id: int, limit: int = 50) -> list[Message]:
        def q(cur):
            cur.execute(
                self._SELECT + """
                WHERE m.deleted_at IS NULL AND m.sender_id = %(uid)s
                ORDER BY m.created_at DESC, m.id DESC LIMIT %(lim)s""",
                {'uid': user_id, 'lim': limit})
            return [self._to_message(r) for r in cur.fetchall()]
        return self._run(q)

    def unread_count(self, user_id: int) -> int:
        return sum(1 for m in self.inbox(user_id, limit=200) if not m.is_read)

    def people(self, exclude_user_id: int | None = None) -> list[Person]:
        """Personnel actif pouvant recevoir un message individuel."""
        def q(cur):
            cur.execute(
                f"""SELECT u.id, u.first_name, u.last_name, u.username, u.type_coordonator
                    FROM larcauth_aecuser u
                    WHERE u.is_active AND {_STAFF_SQL} AND u.id <> COALESCE(%s, -1)
                    ORDER BY u.type_coordonator DESC, lower(u.last_name), lower(u.first_name)""",
                (exclude_user_id,))
            return [Person(i, _display_name(first, last, un), bool(c))
                    for i, first, last, un, c in cur.fetchall()]
        return self._run(q)

    def can_broadcast(self, user_id: int) -> bool:
        def q(cur):
            cur.execute("SELECT 1 FROM larcauth_aecuser u WHERE u.id = %s AND u.is_active "
                        "AND u.type_coordonator", (user_id,))
            return cur.fetchone() is not None
        return self._run(q)

    # ------------------------------------------------------------------ écriture
    def send(self, sender_id: int, recipient_id: int | None, subject: str, body: str) -> int:
        """Envoie un message individuel (recipient_id) ou une diffusion (recipient_id=None)."""
        subject, body = (subject or '').strip(), (body or '').strip()
        if not body:
            raise MessageRefused("Le message est vide.")
        if len(subject) > MAX_SUBJECT:
            raise MessageRefused(f"L'objet dépasse {MAX_SUBJECT} caractères.")
        if len(body) > MAX_BODY:
            raise MessageRefused(f"Le message dépasse {MAX_BODY} caractères.")
        if recipient_id is not None and recipient_id == sender_id:
            raise MessageRefused("Vous ne pouvez pas vous écrire à vous-même.")

        def q(cur):
            cur.execute(
                "INSERT INTO larc_message (sender_id, recipient_id, subject, body) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (sender_id, recipient_id, subject, body))
            return cur.fetchone()[0]
        return self._run(q, retry=False)

    def mark_read(self, user_id: int, message_ids: list[int]) -> None:
        if not message_ids:
            return

        def q(cur):
            cur.executemany(
                "INSERT INTO larc_message_read (message_id, user_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING", [(mid, user_id) for mid in message_ids])
        self._run(q)
