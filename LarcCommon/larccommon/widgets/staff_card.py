"""StaffCard — vignette d'un membre du personnel (enseignant ou staff).

Bordure à la couleur du type d'employé, photo ronde (chargée en différé,
cache partagé), NOM + prénoms élidés, fonctions une par ligne, statut
d'emploi (l'absence du jour prime) et bouton Événement.

La vignette ne dépend d'aucune application : le bouton Événement émet
``event_requested`` et le double-clic émet ``activated`` (données de la
ligne en argument) ; c'est l'appelant qui ouvre le wizard ou la fiche.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from larccommon.design_system import ds
from larccommon.icons import icon as md3_icon
from larccommon.l10n import _
from larccommon.photos import find_staff_photo
from larccommon.safe_slot import safe_slot
from larccommon.theme import staff_type_color, staff_type_from_flags, theme_manager
from phibuilder.phi.scale import SpacingToken

from .avatar import make_initials_avatar

# Dimensions : largeur 200, hauteur 200×φ ≈ 324, photo 140×140
CARD_W = ds.sp(SpacingToken.XXXL) + ds.sp(SpacingToken.XL) + ds.sp(SpacingToken.SM)  # 136+52+12 = 200
CARD_H = int(CARD_W * ds.GOLDEN)                                                    # 200×φ = 323
PHOTO = ds.sp(SpacingToken.XXXL) + ds.sp(SpacingToken.XXS)                          # 136+4 = 140


def staff_type_key(data: dict) -> str:
    """Clé de type d'employé depuis les flags de la ligne (grille)."""
    flags = {
        "type_director": data.get("is_adm"),
        "type_coordonator": data.get("is_coordonator"),
        "type_supervisor": data.get("is_supervisor"),
        "type_secretary": data.get("is_secretary"),
        "type_teacher": data.get("is_teacher"),
    }
    return staff_type_from_flags(flags)


def elide_text(text: str, px_width: int, font_px: int) -> str:
    """Tronque le texte avec … pour une largeur donnée (une seule ligne)."""
    f = QFont()
    f.setPixelSize(theme_manager.font_size(font_px))
    return QFontMetrics(f).elidedText(text, Qt.ElideRight, max(px_width, 20))


# Statuts d'emploi (valeurs exactes de emp_status en base) → (clé l10n, repli FR)
_STATUS_LABELS = {
    "actif": ("staff_card.status.actif", "Actif"),
    "suspendu": ("staff_card.status.suspendu", "Suspendu"),
    "en_préavis": ("staff_card.status.en_preavis", "En préavis"),
    "parti": ("staff_card.status.parti", "Parti"),
}

# Fonctions du staff administratif : flag → (clé l10n, repli FR)
_STAFF_ROLES = {
    "type_DRH": ("staff_card.role.drh", "Directeur des Ressources Humaines"),
    "type_Comptable": ("staff_card.role.accountant", "Comptable"),
    "type_ressources_Humaines": ("staff_card.role.hr_officer", "Chargé des Ressources Humaines"),
    "type_Bulletin_Releves": ("staff_card.role.reports", "Chargé des Bulletins et Relevés"),
}

# Fonctions des enseignants : flag → (clé l10n, repli FR)
_TEACHER_ROLES = {
    "is_teacher": ("staff_card.role.teacher", "Enseignant"),
    "is_coordonator": ("staff_card.role.coordinator", "Coordinateur"),
    "is_adm": ("staff_card.role.admin", "Administrateur"),
}


def status_label(data: dict) -> str:
    """Libellé du statut — l'absence du JOUR prime sur le statut d'emploi."""
    if data.get("absent_today"):
        return _("staff_card.absent_today", "Absent aujourd'hui")
    status = (data.get("emp_status") or "actif").lower()
    key, fallback = _STATUS_LABELS.get(status, ("", ""))
    return _(key, fallback) if key else status.title()


def role_titles(data: dict) -> list[str]:
    """Fonctions affichées sur la vignette, une par ligne."""
    roles = _STAFF_ROLES if data.get("is_staff") else _TEACHER_ROLES
    return [_(key, fallback) for flag, (key, fallback) in roles.items() if data.get(flag)]


