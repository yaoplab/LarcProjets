"""Légende de l'écran « Élèves par classe » + correspondance icônes/couleurs.

La légende reste toujours visible (l'infobulle au survol ne suffit pas : on
ne sait pas qu'elle existe). Ses textes viennent de student_slot_rules
(ANOMALY_HELP, STATE_HELP, VERDICT_HELP), source unique partagée avec les
infobulles du tableau. Icônes et couleurs des états / verdicts sont définies
ici une seule fois et réutilisées par le tableau (eleves_classe.py).
"""
from larccommon.design_system import ds
from larccommon.icons import icon as md3_icon
from larccommon.safe_slot import safe_slot
from larccommon.theme import theme_manager
from phibuilder.phi.scale import SpacingToken
from phibuilder.widgets import M3Button, M3Frame, M3Label
from phibuilder.widgets.button import ButtonVariant
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from LarcConfig.common.student_slot_rules import (
    ACTIF,
    ADMIN,
    ANOMALY_HELP,
    ANOMALY_MARK_HELP,
    INTERDIT,
    LIBRE,
    PARTI,
    PERMIS,
    STATE_HELP,
    VERDICT_HELP,
)

STATE_ICONS = {ACTIF: 'check_circle', LIBRE: 'add', PARTI: 'logout'}
VERDICT_LABELS = {PERMIS: "Permise", ADMIN: "Admin", INTERDIT: "Interdite"}
ANOMALY_ICON = 'warning'


def state_color(state: str) -> str:
    p = theme_manager.palette
    return {ACTIF: p.success, LIBRE: p.text_soft, PARTI: p.tertiary}[state]


def verdict_color(verdict: str) -> str:
    p = theme_manager.palette
    return {PERMIS: p.success, ADMIN: p.tertiary, INTERDIT: p.error}[verdict]


class _Entry(QWidget):
    """Une ligne de légende : pastille (icône) ou repère texte + phrase."""

    def __init__(self, text: str, icon_name: str = None, mark: str = None):
        super().__init__()
        phi = theme_manager.phi_theme
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(phi.spacing.spacing(SpacingToken.XS))
        self._icon_name = icon_name
        # Icône petite (ds.icon_sm) : la légende doit rester compacte
        self._icon = M3Label("", theme=phi, style="body_small")
        self._icon.setFixedWidth(ds.icon_sm)
        self._mark = M3Label(mark or "", theme=phi, style="label_medium") if mark else None
        self.text = M3Label(text, theme=phi, style="body_small")
        self.text.setWordWrap(True)
        lay.addWidget(self._mark if mark else self._icon, 0, Qt.AlignTop)
        lay.addWidget(self.text, 1, Qt.AlignTop)

    def restyle(self, color: str):
        p = theme_manager.palette
        if self._icon_name:
            self._icon.setPixmap(md3_icon(self._icon_name, color=color,
                                          size=ds.icon_sm).pixmap(ds.icon_sm, ds.icon_sm))
        if self._mark is not None:
            self._mark.set_color(color)
        self.text.set_color(p.text_strong)


class SlotLegend(QWidget):
    """Légende repliable (ouverte par défaut) : états, anomalies E1-E7,
    valeurs de la colonne Réinitialisation."""

    def __init__(self):
        super().__init__()
        phi = theme_manager.phi_theme
        sp = phi.spacing.spacing
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(sp(SpacingToken.XS))

        self._toggle = M3Button("Masquer la légende", theme=phi, variant=ButtonVariant.TEXT)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        self._toggle.toggled.connect(self._on_toggled)
        outer.addWidget(self._toggle, 0, Qt.AlignLeft)

        self._frame = M3Frame(theme=phi)
        self._frame.setAttribute(Qt.WA_StyledBackground, True)
        grid = QGridLayout(self._frame)
        grid.setContentsMargins(sp(SpacingToken.SM), sp(SpacingToken.XS),
                                sp(SpacingToken.SM), sp(SpacingToken.XS))
        grid.setHorizontalSpacing(sp(SpacingToken.MD))
        grid.setVerticalSpacing(sp(SpacingToken.XXS))

        self._titles = []
        self._states = {}
        self._codes = {}
        self._verdicts = {}

        def title(text, col):
            lbl = M3Label(text, theme=phi, style="label_medium")
            self._titles.append(lbl)
            grid.addWidget(lbl, 0, col)

        title("État", 0)
        for row, state in enumerate((ACTIF, LIBRE, PARTI), start=1):
            self._states[state] = _Entry(STATE_HELP[state], icon_name=STATE_ICONS[state])
            grid.addWidget(self._states[state], row, 0)

        title("Anomalies", 1)
        self._mark = _Entry(ANOMALY_MARK_HELP, icon_name=ANOMALY_ICON)
        grid.addWidget(self._mark, 1, 1, 1, 2)
        codes = list(ANOMALY_HELP)
        half = (len(codes) + 1) // 2  # E1-E4 | E5-E7
        for i, code in enumerate(codes):
            entry = _Entry(ANOMALY_HELP[code], mark=code)
            self._codes[code] = entry
            grid.addWidget(entry, 2 + i % half, 1 + i // half)

        title("Réinitialisation", 3)
        for row, verdict in enumerate((PERMIS, ADMIN, INTERDIT), start=1):
            # Le repère coloré porte déjà « Permise » / « Admin » / « Interdite »
            self._verdicts[verdict] = _Entry(VERDICT_HELP[verdict].split(" : ", 1)[-1],
                                             mark=VERDICT_LABELS[verdict])
            grid.addWidget(self._verdicts[verdict], row, 3)
        # Anomalies sur 2 colonnes (1-2) : plus de place que États / Réinit.
        for col, stretch in enumerate((2, 3, 3, 3)):
            grid.setColumnStretch(col, stretch)
        grid.setRowStretch(grid.rowCount(), 1)
        outer.addWidget(self._frame)
        self._restyle()

    def codes(self) -> list:
        """Codes affichés (contrôle de complétude en test)."""
        return list(self._codes)

    @safe_slot("SlotLegend._on_toggled")
    def _on_toggled(self, checked: bool):
        self._frame.setVisible(checked)
        self._toggle.setText("Masquer la légende" if checked else "Afficher la légende")

    def _restyle(self):
        p = theme_manager.palette
        self._frame.setStyleSheet(
            f"M3Frame {{ background: {p.surface_container_low}; "
            f"border: {ds.border_width}px solid {p.outline_variant}; "
            f"border-radius: {ds.radius_sm}px; }}")
        for lbl in self._titles:
            lbl.set_color(p.text_soft)
        for state, entry in self._states.items():
            entry.restyle(state_color(state))
        self._mark.restyle(p.error)
        for entry in self._codes.values():
            entry.restyle(p.error)
        for verdict, entry in self._verdicts.items():
            entry.restyle(verdict_color(verdict))
        self._toggle.setIcon(md3_icon('info', color=p.primary, size=ds.icon_sm))
