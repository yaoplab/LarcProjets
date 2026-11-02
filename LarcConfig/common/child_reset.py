"""Remise à zéro COMPLÈTE d'une place élève (décision de Pat, 2026-10-27).

« Réinitialiser une place efface TOUT de l'enfant précédent » — sans
INSERT ni DELETE (principe gabarit) : chaque donnée revient à la valeur
qu'ont les places libres jamais utilisées (« seed »). Ce module construit les
requêtes ; db_students_write les exécute dans sa transaction.

Relevés en base (SELECT, 2026-10-27, 2001 places libres) qui fixent les
valeurs ci-dessous :
  - tables learner* : toutes les colonnes de contenu (notes, observations,
    commentaires, moyennes, validated…) → défaut de la colonne, ou la
    convention des lignes jamais saisies (_OVERRIDES) ; colonnes de
    structure (id, enabled, fk_*, ref_*, disabled, titre, bareme…) jamais
    touchées — en particulier enabled des tables « bulletin ».
  - larcauth_aecuser : valeurs dominantes des places libres du même campus
    (_AECUSER_SEED). id, username, password, passdelph, last_login,
    date_joined, created ne sont PAS modifiés : dérivés de l'id ou
    techniques, identiques entre places libres et occupées.
  - foyer : une ligne par élève (foyer.id = id élève, jamais partagée avec un
    parent — vérifié) → adresse vide, pays 'France', enabled TRUE.
  - larcauth_student_parent : PAS de colonne enabled → les liens de l'élève
    sont SUPPRIMÉS (PARENT_LINKS_DELETE_SQL) : SEULE exception au principe
    UPDATE-only, accord de Pat du 2026-11-02. Jamais les parents eux-mêmes.
  - historique (frais, événements, résumés, tâches secrétariat) : pas de
    colonne d'activation → compté et signalé, jamais modifié.
"""
import os
import re

from LarcConfig.common import db_students

# Tables « enfant » remises à vide (toutes, quel que soit le campus : la
# base pré-crée des lignes pour les 2400 places dans chacune d'elles).
RESET_TABLES = [
    'larcauth_learnermat_has_devpers_unit',
    'larcauth_learnermat_has_subjectevals_unit',
    'larcauth_learnermat_has_unit_period',
    'larcauth_learnerprim_has_unit_period',
    'larcauth_learnerpp_has_termsubjectpp',
    'larcauth_learnerpei_has_termsubjectpei',
    'larcauth_learnerdp_has_termsubjectdp',
    'larcauth_learner_has_termothersubject',
    'larcauth_learner_has_subjectgroup',
    'larcauth_learner_has_term',
]

# Colonnes de structure : jamais remises à zéro (lien, paramétrage, activation)
_STRUCTURAL_RE = re.compile(
    r'^(id|enabled|created|updated|disabled|titre|bareme|fk_.*|.*_ptr_id|ref_.*)$', re.IGNORECASE)

# Valeur « vide » quand le défaut SQL ne suit pas la convention des lignes
# jamais saisies, ou qu'il n'y a pas de défaut sur une colonne NOT NULL.
_OVERRIDES = {
    'larcauth_learnermat_has_devpers_unit': {'status': "'-'"},          # 2,1 M lignes '-'
    'larcauth_learnermat_has_subjectevals_unit': {'a': 'FALSE', 'pa': 'FALSE', 'na': 'FALSE'},
    'larcauth_learner_has_subjectgroup': {'description': "' '"},        # 7590 places libres : ' '
}

_CONTENT_CACHE: dict = {}  # {table: [(colonne, type, défaut brut, nullable)]}


def content_columns(table: str, cur) -> list:
    """Colonnes de contenu d'une table (information_schema, cache de session)."""
    if table in _CONTENT_CACHE:
        return _CONTENT_CACHE[table]
    cur.execute(
        "SELECT column_name, data_type, column_default, is_nullable "
        "FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s "
        "ORDER BY ordinal_position",
        (table,),
    )
    cols = [(n, d, df, nullable == 'YES') for n, d, df, nullable in cur.fetchall()
            if not _STRUCTURAL_RE.match(n)]
    _CONTENT_CACHE[table] = cols
    return cols


