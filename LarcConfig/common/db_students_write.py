"""Actions d'écriture sur les places élèves — UPDATE UNIQUEMENT (étape 4).

Principe gabarit : les 40 places de chaque classe pré-existent ; on ne fait
jamais d'INSERT ni de DELETE. Chaque action :
  - pose le contexte d'audit (larccommon.audit_context refresh + attach) ;
  - s'exécute dans UNE transaction explicite (BEGIN … COMMIT, ROLLBACK sur
    erreur ou garde non satisfaite) — les connexions LARC sont en
    autocommit=True, BEGIN ouvre donc une vraie transaction ;
  - verrouille la ligne élève (SELECT … FOR UPDATE) puis revérifie l'état
    attendu CÔTÉ SERVEUR : l'UPDATE porte l'état ancien dans son WHERE et un
    rowcount ≠ 1 annule tout (quelqu'un a modifié la place entre-temps) ;
  - renvoie un ActionResult avec les nombres de lignes modifiées.

Réinitialisation : le verdict (permis / admin / interdit) est RECALCULÉ dans
la transaction, au moment du clic, avec les mêmes requêtes que l'écran.
Depuis le 2026-10-27 (décision de Pat), elle efface TOUT de l'enfant
précédent : contenu des tables learner*, données personnelles, foyer, dossier
(cf. child_reset.py). Les tables « bulletin » ne voient jamais leur colonne
enabled modifiée ; liens parents et historiques (sans colonne d'activation)
sont signalés, jamais supprimés.
"""
from dataclasses import dataclass, field

from larccommon.database import db
from larccommon.logger import log_error

from LarcConfig.common import child_reset, db_students
from LarcConfig.common.campus import campus_of_classroom
from LarcConfig.common.student_slot_rules import (
    ADMIN,
    INTERDIT,
    classroom_of,
    clean_names,
    is_placeholder,
    normalize_name,
    placeholder_names,
    reset_verdict,
)

COLLEGE_LYCEE = 'college_lycee'

@dataclass
class ActionResult:
    ok: bool
    message: str
    counts: dict = field(default_factory=dict)   # {table ou clé: lignes modifiées}
    warnings: list = field(default_factory=list)


class _Refused(Exception):
    """Garde non satisfaite : on annule la transaction, message pour l'écran."""


def _conn():
    if not db.is_server_connected:
        return
    c = db.server_conn
    if not c:
        db.connect_intranet()
        c = db.server_conn
    return c


def _run(name: str, action) -> ActionResult:
    """Exécute action(cur) dans une transaction ; COMMIT si elle renvoie un
    ActionResult ok, ROLLBACK sinon (refus, erreur, garde non tenue)."""
    c = _conn()
    if not c:
        return ActionResult(False, "Base de données indisponible.")
    cur = c.cursor()
    try:
        from larccommon.audit_context import attach, refresh
        refresh()
        attach(c)
        cur.execute("BEGIN")
        result = action(cur)
        cur.execute("COMMIT")
        return result
    except _Refused as e:
        _rollback(cur)
        return ActionResult(False, str(e))
    except Exception as e:
        _rollback(cur)
        from larccommon.error_reporting import get_reporter
        get_reporter().report_exception()
        log_error(f"{name}: {e}")
        return ActionResult(False, f"Échec de l'enregistrement, rien n'a été modifié ({e}).")


def _rollback(cur):
    try:
        cur.execute("ROLLBACK")
    except Exception as e:  # connexion perdue : rien de plus à faire
        log_error(f"db_students_write rollback: {e}")


def _lock_slot(cur, student_id: int) -> dict:
    """Verrouille la place et renvoie son état actuel (+ état de la classe)."""
    cur.execute(
        "SELECT s.enabled, a.first_name, a.last_name, s.s_classroom_id, c.enabled "
        "FROM larcauth_student s "
        "JOIN larcauth_aecuser a ON a.id = s.aecuser_ptr_id "
        "LEFT JOIN larcauth_classroom c ON c.id = %s "
        "WHERE s.aecuser_ptr_id = %s FOR UPDATE OF s, a",
        (classroom_of(student_id), student_id),
    )
    r = cur.fetchone()
    if not r:
        raise _Refused(f"Place {student_id} introuvable.")
    enabled, first, last, s_cls, cls_enabled = r
    return {'enabled': bool(enabled), 'first_name': first, 'last_name': last,
            's_classroom_id': s_cls, 'classroom_enabled': bool(cls_enabled),
            'placeholder': is_placeholder(first, last, student_id)}


def _one(cur, what: str):
    if cur.rowcount != 1:
        raise _Refused(f"La place a été modifiée entre-temps ({what}) : rien n'a été enregistré. "
                       "Rechargez l'écran.")


