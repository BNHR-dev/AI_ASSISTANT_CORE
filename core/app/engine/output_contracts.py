from __future__ import annotations

from typing import Any


OUTPUT_CONTRACTS: dict[str, dict[str, Any]] = {
    "explain_basic": {
        "description": "définition + image mentale + exemple concret",
        "sections": ["Définition", "Image mentale", "Exemple concret"],
        "rules": [
            "Va droit au but dès la première phrase.",
            "Explique avec des mots simples sans appauvrir le fond.",
            "Ajoute un exemple concret court et crédible.",
        ],
    },
    "explain_advanced": {
        "description": "explication détaillée + concepts + implications",
        "sections": ["Résumé", "Concepts clés", "Implications", "Exemple"],
        "rules": [
            "Structure les idées du plus important au plus technique.",
            "Fais apparaître les implications réelles, pas seulement la théorie.",
        ],
    },
    "architecture": {
        "description": "options + comparaison + décision + impacts système",
        "sections": [
            "Options",
            "Comparaison",
            "Décision recommandée",
            "Impacts",
            "Prochaine étape",
        ],
        "rules": [
            "Reste pragmatique et orienté décision.",
            "Évite les architectures trop larges ou spéculatives.",
            "Ancre l'analyse dans le contexte fourni ; évite les recommandations génériques qui s'appliqueraient à n'importe quel projet.",
        ],
    },
    "build": {
        "description": "module python + structure + instructions de test + usage",
        "sections": ["Objectif", "Code", "Tests rapides", "Usage"],
        "rules": [
            "Le livrable doit être directement exploitable.",
            "Place le code dans un bloc de code complet.",
            "Le code doit être cohérent, copiable-collable et sans TODO, pseudo-code ou trous.",
            "Indique explicitement les hypothèses minimales si certaines entrées ne sont pas précisées.",
            "Ajoute des tests ou vérifications rapides minimales.",
            "Si la demande mentionne une technologie, une méthode ou un algorithme précis, implémente-le réellement. Si une alternative est proposée, annonce-la explicitement comme alternative.",
            "Si la demande porte sur des embeddings ou des vecteurs numériques, utilise des vecteurs numériques explicites (tableaux, listes ou numpy arrays). Ne substitue pas silencieusement TfidfVectorizer ou CountVectorizer aux embeddings sans l'annoncer explicitement comme alternative.",
            "Les assertions de test doivent être sémantiquement valides ; ne pas affirmer une valeur exacte quand elle dépend du modèle, de l'entrée, d'un vectorizer ou d'une approximation.",
            "Si la demande porte sur Blender ou bpy, produire un script Python destiné à Blender : commencer le code par import bpy, utiliser les API bpy.ops, bpy.data et bpy.context, créer explicitement caméra/lumière/matériaux si utile, éviter les chemins fichier hardcodés y compris les chemins relatifs comme output.png sauf si l'utilisateur fournit un chemin explicite, et ne pas exécuter bpy.ops.render.render() sauf demande explicite. Idiomes qualité : pour un matériau métallique, utiliser le Principled BSDF avec Metallic = 1.0 ; pour une animation, définir scene.frame_start et scene.frame_end et utiliser keyframe_insert ; pour un texte 3D extrudé, utiliser text_obj.data.extrude et non un resize sur Z ; éviter nodes.clear() suivi d'une référence à un nœud supprimé ; si math.pi est nécessaire, importer math en début de script et ne pas utiliser bpy.math.pi.",
            "La réponse doit être concise et directement exploitable : évite les introductions, les récapitulatifs inutiles et les sections vides.",
        ],
    },
    "quiz": {
        "description": "questions progressives + correction + feedback",
        "sections": ["Questions", "Correction", "Feedback"],
        "rules": [
            "Commence simple puis augmente progressivement la difficulté.",
        ],
    },
    "critique": {
        "description": "analyse + erreurs + améliorations + justification",
        "sections": ["Constat", "Erreurs ou limites", "Améliorations", "Justification"],
        "rules": [
            "Sépare clairement le diagnostic des recommandations.",
            "Sois concis et précis ; une critique courte et ancrée vaut mieux qu'une longue liste de généralités.",
        ],
    },
    "web_research": {
        "description": "synthèse + sources utiles + résumé clair",
        "sections": ["Synthèse", "Points clés", "Sources retenues"],
        "rules": [
            "Ne cite que les sources réellement fournies.",
            "Les points clés doivent venir des résultats retenus, pas d'une connaissance inventée.",
            "Les points clés doivent être concrets : dates, noms de produits, annonces, chiffres, changements techniques ou éléments vérifiables extraits des sources.",
        ],
    },
    "vision": {
        "description": "description + analyse + interprétation visuelle",
        "sections": ["Description", "Analyse", "Interprétation prudente"],
        "rules": [
            "Distingue les faits visibles des hypothèses.",
            "Réponds directement à la demande posée ; ne dévie pas vers une description générale si une question précise est posée.",
            "Si l'image est illisible, ambiguë ou ne contient pas l'élément demandé, indique-le explicitement sans inventer de contenu.",
        ],
    },
    "image_generation": {
        "description": "prompt structuré + paramètres visuels",
        "sections": ["Sujet", "Style", "Cadrage", "Lumière", "Paramètres"],
        "rules": [
            "Transforme la demande en intention visuelle claire et exploitable.",
        ],
    },
}


DEFAULT_CONTRACT = {
    "description": "réponse claire et exploitable",
    "sections": ["Réponse"],
    "rules": ["Va à l'essentiel."],
}


def get_output_contract(
    task_type: str,
    fallback_description: str | None = None,
    locale: str = "fr",
) -> dict[str, Any]:
    contract = OUTPUT_CONTRACTS.get(task_type)
    if contract is None:
        contract = DEFAULT_CONTRACT

    description = fallback_description or contract["description"]

    return {
        "task_type": task_type,
        "description": description,
        "sections": list(contract["sections"]),
        "rules": list(contract["rules"]),
        "locale": locale,
    }


def render_output_contract(
    task_type: str,
    fallback_description: str | None = None,
    locale: str = "fr",
) -> str:
    contract = get_output_contract(
        task_type,
        fallback_description=fallback_description,
        locale=locale,
    )
    sections = "\n".join(
        f"  {idx}. {title}"
        for idx, title in enumerate(contract["sections"], start=1)
    )
    rules = "\n".join(f"- {rule}" for rule in contract["rules"])

    return (
        "Format attendu :\n"
        f"- Description cible : {contract['description']}\n"
        "- Utilise si possible exactement ces titres :\n"
        f"{sections}\n"
        "Règles de sortie :\n"
        f"{rules}"
    )