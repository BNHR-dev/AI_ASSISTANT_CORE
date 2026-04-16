from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine.executor import execute_request


DEFAULT_PROMPT = "génère une image cyberpunk avec néon humide"


def main() -> None:
    prompt = " ".join(sys.argv[1:]).strip() or DEFAULT_PROMPT

    print("DEBUG CWD =", os.getcwd())
    print("DEBUG COMFYUI_BAT_PATH =", os.getenv("COMFYUI_BAT_PATH"))
    print("\n====================")
    print("INPUT:", prompt)

    try:
        result = execute_request(prompt)
    except Exception as exc:
        print("\nERROR:")
        print(f"{type(exc).__name__}: {exc}")
        raise

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

    if result.get("workflow_id") is not None:
        print("\nWORKFLOW_ID:")
        print(result.get("workflow_id"))

    if result.get("artifact_path") is not None:
        print("\nARTIFACT_PATH:")
        print(result.get("artifact_path"))

    if result.get("artifact_filename") is not None:
        print("\nARTIFACT_FILENAME:")
        print(result.get("artifact_filename"))

    if result.get("comfyui_prompt_id") is not None:
        print("\nCOMFYUI_PROMPT_ID:")
        print(result.get("comfyui_prompt_id"))

    if result.get("step_results"):
        print("\nSTEP_RESULTS:")
        for item in result["step_results"]:
            print(item)


if __name__ == "__main__":
    main()