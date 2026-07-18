# AAC — case study

> A personal project, built solo and in the open, used here as an engineering proof.
> Every claim below points at a file, a test or a command in this repository — nothing
> is asserted that the repo cannot back. Verified against `main` at `b407776`.
> **In a hurry?** The first section plus the two tables read in about two minutes.

## The project in one paragraph

AAC (AI Assistant Core) is a **local-first AI orchestrator** designed against the
constraints of 3D production: material that is confidential by default, and AI-generated
code that cannot be trusted blindly. It turns a natural-language request into a
**structured routing decision**, an **explicit plan**, and a **step-by-step run** across
local models and creative tools (Ollama, Blender, ComfyUI, SearXNG) — with the whole run
traceable and replayable after the fact. One author, built in the open from April to
July 2026, AGPL-3.0. It is a working system and a thesis demonstrator — **not** a product
with users, customers or production deployments, and it does not claim to be.

## The problem it explores

Three constraints drive the design, and they conflict with the default way LLM tools are
built:

- **The target domain is confidential by default.** In 3D production, assets are
  typically unreleased work under NDA — so a tool for that world must keep inference,
  vision and generation on the operator's hardware, with an optional web search as the
  only outbound path. (A domain constraint, not a claim of client data: no studio assets
  are used anywhere in this project.)
- **The AI writes code.** The Blender pipeline generates and executes `bpy` scripts.
  Generated code is the least-trusted input in the system, and has to be treated that way.
- **"It generates" is not enough.** Output quality has to be *measured*, and every run has
  to be *explainable and reproducible* — otherwise the tool cannot be audited or improved.

## The decisions that matter

**1. Constrain generation instead of policing free-form code.** For product renders the
LLM does not write Blender code at all: it fills a *validated spec* (bounded shapes,
materials, colors) and a deterministic builder turns the spec into `bpy`. The extractor
and builder are among the most heavily tested modules in the repo
(`core/tests/test_product_render_extractor.py`, `core/tests/test_product_render_builder.py`).

**2. Treat what is generated as hostile — in layers.** A **blocking security gate**
(`analyze_security_gate` in `core/app/engine/blender_ast_guard.py`) refuses denylisted
imports, dynamic execution and out-of-tree `open()` before Blender starts; the refusal is
recorded in the run manifest (`core/app/engine/artifact_manifest.py`). Below it, OS-level
confinement: **bubblewrap** on native Linux (`core/app/clients/blender_sandbox.py` — no
network, no home, read-only system, writes confined to the output directory, fail-closed
`require` mode), and on Docker a **hardened container** as the boundary
(`docker/docker-compose.sandbox.yml`: `cap_drop: ALL`, `no-new-privileges`, read-only
rootfs — mounted unconditionally by both launchers). The threat model, and what each layer
does *not* cover, is written down in [`SECURITY.md`](../SECURITY.md).

**3. Self-correct deterministically, never with an AI retry loop.** When a render misses
its contract (missing light, bad framing), a single deterministic pass repairs the scene
and re-renders (`core/app/engine/blender_runtime_corrector.py`). Framing is verified by
projecting the subject through the camera, then cross-checked against rendered pixels
(`core/app/engine/blender_qa_visual.py`) — geometry first, eyes never.

**4. Make every run evidence.** Each run writes a manifest with a reproducibility block,
a per-run event journal, and per-step checkpoints (`core/app/engine/run_events.py`,
`run_state.py`, `run_locks.py`). The CLI's `reproduce` command replays a run from its
manifest and reports a verdict — the replay even re-runs the security gate
(`core/app/engine/reproduce.py`). The Console (`/console/run`) shows the trace, the
provenance and the framing overlay, and can compare two runs side by side.

**5. Measure the model, don't trust it.** [`BENCHMARK.md`](../BENCHMARK.md) scores the
two LLM sites of the Blender pipeline on versioned corpora with pinned inference —
11 cases across six models. It earned its keep: the per-field breakdown exposed a
spec-level weakness (`schema_version`, weak for *every* model); one extraction-prompt fix
later the default model scores 1.000 on that field, and re-running the comparison showed
the rankings reshuffle — a benchmark measures the **model × prompt pair**. A learned visual metric was
evaluated against pre-registered thresholds before adoption
(`experiments/jepa_eval/` — AUC 0.975, threshold was ≥ 0.80).

**6. Say what the tests actually prove.** Three explicit tiers
([`CONTRIBUTING.md`](../CONTRIBUTING.md)): a hermetic suite that runs in CI, integration
tests that need real Blender/bubblewrap and run locally, and a live tier
(`scripts/linux/live-tests.sh`) that exercises the real stack and asserts reproduce
verdicts. CI proves the sandbox *composition*; the *effective* confinement is proven by
the local integration tier — the docs say so explicitly.

## What is demonstrated — and how to check it

| Claim | Check it with |
|---|---|
| One command brings up the hardened stack on Linux | `./run.sh` from a bare clone (Docker only) — health-gated |
| Requests are routed, planned, executed, traced | `python core/cli.py inspect "<prompt>"` · `… execute "<prompt>"` · `/console` |
| Generated `bpy` is gated and confined | `core/tests/test_blender_security_gate.py` (CI) · `test_blender_sandbox.py` (CI, argv composition) · `test_blender_sandbox_integration.py` (local, real confinement) |
| Runs are replayable | `python core/cli.py reproduce <run-dir>` · live tier asserts the verdicts |
| Model quality is measured, reproducibly | the harness commands in [`BENCHMARK.md`](../BENCHMARK.md) § *Reproduce it yourself* |
| The suite is real | `cd core && python -m pytest -q` — ≈120 test files, ≈1,650 test functions at `b407776` (count: `grep -rc "def test_" core/tests`) |

## Honest limits

- The Blender pipeline is **experimental** (functional, but the youngest part).
- Only the two Blender LLM sites are measured; corpora are small (5 and 11 cases); pinned
  decoding makes it a **reproducibility** baseline, not a robustness one.
- **Linux is validated end to end. Windows/WSL2 and macOS are documented, not validated.**
- The sandbox has no CPU/RAM quotas; ComfyUI is not OS-sandboxed (it runs fixed,
  operator-authored workflows); the security gate is a denylist, not a proof.
- CI covers one OS and one Python version; the effective-confinement tests are local-only.
- Single maintainer; no external code review or security audit has taken place.

## Where to go next

The 3-minute [demo script](DEMO_SCRIPT.md) · the [technical brief](TECH_BRIEF.md) · the
[architecture document](../core/docs/ARCHITECTURE.md) · the [benchmark](../BENCHMARK.md).
