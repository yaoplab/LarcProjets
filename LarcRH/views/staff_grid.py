"""StaffGrid — grille photos responsive (QScrollArea + QGridLayout adaptatif)."""
from __future__ import annotations

import queue

from PySide6.QtCore import Qt, Signal, QTimer, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QGridLayout,
    QLabel, QPushButton, QSizePolicy, QComboBox, QFileDialog,
    QProgressBar, QApplication,
)

from phibuilder.widgets import M3TableWidget

from larccommon.design_system import ds
from phibuilder.phi.scale import SpacingToken
from PySide6.QtWidgets import QHeaderView
from larccommon.theme import theme_manager
from larccommon.icons import icon as md3_icon
from larccommon.safe_slot import safe_slot
from larccommon.widgets.staff_type_legend import StaffTypeLegend
from larccommon.widgets.staff_card import StaffCard, CARD_W

SPACING = ds.space_xs    # 8
MARGIN = ds.space_xs     # 8


def _open_dedicated_connection():
    """Ouvre une connexion DÉDIÉE (threads) vers la même cible que l'app :
    Intranet ou Supabase selon le mode actif. Jamais db.server_conn ici."""
    import configparser
    import psycopg2
    from larccommon.config_loader import find_cfg
    from larccommon.database import db, DBMode

    cfg = configparser.ConfigParser()
    cfg.read(find_cfg())
    section = ("IntranetDatabase" if db.server_mode == DBMode.INTRANET
               else "SupabaseDatabase")
    c = cfg[section]
    if section == "SupabaseDatabase":
        return psycopg2.connect(host=c["Host"], port=c.getint("Port"),
                                dbname=c["DB"], user=c["User"],
                                password=c["Pass"], sslmode="require")
    return psycopg2.connect(host=c.get("Host", "127.0.0.1"),
                            port=c.getint("Port", 5432),
                            dbname=c.get("DB", "NewLarcDB"),
                            user=c.get("User", "postgres"),
                            password=c.get("Pass", "postgres"))


class _PersistentLoader(QThread):
    """Worker PERSISTANT : un seul thread, une SEULE connexion dédiée ouverte
    pour la session et réutilisée à chaque requête (plus de handshake TLS
    Supabase par clic — 2026-08-16).

    Les requêtes arrivent par file (queue.Queue) ; seule la plus récente est
    traitée (les autres sont périmées — même garantie que la génération).
    """
    finished_rows = Signal(object)   # (generation, rows)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: "queue.Queue" = queue.Queue()

    def request(self, id_lo, id_hi, is_staff, search_text, filters, gen):
        self._queue.put((id_lo, id_hi, is_staff, search_text, filters, gen))

    def stop(self):
        """Demande l'arrêt propre (à connecter à aboutToQuit)."""
        self._queue.put(None)

    def run(self):
        from LarcRH.common.hr_database import HRDatabase
        conn = None
        try:
            try:
                conn = _open_dedicated_connection()
            except Exception:
                from larccommon.error_reporting import get_reporter
                get_reporter().report_exception()
                import traceback
                traceback.print_exc()
                conn = None
            while True:
                item = self._queue.get()
                if item is None:
                    break
                # Vider la file : seule la requête la plus récente compte
                latest = item
                while True:
                    try:
                        nxt = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is None:
                        latest = None
                        break
                    latest = nxt
                if latest is None:
                    break
                id_lo, id_hi, is_staff, search_text, filters, gen = latest
                rows: list = []
                try:
                    rows = HRDatabase.search_staff(
                        id_lo, id_hi, is_staff,
                        search_text=search_text, filters=filters, conn=conn)
                except Exception:
                    # Connexion morte ? La rouvrir et retenter une fois
                    from larccommon.error_reporting import get_reporter
                    get_reporter().report_exception()
                    try:
                        if conn:
                            conn.close()
                    except Exception:
                        from larccommon.error_reporting import get_reporter
                        get_reporter().report_exception()
                        pass
                    try:
                        conn = _open_dedicated_connection()
                        rows = HRDatabase.search_staff(
                            id_lo, id_hi, is_staff,
                            search_text=search_text, filters=filters, conn=conn)
                    except Exception:
                        from larccommon.error_reporting import get_reporter
                        get_reporter().report_exception()
                        import traceback
                        traceback.print_exc()
                        rows = []
                self.finished_rows.emit((gen, rows))
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    from larccommon.error_reporting import get_reporter
                    get_reporter().report_exception()
                    pass


