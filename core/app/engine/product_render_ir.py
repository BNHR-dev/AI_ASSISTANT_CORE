"""
H.5.1 / H.5.4 — Product Render Intermediate Representation (IR).

Mini-IR product_render-spécifique, ultra-plate.
Cadrée par l'ADR [[16_H5_PRODUCT_RENDER_IR_CADRAGE]] (Décision 11).

V0 (H.5.1) — 5 champs leaf : schema_version + subject.{kind, color, material}
                              + backdrop.color.

V1 (H.5.4) — V0 + 4 champs leaf optionnels enrichissant la lisibilité du
             rendu, avec défauts explicites résolus par le builder :
  - subject.shape         (cylindrical | rectangular | rounded) — défaut cylindrical
  - subject.cap           (present | absent)                    — défaut absent
  - subject.transparency  (opaque | translucent | glass)        — défaut opaque
  - framing               (close_packshot | medium)             — défaut medium

Compatibilité :
- schema_version accepte v0 ET v1.
- En v0, les 4 nouveaux champs DOIVENT rester non fournis (extra="forbid"
  ne les rejette pas car ils existent dans le modèle ; c'est un validateur
  de niveau IR qui interdit leur présence quand schema_version == "v0",
  garantissant un comportement strictement identique à H.5.1).
- En v1, leur absence vaut "non spécifié" → le builder applique les défauts.

Le LLM décide QUOI (forme, couleur, matériau, fond + shape, cap, transparency,
framing en v1). Le système (product_render_builder) décide COMMENT (code bpy
déterministe).

Aucun import LLM, aucun appel réseau, aucun I/O.
Fonction PURE : import + validation Pydantic.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums fermés (V0)
# ---------------------------------------------------------------------------

# Set borné de formes que le LLM peut demander. Le builder mappe chaque
# kind vers une primitive bpy + des dimensions canoniques.
# semantic_fidelity_v1 : "watch" ajouté sur preuve (smoke audit 2026-06-10,
# "chronomètre métal poli" dégradé en box). Disque vertical face caméra.
SubjectKind = Literal["bottle", "jar", "box", "tube", "cylinder", "sphere", "watch"]

# Set borné de profils matériaux. Le builder mappe chaque material vers
# une configuration Principled BSDF déterministe (roughness, metallic, etc.).
SubjectMaterial = Literal["matte", "glossy", "glass", "metallic"]


# ---------------------------------------------------------------------------
# Enums fermés (V1 — H.5.4)
# ---------------------------------------------------------------------------

# Silhouette globale du sujet. Affine le rendu sans changer subject.kind.
# Builder V1 mapping :
#   cylindrical → primitive d'origine pour kind (V0 behavior)
#   rectangular → flacon "carré" (cube allongé : forme de packaging)
#   rounded     → flacon arrondi (sphère aplatie verticalement)
SubjectShape = Literal["cylindrical", "rectangular", "rounded"]

# Présence d'un bouchon secondaire posé au sommet du sujet.
SubjectCap = Literal["present", "absent"]

# Profil de transparence. Orthogonal à subject.material : si transparency=glass,
# le builder force un matériau verre (transmission=1.0) en conservant la couleur
# du subject (tint amber, etc.).
SubjectTransparency = Literal["opaque", "translucent", "glass"]

# semantic_fidelity_v1 — Fidélité du mapping kind. Rempli par l'extracteur :
#   exact       → le kind correspond directement au mot de la demande
#   approximate → le sujet demandé n'a pas de case dans l'enum ; le kind est
#                 la primitive la plus proche. La dégradation devient visible
#                 (manifest / scene_report) au lieu d'être silencieuse.
# None = information non disponible (extraction antérieure, legacy).
KindFidelity = Literal["exact", "approximate"]

# Longueur max du label descriptif libre — alignée sur le cap user_intent
# de intent.json (120 caractères, by design).
SUBJECT_LABEL_MAX_LEN = 120

# Profil de cadrage. medium = cadrage canonique H.4.8.x. close_packshot = sujet
# agrandi (scale 1.4x) pour rapprocher le cadrage sans toucher au tuning
# caméra/lumière H.4.8.x — la normalisation passive du corrector réapplique
# CANONICAL_CAMERA à l'identique, donc seule la mise à l'échelle du sujet
# produit un effet de framing rapproché stable.
Framing = Literal["close_packshot", "medium"]


# Valeurs par défaut V1 (résolues côté builder quand le champ est None).
V1_DEFAULTS: dict[str, str] = {
    "shape": "cylindrical",
    "cap": "absent",
    "transparency": "opaque",
    "framing": "medium",
}


# ---------------------------------------------------------------------------
# Palette de couleurs nommées
# ---------------------------------------------------------------------------
# Le LLM peut soit utiliser un nom de cette palette, soit un code hex
# `#RRGGBB`. Toute autre valeur fait échouer la validation.
#
# Volontairement courte en V0 : un LLM local peut produire de manière fiable
# un de ces tokens ou un code hex. Étendre la palette = étendre le risque
# que le LLM hallucine des variantes ("Amber", "AMBER", "amber-tinted"...).
# Les noms restent en minuscules, ASCII, simples.

NAMED_COLOR_PALETTE: dict[str, tuple[float, float, float, float]] = {
    "white":         (0.95, 0.95, 0.95, 1.0),
    "black":         (0.02, 0.02, 0.02, 1.0),
    "neutral_gray":  (0.50, 0.50, 0.50, 1.0),
    "warm_gray":     (0.55, 0.50, 0.45, 1.0),
    "cool_gray":     (0.45, 0.50, 0.55, 1.0),
    "amber":         (0.75, 0.45, 0.15, 1.0),
    "red":           (0.80, 0.10, 0.10, 1.0),
    "green":         (0.10, 0.55, 0.20, 1.0),
    "blue":          (0.10, 0.30, 0.75, 1.0),
    "yellow":        (0.90, 0.80, 0.15, 1.0),
    "orange":        (0.90, 0.45, 0.10, 1.0),
    "purple":        (0.45, 0.15, 0.55, 1.0),
    "pink":          (0.95, 0.55, 0.65, 1.0),
    "brown":         (0.40, 0.25, 0.15, 1.0),
    "beige":         (0.85, 0.75, 0.60, 1.0),
}

# Regex pour valider un code couleur hex strict #RRGGBB (lower ou upper).
HEX_COLOR_REGEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_color_token(value: str) -> str:
    """
    Valide qu'un token couleur est soit une couleur nommée connue,
    soit un code hex #RRGGBB. Retourne la valeur normalisée (lower).
    Lève ValueError sinon.

    Pure : pas d'I/O.
    """
    if not isinstance(value, str):
        raise ValueError(f"color must be str, got {type(value).__name__}")
    token = value.strip()
    if not token:
        raise ValueError("color must be non-empty")
    lower = token.lower()
    if lower in NAMED_COLOR_PALETTE:
        return lower
    if HEX_COLOR_REGEX.match(token):
        return token.lower()
    raise ValueError(
        f"color '{value}' is neither a named palette entry "
        f"({sorted(NAMED_COLOR_PALETTE.keys())}) nor a #RRGGBB hex code"
    )


def resolve_color(token: str) -> tuple[float, float, float, float]:
    """
    Résout un token couleur (validé) en RGBA tuple.

    Pure : pas d'I/O. Utilisée par le builder pour mapper IR → params bpy.
    Lève ValueError si le token est malformé (filet de sécurité ; en pratique
    Pydantic a déjà validé).
    """
    token = _validate_color_token(token)
    if token in NAMED_COLOR_PALETTE:
        return NAMED_COLOR_PALETTE[token]
    # token est un hex #RRGGBB déjà validé
    r = int(token[1:3], 16) / 255.0
    g = int(token[3:5], 16) / 255.0
    b = int(token[5:7], 16) / 255.0
    return (r, g, b, 1.0)


# ---------------------------------------------------------------------------
# Schémas Pydantic V0
# ---------------------------------------------------------------------------

class ProductSubjectIR(BaseModel):
    """Sujet produit : forme + couleur + profil matériau (+ champs V1 optionnels)."""

    model_config = ConfigDict(extra="forbid")

    kind: SubjectKind = Field(
        ..., description="Forme primitive du sujet (enum fermé V0)."
    )
    color: str = Field(
        ..., description="Couleur du sujet : palette nommée ou #RRGGBB."
    )
    material: SubjectMaterial = Field(
        ..., description="Profil matériau Principled BSDF (enum fermé V0)."
    )
    # --- Champs V1 (H.5.4) ---
    # Optionnels avec sentinel `None`. Le builder résout les défauts V1
    # uniquement quand schema_version == "v1". En v0 leur présence est
    # interdite par le validateur de niveau ProductRenderIntent.
    shape: Optional[SubjectShape] = Field(
        default=None,
        description="V1 : silhouette globale du sujet (défaut builder = cylindrical).",
    )
    cap: Optional[SubjectCap] = Field(
        default=None,
        description="V1 : bouchon secondaire au sommet du sujet (défaut builder = absent).",
    )
    transparency: Optional[SubjectTransparency] = Field(
        default=None,
        description="V1 : profil de transparence (défaut builder = opaque).",
    )
    # --- Champs semantic_fidelity_v1 ---
    # Métadonnées de fidélité, VERSION-NEUTRES (autorisées en v0 ET v1) :
    # elles ne changent pas la géométrie produite par le builder, elles
    # tracent ce que l'utilisateur a réellement demandé.
    label: Optional[str] = Field(
        default=None,
        max_length=SUBJECT_LABEL_MAX_LEN,
        description=(
            "semantic_fidelity_v1 : description courte et fidèle du sujet "
            "tel que demandé (ex. 'chronomètre métal poli'). Texte libre, "
            f"max {SUBJECT_LABEL_MAX_LEN} caractères."
        ),
    )
    kind_fidelity: Optional[KindFidelity] = Field(
        default=None,
        description=(
            "semantic_fidelity_v1 : exact si kind correspond directement à "
            "la demande, approximate si kind est la primitive la plus proche."
        ),
    )

    @field_validator("color")
    @classmethod
    def _validate_color(cls, v: str) -> str:
        return _validate_color_token(v)

    @field_validator("label")
    @classmethod
    def _normalize_label(cls, v: Optional[str]) -> Optional[str]:
        """Strip ; chaîne vide → None (absence d'information explicite)."""
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None


class PedestalIR(BaseModel):
    """
    semantic_fidelity_v1 — Socle paramétrable (V1 uniquement).

    Permet d'honorer les demandes type "sur socle pierre" au lieu de les
    perdre silencieusement. Absent (None au niveau intent) → le builder
    conserve le Pedestal canonique (gris foncé matte), comportement V0.
    Seuls couleur et profil matériau sont exposés ; géométrie canonique.
    """

    model_config = ConfigDict(extra="forbid")

    color: str = Field(
        ..., description="Couleur du socle : palette nommée ou #RRGGBB."
    )
    material: SubjectMaterial = Field(
        default="matte",
        description="Profil matériau du socle (défaut matte).",
    )

    @field_validator("color")
    @classmethod
    def _validate_color(cls, v: str) -> str:
        return _validate_color_token(v)


class BackdropIR(BaseModel):
    """Fond de scène : couleur seule en V0 (forme = plan canonique)."""

    model_config = ConfigDict(extra="forbid")

    color: str = Field(
        ..., description="Couleur du backdrop : palette nommée ou #RRGGBB."
    )

    @field_validator("color")
    @classmethod
    def _validate_color(cls, v: str) -> str:
        return _validate_color_token(v)


class ProductRenderIntent(BaseModel):
    """
    Intent product_render — V0 (H.5.1) ou V1 (H.5.4).

    schema_version : "v0" ou "v1". Détermine le comportement du builder.
    subject        : la chose à mettre en scène (kind + color + material
                     + shape/cap/transparency optionnels en V1).
    backdrop       : le fond (color).
    framing        : V1 uniquement — cadrage (défaut builder = medium).

    Garde-fous V0 (préservation byte-équivalente de H.5.1) :
    - les champs V1 (subject.shape/cap/transparency, framing) doivent rester
      non fournis quand schema_version == "v0".
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v0", "v1"] = Field(
        ..., description="Version du schéma IR. V0 figée en H.5.1, V1 en H.5.4."
    )
    subject: ProductSubjectIR
    backdrop: BackdropIR
    # --- Champ V1 (H.5.4) ---
    framing: Optional[Framing] = Field(
        default=None,
        description="V1 : cadrage du rendu (défaut builder = medium).",
    )
    # --- Champ semantic_fidelity_v1 (V1 uniquement) ---
    pedestal: Optional[PedestalIR] = Field(
        default=None,
        description=(
            "V1 : socle paramétrable (couleur + matériau). None = Pedestal "
            "canonique (comportement V0)."
        ),
    )

    @model_validator(mode="after")
    def _enforce_v0_purity(self) -> "ProductRenderIntent":
        """En v0, aucun champ V1 ne doit être fourni (compat byte-équivalente).

        Exceptions version-neutres : subject.label et subject.kind_fidelity
        (métadonnées de traçabilité sans effet géométrique)."""
        if self.schema_version == "v0":
            forbidden_v1 = {
                "subject.shape": self.subject.shape,
                "subject.cap": self.subject.cap,
                "subject.transparency": self.subject.transparency,
                "framing": self.framing,
                "pedestal": self.pedestal,
            }
            offenders = [k for k, v in forbidden_v1.items() if v is not None]
            if offenders:
                raise ValueError(
                    f"schema_version='v0' interdit les champs V1 : {offenders}. "
                    f"Utilise schema_version='v1' pour activer ces champs."
                )
        return self
