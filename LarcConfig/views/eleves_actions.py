"""Actions sur la place sélectionnée de « Élèves par classe » (étape 4).

Barre de boutons + menu contextuel, activés selon l'état de la ligne
(student_slot_rules.available_actions) ; une action grisée explique pourquoi
dans son infobulle. Chaque écriture passe par une confirmation qui annonce
les conséquences exactes (matières désinscrites, lignes de notes effacées),
puis par db_students_write (UPDATE uniquement, transaction, garde serveur).
Après une écriture réussie : signal `changed` (rechargement de la classe et
de la vue d'ensemble « Classes » du campus).
"""
from larccommon.design_system import ds
from larccommon.icons import icon as md3_icon
from larccommon.safe_slot import safe_slot
from larccommon.theme import theme_manager
from phibuilder.phi.scale import SpacingToken
from phibuilder.widgets import M3Button, M3Menu
from phibuilder.widgets.button import ButtonVariant
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QWidget

from LarcConfig.common import db_students, db_students_write
from LarcConfig.common.student_slot_rules import (
    ACTIF,
    ACTION_LABELS,
    ADMIN,
    INSCRIRE,
    MODIFIER,
    PRESENCE,
    REINITIALISER,
    SECRETARY_NOTE,
    available_actions,
)
from LarcConfig.views.eleve_dialogs import NameDialog, confirm

_ICONS = {INSCRIRE: 'add', MODIFIER: 'edit', PRESENCE: 'person', REINITIALISER: 'refresh'}
_TABLE_LABELS = {
    'larcauth_learnermat_has_devpers_unit': "développement personnel (maternelle)",
    'larcauth_learnermat_has_subjectevals_unit': "évaluations par matière (maternelle)",
    'larcauth_learnerprim_has_unit_period': "notes d'unité (primaire)",
    'larcauth_learnerpp_has_termsubjectpp': "notes PP",
    'larcauth_learnerpei_has_termsubjectpei': "notes PEI / MYP",
    'larcauth_learnerdp_has_termsubjectdp': "notes DP",
    'larcauth_learner_has_termothersubject': "autres matières",
}


def presence_label(row) -> str:
    return "Marquer absent (parti)" if row and row['state'] == ACTIF else "Marquer présent"


def _name(row) -> str:
    return f"{(row.get('last_name') or '').strip()} {(row.get('first_name') or '').strip()}".strip()


