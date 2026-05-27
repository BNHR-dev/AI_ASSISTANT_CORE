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

Choix entre v0 et v1 :
- Utilise `schema_version="v0"` si la demande ne mentionne AUCUN des \
champs V1 (silhouette, bouchon, transparence, cadrage). Dans ce cas, \
OMETS complètement les clés `shape`, `cap`, `transparency`, `framing` \
du JSON (ne les mets pas à null).
- Utilise `schema_version="v1"` UNIQUEMENT si tu remplis effectivement \
au moins un de ces champs V1.

Distinction CRITIQUE material vs transparency :
- `material` est l'aspect de surface : matte, glossy, glass, metallic.
- `transparency` est le profil de transmission : opaque, translucent, glass.
- `opaque` et `translucent` ne sont PAS des valeurs de `material`. \
Ne les mets JAMAIS dans `material`.

Choix entre couleur nommée et hex :
- Préfère la couleur nommée si elle existe dans la palette. \
Par exemple écris `white` plutôt que `#ffffff`, `beige` plutôt que \
`#f5deb3`.
- Utilise un hex `#RRGGBB` SEULEMENT si aucune couleur nommée ne \
correspond raisonnablement.

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


# ---------------------------------------------------------------------------
# H.6.4 — Normalizers déterministes post-parse / pré-Pydantic
# ---------------------------------------------------------------------------
# Cible : corriger trois biais récurrents observés sur qwen2.5-coder:7b lors
# du premier benchmark réel H.6.3, sans changer le modèle ni l'IR. Toutes
# les fonctions sont pures, idempotentes, et appliquées en pipeline AVANT
# que Pydantic valide. Le `extracted_json` retourné dans le résultat reste
# la donnée brute (pré-normalisation) pour traçabilité.

# Synonymes hex → palette nommée. Choix conservateur : on ne mappe que des
# hex CSS communs sans ambiguïté. Pas de distance euclidienne (trop fragile,
# risque de faux positifs). Extensible au cas par cas avec preuve via le
# rapport eval.
_HEX_TO_PALETTE_SYNONYMS: dict[str, str] = {
    "#ffffff": "white",
    "#fff":    "white",
    "#000000": "black",
    "#000":    "black",
    "#ff0000": "red",
    "#f00":    "red",
    "#00ff00": "green",
    "#0f0":    "green",
    "#0000ff": "blue",
    "#0000ff": "blue",
    "#00f":    "blue",
    "#ffff00": "yellow",
    "#ff0":    "yellow",
    "#ffa500": "orange",        # CSS orange
    "#ffc0cb": "pink",          # CSS pink
    "#a52a2a": "brown",         # CSS brown
    "#f5f5dc": "beige",         # CSS beige
    "#f5deb3": "beige",         # CSS wheat — observé H.6.3 dans v1-jar
    "#808080": "neutral_gray",
    "#888888": "neutral_gray",
    "#888":    "neutral_gray",
}

# Valeurs de l'enum SubjectTransparency qui ne sont *pas* aussi des valeurs
# de SubjectMaterial. Sur ces tokens, une présence dans le champ `material`
# du JSON LLM est forcément une erreur de placement (le modèle a confondu
# les deux). "glass" est volontairement exclu (token légal des deux côtés).
_TRANSPARENCY_ONLY: frozenset[str] = frozenset({"opaque", "translucent"})


def _normalize_color_hex_to_palette(value: object) -> object:
    """
    Si `value` est un hex CSS courant équivalent à un nom de la palette,
    retourne le nom. Sinon retourne `value` inchangé. Pure.
    """
    if not isinstance(value, str):
        return value
    key = value.strip().lower()
    return _HEX_TO_PALETTE_SYNONYMS.get(key, value)


