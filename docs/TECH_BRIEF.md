# AAC — technical brief

> A compact map for a technical reviewer: architecture, control mechanisms, measured results,
> and exactly what is proven where. Consistent with `main` at `b407776`; deeper material
> in [`ARCHITECTURE.md`](../core/docs/ARCHITECTURE.md), [`SECURITY.md`](../SECURITY.md)
> and [`BENCHMARK.md`](../BENCHMARK.md).

## Architecture

```text
request
  ↓
router          task_classifier (weighted rules, always first)
                └─ router_embeddings (semantic fallback, rules' dead zone only)
  ↓
planner         plan_builder → one explicit strategy:
                single_step · two_step_llm · web_pipeline · visual_pipeline · blender_pipeline
  ↓
executor        per-run lock · event journal · per-step checkpoint
                declared retry · human-in-the-loop pause/approve
  ↓
result_assembler → manifest + artifacts, replayable via `aac reproduce`
```

Surfaces: OpenAI-compatible API (`core/openai_compat.py` — drops into Open-WebUI), a local
Console (`core/console.py` — run trace, provenance badges, framing overlay, side-by-side
run compare), and a CLI (`core/cli.py` — `health / inspect / execute / resume / reproduce`;
run it as `python core/cli.py …`, there is no installed entry point). Everything binds to
`127.0.0.1`.

## Mechanisms → where → proven by

| Mechanism | Implementation | Proven by |
|---|---|---|
| Constrained 3D generation (LLM fills a validated spec; deterministic builder writes the `bpy`) | `core/app/engine/product_render_extractor.py` + `product_render_builder.py` | `test_product_render_extractor.py` · `test_product_render_builder.py` (CI) |
| Blocking security gate (denylisted imports, `eval`/`exec`, out-of-tree `open()` → run refused) | `blender_ast_guard.py: analyze_security_gate`, wired in `blender_client.py` | `test_blender_security_gate.py` (CI); refusal recorded in the manifest (`artifact_manifest.py`) |
| OS sandbox, native Linux (no network, no home, RO system, output-dir-only writes, fail-closed `require`) | `core/app/clients/blender_sandbox.py` (bubblewrap) | argv composition: `test_blender_sandbox.py` (CI) · **effective confinement**: `test_blender_sandbox_integration.py` (**local**) |
| Hardened container, Docker path (`cap_drop: ALL`, `no-new-privileges`, RO rootfs, cpu/mem/pids limits) | `docker/docker-compose.sandbox.yml`, mounted unconditionally by `run.sh` / `run.ps1` | compose file + `SECURITY.md`; non-root image + SHA256-pinned Blender tarball in `core/Dockerfile` |
| Deterministic self-correction (one repair pass, no AI retry loop) | `blender_runtime_corrector.py` | Blender-side tests (CI) + live tier |
| Framing verified by geometry, cross-checked on pixels | `blender_qa_visual.py`; overlay drawn by the Console | `test_blender_qa_visual.py` (CI) · `test_framing_contract.py` (integration, local) |
| Run as evidence (manifest w/ repro block, `events.jsonl`, checkpoint/resume, per-run lock, HITL) | `run_events.py` · `run_state.py` · `run_locks.py` · `executor.py` | executor/console test files (CI) |
| Reproduce engine (replay from manifest, verdicts; replay re-runs the security gate) | `core/app/engine/reproduce.py` · `aac reproduce` | CI tests + live tier asserts verdicts on real runs |
| Hybrid router (rules first, embeddings only in the dead zone; degrades to rules if `bge-m3` absent) | `router_embeddings.py`; corpus + offline trainer in `core/scripts/` | router test files + property-based fuzzing (CI) |
| API auth (bearer, constant-time compare, fail-closed `required` mode) | `core/app/auth.py` | auth test files (CI) |
| Bring-your-own Ollama (endpoint + per-role models, provenance in manifests) | env-only config, `docs/OLLAMA.md` | `test_byo_ollama.py` (CI — real HTTP server, no mocked client); wired into `run.sh` only |

## Measured results

**LLM quality** ([`BENCHMARK.md`](../BENCHMARK.md) — versioned corpora, pinned inference,
deterministic scoring; 11 cases × 6 models, RTX 3060):

| Model | Quality | Time (11 cases) |
|---|---|---|
| **`qwen2.5-coder:7b`** *(default)* | **0.987** | **34.4 s** |
| `qwen2.5-coder:14b` | 0.951 | 56.9 s |
| `qwen2.5:7b` | 0.931 | 40.0 s |

*(top 3 of the 6 candidates shown — full table and per-field scores in [`BENCHMARK.md`](../BENCHMARK.md))*

The per-field breakdown exposed a spec-level weakness (`schema_version`, weak for every
model); after one extraction-prompt fix the default model scores 1.000 on that field — and
the cross-model rankings reshuffled, evidence that the benchmark measures the
**model × prompt pair**. These are internal quality baselines on small corpora — not a
general-capability or business-value claim.

**Learned visual metric, gated by pre-registration** (`experiments/jepa_eval/`, marked
*experimental* in `BENCHMARK.md` §4): adopted only after clearing a pre-registered
threshold on a small synthetic-defect render set — **AUC 0.975** (bar: ≥ 0.80), with pixel
and histogram baselines reported alongside for transparency. A metric-adoption gate, not
a product-quality score.

**Image under contract in a captured world** (`experiments/splat_demo/`): a single
demonstrative run — the pipeline's packshot rendered inside a Gaussian-splat environment,
same framing contract — passed (occupancy 0.30, inside the calibrated 0.25–0.55 band;
`results/contract_report.json`), with the splat's source and sha256 recorded in the
manifest.

## What the tests prove, and where

| Tier | Size | Runs in CI? | Proves |
|---|---|---|---|
| hermetic (default) | ≈120 files, ≈1,650 test functions at `b407776` (count: `grep -rc "def test_" core/tests`) | **yes** (`ubuntu-latest`, Python 3.13, `ruff` + `pytest -q`) | contracts, gate logic, sandbox **composition**, API surface; includes 10 Hypothesis properties (`test_fuzz_properties.py`) |
| integration | small, gated on local Blender / bubblewrap | **no** | real renders; **effective** sandbox confinement (network cut, secrets unreadable) |
| live (`scripts/linux/live-tests.sh`) | 5 scenarios | **no** | real end-to-end runs incl. generation → capture → **reproduce verdicts** |

A green CI badge therefore proves the hermetic suite — not end-to-end behavior. This
split is stated in [`CONTRIBUTING.md`](../CONTRIBUTING.md) and [`SECURITY.md`](../SECURITY.md).

## Platform status

| Platform | Status |
|---|---|
| Linux (Fedora, RTX 3060) | **validated end to end** — bare clone → `./run.sh` → hardened stack → real Blender render |
| Windows (Docker Desktop + WSL2) | documented, same containers by construction — **not yet validated** |
| macOS | documented (CPU fallback) — **not yet validated** |

## Known limits

Experimental Blender pipeline · only the two Blender LLM sites measured, on small corpora
(reproducibility baseline, not robustness) · no CPU/RAM quotas in the sandbox · ComfyUI
not OS-sandboxed (fixed, operator-authored workflows) · security gate is a denylist, not
a proof · CI on one OS / one Python; effective-confinement tests local-only · single
maintainer · no production deployments claimed.
