"""StaffCard, find_staff_photo et StudentCard (_restyle, textes l10n) — offscreen."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from larccommon import photos
from larccommon.app_config import app_config
from larccommon.design_system import ds
from larccommon.l10n import Translator
from larccommon.widgets import StaffCard, StudentCard
from larccommon.widgets import staff_card as staff_card_mod

TEACHER = {"id": 990001, "last_name": "KOFFI", "first_name": "Ama Akossiwa",
           "full_name": "KOFFI Ama", "is_teacher": True, "is_coordonator": True,
           "emp_status": "actif"}
STAFF = {"id": 990002, "last_name": "MENSAH", "first_name": "Kodjo",
         "full_name": "MENSAH Kodjo", "is_staff": True, "type_DRH": True,
         "type_Comptable": True, "emp_status": "suspendu", "absent_today": True}

NEW_KEYS = [
    "staff_card.role.drh", "staff_card.role.accountant", "staff_card.role.hr_officer",
    "staff_card.role.reports", "staff_card.role.teacher", "staff_card.role.coordinator",
    "staff_card.role.admin", "staff_card.status.actif", "staff_card.status.suspendu",
    "staff_card.status.en_preavis", "staff_card.status.parti", "staff_card.absent_today",
    "staff_card.events_tooltip", "student_card.exits", "student_card.events",
    "student_card.presence.present", "student_card.presence.absent",
    "student_card.presence.late", "student_card.presence.exited",
    "student_card.payment.late", "student_card.payment.normal", "student_card.payment.paid",
    "student_card.metrics.absences", "student_card.metrics.lates",
]


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def photo_dirs(monkeypatch, tmp_path):
    """Dossiers photos isolés : staff_photos_dir et photos_dir dans tmp_path."""
    staff_dir, students_dir = tmp_path / "staff", tmp_path / "students"
    staff_dir.mkdir()
    students_dir.mkdir()
    monkeypatch.setattr(app_config, "_loaded", True)
    monkeypatch.setitem(app_config._cache, "staff_photos_dir", str(staff_dir))
    monkeypatch.setitem(app_config._cache, "photos_dir", str(students_dir))
    monkeypatch.setattr(staff_card_mod, "_PHOTO_CACHE", {})
    return staff_dir, students_dir


@pytest.fixture
def blue_theme():
    ds.switch_theme("blue")
    yield
    ds.switch_theme("blue")


def _texts(widget) -> list[str]:
    return [lbl.text() for lbl in widget.findChildren(QLabel)]


# ── find_staff_photo ─────────────────────────────────────────────────────

def test_find_staff_photo_absente(photo_dirs):
    assert photos.find_staff_photo(990001) == ""


def test_find_staff_photo_dossier_staff_prioritaire(photo_dirs):
    staff_dir, students_dir = photo_dirs
    (students_dir / "990001.png").write_bytes(b"x")
    assert photos.find_staff_photo(990001) == str(students_dir / "990001.png")
    (staff_dir / "990001.png").write_bytes(b"x")
    assert photos.find_staff_photo(990001) == str(staff_dir / "990001.png")


def test_staff_photos_dir_relatif_resolu_depuis_larc_root(monkeypatch, tmp_path):
    monkeypatch.setenv("LARC_ROOT", str(tmp_path))
    monkeypatch.setattr(app_config, "_loaded", True)
    monkeypatch.setitem(app_config._cache, "staff_photos_dir", "Photos/Staff")
    assert app_config.get("staff_photos_dir") == os.path.normpath(
        str(tmp_path.resolve() / "Photos" / "Staff"))


def test_staff_photos_dir_par_defaut_larcrh():
    default = app_config.DEFAULTS["staff_photos_dir"]
    assert default.endswith(os.path.join("LarcRH", "photos"))


# ── StaffCard ────────────────────────────────────────────────────────────

def test_staff_card_enseignant(qapp, photo_dirs, blue_theme):
    card = StaffCard(TEACHER)
    texts = _texts(card)
    assert "KOFFI" in texts
    assert "● Enseignant" in texts and "● Coordinateur" in texts
    assert "● Actif" in texts
    assert card.height() == staff_card_mod.CARD_H


def test_staff_card_staff_absent(qapp, photo_dirs, blue_theme):
    card = StaffCard(STAFF)
    texts = _texts(card)
    assert "● Directeur des Ressources Humaines" in texts
    assert "● Comptable" in texts
    # L'absence du jour prime sur le statut d'emploi
    assert "● Absent aujourd'hui" in texts
    assert "● Suspendu" not in texts


def test_staff_card_statut_inconnu_et_parti(qapp, photo_dirs):
    assert staff_card_mod.status_label({"emp_status": "parti"}) == "Parti"
    assert staff_card_mod.status_label({"emp_status": "en_préavis"}) == "En préavis"
    assert staff_card_mod.status_label({"emp_status": "détaché"}) == "Détaché"


def test_staff_card_signal_event_requested(qapp, photo_dirs):
    card = StaffCard(TEACHER)
    got = []
    card.event_requested.connect(got.append)
    card._event_btn.click()
    assert got == [TEACHER]


def test_staff_card_signal_activated(qapp, photo_dirs):
    card = StaffCard(STAFF)
    got = []
    card.activated.connect(got.append)
    card.mouseDoubleClickEvent(None)
    assert got == [STAFF]


def test_staff_card_photo_absente_avatar(qapp, photo_dirs):
    card = StaffCard(TEACHER)
    assert not card.has_photo()
    card.load_photo()
    assert card.has_photo()
    pix = card._photo_lbl.pixmap()
    assert (pix.width(), pix.height()) == (staff_card_mod.PHOTO, staff_card_mod.PHOTO)
    _pix, is_photo = staff_card_mod._PHOTO_CACHE[(TEACHER["id"], staff_card_mod.PHOTO)]
    assert is_photo is False


def test_staff_card_restyle_sans_erreur(qapp, photo_dirs, blue_theme):
    card = StaffCard(STAFF)
    ds.switch_theme("dark")
    fresh = StaffCard(STAFF)
    assert card._status_lbl.text() == "● Absent aujourd'hui"
    # Après changement de thème : mêmes styles qu'une vignette neuve
    assert card.styleSheet() == fresh.styleSheet()
    for a, b in zip(card.findChildren(QLabel), fresh.findChildren(QLabel)):
        assert (a.text(), a.styleSheet()) == (b.text(), b.styleSheet())
    card._restyle()


# ── StudentCard ──────────────────────────────────────────────────────────

def test_student_card_textes(qapp, photo_dirs, blue_theme):
    card = StudentCard(990003, "AGBO", "Yao")
    card.set_exit_count(2)
    assert card._exit_label.text() == "2 sortie(s)"
    card.set_exit_count(0)
    assert card._exit_label.text() == ""
    card.set_event_count(3)
    assert card._evt_label.text() == "3 evt(s)"
    card.set_presence("late")
    assert card._status_label.text() == "Retard"
    card.set_payment_status("solde")
    assert card._status_label.text() == "Soldé"
    card.set_metrics(11, 6)
    assert "11 abs" in card._metrics_label.text() and "6 ret" in card._metrics_label.text()


def test_student_card_restyle_suit_le_theme(qapp, photo_dirs, blue_theme):
    card = StudentCard(990004, "KOFFI", "Ama")
    card.set_validation({"dossier": {"ok": True}})
    card.set_presence("absent")
    card.set_absent(True)
    light_name = card._name_label.text()
    ds.switch_theme("dark")
    p = ds.p
    assert card._name_label.text() != light_name
    assert p.text_strong in card._name_label.text()
    assert p.error in card._status_label.styleSheet()
    assert p.error_container in card.styleSheet()           # absent conservé
    assert p.success in card._badge_labels["dossier_valid"].styleSheet()
    assert p.success not in card._badge_labels["photo_valid"].styleSheet()


def test_student_card_set_status_recolore(qapp, photo_dirs, blue_theme):
    card = StudentCard(990005, "DOSSOU", "Afi")
    card.set_status("Absent", ds.p.error)
    ds.switch_theme("dark")
    assert ds.p.error in card._status_label.styleSheet()
    assert card._status_label.text() == "Absent"


def test_student_card_taille_pastilles_config(qapp, photo_dirs):
    card = StudentCard(990006, "AMEGAN", "Esi")
    size = card._cfg.validation_badge
    assert size == 14
    assert all(c.width() == size for c in card._badge_labels.values())


# ── Traductions ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("lang", ["fr", "en"])
def test_cles_l10n_presentes(lang):
    path = os.path.join(Translator.l10n_dir(), f"{lang}.json")
    if not os.path.isfile(path):
        pytest.skip(f"{lang}.json absent (fichier hors git)")
    with open(path, encoding="utf-8") as f:
        strings = json.load(f)
    missing = [k for k in NEW_KEYS if k not in strings]
    assert not missing


def test_textes_fr_identiques_aux_replis():
    """fr.json doit reprendre exactement les textes français en repli."""
    path = os.path.join(Translator.l10n_dir(), "fr.json")
    if not os.path.isfile(path):
        pytest.skip("fr.json absent")
    with open(path, encoding="utf-8") as f:
        fr = json.load(f)
    assert fr["staff_card.absent_today"] == "Absent aujourd'hui"
    assert fr["student_card.exits"].format(count=2) == "2 sortie(s)"
    assert fr["student_card.presence.present"] == "Present"
