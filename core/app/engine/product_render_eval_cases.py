"""
H.6.2 — Corpus de cas canoniques pour l'eval harness Product Render IR.

Petit corpus versionné (~10 prompts) permettant de mesurer objectivement la
qualité d'extraction d'un modèle LLM sur la tâche `extract_product_render_intent`.

Principes de conception :

- **Expectations partielles** : chaque cas ne déclare que les champs
  *non-ambigus* dans le prompt utilisateur. Un prompt qui ne mentionne pas
  le bouchon ne contraint pas `subject.cap`. Cela évite de pénaliser le
  modèle pour des champs où le prompt ne donne pas de signal.
- **V0 ∪ V1** : 5 cas V0 (champs minimaux) + 5 cas V1 (avec shape / cap /
  transparency / framing si signal explicite dans le prompt).
- **Couleurs validées au chargement** : tout token couleur attendu passe
  par `_validate_color_token` à l'import → un cas malformé fait échouer
  le module immédiatement, signal clair.
- **Pas de défauts implicites** : si un prompt V1 ne précise pas le
  cadrage, le cas ne contraint pas `framing`. Les défauts sont la
  responsabilité du builder, pas du LLM.
- **Lecture seule sur l'IR** : aucun import depuis le builder ni
  l'extractor. Le module est utilisable de manière statique.

Stabilité :
- L'ordre et les ids sont stables. Ajouts en fin de liste uniquement.
- Toute modification d'un cas existant doit être justifiée (= recalibrage
  intentionnel, pas correction silencieuse pour faire passer un modèle).

Format `expected` : dict avec un sous-ensemble libre des clés suivantes :
- "schema_version"     : "v0" | "v1"
- "subject.kind"       : SubjectKind
- "subject.color"      : token couleur (validé)
- "subject.material"   : SubjectMaterial
- "subject.shape"      : SubjectShape         (V1 only)
- "subject.cap"        : SubjectCap           (V1 only)
- "subject.transparency": SubjectTransparency (V1 only)
- "backdrop.color"     : token couleur (validé)
- "framing"            : Framing              (V1 only)

Aucune clé non listée n'est tolérée (validation à l'import).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.engine.product_render_ir import _validate_color_token


# ---------------------------------------------------------------------------
# Schéma autorisé des `expected`
# ---------------------------------------------------------------------------

ALLOWED_EXPECTED_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "subject.kind",
    "subject.color",
    "subject.material",
    "subject.shape",
    "subject.cap",
    "subject.transparency",
    "backdrop.color",
    "framing",
    # semantic_fidelity_v1. `subject.label` est volontairement exclu :
    # texte libre, l'exact-match du harness serait un faux signal.
    "subject.kind_fidelity",
    "pedestal.color",
    "pedestal.material",
})

# Clés "couleur" qui doivent passer par _validate_color_token à l'import.
_COLOR_KEYS: frozenset[str] = frozenset(
    {"subject.color", "backdrop.color", "pedestal.color"}
)


# ---------------------------------------------------------------------------
# Dataclass EvalCase
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvalCase:
    """
    Un cas d'évaluation = prompt utilisateur + sortie IR attendue partielle.

    id       : slug stable, kebab-case, unique sur tout le corpus.
    prompt   : texte exact passé à l'extractor.
    expected : sous-ensemble strict de `ALLOWED_EXPECTED_KEYS`. Chaque clé
               présente sera scorée par le harness ; chaque clé absente
               est ignorée (pas d'attente => pas de pénalité).
    notes    : commentaire libre, non utilisé par le scoring.
    """

    id: str
    prompt: str
    expected: Mapping[str, Any]
    notes: str = ""

    def __post_init__(self) -> None:
        # Validation stricte des clés.
        unknown = set(self.expected) - ALLOWED_EXPECTED_KEYS
        if unknown:
            raise ValueError(
                f"EvalCase {self.id!r}: clés expected inconnues : "
                f"{sorted(unknown)} (autorisées : {sorted(ALLOWED_EXPECTED_KEYS)})"
            )
        # Validation des tokens couleur (fail-fast à l'import).
        for k in _COLOR_KEYS & self.expected.keys():
            v = self.expected[k]
            if not isinstance(v, str):
                raise ValueError(
                    f"EvalCase {self.id!r}: {k} doit être str, got {type(v).__name__}"
                )
            _validate_color_token(v)
        # Validation cohérence V0/V1 : un cas V0 ne doit pas attendre des
        # champs V1 (sinon il est intrinsèquement non-satisfiable).
        sv = self.expected.get("schema_version")
        if sv == "v0":
            v1_only = {
                "subject.shape", "subject.cap",
                "subject.transparency", "framing",
                "pedestal.color", "pedestal.material",
            }
            forbidden = v1_only & self.expected.keys()
            if forbidden:
                raise ValueError(
                    f"EvalCase {self.id!r}: schema_version='v0' interdit "
                    f"d'attendre des champs V1 : {sorted(forbidden)}"
                )


# ---------------------------------------------------------------------------
# Corpus canonique — DEFAULT_CASES
# ---------------------------------------------------------------------------
# 10 cas. Ne pas réordonner / ne pas modifier sans note explicite.

DEFAULT_CASES: tuple[EvalCase, ...] = (
    # --- V0 (5) ---
    EvalCase(
        id="v0-bottle-amber-glass-neutral-gray",
        prompt="bouteille de parfum ambrée en verre sur fond gris neutre",
        expected={
            "schema_version": "v0",
            "subject.kind": "bottle",
            "subject.color": "amber",
            "subject.material": "glass",
            "backdrop.color": "neutral_gray",
        },
        notes="Cas canonique H.5.1 (probe historique).",
    ),
    EvalCase(
        id="v0-jar-white-matte-beige",
        prompt="pot en céramique mat blanc sur fond beige",
        expected={
            "schema_version": "v0",
            "subject.kind": "jar",
            "subject.color": "white",
            "subject.material": "matte",
            "backdrop.color": "beige",
        },
    ),
    EvalCase(
        id="v0-box-red-glossy-black",
        prompt="boîte rouge brillante sur fond noir",
        expected={
            "schema_version": "v0",
            "subject.kind": "box",
            "subject.color": "red",
            "subject.material": "glossy",
            "backdrop.color": "black",
        },
    ),
    EvalCase(
        id="v0-tube-green-matte-white",
        prompt="tube vert mat sur fond blanc",
        expected={
            "schema_version": "v0",
            "subject.kind": "tube",
            "subject.color": "green",
            "subject.material": "matte",
            "backdrop.color": "white",
        },
    ),
    EvalCase(
        id="v0-sphere-metallic-cool-gray",
        prompt="sphère chromée métallique sur fond gris froid",
        expected={
            "schema_version": "v0",
            "subject.kind": "sphere",
            "subject.material": "metallic",
            "backdrop.color": "cool_gray",
        },
        notes="subject.color volontairement non contraint (chrome est ambigu).",
    ),
    # --- V1 (5) ---
    EvalCase(
        id="v1-bottle-rectangular-amber-glass-cap-closeup",
        prompt=(
            "packshot serré d'une bouteille de parfum rectangulaire ambrée "
            "en verre avec bouchon, sur fond gris neutre"
        ),
        expected={
            "schema_version": "v1",
            "subject.kind": "bottle",
            "subject.color": "amber",
            "subject.material": "glass",
            "subject.shape": "rectangular",
            "subject.cap": "present",
            "subject.transparency": "glass",
            "backdrop.color": "neutral_gray",
            "framing": "close_packshot",
        },
    ),
    EvalCase(
        id="v1-jar-rounded-white-translucent-beige",
        prompt=(
            "pot de crème rond translucide blanc sur fond beige, cadrage moyen"
        ),
        expected={
            "schema_version": "v1",
            "subject.kind": "jar",
            "subject.color": "white",
            "subject.shape": "rounded",
            "subject.transparency": "translucent",
            "backdrop.color": "beige",
            "framing": "medium",
        },
        notes="material non contraint (un pot translucide peut être matte ou glossy).",
    ),
    EvalCase(
        id="v1-bottle-blue-glass-closeup-white",
        prompt=(
            "bouteille en verre transparente bleue, cadrage rapproché, fond blanc"
        ),
        expected={
            "schema_version": "v1",
            "subject.kind": "bottle",
            "subject.color": "blue",
            "subject.material": "glass",
            "subject.transparency": "glass",
            "backdrop.color": "white",
            "framing": "close_packshot",
        },
    ),
    EvalCase(
        id="v1-tube-cylindrical-red-opaque-warm-gray",
        prompt=(
            "tube de cosmétique cylindrique opaque rouge sur fond gris chaud"
        ),
        expected={
            "schema_version": "v1",
            "subject.kind": "tube",
            "subject.color": "red",
            "subject.shape": "cylindrical",
            "subject.transparency": "opaque",
            "backdrop.color": "warm_gray",
        },
        notes="material et framing non contraints.",
    ),
    EvalCase(
        id="v1-box-rectangular-black-matte-cap-beige",
        prompt=(
            "boîte carrée mate noire avec couvercle sur fond beige"
        ),
        expected={
            "schema_version": "v1",
            "subject.kind": "box",
            "subject.color": "black",
            "subject.material": "matte",
            "subject.shape": "rectangular",
            "subject.cap": "present",
            "backdrop.color": "beige",
        },
    ),
    # --- Ajout semantic_fidelity_v1 (append-only, corpus historique intact) ---
    EvalCase(
        id="sf1-watch-metallic-stone-pedestal",
        prompt=(
            "packshot cinématographique : chronomètre en métal poli "
            "sur un socle en pierre"
        ),
        expected={
            "schema_version": "v1",
            "subject.kind": "watch",
            "subject.material": "metallic",
            "subject.kind_fidelity": "exact",
            "pedestal.color": "warm_gray",
            "pedestal.material": "matte",
            "framing": "close_packshot",
        },
        notes=(
            "Prompt du smoke 3 de l'audit 2026-06-10, qui dégradait en "
            "kind=box silencieusement. Vérifie le kind watch, la fidélité "
            "déclarée et l'extraction du socle."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Validation globale du corpus (fail-fast à l'import)
# ---------------------------------------------------------------------------

def _validate_corpus(cases: tuple[EvalCase, ...]) -> None:
    seen: set[str] = set()
    for c in cases:
        if c.id in seen:
            raise ValueError(f"corpus contient un id dupliqué : {c.id!r}")
        seen.add(c.id)
        if not c.prompt.strip():
            raise ValueError(f"EvalCase {c.id!r}: prompt vide")


_validate_corpus(DEFAULT_CASES)
