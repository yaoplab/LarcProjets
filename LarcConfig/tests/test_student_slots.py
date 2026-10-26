"""Tests étape 3 — règles des slots élèves, SQL en lecture seule, écrans campus.

Les formats de placeholder testés sont ceux relevés en base le 2026-10-26
(SELECT sur les 2400 slots).
"""
import re
from pathlib import Path
from unittest.mock import patch

import larccommon  # noqa: F401 — initialiser larccommon AVANT phibuilder (import circulaire connu)
import pytest

from LarcConfig.common import db_students
from LarcConfig.common import student_slot_rules as rules
from LarcConfig.tests.test_db_enrolment import _fake

# ── Règles pures ─────────────────────────────────────────────────────────

class TestSlotNumbering:
    def test_slot_number_and_classroom(self):
        assert rules.slot_number(213140) == 40
        assert rules.slot_number(113101) == 1
        assert rules.classroom_of(213140) == 2131


class TestPlaceholderPatterns:
    @pytest.mark.parametrize("first,last,sid", [
        ("First_name of 123139", "Name of 123139", 123139),
        ("Prénom de  211105", "Nom de  211105", 211105),
        ("Prénom de 114207", "Nom de 114207", 114207),
        ("Prenom de 216101", "Nom de 216101", 216101),
        ("ID221201", "ID221201", 221201),
        ("ID11810", "ID118108", 118108),          # faute de frappe dans le prénom
        ("Student", " 111140", 111140),
        ("Student", "211109", 211109),
        (" Student", "111108", 111108),
        (" Studebnt", "112110", 112110),
        (" Imrane", "112111", 112111),            # prénom résiduel, nom = ID
        ("Student", "2121120", 212120),           # ID mal tapé
        ("112108 Firstname", "112108 Name", 112108),
        ("Fist Name", "Name", 111106),
        ("First Name", "Name", 111106),
        ("FIrstname", "Lastname", 111107),
    ])
    def test_recognised(self, first, last, sid):
        assert rules.is_placeholder(first, last, sid)

    @pytest.mark.parametrize("first,last", [
        ("Nameer", "BEEDASSY"),
        ("Victoria", "EDOH"),
        ("Stéphanie Nshira", "NIMAKO "),
        ("Student", "DUPONT"),     # prénom générique seul ne suffit pas
        ("Nomena", "RAKOTO"),
    ])
    def test_real_names_not_placeholders(self, first, last):
        assert not rules.is_placeholder(first, last, 123101)

    def test_without_id(self):
        assert rules.is_placeholder("First_name of 1", "Name of 1")


class TestSlotState:
    def test_states(self):
        assert rules.slot_state(True, False) == rules.ACTIF
        assert rules.slot_state(True, True) == rules.ACTIF       # mais anomalie E1
        assert rules.slot_state(False, True) == rules.LIBRE
        assert rules.slot_state(False, False) == rules.PARTI


def _ctx(**kw):
    base = dict(student_id=213101, first_name="Julia", last_name="ATIEH", enabled=True,
                s_classroom_id=2131)
    base.update(kw)
    return rules.SlotContext(**base)


class TestAnomalies:
    def test_clean_slot(self):
        assert rules.slot_anomalies(_ctx()) == []

    def test_e1_active_placeholder(self):
        assert 'E1' in rules.slot_anomalies(_ctx(first_name="Student", last_name="213101"))

    def test_e2_empty_name(self):
        assert 'E2' in rules.slot_anomalies(_ctx(first_name="  "))
        assert 'E2' in rules.slot_anomalies(_ctx(last_name=None))

    def test_e3_wrong_classroom(self):
        assert 'E3' in rules.slot_anomalies(_ctx(s_classroom_id=2141))

    def test_e4_is_class_level(self):
        assert rules.class_anomalies(39) == ['E4']
        assert rules.class_anomalies(40) == []

    def test_e5_duplicate_text_names_other_slot(self):
        ctx = _ctx(duplicate_of=((214101, 2141),))
        assert 'E5' in rules.slot_anomalies(ctx)
        assert "214101 (2141)" in rules.anomaly_text('E5', ctx)

    def test_e5_ignored_when_inactive(self):
        assert 'E5' not in rules.slot_anomalies(_ctx(enabled=False, duplicate_of=((1, 2),)))

    def test_e6_active_in_disabled_class(self):
        assert 'E6' in rules.slot_anomalies(_ctx(classroom_enabled=False))

    def test_e7_only_when_subjects_needed(self):
        assert 'E7' in rules.slot_anomalies(_ctx(needs_subjects=True, active_subjects=0))
        assert 'E7' not in rules.slot_anomalies(_ctx(needs_subjects=True, active_subjects=6))
        assert 'E7' not in rules.slot_anomalies(_ctx(needs_subjects=False, active_subjects=0))

    def test_duplicate_key_keeps_accents(self):
        assert rules.normalize_name(" NIMAKO ", "Stéphanie  Nshira") == ("nimako", "stéphanie nshira")
        assert rules.normalize_name("A", "Stephanie") != rules.normalize_name("A", "Stéphanie")


