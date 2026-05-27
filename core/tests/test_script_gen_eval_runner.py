"""
H.6.8.a — Tests du runner `script_gen_eval_runner`.

Vérifie :
- helpers de nommage (`slugify_model`, `build_report_path`,
  `_format_timestamp`) ;
- composition du payload final (`build_report_payload`) avec schema,
  metadata, hashes prompts ;
- persistance JSON (`save_report` + roundtrip) ;
- orchestration `run_and_save` avec `generate_fn` mocké ;
- CLI `main`.

Aucun appel Ollama réel. Aucun subprocess Blender.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.engine.script_gen_eval_cases import DEFAULT_CASES, ScriptGenCase
from app.engine.script_gen_eval_harness import (
    ScriptGenHarnessReport,
    run_harness,
)
from app.engine.script_gen_eval_runner import (
    DEFAULT_EVAL_REPORTS_DIR,
    REPORT_SCHEMA,
    _format_timestamp,
    _prompt_sha256_short,
    build_report_path,
    build_report_payload,
    main,
    run_and_save,
    save_report,
    slugify_model,
)


# Mock LLM response (script bien formé pour freeform — réutilisé du harness).
_GOOD_FREEFORM_SCRIPT = '''```python
import bpy
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0)
sphere = bpy.context.object
sphere.name = "Sphere"
bpy.ops.mesh.primitive_cube_add(size=2)
cube = bpy.context.object
cube.name = "Cube"
bpy.ops.object.camera_add(location=(5, -5, 4))
bpy.context.scene.camera = bpy.context.object
bpy.ops.object.light_add(type='AREA')
key = bpy.context.object
key.name = "Key_Light"
```'''


# ---------------------------------------------------------------------------
# slugify_model
# ---------------------------------------------------------------------------

def test_slugify_model_typical_names() -> None:
    assert slugify_model("qwen2.5-coder:7b") == "qwen2.5-coder-7b"
    assert slugify_model("Qwen/Qwen2.5-VL:3B") == "Qwen-Qwen2.5-VL-3B"


def test_slugify_model_empty_returns_unknown() -> None:
    assert slugify_model("") == "unknown-model"
    assert slugify_model("   ") == "unknown-model"


def test_slugify_model_strips_trailing_dashes() -> None:
    # Caractère interdit en tête/queue → strip après remplacement.
    assert slugify_model(":weird:") == "weird"


# ---------------------------------------------------------------------------
# Timestamp & path
# ---------------------------------------------------------------------------

def test_format_timestamp_filesystem_safe() -> None:
    now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    ts = _format_timestamp(now)
    assert ts == "2026-05-28T093015Z"
    # Pas de `:` dans le timestamp (filesystem-safe Windows).
    assert ":" not in ts


def test_build_report_path_canonical_format(tmp_path: Path) -> None:
    now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    p = build_report_path(model="qwen2.5-coder:7b", now=now, base_dir=tmp_path)
    assert p.parent == tmp_path
    assert p.name == "2026-05-28T093015Z_script_gen_qwen2.5-coder-7b.json"


def test_default_eval_reports_dir_is_under_outputs_blender() -> None:
    assert str(DEFAULT_EVAL_REPORTS_DIR).replace("\\", "/").endswith(
        "outputs/blender/_eval_reports"
    )


# ---------------------------------------------------------------------------
# prompt sha256 short
# ---------------------------------------------------------------------------

def test_prompt_sha256_short_is_deterministic_and_truncated() -> None:
    h1 = _prompt_sha256_short("hello world")
    h2 = _prompt_sha256_short("hello world")
    assert h1 == h2
    assert len(h1) == 12
    # Différent pour un prompt différent.
    assert h1 != _prompt_sha256_short("HELLO WORLD")


# ---------------------------------------------------------------------------
# build_report_payload
# ---------------------------------------------------------------------------

def test_build_report_payload_has_required_top_level_keys() -> None:
    def fake_gen(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    cases = DEFAULT_CASES[:2]
    report = run_harness(cases=cases, model="m", generate_fn=fake_gen)
    now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    payload = build_report_payload(report, cases=cases, now=now)

    for key in (
        "schema", "generated_at_utc", "model",
        "corpus_version", "inference_config", "cases", "aggregate",
    ):
        assert key in payload

    assert payload["schema"] == REPORT_SCHEMA
    assert payload["generated_at_utc"] == "2026-05-28T09:30:15Z"
    assert payload["model"] == "m"
    assert payload["corpus_version"]["n_cases"] == 2
    assert payload["corpus_version"]["case_ids"] == [c.id for c in cases]


def test_build_report_payload_attaches_prompt_hash_per_case() -> None:
    def fake_gen(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    cases = DEFAULT_CASES[:1]
    report = run_harness(cases=cases, model="m", generate_fn=fake_gen)
    now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    payload = build_report_payload(report, cases=cases, now=now)

    assert len(payload["cases"]) == 1
    case_dict = payload["cases"][0]
    assert "prompt_sha256_short" in case_dict
    assert len(case_dict["prompt_sha256_short"]) == 12
    assert case_dict["prompt_sha256_short"] == _prompt_sha256_short(cases[0].prompt)


def test_build_report_payload_raises_on_mismatch() -> None:
    def fake_gen(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    cases = DEFAULT_CASES[:2]
    report = run_harness(cases=cases, model="m", generate_fn=fake_gen)
    now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="mismatch cases"):
        build_report_payload(report, cases=DEFAULT_CASES[:3], now=now)


def test_build_report_payload_inference_config_present_with_nulls() -> None:
    """H.6.8.a ne stabilise pas l'inférence ; tous les champs sont None."""
    def fake_gen(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    cases = DEFAULT_CASES[:1]
    report = run_harness(cases=cases, model="m", generate_fn=fake_gen)
    now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    payload = build_report_payload(report, cases=cases, now=now)

    cfg = payload["inference_config"]
    for key in ("temperature", "top_p", "top_k", "seed", "format", "num_ctx"):
        assert cfg[key] is None, f"inference_config[{key}] devrait être None en H.6.8.a"
    assert "notes" in cfg and cfg["notes"]


# ---------------------------------------------------------------------------
# save_report + roundtrip
# ---------------------------------------------------------------------------

def test_save_report_writes_json_and_roundtrips(tmp_path: Path) -> None:
    payload = {
        "schema": "script_gen.1",
        "generated_at_utc": "2026-05-28T09:30:15Z",
        "model": "m",
        "corpus_version": {"n_cases": 0, "case_ids": []},
        "inference_config": {},
        "cases": [],
        "aggregate": {"n_cases": 0},
    }
    target = tmp_path / "sub" / "report.json"
    save_report(payload, target)
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == payload


def test_save_report_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "c" / "x.json"
    save_report({"x": 1}, target)
    assert target.exists()


# ---------------------------------------------------------------------------
# run_and_save
# ---------------------------------------------------------------------------

def test_run_and_save_persists_report_on_disk(tmp_path: Path) -> None:
    def fake_gen(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    fixed_now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    path, payload = run_and_save(
        cases=DEFAULT_CASES[:2],
        model="qwen2.5-coder:7b",
        generate_fn=fake_gen,
        base_dir=tmp_path,
        now=fixed_now,
    )

    assert path.exists()
    assert path.name == "2026-05-28T093015Z_script_gen_qwen2.5-coder-7b.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == payload
    assert on_disk["aggregate"]["n_cases"] == 2


# ---------------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------------

def test_main_smoke_with_default_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """
    Smoke test du CLI : patche `_default_generate_fn` pour éviter tout
    appel Ollama réel.
    """
    import app.engine.script_gen_eval_harness as harness_mod

    def fake_gen(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    monkeypatch.setattr(harness_mod, "_default_generate_fn", fake_gen)

    exit_code = main([
        "--model", "test-model",
        "--base-dir", str(tmp_path),
    ])
    assert exit_code == 0

    # Un fichier rapport doit exister.
    files = list(tmp_path.glob("*_script_gen_test-model.json"))
    assert len(files) == 1

    captured = capsys.readouterr()
    assert "script_gen eval terminée" in captured.out
    assert "mean_score" in captured.out
