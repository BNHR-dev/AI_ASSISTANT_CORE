# Dependencies & preflight

Everything AAC needs, what installs it, and how to check it. Three paths:

- **Windows, from scratch** — one script installs the missing services natively (no Docker). See below.
- **Docker demo** (`make demo`) — services come as containers; you only fetch *models*.
- **Native** (development / production) — you install the services and tools yourself.

## Virgin Windows machine — one click

On a fresh Windows 10/11, run `scripts\windows\Install-AAC.bat` (double-click, or from
a terminal). It detects what is present and installs the missing pieces **natively and
idempotently** — no Docker required (Docker Desktop, when running, is used only for SearXNG):

| Installed | Via |
|---|---|
| Ollama + the 3 generation models (not `bge-m3` — see below) | direct download (ollama.com) + `ollama pull` |
| ComfyUI portable + image models | GitHub release + HuggingFace (needs 7-Zip already present) |
| SearXNG | Docker Desktop **if running**, otherwise skipped (web pipeline off) |
| Backend venv + `core\.env` + launch scripts | `python -m venv` + `pip` |

**Prerequisites it checks but does not install:** Python 3.11–3.13 (hard requirement —
the script stops without it), 7-Zip (only needed to extract ComfyUI), Blender (only
needed for the 3D pipeline — see the Blender section below), Git (optional).

```bat
scripts\windows\Install-AAC.bat              REM full install
scripts\windows\Install-AAC.bat -CheckOnly   REM "doctor" — checks only, installs nothing
scripts\windows\Install-AAC.bat -SkipComfyUI REM skip the heaviest phase
```

Honest limits: this native path is **not sandboxed** (no bubblewrap on Windows) — for the
hardened/secure path use **Docker Desktop + WSL2** (`run.bat`). **SearXNG** has no clean
native Windows install (web pipeline stays off — use Docker Desktop for it); **GPU drivers**
are assumed already present; ComfyUI is best-effort (its failure does not block the core).
When done, the script prints the backend start command.

The model-only helpers below (`make …`) are the Linux / WSL2 / macOS path.

Run the preflight any time to see exactly what is present or missing:

```bash
make doctor      # ✓ present  ✗ missing  ~ optional/degraded — from the repo root, installs nothing
```

## The four dependency groups

| Group | What | Demo (Docker) | Native |
|---|---|---|---|
| **Services** | Ollama, SearXNG, ComfyUI | containers via `docker compose` | install on host (below) |
| **Ollama models** | 3 generation LLMs (`qwen3:8b`, `qwen2.5-coder:7b`, `qwen2.5vl:3b`) + `bge-m3` (embeddings) | `make pull-llms` | `make pull-llms` |
| **Image models** | RealVisXL V5.0 + 4x-UltraSharp | `make fetch-models` | `make fetch-models` |
| **Blender** | headless `bpy` runtime (3D pipeline, optional) | runs in backend container — install in image | install on host (below) |

One command for all model downloads (idempotent, safe to re-run):

```bash
make deps        # = fetch-models + pull-llms
```

## Ollama models — `make pull-llms`

`make pull-llms` pulls the four models listed in `scripts/models.manifest` — three
generation LLMs plus one **embedding model** (the single source of truth for model
names; roles resolve in `app/engine/task_routing.py` and `app/infra/ollama_runtime.py`):

| Model | Role |
|---|---|
| `qwen3:8b` | chat / routing / explanation / critique / architecture |
| `qwen2.5-coder:7b` | code build + Blender `bpy` generation |
| `qwen2.5vl:3b` | vision (VLM) |
| `bge-m3` | embeddings — semantic router fallback (**optional**: absent ⇒ rule-only routing, flagged in `/health/runtime`) |

The native installers (`scripts/linux/bootstrap.sh`, `scripts\windows\Install-AAC.bat`)
pull the three generation models only — `make pull-llms` (manifest-driven) is what adds
`bge-m3`. Without it the semantic router layer degrades gracefully to rules.

`scripts/linux/fetch-ollama-models.sh` auto-detects the mode:

- **native** — uses the host `ollama` binary (install: <https://ollama.com/download>)
- **docker** — pulls into the running demo container (`docker/docker-compose.app.yml`)

Force a mode with `AAC_OLLAMA_MODE=native|docker make pull-llms`. Already-present models are skipped.

## Image models (ComfyUI) — `make fetch-models`

Public HuggingFace models, no token, into `$COMFYUI_MODELS_DIR` (default `docker/models/`):

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
- **SearXNG** — `make setup` writes `docker/searxng/settings.yml` from the committed example

The default topology binds every service to `127.0.0.1` — see [`ARCHITECTURE.md`](../core/docs/ARCHITECTURE.md).
