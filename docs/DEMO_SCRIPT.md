# AAC — 3-minute demo script

> Shooting script for a short walkthrough aimed at technical reviewers. Every shot shows
> something that actually runs at `main` (`b407776`); captions are English; no audio
> needed. **Film on native Linux with the Docker stack** — the validated platform. Do not
> capture on Windows/WSL2 or macOS: those paths are documented but not validated, and the
> video must not imply otherwise.

## Claims discipline

- Show mechanisms, not promises: nothing appears on screen that a viewer cannot re-run
  from a bare clone.
- No production/customer framing — this is a personal engineering project.
- Quality numbers only from [`BENCHMARK.md`](../BENCHMARK.md); security wording aligned
  with [`SECURITY.md`](../SECURITY.md) (on the Docker path the *container* is the
  boundary — do not caption "bubblewrap" here).

## Preconditions (before recording)

- Stack up and healthy: `./run.sh` already run once (models downloaded), Console open on
  `http://127.0.0.1:8000/console`.
- Terminal with a venv that has `core/requirements.txt`, font large enough to read. The
  CLI has **no installed entry point** — alias it once before filming:
  `alias aac='python core/cli.py'` (that is the exact command a viewer can re-run).
- One earlier Blender run available in Outputs (for the compare shot).
- 2D quality left on `draft` — keeps generation inside the shot budget.

## Shot list

**[0:00–0:15] Cold open — one command, honest health.**
Terminal: the tail of a `./run.sh` start, ending on `== OK — stack ready ==`; cut to the
Console health strip (`/console/health`) showing every service green.
*Caption: "Local-first AI orchestration. One command. The launcher verifies every service
is actually healthy — hardened container stack by default."*

**[0:15–0:45] The decision is inspectable.**
Terminal: `aac inspect "explain how a rate limiter works"` — the routing decision tree
renders (task, agent, model, tool). Then `aac inspect` on an image prompt to show the
route change.
*Caption: "Router → planner → executor. Every decision is structured and inspectable —
before anything runs."*

**[0:45–1:35] 3D under contract.**
Console: submit *"create a Blender scene with a cube"*. Show the live trace streaming
(step timeline), then the finished run: `scene.blend` + `preview.png`. Open the run
detail (`/console/run`): point at the **security-gate step** in the trace, then the
**framing overlay** drawn over the render, then the **manifest** (models, parameters,
provenance).
*Caption 1: "The AI fills a validated spec — a deterministic builder writes the Blender
code. Generated code is AST-gated before it runs, inside a hardened container
(`cap_drop: ALL`, read-only rootfs)."*
*Caption 2: "Framing is verified by projecting the subject through the camera — then
cross-checked against the rendered pixels."*

**[1:35–2:05] Runs are evidence.**
Run detail: click **Reproduce** (or terminal: `aac reproduce <run-dir>`). Show the
verdict panel comparing the replay against the original, artifact by artifact.
*Caption: "Every run ships a manifest and an event journal. `aac reproduce` replays it
and reports a verdict — the replay re-runs the security gate too."*

**[2:05–2:30] Compare two runs.**
Console `/console/compare`: pick the fresh run and the pre-staged one, show the
side-by-side trace and the repro diff.
*Caption: "Two runs, side by side — what changed, and why."*

**[2:30–2:50] Measured, not asserted.**
Full-screen the benchmark chart (`docs/assets/benchmark-quality-vs-speed.png`), then a
brief scroll of `BENCHMARK.md` stopping on the per-field table.
*Caption: "11 cases × six local models, pinned inference. The per-field breakdown caught
a spec bug (`schema_version`) — one prompt fix later the default model scores 1.000 on
that field, and the rankings reshuffled."*

**[2:50–3:00] Honest close.**
Static end card over the repo README.
*Caption: "Experimental 3D pipeline. Validated end to end on Linux; Windows and macOS
documented, not yet validated. Code, benchmark and threat model: github.com/BNHR-dev/AI_ASSISTANT_CORE."*

## Fallbacks while filming

- If a live Blender run overruns the shot budget, film the run detail of a pre-staged run
  instead — the trace, gate step, overlay and manifest are identical on replayed footage.
  **Caption it as a replayed run**: pre-staged footage must never be presented as live
  generation.
- If `--final` 2D quality is tempting: don't — `draft` is the honest default for this
  hardware budget and keeps the demo reproducible by a viewer.

## Deliberately not shown

Windows/macOS captures (not validated) · bring-your-own-Ollama on Windows (not wired) ·
any "production ready" framing · bubblewrap claims on the Docker path (the container is
the boundary there; bwrap is the *native* path's confinement).
