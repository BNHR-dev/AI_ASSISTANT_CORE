# Roadmap — AI_ASSISTANT_CORE

## General line

Current priority:
**stabilize, clarify and cleanly expose the existing core before adding new orchestration layers.**

The project has already passed the critical milestone:
- a readable central router
- an explicit planner
- a multi-strategy executor
- real web and visual pipelines
- usable observability

So the short-term value is not a big refactor, but a tight alignment between code, runtime,
API and docs.

## Current baseline

Correct reading of the snapshot:
**V1.7.0 stable / controlled V2 proto, with the visual subsystem integrated, a stabilized
single-host (localhost) runtime after the VM migration closed, and phase 1 of safe backend
hardening validated.**

## What is already locked

### Core
- a single central decision
- an active planner
- readable traces
- simple `build` via `single_step`
- a clean web pipeline
- a clean visual pipeline
- a coherent `/execute` surface
- preserved ComfyUI contracts

### Blender pipeline
- functional Blender pipeline, headless on the host
- generation of `scene.py`, `scene.blend` and `preview.png`
- `scene.blend` as the canonical artifact
- `preview.png` best-effort, produced in a separate subprocess
- readable preview PNG; visual quality still improvable

### Single-host runtime
- Hyper-V VM migration closed: the whole canonical runtime runs on the host, over `localhost`
- backend (FastAPI) on the host, bound to `127.0.0.1:8000`
- Ollama / SearXNG / Open-WebUI in containers (`docker-compose.linux.yml`), ports bound to `127.0.0.1`
- ComfyUI and Blender run directly on the host
- old VM/Windows topology removed from the tree (git history only), outside the canonical runtime

### Hardening phase 1
- safe hardening applied on the backend without breaking operations
- generated `bpy` code runs OS-confined via **bubblewrap** (modes `auto`/`require`/`off`);
  a stronger **VM-grade isolation** remains a product goal, not shipped — not presented as if it were

## Reasonable near-term priorities

1. **Aligned canonical docs**
   - `README.md` at the root; `ARCHITECTURE.md`, `ROADMAP.md`, `SETUP_LINUX.md` in `docs/`
   - consistency between the root README and `docs/` (no duplication)

2. **Honest runtime consolidation**
   - keep the canonical `localhost` endpoints (backend `8000`, Ollama `12000`,
     SearXNG `8081`, ComfyUI `8188`) consistent across code, compose and docs
   - Open-WebUI as an optional operator UI, outside the canonical runtime

3. **A cleaner debug surface**
   - keep `/health/runtime` as a useful view
   - eventually clean up the "dormant" labeling when it no longer reflects real usage

4. **Visible quality without rebuilding the core**
   - improve prompts and output contracts
   - refine the build output
   - keep web synthesis clean
   - further improve the visual pipeline's ergonomics

5. **Legacy under control**
   - keep the root shims passive
   - avoid any new business logic outside `app/*`

## Possible Blender improvements

Once the Blender pipeline is stable in use, the most natural improvements are:
- preview PNG visual quality
- better bpy templates (materials, lighting, composition)
- inspection and validation of generated scenes
- opening toward more advanced 3D workflows (multi-object, animation, exports)

## Controlled V2 proto

What the V2 proto can target without breaking the core:
- better visible output quality
- more robust prompts
- more homogeneous outputs
- better UI exposure of agents and visual capabilities
- greater comfort for creative workflows

## What not to do too early

- wiring a new orchestration layer before internal stabilization
- introducing uncontracted long-term memory
- multiplying selectors when `router + planner` already suffices
- hiding legacy debt under a new abstraction
- starting a large architecture effort when the current need is mostly real consistency
