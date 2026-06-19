# Architecture — AI_ASSISTANT_CORE

## Overview

AI_ASSISTANT_CORE is a local orchestration core.

The system takes a user request, produces a **structured decision**, turns it into an
**execution plan**, runs that plan step by step, then returns a **useful final output**
with enough of an **observability surface** to trace what happened.

The right abstraction for the project is not "a routing backend" but:
- decision
- plan
- execution
- assembly
- observability

## Canonical core diagram

```text
User
↓
Open-WebUI / HTTP client / OpenAI-compatible API
↓
app/main.py
↓
router_service
├── task_classifier
├── TASK_ROUTING
├── routing_conditions
└── tool_selector
↓
plan_builder / planner_service
↓
step_executor
├── llm_primary
├── llm_secondary
├── tool_web_search
├── llm_synthesis
├── prepare_visual
└── tool_comfyui
↓
result_assembler
↓
ExecuteResponse / final response
```

## The real layers

### 1. API entry
- `app/main.py`
- `openai_compat.py`

Role: expose the FastAPI surface and the minimal OpenAI compatibility for Open-WebUI.

### 2. Understanding and decision
- `app/task_classifier.py`
- `app/tool_selector.py`
- `app/engine/task_routing.py`
- `app/engine/routing_conditions.py`
- `app/engine/router_service.py`

Role: recognize the task, select agent/model/tool, apply the limited hybrid rules, and
produce a readable final decision.

### 3. Planning
- `app/engine/planner_types.py`
- `app/engine/plan_builder.py`
- `app/engine/planner_service.py`
- `app/engine/state_store.py`

Role: turn the decision into an explicit `ExecutionPlan`.

### 4. Execution
- `app/engine/step_executor.py`
- `app/engine/executor.py`

Role: run the steps in order, trace results, and surface the useful metadata.

### 5. Assembly
- `app/engine/result_assembler.py`
- `app/engine/output_contracts.py`

Role: keep the final output useful and hide intermediate technical noise from the user.

### 6. Runtime and observability
- `app/engine/runtime_debug.py`
- `app/infra/tool_manager.py`

Role: expose runtime health and the canonical boundaries, without hiding business logic in them.

## Execution strategies

The planner currently produces five real strategies:
- `single_step`
- `two_step_llm`
- `web_pipeline`
- `visual_pipeline`
- `blender_pipeline`

## Deployment architecture (single-host)

### Product core
The product core stays:
- router
- planner
- executor
- observability

The single-host deployment adds no business logic. Isolating the execution of generated
code remains a **product goal** (internal audit, finding C1) — not yet shipped, and not to
be presented as a boundary that is already in place.

### Product runtime (single-host, localhost)
The entire canonical runtime runs on a single machine and communicates over `localhost`
(`127.0.0.1`). An older topology — an Ubuntu/Linux guest on a Windows host (Hyper-V) — is
archived under `infra/vm/`, outside the canonical runtime.

#### On the host
- AI_ASSISTANT_CORE backend (FastAPI), bound to `127.0.0.1:8000`
- ComfyUI — assumed reachable at `127.0.0.1:8188`
- Blender — run headless directly on the host (NVIDIA GPU)

#### In containers (`docker-compose.linux.yml`, ports bound to `127.0.0.1`)
- Ollama — local LLM (`127.0.0.1:${OLLAMA_PORT} -> 11434`)
- SearXNG — web search (`127.0.0.1:8081 -> 8080`)
- Open-WebUI (optional) — operator UI, **outside the canonical runtime** (not required for the core)

### Product security boundary
- single-host deployment: no dedicated network isolation boundary today
- isolating the execution of generated code remains a **product goal** (audit finding C1),
  not yet shipped — not to be overstated as done
- **roadmap**: isolate generated-code execution in a **dedicated isolation VM** (Linux, on
  the host), driven by studio asset confidentiality — distinct from the archived Hyper-V topology

All services listen on `127.0.0.1` (backend `8000`, Ollama `12000`, SearXNG `8081`,
ComfyUI `8188`, optional Open-WebUI `8088`).

## Blender subsystem (experimental pipeline)

The Blender pipeline lives in the clients/tools layer without changing the router +
planner + executor core.

- `app/clients/blender_client.py` — headless Blender execution on the host
- the existing planner/executor routes to `blender_pipeline` for 3D requests
- files are produced under `outputs/blender/<uuid>/`:
  - `scene.py` — generated bpy script
  - `scene.blend` — canonical artifact
  - `preview.png` — best-effort render, produced in a separate subprocess
