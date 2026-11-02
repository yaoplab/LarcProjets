"""Tests de la remise à zéro complète d'une place (décision de Pat, 2026-10-27).

Constructeurs SQL de child_reset (pas de base), aperçu des confirmations
(curseur simulé) et lignes affichées dans le dialogue.
"""
import re
from pathlib import Path
from unittest.mock import patch

import larccommon  # noqa: F401 — initialiser larccommon AVANT phibuilder (import circulaire connu)
import pytest

from LarcConfig.common import child_reset, db_students
from LarcConfig.common import student_slot_rules as rules


class TestModuleIsUpdateOnly:
    # Seule suppression autorisée (accord de Pat, 2026-11-02) : les liens
    # parents de l'élève réinitialisé. Toute autre INSERT/DELETE fait échouer.
    WHITELISTED_DELETE = "DELETE FROM larcauth_student_parent WHERE student_id = %s"

    def test_only_whitelisted_delete_no_insert_or_ddl(self):
        code = re.sub(r'""".*?"""', '', Path(child_reset.__file__).read_text(encoding='utf-8'),
                      flags=re.S)
        assert code.count(self.WHITELISTED_DELETE) == 1
        code = code.replace(self.WHITELISTED_DELETE, "")
        for kw in ("INSERT", "DELETE", "TRUNCATE", "ALTER", "DROP", "CREATE"):
            assert not re.search(rf'\b{kw}\b', code, re.I), kw

    def test_parent_links_delete_targets_only_this_student(self):
        assert child_reset.PARENT_LINKS_DELETE_SQL == self.WHITELISTED_DELETE
        assert child_reset.PARENT_LINKS_COUNT_SQL == \
            "SELECT COUNT(*) FROM larcauth_student_parent WHERE student_id = %s"


class TestContentColumns:
    def test_structural_columns_never_reset(self):
        rows = [("id", "integer", None, "NO"), ("enabled", "boolean", None, "YES"),
                ("fk_student_id", "integer", None, "YES"), ("ref_unit_nr", "integer", None, "YES"),
                ("disabled", "boolean", "false", "YES"), ("titre", "character varying", None, "NO"),
                ("bareme", "smallint", None, "NO"), ("learner_has_termsubject_ptr_id", "integer", None, "NO"),
                ("created", "timestamp", None, "NO"), ("updated", "timestamp", None, "NO"),
                ("observation_global", "text", None, "YES"), ("validated", "boolean", None, "NO")]

        class Cur:
            def execute(self, *_a):
                pass

            def fetchall(self):
                return rows
        child_reset._CONTENT_CACHE.pop('t_test', None)
        got = child_reset.content_columns('t_test', Cur())
        assert [c[0] for c in got] == ['observation_global', 'validated']
        assert got[1] == ('validated', 'boolean', None, False)
        assert child_reset.content_columns('t_test', None) is got   # cache de session
        child_reset._CONTENT_CACHE.pop('t_test', None)


class TestTargetValues:
    @pytest.mark.parametrize("table,col,dtype,default,nullable,expected", [
        ('larcauth_learnermat_has_devpers_unit', 'status', 'USER-DEFINED', None, True, "'-'"),
        ('larcauth_learnermat_has_subjectevals_unit', 'a', 'boolean', None, True, 'FALSE'),
        ('larcauth_learner_has_subjectgroup', 'description', 'character varying', None, False, "' '"),
        ('x', 'note_lang_cat1', 'character varying', "'-'::character varying", False,
         "'-'::character varying"),
        ('x', 'note_on_max', 'smallint', '0', False, '0'),
        ('x', 'b_la', 'boolean', 'true', False, 'true'),
        ('x', 'f01_observation', 'character varying', None, True, 'NULL'),
        ('x', 'f13_note', 'ARRAY', None, True, 'NULL'),
        ('x', 'validated', 'boolean', None, False, 'FALSE'),
        ('x', 'observation_target', 'character varying', None, False, "''"),
        ('x', 'weird', 'integer', None, False, None),
        ('x', 'seq', 'integer', "nextval('s'::regclass)", False, None),
    ])
    def test_target(self, table, col, dtype, default, nullable, expected):
        assert child_reset.target_expr(table, col, dtype, default, nullable) == expected


