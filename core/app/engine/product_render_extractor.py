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
    SUBJECT_LABEL_MAX_LEN,
    V1_DEFAULTS,
    _validate_color_token,
)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# H.6.1 — Source de vérité unique via blender_model_config. Évalué à l'import
# pour préserver l'API publique (DEFAULT_EXTRACTION_MODEL est ré-exporté et
# utilisé par les tests). Pour un override dynamique sans redémarrage, passer
# explicitement `model=get_blender_llm_model()` à l'appel.
DEFAULT_EXTRACTION_MODEL = get_blender_llm_model()


# ---------------------------------------------------------------------------
# H.6.5.a — Paramètres d'inférence pour stabiliser l'extraction IR.
# ---------------------------------------------------------------------------
# La pipeline d'extraction n'a pas besoin de créativité : elle convertit un
# prompt utilisateur en une structure typée fermée. La variance run-to-run
# observée sur le benchmark H.6.4 (parse_ok_rate ∈ [0.7, 1.0]) est due
# essentiellement à la randomisation par défaut d'Ollama. On force ici un
# régime quasi-déterministe + format JSON serveur.
#
# - `temperature=0.0`  : pas de sampling stochastique.
# - `top_p=1.0`        : neutre quand temp=0 (présent pour explicitness).
# - `top_k=1`          : argmax explicite (certaines versions d'Ollama
#                        ignorent top_k à temp=0 ; redondant et sûr).
# - `seed=42`          : reproductibilité d'un run à l'autre.
# - `num_ctx=4096`     : marge contre toute troncation du prompt verbeux
#                        (palette + enums + scaffolding ≈ 1-2k tokens).
#
# `format="json"` force le serveur Ollama à émettre un JSON syntaxiquement
# valide (sans markdown ni commentaires). N'enforce pas le schéma de l'IR,
# mais élimine les modes "no_json_block_found" / "json_decode_error".
EXTRACTION_INFERENCE_OPTIONS: dict[str, object] = {
    "temperature": 0.0,
    "top_p": 1.0,
    "top_k": 1,
    "seed": 42,
    "num_ctx": 4096,
}
EXTRACTION_RESPONSE_FORMAT: str = "json"


def _default_extraction_generate_fn(model: str, prompt: str) -> str:
    """
    `generate_fn` par défaut de l'extracteur H.6.5.a : encapsule
    `generate_with_ollama` avec les paramètres d'inférence stabilisés et
    le format JSON serveur.

    Préserve la signature `(model, prompt) -> str` attendue par
    `extract_product_render_intent` et par les mocks de test, donc aucun
    test existant n'est affecté.
    """
    return generate_with_ollama(
        model,
        prompt,
        options=EXTRACTION_INFERENCE_OPTIONS,
        format=EXTRACTION_RESPONSE_FORMAT,
    )


def build_extraction_generate_fn(seed: int) -> Callable[[str, str], str]:
    """
    H.6.7a — Factory qui retourne un `generate_fn` câblé sur un seed
    arbitraire, sans modifier le `EXTRACTION_INFERENCE_OPTIONS` global.

    Utilisé par le runner multi-seed pour mesurer la robustesse du
    modèle au-delà du seed unique (42) figé en H.6.5.a. Le reste des
    paramètres (temperature=0, top_p, top_k, num_ctx) est préservé pour
    isoler la variable d'intérêt : seul le seed varie.

    Pure : retourne une closure qui, à l'appel, fera l'I/O via
    `generate_with_ollama`. Les tests unitaires peuvent monkeypatcher
    `generate_with_ollama` au point d'import du module pour vérifier le
    payload final.
    """
    options = dict(EXTRACTION_INFERENCE_OPTIONS)
    options["seed"] = seed

    def _fn(model: str, prompt: str) -> str:
        return generate_with_ollama(
            model,
            prompt,
            options=options,
            format=EXTRACTION_RESPONSE_FORMAT,
        )

    return _fn

