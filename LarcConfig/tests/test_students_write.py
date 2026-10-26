"""Tests étape 4 — actions d'écriture sur les places (UPDATE uniquement).

Aucune base réelle : un curseur scripté répond aux requêtes et enregistre
tout ce qui est exécuté (BEGIN / COMMIT / ROLLBACK compris).
"""
import re
from pathlib import Path
from unittest.mock import patch

import larccommon  # noqa: F401 — initialiser larccommon AVANT phibuilder (import circulaire connu)
import pytest

from LarcConfig.common import db_students, db_students_write
from LarcConfig.common import student_slot_rules as rules
from LarcConfig.common.campus import campus_of_classroom


class ScriptCursor:
    """Curseur simulé : `slot` = ligne renvoyée par le SELECT … FOR UPDATE,
    `rowcounts` = {fragment SQL: rowcount}, `notes` / `bulletin` = preuves."""

    def __init__(self, slot, notes=0, bulletin=False, duplicates=(), rowcounts=None,
                 fail_on=None):
        self.slot = slot
        self.notes = notes
        self.bulletin = bulletin
        self.duplicates = list(duplicates)
        self.rowcounts = rowcounts or {}
        self.fail_on = fail_on
        self.executed = []
        self.rowcount = 0
        self._result = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("panne simulée")
        self._result, self.rowcount = [], 0
        if "FOR UPDATE OF" in sql:
            self._result = [self.slot] if self.slot else []
        elif "LOWER(TRIM(a.last_name))" in sql:
            self._result = self.duplicates
        elif "COUNT(*) FILTER" in sql:
            self._result = [(params[0], self.notes)]
        elif "BOOL_OR" in sql:
            self._result = [(params[0], self.bulletin)]
        elif sql.startswith("UPDATE"):
            self.rowcount = next((n for frag, n in self.rowcounts.items() if frag in sql), 1)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)

    def sql(self):
        return [s for s, _p in self.executed]

    def updates(self):
        return [s for s in self.sql() if s.startswith("UPDATE")]


class FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur


@pytest.fixture(autouse=True)
def _no_audit_and_known_columns():
    cols = {
        'larcauth_learnermat_has_devpers_unit': [("a", "boolean", None), ("status", "USER-DEFINED", None)],
        'larcauth_learnermat_has_subjectevals_unit': [("a", "boolean", None), ("value", "smallint", "0")],
        'larcauth_learnerprim_has_unit_period': [("f_la", "smallint", None)],
        'larcauth_learnerpp_has_termsubjectpp': [("f01_note", "double precision", None)],
        'larcauth_learnerpei_has_termsubjectpei': [("f01_note_a", "smallint", None)],
        'larcauth_learnerdp_has_termsubjectdp': [("f13_note", "ARRAY", None)],
        'larcauth_learner_has_termothersubject': [("mark_on_20", "double precision", None)],
    }
    saved = dict(db_students._COLS_CACHE)
    db_students._COLS_CACHE.update(cols)
    with patch("larccommon.audit_context.attach"), patch("larccommon.audit_context.refresh"), \
            patch.object(db_students_write, "_report", create=True), \
            patch("larccommon.error_reporting.get_reporter"):
        yield
    db_students._COLS_CACHE.clear()
    db_students._COLS_CACHE.update(saved)


def _run_with(cur):
    return patch.object(db_students_write, "_conn", return_value=FakeConn(cur))


FREE = (False, "First_name of 213105", "Name of 213105", 2131, True)
ACTIVE = (True, "Julia", "ATIEH", 2231, True)
LEFT = (False, "Adam", "BACHAR", 2251, True)


# ── Règles pures ─────────────────────────────────────────────────────────

class TestCleanNames:
    def test_convention_upper_last_title_first(self):
        assert rules.clean_names("  dupont  ", "julia  marie") == ("DUPONT", "Julia Marie")
        assert rules.clean_names("El Hajj", "JEAN-LUC") == ("EL HAJJ", "Jean-Luc")
        assert rules.clean_names("x", "Stéphanie McKay") == ("X", "Stéphanie McKay")  # tel que saisi

    @pytest.mark.parametrize("last,first", [("", "Julia"), ("DUPONT", "  "), (None, None)])
    def test_empty_refused(self, last, first):
        with pytest.raises(ValueError, match="obligatoires"):
            rules.clean_names(last, first)

    @pytest.mark.parametrize("last,first", [("Name of 213101", "First_name of 213101"),
                                            ("213101", "Student"), ("ID213101", "ID213101")])
    def test_placeholder_refused(self, last, first):
        with pytest.raises(ValueError, match="gabarit"):
            rules.clean_names(last, first)

    def test_first_name_length_limit(self):
        with pytest.raises(ValueError, match="maximum 30"):
            rules.clean_names("DUPONT", "A" * 31)

    def test_placeholder_names(self):
        assert rules.placeholder_names(213101) == ("Name of 213101", "First_name of 213101")
        assert rules.is_placeholder(*reversed(rules.placeholder_names(213101)), 213101)