class TestContentSql:
    def test_direct_table(self):
        cols = [("observation_global", "text", None, True), ("validated", "boolean", None, False),
                ("note_on_max", "smallint", "0", False)]
        sql = child_reset.build_content_reset_sql('larcauth_learnerprim_has_unit_period', cols)
        assert sql.startswith("UPDATE larcauth_learnerprim_has_unit_period t SET ")
        assert '"observation_global" = NULL' in sql and '"validated" = FALSE' in sql
        assert '"note_on_max" = 0' in sql
        assert "WHERE t.fk_student_id = %s AND (" in sql
        assert 't."note_on_max" IS DISTINCT FROM 0' in sql   # seules les lignes encore remplies
        assert "enabled" not in sql

    def test_child_table_joins_termsubject(self):
        cols = [("f01_observation", "character varying", None, True),
                ("note_on_7_checked", "boolean", "false", False)]
        sql = child_reset.build_content_reset_sql('larcauth_learnerpei_has_termsubjectpei', cols)
        assert " FROM larcauth_learner_has_termsubject lht WHERE " in sql
        assert "lht.id = t.learner_has_termsubject_ptr_id AND lht.fk_student_id = %s" in sql
        count = child_reset.build_content_count_sql('larcauth_learnerpei_has_termsubjectpei', cols)
        assert count.startswith("SELECT COUNT(*) FROM larcauth_learnerpei_has_termsubjectpei t, "
                                "larcauth_learner_has_termsubject lht WHERE")

    def test_reset_tables_cover_every_learner_table(self):
        for t in ('larcauth_learner_has_term', 'larcauth_learner_has_subjectgroup',
                  'larcauth_learnermat_has_unit_period', 'larcauth_learnerprim_has_unit_period'):
            assert t in child_reset.RESET_TABLES
        assert 'larcauth_learner_has_termsubject' not in child_reset.RESET_TABLES  # seulement enabled


class TestPersonalData:
    @pytest.mark.parametrize("campus", ['maternelle', 'primaire', 'college_lycee'])
    def test_seed_covers_personal_columns(self, campus):
        seed = child_reset.aecuser_seed(campus)
        for col in ('date_of_birth', 'firstname_2', 'date_entree', 'tel_maison',
                    'tel_smartphone_1', 'tel_smartphone_2', 'email', 'emailperso', 'avatar',
                    'picture2', 'fk_gender_id', 'fk_foyer_id', 'fk_parent_id', 'civility',
                    'nationality', 'emergency_contact_name', 'emergency_contact_phone',
                    'blood_type', 'id_document_number', 'type_parentutor', 'type_student'):
            assert col in seed, col
        for kept in ('id', 'username', 'password', 'passdelph', 'date_joined', 'created'):
            assert kept not in seed
        assert seed['fk_foyer_id'] == 'id' and seed['type_student'] == 'TRUE'
        assert seed['picture2'] == "'\\x00000000'::bytea"

    def test_campus_specific_seeds(self):
        assert child_reset.aecuser_seed('primaire')['email'] == "'enattente@arc-en-ciel.org'"
        assert child_reset.aecuser_seed('primaire')['avatar'] == "' '"
        assert child_reset.aecuser_seed('primaire')['type_teacher'] == 'FALSE'
        assert child_reset.aecuser_seed('maternelle')['fk_gender_id'] == '11'
        assert child_reset.aecuser_seed('college_lycee')['type_teacher'] == 'NULL'

    def test_aecuser_sql_guarded_by_old_names(self):
        sql = child_reset.build_aecuser_reset_sql('college_lycee')
        assert sql.startswith("UPDATE larcauth_aecuser SET last_name = %s, first_name = %s, ")
        assert sql.endswith("WHERE id = %s AND last_name = %s AND first_name = %s")

    def test_student_and_foyer_sql(self):
        assert "notes = NULL" in child_reset.STUDENT_RESET_SQL
        assert "statut_scolarite = DEFAULT" in child_reset.STUDENT_RESET_SQL
        assert child_reset.STUDENT_RESET_SQL.endswith("WHERE aecuser_ptr_id = %s AND enabled = %s")
        assert "NOT EXISTS" in child_reset.FOYER_RESET_SQL and "x.id <> f.id" in child_reset.FOYER_RESET_SQL

    def test_personal_labels_for_dialog(self):
        assert "date de naissance" in child_reset.PERSONAL_LABELS
        assert "adresse du foyer" in child_reset.PERSONAL_LABELS


