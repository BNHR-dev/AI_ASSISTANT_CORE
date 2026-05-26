"""
H.5.1 — Product Render Intermediate Representation (IR) V0.

Mini-IR product_render-spécifique, ultra-plate, ~5 champs leaf.
Cadrée par l'ADR [[16_H5_PRODUCT_RENDER_IR_CADRAGE]] (Décision 11).

Politique V0 (stricte, à respecter par toute extension future) :
- IR product_render-SPÉCIFIQUE uniquement (pas de Scene Graph polymorphe).
- IR PLATE (pas d'arbres récursifs, pas de listes d'objets).
- 5 champs LEAF maximum : schema_version + subject.{kind, color, material} + backdrop.color.
- Pas de pedestal, pas de camera, pas de lighting exposés en V0
  (restent canoniques via blender_runtime_corrector.CANONICAL_*).
- Toute extension passe par une ADR séparée (V1 = H.5.4 envisagée).

Le LLM décide QUOI (forme, couleur, matériau, fond).
Le système (product_render_builder) décide COMMENT (code bpy déterministe).

Aucun import LLM, aucun appel réseau, aucun I/O.
Fonction PURE : import + validation Pydantic.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums fermés (V0)
# ---------------------------------------------------------------------------

# Set borné de formes que le LLM peut demander. Le builder mappe chaque
# kind vers une primitive bpy + des dimensions canoniques.
SubjectKind = Literal["bottle", "jar", "box", "tube", "cylinder", "sphere"]

# Set borné de profils matériaux. Le builder mappe chaque material vers
# une configuration Principled BSDF déterministe (roughness, metallic, etc.).
SubjectMaterial = Literal["matte", "glossy", "glass", "metallic"]


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
    """Sujet produit : forme + couleur + profil matériau."""

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
    Intent product_render V0 — 5 champs leaf maximum.

    schema_version : versionné dès V0 pour permettre une migration future
                     non-cassante quand V1 ajoutera des champs.
    subject        : la chose à mettre en scène (kind + color + material).
    backdrop       : le fond (color en V0).

    PAS en V0 :
    - pedestal : toujours canonique en V0.
    - camera   : toujours canonique (CANONICAL_CAMERA H.4.8.1).
    - lighting : toujours canonique (CANONICAL_KEY_LIGHT + CANONICAL_FILL_LIGHT).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["v0"] = Field(
        ..., description="Version du schéma IR. V0 figée en H.5.1."
    )
    subject: ProductSubjectIR
    backdrop: BackdropIR
