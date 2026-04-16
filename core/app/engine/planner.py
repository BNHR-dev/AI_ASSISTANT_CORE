from app.clients.ollama_client import generate_with_ollama


def build_plan(message: str, model_name: str) -> str:
    planning_prompt = f"""
Tu es un planificateur technique pour un assistant IA local orienté création numérique.

Ta mission :
analyser la demande utilisateur et proposer un plan d'action clair, pratique, structuré et directement exploitable.

Consignes :
- Réponds en français.
- Structure la réponse en étapes numérotées.
- Pour chaque étape, donne :
  1. l'objectif
  2. l'outil recommandé
  3. l'action à faire
  4. le résultat attendu
- Si pertinent, mentionne ComfyUI, FL Studio, Photoshop, Krita ou Premiere Pro.
- Reste concret.
- N'écris pas de blabla inutile.
- N'invente pas de recherche web.
- Tu produis un PLAN, pas une réponse générale.

Demande utilisateur :
{message}
""".strip()

    return generate_with_ollama(model_name, planning_prompt)