class TestKeptAndFiles:
    def test_kept_tables_listed(self):
        tables = [t for t, _c, _l in child_reset.KEPT_TABLES]
        assert 'larcauth_student_parent' not in tables      # supprimés, plus « conservés »
        assert tables == ['compta_student_fee', 'student_event', 'student_daily_summary',
                          'secretary_todo']
        assert child_reset.build_kept_count_sql('compta_student_fee', 'student_id') == \
            "SELECT COUNT(*) FROM compta_student_fee WHERE student_id = %s"

    def test_disk_files_found_by_id(self, tmp_path):
        photos = tmp_path / "photos"
        (photos / "cache").mkdir(parents=True)
        (photos / "223101.png").write_bytes(b"x")
        (photos / "cache" / "223101.png").write_bytes(b"x")
        folder = tmp_path / "LarcSecretaire" / "data" / "students" / "223101" / "dossiers"
        folder.mkdir(parents=True)
        (folder / "certificat.pdf").write_bytes(b"x")
        values = {'photos_dir': str(photos), 'photos_cache_dir': str(photos / "cache")}
        with patch("larccommon.app_config.app_config.get", side_effect=values.get), \
                patch("larccommon.paths.larc_root", return_value=tmp_path):
            found = child_reset.disk_files(223101)
        assert str(photos / "223101.png") in found
        assert str(photos / "cache" / "223101.png") in found
        assert str(tmp_path / "LarcSecretaire" / "data" / "students" / "223101") in found
        with patch("larccommon.app_config.app_config.get", side_effect=values.get), \
                patch("larccommon.paths.larc_root", return_value=tmp_path):
            assert child_reset.disk_files(223102) == []


# ── Aperçu (SELECT) et lignes de confirmation ────────────────────────────

class PreviewCursor:
    def __init__(self):
        self.executed = []
        self._r = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        if "COUNT(*) FILTER" in sql:
            self._r = [(params[0], 2)]
        elif "BOOL_OR" in sql:
            self._r = [(params[0], False)]
        elif sql.startswith("SELECT COUNT(*) FROM larcauth_learner_has_termsubject WHERE"):
            self._r = [(9,)]
        elif "larcauth_student_parent" in sql:
            self._r = [(2,)]
        elif sql.startswith("SELECT COUNT(*)"):
            self._r = [(3,)] if "learnerpei" in sql else [(0,)]

    def fetchone(self):
        return self._r[0] if self._r else None

    def fetchall(self):
        return list(self._r)


class Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur


def test_preview_counts_everything_and_is_select_only():
    cur = PreviewCursor()
    content = {t: [("c", "text", None, True)] for t in child_reset.RESET_TABLES}
    notes = {t: [("n", "smallint", None)] for t in db_students.note_tables('college_lycee')}
    with patch.dict(child_reset._CONTENT_CACHE, content), \
            patch.dict(db_students._COLS_CACHE, notes), \
            patch.object(db_students, "_conn", return_value=Conn(cur)), \
            patch.object(child_reset, "disk_files", return_value=["X:/photos/223101.png"]):
        pv = db_students.get_action_preview(223101, 'college_lycee')
    assert pv['enrolments'] == 9 and pv['bulletin'] is False
    assert sum(pv['notes'].values()) == 6
    assert pv['content']['larcauth_learnerpei_has_termsubjectpei'] == 3
    assert pv['parents'] == 2 and "lien(s) parent" not in pv['kept']
    assert pv['files'] == ["X:/photos/223101.png"]
    assert all(s.lstrip().startswith("SELECT") for s in cur.executed)


def _row():
    return {'id': 223101, 'slot': 1, 'state': rules.ACTIF, 'verdict': rules.ADMIN,
            'placeholder': False, 'has_bulletin': False,
            'last_name': 'ATIEH', 'first_name': 'Julia'}


def test_confirmation_lines_list_everything():
    from LarcConfig.views.eleves_actions import reset_confirmation_lines
    preview = {'enrolments': 9,
               'notes': {'larcauth_learnerpei_has_termsubjectpei': 4},
               'bulletin': False,
               'content': {'larcauth_learnerpei_has_termsubjectpei': 5,
                           'larcauth_learner_has_term': 1,
                           'larcauth_learnermat_has_devpers_unit': 0},
               'parents': 2,
               'kept': {"frais de scolarité": 1, "tâche(s) du secrétariat": 0},
               'files': ["X:/photos/223101.png"]}
    text = "\n".join(reset_confirmation_lines(_row(), preview))
    assert "Notes saisies effacées : 4 ligne(s) — confirmation administrateur." in text
    assert "Notes, observations et commentaires vidés : 6 ligne(s) dans 2 table(s)." in text
    assert "Désinscrit(e) de 9 matière(s)." in text
    assert "date de naissance" in text and "adresse du foyer" in text
    assert "Liens parents supprimés : 2" in text and "CONSERVÉ : 1 frais de scolarité" in text
    assert "lien(s) parent" not in text
    assert "tâche(s) du secrétariat" not in text
    assert "Photo et fichiers sur disque à retirer par le secrétariat" in text
    assert "X:/photos/223101.png" in text


def test_confirmation_lines_unknown_values():
    from LarcConfig.views.eleves_actions import reset_confirmation_lines
    preview = {'enrolments': None, 'notes': None, 'bulletin': None, 'content': None,
               'parents': None, 'kept': None, 'files': []}
    text = "\n".join(reset_confirmation_lines(_row(), preview))
    assert "? (illisible)" in text and "Photo et fichiers" not in text
