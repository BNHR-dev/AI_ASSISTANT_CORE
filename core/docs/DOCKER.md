# Docker — "runs anywhere in one command"

> Goal: `docker compose up` brings up the full AAC stack on **Windows / macOS / Linux**,
> so an evaluator can run the project **without a Linux environment and without manual setup**.
>
> The **production / dev** runtime stays **native Linux** (faster, `bwrap` isolation,
> EEVEE GPU rendering). Docker is the **reachability** path, not the production runtime.

## The four reachability tiers
1. **Hosted video / demo** — the reviewer runs nothing, they just see it works.
2. **`docker compose up`** — a technical evaluator runs the stack in one command (this document).
3. **WSL2** — a full Linux environment inside Windows.
4. **Native Linux** — the production runtime.

## Target topology
| Service | Image | Role | GPU |
|---|---|---|---|
| `aac-backend` | built (`Dockerfile`) | FastAPI + Blender + bwrap, spawns Blender locally | optional |
| `comfyui` | built (`Dockerfile.comfyui`) | ComfyUI server `:8188`, models via volume | optional |
| `ollama` | `ollama/ollama` | LLM `:11434` | optional |
| `searxng` | `searxng/searxng` | web search `:8080` | no |

Internal compose network: the backend reaches the others by **service name**
(`http://ollama:11434`, `http://searxng:8080`, `http://comfyui:8188`). Only the backend
is exposed on the host (`127.0.0.1:8000`).

## Optional GPU, CPU fallback (the cross-platform key)
- **Base** (`docker-compose.app.yml`) = **CPU-safe**, runs everywhere (even without a GPU).
- **Overlay** (`docker-compose.gpu.yml`) = adds the NVIDIA reservations.
  - Linux + NVIDIA → `docker compose -f docker-compose.app.yml -f docker-compose.gpu.yml up`
  - Windows + NVIDIA (Docker Desktop, WSL2 backend) → same overlay (CUDA via WSL2)
  - macOS / no GPU → base only = CPU (slow but functional)

## Design decisions
1. **Blender lives in the backend**, not as a separate service: the backend `subprocess`es
   it locally (as in native mode). No refactor into a network service.
2. **In-container rendering = Cycles** (CPU/GPU). EEVEE-headless-GPU stays a
   **native Linux-host** capability; the Docker demo renders with Cycles.
3. **bwrap inside a container** — two modes, no hand-waving:
   - **Demo (default)** → `AAC_BLENDER_SANDBOX=auto`: bwrap confines Blender if the container
     allows nested namespaces, otherwise it degrades gracefully (Blender still runs, but only
     the container boundary isolates it). We do **not** claim "container == bwrap".
   - **Enforced** → the `docker-compose.sandbox.yml` overlay (`make demo-secure` /
     `make demo-gpu-secure`): grants the *empirically verified minimal* privileges for nested
     bwrap — `security_opt: seccomp=unconfined` + `cap_add: [SYS_ADMIN, NET_ADMIN]` — and sets
     `AAC_BLENDER_SANDBOX=require` (fail-closed). Each of the three is necessary (removing any
     one makes the bwrap self-check fail). This is **not** `--privileged`.

   Stated tradeoff: the overlay widens the **container's** privileges (the container runs only
   our *trusted* backend) so that bwrap can **tighten** isolation around the *untrusted* `bpy`
   code — no network, no home, read-only FS, writes confined to the output dir. Net effect:
   hostile code is *more* confined than in degraded `auto` mode, where it would run with no
   sandbox at all.
4. **Models outside the image** (RealVisXL ~7 GB, ESRGAN, Ollama models): mounted as
   volumes, never baked into the image. **Full** demo: RealVisXL + refiner + ESRGAN.
5. **No separate Windows architecture.** Windows runs the *exact same* Linux containers
   through Docker Desktop's WSL2 backend — same `Dockerfile`, same `docker-compose.app.yml`,
   same in-container `bwrap`. WSL2 *is* the translation layer; nothing is reimplemented for
   Windows. (The `docker-compose.yml` / `docker-compose.linux.yml` split is only the
   *native-services* path, kept in mirror and differing solely by SELinux `:z` volume tags.)

## Prerequisites

