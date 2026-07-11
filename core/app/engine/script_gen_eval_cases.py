"""
H.6.8.a — Corpus de cas canoniques pour l'eval harness `script_gen`.

Petit corpus versionné (5 prompts) permettant de mesurer objectivement la
qualité du `scene.py` produit par le LLM via `build_script_gen_prompt`
(extrait en H.6.8.a depuis `build_blender_script`).

Le corpus mesure le SECOND site LLM critique de la pipeline Blender (le
premier étant l'extractor Product Render IR mesuré en H.6.x via
`product_render_eval_cases`).

Principes de conception (alignés sur H.6.2 extractor) :

- **Pas de cas product_render fallback** : ce chemin dépend d'un échec
  de l'extractor IR qui peut évoluer (mean_score H.6.6 = 0.943, monte
  encore). Un cas qui dépend d'un fail n'est pas reproductible dans le
  temps. Le corpus H.6.8.a couvre donc UNIQUEMENT les chemins
  `script_gen` LLM stables : freeform et template `interior_space`.
- **5 cas max en H.6.8.a** : 2 freeform + 2 interior_space + 1 ambigu
  non-template (intérieur non détecté).
- **Applicable checks explicites par cas** : seul un sous-ensemble des
  checks AST guard fait sens par cas. Le scoring ne pénalise pas un
  cas pour un check qui ne s'applique pas (cf. H.6.2 expected partiels).
- **Expectations descriptives, pas prescriptives** : aucun seuil
  pass/fail au niveau du cas. Le rapport reste descriptif (cadrage
  H.6.8.a §7 décision D7).
- **Pas de défauts implicites** : un cas freeform ne contraint pas le
  nom des objets ; l'application de `template_required_objects_named`
  est explicitement listée par cas.
- **Lecture seule sur l'AST guard et les templates** : aucun import qui
  pourrait introduire un coupling runtime.

Stabilité :
- L'ordre et les ids sont stables. Ajouts en fin de liste uniquement.
- Toute modification d'un cas existant doit être justifiée explicitement
  (= recalibrage intentionnel, pas correction silencieuse pour faire
  passer un modèle).

Format `expected` : dict avec un sous-ensemble libre des clés suivantes :
- "template"                  : "interior_space" | None
- "must_name_objects"         : list[str] (sous-ensemble de TEMPLATE_SPECS
                                  required_objects ; vide si pas de template)
- "must_not_use_external_assets": bool (toujours True dans H.6.8.a)
- "applicable_checks"         : list[str] (cf. CHECK_* constantes)

Aucune clé non listée n'est tolérée (validation à l'import).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.engine.blender_templates import TEMPLATE_SPECS


# ---------------------------------------------------------------------------
# Constantes de check (noms canoniques côté harness)
# ---------------------------------------------------------------------------
# Sont préfixés / nommés indépendamment des constantes V_* de
# `blender_ast_guard.py` pour éviter tout couplage de naming (le harness
# peut composer plusieurs checks pour produire un check canonique).
# La correspondance check_canonique → violation AST guard est faite dans
# `script_gen_eval_harness.py`.

CHECK_GENERATION_OK              = "generation_ok"
CHECK_PYTHON_EXTRACTED           = "python_extracted"
CHECK_AST_PARSEABLE              = "ast_parseable"
CHECK_NO_EXTERNAL_ASSETS         = "no_external_assets"
CHECK_NO_PLACEHOLDER_PATHS       = "no_placeholder_paths"
CHECK_MESHES_NEW_HAS_GEOMETRY    = "meshes_new_has_geometry"
CHECK_HAS_PRIMITIVE_GEOMETRY     = "has_primitive_geometry"
CHECK_SCRIPT_MIN_SIZE            = "script_min_size"
CHECK_ACTIVE_CAMERA_ASSIGNED     = "active_camera_assigned"
CHECK_DELETE_DEFAULT_PRESENT     = "delete_default_present"
CHECK_NOT_FALLBACK_CUBE_SUN_ONLY = "not_fallback_cube_sun_only"
CHECK_TEMPLATE_REQUIRED_OBJECTS  = "template_required_objects_named"
CHECK_TEMPLATE_FORBIDDEN_PREFIX  = "template_forbidden_prefix_absent"


# Ensemble exhaustif des checks reconnus. Toute valeur dans
# `applicable_checks` d'un cas DOIT appartenir à cet ensemble.
ALL_CHECKS: frozenset[str] = frozenset({
    CHECK_GENERATION_OK,
    CHECK_PYTHON_EXTRACTED,
    CHECK_AST_PARSEABLE,
    CHECK_NO_EXTERNAL_ASSETS,
    CHECK_NO_PLACEHOLDER_PATHS,
    CHECK_MESHES_NEW_HAS_GEOMETRY,
    CHECK_HAS_PRIMITIVE_GEOMETRY,
    CHECK_SCRIPT_MIN_SIZE,
    CHECK_ACTIVE_CAMERA_ASSIGNED,
    CHECK_DELETE_DEFAULT_PRESENT,
    CHECK_NOT_FALLBACK_CUBE_SUN_ONLY,
    CHECK_TEMPLATE_REQUIRED_OBJECTS,
    CHECK_TEMPLATE_FORBIDDEN_PREFIX,
})


# Checks "base" applicables à tout cas (freeform ou template).
_BASE_APPLICABLE_CHECKS: tuple[str, ...] = (
    CHECK_GENERATION_OK,
    CHECK_PYTHON_EXTRACTED,
    CHECK_AST_PARSEABLE,
    CHECK_NO_EXTERNAL_ASSETS,
    CHECK_NO_PLACEHOLDER_PATHS,
    CHECK_MESHES_NEW_HAS_GEOMETRY,
    CHECK_HAS_PRIMITIVE_GEOMETRY,
    CHECK_SCRIPT_MIN_SIZE,
    CHECK_ACTIVE_CAMERA_ASSIGNED,
    CHECK_DELETE_DEFAULT_PRESENT,
    CHECK_NOT_FALLBACK_CUBE_SUN_ONLY,
)


# ---------------------------------------------------------------------------
# Schéma autorisé des `expected`
# ---------------------------------------------------------------------------

ALLOWED_EXPECTED_KEYS: frozenset[str] = frozenset({
    "template",
    "must_name_objects",
    "must_not_use_external_assets",
    "applicable_checks",
})


# ---------------------------------------------------------------------------
# Dataclass ScriptGenCase
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScriptGenCase:
    """
    Un cas d'évaluation `script_gen` = prompt utilisateur + expectations
    partielles + liste explicite des checks applicables.

    id              : slug stable, kebab-case, unique sur tout le corpus.
    prompt          : texte exact passé à build_script_gen_prompt + Ollama.
    expected        : dict avec sous-ensemble de ALLOWED_EXPECTED_KEYS.
    category        : "freeform" | "interior_space" | "ambiguous"
                      (catégorie documentaire, n'influence pas le scoring).
    rationale       : phrase courte expliquant ce que le cas mesure.
    """
    id: str
    prompt: str
    expected: Mapping[str, Any]
    category: str
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError(f"ScriptGenCase: id invalide: {self.id!r}")
        if not self.prompt or not isinstance(self.prompt, str):
            raise ValueError(
                f"ScriptGenCase {self.id!r}: prompt invalide"
            )
        if self.category not in {"freeform", "interior_space", "ambiguous"}:
            raise ValueError(
                f"ScriptGenCase {self.id!r}: category invalide: {self.category!r}"
            )
        unknown = set(self.expected.keys()) - ALLOWED_EXPECTED_KEYS
        if unknown:
            raise ValueError(
                f"ScriptGenCase {self.id!r}: clés expected non autorisées: "
                f"{sorted(unknown)}"
            )

        # Validation 'template'
        template = self.expected.get("template")
        if template not in (None, "interior_space"):
            # H.6.8.a n'autorise PAS product_render dans le corpus
            # (cf. cadrage : pas de cas product_render fallback).
            raise ValueError(
                f"ScriptGenCase {self.id!r}: template invalide en H.6.8.a: "
                f"{template!r}. Autorisés: None, 'interior_space'."
            )

        # Validation 'must_name_objects'
        must_name = self.expected.get("must_name_objects", [])
        if not isinstance(must_name, (list, tuple)):
            raise ValueError(
                f"ScriptGenCase {self.id!r}: must_name_objects doit être list"
            )
        if template is None and must_name:
            raise ValueError(
                f"ScriptGenCase {self.id!r}: must_name_objects doit être vide "
                f"quand template=None"
            )
        if template == "interior_space":
            spec_required = set(TEMPLATE_SPECS["interior_space"]["required_objects"])
            extra = set(must_name) - spec_required
            if extra:
                raise ValueError(
                    f"ScriptGenCase {self.id!r}: must_name_objects contient "
                    f"des noms hors TEMPLATE_SPECS[interior_space]: "
                    f"{sorted(extra)}"
                )

        # Validation 'applicable_checks'
        applicable = self.expected.get("applicable_checks", [])
        if not isinstance(applicable, (list, tuple)):
            raise ValueError(
                f"ScriptGenCase {self.id!r}: applicable_checks doit être list"
            )
        if not applicable:
            raise ValueError(
                f"ScriptGenCase {self.id!r}: applicable_checks ne peut pas "
                f"être vide"
            )
        unknown_checks = set(applicable) - ALL_CHECKS
        if unknown_checks:
            raise ValueError(
                f"ScriptGenCase {self.id!r}: checks inconnus dans "
                f"applicable_checks: {sorted(unknown_checks)}"
            )
        # Cohérence template ↔ checks template
        if template is None and CHECK_TEMPLATE_REQUIRED_OBJECTS in applicable:
            raise ValueError(
                f"ScriptGenCase {self.id!r}: check "
                f"{CHECK_TEMPLATE_REQUIRED_OBJECTS} requis mais template=None"
            )


# ---------------------------------------------------------------------------
# Corpus H.6.8.a — 5 cas
# ---------------------------------------------------------------------------
# Répartition validée :
#   2 freeform  : C1 + C2
#   2 interior  : C3 + C4
#   1 ambigu    : C5  (intérieur descriptif non détecté → template=None)
#
# IMPORTANT : aucun cas product_render. Ce chemin dépend d'un fail IR
# évolutif et n'est pas reproductible dans le temps (cf. cadrage §1).
# ---------------------------------------------------------------------------

DEFAULT_CASES: tuple[ScriptGenCase, ...] = (
    # ---- 2 cas freeform ----
    ScriptGenCase(
        id="freeform_metal_sphere_floating",
        prompt=(
            "Crée une sphère métallique flottant au-dessus d'un cube rouge "
            "mat, avec une caméra en plongée."
        ),
        category="freeform",
        rationale=(
            "Base freeform 2 primitives + caméra : mesure le minimum vital "
            "LLM sans template ni scaffold."
        ),
        expected={
            "template": None,
            "must_name_objects": [],
            "must_not_use_external_assets": True,
            "applicable_checks": list(_BASE_APPLICABLE_CHECKS),
        },
    ),
    ScriptGenCase(
        id="freeform_low_poly_tree",
        prompt=(
            "Génère un arbre low-poly simple avec un tronc cylindrique brun "
            "et un feuillage en icosphère verte."
        ),
        category="freeform",
        rationale=(
            "Second cas freeform avec structure et matériaux nommés. Évite "
            "de mesurer un seul archétype 'sphère + cube' en freeform."
        ),
        expected={
            "template": None,
            "must_name_objects": [],
            "must_not_use_external_assets": True,
            "applicable_checks": list(_BASE_APPLICABLE_CHECKS),
        },
    ),

    # ---- 2 cas interior_space ----
    ScriptGenCase(
        id="interior_salon_moderne",
        prompt=(
            "Crée une scène intérieure d'un salon moderne avec un canapé "
            "et une table basse."
        ),
        category="interior_space",
        rationale=(
            "Premier cas template interior_space (salon + scène intérieure). "
            "Mesure la préservation des 7 objets obligatoires du scaffold."
        ),
        expected={
            "template": "interior_space",
            "must_name_objects": list(
                TEMPLATE_SPECS["interior_space"]["required_objects"]
            ),
            "must_not_use_external_assets": True,
            "applicable_checks": list(_BASE_APPLICABLE_CHECKS) + [
                CHECK_TEMPLATE_REQUIRED_OBJECTS,
            ],
        },
    ),
    ScriptGenCase(
        id="interior_cuisine_industrielle",
        prompt=(
            "Génère une cuisine industrielle avec un îlot central, une hotte "
            "aspirante et de grandes fenêtres."
        ),
        category="interior_space",
        rationale=(
            "Second cas interior_space avec prompt plus chargé (props "
            "additionnels). Mesure si le LLM ajoute du mobilier sans "
            "casser les 7 required_objects."
        ),
        expected={
            "template": "interior_space",
            "must_name_objects": list(
                TEMPLATE_SPECS["interior_space"]["required_objects"]
            ),
            "must_not_use_external_assets": True,
            "applicable_checks": list(_BASE_APPLICABLE_CHECKS) + [
                CHECK_TEMPLATE_REQUIRED_OBJECTS,
            ],
        },
    ),

    # ---- 1 cas ambigu non-template / intérieur non détecté ----
    ScriptGenCase(
        id="ambiguous_atelier_artiste",
        prompt=(
            "Atelier d'artiste avec un chevalet, des toiles posées contre "
            "le mur et lumière naturelle douce."
        ),
        category="ambiguous",
        rationale=(
            "Cas ambigu non-template / intérieur non détecté : 'atelier' et "
            "'mur' (singulier) ne sont dans aucun _INTERIOR_KEYWORDS. "
            "Mesure ce que le LLM produit sans scaffold quand le prompt "
            "évoque clairement une scène structurée. Pas un cas "
            "product_render fallback (cadrage §1)."
        ),
        expected={
            "template": None,
            "must_name_objects": [],
            "must_not_use_external_assets": True,
            "applicable_checks": list(_BASE_APPLICABLE_CHECKS),
        },
    ),
)


# ---------------------------------------------------------------------------
# Validation au chargement : unicité des IDs
# ---------------------------------------------------------------------------

def _validate_corpus(cases: tuple[ScriptGenCase, ...]) -> None:
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        seen: set[str] = set()
        dups: list[str] = []
        for cid in ids:
            if cid in seen:
                dups.append(cid)
            seen.add(cid)
        raise ValueError(f"Corpus script_gen: ids dupliqués: {sorted(set(dups))}")

    # Répartition attendue H.6.8.a : 2 freeform + 2 interior + 1 ambiguous
    categories = [c.category for c in cases]
    if cases is DEFAULT_CASES:
        expected_counts = {"freeform": 2, "interior_space": 2, "ambiguous": 1}
        for cat, expected in expected_counts.items():
            actual = categories.count(cat)
            if actual != expected:
                raise ValueError(
                    f"Corpus script_gen H.6.8.a: catégorie {cat!r} attendue "
                    f"{expected} fois, trouvée {actual} fois"
                )


_validate_corpus(DEFAULT_CASES)