class TestResetVerdict:
    @pytest.mark.parametrize("notes,bulletin,expected", [
        (False, False, rules.PERMIS),
        (True, False, rules.ADMIN),
        (False, True, rules.INTERDIT),
        (True, True, rules.INTERDIT),
        (None, False, rules.INTERDIT),
        (False, None, rules.INTERDIT),
        (None, None, rules.INTERDIT),
    ])
    def test_verdict(self, notes, bulletin, expected):
        assert rules.reset_verdict(notes, bulletin) == expected


class TestAssessAndSummarize:
    def _slots(self):
        return [
            {'id': 213101, 'first_name': 'Julia', 'last_name': 'ATIEH', 'enabled': True,
             's_classroom_id': 2131},
            {'id': 213102, 'first_name': 'Student', 'last_name': ' 213102', 'enabled': False,
             's_classroom_id': 2131},
            {'id': 213103, 'first_name': 'Adam', 'last_name': 'BACHAR', 'enabled': False,
             's_classroom_id': 2131},
        ]

    def test_assess_states_verdicts_and_unknown_evidence(self):
        ev = {213101: {'notes': 3, 'has_notes': True, 'has_bulletin': False}}
        rows = rules.assess_slots(self._slots(), True, {213101: ((214101, 2141),)}, {}, False, ev)
        assert [r['state'] for r in rows] == [rules.ACTIF, rules.LIBRE, rules.PARTI]
        assert rows[0]['verdict'] == rules.ADMIN
        assert rows[0]['anomalies'] == ['E5']
        assert rows[1]['verdict'] == rules.INTERDIT   # preuves absentes = inconnu
        assert rows[0]['slot'] == 1

    def test_summary_counts_class_level_e4(self):
        rows = rules.assess_slots(self._slots(), True, {}, {}, False)
        s = rules.summarize(rows)
        assert (s['actif'], s['libre'], s['parti']) == (1, 1, 1)
        assert s['class_anomalies'] == ['E4'] and s['anomalies'] == 1


# ── SQL en lecture seule ─────────────────────────────────────────────────

class TestReadOnlyModule:
    def test_no_write_statement_in_source(self):
        src = Path(db_students.__file__).read_text(encoding='utf-8')
        code = re.sub(r'""".*?"""', '', src, flags=re.S)  # docstrings exclues
        for kw in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALTER", "DROP", "CREATE"):
            assert not re.search(rf'\b{kw}\b', code, re.I), kw


class TestSlotsSql:
    def test_get_slots_uses_id_range_of_class(self):
        conn, cur = _fake([], ['id'])
        with patch.object(db_students, "_conn", return_value=conn):
            db_students.get_slots(2131)
        sql, params = cur.executed[0]
        assert "BETWEEN %s AND %s" in sql and "larcauth_aecuser" in sql
        assert params == (213101, 213140)

    def test_slots_for_classes_escapes_modulo(self):
        conn, cur = _fake([], ['id'])
        with patch.object(db_students, "_conn", return_value=conn):
            db_students.get_slots_for_classes([1131, 1132])
        sql, params = cur.executed[0]
        assert "%% 100" in sql
        assert params == (1131, 1132, 40)

    def test_duplicates_group_active_students(self):
        rows = [(213101, 'Julia', 'ATIEH', True, 2131), (214101, 'Julia ', ' atieh', True, 2141),
                (215101, 'Ali', 'JABER', True, 2151)]
        conn, cur = _fake(rows, ['id', 'first_name', 'last_name', 'enabled', 's_classroom_id'])
        with patch.object(db_students, "_conn", return_value=conn):
            dup = db_students.get_duplicates()
        assert "s.enabled = TRUE" in cur.executed[0][0]
        assert dup == {213101: ((214101, 2141),), 214101: ((213101, 2131),)}

    def test_active_subjects_filters_term_and_classes(self):
        conn, cur = _fake([], ['id', 'n'])
        with patch.object(db_students, "_conn", return_value=conn):
            db_students.get_active_subject_counts([2231], 1)
        sql, params = cur.executed[0]
        assert "lht.enabled = TRUE AND cts.enabled = TRUE AND cts.fk_term_id = %s" in sql
        assert params == (1, 2231)


