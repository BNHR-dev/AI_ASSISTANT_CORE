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
Open-WebUI / HTTP client / OpenAI-compatible API / local Console
↓
app/main.py
↓
router_service
├── task_classifier        (weighted keyword rules — always first)
├── router_embeddings      (semantic fallback, only in the rules' dead zone)
├── TASK_ROUTING
├── routing_conditions
└── tool_selector
↓
plan_builder / planner_service
↓
executor ── per-run lock (run_locks) · event journal (run_events)
│           per-step checkpoint (run_state) · declared retry · HITL pause
↓
step_executor
├── llm_primary
├── llm_secondary
├── tool_web_search
├── llm_synthesis
├── prepare_visual
├── tool_comfyui
├── prepare_blender_script
└── tool_blender
↓
result_assembler
↓
ExecuteResponse / final response
   ↘ outputs/runs/<request_id>/  (events.jsonl + state.json)
   ↘ v2 manifests with a repro block → POST /reproduce replay & verdict
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
- `app/engine/router_embeddings.py`

Role: recognize the task, select agent/model/tool, apply the limited hybrid rules, and
produce a readable final decision.

The router is hybrid with a strict precedence: the keyword rules always win. The
embeddings classifier (`router_embeddings`, bge-m3 via Ollama + logistic weights) only
speaks in the rules' dead zone — when no rule scores — and the system degrades to the
historical rule-only behavior whenever the embedding model or the trained weights are
unavailable.

### 3. Planning
- `app/engine/planner_types.py`
- `app/engine/plan_builder.py`
- `app/engine/planner_service.py`
- `app/engine/execution_state_factory.py`

Role: turn the decision into an explicit `ExecutionPlan`.

### 4. Execution
- `app/engine/step_executor.py`
- `app/engine/executor.py`

Role: run the steps in order, trace results, and surface the useful metadata.

The executor also owns the run lifecycle guarantees: a per-run execution lock
(`run_locks`), an event journal entry per transition (`run_events`), a checkpoint after
every step (`run_state`), bounded declarative retry (`max_attempts` per step), and the
human-in-the-loop pause (`pause_before_tools` → the run stops before each tool step;
each resume approves the next gated step only).

### 5. Assembly
- `app/engine/result_assembler.py`
- `app/engine/output_contracts.py`

Role: keep the final output useful and hide intermediate technical noise from the user.

### 6. Runtime and observability
- `app/engine/runtime_debug.py`
- `app/infra/tool_manager.py`

Role: expose runtime health and the canonical boundaries, without hiding business logic in them.

### 7. Run persistence, reproducibility and replay
- `app/engine/run_events.py` — append-only event journal per run
- `app/engine/run_state.py` — atomic per-step checkpoint, consumed by `/resume`
- `app/engine/run_identity.py` — the single canonical `request_id` contract
- `app/engine/run_locks.py` — per-process, per-run execution lock
- `app/engine/repro.py` — capture-side hashing (semantic, pixel, perceptual)
- `app/engine/reproduce.py` — replay engine behind `/reproduce`

Role: make every run traceable on disk, resumable after interruption, and replayable
with an explicit verdict. See the dedicated section below.

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

The single-host deployment adds no business logic. Generated `bpy` code is confined at the
OS level with **bubblewrap** (no network, no home, read-only system); a stronger
**VM-grade isolation** remains a product goal — not yet shipped, and not presented as if it were.

### Product runtime (single-host, localhost)
The entire canonical runtime runs on a single machine and communicates over `localhost`
(`127.0.0.1`). An older topology — an Ubuntu/Linux guest on a Windows host (Hyper-V) — was
removed from the tree; only its history remains in git.

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
- generated `bpy` code runs OS-confined via **bubblewrap** (no network, no home, read-only
  system; modes `auto`/`require`/`off` — see [`SECURITY.md`](../../SECURITY.md))
- **roadmap**: a stronger **dedicated isolation VM** (Linux, on the host), driven by studio
  asset confidentiality — distinct from the old, now-removed Hyper-V topology

All services listen on `127.0.0.1` (backend `8000`, Ollama `12000`, SearXNG `8081`,
ComfyUI `8188`, optional Open-WebUI `8088`).

## Run persistence and reproducibility

Every run writes to `outputs/runs/<request_id>/`:

- **`events.jsonl`** (`run_events`) — append-only lifecycle journal: route decided, plan
  built, step started/retried/blocked/finished, HITL pause, run finished. Non-blocking
  by contract: a lost event never takes the pipeline down.
- **`state.json`** (`run_state`) — checkpoint written **atomically** (same-directory
  temp file + fsync + `os.replace`) after every step. `POST /resume` rebuilds the plan
  from it, restores the succeeded steps as-is and re-runs the rest. With
  `pause_before_tools`, the run stops before each tool step (`status: paused`) and each
  resume approves **the next gated step only** — a multi-tool plan pauses before each tool.
- **Retention**: `events.jsonl` and `state.json` contain user prompts and expire
  together (`AAC_RUN_EVENTS_RETENTION_DAYS`, default 30; `0` disables the purge).
  Blender/ComfyUI artifacts live under their own output roots and are never touched by
  this purge.

Two support modules guard this surface:

- **`run_identity`** — the single canonical `request_id` contract
  (`^[A-Za-z0-9-]{1,64}$`), applied by the API schema, the Console routes, the
  persistence modules and the ComfyUI replay. Ids name directories on disk; no path is
  ever resolved from an unvalidated id.
- **`run_locks`** — a per-process, per-run execution lock. Concurrent execute/resume of
  the same run is rejected: the API answers `409`, the Console silently re-attaches the
  client to the live stream. **The guard is per-process only** — multi-worker
  coordination is explicitly out of scope today.

**Reproducibility (v2 manifests).** Each Blender/ComfyUI run captures a `repro` block —
exact parameters, semantic scene-report hash (Blender tier 2), pixel and perceptual
hashes (tier 3), engine/torch versions, model hashes — plus resolved-workflow sidecars
next to the manifest. `POST /reproduce` (same engine behind `aac reproduce` and the
Console button) replays a run from that material and returns a verdict:
`exact / perceptual / different / failed / refused`. Integrity comes first: material
that does not re-hash what the manifest recorded is refused, generated code is
re-audited by the security gate at every replay, and the verdict covers **every**
variant recorded in the manifest — a partial replay cannot claim success. GPU
bit-exactness is opportunistic, never promised: the honest cross-machine tier is
perceptual.

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
- `POST /execute` — **synchronous**: the response returns when the run is done
- `POST /resume` — resume an interrupted or paused run from its checkpoint
  (`422` invalid id, `404` no checkpoint, `409` run already executing)
- `POST /reproduce` — replay a run from its v2 manifest and compare artifacts

### Local Console (optional, `/console`)
Mounted only when explicitly enabled; a browser UI on top of the same engine, not a
second business layer.

Long runs never block the page: `POST /console/run` starts the run in a background
task and returns immediately; the page follows it live through SSE
(`GET /console/stream/<request_id>`), fed by the on-disk event journal, then fetches
the final fragment from an in-memory results registry
(`GET /console/run-result/<request_id>`).

- the results registry is **in-memory and bounded** (last 50 runs): a Console restart
  loses the rendered fragment, never the truth — events, checkpoints, manifests and
  artifacts are on disk
- `/execute` itself stays synchronous; the async path is Console-specific
- the long-run UX problem is solved for the local Console. A durable, cancellable,
  multi-worker job queue remains **out of scope**.

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

- OS-level sandbox of generated-code execution is shipped (bubblewrap); stronger VM-grade isolation is a **product goal**, not shipped
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
