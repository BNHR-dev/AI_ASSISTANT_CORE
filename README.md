# AI_ASSISTANT_CORE

**A local-first AI orchestrator for creative and 3D-production work.**

[![tests](https://github.com/BNHR-dev/AI_ASSISTANT_CORE/actions/workflows/tests.yml/badge.svg)](https://github.com/BNHR-dev/AI_ASSISTANT_CORE/actions/workflows/tests.yml) [![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE) ![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white) ![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)

AI_ASSISTANT_CORE (AAC) takes a natural-language request, makes a structured routing decision, builds an explicit execution plan, runs it step by step across local models and creative tools, and returns a result you can trace end to end.

It is not a thin wrapper around a chat model. The core is a real orchestration loop — **router → planner → executor** — with an observability surface on top, and it runs entirely on your own hardware.

> **Status.** The core (routing, planning, execution, OpenAI-compatible API) is stable and test-covered. The Blender pipeline is experimental but functional. Limitations are stated plainly under [Roadmap](#roadmap) — nothing here is oversold.

## Why local-first

AAC targets **3D animation studios** and creative pipelines, where the material is confidential by default: unreleased films, client assets, work under NDA and content-security regimes (MPA/TPN-style). That constraint drives the design:

- **Inference and generation stay on the host.** LLM, vision and image/3D generation all run locally; the only outbound path is the optional web-search pipeline.
- **Generated code is treated as untrusted.** AAC writes and runs code (e.g. `bpy` scripts for Blender). Isolating that execution from confidential assets — in a dedicated VM — is on the [roadmap](#roadmap), not yet shipped, and not presented as if it were.

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

## Quickstart

```bash
cp core/.env.example core/.env

# Linux (Fedora — validated): native GPU via nvidia-container-toolkit
docker compose -f core/docker-compose.linux.yml up -d

# Windows (Docker Desktop): same localhost endpoints, GPU through the WSL2 backend
docker compose -f core/docker-compose.yml up -d
```

Then start the FastAPI backend on `127.0.0.1:8000`.
Full setup → [`core/docs/SETUP_LINUX.md`](core/docs/SETUP_LINUX.md) · Architecture → [`core/docs/ARCHITECTURE.md`](core/docs/ARCHITECTURE.md)

## Roadmap

- **Isolation VM** — run generated code (`bpy`/Blender) in a dedicated, sandboxed VM so untrusted code never touches confidential assets. The studio-confidentiality requirement made concrete — a goal, not a shipped guarantee.
- Output quality — stronger prompts, more consistent results across pipelines
- Richer Blender templates (materials, lighting, composition) and multi-object / animation workflows

## License

Copyright (c) 2026 BNHR-dev

[AGPL-3.0](LICENSE) — derivatives, including networked/SaaS use, must stay open. Commercial licensing is available from the author.
