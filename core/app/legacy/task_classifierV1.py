def classify_task(message: str, has_image: bool = False) -> tuple[str, str]:
    text = message.lower().strip()

    # =========================================================
    # 1. VISION
    # =========================================================
    if has_image:
        return ("vision", "Image détectée.")

    vision_keywords = [
        "analyse cette image",
        "analyse cette capture",
        "analyse ce screenshot",
        "décris cette image",
        "decris cette image",
        "dis-moi ce que tu vois",
        "dis moi ce que tu vois",
        "screenshot",
        "photo",
        "diagramme",
        "schéma",
        "schema",
        "capture d'écran",
        "capture d’ecran",
        "capture d’écran",
    ]
    vision_concept_protection = [
        "image latente",
        "génération d'image",
        "generation d'image",
        "génération image",
        "generation image",
        "vision par ordinateur",
    ]
    if (
        any(k in text for k in vision_keywords)
        and not any(k in text for k in vision_concept_protection)
    ):
        return ("vision", "Analyse visuelle.")

    # =========================================================
    # 2. QUIZ
    # =========================================================
    if any(k in text for k in [
        "quiz",
        "teste moi",
        "teste-moi",
        "interroge moi",
        "interroge-moi",
        "pose-moi des questions",
        "pose moi des questions",
    ]):
        return ("quiz", "Quiz demandé")

    # =========================================================
    # 3. CRITIQUE
    # =========================================================
    if any(k in text for k in [
        "corrige",
        "critique",
        "correction",
        "erreur",
        "erreurs",
        "bug",
        "bugs",
        "feedback",
        "review",
        "relis",
        "améliore",
        "ameliore",
    ]):
        return ("critique", "Critique demandée")

    # =========================================================
    # 4. WEB RESEARCH
    # Le mot 'source' seul ne suffit pas.
    # =========================================================
    if any(k in text for k in [
        "cherche",
        "recherche",
        "trouve",
        "trouve moi",
        "trouve-moi",
        "news",
        "actualités",
        "actualites",
        "dernières avancées",
        "dernieres avancees",
        "t'as des sources",
        "t’as des sources",
        "tu as des sources",
        "donne-moi des sources",
        "donne moi des sources",
        "sources récentes",
        "sources recentes",
        "articles récents",
        "articles recents",
        "doc officielle",
        "documentation officielle",
        "site officiel",
        "sur internet",
        "en ligne",
        "regarde en ligne",
        "va chercher",
    ]):
        return ("web_research", "Recherche web")

    # =========================================================
    # 5. ARCHITECTURE
    # Ajout des formulations implicites réelles.
    # =========================================================
    if any(k in text for k in [
        "architecture",
        "compare",
        "comparaison",
        "pipeline",
        "routing",
        "router",
        "orchestrateur",
        "quelle approche",
        "stocker",
        "stockage",
        "organiser",
        "structure",
        "structurer",
        "comment gérer",
        "comment gerer",
        "comment stocker",
        "tu me proposes quoi",
        "tu me proposes quoi pour stocker",
        "j'hésite",
        "j’hésite",
        "j'hesite",
        "j’hesite",
    ]):
        return ("architecture", "Analyse système")

    # =========================================================
    # 6. EXPLAIN ADVANCED
    # =========================================================
    if any(k in text for k in [
        "en détail",
        "en detail",
        "approfondis",
        "approfondir",
        "mécanisme",
        "mecanisme",
        "fonctionnement interne",
        "implications",
        "technique",
    ]):
        return ("explain_advanced", "Explication avancée")

    # =========================================================
    # 7. EXPLAIN BASIC
    # =========================================================
    if any(k in text for k in [
        "explique",
        "c'est quoi",
        "cest quoi",
        "définition",
        "definition",
        "simplement",
        "je comprends rien",
        "je comprends rien à",
        "je comprends rien a",
    ]):
        return ("explain_basic", "Explication simple")

    # =========================================================
    # 8. BUILD
    # =========================================================
    if any(k in text for k in [
        "code",
        "python",
        "script",
        "fonction",
        "api",
        "json",
        "module",
        "classe",
        "class",
        "parser",
        "regex",
        "sql",
        "fastapi",
    ]):
        return ("build", "Code demandé")

    # =========================================================
    # 9. FALLBACK
    # =========================================================
    return ("explain_basic", "Fallback")
