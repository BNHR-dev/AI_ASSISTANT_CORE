"""Labeled-dataset builder for the V-JEPA experiment — runs INSIDE aac-backend.

Pushed into /outputs/blender/_jepa_eval/_scripts/ by make_dataset.py (the container
rootfs is read-only; /outputs is the only writable tree) and executed with the
container's Python, where the `app` package and Blender are native.

Per eval-corpus case (11):
  base        — real pipeline run (execute_request, corrector included), preview copied
  conform_j1/2 — in-contract camera jitters, re-rendered headless (bypasses corrector)
  deg_*        — injected defects (no key light / off framing / intruder / rim light)

Every variant records label + what the deterministic contract sees (contract_verdict).
Idempotent: a variant whose preview.png exists is skipped.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.engine.blender_preview_fidelity import preview_fidelity_script_block
from app.engine.blender_qa_visual import run_visual_qa
from app.engine.blender_runtime_contract import evaluate_runtime_contract
from app.engine.product_render_eval_cases import DEFAULT_CASES

OUTPUT_ROOT = Path(os.environ.get("BLENDER_OUTPUT_DIR", "/outputs/blender"))
DATASET_ROOT = OUTPUT_ROOT / "_jepa_eval"
SCRIPTS_DIR = DATASET_ROOT / "_scripts"
BLENDER_EXE = os.environ.get("BLENDER_EXE", "/usr/local/bin/blender")
FIDELITY_MARKER = "### PREVIEW_FIDELITY_BLOCK ###"

CONFORM_VARIANTS = ("conform_j1", "conform_j2")
DEGRADED_VARIANTS = ("deg_nolight", "deg_framing", "deg_intruder", "deg_rimlight")
DEFECT_OF = {
    "deg_nolight": "key light removed",
    "deg_framing": "camera pushed far and off-axis",
    "deg_intruder": "intruder cube next to the product",
    "deg_rimlight": "strong colored rim light",
}


def build_mutate_script() -> Path:
    template = (SCRIPTS_DIR / "mutate_and_render_template.py").read_text(encoding="utf-8")
    if FIDELITY_MARKER not in template:
        raise RuntimeError("fidelity marker missing from mutate template")
    final = template.replace(FIDELITY_MARKER, preview_fidelity_script_block(), 1)
    path = SCRIPTS_DIR / "mutate_and_render.py"
    path.write_text(final, encoding="utf-8")
    return path


def contract_verdict(object_names: list[str] | None, preview: Path) -> dict:
    runtime = evaluate_runtime_contract(object_names, "product_render")
    visual = run_visual_qa(str(preview))
    return {
        "runtime_status": runtime["status"],
        "runtime_violations": runtime["violations"],
        "visual_status": visual["status"],
        "visual_violations": visual["violations"],
        "contract_caught": bool(
            runtime["violations"]
            or visual["violations"]
            or runtime["status"] == "degraded"
            or visual["status"] == "degraded"
        ),
    }


def write_variant_json(out_dir: Path, payload: dict) -> None:
    (out_dir / "variant.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def run_base(case) -> Path | None:
    """Full pipeline run for a case; returns the base scene.blend path (or None)."""
    base_dir = DATASET_ROOT / case.id / "base"
    if (base_dir / "preview.png").exists():
        provenance = json.loads((base_dir / "variant.json").read_text(encoding="utf-8"))
        return OUTPUT_ROOT / provenance["source_run"] / "scene.blend"

    from app.engine.executor import execute_request  # heavy import, only when needed

    print(f"[base] {case.id}: pipeline run...", flush=True)
    result = execute_request(case.prompt, mode="blender_script")
    run_id = result.get("request_id")
    run_dir = OUTPUT_ROOT / str(run_id)
    preview = run_dir / "preview.png"
    blend = run_dir / "scene.blend"
    if not (preview.exists() and blend.exists()):
        print(f"[base] {case.id}: FAILED (run {run_id}, artifacts missing)", flush=True)
        return None

    base_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(preview, base_dir / "preview.png")
    report = {}
    report_path = run_dir / "scene_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    write_variant_json(
        base_dir,
        {
            "case_id": case.id,
            "variant": "base",
            "label": "conform",
            "defect": None,
            "source_run": str(run_id),
            "prompt": case.prompt,
            "contract_verdict": {
                "runtime_status": report.get("status"),
                "runtime_violations": report.get("violations", []),
                "visual_status": (report.get("visual_qa") or {}).get("status"),
                "visual_violations": (report.get("visual_qa") or {}).get("violations", []),
                "contract_caught": bool(report.get("violations")),
            },
        },
    )
    print(f"[base] {case.id}: ok (run {run_id})", flush=True)
    return blend


def run_variant(case_id: str, blend: Path, variant: str, script: Path) -> None:
    out_dir = DATASET_ROOT / case_id / variant
    if (out_dir / "preview.png").exists():
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "JEPA_VARIANT": variant, "JEPA_OUT": str(out_dir)}
    cmd = [
        BLENDER_EXE,
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        str(blend),
        "--python",
        str(script),
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
    preview = out_dir / "preview.png"
    if not preview.exists():
        tail = (proc.stdout + proc.stderr)[-400:]
        print(f"[{variant}] {case_id}: FAILED — {tail}", flush=True)
        return

    object_names = None
    objects_path = out_dir / "objects.json"
    if objects_path.exists():
        object_names = json.loads(objects_path.read_text(encoding="utf-8"))["object_names"]
    label = "conform" if variant in CONFORM_VARIANTS else "degraded"
    write_variant_json(
        out_dir,
        {
            "case_id": case_id,
            "variant": variant,
            "label": label,
            "defect": DEFECT_OF.get(variant),
            "source_blend": str(blend),
            "contract_verdict": contract_verdict(object_names, preview),
        },
    )
    print(f"[{variant}] {case_id}: ok", flush=True)


def main() -> None:
    DATASET_ROOT.mkdir(parents=True, exist_ok=True)
    script = build_mutate_script()

    entries = []
    for case in DEFAULT_CASES:
        blend = run_base(case)
        if blend is None:
            continue
        for variant in CONFORM_VARIANTS + DEGRADED_VARIANTS:
            run_variant(case.id, blend, variant, script)
        for variant in ("base",) + CONFORM_VARIANTS + DEGRADED_VARIANTS:
            vjson = DATASET_ROOT / case.id / variant / "variant.json"
            if vjson.exists() and (DATASET_ROOT / case.id / variant / "preview.png").exists():
                entries.append(json.loads(vjson.read_text(encoding="utf-8")))

    (DATASET_ROOT / "dataset.json").write_text(
        json.dumps({"total": len(entries), "entries": entries}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    n_conform = sum(1 for e in entries if e["label"] == "conform")
    print(f"dataset: {len(entries)} images ({n_conform} conform, {len(entries) - n_conform} degraded)", flush=True)
    if len(entries) < len(DEFAULT_CASES) * 7:
        print("WARNING: incomplete dataset — re-run to retry failed variants", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
