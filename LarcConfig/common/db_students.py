"""Slots élèves par classe et par campus — LECTURE SEULE (SELECT uniquement).

Étape 3 du plan « campus » (2026-10-26) : aucune écriture ici. Les actions
(réinitialiser un slot, etc.) viendront dans un module séparé, avec aperçu et
confirmation.

Preuves « notes » / « bulletin » par élève (verdict de réinitialisation) :
  - Maternelle : notes = larcauth_learnermat_has_devpers_unit et
    larcauth_learnermat_has_subjectevals_unit (a/pa/na à TRUE, status ≠ '-',
    value ≠ 0) ; bulletin = larcauth_learnermat_has_unit_period avec moyenne
    ou observation saisie (enabled y vaut TRUE partout : inutilisable).
  - Primaire : notes = colonnes f_*, s_*, c_*, note_on_max, unit_mark_on_max de
    larcauth_learnerprim_has_unit_period + colonnes de notes de
    larcauth_learnerpp_has_termsubjectpp ; bulletin = learnerprim.enabled.
  - Collège & Lycée : notes = colonnes de notes PEI/DP (via
    learner_has_termsubject) + larcauth_learner_has_termothersubject ;
    bulletin = larcauth_learner_has_term.enabled.
Une valeur « saisie » = non NULL, non vide, différente du défaut de la colonne
(et de '-' pour les statuts, FALSE pour les booléens). Lecture impossible
d'une table → None (inconnu) pour les élèves de la classe.
"""
import re

from larccommon.database import db
from larccommon.logger import log_error

from LarcConfig.common import db_enrolment
from LarcConfig.common.campus import Campus
from LarcConfig.common.enrolment_rules import TERM_SLOTS
from LarcConfig.common.student_slot_rules import SLOTS_PER_CLASS, normalize_name


def _conn():
    if not db.is_server_connected:
        return
    c = db.server_conn
    if not c:
        db.connect_intranet()
        c = db.server_conn
    return c


def _rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _report(where: str, e: Exception):
    from larccommon.error_reporting import get_reporter
    get_reporter().report_exception()
    log_error(f"{where}: {e}")


def _in_list(values) -> str:
    return ", ".join(["%s"] * len(values))


# ── Slots ────────────────────────────────────────────────────────────────

_SLOT_SELECT = (
    "SELECT s.aecuser_ptr_id AS id, a.first_name, a.last_name, s.enabled, "
    "s.s_classroom_id "
    "FROM larcauth_student s "
    "JOIN larcauth_aecuser a ON a.id = s.aecuser_ptr_id "
)


def get_slots(classroom_id: int) -> list[dict]:
    """Les slots (normalement 40) d'une classe, triés par ID."""
    c = _conn()
    if not c:
        return []
    try:
        cur = c.cursor()
        cur.execute(
            _SLOT_SELECT + "WHERE s.aecuser_ptr_id BETWEEN %s AND %s ORDER BY s.aecuser_ptr_id",
            (classroom_id * 100 + 1, classroom_id * 100 + SLOTS_PER_CLASS),
        )
        return _rows(cur)
    except Exception as e:
        _report("get_slots", e)
        return []


def get_slots_for_classes(classroom_ids: list) -> list[dict]:
    """Slots de plusieurs classes en une requête (vue d'ensemble d'un campus)."""
    c = _conn()
    if not c or not classroom_ids:
        return []
    try:
        cur = c.cursor()
        cur.execute(
            _SLOT_SELECT
            + f"WHERE s.aecuser_ptr_id / 100 IN ({_in_list(classroom_ids)}) "
            "AND s.aecuser_ptr_id %% 100 BETWEEN 1 AND %s "
            "ORDER BY s.aecuser_ptr_id",
            tuple(classroom_ids) + (SLOTS_PER_CLASS,),
        )
        return _rows(cur)
    except Exception as e:
        _report("get_slots_for_classes", e)
        return []


