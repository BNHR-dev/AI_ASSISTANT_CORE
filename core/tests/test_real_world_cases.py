import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # core/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine.router_service import build_route_decision


def test_real_world_cases():
    cases = [
        # 🧠 hybride explication + code
        {
            "input": "explique moi les embeddings vite fait avec un exemple python",
            "expected_task": "explain_basic",
            "expected_second_call": "build",
        },

        # 🌐 web implicite
        {
            "input": "t'as des sources sur les dernières avancées IA ?",
            "expected_task": "web_research",
            "expected_tool": "web",
        },

        # 🎨 génération image
        {
            "input": "fais moi une image cyberpunk stylée",
            "expected_tool": "comfyui",
        },

        # 🧠 ambigu : code ou explication ?
        {
            "input": "c'est quoi une API fastapi avec un exemple",
            "expected_task": "explain_basic",
            "expected_second_call": "build",
        },

        # ⚠️ bruit utilisateur
        {
            "input": "stp tu peux me dire genre comment marche un embedding vite fait merci",
            "expected_task": "explain_basic",
        },

        # 🔧 build pur
        {
            "input": "écris moi une fonction python qui parse du json",
            "expected_task": "build",
        },

        # 🧠 architecture + implémentation
        {
            "input": "je sais pas quoi choisir entre deux archis mémoire tu me proposes un truc simple à coder",
            "expected_task": "architecture",
            "expected_second_call": "build",
        },

        # ❌ faux positif web à éviter
        {
            "input": "explique moi internet simplement",
            "expected_task": "explain_basic",
            "expected_tool": None,
        },

        # ❌ faux positif code à éviter
        {
            "input": "c'est quoi python",
            "expected_task": "explain_basic",
            "expected_second_call": None,
        },

        # 🔥 combo critique + amélioration
        {
            "input": "voici mon code il est nul améliore le stp",
            "expected_task": "critique",
            "expected_second_call": "build",
        },
    ]

    for case in cases:
        result = build_route_decision(case["input"], False)

        print("\n====================")
        print("INPUT:", case["input"])
        print("OUTPUT:", result)

        if "expected_task" in case:
            assert result["task_type"] == case["expected_task"]

        if "expected_second_call" in case:
            assert result["second_call"] == case["expected_second_call"]

        if "expected_tool" in case:
            assert result["selected_tool"] == case["expected_tool"]