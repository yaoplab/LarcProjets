"""Règles des slots élèves (pur Python, sans Qt ni SQL).

Principe gabarit : chaque classe possède 40 slots pré-créés, ID élève =
100 × id_classe + n° (01..40). Un slot est « libre » quand son nom est un
gabarit (placeholder), « actif » quand l'élève est activé, « parti » quand il
est désactivé mais porte encore un vrai nom.

Formats de placeholder relevés en base (2026-10-26, 2400 slots, SELECT) :
  - 'Name of <ID>' / 'First_name of <ID>'
  - 'Nom de <ID>' (1 ou 2 espaces) / 'Prénom de <ID>' ou 'Prenom de <ID>'
  - 'ID<ID>' dans les deux champs (y compris faute de frappe 'ID11810')
  - nom = '<ID>' (avec ou sans espace devant, parfois mal tapé '2121120'),
    prénom 'Student' / ' Studebnt' / un prénom résiduel (' Imrane')
  - '<ID> Name' / '<ID> Firstname'
  - 'Name' / 'Fist Name', 'Lastname' / 'FIrstname'
"""
import re
from dataclasses import dataclass
from typing import Optional

SLOTS_PER_CLASS = 40

# Préfixes gabarit : « Name of 123139 », « Nom de  211105 », « Prénom de … »
_PREFIX_RE = re.compile(r'^(name of|first_name of|first name of|nom de|pr[ée]nom de)\b')
# Champ purement numérique, éventuellement préfixé « ID » et/ou suivi d'un
# mot générique : « 111140 », « ID221201 », « 112108 Name », « 112108 Firstname »
_NUMERIC_RE = re.compile(r'^(id\s*)?\d+(\s+(name|firstname|first name|lastname))?$')
# Mots génériques seuls (casse/espaces ignorés)
_GENERIC = {'name', 'lastname', 'last name', 'firstname', 'first name', 'fist name',
            'student', 'studebnt', 'prenom', 'prénom', 'nom'}

# États d'un slot
ACTIF = 'actif'
LIBRE = 'libre'
PARTI = 'parti'

# Verdicts de réinitialisation d'un slot (retour à l'état « libre »)
PERMIS = 'permis'
ADMIN = 'admin_confirmation'
INTERDIT = 'interdit'

# Codes d'anomalie
ANOMALIES = {
    'E1': "Place active mais nom de place libre",
    'E2': "Nom ou prénom vide",
    'E3': "Classe de l'élève ≠ classe du slot",
    'E4': "Classe incomplète (moins de 40 places)",
    'E5': "Doublon nom + prénom actif",
    'E6': "Élève actif dans une classe désactivée",
    'E7': "Aucune matière active ce trimestre",
}

# Textes de la légende et des infobulles — source unique (écrans + tooltips)
ANOMALY_HELP = {
    'E1': "Place active avec un nom de place libre",
    'E2': "Nom ou prénom vide",
    'E3': "Classe de l'élève ≠ classe de la place",
    'E4': "Classe incomplète (moins de 40 places)",
    'E5': "Doublon : même enfant actif dans une autre classe",
    'E6': "Élève actif dans une classe désactivée",
    'E7': "Collège/lycée : élève actif sans matière ce trimestre",
}
ANOMALY_MARK_HELP = "Triangle rouge = anomalie (code et détail dans la colonne Anomalie)"
STATE_HELP = {
    'actif': "Actif : élève inscrit cette année",
    'libre': "Libre : place vide (nom gabarit), disponible",
    'parti': "Parti : élève désactivé, son nom est conservé",
}
VERDICT_HELP = {
    'permis': "Permise : aucune note ni bulletin, la place peut être libérée",
    'admin_confirmation': "Admin : notes saisies mais pas de bulletin, "
                          "confirmation d'un administrateur",
    'interdit': "Interdite : bulletin produit, ou données incertaines (dans le doute on bloque)",
}


def slot_number(student_id: int) -> int:
    """N° du slot dans la classe (01..40)."""
    return student_id % 100


def classroom_of(student_id: int) -> int:
    """Classe propriétaire du slot (ID // 100)."""
    return student_id // 100


def _norm(value) -> str:
    return ' '.join((value or '').split()).lower()


def _is_placeholder_field(value: str) -> bool:
    v = _norm(value)
    return bool(v) and (bool(_PREFIX_RE.match(v)) or bool(_NUMERIC_RE.match(v)) or v in _GENERIC)


def is_placeholder(first_name, last_name, student_id: int = None) -> bool:
    """Vrai si le slot porte un nom gabarit (cf. formats en tête de module).

    Le nom de famille est décisif : un nom gabarit (« 112111 ») rend le slot
    libre même si le prénom ressemble à un vrai prénom (résidu de saisie).
    Un prénom gabarit ne suffit que si le nom est vide ou générique aussi."""
    last, first = _norm(last_name), _norm(first_name)
    if student_id is not None and str(student_id) in (last, first):
        return True
    if _is_placeholder_field(last):
        return True
    return _is_placeholder_field(first) and (not last or _is_placeholder_field(last))