# Cache des photos/avatars par (id, taille) — le rebuild de la grille
# (thème, filtres) rechargeait chaque image du disque → 1-3 s de latence
# proportionnelle au nombre de vignettes (signalé 2026-08-16).
# Chaque entrée = (pixmap, is_real_photo) : si un AVATAR est en cache et
# qu'une vraie photo arrive sur le disque, on la recharge automatiquement.
_PHOTO_CACHE: dict[tuple, tuple[QPixmap, bool]] = {}
_PHOTO_CACHE_MAX = 500


def cached_staff_photo(staff_id: int, size: int, fallback_name: str) -> QPixmap:
    """Photo du membre (ou avatar à initiales) mise à l'échelle, via le cache."""
    key = (staff_id, size)
    path = find_staff_photo(staff_id)
    if key in _PHOTO_CACHE:
        pix, was_photo = _PHOTO_CACHE[key]
        if was_photo or not path:
            return pix
        # Avatar en cache mais une photo vient d'arriver → recharger
        del _PHOTO_CACHE[key]
    pix = None
    if path:
        pix = QPixmap(path)
        if pix.isNull():
            pix = None
    if pix is None:
        pix = make_initials_avatar(fallback_name, size)
        is_photo = False
    else:
        pix = pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        is_photo = True
    if len(_PHOTO_CACHE) >= _PHOTO_CACHE_MAX:
        _PHOTO_CACHE.clear()
    _PHOTO_CACHE[key] = (pix, is_photo)
    return pix


