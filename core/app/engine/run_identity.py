"""
run_identity.py — contrat canonique des identifiants de run (request_id).

Un request_id nomme un dossier sur disque (`outputs/runs/<request_id>/`,
`outputs/comfyui/repro/<request_id>/…`) : tout identifiant accepté sans
validation est une traversée de chemin potentielle. Avant ce module, la
Console portait sa propre regex mais l'API `/resume`, les checkpoints
(run_state), le journal d'événements (run_events) et le rejeu ComfyUI
acceptaient n'importe quelle chaîne.

Contrat UNIQUE, appliqué par toutes les surfaces :

    ^[A-Za-z0-9-]{1,64}$

- couvre les uuid4 générés par l'executor et la Console ;
- aucun séparateur de chemin (ni `/`, ni `\\`, ni `.`) : un id valide ne
  peut pas sortir de sa racine, quel que soit l'OS ;
- borné à 64 caractères (pas de noms de fichiers pathologiques).

`resolve_run_dir` ajoute la défense en profondeur : même un id valide doit
résoudre SOUS sa racine (symlinks suivis) pour être utilisé.

Stdlib uniquement ; importable par schemas (API), console et engine sans
dépendance circulaire.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

REQUEST_ID_PATTERN = r"^[A-Za-z0-9-]{1,64}$"
REQUEST_ID_RE = re.compile(REQUEST_ID_PATTERN)


def is_valid_request_id(value: object) -> bool:
    """L'objet est-il un request_id conforme au contrat canonique ?

    `fullmatch`, pas `match` : avec `$`, le module `re` accepte un `\\n`
    final ("req-1\\n" passerait) — fullmatch exige que TOUT soit consommé.
    """
    return isinstance(value, str) and bool(REQUEST_ID_RE.fullmatch(value))


def resolve_run_dir(base_dir: Path, request_id: str) -> Optional[Path]:
    """Dossier du run sous `base_dir`, ou None si l'id est invalide.

    Vérifie le contrat de charset PUIS que le chemin résolu (symlinks
    suivis) reste sous la racine — le charset rend la traversée impossible
    par construction, la résolution est la ceinture de sécurité.
    """
    if not is_valid_request_id(request_id):
        return None
    candidate = base_dir / request_id
    try:
        if not candidate.resolve().is_relative_to(base_dir.resolve()):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate
