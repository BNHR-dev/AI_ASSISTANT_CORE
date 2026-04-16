from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.clients.comfyui_client import run_comfyui_workflow


DEFAULT_PROMPT = "génère une image cyberpunk avec néon humide"


def main() -> None:
    prompt = " ".join(sys.argv[1:]).strip() or DEFAULT_PROMPT

    print("DEBUG CWD =", os.getcwd())
    print("DEBUG COMFYUI_BAT_PATH =", os.getenv("COMFYUI_BAT_PATH"))
    print("\n====================")
    print("DIRECT COMFYUI INPUT:", prompt)

    try:
        result = run_comfyui_workflow(prompt)
    except Exception as exc:
        print("\nERROR:")
        print(f"{type(exc).__name__}: {exc}")
        raise

    print("\nRESULT:")
    for key in [
        "status",
        "workflow_id",
        "filename",
        "output_path",
        "prompt_id",
        "parameters",
    ]:
        print(f"{key}: {result.get(key)}")


if __name__ == "__main__":
    main()