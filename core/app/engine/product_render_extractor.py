"""
H.5.2 — Extracteur IR product_render.

Transforme un prompt utilisateur en `ProductRenderIntent` validé via :
  prompt utilisateur
    → construction d'un prompt LLM strict (JSON-only, enums explicites)
    → appel Ollama (ou callable injecté pour les tests)
    → parsing tolérant (JSON pur, bloc ```json, texte parasite)
    → validation Pydantic via ProductRenderIntent (H.5.1)
    → IR validée OU fallback déterministe valide
    → rapport ProductRenderExtractionResult

Cadré par ADR [[16_H5_PRODUCT_RENDER_IR_CADRAGE]] (Décision 11).

H.5.2 reste une BRIQUE ISOLÉE :
- pas de branchement dans `build_blender_script` (= H.5.3)
- pas de modification du builder H.5.1
- pas de modification du noyau router/planner/executor/openai_compat
- `/execute` continue d'utiliser le pipeline H.4.x intégral

Invariants par construction :
- une sortie LLM imparfaite ne crashe JAMAIS le système (fallback)
- l'IR retournée est toujours valide (typée `ProductRenderIntent`)
- le fallback est explicite (status="fallback" + error string + raw_response)
- pas d'import circulaire (pas de blender_client, pas de router/planner/executor)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Optional

from app.clients.ollama_client import generate_with_ollama
from app.engine.blender_model_config import get_blender_llm_model
from app.engine.llm_trajectory_log import log_trajectory
from app.engine.product_render_ir import (
    BackdropIR,
    NAMED_COLOR_PALETTE,
    ProductRenderIntent,
    ProductSubjectIR,
    SubjectKind,
    SubjectMaterial,
)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# H.6.1 — Source de vérité unique via blender_model_config. Évalué à l'import
# pour préserver l'API publique (DEFAULT_EXTRACTION_MODEL est ré-exporté et
# utilisé par les tests). Pour un override dynamique sans redémarrage, passer
# explicitement `model=get_blender_llm_model()` à l'appel.
DEFAULT_EXTRACTION_MODEL = get_blender_llm_model()

# Listes pour le prompt strict. Source de vérité = enums Pydantic.
_KIND_VALUES: tuple[str, ...] = (
    "bottle", "jar", "box", "tube", "cylinder", "sphere",
)
_MATERIAL_VALUES: tuple[str, ...] = (
    "matte", "glossy", "glass", "metallic",
)
# H.5.4 — Enums V1 (resynchronisés avec product_render_ir).
_SHAPE_VALUES: tuple[str, ...] = ("cylindrical", "rectangular", "rounded")
_CAP_VALUES: tuple[str, ...] = ("present", "absent")
_TRANSPARENCY_VALUES: tuple[str, ...] = ("opaque", "translucent", "glass")
_FRAMING_VALUES: tuple[str, ...] = ("close_packshot", "medium")
_PALETTE_NAMES: tuple[str, ...] = tuple(sorted(NAMED_COLOR_PALETTE.keys()))


# ---------------------------------------------------------------------------
# Fallback IR — instance figée, validée au chargement du module
# ---------------------------------------------------------------------------
# Cas canonique H.5.1 validé visuellement par l'utilisateur sur la probe
# `h51_builder_probe_bottle_amber_glass`. Si pour une raison X la
# construction de cet IR échoue (par exemple un changement cassant du schéma
# Pydantic V0), le module ne se chargera pas — c'est volontaire : la suite
# de tests le révélera immédiatement.

FALLBACK_INTENT: ProductRenderIntent = ProductRenderIntent(
    schema_version="v0",
    subject=ProductSubjectIR(kind="bottle", color="amber", material="glass"),
    backdrop=BackdropIR(color="neutral_gray"),
)


# ---------------------------------------------------------------------------
# Rapport d'extraction
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProductRenderExtractionResult:
    """
    Résultat d'une tentative d'extraction IR product_render.

    intent          : IR validée (parsed ou fallback). Toujours valide.
    status          : "parsed"   = JSON LLM valide + validation Pydantic OK
                      "fallback" = échec à n'importe quelle étape, intent = FALLBACK_INTENT
    raw_response    : string brute retournée par le LLM (None si appel impossible)
    extracted_json  : dict json décodé (None si extraction/décodage échoué)
    error           : message d'erreur explicatif (None si status="parsed")
    model           : nom du modèle Ollama utilisé (None si appel hors LLM, par exemple
                      via parse_product_render_intent_from_text)
    """
    intent: ProductRenderIntent
    status: str
    raw_response: Optional[str]
    extracted_json: Optional[dict]
    error: Optional[str]
    model: Optional[str]


# ---------------------------------------------------------------------------
# Prompt builder (pur)
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
Tu es un extracteur de paramètres produit pour un système de rendu 3D \
contrôlé.

Tu réponds UNIQUEMENT par un objet JSON valide. Aucun texte avant, aucun \
texte après, aucun commentaire, aucun bloc markdown ```. Juste l'objet \
JSON.

Le JSON doit avoir EXACTEMENT cette forme (schema_version v1) :

{{
  "schema_version": "v1",
  "subject": {{
    "kind": "<one of: {kinds}>",
    "color": "<named color OR #RRGGBB hex>",
    "material": "<one of: {materials}>",
    "shape": "<one of: {shapes}>",
    "cap": "<one of: {caps}>",
    "transparency": "<one of: {transparencies}>"
  }},
  "backdrop": {{
    "color": "<named color OR #RRGGBB hex>"
  }},
  "framing": "<one of: {framings}>"
}}

Valeurs autorisées pour `subject.kind` : {kinds}.
Valeurs autorisées pour `subject.material` : {materials}.
Valeurs autorisées pour `subject.shape` : {shapes}.
Valeurs autorisées pour `subject.cap` : {caps}.
Valeurs autorisées pour `subject.transparency` : {transparencies}.
Valeurs autorisées pour `framing` : {framings}.
Couleurs nommées autorisées : {palette}.
Tu peux aussi utiliser un code hex de la forme #RRGGBB (par exemple \
#a83232).

Indications V1 :
- `shape` décrit la silhouette : `cylindrical` pour un flacon cylindrique, \
`rectangular` pour un packaging carré, `rounded` pour un flacon arrondi.
- `cap` = `present` si l'objet a un bouchon visible (bouteille, flacon, \
spray), sinon `absent`.
- `transparency` = `glass` pour le verre, `translucent` pour un plastique \
diffusant, `opaque` sinon.
- `framing` = `close_packshot` si la demande mentionne packshot, rendu \
produit serré, plan rapproché ; sinon `medium`.

Choisis les valeurs qui correspondent le mieux à la demande utilisateur. \
Si la demande est ambiguë, choisis des valeurs simples qui restent dans \
ces listes. NE PAS inventer de valeur hors des listes ci-dessus.

Demande utilisateur :
{message}

Réponds UNIQUEMENT avec l'objet JSON.\
"""