def _row(state, verdict=rules.PERMIS, placeholder=False, has_bulletin=False):
    return {'id': 213101, 'slot': 1, 'state': state, 'verdict': verdict,
            'placeholder': placeholder, 'has_bulletin': has_bulletin,
            'last_name': 'ATIEH', 'first_name': 'Julia'}


class TestAvailableActions:
    def test_free_slot(self):
        a = rules.available_actions(_row(rules.LIBRE, placeholder=True))
        assert a[rules.INSCRIRE][0] and not a[rules.MODIFIER][0]
        assert not a[rules.PRESENCE][0] and not a[rules.REINITIALISER][0]
        assert "Inscrire" in a[rules.MODIFIER][1]

    def test_active_student(self):
        a = rules.available_actions(_row(rules.ACTIF))
        assert not a[rules.INSCRIRE][0]
        assert a[rules.MODIFIER][0] and a[rules.PRESENCE][0] and a[rules.REINITIALISER][0]

    def test_left_student(self):
        a = rules.available_actions(_row(rules.PARTI))
        assert a[rules.PRESENCE][0] and "présent" in a[rules.PRESENCE][1]

    def test_active_placeholder_e1_cannot_be_renamed(self):
        a = rules.available_actions(_row(rules.ACTIF, placeholder=True))
        assert not a[rules.MODIFIER][0] and a[rules.REINITIALISER][0]

    def test_reset_forbidden_explains_bulletin(self):
        a = rules.available_actions(_row(rules.ACTIF, rules.INTERDIT, has_bulletin=True))
        assert not a[rules.REINITIALISER][0] and "Bulletin" in a[rules.REINITIALISER][1]
        a = rules.available_actions(_row(rules.ACTIF, rules.INTERDIT, has_bulletin=None))
        assert "prudence" in a[rules.REINITIALISER][1]

    def test_reset_admin_says_so(self):
        a = rules.available_actions(_row(rules.ACTIF, rules.ADMIN))
        assert a[rules.REINITIALISER][0] and "admin" in a[rules.REINITIALISER][1]


class TestCampusOfClassroom:
    @pytest.mark.parametrize("cid,key", [(1131, 'maternelle'), (2111, 'maternelle'),
                                         (1141, 'primaire'), (2181, 'primaire'),
                                         (2231, 'college_lycee'), (1322, 'college_lycee')])
    def test_mapping(self, cid, key):
        assert campus_of_classroom(cid).key == key

    def test_unknown(self):
        assert campus_of_classroom(9991) is None


# ── Module d'écriture : uniquement des UPDATE ────────────────────────────

class TestWriteModuleSql:
    def test_no_insert_delete_or_ddl(self):
        src = Path(db_students_write.__file__).read_text(encoding='utf-8')
        code = re.sub(r'""".*?"""', '', src, flags=re.S)
        for kw in ("INSERT", "DELETE", "TRUNCATE", "ALTER", "DROP", "CREATE"):
            assert not re.search(rf'\b{kw}\b', code, re.I), kw

    def test_read_module_still_select_only(self):
        src = Path(db_students.__file__).read_text(encoding='utf-8')
        code = re.sub(r'""".*?"""', '', src, flags=re.S)
        assert not re.search(r'\b(UPDATE|INSERT|DELETE)\b', code, re.I)

    def test_note_reset_sql_direct_with_overrides(self):
        sql = db_students_write.build_note_reset_sql(
            'larcauth_learnermat_has_devpers_unit',
            [("a", "boolean", None), ("status", "USER-DEFINED", None)])
        assert sql.startswith("UPDATE larcauth_learnermat_has_devpers_unit t SET")
        assert '"a" = DEFAULT' in sql and '"status" = \'-\'' in sql
        assert "t.fk_student_id = %s" in sql and "IS TRUE" in sql

    def test_note_reset_sql_via_termsubject(self):
        sql = db_students_write.build_note_reset_sql(
            'larcauth_learnerdp_has_termsubjectdp', [("f13_note", "ARRAY", None)])
        assert "FROM larcauth_learner_has_termsubject lht" in sql
        assert "lht.id = t.learner_has_termsubject_ptr_id AND lht.fk_student_id = %s" in sql
        assert '"f13_note" = DEFAULT' in sql

    def test_subjectevals_flags_reset_to_false(self):
        sql = db_students_write.build_note_reset_sql(
            'larcauth_learnermat_has_subjectevals_unit',
            [("a", "boolean", None), ("value", "smallint", "0")])
        assert '"a" = FALSE' in sql and '"value" = DEFAULT' in sql