class TestEvidenceSql:
    def test_filled_expr_by_type(self):
        assert "cardinality" in db_students.filled_expr("f13_note_a", "ARRAY", None)
        assert db_students.filled_expr("a", "boolean", None) == '(t."a" IS TRUE)'
        e = db_students.filled_expr("S09_note_f", "smallint", None)
        assert 't."S09_note_f" IS NOT NULL' in e and "'-'" in e
        assert "<> '0'" in db_students.filled_expr("value", "smallint", "0")

    def test_default_literal(self):
        assert db_students._default_literal("'-'::character varying") == '-'
        assert db_students._default_literal("0") == '0'
        assert db_students._default_literal(None) is None

    def test_note_columns_filters_and_caches(self):
        cols = [("id", "integer", None), ("fk_student_id", "integer", None),
                ("f_la_a", "smallint", None), ("b_la", "boolean", "true"),
                ("c_la", "character varying", None), ("nb_mat", "smallint", "0"),
                ("note_on_max", "smallint", "0"), ("unit_comment", "text", None)]
        cur = _fake(cols, ['column_name'])[1]
        db_students._COLS_CACHE.pop('larcauth_learnerprim_has_unit_period', None)
        got = db_students.note_columns('larcauth_learnerprim_has_unit_period', cur)
        assert [n for n, _d, _df in got] == ['f_la_a', 'c_la', 'note_on_max']
        assert db_students.note_columns('larcauth_learnerprim_has_unit_period', None) is got
        db_students._COLS_CACHE.pop('larcauth_learnerprim_has_unit_period', None)

    def test_pei_excludes_observations_and_flags(self):
        cols = [("f01_note_a", "smallint", None), ("f01_observation", "character varying", None),
                ("f13_obsersation", "character varying", None), ("note_on_7", "smallint", None),
                ("note_on_7_checked", "boolean", "false"), ("jgt_a", "smallint", None)]
        cur = _fake(cols, ['column_name'])[1]
        db_students._COLS_CACHE.pop('larcauth_learnerpei_has_termsubjectpei', None)
        got = db_students.note_columns('larcauth_learnerpei_has_termsubjectpei', cur)
        assert [n for n, _d, _df in got] == ['f01_note_a', 'note_on_7', 'jgt_a']
        db_students._COLS_CACHE.pop('larcauth_learnerpei_has_termsubjectpei', None)

    def test_notes_sql_joins_termsubject_for_child_tables(self):
        sql = db_students.build_notes_sql('larcauth_learnerdp_has_termsubjectdp',
                                          [("f01_note", "double precision", None)])
        assert "JOIN larcauth_learner_has_termsubject lht" in sql
        assert "lht.fk_student_id BETWEEN %s AND %s" in sql

    def test_notes_sql_direct_student_column(self):
        sql = db_students.build_notes_sql('larcauth_learnermat_has_devpers_unit',
                                          [("status", "USER-DEFINED", None)])
        assert "t.fk_student_id BETWEEN %s AND %s" in sql and "FILTER" in sql

    def test_bulletin_rules_per_campus(self):
        assert "larcauth_learner_has_term t" in db_students.build_bulletin_sql('college_lycee')
        assert "t.enabled = TRUE" in db_students.build_bulletin_sql('primaire')
        mat = db_students.build_bulletin_sql('maternelle')
        assert "unit_average_global_on_20" in mat and "enabled" not in mat

    def test_evidence_unknown_when_table_unreadable(self):
        class Boom(Exception):
            pass

        conn, cur = _fake([], ['id'])

        def explode(sql, params=None):
            raise Boom("table illisible")
        cur.execute = explode
        with patch.object(db_students, "_conn", return_value=conn), \
                patch.object(db_students, "_report"):
            ev = db_students.get_evidence(1131, 'maternelle')
        assert len(ev) == 40
        assert ev[113101] == {'notes': None, 'has_notes': None, 'has_bulletin': None}

    def test_evidence_counts_notes_and_bulletin(self):
        conn, cur = _fake([], ['id'])
        answers = iter([
            [(214101, 2), (214102, 0)],        # learnerprim
            [(214101, 1)],                     # learnerpp
            [(214101, True), (214102, False)],  # bulletin
        ])
        cur.fetchall = lambda: next(answers)
        db_students._COLS_CACHE.update({
            'larcauth_learnerprim_has_unit_period': [("f_la", "smallint", None)],
            'larcauth_learnerpp_has_termsubjectpp': [("f01_note", "double precision", None)],
        })
        try:
            with patch.object(db_students, "_conn", return_value=conn):
                ev = db_students.get_evidence(2141, 'primaire')
        finally:
            db_students._COLS_CACHE.pop('larcauth_learnerprim_has_unit_period', None)
            db_students._COLS_CACHE.pop('larcauth_learnerpp_has_termsubjectpp', None)
        assert ev[214101] == {'notes': 3, 'has_notes': True, 'has_bulletin': True}
        assert ev[214102] == {'notes': 0, 'has_notes': False, 'has_bulletin': False}
        assert ev[214140]['has_notes'] is False


