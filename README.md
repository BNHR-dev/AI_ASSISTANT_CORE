# AI_ASSISTANT_CORE

**A local-first AI orchestrator for creative and 3D-production work.**

[![tests](https://github.com/BNHR-dev/AI_ASSISTANT_CORE/actions/workflows/tests.yml/badge.svg)](https://github.com/BNHR-dev/AI_ASSISTANT_CORE/actions/workflows/tests.yml) [![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE) ![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white) ![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)

AI_ASSISTANT_CORE (AAC) takes a natural-language request, makes a structured routing decision, builds an explicit execution plan, runs it step by step across local models and creative tools, and returns a result you can trace end to end.

It is not a thin wrapper around a chat model. The core is a real orchestration loop — **router → planner → executor** — with an observability surface on top, and it runs entirely on your own hardware.

> **Status.** The core (routing, planning, execution, OpenAI-compatible API) is stable and test-covered. The Blender pipeline is experimental but functional — and its LLM quality is **measured, not asserted**: a reproducible, multi-model baseline lives in [`BENCHMARK.md`](BENCHMARK.md). Limitations are stated plainly under [Roadmap](#roadmap) — nothing here is oversold.

## Quickstart — from zero to running, one command

**The only thing you install yourself is [Docker](https://docs.docker.com/get-docker/)** (with the Compose v2 plugin). No Python, no models, no manual setup — the launcher fetches and builds everything.

```bash
git clone https://github.com/BNHR-dev/AI_ASSISTANT_CORE.git aac
cd aac
./run.sh          # Linux / WSL2 / macOS    —    Windows: run.bat (Docker Desktop + WSL2)
```

`run.sh` writes the SearXNG config, downloads the models (~20 GB on first run), builds the images, brings up the **hardened** stack, then verifies every service is *actually* healthy before opening the Console. The NVIDIA GPU is auto-detected (CPU fallback otherwise).

When you see `== OK — stack ready ==`, open **<http://127.0.0.1:8000/console>** and try a prompt, an image, or *"create a Blender scene with a cube"* (3D → `scene.blend` + `preview.png`). Stop with `./run.sh --down`.

> Validated end to end from a **bare clone with only Docker installed** — models downloaded, images built, hardened stack healthy, a real Blender render — nothing pre-staged.

## Why local-first

AAC targets **3D animation studios** and creative pipelines, where the material is confidential by default: unreleased films, client assets, work under NDA and content-security regimes (MPA/TPN-style). That constraint drives the design:

- **Inference and generation stay on the host.** LLM, vision and image/3D generation all run locally; the only outbound path is the optional web-search pipeline.
- **Generated code is treated as untrusted.** AAC writes and runs code (`bpy` scripts for Blender), and confines that execution at the OS level. **Native Linux** uses [bubblewrap](https://github.com/containers/bubblewrap): no network, no home, a read-only system, writes restricted to a single output directory; it can be made mandatory (`AAC_BLENDER_SANDBOX=require`, fail-closed). The **recommended cross-platform path is Docker** (incl. Windows via the WSL2 backend): there the **hardened container itself is the confinement boundary** — `cap_drop: ALL`, `no-new-privileges`, read-only rootfs, no extra privileges (`AAC_BLENDER_SANDBOX=off`) — the Docker confinement *replaces* bubblewrap in that mode, rather than running it. (A bubblewrap-without-`SYS_ADMIN` path is proven under rootless Podman and documented in [`SECURITY.md`](SECURITY.md), but not yet wired in.) The scope is deliberate: only the LLM-generated Blender code is treated as hostile; ComfyUI runs fixed, user-authored workflows on the host. Stronger isolation — a dedicated VM, ComfyUI confinement and CPU/RAM quotas — is on the [roadmap](#roadmap), not yet shipped, and not presented as if it were.

## How it works

One readable path from request to output:

```
task_classifier → routing → tool_selector → planner → step_executor → result_assembler
```

- **Router** — classifies the request, then selects an agent, a model, and a tool when one is needed.
- **Planner** — turns that decision into an explicit, inspectable plan.
- **Executor** — runs the plan step by step and assembles the final output.
- **Observability** — every run exposes enough trace to replay the decision after the fact.

Execution strategies are explicit, not implicit:

| Strategy | For |
|---|---|
| `single_step` | direct answers, simple build |
| `two_step_llm` | explain-then-refine |
| `web_pipeline` | SearXNG search + LLM synthesis |
| `visual_pipeline` | prompt-intent analysis → ComfyUI |
| `blender_pipeline` | natural language → `bpy` → headless Blender |

## What it does

- Explanation (plain / advanced), code-oriented build, critique, architecture
- Web research via SearXNG with LLM synthesis
- Vision through a local VLM (`qwen2.5vl`)
- Image generation via ComfyUI — subject, render-intent and style analysis drive workflow selection
- Blender pipeline (experimental): generates `scene.py`, runs Blender headless, produces a canonical `scene.blend` plus a best-effort `preview.png` rendered in a separate subprocess
- OpenAI-compatible API — drops straight into Open-WebUI and other OpenAI clients

## Stack — everything on `127.0.0.1`

| Service | Role |
|---|---|
| Ollama | local LLM inference |
| SearXNG | private web search |
| ComfyUI | image generation |
| Blender (headless) | 3D scene generation |
| FastAPI | the orchestrator + API |

Single-host and GPU-accelerated (built on an RTX 3060). Runs on **Linux** (Fedora, validated) and **Windows** (Docker Desktop).

## API

OpenAI-compatible layer:
- `GET /v1/models`
- `POST /v1/chat/completions` — `content` is always a string

Native surface:
- `POST /route` — inspect the routing decision for a request
- `POST /execute` — run the full pipeline
- `GET /health` · `GET /health/runtime` · `GET /debug/canonical`

## Run the demo — one command, any OS

No Linux box, no manual setup: the whole stack (FastAPI backend + Ollama + SearXNG +
ComfyUI) ships as containers. From the repo:

```bash
./run.sh          # Linux / WSL2 / macOS — hardened, GPU autodetect, honest health gate
run.bat           # Windows — Docker Desktop + WSL2 (delegates to run.ps1)
```

`run.sh` downloads the image models (RealVisXL + 4x-UltraSharp, ~6.6 GB, from HuggingFace),
writes the config, brings the **hardened** stack up, and verifies every service is *actually*
healthy before opening the Console. The backend is then on `http://127.0.0.1:8000` —
generate straight through the OpenAI-compatible API:

```bash
curl -sN http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"assistant-core-image","messages":[{"role":"user",
       "content":"a tranquil japanese zen garden at dawn, cinematic"}]}'
```

**Four tiers of reachability** — pick the lowest-effort one that fits:

| Tier | You run | Needs |
|---|---|---|
| Hosted video / demo | nothing, just watch | a browser |
| **`./run.sh`** (this) | one command | Docker (+ NVIDIA toolkit for GPU) |
| WSL2 | a full Linux env inside Windows | WSL2 |
| Native | the production runtime | Linux + GPU |

GPU prerequisites, the hardened-container security model, the per-run output layout and the
end-to-end flow → [`docs/DOCKER.md`](docs/DOCKER.md) · security → [`SECURITY.md`](SECURITY.md).

## Quickstart — native services (development)

```bash
cp core/.env.example core/.env

# Native-services stack (Fedora — SELinux :z labels, host GPU via nvidia-container-toolkit)
docker compose -f docker/docker-compose.linux.yml up -d
```

Then start the FastAPI backend on `127.0.0.1:8000`.
Full setup → [`docs/SETUP_LINUX.md`](docs/SETUP_LINUX.md) · Windows → [`docs/SETUP_WINDOWS.md`](docs/SETUP_WINDOWS.md)

## Dependencies

Services come as containers with `make demo`; you only fetch the **models**. Everything is
idempotent and a single preflight tells you exactly what is missing:

```bash
cd core
make deps        # download all models: ComfyUI (RealVisXL + 4x-UltraSharp) + Ollama LLMs
make doctor      # preflight — checks Docker / Ollama models / image models / Blender / SearXNG
```

| Group | Command | Notes |
|---|---|---|
| LLM models | `make pull-llms` | `qwen3:8b`, `qwen2.5-coder:7b`, `qwen2.5vl:3b` — native `ollama` or demo container, auto-detected |
| Image models | `make fetch-models` | RealVisXL V5.0 + 4x-UltraSharp from HuggingFace (no token) |
| Blender (3D, optional) | host install | set `BLENDER_EXE`; the core runs without it |

**Virgin Windows machine?** `core\scripts\windows\Install-AAC.bat` installs *everything*
natively (winget: Ollama + models, Blender, ComfyUI, Python venv) — no Docker. Run it with
`-CheckOnly` to use it as a doctor.

What each pipeline needs, native service installs, the Windows one-click and the Blender
per-OS guide → [`core/docs/DEPENDENCIES.md`](core/docs/DEPENDENCIES.md).

## Roadmap

- **Stronger isolation** — generated `bpy`/Blender code already runs OS-confined (bubblewrap: no network, no home, read-only system). Next hardening steps: a dedicated VM, ComfyUI confinement, and CPU/RAM quotas — so untrusted code can never touch confidential assets and can't exhaust the host. The studio-confidentiality requirement, taken further — a goal, not a shipped guarantee.
- Output quality — stronger prompts, more consistent results across pipelines
- Richer Blender templates (materials, lighting, composition) and multi-object / animation workflows

## License

Copyright (c) 2026 BNHR-dev

[AGPL-3.0](LICENSE) — derivatives, including networked/SaaS use, must stay open. Commercial licensing is available from the author.