- **Docker** with the Compose plugin (`docker compose version` ≥ v2), plus `make`, `bash`, `curl`.
- **~8 GB free disk** (≈6.6 GB of models + build layers) and an internet connection for the first run.
- **GPU (optional, recommended for images):** NVIDIA driver + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html). On Windows: Docker Desktop with the WSL2 backend. No GPU? Use `make demo` (CPU — works anywhere, slower).

## Run the stack

**One command** (does everything: SearXNG config, model download, build, up):
```bash
cd core
make demo-gpu     # NVIDIA GPU (native Linux, or Windows + Docker Desktop/WSL2) — full demo
make demo         # CPU only — runs anywhere, slow for image generation
```
`make demo` downloads RealVisXL + 4x-UltraSharp (~6.6 GB, from HuggingFace) if missing,
then brings the stack up. Backend on `http://127.0.0.1:8000`. `make down` stops it,
`make logs` follows the logs.

Manual equivalent (under the hood):
```bash
cp searxng/settings.example.yml searxng/settings.yml   # SearXNG config (required)
bash scripts/fetch-models.sh                            # models -> ./models (idempotent)
docker compose -f docker-compose.app.yml -f docker-compose.gpu.yml up --build
curl -s http://127.0.0.1:8000/health                   # -> {"status":"ok"}
```
Overrides (e.g. reuse models you already have): `cp env.docker.example .env`,
then adjust `COMFYUI_MODELS_DIR` / `COMFYUI_CHECKPOINT_NAME` (compose loads `.env`).

Generate an image end to end (OpenAI-compatible API, backend → ComfyUI):
```bash
curl -sN http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"assistant-core-image","messages":[{"role":"user",
       "content":"a cinematic photo of a red fox in a misty forest at golden hour"}]}'
```
The backend forces `image_generation`, calls ComfyUI (`comfyui:8188`), fetches the image
via `/view` and returns it as a data URI. Validated on an RTX 3060: ~45 s cold (RealVisXL
load + 30-step draft).

Each generation stores the image **and** a `manifest.json` (traceability: timestamped step
route, parameters, `runtime`/OS block) under `core/outputs/comfyui/<request_id>/` on the
host (bind mount). Files are owned by the host user (`AAC_UID:AAC_GID`, default 1000; the
root backend `chown`s them).

## Implementation status
- **Backend containerized** — `Dockerfile` (Python 3.14 + `requirements.txt` + app); `up`
  → `/health` OK, talks to Ollama/SearXNG. Blender **5.1.1** + **bubblewrap** in the image
  (validated: `blender --version` + headless **Cycles CPU** render inside the container).
- **ComfyUI containerized** — `Dockerfile.comfyui` (python:3.14-slim, ComfyUI pinned, torch
  via `TORCH_CHANNEL` build-arg), models on a **read-only** volume, output on a volume
  **shared** with the backend. **Zero custom nodes** (the `cinematic_scene_v1` workflow uses
  only core nodes). `cp314` wheels exist on both cpu and cu128, so Python 3.14 is kept.
- **GPU overlay** — `docker-compose.gpu.yml` (cu128 channel + NVIDIA reservation). Validated
  on an RTX 3060: `cuda.is_available()=True`, RealVisXL generation inside the container.
- **Cross-platform** — full-stack `up` (backend+comfyui+ollama **healthy**) + validated
  **backend→ComfyUI round-trip** via the API. Windows/WSL2 documented, not yet tested here.
- **One-command UX** — `make demo`, model-fetch script, the four reachability tiers above.

## Known risks
- **Model size** = the real "works out of the box" friction (full demo).
- **PyTorch on Python 3.14** — resolved: `cp314` wheels on cpu (`torch 2.12.1`) and cu128
  (`torch 2.11.0`), and every ComfyUI requirement has a cp314 wheel → no compilation.
- **Windows GPU prerequisites**: Docker Desktop + WSL2 backend + NVIDIA driver (else CPU).
- **Nested bwrap** = needs container privileges (otherwise `auto`/`off` in the demo).
- **SearXNG**: requires `core/searxng/settings.yml` (gitignored because it holds a secret) —
  `cp` it from `settings.example.yml`. The template enables `format: json` (required by the
  backend) and `limiter: false` (internal access). Without the file, the service crash-loops
  (exit 127).