# ── Inscrire ─────────────────────────────────────────────────────────────

class TestInscrire:
    def test_free_slot_enrolled_in_one_transaction(self):
        cur = ScriptCursor(FREE)
        with _run_with(cur):
            res = db_students_write.inscrire(213105, "dupont", "léa")
        assert res.ok, res.message
        sql = cur.sql()
        assert sql[0] == "BEGIN" and sql[-1] == "COMMIT"
        assert "FOR UPDATE OF s, a" in sql[1]
        names = next(p for s, p in cur.executed if s.startswith("UPDATE larcauth_aecuser"))
        assert names == ("DUPONT", "Léa", 213105, "Name of 213105", "First_name of 213105")
        st_sql, st_params = next((s, p) for s, p in cur.executed
                                 if s.startswith("UPDATE larcauth_student"))
        assert "enabled = TRUE" in st_sql and "WHERE aecuser_ptr_id = %s AND enabled = FALSE" in st_sql
        assert st_params == (2131, 213105)
        assert res.counts == {'larcauth_aecuser': 1, 'larcauth_student': 1}

    def test_warns_on_duplicate_but_enrols(self):
        cur = ScriptCursor(FREE, duplicates=[(214101, 2141, "DUPONT", "Léa")])
        with _run_with(cur):
            res = db_students_write.inscrire(213105, "Dupont", "Léa")
        assert res.ok and "214101" in res.warnings[0]

    @pytest.mark.parametrize("slot,msg", [
        (ACTIVE, "plus libre"),
        ((False, "Adam", "BACHAR", 2131, True), "plus libre"),        # parti ≠ libre
        ((False, "Student", "213105", 2131, False), "désactivée"),   # classe désactivée
    ])
    def test_refusals_rollback_without_update(self, slot, msg):
        cur = ScriptCursor(slot)
        with _run_with(cur):
            res = db_students_write.inscrire(213105, "DUPONT", "Léa")
        assert not res.ok and msg in res.message
        assert cur.sql()[-1] == "ROLLBACK" and not cur.updates()

    def test_invalid_name_never_touches_db(self):
        cur = ScriptCursor(FREE)
        with _run_with(cur):
            res = db_students_write.inscrire(213105, "Name of 213105", "First_name of 213105")
        assert not res.ok and not cur.executed

    def test_concurrent_change_rolls_back(self):
        cur = ScriptCursor(FREE, rowcounts={"UPDATE larcauth_student": 0})
        with _run_with(cur):
            res = db_students_write.inscrire(213105, "DUPONT", "Léa")
        assert not res.ok and "modifiée entre-temps" in res.message
        assert cur.sql()[-1] == "ROLLBACK" and "COMMIT" not in cur.sql()

    def test_db_error_rolls_back(self):
        cur = ScriptCursor(FREE, fail_on="UPDATE larcauth_student")
        with _run_with(cur):
            res = db_students_write.inscrire(213105, "DUPONT", "Léa")
        assert not res.ok and "rien n'a été modifié" in res.message
        assert cur.sql()[-1] == "ROLLBACK"

    def test_no_connection(self):
        with patch.object(db_students_write, "_conn", return_value=None):
            assert not db_students_write.inscrire(213105, "DUPONT", "Léa").ok


# ── Présent / absent ─────────────────────────────────────────────────────

