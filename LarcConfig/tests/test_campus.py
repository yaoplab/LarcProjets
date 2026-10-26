"""Tests campus — découpage Maternelle / Primaire / Collège & Lycée (étape 2).

Définitions pures (common/campus.py), filtre SQL de db_enrolment par campus
et structure du menu de ConfigWindow (constantes de module, sans widget).
"""
from unittest.mock import patch

import larccommon  # noqa: F401 — initialiser larccommon AVANT phibuilder (import circulaire connu)

from LarcConfig.common import db_enrolment
from LarcConfig.common.campus import CAMPUSES, Campus, get_campus
from LarcConfig.tests.test_db_enrolment import _fake


class TestCampusDefinitions:
    def test_three_campuses_in_menu_order(self):
        assert [cp.key for cp in CAMPUSES] == ['maternelle', 'primaire', 'college_lycee']

    def test_maternelle_is_pyp_and_pp_levels_1_to_3(self):
        cp = get_campus('maternelle')
        assert cp.label == 'Maternelle'
        assert set(cp.program_ids) == {11, 21}
        assert cp.levels == (1, 2, 3)

    def test_primaire_is_pyp_and_pp_levels_4_to_8(self):
        cp = get_campus('primaire')
        assert cp.label == 'Primaire'
        assert set(cp.program_ids) == {11, 21}
        assert cp.levels == (4, 5, 6, 7, 8)

    def test_college_lycee_is_myp_dp_pei_all_levels(self):
        cp = get_campus('college_lycee')
        assert cp.label == 'Collège & Lycée'
        assert set(cp.program_ids) == {12, 13, 22, 23}
        assert cp.levels is None

    def test_college_lycee_matches_historic_perimeter(self):
        assert set(get_campus('college_lycee').program_ids) == set(db_enrolment.PROGRAM_IDS)

    def test_maternelle_and_primaire_levels_do_not_overlap(self):
        assert not set(get_campus('maternelle').levels) & set(get_campus('primaire').levels)

    def test_no_program_shared_between_primary_and_secondary(self):
        prim = set(get_campus('maternelle').program_ids) | set(get_campus('primaire').program_ids)
        assert not prim & set(get_campus('college_lycee').program_ids)

    def test_unknown_key_returns_none(self):
        assert get_campus('lycee') is None

    def test_campus_is_frozen(self):
        cp = get_campus('primaire')
        try:
            cp.label = 'x'
        except AttributeError:
            return
        raise AssertionError("Campus doit être immuable (frozen)")


class TestDbEnrolmentCampusFilter:
    def test_classrooms_maternelle_filters_programs_and_levels(self):
        conn, cur = _fake([], ['id'])
        with patch.object(db_enrolment, "_conn", return_value=conn):
            db_enrolment.get_classrooms(campus=get_campus('maternelle'))
        sql, params = cur.executed[0]
        assert "lv.fk_program_id IN (%s, %s)" in sql
        assert "lv.level_in_pgm IN (%s, %s, %s)" in sql
        assert params == (11, 21, 1, 2, 3)

    def test_classrooms_campus_and_program_combined(self):
        conn, cur = _fake([], ['id'])
        with patch.object(db_enrolment, "_conn", return_value=conn):
            db_enrolment.get_classrooms(program_id=21, campus=get_campus('primaire'))
        sql, params = cur.executed[0]
        assert "lv.level_in_pgm IN" in sql
        assert "lv.fk_program_id = %s" in sql
        assert params == (11, 21, 4, 5, 6, 7, 8, 21)

    def test_classrooms_college_lycee_has_no_level_filter(self):
        conn, cur = _fake([], ['id'])
        with patch.object(db_enrolment, "_conn", return_value=conn):
            db_enrolment.get_classrooms(campus=get_campus('college_lycee'))
        sql, params = cur.executed[0]
        assert "level_in_pgm IN" not in sql
        assert set(params) == {12, 13, 22, 23}

    def test_classrooms_without_campus_unchanged(self):
        conn, cur = _fake([], ['id'])
        with patch.object(db_enrolment, "_conn", return_value=conn):
            db_enrolment.get_classrooms()
        sql, params = cur.executed[0]
        assert "WHERE lv.fk_program_id IN (%s, %s, %s, %s) ORDER BY" in sql
        assert params == db_enrolment.PROGRAM_IDS

    def test_programs_for_campus(self):
        conn, cur = _fake([], ['id'])
        with patch.object(db_enrolment, "_conn", return_value=conn):
            db_enrolment.get_programs(campus=Campus('x', 'X', (11, 21)))
        sql, params = cur.executed[0]
        assert "WHERE id IN (%s, %s)" in sql
        assert params == (11, 21)

    def test_programs_without_campus_unchanged(self):
        conn, cur = _fake([], ['id'])
        with patch.object(db_enrolment, "_conn", return_value=conn):
            db_enrolment.get_programs()
        sql, params = cur.executed[0]
        assert "WHERE id IN (%s, %s, %s, %s) ORDER BY sigle" in sql
        assert params == db_enrolment.PROGRAM_IDS


class TestConfigWindowMenu:
    def _mod(self):
        from LarcConfig.views import config_window
        return config_window

    def test_one_category_per_campus(self):
        cats = {cat: keys for cat, _t, keys in self._mod()._CATEGORIES}
        assert cats['maternelle'] == ['mat_classes', 'mat_eleves']
        assert cats['primaire'] == ['prim_classes', 'prim_eleves']
        assert cats['college_lycee'] == ['cl_classes', 'cl_eleves', 'matieres_classe']

    def test_category_titles_match_campus_labels(self):
        titles = {cat: t for cat, t, _k in self._mod()._CATEGORIES}
        for cp in CAMPUSES:
            assert titles[cp.key] == cp.label

    def test_old_ecole_entries_removed(self):
        mod = self._mod()
        keys = {k for k, _l, _i in mod._SECTIONS}
        assert not {'programmes', 'classes', 'affectation'} & keys
        assert 'ecole' not in {cat for cat, _t, _k in mod._CATEGORIES}

    def test_campus_screens_live_and_mapped_to_their_campus(self):
        mod = self._mod()
        assert not mod._PLANNED
        assert mod._CAMPUS_SECTIONS == {
            'mat_classes': ('classes', 'maternelle'), 'mat_eleves': ('eleves', 'maternelle'),
            'prim_classes': ('classes', 'primaire'), 'prim_eleves': ('eleves', 'primaire'),
            'cl_classes': ('classes', 'college_lycee'), 'cl_eleves': ('eleves', 'college_lycee'),
        }
        for _kind, campus_key in mod._CAMPUS_SECTIONS.values():
            assert get_campus(campus_key) is not None

    def test_common_categories_kept(self):
        cats = [cat for cat, _t, _k in self._mod()._CATEGORIES]
        assert cats == ['accueil', 'temps', 'maternelle', 'primaire',
                        'college_lycee', 'vie', 'apparence', 'admin']

    def test_every_section_has_label_and_icon(self):
        mod = self._mod()
        for _cat, _t, keys in mod._CATEGORIES:
            for k in keys:
                assert k in mod._LABELS and k in mod._ICONS
