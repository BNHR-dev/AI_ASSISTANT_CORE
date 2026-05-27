"""
H.6.8.a — Tests d'équivalence stricte byte-to-byte pour `build_script_gen_prompt`.

Ce test prouve que l'extraction de `build_script_gen_prompt` à partir de
l'assemblage inline historiquement présent dans `build_blender_script`
produit STRICTEMENT le même prompt, pour toutes les combinaisons d'entrées
pertinentes.

Méthode :
- `_legacy_build_prompt_inline` reproduit MOT POUR MOT la logique pré-H.6.8.a.
- Pour chacune des 6 combinaisons (template scaffold ∈ {None,
  interior_space, product_render} × intent ∈ {None/vide, riche}), on
  asserte `legacy == new` byte-to-byte.

Si une seule de ces assertions casse, le refactor est invalide et doit
être annulé (cf. cadrage H.6.8.a §5 conditions strictes).

Aucun appel Ollama, aucun subprocess, aucune dépendance Blender. Test
PUR, exécutable hors VM.
"""
from __future__ import annotations

import pytest

from app.clients.blender_client import (
    _BLENDER_SYSTEM_PROMPT,
    _build_creative_guidance,
    _template_fidelity_block,
    build_script_gen_prompt,
)
from app.engine.blender_templates import (
    TEMPLATE_INTERIOR_SPACE,
    TEMPLATE_PRODUCT_RENDER,
)


# ---------------------------------------------------------------------------
# Reproduction fidèle du code legacy (pré-H.6.8.a)
# ---------------------------------------------------------------------------
# Ce bloc est une COPIE EXACTE de l'assemblage inline qui se trouvait dans
# `build_blender_script` avant H.6.8.a. Il sert UNIQUEMENT de référence pour
# le test d'équivalence. Toute modification de ce bloc invaliderait le test.
#
# Source d'origine : core/app/clients/blender_client.py, lignes ~428-452
# (commit pré-H.6.8.a).
# ---------------------------------------------------------------------------

def _legacy_build_prompt_inline(
    message: str,
    intent: object,
    template_scaffold: str | None,
    selected_template_name: str | None,
) -> str:
    """Reproduction byte-fidèle de l'assemblage inline pré-H.6.8.a."""
    if template_scaffold is not None:
        # H.4.3 — Creative guidance (Option C) : injectée UNIQUEMENT lorsqu'un
        # scaffold contrôlé est actif. Sur prompt libre (template_scaffold is
        # None) le prompt reste strictement identique à l'état H.4.2.
        guidance = _build_creative_guidance(intent)
        guidance_block = f"{guidance}\n\n" if guidance else ""
        # H.4.3-C — Consignes de fidélité scaffold spécifiques au template.
        fidelity = _template_fidelity_block(selected_template_name)
        fidelity_block = f"{fidelity}\n\n" if fidelity else ""
        prompt = (
            f"{_BLENDER_SYSTEM_PROMPT}\n\n"
            f"--- SCAFFOLD DE SCÈNE OBLIGATOIRE ---\n"
            f"La demande correspond à un type de scène reconnu : {selected_template_name}.\n"
            f"Tu DOIS utiliser le scaffold suivant comme base de ton script.\n"
            f"Tu peux adapter les dimensions, matériaux, objets secondaires et noms selon la demande.\n"
            f"Tu NE DOIS PAS supprimer : la caméra active, la lumière Key_Light, le sol, le sujet principal, "
            f"la sauvegarde via OUTPUT_BLEND_PATH.\n\n"
            f"{template_scaffold}\n"
            f"--- FIN SCAFFOLD ---\n\n"
            f"{fidelity_block}"
            f"{guidance_block}"
            f"Demande utilisateur : {message}"
        )
    else:
        prompt = f"{_BLENDER_SYSTEM_PROMPT}\n\nDemande utilisateur : {message}"
    return prompt


# ---------------------------------------------------------------------------
# Fixtures intent
# ---------------------------------------------------------------------------

# Intent vide (équivalent à parse_artistic_intent sur prompt sans signal) :
# - style/mood listes vides ou ['unknown']
# - composition_lighting vide ou 'unknown'
# → _build_creative_guidance retourne "" → guidance_block = ""
_INTENT_EMPTY_DICT: dict = {
    "style": [],
    "mood": [],
    "composition_lighting": "",
}

# Intent riche : tous les champs autorisés peuplés.
# → _build_creative_guidance retourne un bloc non vide.
_INTENT_RICH_DICT: dict = {
    "style": ["cinematic", "moody"],
    "mood": ["nostalgic"],
    "composition_lighting": "low-key rim light",
}

# Intent partiel : uniquement style.
_INTENT_PARTIAL_DICT: dict = {
    "style": ["minimalist"],
    "mood": [],
    "composition_lighting": "",
}


# ---------------------------------------------------------------------------
# Combinaisons paramétrées
# ---------------------------------------------------------------------------