class StaffGrid(QWidget):
    """Grille de photos responsive avec toolbar de recherche/filtres."""

    staff_selected = Signal(dict)

    def __init__(self, cat_key: str, id_lo: int, id_hi: int,
                 is_staff: bool = False, parent=None):
        super().__init__(parent)
        self._cat_key = cat_key
        self._id_lo = id_lo
        self._id_hi = id_hi
        self._is_staff = is_staff
        self._cols = 1
        self._view_mode = "grid"  # "grid" or "table"
        self._search_text = ""
        self._filter_campus: int | None = None  # conservé (API filters) mais plus de combo UI
        self._filter_status: str | None = None
        self._sort_by = "name"
        self._all_data: list[dict] = []
        self._load_generation = 0
        self._grid_built = False
        # Worker persistant : connexion dédiée ouverte UNE fois, réutilisée
        self._loader = _PersistentLoader(self)
        self._loader.finished_rows.connect(self._rows_ready)
        self._loader.start()
        QApplication.instance().aboutToQuit.connect(self._loader.stop)
        ds.theme_changed.connect(self.refresh)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Barre d'attente (indéterminée) — visible pendant la construction
        # de la grille (données + vignettes). Restera animée entre les lots
        # de photos ; avec Supabase, signale au moins que ça travaille.
        self._busy = QProgressBar()
        self._busy.setRange(0, 0)  # indéterminé
        self._busy.setTextVisible(False)
        self._busy.setFixedHeight(ds.space_xxs + ds.space_xxs)
        self._busy.setVisible(False)
        outer.addWidget(self._busy)

        # ── Toolbar ──
        self._setup_toolbar(outer)

        # ── Légende des couleurs des types d'employés ──
        outer.addWidget(StaffTypeLegend())

        # ── Scroll area for cards / table ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setStyleSheet(f"background: {theme_manager.palette.background}; border: none;")

        self._container = QWidget()
        self._grid = QGridLayout(self._container)
        self._grid.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self._grid.setSpacing(SPACING)
        self._table_view: M3TableWidget | None = None

        self._scroll.setWidget(self._container)
        outer.addWidget(self._scroll, 1)

        self.refresh()

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------
    def _setup_toolbar(self, parent_layout):
        p = theme_manager.palette
        toolbar = QWidget()
        toolbar.setAttribute(Qt.WA_StyledBackground, True)
        toolbar.setStyleSheet(f"background: {p.surface}; border-bottom: 1px solid {p.outline_variant};")
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(ds.space_md, ds.space_xs, ds.space_md, ds.space_xs)
        tb.setSpacing(ds.space_sm)

        # Search
        from phibuilder.widgets import M3TextField
        self._search_field = M3TextField()
        self._search_field.setPlaceholderText("Rechercher...")
        self._search_field.setFixedHeight(ds.field_height)
        self._search_field.setStyleSheet(ds.flat_input_qss())
        self._search_field.setMinimumWidth(ds.sidebar_width - ds.space_lg)
        self._search_field.textChanged.connect(self._on_search_changed)
        # Toolbar responsive (IE5/Q14) : la recherche reçoit 2 parts, chaque