# ── Écrans (offscreen, base simulée) ─────────────────────────────────────

@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


_CLASSES = [
    {'id': 2131, 'label': 'PP-GS', 'enabled': True, 'program_sigle': 'PP', 'level_label': 'PP/GS',
     'slots': 40, 'actifs': 15, 'libres': 25, 'partis': 0, 'anomalies': 11},
    {'id': 2132, 'label': 'PP-GS - B', 'enabled': False, 'program_sigle': 'PP',
     'level_label': 'PP/GS', 'slots': 40, 'actifs': 0, 'libres': 40, 'partis': 0, 'anomalies': 0},
]


def test_campus_classes_panel_builds(qapp):
    from LarcConfig.views import campus_classes
    with patch.object(campus_classes.db_students, "get_classes_overview", return_value=_CLASSES):
        panel = campus_classes.CampusClassesPanel({}, 'maternelle')
    assert panel._table.rowCount() == 2
    assert panel._table.item(0, 4).text() == "15/40"
    assert "1 classes actives sur 2" in panel._banner.text()
    panel._restyle()


def test_eleves_panel_builds_and_filters(qapp):
    from LarcConfig.views import eleves_classe
    rows = rules.assess_slots([
        {'id': 213101, 'first_name': 'Julia', 'last_name': 'ATIEH', 'enabled': True,
         's_classroom_id': 2131},
        {'id': 213102, 'first_name': 'Student', 'last_name': ' 213102', 'enabled': False,
         's_classroom_id': 2131},
    ], True, {213101: ((214101, 2141),)}, {}, False,
        {213101: {'notes': 2, 'has_notes': True, 'has_bulletin': False},
         213102: {'notes': 0, 'has_notes': False, 'has_bulletin': False}})
    with patch.object(eleves_classe.db_enrolment, "get_programs",
                      return_value=[{'id': 21, 'sigle': 'PP', 'label': 'PP'}]), \
            patch.object(eleves_classe.db_enrolment, "get_classrooms",
                         return_value=[_CLASSES[0], _CLASSES[1]]), \
            patch.object(eleves_classe.db_students, "get_class_detail", return_value=rows):
        panel = eleves_classe.ElevesClassePanel({}, 'maternelle')
        assert panel._class_combo.count() == 1          # classe B désactivée exclue
        assert panel._table.rowCount() == 2
        assert panel._table.item(0, 7).text() == "Admin"
        assert panel._table.item(1, 7).text() == "Permise"
        assert panel._table.item(0, 8).text().startswith("E5")
        assert "1 actifs · 1 libres · 0 partis" in panel._banner.text()
        panel._only_anomalies.setChecked(True)
        assert panel._table.rowCount() == 1
        panel._restyle()


# ── Légende et infobulles (retour Pat 2026-10-26) ────────────────────────

