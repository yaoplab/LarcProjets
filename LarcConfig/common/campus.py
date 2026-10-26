"""Campus de l'établissement — découpage du menu LarcConfig (pur Python).

Un campus regroupe des programmes et, éventuellement, une plage de niveaux
(``level_in_pgm``). Faits vérifiés en base (2026-10-26) :
id niveau = programme*10 + level_in_pgm ; id classe = niveau*10 + index
(…1 classe principale, …2 « - B »).
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Campus:
    key: str
    label: str
    program_ids: tuple[int, ...]
    levels: Optional[tuple[int, ...]] = None  # None = tous les niveaux


CAMPUSES = [
    # PYP (11) + PP (21) — PreK/PS, PreK2/MS, K/GS
    Campus('maternelle', 'Maternelle', (11, 21), (1, 2, 3)),
    # PYP (11) + PP (21) — PYP-1..5 / PP-1..5
    Campus('primaire', 'Primaire', (11, 21), (4, 5, 6, 7, 8)),
    # MYP (12), DPEn (13), PEI (22), DPFr (23)
    Campus('college_lycee', 'Collège & Lycée', (12, 13, 22, 23)),
]


def get_campus(key: str) -> Optional[Campus]:
    """Campus par clé (None si inconnue)."""
    return next((cp for cp in CAMPUSES if cp.key == key), None)


def campus_of_classroom(classroom_id: int) -> Optional[Campus]:
    """Campus d'une classe : programme = id // 100, niveau = (id // 10) % 10
    (id classe = (programme*10 + niveau)*10 + index)."""
    program_id, level = classroom_id // 100, (classroom_id // 10) % 10
    return next((cp for cp in CAMPUSES if program_id in cp.program_ids
                 and (cp.levels is None or level in cp.levels)), None)