def target_expr(table: str, column: str, dtype: str, default, nullable: bool):
    """Expression SQL de la valeur « vide » d'une colonne, ou None si aucune
    valeur sûre n'existe (NOT NULL sans défaut d'un type inattendu)."""
    override = _OVERRIDES.get(table, {}).get(column)
    if override is not None:
        return override
    if default is not None and not default.lower().startswith('nextval('):
        return default  # ex. '-'::character varying, 0, true, '{}'::jsonb
    if nullable:
        return 'NULL'
    if dtype == 'boolean':
        return 'FALSE'
    if dtype in ('text', 'character varying'):
        return "''"
    return None


def _assignments(table: str, cols: list) -> list:
    """[(colonne citée, expression cible)] — colonnes sans cible sûre ignorées."""
    out = []
    for name, dtype, default, nullable in cols:
        expr = target_expr(table, name, dtype, default, nullable)
        if expr is not None:
            out.append((db_students._quote(name), expr))
    return out


def _dirty(assignments: list) -> str:
    return " OR ".join(f"t.{col} IS DISTINCT FROM {expr}" for col, expr in assignments) or "FALSE"


def _scope(table: str) -> tuple:
    """(FROM additionnel, condition élève) — paramètre : id élève."""
    if table in db_students._VIA_TERMSUBJECT:
        return (" FROM larcauth_learner_has_termsubject lht",
                "lht.id = t.learner_has_termsubject_ptr_id AND lht.fk_student_id = %s")
    return ("", "t.fk_student_id = %s")


def build_content_reset_sql(table: str, cols: list) -> str:
    """UPDATE remettant à vide le contenu des lignes de l'élève qui en ont
    encore (rowcount = lignes réellement nettoyées)."""
    assignments = _assignments(table, cols)
    sets = ", ".join(f"{col} = {expr}" for col, expr in assignments)
    extra_from, scope = _scope(table)
    return f"UPDATE {table} t SET {sets}{extra_from} WHERE {scope} AND ({_dirty(assignments)})"


def build_content_count_sql(table: str, cols: list) -> str:
    """SELECT du nombre de lignes de l'élève portant encore du contenu."""
    assignments = _assignments(table, cols)
    extra_from, scope = _scope(table)
    from_clause = f"{table} t" + (extra_from.replace(" FROM", ",") if extra_from else "")
    return f"SELECT COUNT(*) FROM {from_clause} WHERE {scope} AND ({_dirty(assignments)})"


# ── Données personnelles (larcauth_aecuser) ──────────────────────────────

# Commun aux 3 campus (valeur dominante des places libres, ou défaut SQL)
_AECUSER_COMMON = {
    'firstname_2': 'NULL', 'date_of_birth': 'NULL',
    'tel_maison': 'NULL', 'tel_smartphone_1': 'NULL', 'tel_smartphone_2': 'NULL',
    'emailperso': 'NULL', 'picture2': "'\\x00000000'::bytea",   # 4 octets nuls (1943 places)
    'type_student': 'TRUE', 'type_secretary': 'FALSE',
    'is_active': 'FALSE', 'is_staff': 'FALSE', 'is_superuser': 'FALSE',
    'fk_parent_id': 'NULL', 'fk_foyer_id': 'id',               # chaque élève = son foyer
    'fk_language': 'DEFAULT', 'civility': 'NULL', 'nationality': 'NULL',
    'marital_status': 'NULL', 'children_count': 'DEFAULT',
    'emergency_contact_name': 'NULL', 'emergency_contact_phone': 'NULL',
    'blood_type': 'NULL', 'cnss_number': 'NULL', 'tax_id': 'NULL',
    'id_document_type': 'NULL', 'id_document_number': 'NULL', 'id_document_expiry': 'NULL',
    'matricule': 'NULL', 'professional_category': 'NULL', 'emp_status': 'DEFAULT',
    'departure_date': 'NULL', 'departure_reason': 'NULL',
    'fk_campus_id': 'NULL', 'fk_supervisor_id': 'NULL',
}
_ROLE_FLAGS = ('type_parentutor', 'type_teacher', 'type_coordonator', 'type_supervisor',
               'type_director')
# Par campus : valeurs dominantes des places libres de ce campus
_AECUSER_BY_CAMPUS = {
    'maternelle': {'email': "'en_attente@arc-en-ciel.org'", 'avatar': "''",
                   'date_entree': "'2019-07-02'", 'fk_gender_id': '11', 'roles': 'NULL'},
    'primaire': {'email': "'enattente@arc-en-ciel.org'", 'avatar': "' '",
                 'date_entree': 'NULL', 'fk_gender_id': 'NULL', 'roles': 'FALSE'},
    'college_lycee': {'email': "'en_attente@arc-en-ciel.org'", 'avatar': "''",
                      'date_entree': "'2019-07-02'", 'fk_gender_id': '11', 'roles': 'NULL'},
}