class TestSetPresent:
    def test_absent_college_disables_enrolments(self):
        cur = ScriptCursor((True, "Julia", "ATIEH", 2231, True),
                           rowcounts={"larcauth_learner_has_termsubject": 9})
        with _run_with(cur):
            res = db_students_write.set_present(223101, False)
        assert res.ok and "9 matière" in res.message
        assert res.counts == {'larcauth_learner_has_termsubject': 9, 'larcauth_student': 1}
        st = next(p for s, p in cur.executed if s.startswith("UPDATE larcauth_student"))
        assert st == (False, 223101, True)
        assert cur.sql()[-1] == "COMMIT"

    def test_absent_maternelle_keeps_enrolments(self):
        cur = ScriptCursor((True, "Julia", "ATIEH", 1131, True))
        with _run_with(cur):
            res = db_students_write.set_present(113101, False)
        assert res.ok
        assert not any("learner_has_termsubject" in s for s in cur.updates())

    def test_present_again_without_reenrolment(self):
        cur = ScriptCursor(LEFT)
        with _run_with(cur):
            res = db_students_write.set_present(225124, True)
        assert res.ok and "Matières par classe" in res.message
        assert cur.updates() == [s for s in cur.updates() if s.startswith("UPDATE larcauth_student")]

    def test_refuses_free_slot_and_same_state(self):
        for slot, enabled in ((FREE, True), (ACTIVE, True)):
            cur = ScriptCursor(slot)
            with _run_with(cur):
                res = db_students_write.set_present(213105, enabled)
            assert not res.ok and not cur.updates() and cur.sql()[-1] == "ROLLBACK"


# ── Renommer ─────────────────────────────────────────────────────────────

class TestRename:
    def test_rename_active_guarded_by_old_names(self):
        cur = ScriptCursor(ACTIVE)
        with _run_with(cur):
            res = db_students_write.rename(223101, "Atieh", "julia")
        assert res.ok
        params = next(p for s, p in cur.executed if s.startswith("UPDATE larcauth_aecuser"))
        assert params == ("ATIEH", "Julia", 223101, "ATIEH", "Julia")

    def test_refused_on_placeholder(self):
        cur = ScriptCursor(FREE)
        with _run_with(cur):
            res = db_students_write.rename(213105, "DUPONT", "Léa")
        assert not res.ok and "Inscrire" in res.message and not cur.updates()


# ── Réinitialiser ────────────────────────────────────────────────────────

class TestReinitialiser:
    def test_permis_frees_slot_without_touching_notes(self):
        cur = ScriptCursor((True, "Julia", "ATIEH", 1131, True), notes=0, bulletin=False,
                           rowcounts={"larcauth_learner_has_termsubject": 0})
        with _run_with(cur):
            res = db_students_write.reinitialiser(113101)
        assert res.ok, res.message
        ups = cur.updates()
        assert not any("learnermat" in s for s in ups)
        names = next(p for s, p in cur.executed if s.startswith("UPDATE larcauth_aecuser"))
        assert names[:2] == ("Name of 113101", "First_name of 113101")
        st = next(p for s, p in cur.executed if s.startswith("UPDATE larcauth_student"))
        assert st == (1131, 113101, True)
        assert cur.sql()[-1] == "COMMIT"

    def test_interdit_refused_server_side(self):
        cur = ScriptCursor(ACTIVE, notes=3, bulletin=True)
        with _run_with(cur):
            res = db_students_write.reinitialiser(223101, accept_notes_erase=True)
        assert not res.ok and "bulletin" in res.message
        assert not cur.updates() and cur.sql()[-1] == "ROLLBACK"

    def test_admin_needs_explicit_confirmation(self):
        cur = ScriptCursor(ACTIVE, notes=4, bulletin=False)
        with _run_with(cur):
            res = db_students_write.reinitialiser(223101)
        assert not res.ok and "administrateur" in res.message and not cur.updates()

    def test_admin_confirmed_erases_notes_per_table(self):
        cur = ScriptCursor(ACTIVE, notes=4, bulletin=False,
                           rowcounts={"UPDATE larcauth_learnerpei": 4,
                                      "UPDATE larcauth_learnerdp": 2,
                                      "UPDATE larcauth_learner_has_termothersubject": 1,
                                      "UPDATE larcauth_learner_has_termsubject": 9})
        with _run_with(cur):
            res = db_students_write.reinitialiser(223101, accept_notes_erase=True)
        assert res.ok, res.message
        assert res.counts['larcauth_learnerpei_has_termsubjectpei'] == 4
        assert res.counts['larcauth_learnerdp_has_termsubjectdp'] == 2
        assert res.counts['larcauth_learner_has_termothersubject'] == 1
        assert res.counts['larcauth_learner_has_termsubject'] == 9
        assert "7 ligne(s) de notes" in res.message
        # bulletin jamais touché : aucune écriture sur learner_has_term
        assert not any("larcauth_learner_has_term " in s or "learner_has_term SET" in s
                       for s in cur.updates())
        assert cur.sql()[-1] == "COMMIT"

    def test_left_student_can_be_reset(self):
        cur = ScriptCursor(LEFT, notes=0, bulletin=False)
        with _run_with(cur):
            res = db_students_write.reinitialiser(225124)
        assert res.ok
        st = next(p for s, p in cur.executed if s.startswith("UPDATE larcauth_student"))
        assert st == (2251, 225124, False)

    def test_already_free(self):
        cur = ScriptCursor(FREE)
        with _run_with(cur):
            res = db_students_write.reinitialiser(213105)
        assert not res.ok and "déjà libre" in res.message

    def test_failure_during_note_erase_rolls_back_everything(self):
        cur = ScriptCursor(ACTIVE, notes=4, bulletin=False, fail_on="UPDATE larcauth_learnerdp")
        with _run_with(cur):
            res = db_students_write.reinitialiser(223101, accept_notes_erase=True)
        assert not res.ok and cur.sql()[-1] == "ROLLBACK" and "COMMIT" not in cur.sql()