def slot_state(enabled: bool, placeholder: bool) -> str:
    """'actif' | 'libre' | 'parti'. Actif + gabarit = actif (anomalie E1)."""
    if enabled:
        return ACTIF
    return LIBRE if placeholder else PARTI


def normalize_name(last_name, first_name) -> tuple:
    """Clé de doublon E5 : espaces réduits, minuscules, accents conservés."""
    return (_norm(last_name), _norm(first_name))


@dataclass(frozen=True)
class SlotContext:
    """Données d'un slot nécessaires au calcul des anomalies."""
    student_id: int
    first_name: Optional[str]
    last_name: Optional[str]
    enabled: bool
    s_classroom_id: Optional[int]
    classroom_enabled: bool = True
    duplicate_of: tuple = ()          # ((id, classe), …) autres élèves actifs homonymes
    needs_subjects: bool = False      # collège/lycée : E7 applicable
    active_subjects: Optional[int] = None  # inscriptions actives ce trimestre


def class_anomalies(slot_count: int) -> list:
    """Anomalies de niveau classe : E4 si moins de 40 slots."""
    return ['E4'] if slot_count < SLOTS_PER_CLASS else []


def slot_anomalies(ctx: SlotContext) -> list:
    """Codes d'anomalie d'un slot (E1-E3, E5-E7 ; E4 est au niveau classe)."""
    codes = []
    placeholder = is_placeholder(ctx.first_name, ctx.last_name, ctx.student_id)
    if ctx.enabled and placeholder:
        codes.append('E1')
    if not (ctx.first_name or '').strip() or not (ctx.last_name or '').strip():
        codes.append('E2')
    if ctx.s_classroom_id != classroom_of(ctx.student_id):
        codes.append('E3')
    if ctx.enabled and ctx.duplicate_of:
        codes.append('E5')
    if ctx.enabled and not ctx.classroom_enabled:
        codes.append('E6')
    if ctx.enabled and not placeholder and ctx.needs_subjects and not ctx.active_subjects:
        codes.append('E7')
    return codes


def anomaly_text(code: str, ctx: SlotContext = None) -> str:
    """Libellé court d'une anomalie, avec l'autre ID/classe pour E5."""
    text = ANOMALIES.get(code, code)
    if code == 'E5' and ctx is not None and ctx.duplicate_of:
        others = ", ".join(f"{sid} ({cls})" for sid, cls in ctx.duplicate_of)
        text = f"{text} : {others}"
    return f"{code} {text}"


def reset_verdict(has_notes: Optional[bool], has_bulletin: Optional[bool]) -> str:
    """Peut-on remettre le slot à l'état libre ?

    Dans le doute (None = inconnu), on bloque : 'interdit'. Un bulletin
    existant interdit ; des notes sans bulletin exigent une confirmation admin."""
    if has_notes is None or has_bulletin is None:
        return INTERDIT
    if has_bulletin:
        return INTERDIT
    if has_notes:
        return ADMIN
    return PERMIS


def assess_slots(slots: list, classroom_enabled: bool, duplicates: dict,
                 active_subjects: dict, needs_subjects: bool,
                 evidence: dict = None) -> list:
    """Assemble l'état complet des slots d'une classe (pur, testable).

    slots : dicts id, first_name, last_name, enabled, s_classroom_id.
    duplicates : {id: ((autre_id, autre_classe), …)} (E5).
    active_subjects : {id: nb inscriptions actives ce trimestre} (E7).
    evidence : {id: {'notes': int|None, 'has_notes': bool|None,
    'has_bulletin': bool|None}} — absent = inconnu."""
    rows = []
    for s in slots:
        sid = s['id']
        placeholder = is_placeholder(s.get('first_name'), s.get('last_name'), sid)
        ctx = SlotContext(
            student_id=sid, first_name=s.get('first_name'), last_name=s.get('last_name'),
            enabled=bool(s.get('enabled')), s_classroom_id=s.get('s_classroom_id'),
            classroom_enabled=classroom_enabled,
            duplicate_of=tuple(duplicates.get(sid, ())),
            needs_subjects=needs_subjects,
            active_subjects=active_subjects.get(sid, 0),
        )
        codes = slot_anomalies(ctx)
        ev = (evidence or {}).get(sid, {})
        rows.append({
            'id': sid,
            'slot': slot_number(sid),
            'first_name': s.get('first_name'),
            'last_name': s.get('last_name'),
            'state': slot_state(ctx.enabled, placeholder),
            'placeholder': placeholder,
            'notes': ev.get('notes'),
            'has_notes': ev.get('has_notes'),
            'has_bulletin': ev.get('has_bulletin'),
            'verdict': reset_verdict(ev.get('has_notes'), ev.get('has_bulletin')),
            'anomalies': codes,
            'anomaly_texts': [anomaly_text(c, ctx) for c in codes],
        })
    return rows