class StaffCard(QFrame):
    """Vignette 200×323 d'un enseignant ou d'un membre du staff."""

    event_requested = Signal(dict)   # bouton Événement
    activated = Signal(dict)         # double-clic

    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self._data = data
        self.setObjectName("staff_card")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(CARD_W)
        self.setFixedHeight(CARD_H)
        self._status_lbl: QLabel | None = None
        self._styled_labels: list[QLabel] = []
        # Couleur du TYPE d'employé (BORDURE + fonctions) — calculée par _restyle
        self._role_badge = ""
        self._setup_ui()
        ds.theme_changed.connect(self._restyle)

    @property
    def data(self) -> dict:
        return self._data

    def _style(self) -> str:
        p = theme_manager.palette
        return f"""
            #staff_card {{
                background: {p.surface}; border: 2px solid {self._role_badge};
                border-radius: {ds.radius_sm}px;
            }}
            #staff_card:hover {{ border-color: {p.primary}; }}
        """

    def _status_color(self) -> str:
        p = theme_manager.palette
        if self._data.get("absent_today"):
            return p.error
        status = (self._data.get("emp_status") or "actif").lower()
        status_colors = {
            "actif": p.success, "suspendu": p.tertiary,
            "en_préavis": p.tertiary, "parti": p.error,
        }
        return status_colors.get(status, p.text_soft)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(ds.space_xs, ds.space_xs, ds.space_xs, ds.space_xs)
        layout.setSpacing(ds.space_xxs)

        # Photo 140×140 — chargée EN DIFFÉRÉ (load_photo) : la grille s'affiche
        # immédiatement, les photos arrivent juste après (fini les 3 s d'attente
        # pour afficher la liste — les PNG font jusqu'à 2 Mo, signalé 2026-08-16)
        self._photo_lbl = QLabel()
        self._photo_lbl.setFixedSize(PHOTO, PHOTO)
        self._photo_lbl.setAlignment(Qt.AlignCenter)
        self._photo_lbl.setStyleSheet(
            f"QLabel {{ border-radius: {PHOTO // 2}px; background: transparent; }}")
        layout.addWidget(self._photo_lbl, 0, Qt.AlignCenter)

        # NOM (1ʳᵉ ligne, gras) + Prénoms (2ᵉ ligne, tronqués)
        self._name_lbl = QLabel(self._data.get("last_name", "") or "—")
        self._name_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._name_lbl)
        self._styled_labels.append(self._name_lbl)

        first = self._data.get("first_name", "") or ""
        self._first_lbl = QLabel()
        self._first_lbl.setAlignment(Qt.AlignCenter)
        self._first_lbl.setVisible(bool(first))
        layout.addWidget(self._first_lbl)
        self._styled_labels.append(self._first_lbl)

        # Ligne blanche (séparateur avant les fonctions)
        self._sep = QFrame()
        self._sep.setFrameShape(QFrame.HLine)
        layout.addWidget(self._sep)

        # Fonctions — UNE PAR LIGNE, chacune avec sa pastille (couleur du type)
        self._role_lbls: list[QLabel] = []
        for titre in role_titles(self._data):
            lbl = QLabel(f"● {titre}")
            lbl.setAlignment(Qt.AlignCenter)
            layout.addWidget(lbl)
            self._role_lbls.append(lbl)
            self._styled_labels.append(lbl)

        # NB : catégorie pro, matricule et campus ne sont PLUS affichés sur la
        # vignette (carte 200×323 serrée) — ils restent dans la fiche détail.

        # Statut (badge coloré) — l'absence du JOUR prime sur le statut d'emploi
        self._status_lbl = QLabel()

        layout.addStretch()

        # Bouton événement + STATUT JUSTE À SA DROITE
        btn_row = QHBoxLayout()
        btn_row.setSpacing(ds.space_xs)
        btn_row.addStretch()
        self._event_btn = QPushButton()
        self._event_btn.setFixedSize(ds.space_lg, ds.space_lg)
        self._event_btn.setCursor(Qt.PointingHandCursor)
        self._event_btn.setToolTip(_("staff_card.events_tooltip", "Événements"))
        self._event_btn.clicked.connect(lambda checked: self._on_event())
        btn_row.addWidget(self._event_btn)
        btn_row.addWidget(self._status_lbl)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._restyle()

    @safe_slot("StaffCard._on_event")
    def _on_event(self):
        self.event_requested.emit(self._data)

    @safe_slot("StaffCard._restyle")
    def _restyle(self):
        """Styles de la vignette et des sous-widgets (construction et thème)."""
        p = theme_manager.palette
        s = theme_manager.font_size
        # Relire la couleur du type AVANT le style (bordure déclinée par thème)
        self._role_badge, _bg, _fg = staff_type_color(staff_type_key(self._data))
        self.setStyleSheet(self._style())
        self._name_lbl.setStyleSheet(
            f"font-size: {s(13)}px; font-weight: bold; color: {p.text_strong}; border: none;")
        # Prénoms : relire l'élision avec l'échelle de police courante
        first = self._data.get("first_name", "") or ""
        self._first_lbl.setText(elide_text(first, CARD_W - 2 * ds.space_sm, 11))
        self._first_lbl.setStyleSheet(
            f"font-size: {s(11)}px; color: {p.text_strong}; border: none;")
        self._sep.setStyleSheet(f"color: {p.border_light}; border: none; max-height: 1px;")
        for lbl in self._role_lbls:
            lbl.setStyleSheet(
                f"font-size: {s(10)}px; font-weight: bold; "
                f"color: {self._role_badge}; border: none;")
        self._status_lbl.setText(f"● {status_label(self._data)}")
        self._status_lbl.setStyleSheet(
            f"font-size: {s(9)}px; font-weight: bold; color: {self._status_color()}; "
            f"background: transparent; border: none; padding: {ds.border_width}px 0px;")
        self._event_btn.setIcon(md3_icon("event", color=p.primary, size=16))
        self._event_btn.setStyleSheet(
            f"QPushButton {{ border: 1px solid {p.outline}; border-radius: "
            f"{ds.radius_xs}px; background: transparent; }} "
            f"QPushButton:hover {{ background: {p.surface_variant}; }}")

    def load_photo(self):
        """Charge la photo (cache partagé) — appelé en différé après affichage."""
        self._photo_lbl.setPixmap(cached_staff_photo(
            self._data.get("id", 0), PHOTO, self._data.get("full_name", "")))

    def has_photo(self) -> bool:
        """Vrai si la photo (ou l'avatar) a déjà été chargée."""
        return not self._photo_lbl.pixmap().isNull()

    def mouseDoubleClickEvent(self, event):
        self.activated.emit(self._data)
