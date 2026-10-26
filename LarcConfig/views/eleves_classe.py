"""Écran « Élèves par classe » d'un campus — 40 places.

Colonne de gauche : Programme (du campus) puis Classe (classes activées
seulement) et filtre « Anomalies seulement ». À droite : bandeau de compteurs,
légende, barre d'actions (étape 4 : inscrire, modifier, présent/absent,
réinitialiser — cf. eleves_actions.py) et tableau des 40 places : état,
preuves notes / bulletin, verdict de réinitialisation et anomalies.
"""
from larccommon.icons import icon as md3_icon
from larccommon.safe_slot import safe_slot
from larccommon.theme import theme_manager
from phibuilder.phi.scale import SpacingToken
from phibuilder.widgets import M3Button, M3ComboBox, M3Label, M3TableWidget
from phibuilder.widgets.button import ButtonVariant
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QTableWidgetItem, QVBoxLayout, QWidget

from LarcConfig.common import db_enrolment, db_students
from LarcConfig.common.campus import get_campus
from LarcConfig.common.student_slot_rules import (
    ACTIF,
    ANOMALIES,
    ANOMALY_HELP,
    LIBRE,
    PARTI,
    STATE_HELP,
    VERDICT_HELP,
    summarize,
)
from LarcConfig.views.eleves_actions import SlotActionBar
from LarcConfig.views.slot_legend import (
    ANOMALY_ICON,
    STATE_ICONS,
    VERDICT_LABELS,
    SlotLegend,
    state_color,
    verdict_color,
)
from LarcConfig.views.table_rows import fix_row_height

_HEADERS = ["N°", "ID", "État", "Nom", "Prénom", "Notes", "Bulletin",
            "Réinitialisation", "Anomalie"]
_STATE_LABELS = {ACTIF: "Actif", LIBRE: "Libre", PARTI: "Parti"}
_BULLETIN_TIPS = {True: "Bulletin produit", False: "Aucun bulletin",
                  None: "Inconnu : données illisibles"}


def anomaly_tooltip(row: dict) -> str:
    """Infobulle de la cellule Anomalie : détail + phrase de légende par code."""
    return "\n".join(f"{text}\n   {ANOMALY_HELP.get(code, '')}"
                     for code, text in zip(row['anomalies'], row['anomaly_texts']))


def _yes_no(value) -> str:
    return "?" if value is None else ("oui" if value else "non")


