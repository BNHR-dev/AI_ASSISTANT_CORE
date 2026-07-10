"""
Harnais — routage template sur le corpus d'éval product_render (2026-07-10).

Trouvaille de l'expérience jepa_eval (experiments/jepa_eval, BENCHMARK.md) :
6 des 11 prompts du corpus d'éval routaient vers le chemin legacy — le corpus
avait été construit pour mesurer l'*extracteur* (H.6.2), le routage template
en amont n'était couvert par aucun harnais. Décision produit : le corpus est
product_render par construction, chaque cas DOIT router vers le builder
déterministe. Le trou est fermé par le signal composé « objet nu sur fond »
(_matches_bare_packshot, évalué après les mots-clés intérieurs).

Ce module est le harnais : il rejoue la chaîne de sélection réelle de
build_blender_script (intent d'abord, message brut en fallback — cf.
app/clients/blender_client.py) sur TOUT le corpus, plus des négatifs qui
verrouillent le périmètre du nouveau signal. Tout ajout au corpus est
automatiquement couvert.
"""
from __future__ import annotations

import pytest

from app.engine.artistic_intent import parse_artistic_intent
from app.engine.blender_templates import (
    get_template_name,
    get_template_name_from_intent,
)
from app.engine.product_render_eval_cases import DEFAULT_CASES


def route_template_name(message: str) -> str | None:
    """
    Rejoue la chaîne de sélection de build_blender_script :
    creative_intent (H.4.1) d'abord, message brut (fallback historique) sinon.
    Miroir volontaire de app/clients/blender_client.py — si la chaîne du
    client change, ce harnais doit changer avec elle.
    """
    intent = parse_artistic_intent(message)
    name = get_template_name_from_intent(intent)
    if name is None:
        name = get_template_name(message)
    return name


# ---------------------------------------------------------------------------
# Le corpus entier route vers le builder déterministe
# ---------------------------------------------------------------------------

class TestEvalCorpusRoutesToProductRender:

    @pytest.mark.parametrize(
        "case",
        DEFAULT_CASES,
        ids=[c.id for c in DEFAULT_CASES],
    )
    def test_corpus_case_routes_product_render(self, case):
        assert route_template_name(case.prompt) == "product_render", (
            f"{case.id}: le prompt du corpus d'éval doit atteindre le builder "
            f"déterministe (prompt: {case.prompt!r})"
        )


# ---------------------------------------------------------------------------
# Négatifs — le signal « objet nu sur fond » ne déborde pas
# ---------------------------------------------------------------------------

class TestBarePackshotSignalScope:

    @pytest.mark.parametrize("message", [
        # Test négatif historique H.5.4.1 : « cinématographique » seul.
        "scène cinématographique sombre dans une rue",
        # « sur fond » idiomatique sans objet produit → pas un packshot.
        "ville futuriste sur fond de coucher de soleil",
        "silhouette d'un personnage sur fond de brume",
        # Objet produit sans marqueur de fond → pas assez de signal.
        "un cube flottant dans un laboratoire",
        "une bouteille abandonnée dans une rue sombre",
    ])
    def test_does_not_route_product_render(self, message):
        assert route_template_name(message) != "product_render"

    @pytest.mark.parametrize("message", [
        # Mot-clé intérieur explicite : prioritaire sur le signal composé.
        "boîte rouge sur fond noir dans une chambre",
        "pot blanc sur fond beige au centre de la salle",
    ])
    def test_explicit_interior_keyword_wins(self, message):
        assert route_template_name(message) == "interior_space"

    @pytest.mark.parametrize("message", [
        # La grammaire packshot minimale, hors corpus.
        "boîte rouge brillante sur fond noir",
        "sphère chromée métallique sur fond gris froid",
        "tube vert mat sur un fond blanc",
    ])
    def test_bare_object_on_backdrop_routes_product_render(self, message):
        assert route_template_name(message) == "product_render"