def get_duplicates() -> dict:
    """E5 : {id: ((autre_id, autre_classe), …)} pour les élèves actifs de toute
    l'école partageant nom + prénom normalisés (espaces, casse ; accents gardés)."""
    c = _conn()
    if not c:
        return {}
    try:
        cur = c.cursor()
        cur.execute(_SLOT_SELECT + "WHERE s.enabled = TRUE")
        groups = {}
        for r in _rows(cur):
            groups.setdefault(normalize_name(r['last_name'], r['first_name']), []).append(
                (r['id'], r['s_classroom_id']))
        out = {}
        for members in groups.values():
            if len(members) < 2:
                continue
            for sid, _cls in members:
                out[sid] = tuple(m for m in members if m[0] != sid)
        return out
    except Exception as e:
        _report("get_duplicates", e)
        return {}


# ── Trimestre courant et inscriptions (E7) ───────────────────────────────

def resolve_term_id() -> int:
    """Trimestre courant, comme db_access.get_stats : créneau TERM_SLOTS du
    current_term_number de l'année active s'il porte des matières, sinon le
    trimestre qui en porte le plus. 0 si indéterminable."""
    c = _conn()
    if not c:
        return 0
    try:
        cur = c.cursor()
        cur.execute("SELECT current_term_number FROM larcauth_academicyear ORDER BY s_id DESC LIMIT 1")
        r = cur.fetchone()
        slot = TERM_SLOTS.get(r[0]) if r else None
        if slot:
            cur.execute("SELECT COUNT(*) FROM larcauth_classroom_termsubject "
                        "WHERE enabled = TRUE AND fk_term_id = %s", (slot,))
            if cur.fetchone()[0] > 0:
                return slot
        cur.execute("SELECT fk_term_id FROM larcauth_classroom_termsubject WHERE enabled = TRUE "
                    "GROUP BY fk_term_id ORDER BY COUNT(*) DESC LIMIT 1")
        r = cur.fetchone()
        return r[0] if r else 0
    except Exception as e:
        _report("resolve_term_id", e)
        return 0


def get_active_subject_counts(classroom_ids: list, term_id: int) -> dict:
    """{id élève: nb d'inscriptions actives (élève et matière-classe) au trimestre}."""
    c = _conn()
    if not c or not classroom_ids or not term_id:
        return {}
    try:
        cur = c.cursor()
        cur.execute(
            "SELECT lht.fk_student_id AS id, COUNT(*) AS n "
            "FROM larcauth_learner_has_termsubject lht "
            "JOIN larcauth_classroom_termsubject cts ON cts.id = lht.fk_classroom_termsubject_id "
            "WHERE lht.enabled = TRUE AND cts.enabled = TRUE AND cts.fk_term_id = %s "
            f"AND lht.fk_student_id / 100 IN ({_in_list(classroom_ids)}) "
            "GROUP BY lht.fk_student_id",
            (term_id,) + tuple(classroom_ids),
        )
        return {r['id']: r['n'] for r in _rows(cur)}
    except Exception as e:
        _report("get_active_subject_counts", e)
        return {}


# ── Vue d'ensemble d'un campus ───────────────────────────────────────────