def _set_names(cur, student_id: int, old: dict, last: str, first: str):
    """UPDATE des noms, gardé par les anciennes valeurs (concurrence)."""
    cur.execute(
        "UPDATE larcauth_aecuser SET last_name = %s, first_name = %s, updated = NOW() "
        "WHERE id = %s AND last_name = %s AND first_name = %s",
        (last, first, student_id, old['last_name'], old['first_name']),
    )
    _one(cur, "nom")


def _disable_enrolments(cur, student_id: int) -> int:
    cur.execute(
        "UPDATE larcauth_learner_has_termsubject SET enabled = FALSE, updated = NOW() "
        "WHERE fk_student_id = %s AND enabled = TRUE",
        (student_id,),
    )
    return cur.rowcount


def _duplicates_elsewhere(cur, student_id: int, last: str, first: str) -> list:
    """E5 : élèves actifs de même nom normalisé ailleurs (avertissement)."""
    cur.execute(
        "SELECT s.aecuser_ptr_id, s.s_classroom_id, a.last_name, a.first_name "
        "FROM larcauth_student s JOIN larcauth_aecuser a ON a.id = s.aecuser_ptr_id "
        "WHERE s.enabled = TRUE AND s.aecuser_ptr_id <> %s "
        "AND LOWER(TRIM(a.last_name)) = LOWER(%s)",
        (student_id, last),
    )
    key = normalize_name(last, first)
    return [(sid, cls) for sid, cls, ln, fn in cur.fetchall() if normalize_name(ln, fn) == key]


def _campus_key(student_id: int) -> str:
    cp = campus_of_classroom(classroom_of(student_id))
    if cp is None:
        raise _Refused(f"Classe {classroom_of(student_id)} hors des campus connus.")
    return cp.key


# ── 1. Inscrire un élève sur une place libre ─────────────────────────────

def inscrire(student_id: int, last_name: str, first_name: str) -> ActionResult:
    """Place libre (désactivée + nom gabarit) d'une classe activée → élève actif."""
    try:
        last, first = clean_names(last_name, first_name)
    except ValueError as e:
        return ActionResult(False, str(e))

    def action(cur):
        slot = _lock_slot(cur, student_id)
        if slot['enabled'] or not slot['placeholder']:
            raise _Refused("Cette place n'est plus libre : rien n'a été enregistré.")
        if not slot['classroom_enabled']:
            raise _Refused("La classe est désactivée : impossible d'y inscrire un élève.")
        warnings = [f"Même nom déjà actif : {sid} (classe {cls})"
                    for sid, cls in _duplicates_elsewhere(cur, student_id, last, first)]
        _set_names(cur, student_id, slot, last, first)
        cur.execute(
            "UPDATE larcauth_student SET enabled = TRUE, s_classroom_id = %s, updated_s = NOW() "
            "WHERE aecuser_ptr_id = %s AND enabled = FALSE",
            (classroom_of(student_id), student_id),
        )
        _one(cur, "inscription")
        return ActionResult(True, f"{last} {first} inscrit(e) sur la place {student_id}.",
                            {'larcauth_aecuser': 1, 'larcauth_student': 1}, warnings)

    return _run("inscrire", action)


# ── 2. Présent / absent (actif ↔ parti) ──────────────────────────────────

def set_present(student_id: int, enabled: bool) -> ActionResult:
    """Absent : enabled=FALSE, et pour le collège/lycée désinscription de
    toutes ses matières. Présent : enabled=TRUE seulement (réinscription aux
    matières à faire dans « Matières par classe »)."""
    def action(cur):
        slot = _lock_slot(cur, student_id)
        if slot['placeholder'] and not slot['enabled']:
            raise _Refused("Place libre : utiliser « Inscrire un élève ».")
        if slot['enabled'] == enabled:
            raise _Refused("L'élève est déjà dans cet état : rien n'a été modifié.")
        if enabled and not slot['classroom_enabled']:
            raise _Refused("La classe est désactivée : impossible d'y réactiver un élève.")
        counts = {}
        if not enabled and _campus_key(student_id) == COLLEGE_LYCEE:
            counts['larcauth_learner_has_termsubject'] = _disable_enrolments(cur, student_id)
        cur.execute(
            "UPDATE larcauth_student SET enabled = %s, updated_s = NOW() "
            "WHERE aecuser_ptr_id = %s AND enabled = %s",
            (enabled, student_id, not enabled),
        )
        _one(cur, "présence")
        counts['larcauth_student'] = 1
        name = f"{(slot['last_name'] or '').strip()} {(slot['first_name'] or '').strip()}"
        if enabled:
            return ActionResult(True, f"{name} marqué(e) présent(e). Réinscrire ses matières "
                                      "dans « Matières par classe » si besoin.", counts)
        n = counts.get('larcauth_learner_has_termsubject')
        extra = f" Désinscrit(e) de {n} matière(s)." if n is not None else ""
        return ActionResult(True, f"{name} marqué(e) absent(e) (parti).{extra}", counts)

    return _run("set_present", action)


# ── 3. Corriger nom / prénom ─────────────────────────────────────────────

