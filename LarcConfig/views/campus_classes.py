"""Écran « Classes » d'un campus — vue d'ensemble en lecture seule.

Une ligne par classe du campus (principale et « - B ») : état, slots actifs
sur 40, libres, partis, anomalies. Les classes désactivées (gabarits B) sont
grisées. Aucune écriture (étape 3 du plan campus, 2026-10-26).
"""
from larccommon.icons import icon as md3_icon
from larccommon.safe_slot import safe_slot
from larccommon.theme import theme_manager
from phibuilder.phi.scale import SpacingToken
from phibuilder.widgets import M3Label, M3TableWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHeaderView, QTableWidgetItem, QVBoxLayout, QWidget

from LarcConfig.common import db_students
from LarcConfig.common.campus import get_campus
from LarcConfig.common.student_slot_rules import SLOTS_PER_CLASS, anomaly_summary_lines
from LarcConfig.views.table_rows import fix_row_height

_HEADERS = ["Classe", "Programme", "Niveau", "État", "Actifs", "Libres", "Partis", "Anomalies"]


class CampusClassesPanel(QWidget):
    def __init__(self, user: dict, campus_key: str):
        super().__init__()
        self._user = user
        self._campus = get_campus(campus_key)
        self._rows = []

        phi = theme_manager.phi_theme
        sp = phi.spacing.spacing
        lay = QVBoxLayout(self)
        lay.setContentsMargins(sp(SpacingToken.LG), sp(SpacingToken.LG),
                               sp(SpacingToken.LG), sp(SpacingToken.LG))
        lay.setSpacing(sp(SpacingToken.SM))

        self._title = M3Label(f"{self._campus.label} — Classes", theme=phi, style="headline_small")
        lay.addWidget(self._title)
        self._banner = M3Label("", theme=phi, style="body_medium")
        lay.addWidget(self._banner)
        self._hint = M3Label(
            "Lecture seule — les classes désactivées (gabarits « - B ») sont grisées. "
            "Triangle rouge = anomalies : nombre de places concernées et codes "
            "(détail et légende E1-E7 dans « Élèves par classe »).",
            theme=phi, style="body_small")
        self._hint.setWordWrap(True)
        lay.addWidget(self._hint)

        self._table = M3TableWidget(theme=phi)
        fix_row_height(self._table)
        self._table.setColumnCount(len(_HEADERS))
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.setAlternatingRowColors(False)
        h = self._table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeToContents)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self._table, 1)

        self.reload()

    def _ro(self, text, align=Qt.AlignLeft | Qt.AlignVCenter) -> QTableWidgetItem:
        item = QTableWidgetItem(str(text))
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setTextAlignment(align)
        return item

    @safe_slot("CampusClassesPanel.reload")
    def reload(self):
        self._rows = db_students.get_classes_overview(self._campus)
        self._fill()

    def _restyle(self):
        self._fill()

    def _fill(self):
        p = theme_manager.palette
        rows = self._rows
        active = [r for r in rows if r['enabled']]
        self._banner.setText(
            f"{len(active)} classes actives sur {len(rows)} · "
            f"{sum(r['actifs'] for r in rows)} élèves actifs · "
            f"{sum(r['anomalies'] for r in rows)} anomalie(s)")
        center = Qt.AlignCenter
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            state = self._ro("Active" if r['enabled'] else "Désactivée")
            state.setIcon(md3_icon('check_circle' if r['enabled'] else 'visibility_off',
                                   color=p.success if r['enabled'] else p.text_disabled))
            cells = [
                self._ro(r['label']),
                self._ro(r.get('program_sigle') or ''),
                self._ro(r.get('level_label') or ''),
                state,
                self._ro(f"{r['actifs']}/{SLOTS_PER_CLASS}", center),
                self._ro(r['libres'], center),
                self._ro(r['partis'], center),
                self._ro(self._anomaly_text(r), center),
            ]
            for j, item in enumerate(cells):
                if not r['enabled']:
                    item.setForeground(QColor(p.text_disabled))
                self._table.setItem(i, j, item)
            if r['anomalies']:
                anomalies = self._table.item(i, len(cells) - 1)
                anomalies.setForeground(QColor(p.error))
                anomalies.setIcon(md3_icon('warning', color=p.error))
                anomalies.setToolTip("\n".join(anomaly_summary_lines(r.get('codes') or {})))

    @staticmethod
    def _anomaly_text(r: dict) -> str:
        """« 11 · E5 » : codes visibles sans survol (l'infobulle détaille)."""
        codes = r.get('codes') or {}
        if not r['anomalies'] or not codes:
            return str(r['anomalies'])
        return f"{r['anomalies']} · {', '.join(codes)}"