class SlotActionBar(QWidget):
    changed = Signal()  # émis après une écriture réussie

    def __init__(self, campus):
        super().__init__()
        self._campus = campus
        self._row = None
        phi = theme_manager.phi_theme
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(phi.spacing.spacing(SpacingToken.XS))
        self._buttons = {}
        for action in (INSCRIRE, MODIFIER, PRESENCE, REINITIALISER):
            btn = M3Button(ACTION_LABELS[action], theme=phi, variant=ButtonVariant.TONAL)
            btn.clicked.connect(lambda checked=False, a=action: self.run(a))
            self._buttons[action] = btn
            lay.addWidget(btn)
        lay.addStretch()
        self.set_row(None)

    # ── État des boutons ─────────────────────────────────────────────────

    def set_row(self, row):
        """Active les actions possibles pour la ligne sélectionnée (ou aucune)."""
        self._row = row
        actions = available_actions(row) if row else {}
        for action, btn in self._buttons.items():
            ok, reason = actions.get(action, (False, "Sélectionner une ligne du tableau."))
            btn.setEnabled(ok)
            btn.setToolTip(reason)
        self._buttons[PRESENCE].setText(presence_label(row))
        self._restyle()

    def _restyle(self):
        p = theme_manager.palette
        for action, btn in self._buttons.items():
            color = p.primary if btn.isEnabled() else p.text_disabled
            btn.setIcon(md3_icon(_ICONS[action], color=color, size=ds.icon_sm))

    def menu_for(self, row, parent) -> M3Menu:
        """Menu contextuel : mêmes actions, raison en infobulle si grisée."""
        self.set_row(row)
        menu = M3Menu(theme=theme_manager.phi_theme, parent=parent)
        menu.setToolTipsVisible(True)
        for action, (ok, reason) in available_actions(row).items():
            label = presence_label(row) if action == PRESENCE else ACTION_LABELS[action]
            act = menu.addAction(label)
            act.setEnabled(ok)
            act.setToolTip(reason)
            act.triggered.connect(lambda checked=False, a=action: self.run(a))
        return menu

    # ── Exécution ────────────────────────────────────────────────────────

    @safe_slot("SlotActionBar.run")
    def run(self, action: str):
        row = self._row
        if not row or not available_actions(row)[action][0]:
            return
        handler = {INSCRIRE: self._inscrire, MODIFIER: self._modifier,
                   PRESENCE: self._presence, REINITIALISER: self._reinitialiser}[action]
        result = handler(row)
        if result is None:  # annulé par l'utilisateur
            return
        self._report(result)
        if result.ok:
            self.changed.emit()

    def _report(self, result):
        text = result.message
        if result.warnings:
            text += "\n\nAttention :\n" + "\n".join(f"• {w}" for w in result.warnings)
        if result.ok:
            QMessageBox.information(self, "Enregistré", text)
        else:
            QMessageBox.warning(self, "Non enregistré", text)

    def _inscrire(self, row):
        dlg = NameDialog(self, f"Inscrire un élève — place {row['id']}",
                         f"Place n° {row['slot']:02d} de la classe {row['id'] // 100}.",
                         note=SECRETARY_NOTE.get(self._campus.key, ""),
                         confirm_text="Inscrire")
        if dlg.exec() != dlg.DialogCode.Accepted or not dlg.values:
            return None
        return db_students_write.inscrire(row['id'], *dlg.values)

    def _modifier(self, row):
        dlg = NameDialog(self, f"Modifier nom / prénom — {row['id']}",
                         "Corriger l'orthographe. Pour un autre enfant, réinitialiser "
                         "la place puis inscrire.",
                         last_name=row.get('last_name') or "",
                         first_name=row.get('first_name') or "")
        if dlg.exec() != dlg.DialogCode.Accepted or not dlg.values:
            return None
        return db_students_write.rename(row['id'], *dlg.values)

    def _presence(self, row):
        name = _name(row)
        if row['state'] == ACTIF:
            lines = [f"{name} passe à l'état « parti » : il/elle n'apparaît plus comme élève actif."]
            if self._campus.key == 'college_lycee':
                n = db_students.get_action_preview(row['id'], self._campus.key)['enrolments']
                lines.append("Désinscrit(e) de " + ("? (illisible)" if n is None else str(n))
                             + " matière(s).")
            lines.append("Le nom est conservé ; les notes ne sont pas effacées.")
            if not confirm(self, "Marquer absent", lines, "Marquer absent"):
                return None
            return db_students_write.set_present(row['id'], False)
        lines = [f"{name} redevient élève actif.",
                 "Aucune réinscription automatique aux matières : la faire dans "
                 "« Matières par classe »."]
        if not confirm(self, "Marquer présent", lines, "Marquer présent"):
            return None
        return db_students_write.set_present(row['id'], True)

    def _reinitialiser(self, row):
        preview = db_students.get_action_preview(row['id'], self._campus.key)
        notes = preview['notes'] or {}
        n_notes = sum(notes.values())
        lines = [f"La place {row['id']} redevient libre (nom gabarit « Name of {row['id']} »).",
                 f"{_name(row)} disparaît de la classe."]
        n = preview['enrolments']
        lines.append("Désinscrit(e) de " + ("? (illisible)" if n is None else str(n))
                     + " matière(s).")
        admin = row['verdict'] == ADMIN or n_notes > 0
        if admin:
            tables = [f"{v} ligne(s) — {_TABLE_LABELS.get(t, t)}" for t, v in notes.items() if v]
            lines.append(f"Efface {n_notes} ligne(s) de notes dans {len(tables)} table(s) :")
            lines.extend(f"   {t}" for t in tables)
            lines.append("Confirmation administrateur : cet effacement est définitif.")
        if not confirm(self, "Réinitialiser la place", lines,
                       "Effacer les notes et libérer" if admin else "Libérer la place",
                       danger=admin):
            return None
        return db_students_write.reinitialiser(row['id'], accept_notes_erase=admin)
