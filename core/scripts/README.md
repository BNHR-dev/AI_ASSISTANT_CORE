# Scripts

Operational helpers for the AAC stack.

- `fetch-models.sh` — downloads the demo image models (RealVisXL V5.0 + 4x-UltraSharp) from
  HuggingFace into `$COMFYUI_MODELS_DIR` (default `./models`). Public models, no token,
  idempotent. Invoked automatically by `make demo` / `make fetch-models`.
- `fetch-ollama-models.sh` — pulls the LLM models the router needs (`qwen3:8b`,
  `qwen2.5-coder:7b`, `qwen2.5vl:3b`). Auto-detects native `ollama` vs the demo container
  (`AAC_OLLAMA_MODE=native|docker` to force). Idempotent. Run via `make pull-llms`.
- `check-deps.sh` — preflight « doctor ». Reports ✓/✗ for Docker, Ollama + models, ComfyUI
  models, Blender and SearXNG, with a fix hint per item. Installs nothing. Run via `make doctor`.
- `init_local_config.py` — creates a local `core/.env` and `searxng/settings.yml` from the
  committed examples, generating secrets when missing.
- `windows/bootstrap.ps1` + `windows/Install-AAC.bat` — **virgin Windows** bootstrap. Installs
  everything natively (winget: Git/Python/7-Zip/Ollama/Blender, ComfyUI portable, image + LLM
  models, backend venv, `core/.env`) — no Docker. Idempotent; self-elevates; `-CheckOnly` acts
  as a doctor. See [`../docs/DEPENDENCIES.md`](../docs/DEPENDENCIES.md).

Full dependency guide: [`../docs/DEPENDENCIES.md`](../docs/DEPENDENCIES.md).
