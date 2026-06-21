# Dependencies & preflight

Everything AAC needs, what installs it, and how to check it. Three paths:

- **Windows, from scratch** — one script installs *everything* natively (no Docker). See below.
- **Docker demo** (`make demo`) — services come as containers; you only fetch *models*.
- **Native** (development / production) — you install the services and tools yourself.

## Virgin Windows machine — one click

On a fresh Windows 10/11, run `core\scripts\windows\Install-AAC.bat` (double-click, or from
a terminal). It self-elevates and installs **everything natively, no Docker**, idempotently:

| Installed | Via |
|---|---|
| Git, Python 3.12, 7-Zip | winget (App Installer, built into Windows) |
| Ollama + the 3 LLM models | winget + `ollama pull` |
| Blender (3D pipeline) | winget |
| ComfyUI portable + image models | GitHub release + HuggingFace |
| Backend venv + `core\.env` | `python -m venv` + `pip` |

```bat
core\scripts\windows\Install-AAC.bat              REM full install
core\scripts\windows\Install-AAC.bat -CheckOnly   REM "doctor" — checks only, installs nothing
core\scripts\windows\Install-AAC.bat -SkipComfyUI REM skip the heaviest phase
```

Honest limits: **SearXNG** has no clean native Windows install (web pipeline stays off — use
Docker Desktop for it); **GPU drivers** are assumed already present; ComfyUI is best-effort
(its failure does not block the core). When done, the script prints the backend start command.

The model-only helpers below (`make …`) are the Linux / WSL2 / macOS path.

Run the preflight any time to see exactly what is present or missing:

```bash
cd core
make doctor      # ✓ present  ✗ missing (blocking)  ~ optional/degraded — installs nothing
```

## The four dependency groups

| Group | What | Demo (Docker) | Native |
|---|---|---|---|
| **Services** | Ollama, SearXNG, ComfyUI | containers via `docker compose` | install on host (below) |
| **LLM models** | `qwen3:8b`, `qwen2.5-coder:7b`, `qwen2.5vl:3b` | `make pull-llms` | `make pull-llms` |
| **Image models** | RealVisXL V5.0 + 4x-UltraSharp | `make fetch-models` | `make fetch-models` |
| **Blender** | headless `bpy` runtime (3D pipeline, optional) | runs in backend container — install in image | install on host (below) |

One command for all model downloads (idempotent, safe to re-run):

```bash
make deps        # = fetch-models + pull-llms
```

## LLM models (Ollama) — `make pull-llms`

The router needs three models — source of truth is `app/engine/task_routing.py`:

| Model | Role |
|---|---|
| `qwen3:8b` | chat / routing / explanation / critique / architecture |
| `qwen2.5-coder:7b` | code build + Blender `bpy` generation |
| `qwen2.5vl:3b` | vision (VLM) |

`scripts/fetch-ollama-models.sh` auto-detects the mode:

- **native** — uses the host `ollama` binary (install: <https://ollama.com/download>)
- **docker** — pulls into the running demo container (`docker-compose.app.yml`)

Force a mode with `AAC_OLLAMA_MODE=native|docker make pull-llms`. Already-present models are skipped.

## Image models (ComfyUI) — `make fetch-models`

Public HuggingFace models, no token, into `$COMFYUI_MODELS_DIR` (default `core/models/`):

- `checkpoints/RealVisXL_V5.0_fp16.safetensors` (~6.5 GB)
- `upscale_models/4x-UltraSharp.pth` (~64 MB)

Already have them elsewhere? Point `COMFYUI_MODELS_DIR` at your existing ComfyUI `models/` dir.

## Blender (optional 3D pipeline)

Blender runs **headless on the host** (native) or inside the backend image (demo). The core
router → planner → executor works without it; only the `blender_pipeline` strategy needs it.

After install, tell AAC where the binary is:

```bash
export BLENDER_EXE=/path/to/blender   # `make doctor` reports the detected version
```

| OS | Install |
|---|---|
| Fedora | `sudo dnf install blender` — or the official tarball from <https://www.blender.org/download/> for a pinned version |
| Debian/Ubuntu | `sudo snap install blender --classic` — or the official tarball |
| Windows | installer from <https://www.blender.org/download/> (or `winget install BlenderFoundation.Blender`); set `BLENDER_EXE` to `blender.exe` |
| macOS | `brew install --cask blender` |

GPU rendering (OptiX on the RTX 3060) needs the NVIDIA driver — see
[`SETUP_LINUX.md`](SETUP_LINUX.md).

## Services, native

For a full native runtime (no Docker), see [`SETUP_LINUX.md`](SETUP_LINUX.md). In short:

- **Ollama** — <https://ollama.com/download>, then `make pull-llms`
- **ComfyUI** — clone + install from <https://github.com/comfyanonymous/ComfyUI>, then `make fetch-models`
- **SearXNG** — `make setup` writes `searxng/settings.yml` from the committed example

The default topology binds every service to `127.0.0.1` — see [`ARCHITECTURE.md`](ARCHITECTURE.md).