- the preview render is isolated in a second subprocess so it cannot pollute the main
  script and so `scene.blend` stays the reference artifact
- `preview.png` must never make the overall pipeline blocking
- `/health/runtime` may stay `partial` if ComfyUI is unavailable, without blocking Blender

## Visual subsystem

The visual pipeline was refined without changing the general core.

### Visual intent analysis
The system analyzes:
- `subject_type`: `portrait`, `product`, `scene`
- `render_intent`: `standard`, `packshot`, `poster`, `cover`, `key_visual`
- `style_flags`: style signals used to enrich the prompt

### Workflow selection
Current mapping:
- `portrait` → `portrait_basic_v1`
- `product` → `object_basic_v1`
- `scene` → `cinematic_scene_v1`

### Visual prompt enrichment
`app/clients/comfyui_client.py` enriches the positive prompt based on:
- the subject
- the render intent
- the style flags
- the chosen workflow

### Variants and partial success
The visual pipeline now surfaces:
- `artifact_path` and `artifact_filename`
- `artifact_paths` and `artifact_filenames`
- `workflow_id`
- `variants_count`
- `completed_variants`
- `partial_visual_success`
- `comfyui_status`
- `comfyui_prompt_id`

## API and external surface

### Canonical FastAPI
- `GET /health`
- `GET /health/runtime`
- `GET /debug/canonical`
- `POST /route`
- `POST /execute`

### OpenAI-compatible
- `GET /v1/models` — static model cards (`MODEL_TO_MODE`); unknown model ID → `auto` fallback
- `POST /v1/chat/completions` — `choices[0].message.content` is **always a string**

This layer interfaces with Open-WebUI without coupling to that specific tool.

For `artifact_type == "image"` results, the content is a markdown data-URI when the image is
retrievable and its `Content-Type` is `image/*`:
- **HTTP branch** (`artifact_view_url(s)`): download from ComfyUI `/view` → `![filename](data:<mime>;base64,...)`
- **local branch** (`artifact_path(s)`): filesystem read → same embed
- non-image `Content-Type` → rejected, text fallback "not retrievable from ComfyUI"
- `MAX_EMBED_IMAGES = 4` — `MAX_EMBED_BYTES_PER_IMAGE = 4 MiB` — `COMFYUI_VIEW_TIMEOUT` env var (default 15s)

## Canonical vs legacy

Source of truth:
1. `app/*`
2. `openai_compat.py`
3. docs `docs/*`

Confirmed legacy shims at the repo root:
- `executor.py`
- `router_service.py`
- `task_classifier.py`
- `tool_selector.py`
- `task_routing.py`
- `comfyui_client.py`

These files must not become business sources again.

## Known structural gaps

- isolation/sandbox of generated-code execution: a **product goal**, not shipped (audit finding C1)
- legacy debt still present, though contained
- Open-WebUI is an optional operator UI on the host, non-canonical and not required for the core

## `/debug/canonical` surface and module classification

The `/debug/canonical` surface exposes the canonical classification of `app/*` code in three lists:
- `ACTIVE_RUNTIME_MODULES` — code carrying the **decision → plan → execution → output** flow
- `ACTIVE_AUXILIARY_MODULES` — technical support code used **by** the runtime but not part of
  the flow (clients, health checks, URLs)
- `DORMANT_MODULES` — present in the repo but not imported by the real flow (superseded
  internals, legacy snapshots, unused helpers)

The three lists are defined in `app/engine/runtime_debug.py` and must form an **exhaustive,
disjoint** partition of every `app/*.py` (excluding `__init__.py`).

### Structural lock

This classification is locked by `tests/test_runtime_debug_classification.py`, which checks,
without mocks:
- every listed module actually exists on disk
- the three lists are pairwise disjoint
- every `app/*.py` (excluding `__init__.py`) appears in **exactly one** of the three lists
- the critical flow modules (`task_classifier`, `router_service`, `planner_service`,
  `plan_builder`, `executor`, `step_executor`, `result_assembler`, etc.) stay in `ACTIVE_RUNTIME_MODULES`
- the root legacy shims do not leak into the `app/*` lists
- the `get_canonical_boundaries()` payload exposes the module constants

Consequence: any new file under `app/` that is classified as neither runtime, auxiliary, nor
dormant fails the tests with a clear message naming the file. This prevents silent drift
between what actually lives in the code and what the debug surface reports.

## Architecture decision to preserve

The project should keep evolving as a **router + planner + executor** core, through
incremental, testable, visible improvements — rather than large refactors or premature new layers.