# ── Interface (offscreen, base simulée) ──────────────────────────────────

@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_name_dialog_validates_before_closing(qapp):
    from LarcConfig.views.eleve_dialogs import NameDialog
    dlg = NameDialog(None, "Inscrire", "Place 05", note=rules.SECRETARY_NOTE['maternelle'])
    dlg._last.setText("Name of 213105")
    dlg._first.setText("First_name of 213105")
    dlg.accept()
    assert dlg.values is None and not dlg._error.isHidden() and "gabarit" in dlg._error.text()
    dlg._last.setText("dupont")
    dlg._first.setText("léa")
    dlg.accept()
    assert dlg.values == ("DUPONT", "Léa")


def test_action_bar_states_and_menu(qapp):
    from LarcConfig.common.campus import get_campus
    from LarcConfig.views.eleves_actions import SlotActionBar
    bar = SlotActionBar(get_campus('college_lycee'))
    assert not any(b.isEnabled() for b in bar._buttons.values())
    bar.set_row(_row(rules.LIBRE, placeholder=True))
    assert bar._buttons[rules.INSCRIRE].isEnabled()
    assert not bar._buttons[rules.REINITIALISER].isEnabled()
    assert bar._buttons[rules.REINITIALISER].toolTip() == "Place déjà libre."
    bar.set_row(_row(rules.ACTIF, rules.INTERDIT, has_bulletin=True))
    assert not bar._buttons[rules.REINITIALISER].isEnabled()
    assert "Bulletin" in bar._buttons[rules.REINITIALISER].toolTip()
    assert bar._buttons[rules.PRESENCE].text() == "Marquer absent (parti)"
    menu = bar.menu_for(_row(rules.PARTI), None)
    labels = [a.text() for a in menu.actions()]
    assert "Marquer présent" in labels and len(labels) == 4


def test_action_bar_runs_confirmed_write_and_signals(qapp):
    from LarcConfig.common.campus import get_campus
    from LarcConfig.views import eleves_actions
    bar = eleves_actions.SlotActionBar(get_campus('college_lycee'))
    bar.set_row(_row(rules.ACTIF))
    seen = []
    bar.changed.connect(lambda: seen.append(True))
    ok = db_students_write.ActionResult(True, "fait", {'larcauth_student': 1})
    with patch.object(eleves_actions.db_students, "get_action_preview",
                      return_value={'enrolments': 9, 'notes': {}, 'bulletin': False}), \
            patch.object(eleves_actions, "confirm", return_value=True) as conf, \
            patch.object(eleves_actions.db_students_write, "set_present",
                         return_value=ok) as write, \
            patch.object(eleves_actions.QMessageBox, "information"):
        bar.run(rules.PRESENCE)
    write.assert_called_once_with(213101, False)
    assert "Désinscrit(e) de 9 matière(s)." in conf.call_args[0][2]
    assert seen == [True]


def test_action_bar_cancel_writes_nothing(qapp):
    from LarcConfig.common.campus import get_campus
    from LarcConfig.views import eleves_actions
    bar = eleves_actions.SlotActionBar(get_campus('maternelle'))
    bar.set_row(_row(rules.ACTIF, rules.ADMIN))
    with patch.object(eleves_actions.db_students, "get_action_preview",
                      return_value={'enrolments': 0,
                                    'notes': {'larcauth_learnermat_has_devpers_unit': 34,
                                              'larcauth_learnermat_has_subjectevals_unit': 2},
                                    'bulletin': False}), \
            patch.object(eleves_actions, "confirm", return_value=False) as conf, \
            patch.object(eleves_actions.db_students_write, "reinitialiser") as write:
        bar.run(rules.REINITIALISER)
    write.assert_not_called()
    lines = conf.call_args[0][2]
    assert any("Efface 36 ligne(s) de notes dans 2 table(s)" in line for line in lines)
    assert conf.call_args[1]['danger'] is True