def get_classes_overview(campus: Campus) -> list[dict]:
    """Une ligne par classe du campus (principale + B) : id, label, enabled,
    program_sigle, level_label, slots, actifs, libres, partis, anomalies."""
    from LarcConfig.common.student_slot_rules import assess_slots, summarize
    # Tri stable par programme puis ID : la principale (…1) avant sa « - B »
    # (…2), quel que soit index_in_level (incohérent sur certaines classes).
    classrooms = sorted(db_enrolment.get_classrooms(campus=campus),
                        key=lambda cl: (cl.get('program_sigle') or '', cl['id']))
    ids = [cl['id'] for cl in classrooms]
    slots = get_slots_for_classes(ids)
    duplicates = get_duplicates()
    needs_subjects = campus.key == 'college_lycee'
    subjects = get_active_subject_counts(ids, resolve_term_id()) if needs_subjects else {}
    by_class = {}
    for s in slots:
        by_class.setdefault(s['id'] // 100, []).append(s)
    out = []
    for cl in classrooms:
        mine = by_class.get(cl['id'], [])
        rows = assess_slots(mine, bool(cl['enabled']), duplicates, subjects, needs_subjects)
        summary = summarize(rows, len(mine))
        out.append({
            'id': cl['id'], 'label': cl['label'], 'enabled': bool(cl['enabled']),
            'program_sigle': cl.get('program_sigle'), 'level_label': cl.get('level_label'),
            'slots': len(mine), 'actifs': summary['actif'], 'libres': summary['libre'],
            'partis': summary['parti'], 'anomalies': summary['anomalies'],
            'codes': summary['codes'],
        })
    return out


# ── Preuves notes / bulletin ─────────────────────────────────────────────

_COLS_CACHE: dict = {}  # {table: [(colonne, type, défaut)]} — par session

# Colonnes de notes, par table (motif sur le nom, insensible à la casse)
_NOTE_PATTERNS = {
    'larcauth_learnermat_has_devpers_unit': r'^(a|pa|na|status)$',
    'larcauth_learnermat_has_subjectevals_unit': r'^(a|pa|na|status|value)$',
    'larcauth_learnerprim_has_unit_period': r'^([fsc]_|note_on_max$|unit_mark_on_max$)',
    'larcauth_learnerpp_has_termsubjectpp': r'(note|jgt_)',
    'larcauth_learnerpei_has_termsubjectpei': r'(note|jgt_)',
    'larcauth_learnerdp_has_termsubjectdp': r'(note|jgt_|^moy_|^bacblanc|^cc_on|^cpei$)',
    'larcauth_learner_has_termothersubject': r'^(mark_|os_note_)',
}
_EXCLUDE_RE = re.compile(r'(observation|obsersation|_checked$)', re.IGNORECASE)

# Tables « filles » sans fk_student_id : élève via learner_has_termsubject
_VIA_TERMSUBJECT = {
    'larcauth_learnerpp_has_termsubjectpp',
    'larcauth_learnerpei_has_termsubjectpei',
    'larcauth_learnerdp_has_termsubjectdp',
}

_NOTE_TABLES = {
    'maternelle': ['larcauth_learnermat_has_devpers_unit',
                   'larcauth_learnermat_has_subjectevals_unit'],
    'primaire': ['larcauth_learnerprim_has_unit_period',
                 'larcauth_learnerpp_has_termsubjectpp'],
    'college_lycee': ['larcauth_learnerpei_has_termsubjectpei',
                      'larcauth_learnerdp_has_termsubjectdp',
                      'larcauth_learner_has_termothersubject'],
}

# Bulletin : (table, condition « bulletin produit » sur l'alias t)
_BULLETIN = {
    'maternelle': ('larcauth_learnermat_has_unit_period',
                   "t.unit_average_global_on_20 IS NOT NULL "
                   "OR COALESCE(TRIM(t.observation_global), '') <> '' "
                   "OR COALESCE(TRIM(t.observation_profil), '') <> ''"),
    'primaire': ('larcauth_learnerprim_has_unit_period', "t.enabled = TRUE"),
    'college_lycee': ('larcauth_learner_has_term', "t.enabled = TRUE"),
}


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _default_literal(default):
    """Valeur par défaut d'information_schema → texte comparable (ou None)."""
    if default is None:
        return None
    d = default.split('::')[0].strip()
    if d.startswith("'") and d.endswith("'"):
        d = d[1:-1]
    return None if d.lower().startswith('nextval(') else d


def note_columns(table: str, cur=None) -> list:
    """[(colonne, type, défaut)] des colonnes de notes d'une table, lues dans
    information_schema puis gardées en cache pour la session."""
    if table in _COLS_CACHE:
        return _COLS_CACHE[table]
    if cur is None:
        c = _conn()
        if not c:
            return []
        cur = c.cursor()
    cur.execute(
        "SELECT column_name, data_type, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    )
    pattern = re.compile(_NOTE_PATTERNS[table], re.IGNORECASE)
    cols = [(name, dtype, _default_literal(default)) for name, dtype, default in cur.fetchall()
            if pattern.search(name) and not _EXCLUDE_RE.search(name)]
    _COLS_CACHE[table] = cols
    return cols


def filled_expr(column: str, dtype: str, default) -> str:
    """Expression SQL « la colonne porte une valeur saisie » (alias t)."""
    q = f"t.{_quote(column)}"
    if dtype == 'ARRAY':
        return f"(COALESCE(cardinality({q}), 0) > 0)"
    if dtype == 'boolean':
        return f"({q} IS TRUE)"
    parts = [f"{q} IS NOT NULL", f"TRIM({q}::text) NOT IN ('', '-')"]
    if default is not None:
        parts.append(f"{q}::text <> '{default.replace(chr(39), chr(39) * 2)}'")
    return "(" + " AND ".join(parts) + ")"


def build_notes_sql(table: str, cols: list) -> str:
    """SELECT id élève, nb de lignes portant au moins une note saisie, pour
    les élèves d'une plage d'ID (paramètres : id_min, id_max)."""
    any_filled = " OR ".join(filled_expr(n, d, df) for n, d, df in cols) or "FALSE"
    if table in _VIA_TERMSUBJECT:
        return (
            f"SELECT lht.fk_student_id AS id, COUNT(*) FILTER (WHERE {any_filled}) AS n "
            f"FROM {table} t "
            "JOIN larcauth_learner_has_termsubject lht ON lht.id = t.learner_has_termsubject_ptr_id "
            "WHERE lht.fk_student_id BETWEEN %s AND %s GROUP BY lht.fk_student_id"
        )
    return (
        f"SELECT t.fk_student_id AS id, COUNT(*) FILTER (WHERE {any_filled}) AS n "
        f"FROM {table} t WHERE t.fk_student_id BETWEEN %s AND %s GROUP BY t.fk_student_id"
    )


def build_bulletin_sql(campus_key: str) -> str:
    table, cond = _BULLETIN[campus_key]
    return (
        f"SELECT t.fk_student_id AS id, BOOL_OR({cond}) AS b FROM {table} t "
        "WHERE t.fk_student_id BETWEEN %s AND %s GROUP BY t.fk_student_id"
    )


def get_evidence(classroom_id: int, campus_key: str) -> dict:
    """{id: {'notes': int|None, 'has_notes': bool|None, 'has_bulletin': bool|None}}
    pour les 40 slots d'une classe — quelques requêtes groupées, pas 40×N."""
    ids = range(classroom_id * 100 + 1, classroom_id * 100 + SLOTS_PER_CLASS + 1)
    notes = {sid: 0 for sid in ids}
    bulletin = {sid: False for sid in ids}
    c = _conn()
    if not c or campus_key not in _NOTE_TABLES:
        return {sid: {'notes': None, 'has_notes': None, 'has_bulletin': None} for sid in ids}
    bounds = (ids[0], ids[-1])
    cur = c.cursor()
    for table in _NOTE_TABLES[campus_key]:
        if notes is None:
            break
        try:
            cols = note_columns(table, cur)
            cur.execute(build_notes_sql(table, cols), bounds)
            for sid, n in cur.fetchall():
                if sid in notes:
                    notes[sid] += n or 0
        except Exception as e:
            _report(f"get_evidence notes {table}", e)
            notes = None
    try:
        cur.execute(build_bulletin_sql(campus_key), bounds)
        for sid, b in cur.fetchall():
            if sid in bulletin:
                bulletin[sid] = bool(b)
    except Exception as e:
        _report(f"get_evidence bulletin {campus_key}", e)
        bulletin = None
    return {
        sid: {
            'notes': None if notes is None else notes[sid],
            'has_notes': None if notes is None else notes[sid] > 0,
            'has_bulletin': None if bulletin is None else bulletin[sid],
        }
        for sid in ids
    }


def get_class_detail(classroom: dict, campus: Campus) -> list[dict]:
    """Slots évalués d'une classe (états, anomalies, preuves, verdict)."""
    from LarcConfig.common.student_slot_rules import assess_slots
    cid = classroom['id']
    needs_subjects = campus.key == 'college_lycee'
    subjects = get_active_subject_counts([cid], resolve_term_id()) if needs_subjects else {}
    return assess_slots(get_slots(cid), bool(classroom.get('enabled', True)), get_duplicates(),
                        subjects, needs_subjects, get_evidence(cid, campus.key))


# ── Preuves d'un seul élève (confirmations et contrôle au moment du clic) ─
# Le curseur est passé par l'appelant : db_students_write les rejoue DANS sa
# transaction, juste avant d'écrire. Les erreurs remontent (pas de None
# silencieux) : l'appelant bloque dans le doute.

def note_tables(campus_key: str) -> list:
    """Tables de notes d'un campus (même liste que get_evidence)."""
    return list(_NOTE_TABLES.get(campus_key, []))


def notes_by_table(student_id: int, campus_key: str, cur) -> dict:
    """{table: nb de lignes portant au moins une note saisie} pour un élève."""
    out = {}
    for table in note_tables(campus_key):
        cur.execute(build_notes_sql(table, note_columns(table, cur)), (student_id, student_id))
        out[table] = sum((n or 0) for _sid, n in cur.fetchall())
    return out


def bulletin_of(student_id: int, campus_key: str, cur) -> bool:
    """Vrai si un bulletin a été produit pour l'élève (règle du campus)."""
    cur.execute(build_bulletin_sql(campus_key), (student_id, student_id))
    return any(bool(b) for _sid, b in cur.fetchall())


def count_active_enrolments(student_id: int, cur) -> int:
    """Inscriptions actives de l'élève (toutes matières, tous trimestres)."""
    cur.execute(
        "SELECT COUNT(*) FROM larcauth_learner_has_termsubject "
        "WHERE fk_student_id = %s AND enabled = TRUE",
        (student_id,),
    )
    r = cur.fetchone()
    return r[0] if r else 0


def get_action_preview(student_id: int, campus_key: str) -> dict:
    """Chiffres affichés dans les confirmations (SELECT uniquement) :
    inscriptions actives, notes par table, bulletin, lignes portant encore du
    contenu (notes / observations / commentaires) par table learner*,
    éléments conservés (liens parents, historiques) et fichiers sur disque.
    None par valeur illisible."""
    from LarcConfig.common import child_reset  # import local : child_reset importe ce module
    c = _conn()
    out = {'enrolments': None, 'notes': None, 'bulletin': None, 'content': None,
           'parents': None, 'kept': None, 'files': child_reset.disk_files(student_id)}
    if not c:
        return out
    cur = c.cursor()

    def content():
        res = {}
        for table in child_reset.RESET_TABLES:
            cols = child_reset.content_columns(table, cur)
            if cols:
                cur.execute(child_reset.build_content_count_sql(table, cols), (student_id,))
                res[table] = cur.fetchone()[0]
        return res

    def _scalar(sql):
        cur.execute(sql, (student_id,))
        return cur.fetchone()[0]

    def kept():
        res = {}
        for table, column, label in child_reset.KEPT_TABLES:
            cur.execute(child_reset.build_kept_count_sql(table, column), (student_id,))
            res[label] = cur.fetchone()[0]
        return res

    for key, fn in (('enrolments', lambda: count_active_enrolments(student_id, cur)),
                    ('notes', lambda: notes_by_table(student_id, campus_key, cur)),
                    ('bulletin', lambda: bulletin_of(student_id, campus_key, cur)),
                    ('content', content),
                    ('parents', lambda: _scalar(child_reset.PARENT_LINKS_COUNT_SQL)),
                    ('kept', kept)):
        try:
            out[key] = fn()
        except Exception as e:
            _report(f"get_action_preview {key}", e)
    return out