# filtre 1 part — tous plafonnés à field_max_width (IE6).
        tb.addWidget(self._search_field, 2)

        # NB : pas de filtre campus — la sidebar catégorise déjà par niveau
        # (Collège/Lycée, Primaire, Maternelle) ; retiré à la demande (2026-08-16)

        # Status filter — valeurs exactes de emp_status en base
        self._status_combo = QComboBox()
        self._status_combo.addItem("Tous statuts", None)
        self._status_combo.addItem("Actif", "actif")
        self._status_combo.addItem("Suspendu", "suspendu")
        self._status_combo.addItem("En préavis", "en_préavis")
        self._status_combo.addItem("Parti", "parti")
        self._status_combo.setFixedHeight(ds.field_height)
        self._status_combo.setStyleSheet(ds.flat_input_qss())
        self._status_combo.currentIndexChanged.connect(self._on_filter_changed)
        tb.addWidget(self._status_combo, 1)

        # Sort
        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["Nom", "Ancienneté", "Date d'embauche"])
        self._sort_combo.setFixedHeight(ds.field_height)
        self._sort_combo.setStyleSheet(ds.flat_input_qss())
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        tb.addWidget(self._sort_combo, 1)

        tb.addStretch()

        # View toggle
        grid_btn = QPushButton()
        grid_btn.setIcon(md3_icon("view_module", color=p.primary, size=18))
        grid_btn.setFixedSize(ds.field_height, ds.field_height)
        grid_btn.setCursor(Qt.PointingHandCursor)
        grid_btn.setToolTip("Vue grille")
        grid_btn.clicked.connect(lambda checked: self._set_view_mode("grid"))
        grid_btn.setStyleSheet(f"QPushButton {{ border: 1px solid {p.outline}; border-radius: {ds.radius_xs}px; background: transparent; }} QPushButton:hover {{ background: {p.surface_variant}; }}")
        tb.addWidget(grid_btn)

        table_btn = QPushButton()
        table_btn.setIcon(md3_icon("description", color=p.primary, size=18))
        table_btn.setFixedSize(ds.field_height, ds.field_height)
        table_btn.setCursor(Qt.PointingHandCursor)
        table_btn.setToolTip("Vue tableau")
        table_btn.clicked.connect(lambda checked: self._set_view_mode("table"))
        table_btn.setStyleSheet(f"QPushButton {{ border: 1px solid {p.outline}; border-radius: {ds.radius_xs}px; background: transparent; }} QPushButton:hover {{ background: {p.surface_variant}; }}")
        tb.addWidget(table_btn)

        # Export
        export_btn = QPushButton("CSV")
        export_btn.setFixedHeight(ds.field_height)
        export_btn.setCursor(Qt.PointingHandCursor)
        export_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {p.primary}; border: 1px solid {p.primary};
            border-radius: {ds.radius_xs}px; padding: {ds.space_xxs}px {ds.space_xs}px; font-size: {theme_manager.font_size(11)}px; }}
            QPushButton:hover {{ background: {p.primary}; color: white; }}
        """)
        export_btn.clicked.connect(self._on_export_csv)
        tb.addWidget(export_btn)

        parent_layout.addWidget(toolbar)

    # ------------------------------------------------------------------
    # Search / filter / sort
    # ------------------------------------------------------------------
    @safe_slot("StaffGrid._on_search_changed")
    def _on_search_changed(self, text: str):
        self._search_text = text.strip()
        self.refresh()

    @safe_slot("StaffGrid._on_filter_changed")
    def _on_filter_changed(self):
        self._filter_status = self._status_combo.currentData()
        self.refresh()

    @safe_slot("StaffGrid._on_sort_changed")
    def _on_sort_changed(self, idx: int):
        self._sort_by = ["name", "seniority", "hire_date"][idx]
        self.refresh()

    def _set_view_mode(self, mode: str):
        self._view_mode = mode
        self.refresh()

    @safe_slot("StaffGrid._on_export_csv")
    def _on_export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exporter CSV", "personnel.csv", "CSV (*.csv)")
        if not path:
            return
        import csv
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(["ID", "Nom", "Prénom", "Email", "Rôles", "Statut"])
            for d in self._all_data:
                roles = []
                if d.get("is_staff"):
                    for k, lbl in [("type_DRH","DRH"),("type_Comptable","Comptable"),
                                   ("type_ressources_Humaines","RH"),("type_Bulletin_Releves","Bull")]:
                        if d.get(k):
                            roles.append(lbl)
                else:
                    if d.get("is_teacher"): roles.append("Ens")
                    if d.get("is_coordonator"): roles.append("Coord")
                    if d.get("is_adm"): roles.append("Admin")
                w.writerow([d["id"], d["last_name"], d["first_name"], d.get("email",""),
                           " · ".join(roles), d.get("emp_status","actif")])

    def refresh(self, quiet: bool = False):
        """Recharge les données en ARRIÈRE-PLAN (QThread, connexion dédiée).

        L'UI reste fluide pendant la requête (Intranet comme Supabase) et la
        barre d'attente reste animée. quiet=True : rechargement silencieux
        (showEvent) sans barre.
        """
        if not quiet:
            self._busy.setVisible(True)
        self._load_generation += 1
        self._loader.request(
            self._id_lo, self._id_hi, self._is_staff,
            self._search_text,
            {"campus_id": self._filter_campus, "status": self._filter_status,
             "sort": self._sort_by},
            self._load_generation)

    def _rows_ready(self, payload):
        """Résultat du worker : rendu si les données ont changé (ou 1ʳᵉ fois)."""
        gen, rows = payload
        if gen != self._load_generation:
            return  # résultat périmé — un chargement plus récent a démarré
        changed = rows != self._all_data
        self._all_data = rows
        if changed or not self._grid_built:
            if self._view_mode == "grid":
                self._render_grid()
            else:
                self._render_table()
            self._grid_built = True
        self._busy.setVisible(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._view_mode == "grid":
            self._reflow()

    def showEvent(self, event):
        super().showEvent(event)
        # Rechargement silencieux en arrière-plan — le rebuild n'a lieu dans
        # _rows_ready QUE si les données ont changé (pas de flicker, 2026-08-16)
        if getattr(self, "_grid_built", False):
            self.refresh(quiet=True)

    def _cols_for_width(self) -> int:
        # Largeur STABLE : la scroll area (pas le viewport, qui oscille avec
        # l'apparition/disparition de la barre de défilement). Sans cela, le
        # nombre de colonnes change (5↔6) à chaque bascule de la barre, les
        # cartes se ré-empilent, la hauteur change, la barre rebascule…
        # BOUCLE INFINIE de reflow (flicker permanent, signalé 2026-08-16).
        sb = self._scroll.verticalScrollBar()
        reserve = sb.width() if sb else 0   # réserve PERMANENTE de scrollbar
        avail = self._scroll.width() - MARGIN * 2 - reserve
        return max(1, (avail + SPACING) // (CARD_W + SPACING))

    def _reflow(self):
        """Redistribue les widgets dans la grille après redimensionnement."""
        new_cols = self._cols_for_width()
        if new_cols == self._cols:
            return
        self._cols = new_cols

        # Construire une liste stable avant de manipuler le layout
        widgets = []
        for i in range(self._grid.count()):
            item = self._grid.itemAt(i)
            if item and item.widget():
                widgets.append(item.widget())

        # Retirer tous les widgets
        for w in widgets:
            self._grid.removeWidget(w)

        # Réinsérer dans le bon ordre
        for i, w in enumerate(widgets):
            row, col = divmod(i, new_cols)
            self._grid.addWidget(w, row, col)

    # NB : le chargement passe par _LoaderThread (voir refresh/_rows_ready) —
    # plus de _load_data synchrone : la requête bloquait l'UI (Supabase).

    def _render_grid(self):
        # Hide table if visible
        if self._table_view:
            self._table_view.hide()

        # Set grid container as scroll content
        self._scroll.setWidget(self._container)
        self._container.show()

        # Clean grid
        i = self._grid.count()
        while i > 0:
            i -= 1
            item = self._grid.itemAt(i)
            if item and item.widget() and item.widget() is not self._table_view:
                w = self._grid.takeAt(i)
                if w.widget():
                    w.widget().deleteLater()

        if not self._all_data:
            lbl = QLabel("Aucun membre trouvé")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(f"color: {theme_manager.palette.text_soft}; font-size: {theme_manager.font_size(13)}px;")
            self._grid.addWidget(lbl, 0, 0)
            return

        self._cols = self._cols_for_width()
        for i, data in enumerate(self._all_data):
            card = StaffCard(data)
            card.event_requested.connect(
                lambda d, c=card: self._on_card_event(d, c))
            card.activated.connect(self.staff_selected)
            self._grid.addWidget(card, i // self._cols, i % self._cols)

        # Photos en différé : la grille s'affiche immédiatement, les photos
        # arrivent par lots de 10 (pas de processEvents — anti-pattern interdit)
        QTimer.singleShot(0, lambda: self._load_photos_async(0))

    @safe_slot("StaffGrid._on_card_event")
    def _on_card_event(self, data: dict, card):
        from LarcRH.views.staff_events import open_staff_event_generator
        open_staff_event_generator(data, card)

    def _load_photos_async(self, start: int):
        cards = [
            item.widget()
            for i in range(self._grid.count())
            if (item := self._grid.itemAt(i)) and item.widget()
            and isinstance(item.widget(), StaffCard)
        ]
        for c in cards[start:start + 10]:
            if not c.has_photo():
                c.load_photo()
        if start + 10 < len(cards):
            QTimer.singleShot(0, lambda: self._load_photos_async(start + 10))
        elif any(not c.has_photo() for c in cards):
            # Des cartes reconstruites entre-temps (showEvent) n'ont pas de
            # photo : relancer une passe — auto-réparation du chargement
            QTimer.singleShot(50, lambda: self._load_photos_async(0))

    def _render_table(self):
        # Clean grid widgets
        i = self._grid.count()
        while i > 0:
            i -= 1
            item = self._grid.itemAt(i)
            if item and item.widget():
                w = self._grid.takeAt(i)
                if w.widget():
                    w.widget().deleteLater()

        # Build table if needed, add to scroll
        if not self._table_view:
            self._table_view = M3TableWidget()
            self._table_view.setEditTriggers(M3TableWidget.NoEditTriggers)
            self._table_view.setSelectionBehavior(M3TableWidget.SelectRows)
            self._table_view.setAlternatingRowColors(False)
            self._table_view.verticalHeader().setDefaultSectionSize(ds.table_row_min)
            self._table_view.setStyleSheet(ds.table_qss())
            self._table_view.doubleClicked.connect(self._on_table_double_click)

        self._scroll.setWidget(self._table_view)
        self._table_view.show()
        cols = ["ID", "Nom", "Prénom", "Email", "Rôles", "Statut"]
        self._table_view.setColumnCount(len(cols))
        self._table_view.set_headers(cols)
        self._table_view.setRowCount(0)
        self._table_view.setColumnHidden(0, True)

        for row_idx, d in enumerate(self._all_data):
            self._table_view.setRowCount(row_idx + 1)
            from PySide6.QtWidgets import QTableWidgetItem
            self._table_view.setItem(row_idx, 0, QTableWidgetItem(str(d["id"])))
            self._table_view.setItem(row_idx, 1, QTableWidgetItem(d.get("last_name", "")))
            self._table_view.setItem(row_idx, 2, QTableWidgetItem(d.get("first_name", "")))
            self._table_view.setItem(row_idx, 3, QTableWidgetItem(d.get("email", "")))
            roles = []
            if d.get("is_staff"):
                for k, lbl in [("type_DRH","DRH"),("type_Comptable","Comptable"),
                               ("type_ressources_Humaines","RH"),("type_Bulletin_Releves","Bull")]:
                    if d.get(k): roles.append(lbl)
            else:
                if d.get("is_teacher"): roles.append("Ens")
                if d.get("is_coordonator"): roles.append("Coord")
                if d.get("is_adm"): roles.append("Admin")
            self._table_view.setItem(row_idx, 4, QTableWidgetItem(" · ".join(roles)))
            self._table_view.setItem(row_idx, 5, QTableWidgetItem(d.get("emp_status", "actif")))

        h = self._table_view.horizontalHeader()
        # IE8 : seule la colonne Nom s'étire ; les autres au contenu
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setStretchLastSection(False)
        h.resizeSection(2, ds.sp(SpacingToken.XXXL) - ds.sp(SpacingToken.SM))
        h.resizeSection(3, ds.sp(SpacingToken.HUGE) - ds.sp(SpacingToken.MD))
        h.resizeSection(4, ds.sp(SpacingToken.XXXL) + ds.sp(SpacingToken.SM))
        h.resizeSection(5, ds.sp(SpacingToken.XXL))
        self._table_view.setSortingEnabled(True)  # IE8d : tri par clic

    @safe_slot("StaffGrid._on_table_double_click")
    def _on_table_double_click(self, index):
        row = index.row()
        if 0 <= row < len(self._all_data):
            self.staff_selected.emit(self._all_data[row])