def summarize(rows: list, slot_count: int = None) -> dict:
    """Compteurs du bandeau : actifs, libres, partis, anomalies (slots
    concernés + 1 si la classe elle-même est incomplète, E4)."""
    count = len(rows) if slot_count is None else slot_count
    out = {ACTIF: 0, LIBRE: 0, PARTI: 0}
    for r in rows:
        out[r['state']] += 1
    out['anomalies'] = sum(1 for r in rows if r['anomalies']) + len(class_anomalies(count))
    out['class_anomalies'] = class_anomalies(count)
    codes = {}
    for code in [c for r in rows for c in r['anomalies']] + out['class_anomalies']:
        codes[code] = codes.get(code, 0) + 1
    out['codes'] = dict(sorted(codes.items()))  # {code: nb de places concernées}
    return out


def anomaly_summary_lines(codes: dict) -> list:
    """« E5 × 11 — Doublon : … » pour chaque code présent (infobulle classe)."""
    return [f"{code} × {n} — {ANOMALY_HELP.get(code, code)}" for code, n in codes.items()]


# ── Actions sur une place (étape 4) ──────────────────────────────────────

INSCRIRE = 'inscrire'
MODIFIER = 'modifier'
PRESENCE = 'presence'
REINITIALISER = 'reinitialiser'

ACTION_LABELS = {
    INSCRIRE: "Inscrire un élève",
    MODIFIER: "Modifier nom / prénom",
    PRESENCE: "Marquer présent / absent",
    REINITIALISER: "Réinitialiser la place",
}

# Qui fait normalement l'inscription, rappelé dans la confirmation
SECRETARY_NOTE = {
    'maternelle': "Normalement fait par la secrétaire de la maternelle.",
    'primaire': "Normalement fait par la secrétaire du primaire.",
    'college_lycee': "Normalement fait par le secrétariat.",
}

FIRST_NAME_MAX = 30   # larcauth_aecuser.first_name varchar(30)
LAST_NAME_MAX = 150   # larcauth_aecuser.last_name varchar(150)


def placeholder_names(student_id: int) -> tuple:
    """Nom gabarit standard d'une place libre : (nom, prénom)."""
    return (f"Name of {student_id}", f"First_name of {student_id}")


def clean_names(last_name, first_name) -> tuple:
    """Normalise et valide un nom saisi : (NOM, Prénom).

    Convention relevée en base (2026-10-26, élèves actifs) : nom en
    MAJUSCULES (388/392), prénom avec initiales en capitales (392/392).
    Lève ValueError (message prêt à afficher) si vide, trop long ou gabarit."""
    last = ' '.join((last_name or '').split()).upper()
    first = ' '.join((first_name or '').split())
    if not last or not first:
        raise ValueError("Le nom et le prénom sont obligatoires.")
    if first == first.lower() or first == first.upper():
        first = first.title()  # « julia » / « JULIA » → « Julia » ; sinon tel que saisi
    if len(first) > FIRST_NAME_MAX:
        raise ValueError(f"Prénom trop long ({len(first)} caractères, maximum {FIRST_NAME_MAX}).")
    if len(last) > LAST_NAME_MAX:
        raise ValueError(f"Nom trop long ({len(last)} caractères, maximum {LAST_NAME_MAX}).")
    if is_placeholder(first, last):
        raise ValueError("Ce nom ressemble à un nom de place libre (gabarit) : saisir le vrai nom.")
    return last, first


def available_actions(row: dict) -> dict:
    """{action: (possible, raison)} pour une ligne de assess_slots.
    La raison sert d'infobulle (pourquoi c'est grisé, ou ce que ça fera)."""
    state, verdict = row['state'], row['verdict']
    actions = {}
    if state == LIBRE:
        actions[INSCRIRE] = (True, "Inscrire un élève sur cette place libre.")
    else:
        actions[INSCRIRE] = (False, "Place occupée : choisir une place libre.")
    if state == LIBRE:
        actions[MODIFIER] = (False, "Place libre : utiliser « Inscrire un élève ».")
    elif row.get('placeholder'):
        actions[MODIFIER] = (False, "Nom gabarit (E1) : réinitialiser la place puis inscrire l'élève.")
    else:
        actions[MODIFIER] = (True, "Corriger le nom ou le prénom.")
    if state == ACTIF:
        actions[PRESENCE] = (True, "Marquer l'élève absent (parti) : il quitte la classe.")
    elif state == PARTI:
        actions[PRESENCE] = (True, "Marquer l'élève présent à nouveau (sans réinscription aux matières).")
    else:
        actions[PRESENCE] = (False, "Place libre : rien à marquer.")
    if state == LIBRE:
        actions[REINITIALISER] = (False, "Place déjà libre.")
    elif verdict == INTERDIT:
        reason = ("Bulletin produit : la place ne peut pas être libérée."
                  if row.get('has_bulletin') else
                  "Notes ou bulletin impossibles à vérifier : réinitialisation bloquée par prudence.")
        actions[REINITIALISER] = (False, reason)
    elif verdict == ADMIN:
        actions[REINITIALISER] = (True, "Notes saisies : leur effacement demande une confirmation admin.")
    else:
        actions[REINITIALISER] = (True, "Libère la place (aucune note ni bulletin).")
    return actions