# Libellés lisibles des données personnelles effacées (confirmation)
PERSONAL_LABELS = [
    "date de naissance", "2e prénom", "date d'entrée", "téléphones", "e-mails",
    "genre", "civilité / nationalité", "contact d'urgence", "groupe sanguin",
    "pièce d'identité", "photo en base", "parent principal", "adresse du foyer",
    "notes et validations du dossier", "statut de scolarité",
]


def aecuser_seed(campus_key: str) -> dict:
    """{colonne: expression SQL} des données personnelles remises à zéro."""
    per = _AECUSER_BY_CAMPUS[campus_key]
    seed = dict(_AECUSER_COMMON)
    seed.update({k: v for k, v in per.items() if k != 'roles'})
    seed.update({flag: per['roles'] for flag in _ROLE_FLAGS})
    return seed


def build_aecuser_reset_sql(campus_key: str) -> str:
    """UPDATE des données personnelles + nom gabarit, gardé par les anciens
    noms. Paramètres : (nom gabarit, prénom gabarit, id, ancien nom, ancien prénom)."""
    sets = ", ".join(f'"{col}" = {expr}' for col, expr in aecuser_seed(campus_key).items())
    return (f"UPDATE larcauth_aecuser SET last_name = %s, first_name = %s, {sets}, "
            "updated = NOW() WHERE id = %s AND last_name = %s AND first_name = %s")


STUDENT_RESET_SQL = (
    "UPDATE larcauth_student SET enabled = FALSE, s_classroom_id = %s, notes = NULL, "
    "notes_json = DEFAULT, validation = DEFAULT, statut_scolarite = DEFAULT, updated_s = NOW() "
    "WHERE aecuser_ptr_id = %s AND enabled = %s"
)

# Foyer propre à l'élève seulement : jamais un foyer référencé par quelqu'un d'autre
FOYER_RESET_SQL = (
    "UPDATE foyer f SET address_line1 = NULL, address_line2 = NULL, postal_code = NULL, "
    "city = NULL, country = DEFAULT, phone = NULL, email = NULL, notes = NULL, enabled = TRUE "
    "WHERE f.id = %s AND NOT EXISTS (SELECT 1 FROM larcauth_aecuser x "
    "WHERE x.fk_foyer_id = f.id AND x.id <> f.id)"
)


# ── Ce qui reste et doit être signalé ────────────────────────────────────

# Liens élève ↔ parents de CET élève uniquement (table de liaison sans
# colonne d'activation) — la ligne parent (larcauth_parent / aecuser) reste.
PARENT_LINKS_COUNT_SQL = "SELECT COUNT(*) FROM larcauth_student_parent WHERE student_id = %s"
PARENT_LINKS_DELETE_SQL = "DELETE FROM larcauth_student_parent WHERE student_id = %s"

# (table, colonne élève, libellé) — comptés et signalés, jamais modifiés
KEPT_TABLES = [
    ('compta_student_fee', 'student_id', "frais de scolarité"),
    ('student_event', 'student_id', "événement(s) de présence"),
    ('student_daily_summary', 'student_id', "résumé(s) journalier(s)"),
    ('secretary_todo', 'student_id', "tâche(s) du secrétariat"),
]


def build_kept_count_sql(table: str, column: str) -> str:
    return f"SELECT COUNT(*) FROM {table} WHERE {column} = %s"


def disk_files(student_id: int) -> list:
    """Fichiers de l'élève stockés sur disque par id (photo LarcSuperviseur,
    cache, dossier LarcSecretaire) — listés, jamais supprimés ici."""
    try:
        from larccommon.app_config import app_config
        from larccommon.paths import larc_root
        candidates = [
            os.path.join(app_config.get('photos_dir'), f"{student_id}.png"),
            os.path.join(app_config.get('photos_dir'), f"{student_id}.jpg"),
            os.path.join(app_config.get('photos_cache_dir'), f"{student_id}.png"),
        ]
        folder = str(larc_root() / 'LarcSecretaire' / 'data' / 'students' / str(student_id))
    except Exception:
        return []
    found = [p for p in candidates if os.path.isfile(p)]
    if os.path.isdir(folder) and any(files for _r, _d, files in os.walk(folder)):
        found.append(folder)
    return found
