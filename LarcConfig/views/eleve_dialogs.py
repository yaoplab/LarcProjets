"""Dialogues des actions sur une place élève (étape 4).

NameDialog : saisie Nom / Prénom (inscription ou correction), validée par
student_slot_rules.clean_names AVANT fermeture — l'erreur s'affiche dans le
dialogue, rien n'est envoyé à la base tant que la saisie est invalide.
confirm() : confirmation avec les conséquences exactes (nombres de matières,
de notes…) ; le libellé du bouton dit ce qui va se passer.
"""
from larccommon.design_system import ds
from larccommon.safe_slot import safe_slot
from larccommon.theme import theme_manager
from phibuilder.phi.scale import SpacingToken
from phibuilder.widgets import M3Dialog, M3Label, M3TextField

from LarcConfig.common.student_slot_rules import clean_names


class NameDialog(M3Dialog):
    def __init__(self, parent, title: str, message: str, last_name: str = "",
                 first_name: str = "", note: str = "", confirm_text: str = "Enregistrer"):
        phi = theme_manager.phi_theme
        super().__init__(parent, title, message, theme=phi)
        p = theme_manager.palette
        sp = phi.spacing.spacing
        self.confirm_btn.setText(confirm_text)
        self.cancel_btn.setText("Annuler")
        self.values = None

        lay = self.layout()
        pos = 2  # après titre + message, avant les boutons
        if note:
            self._note = M3Label(note, theme=phi, style="body_small")
            self._note.setWordWrap(True)
            self._note.set_color(p.text_soft)
            lay.insertWidget(pos, self._note)
            pos += 1
        self._last = self._field(lay, pos, "Nom (en majuscules à l'enregistrement)", last_name)
        self._first = self._field(lay, pos + 2, "Prénom", first_name)
        self._error = M3Label("", theme=phi, style="body_small")
        self._error.setWordWrap(True)
        self._error.set_color(p.error)
        self._error.setVisible(False)
        lay.insertWidget(pos + 4, self._error)
        lay.setSpacing(sp(SpacingToken.SM))
        self._last.setFocus()

    def _field(self, lay, pos: int, label: str, value: str) -> M3TextField:
        phi = theme_manager.phi_theme
        lay.insertWidget(pos, M3Label(label, theme=phi, style="body_small"))
        field = M3TextField((value or "").strip(), theme=phi)
        field.setMinimumWidth(ds.space_xxxl * 3)
        lay.insertWidget(pos + 1, field)
        return field

    @safe_slot("NameDialog.accept")
    def accept(self):
        try:
            self.values = clean_names(self._last.text(), self._first.text())
        except ValueError as e:
            self._error.setText(str(e))
            self._error.setVisible(True)
            return
        super().accept()


def confirm(parent, title: str, lines: list, confirm_text: str, danger: bool = False) -> bool:
    """Confirmation listant les conséquences (une par ligne). danger=True :
    bouton de validation en couleur d'erreur (effacement de notes)."""
    phi = theme_manager.phi_theme
    dlg = M3Dialog(parent, title, "\n".join(f"• {line}" for line in lines), theme=phi)
    dlg.confirm_btn.setText(confirm_text)
    dlg.cancel_btn.setText("Annuler")
    if danger:
        p = theme_manager.palette
        dlg.confirm_btn.setStyleSheet(
            f"M3Button {{ background: {p.error}; color: {p.on_error}; "
            f"border-radius: {ds.radius_sm}px; padding: 0 {ds.space_md}px; }}")
    dlg.cancel_btn.setDefault(True)  # Entrée = Annuler : jamais de validation par réflexe
    return dlg.exec() == dlg.DialogCode.Accepted