def build_extraction_prompt(user_message: str) -> str:
    """
    Construit le prompt LLM strict pour l'extraction IR product_render V1.

    Pure : pas d'I/O. Le prompt liste explicitement les enums autorisés
    (V0 + V1) et la palette de couleurs, pour réduire le risque
    d'hallucination LLM hors-schéma.
    """
    return _PROMPT_TEMPLATE.format(
        kinds=", ".join(_KIND_VALUES),
        materials=", ".join(_MATERIAL_VALUES),
        shapes=", ".join(_SHAPE_VALUES),
        caps=", ".join(_CAP_VALUES),
        transparencies=", ".join(_TRANSPARENCY_VALUES),
        framings=", ".join(_FRAMING_VALUES),
        palette=", ".join(_PALETTE_NAMES),
        message=user_message or "",
    )


# ---------------------------------------------------------------------------
# Parsing tolérant (pur)
# ---------------------------------------------------------------------------

# Bloc markdown ```...``` éventuellement précédé d'un tag de langage
# (json, JSON, python, etc.). Capture le contenu, qu'on strip ensuite.
_MARKDOWN_FENCE_RE = re.compile(
    r"```[a-zA-Z]*\s*(.*?)```",
    re.DOTALL,
)


def _extract_balanced_braces(text: str) -> Optional[str]:
    """
    Extrait la première sous-chaîne `{...}` à accolades équilibrées.
    Ignore les accolades à l'intérieur de chaînes JSON. Retourne None si
    aucune paire équilibrée n'est trouvée.

    Pure : pas d'I/O.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _extract_json_block(text: str) -> Optional[str]:
    """
    Tente d'extraire un bloc JSON depuis une sortie LLM :
    1. Bloc markdown ```...``` (éventuellement tagué json/JSON/python)
    2. Premier `{...}` à accolades équilibrées
    Retourne None si aucune des deux stratégies ne trouve quoi que ce soit.

    Pure : pas d'I/O.
    """
    if not text:
        return None
    # 1. Bloc markdown
    m = _MARKDOWN_FENCE_RE.search(text)
    if m:
        inner = m.group(1).strip()
        # Le bloc peut lui-même contenir du texte parasite avant/après le JSON ;
        # on retombe sur la stratégie des accolades équilibrées dans ce cas.
        if inner.startswith("{") and inner.endswith("}"):
            return inner
        balanced = _extract_balanced_braces(inner)
        if balanced is not None:
            return balanced
        # Le bloc markdown ne contient pas de JSON exploitable → on continue.
    # 2. Accolades équilibrées sur le texte brut
    return _extract_balanced_braces(text)


def _fallback_result(
    raw_response: Optional[str],
    extracted_json: Optional[dict],
    error: str,
    model: Optional[str] = None,
) -> ProductRenderExtractionResult:
    """Constructeur uniforme pour le résultat fallback."""
    return ProductRenderExtractionResult(
        intent=FALLBACK_INTENT,
        status="fallback",
        raw_response=raw_response,
        extracted_json=extracted_json,
        error=error,
        model=model,
    )


def parse_product_render_intent_from_text(
    text: Optional[str],
    *,
    model: Optional[str] = None,
) -> ProductRenderExtractionResult:
    """
    Parse un texte (par exemple une réponse LLM) en `ProductRenderIntent`.

    Robuste à toute entrée :
    - None / "" / whitespace seul → fallback (error="empty_response")
    - texte sans aucun objet JSON → fallback (error="no_json_block_found")
    - JSON syntaxiquement invalide → fallback (error="json_decode_error: ...")
    - JSON valide mais pas un objet (list, scalaire) → fallback (error="json_not_object: ...")
    - JSON objet mais pas conforme à ProductRenderIntent → fallback (error="pydantic_validation_error: ...")
    - JSON valide ET conforme → status="parsed", intent rempli depuis le JSON

    Pure : pas d'I/O, pas d'appel LLM. Testable isolément.

    Le paramètre `model` est purement informatif (propagé au rapport) ;
    le parsing lui-même ne dépend pas du modèle.
    """
    if text is None or not text.strip():
        return _fallback_result(
            raw_response=text,
            extracted_json=None,
            error="empty_response",
            model=model,
        )

    json_str = _extract_json_block(text)
    if json_str is None:
        return _fallback_result(
            raw_response=text,
            extracted_json=None,
            error="no_json_block_found",
            model=model,
        )

    try:
        data = json.loads(json_str)
    except Exception as exc:
        return _fallback_result(
            raw_response=text,
            extracted_json=None,
            error=f"json_decode_error: {exc}",
            model=model,
        )

    if not isinstance(data, dict):
        return _fallback_result(
            raw_response=text,
            extracted_json=None,
            error=f"json_not_object: got {type(data).__name__}",
            model=model,
        )

    try:
        intent = ProductRenderIntent(**data)
    except Exception as exc:
        return _fallback_result(
            raw_response=text,
            extracted_json=data,
            error=f"pydantic_validation_error: {exc}",
            model=model,
        )

    return ProductRenderExtractionResult(
        intent=intent,
        status="parsed",
        raw_response=text,
        extracted_json=data,
        error=None,
        model=model,
    )


# ---------------------------------------------------------------------------
# Orchestrateur LLM
# ---------------------------------------------------------------------------

def extract_product_render_intent(
    message: str,
    model: str = DEFAULT_EXTRACTION_MODEL,
    *,
    generate_fn: Optional[Callable[[str, str], str]] = None,
) -> ProductRenderExtractionResult:
    """
    Pipeline complet H.5.2 : prompt → LLM → parsing → validation → IR ou fallback.

    Arguments
    ---------
    message     : prompt utilisateur original (ex. "bouteille de parfum ambrée sur fond gris").
    model       : nom du modèle Ollama. Défaut `qwen2.5-coder:7b` (cohérent avec blender_client.py).
    generate_fn : callable `(model, prompt) -> str` à utiliser pour l'appel LLM. Si None,
                  utilise `app.clients.ollama_client.generate_with_ollama`.
                  Injecté pour permettre des tests sans dépendance Ollama réelle.

    Returns
    -------
    ProductRenderExtractionResult avec status="parsed" ou "fallback" et un `intent`
    toujours valide (typé `ProductRenderIntent`).

    Garanties :
    - Ne lève JAMAIS d'exception, quelle que soit la sortie LLM.
    - Si l'appel LLM échoue (timeout, réseau, exception), status="fallback"
      et `error="llm_call_error: ..."`.
    - Si la sortie LLM est vide ou inexploitable, status="fallback".
    """
    if generate_fn is None:
        generate_fn = generate_with_ollama

    prompt = build_extraction_prompt(message)

    try:
        raw = generate_fn(model, prompt)
    except Exception as exc:
        result = _fallback_result(
            raw_response=None,
            extracted_json=None,
            error=f"llm_call_error: {type(exc).__name__}: {exc}",
            model=model,
        )
        # H.6.1 — capture passive de la trajectoire (non-bloquante).
        log_trajectory(
            stage="extractor",
            model=model,
            prompt=prompt,
            raw_response=None,
            parse_ok=False,
            ir=None,
            fallback=True,
            error=result.error,
        )
        return result

    result = parse_product_render_intent_from_text(raw, model=model)

    # H.6.1 — capture passive de la trajectoire (non-bloquante).
    log_trajectory(
        stage="extractor",
        model=model,
        prompt=prompt,
        raw_response=raw,
        parse_ok=(result.status == "parsed"),
        ir=result.intent.model_dump() if result.intent is not None else None,
        fallback=(result.status == "fallback"),
        error=result.error,
    )
    return result