def rename(student_id: int, last_name: str, first_name: str) -> ActionResult:
    """Actif ou parti uniquement — jamais sur une place libre (inscrire)."""
    try:
        last, first = clean_names(last_name, first_name)
    except ValueError as e:
        return ActionResult(False, str(e))

    def action(cur):
        slot = _lock_slot(cur, student_id)
        if slot['placeholder']:
            raise _Refused("Place libre ou nom gabarit : utiliser « Inscrire un élève » "
                           "(après réinitialisation si besoin).")
        warnings = []
        if slot['enabled']:
            warnings = [f"Même nom déjà actif : {sid} (classe {cls})"
                        for sid, cls in _duplicates_elsewhere(cur, student_id, last, first)]
        _set_names(cur, student_id, slot, last, first)
        return ActionResult(True, f"Nom enregistré : {last} {first}.", {'larcauth_aecuser': 1},
                            warnings)

    return _run("rename", action)


# ── 4. Réinitialiser la place ────────────────────────────────────────────

def reinitialiser(student_id: int, accept_notes_erase: bool = False) -> ActionResult:
    """Remet la place à l'état d'une place libre jamais utilisée.

    Verdict recalculé dans la transaction : interdit → refus ; admin → refus
    si accept_notes_erase est faux (confirmation admin non donnée). Sinon,
    tout ce qui appartient à l'enfant est remis à vide (child_reset) :
    notes, observations et commentaires de toutes les tables learner*,
    inscriptions, données personnelles, dossier, foyer."""
    def action(cur):
        slot = _lock_slot(cur, student_id)
        if slot['placeholder'] and not slot['enabled']:
            raise _Refused("Place déjà libre : rien à faire.")
        campus_key = _campus_key(student_id)
        notes = db_students.notes_by_table(student_id, campus_key, cur)
        bulletin = db_students.bulletin_of(student_id, campus_key, cur)
        verdict = reset_verdict(sum(notes.values()) > 0, bulletin)
        if verdict == INTERDIT:
            raise _Refused("Réinitialisation interdite : un bulletin a été produit pour cet élève.")
        if verdict == ADMIN and not accept_notes_erase:
            raise _Refused("Des notes existent : la réinitialisation demande la confirmation "
                           "d'un administrateur (effacement des notes).")
        counts = {}
        # 1. Contenu de l'enfant dans les tables learner* (notes, observations…)
        for table in child_reset.RESET_TABLES:
            cols = child_reset.content_columns(table, cur)
            if not cols:
                continue
            cur.execute(child_reset.build_content_reset_sql(table, cols), (student_id,))
            if cur.rowcount:
                counts[table] = cur.rowcount
        # 2. Inscriptions aux matières
        counts['larcauth_learner_has_termsubject'] = _disable_enrolments(cur, student_id)
        # 3. Identité et données personnelles (gardé par les anciens noms)
        last, first = placeholder_names(student_id)
        cur.execute(child_reset.build_aecuser_reset_sql(campus_key),
                    (last, first, student_id, slot['last_name'], slot['first_name']))
        _one(cur, "données personnelles")
        # 4. Dossier élève (gardé par l'état ancien)
        cur.execute(child_reset.STUDENT_RESET_SQL,
                    (classroom_of(student_id), student_id, slot['enabled']))
        _one(cur, "réinitialisation")
        counts['larcauth_aecuser'] = 1
        counts['larcauth_student'] = 1
        # 5. Foyer propre à l'élève (jamais un foyer partagé)
        warnings = []
        cur.execute(child_reset.FOYER_RESET_SQL, (student_id,))
        if cur.rowcount:
            counts['foyer'] = cur.rowcount
        else:
            warnings.append("Foyer absent ou partagé avec un autre compte : non modifié.")
        # 6. Liens parents de CET élève : seule suppression autorisée (accord de
        #    Pat, 2026-11-02 — table de liaison sans colonne d'activation)
        cur.execute(child_reset.PARENT_LINKS_DELETE_SQL, (student_id,))
        if cur.rowcount:
            counts['larcauth_student_parent'] = cur.rowcount
        # 7. Historiques sans colonne d'activation : conservés et signalés
        for table, column, label in child_reset.KEPT_TABLES:
            cur.execute(child_reset.build_kept_count_sql(table, column), (student_id,))
            r = cur.fetchone()
            if r and r[0]:
                warnings.append(f"{r[0]} {label} conservé(s) ({table}) : retrait impossible "
                                "sans suppression — à traiter à part.")
        erased = sum(v for k, v in counts.items() if k in child_reset.RESET_TABLES)
        msg = f"Place {student_id} libérée : données de l'enfant remises à zéro."
        if erased:
            msg += f" {erased} ligne(s) de notes / observations vidée(s)."
        if counts.get('larcauth_student_parent'):
            msg += f" {counts['larcauth_student_parent']} lien(s) parent supprimé(s)."
        return ActionResult(True, msg, counts, warnings)

    return _run("reinitialiser", action)