# Listes pour le prompt strict. Source de vérité = enums Pydantic.
_KIND_VALUES: tuple[str, ...] = (
    "bottle", "jar", "box", "tube", "cylinder", "sphere", "watch",
)
_KIND_FIDELITY_VALUES: tuple[str, ...] = ("exact", "approximate")
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
    "transparency": "<one of: {transparencies}>",
    "label": "<description courte et fidèle du sujet demandé>",
    "kind_fidelity": "<one of: {kind_fidelities}>"
  }},
  "backdrop": {{
    "color": "<named color OR #RRGGBB hex>"
  }},
  "pedestal": {{
    "color": "<named color OR #RRGGBB hex>",
    "material": "<one of: {materials}>"
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

Champs de fidélité (TOUJOURS fournis, en v0 comme en v1) :
- `subject.label` : recopie courte et fidèle de la description du sujet \
telle que demandée, dans la langue de la demande (ex. "chronomètre métal \
poli"). Maximum 120 caractères. N'invente rien, ne traduis pas.
- `subject.kind_fidelity` = `exact` si la demande nomme directement un \
objet de la liste kind (bouteille, pot, boîte, tube, cylindre, sphère, \
montre/chronomètre). Sinon `approximate` : le kind choisi n'est qu'une \
approximation de l'objet demandé.

Fond (`backdrop`) :
- Si la demande NOMME une couleur ou une nuance de fond, respecte-la \
EXACTEMENT, y compris les nuances de gris : "fond gris froid" → cool_gray, \
"fond gris chaud" → warm_gray, "fond gris neutre" → neutral_gray, \
"fond bleu" → blue, "backdrop noir" → black.
- UNIQUEMENT si la demande ne décrit AUCUN fond, utilise `neutral_gray` \
par défaut (studio neutre). N'invente JAMAIS une couleur de fond non demandée.

Socle (`pedestal`) :
- Fournis la clé `pedestal` UNIQUEMENT si la demande décrit le socle, le \
support ou la base du produit (couleur ou matière). Exemple : "sur socle \
pierre" → pedestal.color=warm_gray, pedestal.material=matte. Sinon OMETS \
complètement la clé `pedestal`.
- `pedestal` est un champ V1.

Choix entre v0 et v1 :
- Utilise `schema_version="v0"` si la demande ne mentionne AUCUN des \
champs V1 (silhouette, bouchon, transparence, cadrage, socle). Dans ce \
cas, OMETS complètement les clés `shape`, `cap`, `transparency`, \
`framing`, `pedestal` du JSON (ne les mets pas à null). `label` et \
`kind_fidelity` restent fournis et ne comptent pas dans ce choix.
- Utilise `schema_version="v1"` UNIQUEMENT si tu remplis effectivement \
au moins un de ces champs V1.

RÈGLE ANTI-INFÉRENCE (critique pour le choix v0/v1) : un champ V1 ne se \
remplit QUE si la demande l'énonce avec un mot explicite. Ne DÉDUIS \
JAMAIS `shape`, `cap`, `transparency` ou `framing` du type d'objet ni \
du bon sens :
- "tube" seul → N'écris PAS shape=cylindrical ni transparency=opaque ;
- "boîte" seule → N'écris PAS shape=rectangular ;
- "bouteille" ou "flacon" seuls → N'écris PAS cap=present : il faut \
"bouchon", "couvercle" ou équivalent dans la demande ;
- "en verre" seul → material=glass, et N'écris PAS transparency ;
- transparency s'écrit UNIQUEMENT si la demande contient un mot de \
transmission, avec ce mapping strict : "transparent"/"transparente" → \
transparency=glass (la valeur "transparent" N'EXISTE PAS, ne l'écris \
jamais) ; "translucide" → translucent ; "opaque" → opaque ;
- aucun mot de cadrage (packshot, plan rapproché, cadrage moyen...) → \
N'écris PAS framing.
Si aucun champ V1 n'est énoncé explicitement, la bonne réponse est \
`schema_version="v0"` SANS les clés V1.

Exemples du choix v0/v1 (à imiter) :
- "bouteille de shampoing verte en verre sur fond beige" → \
{{"schema_version": "v0", "subject": {{"kind": "bottle", "color": "green", \
"material": "glass", "label": "bouteille de shampoing verte en verre", \
"kind_fidelity": "exact"}}, "backdrop": {{"color": "beige"}}}} \
(rien d'explicite sur silhouette/bouchon/transparence/cadrage → v0, \
même si une bouteille a "évidemment" un bouchon et que le verre est \
"évidemment" transparent).
- "packshot serré d'un flacon cylindrique translucide bleu avec bouchon, \
fond noir" → \
{{"schema_version": "v1", "subject": {{"kind": "bottle", "color": "blue", \
"material": "glossy", "shape": "cylindrical", "cap": "present", \
"transparency": "translucent", "label": "flacon cylindrique translucide \
bleu avec bouchon", "kind_fidelity": "exact"}}, "backdrop": \
{{"color": "black"}}, "framing": "close_packshot"}} \
(chaque champ V1 rempli correspond à un mot explicite de la demande).

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

Hints lexicaux français → enum kind (à appliquer dès que le mot \
apparaît dans la demande, sans inventer) :
- "pot" ou "pot de crème" → kind=jar
- "flacon", "bouteille" → kind=bottle
- "boîte", "coffret" → kind=box
- "tube" → kind=tube
- "sphère", "boule" → kind=sphere
- "cylindre" → kind=cylinder
- "montre", "chronomètre", "chrono" → kind=watch

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
        kind_fidelities=", ".join(_KIND_FIDELITY_VALUES),
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


# Couleur "safety default" appliquée quand le LLM produit un token
# couleur que la validation Pydantic refuserait (ni palette, ni hex).
# Sans cette protection, une seule couleur hallucinée (ex. "chrome",
# "silver", "ivory") fait tomber tout le cas en fallback, ce qui efface
# l'évaluation des autres champs corrects.
#
# `neutral_gray` est choisi parce qu'il est :
#  - dans la palette (donc accepté par Pydantic) ;
#  - sémantiquement neutre (n'affirme rien que le LLM n'aurait pas dit) ;
#  - aligné avec le défaut implicite du builder pour les rendus sans
#    contrainte couleur forte.
_INVALID_COLOR_SAFETY_DEFAULT: str = "neutral_gray"


def _is_valid_color_token(value: object) -> bool:
    """Pure : retourne True si `value` est un token couleur que
    `_validate_color_token` accepterait."""
    if not isinstance(value, str):
        return False
    try:
        _validate_color_token(value)
        return True
    except ValueError:
        return False


def _normalize_color_safety_default(data: dict) -> dict:
    """
    Remplace toute valeur de `subject.color` / `backdrop.color` que
    Pydantic refuserait par `_INVALID_COLOR_SAFETY_DEFAULT`.

    Préserve la mesurabilité du benchmark : un LLM qui invente "chrome"
    voit son cas continuer à scorer les autres champs (kind, material,
    backdrop, ...), au prix d'une couleur erronée — au lieu d'un
    fallback complet qui mettrait *tous* les champs à zéro.

    Aucun effet sur les valeurs déjà valides (palette ou hex correct).
    Pure, idempotent.
    """
    new_data = dict(data)
    for section in ("subject", "backdrop", "pedestal"):
        block = new_data.get(section)
        if isinstance(block, dict) and "color" in block:
            if not _is_valid_color_token(block["color"]):
                new_block = dict(block)
                new_block["color"] = _INVALID_COLOR_SAFETY_DEFAULT
                new_data[section] = new_block
    return new_data


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
    for section in ("subject", "backdrop", "pedestal"):
        block = new_data.get(section)
        if isinstance(block, dict) and "color" in block:
            new_block = dict(block)
            new_block["color"] = _normalize_color_hex_to_palette(block["color"])
            new_data[section] = new_block
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


def _normalize_semantic_fields(data: dict) -> dict:
    """
    semantic_fidelity_v1 — Assainit les champs de fidélité produits par le
    LLM, pour qu'une valeur imparfaite ne fasse jamais tomber le cas en
    fallback complet :

    - `subject.label` : non-str ou vide après strip → clé supprimée (None
      implicite = "information non disponible") ; trop long → tronqué à
      SUBJECT_LABEL_MAX_LEN (cohérent avec le cap user_intent de intent.json).
    - `subject.kind_fidelity` : valeur hors enum → clé supprimée.

    Pure, idempotent.
    """
    subj = data.get("subject")
    if not isinstance(subj, dict):
        return data
    new_subj = dict(subj)
    if "label" in new_subj:
        label = new_subj["label"]
        if not isinstance(label, str) or not label.strip():
            new_subj.pop("label")
        else:
            new_subj["label"] = label.strip()[:SUBJECT_LABEL_MAX_LEN]
    if "kind_fidelity" in new_subj:
        if new_subj["kind_fidelity"] not in _KIND_FIDELITY_VALUES:
            new_subj.pop("kind_fidelity")
    if new_subj == subj:
        return data
    return {**data, "subject": new_subj}


def _normalize_pedestal(data: dict) -> dict:
    """
    semantic_fidelity_v1 — Assainit le bloc `pedestal` :

    - pedestal non-dict (str, list, null...) → clé supprimée (socle canonique) ;
    - pedestal sans `color` → clé supprimée (color est requis par PedestalIR) ;
    - `material` hors enum SubjectMaterial (ex. "stone" halluciné) → clé
      supprimée (Pydantic appliquera le défaut "matte").

    La couleur elle-même est traitée par les normalizers couleur génériques
    (hex→palette puis safety default), qui couvrent déjà la section pedestal.

    Pure, idempotent.
    """
    if "pedestal" not in data:
        return data
    ped = data["pedestal"]
    if not isinstance(ped, dict) or "color" not in ped:
        new_data = dict(data)
        new_data.pop("pedestal")
        return new_data
    if "material" in ped and ped["material"] not in _MATERIAL_VALUES:
        new_ped = dict(ped)
        new_ped.pop("material")
        return {**data, "pedestal": new_ped}
    return data


def _normalize_schema_version(data: dict) -> dict:
    """
    Coerce `schema_version="v1"` vers `"v0"` dans deux cas symétriques :

    A. **V1 vide** (H.6.4) : aucun champ V1 (`subject.shape`, `subject.cap`,
       `subject.transparency`, `framing`) fourni ou tous None. Le serveur
       LLM a juste annoncé v1 par sur-promotion du prompt sans rien dire
       d'utile.

    B. **V1 dump complet à défaut** (H.6.6) : les **4** champs V1 sont
       tous présents et tous égaux à leur valeur par défaut builder
       (`V1_DEFAULTS`). Observé sur v0-jar au benchmark H.6.5 : le LLM
       remplissait `shape=cylindrical`, `cap=absent`, `transparency=opaque`,
       `framing=medium`. Sémantiquement équivalent à v0 (= "rien à
       ajouter par rapport au comportement défaut du builder").

    Cas conservés en v1 :
    - n'importe quel V1 informatif (= ≠ default) présent ;
    - **présence partielle** des V1 (même tous au default) : le LLM a fait
      un choix sélectif, on respecte. C'est notamment le cas après
      `_normalize_material_transparency` qui fait remonter une seule
      valeur (typiquement `transparency=opaque`) depuis material.

    Lors du downgrade, on purge `subject.shape`, `subject.cap`,
    `subject.transparency` et `framing` pour respecter `_enforce_v0_purity`
    côté Pydantic.

    Pure, idempotent.
    """
    if data.get("schema_version") != "v1":
        return data
    subj_in = data.get("subject", {})
    subj = subj_in if isinstance(subj_in, dict) else {}

    # Collecte des valeurs V1 effectivement présentes (non-None).
    v1_values: dict[str, object] = {}
    for short in ("shape", "cap", "transparency"):
        v = subj.get(short)
        if v is not None:
            v1_values[short] = v
    f = data.get("framing")
    if f is not None:
        v1_values["framing"] = f
    # semantic_fidelity_v1 — un pedestal non-None est toujours informatif
    # (pas de "valeur défaut" possible : le défaut est l'absence de la clé).
    ped = data.get("pedestal")
    if ped is not None:
        v1_values["pedestal"] = ped

    # Cas A — V1 vide.
    case_a = (len(v1_values) == 0)
    # Cas B — V1 dump complet à défaut (les 4 champs scalaires présents,
    # tous au default, et pas de pedestal).
    case_b = (
        len(v1_values) == 4
        and "pedestal" not in v1_values
        and all(val == V1_DEFAULTS[name] for name, val in v1_values.items())
    )
    if not (case_a or case_b):
        return data

    # Downgrade + purge totale des clés V1 (qu'elles soient None ou default).
    # Les métadonnées version-neutres (label, kind_fidelity) sont conservées.
    cleaned_subj = {
        k: v for k, v in subj.items()
        if k not in ("shape", "cap", "transparency")
    }
    new_data = dict(data)
    new_data["schema_version"] = "v0"
    new_data["subject"] = cleaned_subj
    new_data.pop("framing", None)
    new_data.pop("pedestal", None)
    return new_data


def _apply_normalizers(data: dict) -> dict:
    """
    Applique l'ensemble des normalizers dans un ordre stable. Pure.

    Ordre :
    1. _normalize_pedestal              — fix bloc pedestal malformé (semantic_fidelity_v1)
    2. _normalize_colors                — fix hex CSS → palette nommée
    3. _normalize_color_safety_default  — fix couleurs inventées hors palette/hex
    4. _normalize_semantic_fields       — fix label/kind_fidelity malformés (semantic_fidelity_v1)
    5. _normalize_material_transparency — fix swap enum (opaque/translucent → transparency)
    6. _normalize_schema_version        — fix sur-promotion v1 (vide ou dump complet à default)

    Justifications de l'ordre :
    - 1 avant 2/3 : les normalizers couleur ne doivent voir qu'un pedestal
      structurellement valide (dict avec color).
    - 2 avant 3 : un hex CSS commun (`#ffffff`) doit avoir une chance d'être
      mappé sur son nom de palette AVANT que le safety default ne s'applique.
    - 5 avant 6 : si on libère `transparency` à partir de `material`,
      le cas devient un vrai V1 informatif et 6 ne déclenchera pas la
      coercition à v0.
    """
    data = _normalize_pedestal(data)
    data = _normalize_colors(data)
    data = _normalize_color_safety_default(data)
    data = _normalize_semantic_fields(data)
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
        # H.6.5.a — par défaut, on passe par le wrapper stabilisé qui
        # injecte les options d'inférence et le format JSON serveur.
        generate_fn = _default_extraction_generate_fn

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