class TestLegendTexts:
    def test_help_covers_every_code_state_and_verdict(self):
        assert set(rules.ANOMALY_HELP) == {f"E{i}" for i in range(1, 8)} == set(rules.ANOMALIES)
        assert set(rules.STATE_HELP) == {rules.ACTIF, rules.LIBRE, rules.PARTI}
        assert set(rules.VERDICT_HELP) == {rules.PERMIS, rules.ADMIN, rules.INTERDIT}

    def test_summary_counts_codes(self):
        rows = rules.assess_slots(
            [{'id': 213101, 'first_name': 'Student', 'last_name': '213101', 'enabled': True,
              's_classroom_id': 2131}], True, {213101: ((214101, 2141),)}, {}, False)
        s = rules.summarize(rows)
        assert s['codes'] == {'E1': 1, 'E4': 1, 'E5': 1}
        lines = rules.anomaly_summary_lines({'E5': 11})
        assert lines == ["E5 × 11 — " + rules.ANOMALY_HELP['E5']]


def test_legend_builds_with_all_codes(qapp):
    from PySide6.QtWidgets import QLabel

    from LarcConfig.views.slot_legend import VERDICT_LABELS, SlotLegend
    legend = SlotLegend()
    assert legend.codes() == [f"E{i}" for i in range(1, 8)]
    texts = " ".join(lbl.text() for lbl in legend.findChildren(QLabel))
    for help_text in (list(rules.ANOMALY_HELP.values()) + list(rules.STATE_HELP.values())
                      + [rules.ANOMALY_MARK_HELP]):
        assert help_text in texts
    for verdict, help_text in rules.VERDICT_HELP.items():
        assert help_text.split(" : ", 1)[1] in texts      # phrase sans le libellé…
        assert VERDICT_LABELS[verdict] in texts            # …porté par le repère coloré
    for code in rules.ANOMALY_HELP:
        assert code in texts
    legend.show()
    legend._toggle.setChecked(False)
    assert not legend._frame.isVisible()
    assert legend._toggle.text() == "Afficher la légende"
    legend._restyle()


def test_eleves_cells_carry_tooltips(qapp):
    from PySide6.QtCore import Qt

    from LarcConfig.views import eleves_classe
    rows = rules.assess_slots([
        {'id': 213101, 'first_name': 'Julia', 'last_name': 'ATIEH', 'enabled': True,
         's_classroom_id': 2131},
    ], True, {213101: ((214101, 2141),)}, {}, False,
        {213101: {'notes': 2, 'has_notes': True, 'has_bulletin': False}})
    with patch.object(eleves_classe.db_enrolment, "get_programs",
                      return_value=[{'id': 21, 'sigle': 'PP', 'label': 'PP'}]), \
            patch.object(eleves_classe.db_enrolment, "get_classrooms",
                         return_value=[_CLASSES[0]]), \
            patch.object(eleves_classe.db_students, "get_class_detail", return_value=rows):
        panel = eleves_classe.ElevesClassePanel({}, 'maternelle')
    assert panel._legend.codes() == [f"E{i}" for i in range(1, 8)]
    tip = panel._table.item(0, 8).data(Qt.ToolTipRole)
    assert "214101 (2141)" in tip and rules.ANOMALY_HELP['E5'] in tip
    assert panel._table.item(0, 2).data(Qt.ToolTipRole) == rules.STATE_HELP[rules.ACTIF]
    assert panel._table.item(0, 7).data(Qt.ToolTipRole) == rules.VERDICT_HELP[rules.ADMIN]
    assert panel._table.item(0, 6).data(Qt.ToolTipRole) == "Aucun bulletin"


def test_campus_classes_anomaly_codes_visible_and_tooltip(qapp):
    from PySide6.QtCore import Qt

    from LarcConfig.views import campus_classes
    data = [dict(_CLASSES[0], codes={'E1': 3, 'E5': 8}), _CLASSES[1]]
    with patch.object(campus_classes.db_students, "get_classes_overview", return_value=data):
        panel = campus_classes.CampusClassesPanel({}, 'maternelle')
    cell = panel._table.item(0, 7)
    assert cell.text() == "11 · E1, E5"
    tip = cell.data(Qt.ToolTipRole)
    assert "E1 × 3" in tip and "E5 × 8" in tip and rules.ANOMALY_HELP['E1'] in tip
    assert panel._table.item(1, 7).text() == "0"
    assert not panel._table.item(1, 7).data(Qt.ToolTipRole)