def _normalize_colors(data: dict) -> dict:
    """
    Applique la normalisation hex→palette aux deux champs couleur de
    l'IR : subject.color et backdrop.color. N'altère pas le reste. Pure.
    """
    new_data = dict(data)
    subj = new_data.get("subject")
    if isinstance(subj, dict) and "color" in subj:
        new_subj = dict(subj)
        new_subj["color"] = _normalize_color_hex_to_palette(subj["color"])
        new_data["subject"] = new_subj
    backdrop = new_data.get("backdrop")
    if isinstance(backdrop, dict) and "color" in backdrop:
        new_backdrop = dict(backdrop)
        new_backdrop["color"] = _normalize_color_hex_to_palette(backdrop["color"])
        new_data["backdrop"] = new_backdrop
    return new_data


def _normalize_material_transparency(data: dict) -> dict:
    """
    Corrige les valeurs hors-enum dans `subject.material` qui appartiennent
    en réalité à l'enum SubjectTransparency (`opaque` ou `translucent`).

    Règle (révisée H.6.4 d'après l'observation du benchmark) : si
    `subject.material` ∈ {opaque, translucent}, la valeur est *illégale*
    pour material et provoquerait un `pydantic_validation_error`. La
    correction est donc systématique :

    - `material` est **toujours** remplacé par `"matte"` (défaut neutre)
      quand la valeur courante est dans `_TRANSPARENCY_ONLY`.
    - `transparency` :
        - si absent → hérite de la valeur précédente de `material` ;
        - si déjà set → préservé (ne dégrade pas l'information existante,
          même si elle diffère).

    Cas non touchés :
    - material valide (matte / glossy / glass / metallic) → inchangé ;
    - material = "glass" → légal des deux côtés, pas de confusion supposée ;
    - subject absent ou non-dict → inchangé.

    Pure, idempotent.
    """
    subj = data.get("subject")
    if not isinstance(subj, dict):
        return data
    material = subj.get("material")
    if material not in _TRANSPARENCY_ONLY:
        return data
    new_subj = dict(subj)
    if subj.get("transparency") is None:
        new_subj["transparency"] = material
    new_subj["material"] = "matte"
    return {**data, "subject": new_subj}


def _normalize_schema_version(data: dict) -> dict:
    """
    Si `schema_version="v1"` mais qu'aucun champ V1 explicite n'est fourni
    (`subject.shape`, `subject.cap`, `subject.transparency`, `framing` tous
    absents ou None), coerce à `"v0"` et purge les clés V1 None pour
    respecter le validateur de pureté V0.

    Évite la sur-promotion v1 observée systématiquement en H.6.3 sur les
    cas V0. Pure, idempotent.
    """
    if data.get("schema_version") != "v1":
        return data
    subj_in = data.get("subject", {})
    subj = subj_in if isinstance(subj_in, dict) else {}
    has_v1 = (
        subj.get("shape") is not None
        or subj.get("cap") is not None
        or subj.get("transparency") is not None
        or data.get("framing") is not None
    )
    if has_v1:
        return data
    # Aucun champ V1 effectif → recadrage v0. On retire les clés V1
    # explicitement positionnées à None (sinon le validator V0 reste
    # satisfait, mais on évite de transporter du None inutile).
    cleaned_subj = {
        k: v for k, v in subj.items()
        if k not in ("shape", "cap", "transparency")
    }
    new_data = dict(data)
    new_data["schema_version"] = "v0"
    new_data["subject"] = cleaned_subj
    new_data.pop("framing", None)
    return new_data


def _apply_normalizers(data: dict) -> dict:
    """
    Applique l'ensemble des normalizers dans un ordre stable. Pure.

    Ordre :
    1. _normalize_colors                — fix hex→palette
    2. _normalize_material_transparency — fix swap enum
    3. _normalize_schema_version        — fix sur-promotion v1

    L'ordre 2 avant 3 est important : si on libère `transparency` à partir
    de `material`, le cas devient un vrai V1 et 3 ne déclenchera pas la
    coercition à v0.
    """
    data = _normalize_colors(data)
    data = _normalize_material_transparency(data)
    data = _normalize_schema_version(data)
    return data


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

    # H.6.4 — Normalisation déterministe avant Pydantic. `data` reste la
    # donnée brute pour traçabilité (`extracted_json`) ; `normalized` est
    # ce qui est effectivement validé.
    normalized = _apply_normalizers(data)

    try:
        intent = ProductRenderIntent(**normalized)
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
