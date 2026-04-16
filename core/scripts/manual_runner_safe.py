from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine.executor import execute_request


def run_case(user_input: str):
    print("\n====================")
    print("INPUT:", user_input)

    try:
        result = execute_request(user_input)
    except Exception as exc:
        print("\nERROR:")
        print(f"{type(exc).__name__}: {exc}")
        return

    print("\nTRACE:")
    for step in result.get("decision_trace", []):
        print(" -", step)

    print("\nOUTPUT:")
    print(result.get("output"))

    extra = result.get("extra", {})
    if extra:
        print("\nEXTRA:")
        print(extra)


if __name__ == "__main__":
    cases = [
        "explique moi les embeddings",
        "cherche moi les dernières news IA",
        "génère une image cyberpunk",
        "génère une image cyberpunk avec néon humide",
        "compare deux architectures LLM",
        "écris moi un script python simple",
    ]

    for case in cases:
        run_case(case)