class ElevesClassePanel(QWidget):
    data_changed = Signal()  # une écriture a réussi : la vue « Classes » doit se recharger

    def __init__(self, user: dict, campus_key: str):
        super().__init__()
        self._user = user
        self._campus = get_campus(campus_key)
        self._classrooms = []
        self._rows = []
        self._shown = []  # lignes affichées (après filtre), dans l'ordre du tableau

        phi = theme_manager.phi_theme
        sp = phi.spacing.spacing

        lay = QHBoxLayout(self)
        lay.setContentsMargins(sp(SpacingToken.MD), sp(SpacingToken.MD), 0, sp(SpacingToken.MD))
        lay.setSpacing(sp(SpacingToken.MD))

        # Colonne de gauche — même gabarit que « Matières par classe »
        rail = QWidget()
        rail.setFixedWidth(sp(SpacingToken.XXXL) + sp(SpacingToken.XL))
        rail_lay = QVBoxLayout(rail)
        rail_lay.setContentsMargins(0, 0, 0, 0)
        rail_lay.setSpacing(sp(SpacingToken.XS))

        self._programs = db_enrolment.get_programs(campus=self._campus)
        rail_lay.addWidget(M3Label("Programme", theme=phi, style="body_small"))
        self._program_combo = M3ComboBox([p['sigle'] for p in self._programs], theme=phi)
        self._program_combo.currentIndexChanged.connect(self._on_program_changed)
        rail_lay.addWidget(self._program_combo)

        rail_lay.addWidget(M3Label("Classe", theme=phi, style="body_small"))
        self._class_combo = M3ComboBox([], theme=phi)
        self._class_combo.currentIndexChanged.connect(self._on_classroom_changed)
        rail_lay.addWidget(self._class_combo)

        rail_lay.addSpacing(sp(SpacingToken.SM))
        # Bouton bascule plutôt qu'une case à cocher : pas de QCheckBox hors
        # phibuilder (règle des imports UI).
        self._only_anomalies = M3Button("Anomalies seulement", theme=phi,
                                        variant=ButtonVariant.OUTLINED)
        self._only_anomalies.setCheckable(True)
        self._only_anomalies.toggled.connect(self._on_filter_toggled)
        rail_lay.addWidget(self._only_anomalies)
        rail_lay.addStretch()
        lay.addWidget(rail)

        # Zone principale
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, sp(SpacingToken.MD), 0)
        ml.setSpacing(sp(SpacingToken.SM))
        self._title = M3Label(f"{self._campus.label} — Élèves par classe", theme=phi,
                              style="headline_small")
        ml.addWidget(self._title)
        self._banner = M3Label("", theme=phi, style="body_medium")
        ml.addWidget(self._banner)
        # Légende toujours visible (repliable) : l'infobulle seule ne se
        # découvre pas — retour de Pat du 2026-10-26.
        self._legend = SlotLegend()
        ml.addWidget(self._legend)
        # Actions sur la place sélectionnée (clic droit : même menu)
        self._actions = SlotActionBar(self._campus)
        self._actions.changed.connect(self._on_data_written)
        ml.addWidget(self._actions)

        self._table = M3TableWidget(theme=phi)
        fix_row_height(self._table)
        self._table.setColumnCount(len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setAlternatingRowColors(False)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        h.setSectionResizeMode(len(_HEADERS) - 1, QHeaderView.Stretch)
        self._table.currentCellChanged.connect(self._on_current_changed)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        ml.addWidget(self._table, 1)
        lay.addWidget(main, 1)

        if self._programs:
            self._on_program_changed(0)
        self._restyle_filter()

    # ── Navigation ───────────────────────────────────────────────────────

    def reload(self):
        self._load_classroom(self._class_combo.currentIndex())

    @safe_slot("ElevesClassePanel._on_program_changed")
    def _on_program_changed(self, index: int):
        if not (0 <= index < len(self._programs)):
            return
        program_id = self._programs[index]['id']
        # Classes activées seulement : les « - B » sont des gabarits inutilisés.
        self._classrooms = [c for c in db_enrolment.get_classrooms(program_id=program_id,
                                                                   campus=self._campus)
                            if c['enabled']]
        self._class_combo.blockSignals(True)
        self._class_combo.clear()
        for c in self._classrooms:
            self._class_combo.addItem(c['label'])
        self._class_combo.blockSignals(False)
        self._load_classroom(0 if self._classrooms else -1)

    @safe_slot("ElevesClassePanel._on_classroom_changed")
    def _on_classroom_changed(self, index: int):
        self._load_classroom(index)

    @safe_slot("ElevesClassePanel._on_current_changed")
    def _on_current_changed(self, row: int, *_args):
        self._actions.set_row(self._shown[row] if 0 <= row < len(self._shown) else None)

    @safe_slot("ElevesClassePanel._on_context_menu")
    def _on_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if not (0 <= row < len(self._shown)):
            return
        self._table.selectRow(row)
        menu = self._actions.menu_for(self._shown[row], self._table)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    @safe_slot("ElevesClassePanel._on_data_written")
    def _on_data_written(self):
        self.reload()
        self.data_changed.emit()

    @safe_slot("ElevesClassePanel._on_filter_toggled")
    def _on_filter_toggled(self, _checked: bool):
        self._restyle_filter()
        self._fill()

    def _load_classroom(self, index: int):
        if not (0 <= index < len(self._classrooms)):
            self._rows = []
        else:
            self._rows = db_students.get_class_detail(self._classrooms[index], self._campus)
        self._fill()

    # ── Rendu ────────────────────────────────────────────────────────────

    def _restyle(self):
        self._restyle_filter()
        self._legend._restyle()
        self._actions._restyle()
        self._fill()

    def _restyle_filter(self):
        p = theme_manager.palette
        on = self._only_anomalies.isChecked()
        self._only_anomalies.setIcon(md3_icon('filter_list', color=p.error if on else p.text_soft))

    def _ro(self, text, align=Qt.AlignLeft | Qt.AlignVCenter) -> QTableWidgetItem:
        item = QTableWidgetItem("" if text is None else str(text))
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setTextAlignment(align)
        return item

    def _fill(self):
        p = theme_manager.palette
        s = summarize(self._rows) if self._rows else {ACTIF: 0, LIBRE: 0, PARTI: 0,
                                                       'anomalies': 0, 'class_anomalies': []}
        banner = (f"{s[ACTIF]} actifs · {s[LIBRE]} libres · {s[PARTI]} partis · "
                  f"{s['anomalies']} anomalie(s)")
        if s['class_anomalies']:
            banner += f" — E4 {ANOMALIES['E4']} ({len(self._rows)} places)"
        self._banner.setText(banner)

        rows = [r for r in self._rows if r['anomalies']] if self._only_anomalies.isChecked() \
            else self._rows
        selected_id = self._actions._row['id'] if self._actions._row else None
        self._shown = rows
        center = Qt.AlignCenter
        self._table.blockSignals(True)
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            state = self._ro(_STATE_LABELS[r['state']])
            state.setIcon(md3_icon(STATE_ICONS[r['state']], color=state_color(r['state'])))
            state.setForeground(QColor(state_color(r['state'])))
            state.setToolTip(STATE_HELP[r['state']])

            notes = r['notes']
            notes_item = self._ro("?" if notes is None else (notes or "non"), center)
            notes_item.setToolTip("Lignes de notes saisies (toutes périodes)")

            verdict = self._ro(VERDICT_LABELS[r['verdict']], center)
            verdict.setForeground(QColor(verdict_color(r['verdict'])))
            verdict.setToolTip(VERDICT_HELP[r['verdict']])

            bulletin = self._ro(_yes_no(r['has_bulletin']), center)
            bulletin.setToolTip(_BULLETIN_TIPS[r['has_bulletin']])

            anomaly = self._ro(" ; ".join(r['anomaly_texts']))
            if r['anomalies']:
                anomaly.setForeground(QColor(p.error))
                anomaly.setIcon(md3_icon(ANOMALY_ICON, color=p.error))
                anomaly.setToolTip(anomaly_tooltip(r))

            cells = [
                self._ro(f"{r['slot']:02d}", center),
                self._ro(r['id'], center),
                state,
                self._ro((r['last_name'] or '').strip()),
                self._ro((r['first_name'] or '').strip()),
                notes_item,
                bulletin,
                verdict,
                anomaly,
            ]
            for j, item in enumerate(cells):
                if r['state'] == LIBRE and j in (3, 4):
                    item.setForeground(QColor(p.text_disabled))
                self._table.setItem(i, j, item)
        self._table.blockSignals(False)
        # Garder la place sélectionnée après un rechargement (sinon : aucune)
        ids = [r['id'] for r in rows]
        if selected_id in ids:
            self._table.selectRow(ids.index(selected_id))
            self._actions.set_row(rows[ids.index(selected_id)])
        else:
            self._table.clearSelection()
            self._actions.set_row(None)