# Format : (id_lisible, message, intent, template_scaffold, template_name)
_COMBINATIONS = [
    # 1. Pas de template, pas d'intent → prompt minimal.
    (
        "no_template_no_intent",
        "Crée une scène avec un cube rouge",
        None,
        None,
        None,
    ),
    # 2. Pas de template, intent riche → l'intent ne doit PAS être consulté
    #    (rétrocompat H.4.2 stricte).
    (
        "no_template_rich_intent",
        "Crée une scène avec un cube rouge",
        _INTENT_RICH_DICT,
        None,
        None,
    ),
    # 3. Template interior_space, pas d'intent → scaffold + fidélité, pas de guidance.
    (
        "interior_space_no_intent",
        "Crée un salon moderne",
        None,
        TEMPLATE_INTERIOR_SPACE,
        "interior_space",
    ),
    # 4. Template interior_space + intent riche → scaffold + fidélité + guidance.
    (
        "interior_space_rich_intent",
        "Crée un salon moderne",
        _INTENT_RICH_DICT,
        TEMPLATE_INTERIOR_SPACE,
        "interior_space",
    ),
    # 5. Template product_render, pas d'intent → scaffold + fidélité, pas de guidance.
    #    (Combinaison rarement empruntée en runtime depuis H.5.3, mais le code
    #     legacy la supportait, donc on la vérifie.)
    (
        "product_render_no_intent",
        "Bouteille ambrée sur fond neutre",
        None,
        TEMPLATE_PRODUCT_RENDER,
        "product_render",
    ),
    # 6. Template product_render + intent partiel → scaffold + fidélité + guidance partielle.
    (
        "product_render_partial_intent",
        "Bouteille ambrée sur fond neutre",
        _INTENT_PARTIAL_DICT,
        TEMPLATE_PRODUCT_RENDER,
        "product_render",
    ),
]


@pytest.mark.parametrize(
    "case_id,message,intent,template_scaffold,template_name",
    _COMBINATIONS,
    ids=[c[0] for c in _COMBINATIONS],
)
def test_build_script_gen_prompt_byte_equivalent_to_legacy(
    case_id: str,
    message: str,
    intent: object,
    template_scaffold: str | None,
    template_name: str | None,
) -> None:
    """
    Assertion byte-to-byte stricte : l'extraction ne change PAS le prompt
    pour aucune des 6 combinaisons couvertes.
    """
    legacy = _legacy_build_prompt_inline(
        message=message,
        intent=intent,
        template_scaffold=template_scaffold,
        selected_template_name=template_name,
    )
    new = build_script_gen_prompt(
        message=message,
        intent=intent,
        template_scaffold=template_scaffold,
        template_name=template_name,
    )
    # Byte-equality stricte. Pas de strip, pas de normalisation.
    assert new == legacy, (
        f"Refactor H.6.8.a CASSE l'équivalence sur le cas '{case_id}'. "
        f"Le prompt produit diffère du legacy.\n"
        f"--- LEGACY (len={len(legacy)}) ---\n{legacy!r}\n"
        f"--- NEW (len={len(new)}) ---\n{new!r}\n"
    )


def test_build_script_gen_prompt_is_pure_no_argument_mutation() -> None:
    """
    Vérifie qu'aucun argument n'est muté par l'appel (fonction pure).
    """
    intent_before = dict(_INTENT_RICH_DICT)
    snapshot = dict(intent_before)

    _ = build_script_gen_prompt(
        message="msg",
        intent=intent_before,
        template_scaffold=TEMPLATE_INTERIOR_SPACE,
        template_name="interior_space",
    )

    assert intent_before == snapshot, (
        "build_script_gen_prompt a muté son argument intent"
    )


def test_build_script_gen_prompt_no_template_ignores_intent() -> None:
    """
    Documentation explicite : sans template_scaffold, l'intent n'a aucun
    impact sur le prompt (rétrocompat H.4.2 stricte).
    """
    msg = "prompt libre quelconque"

    p_no_intent = build_script_gen_prompt(
        message=msg,
        intent=None,
        template_scaffold=None,
        template_name=None,
    )
    p_empty_intent = build_script_gen_prompt(
        message=msg,
        intent=_INTENT_EMPTY_DICT,
        template_scaffold=None,
        template_name=None,
    )
    p_rich_intent = build_script_gen_prompt(
        message=msg,
        intent=_INTENT_RICH_DICT,
        template_scaffold=None,
        template_name=None,
    )

    assert p_no_intent == p_empty_intent == p_rich_intent


def test_build_script_gen_prompt_with_template_includes_scaffold_and_name() -> None:
    """
    Smoke test : avec template, le prompt contient bien le scaffold et le
    nom du template. Cible la régression la plus probable d'un refactor
    foireux (oubli d'un f-string ou inversion d'argument).
    """
    prompt = build_script_gen_prompt(
        message="canapé moderne",
        intent=None,
        template_scaffold=TEMPLATE_INTERIOR_SPACE,
        template_name="interior_space",
    )
    assert "--- SCAFFOLD DE SCÈNE OBLIGATOIRE ---" in prompt
    assert "interior_space" in prompt
    assert TEMPLATE_INTERIOR_SPACE in prompt
    assert "--- FIN SCAFFOLD ---" in prompt
    assert "Demande utilisateur : canapé moderne" in prompt
