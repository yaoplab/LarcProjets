"""Tests de la messagerie sur une base PostgreSQL temporaire locale (jamais sur le cloud).

La base est créée puis supprimée par la fixture ; le vrai script DbInit/cloud_messages.sql est rejoué.
Les tests sont ignorés si aucun PostgreSQL local n'est joignable (127.0.0.1:5432, postgres/postgres).
"""
from __future__ import annotations

import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")

from common.messages_service import (  # noqa: E402
    MessageRefused,
    MessagesService,
    MessagesUnavailable,
)

SQL_FILE = os.path.join(os.path.dirname(__file__), "..", "DbInit", "cloud_messages.sql")
_ADMIN = dict(host="127.0.0.1", port=5432, user="postgres", password="postgres", connect_timeout=3)

_USERS_DDL = """
CREATE TABLE larcauth_aecuser (
    id serial PRIMARY KEY, username text, first_name text, last_name text, is_active boolean DEFAULT true,
    type_teacher boolean DEFAULT false, type_coordonator boolean DEFAULT false, type_supervisor boolean DEFAULT false,
    type_secretary boolean DEFAULT false, type_director boolean DEFAULT false,
    type_student boolean DEFAULT false, type_parentutor boolean DEFAULT false);
INSERT INTO larcauth_aecuser (id, username, first_name, last_name, type_teacher, type_coordonator, type_student, is_active) VALUES
  (1, 'prof1', 'Ama',  'Kossi',  true,  false, false, true),
  (2, 'prof2', 'Yao',  'Adzo',   true,  false, false, true),
  (3, 'coord', 'Efua', 'Mensah', true,  true,  false, true),
  (4, 'eleve', 'Kofi', 'Eleve',  false, false, true,  true),
  (5, 'ancien','Old',  'Prof',   true,  false, false, false);
"""


@pytest.fixture(scope="module")
def dbname():
    try:
        admin = psycopg2.connect(dbname="postgres", **_ADMIN)
    except Exception:
        pytest.skip("PostgreSQL local indisponible")
    admin.autocommit = True
    name = f"larc_msgtest_{uuid.uuid4().hex[:8]}"
    admin.cursor().execute(f'CREATE DATABASE "{name}"')
    conn = psycopg2.connect(dbname=name, **_ADMIN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(_USERS_DDL)
    with open(SQL_FILE, encoding="utf-8") as f:
        ddl = f.read()
    cur.execute(ddl)
    cur.execute(ddl)                                # idempotence : rejeu sans erreur
    conn.close()
    yield name
    admin.cursor().execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.close()


@pytest.fixture()
def svc(dbname):
    def connect():
        c = psycopg2.connect(dbname=dbname, **_ADMIN)
        c.autocommit = True
        return c
    s = MessagesService(connect)
    yield s
    s.close()
    c = connect()
    c.cursor().execute("TRUNCATE larc_message RESTART IDENTITY CASCADE")
    c.close()


def test_individual_message_reaches_only_the_recipient(svc):
    svc.send(1, 3, "Question", "Bonjour, une question sur la grille.")
    assert [m.subject for m in svc.inbox(3)] == ["Question"]
    assert svc.inbox(2) == []                       # un tiers ne le voit pas
    assert svc.inbox(1) == []                       # l'expéditeur le retrouve dans « envoyés »
    assert [m.recipient_name for m in svc.sent(1)] == ["Efua Mensah"]


def test_reply_is_reciprocal(svc):
    svc.send(1, 3, "Question", "?")
    svc.send(3, 1, "Re: Question", "Voici la réponse.")
    assert svc.inbox(1)[0].sender_name == "Efua Mensah"
    assert svc.inbox(3)[0].sender_name == "Ama Kossi"


def test_broadcast_reaches_all_staff_but_not_students(svc):
    svc.send(3, None, "Réunion", "Réunion pédagogique jeudi.")
    assert [m.is_broadcast for m in svc.inbox(1)] == [True]
    assert [m.is_broadcast for m in svc.inbox(2)] == [True]
    assert svc.inbox(4) == []                       # élève : pas du personnel
    assert svc.inbox(5) == []                       # compte désactivé
    assert svc.inbox(3) == []                       # l'auteur voit sa diffusion dans « envoyés »
    assert svc.sent(3)[0].is_broadcast


def test_only_coordinators_can_broadcast(svc):
    assert svc.can_broadcast(3) and not svc.can_broadcast(1)
    with pytest.raises(MessageRefused):
        svc.send(1, None, "Spam", "Message à tous")
    assert svc.sent(1) == []


def test_read_receipts_are_per_user(svc):
    mid = svc.send(3, None, "Annonce", "Texte")
    assert svc.unread_count(1) == 1 and svc.unread_count(2) == 1
    svc.mark_read(1, [mid])
    svc.mark_read(1, [mid])                         # idempotent
    assert svc.unread_count(1) == 0 and svc.unread_count(2) == 1
    assert svc.inbox(1)[0].is_read and not svc.inbox(2)[0].is_read


def test_people_lists_active_staff_only(svc):
    names = [p.name for p in svc.people(exclude_user_id=1)]
    assert names == ["Efua Mensah", "Yao Adzo"]     # coordinateur d'abord ; ni élève, ni compte inactif, ni soi
    assert svc.people(exclude_user_id=1)[0].is_coordinator


@pytest.mark.parametrize("subject,body", [("x", ""), ("x", "   "), ("y" * 201, "ok"), ("x", "z" * 4001)])
def test_invalid_messages_are_refused(svc, subject, body):
    with pytest.raises(MessageRefused):
        svc.send(1, 3, subject, body)


def test_cannot_write_to_self(svc):
    with pytest.raises(MessageRefused):
        svc.send(1, 1, "moi", "moi")


def test_unreachable_cloud_is_reported_as_unavailable():
    def boom():
        raise MessagesUnavailable("hors ligne")
    with pytest.raises(MessagesUnavailable):
        MessagesService(boom).inbox(1)


def test_missing_tables_are_reported_as_unavailable():
    def connect_empty():
        admin = psycopg2.connect(dbname="postgres", **_ADMIN)
        admin.autocommit = True
        return admin                                # base « postgres » : pas de tables de messagerie
    with pytest.raises(MessagesUnavailable):
        MessagesService(connect_empty).inbox(1)
