from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import os
import sys
from pathlib import Path

print("DEBUG CWD =", os.getcwd())
print("DEBUG COMFYUI_BAT_PATH =", os.getenv("COMFYUI_BAT_PATH"))

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine.executor import execute_request


CASES = [
    "explique moi les embeddings",
    "explique moi les embeddings avec un exemple python",
    "cherche moi les dernières news IA",
    "génère une image cyberpunk",
    "génère une image cyberpunk avec néon humide",
    "compare deux architectures LLM",
    "écris moi un script python simple",
    "génère moi une image de scène de guerre",
]


def run_case(user_input: str) -> None:
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

    if result.get("plan"):
        print("\nPLAN:")
        for step in result["plan"]:
            print(
                f" - {step['step_id']} | {step['step_type']} | "
                f"status={step.get('status')} | tool={step.get('tool')}"
            )

    print("\nOUTPUT:")
    print(result.get("output"))

    if result.get("primary_output") is not None:
        print("\nPRIMARY_OUTPUT:")
        print(result.get("primary_output"))

    if result.get("second_output") is not None:
        print("\nSECOND_OUTPUT:")
        print(result.get("second_output"))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_case(" ".join(sys.argv[1:]))
    else:
        for case in CASES:
            run_case(case)