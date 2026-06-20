# Scripts

Operational helpers for the AAC stack.

- `fetch-models.sh` — downloads the demo models (RealVisXL V5.0 + 4x-UltraSharp) from
  HuggingFace into `$COMFYUI_MODELS_DIR` (default `./models`). Public models, no token,
  idempotent. Invoked automatically by `make demo` / `make fetch-models`.
- `init_local_config.py` — creates a local `core/.env` and `searxng/settings.yml` from the
  committed examples, generating secrets when missing.
