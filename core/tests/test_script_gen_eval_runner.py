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

from app.engine.script_gen_eval_cases import DEFAULT_CASES
from app.engine.script_gen_eval_harness import (
    run_harness,
)
from app.engine.script_gen_eval_runner import (
    DEFAULT_EVAL_REPORTS_DIR,
    REPORT_SCHEMA,
    REPORT_SCHEMA_MULTIRUN,
    _format_timestamp,
    _prompt_sha256_short,
    _safe_case_filename,
    _stats_block,
    aggregate_multirun,
    build_multirun_report_path,
    build_report_path,
    build_report_payload,
    build_scripts_dir_name,
    main,
    persist_extracted_scripts,
    run_and_save,
    run_and_save_multi,
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


def test_build_report_payload_inference_config_reflects_h68b2_stabilisation() -> None:
    """
    H.6.8.b.2 — l'inférence est stabilisée. Le bloc inference_config du
    rapport doit refléter SCRIPT_GEN_INFERENCE_OPTIONS (temperature=0,
    top_k=1, seed=42, num_ctx=8192) et explicitement format=None
    (script_gen produit du Python markdown, pas du JSON).
    """
    from app.engine.script_gen_eval_harness import SCRIPT_GEN_INFERENCE_OPTIONS

    def fake_gen(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    cases = DEFAULT_CASES[:1]
    report = run_harness(cases=cases, model="m", generate_fn=fake_gen)
    now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    payload = build_report_payload(report, cases=cases, now=now)

    cfg = payload["inference_config"]
    assert cfg["temperature"] == SCRIPT_GEN_INFERENCE_OPTIONS["temperature"]
    assert cfg["top_p"] == SCRIPT_GEN_INFERENCE_OPTIONS["top_p"]
    assert cfg["top_k"] == SCRIPT_GEN_INFERENCE_OPTIONS["top_k"]
    assert cfg["seed"] == SCRIPT_GEN_INFERENCE_OPTIONS["seed"]
    assert cfg["num_ctx"] == SCRIPT_GEN_INFERENCE_OPTIONS["num_ctx"]
    # format reste explicitement None (le LLM produit du Python markdown).
    assert cfg["format"] is None
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
# H.6.8.b.1 — Persistance des scripts bruts
# ---------------------------------------------------------------------------

def test_safe_case_filename_strips_invalid_chars() -> None:
    assert _safe_case_filename("clean_id") == "clean_id"
    assert _safe_case_filename("with space") == "with-space"
    assert _safe_case_filename("a/b\\c") == "a-b-c"
    assert _safe_case_filename("") == "unknown-case"


def test_build_scripts_dir_name_single_run() -> None:
    now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    name = build_scripts_dir_name(model="qwen2.5-coder:7b", now=now)
    assert name == "2026-05-28T093015Z_script_gen_qwen2.5-coder-7b_scripts"


def test_build_scripts_dir_name_multi_run() -> None:
    now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    name = build_scripts_dir_name(model="qwen2.5-coder:7b", now=now, n_runs=3)
    assert name == "2026-05-28T093015Z_script_gen_qwen2.5-coder-7b_x3runs_scripts"


def test_persist_extracted_scripts_writes_files_and_returns_map(tmp_path: Path) -> None:
    from app.engine.script_gen_eval_harness import score_script

    score = score_script(
        raw_response=_GOOD_FREEFORM_SCRIPT,
        case=DEFAULT_CASES[0],
        template_name_actual=None,
    )
    assert score.extracted_code is not None and score.extracted_code.strip()

    scripts_dir = tmp_path / "subdir"
    written = persist_extracted_scripts([score], scripts_dir)

    assert DEFAULT_CASES[0].id in written
    target = scripts_dir / written[DEFAULT_CASES[0].id]
    assert target.exists()
    assert target.read_text(encoding="utf-8") == score.extracted_code


def test_persist_extracted_scripts_skips_cases_without_code(tmp_path: Path) -> None:
    from app.engine.script_gen_eval_harness import score_script

    score = score_script(
        raw_response=None,  # generation_ok=False → extracted_code=None
        case=DEFAULT_CASES[0],
        template_name_actual=None,
    )
    assert score.extracted_code is None

    scripts_dir = tmp_path / "empty"
    written = persist_extracted_scripts([score], scripts_dir)

    assert written == {}
    # dir créé même si vide
    assert scripts_dir.exists()


def test_run_and_save_writes_scripts_and_paths_in_report(tmp_path: Path) -> None:
    """H.6.8.b.1 — scripts persistés + raw_script_path dans le rapport."""
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

    scripts_dir = tmp_path / "2026-05-28T093015Z_script_gen_qwen2.5-coder-7b_scripts"
    assert scripts_dir.exists()
    py_files = sorted(scripts_dir.glob("*.py"))
    assert len(py_files) == 2

    # Chaque case_dict porte raw_script_path relatif au base_dir, et le
    # fichier pointé existe.
    for case_dict in payload["cases"]:
        rel = case_dict["raw_script_path"]
        assert rel is not None
        assert rel.startswith(
            "2026-05-28T093015Z_script_gen_qwen2.5-coder-7b_scripts/"
        )
        assert (tmp_path / rel).exists()
        # Le contenu sur disque correspond bien à un script bpy.
        content = (tmp_path / rel).read_text(encoding="utf-8")
        assert "import bpy" in content


def test_run_and_save_raw_script_path_none_when_generation_failed(tmp_path: Path) -> None:
    """Si le LLM échoue, raw_script_path=None et aucun .py écrit pour ce cas."""
    def empty_gen(model: str, prompt: str) -> str:
        return ""

    fixed_now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    path, payload = run_and_save(
        cases=DEFAULT_CASES[:1],
        model="qwen2.5-coder:7b",
        generate_fn=empty_gen,
        base_dir=tmp_path,
        now=fixed_now,
    )

    case_dict = payload["cases"][0]
    assert case_dict["raw_script_path"] is None
    scripts_dir = tmp_path / "2026-05-28T093015Z_script_gen_qwen2.5-coder-7b_scripts"
    # Le dossier peut exister mais doit être vide
    py_files = list(scripts_dir.glob("*.py")) if scripts_dir.exists() else []
    assert py_files == []


# ---------------------------------------------------------------------------
# H.6.8.b.2 — Stabilisation inférence + multi-run
# ---------------------------------------------------------------------------

def test_stats_block_empty_zeros() -> None:
    assert _stats_block([]) == {"mean": 0.0, "min": 0.0, "max": 0.0, "stdev": 0.0}


def test_stats_block_single_value_stdev_zero() -> None:
    block = _stats_block([0.75])
    assert block == {"mean": 0.75, "min": 0.75, "max": 0.75, "stdev": 0.0}


def test_stats_block_multi_values() -> None:
    block = _stats_block([1.0, 0.5, 0.5])
    assert block["mean"] == pytest.approx(2.0 / 3.0)
    assert block["min"] == 0.5
    assert block["max"] == 1.0
    assert block["stdev"] > 0.0


def test_build_multirun_report_path_format() -> None:
    now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    p = build_multirun_report_path(
        model="qwen2.5-coder:7b", n_runs=3, now=now, base_dir=Path("base"),
    )
    assert p.name == "2026-05-28T093015Z_script_gen_qwen2.5-coder-7b_x3runs.json"


def test_aggregate_multirun_empty_returns_zeros() -> None:
    cases = DEFAULT_CASES[:2]
    agg = aggregate_multirun([], cases=cases)
    assert agg["n_runs"] == 0
    assert agg["n_cases"] == 2
    assert agg["case_aggregates"] == []
    assert agg["per_run_summaries"] == []


def test_aggregate_multirun_n_runs_stats_across_runs() -> None:
    from app.engine.script_gen_eval_harness import run_harness

    def fake_gen(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    cases = DEFAULT_CASES[:2]
    reports = [
        run_harness(cases=cases, model="m", generate_fn=fake_gen)
        for _ in range(3)
    ]
    agg = aggregate_multirun(reports, cases=cases)

    assert agg["n_runs"] == 3
    assert agg["n_cases"] == 2
    # mean_score doit être stable cross-run avec le même mock.
    ms = agg["aggregate"]["mean_score"]
    assert ms["stdev"] == 0.0
    assert ms["mean"] == ms["min"] == ms["max"]
    # case_aggregates contient 2 entrées avec stats per-case.
    assert len(agg["case_aggregates"]) == 2
    for c in agg["case_aggregates"]:
        assert "case_id" in c
        assert "score" in c
        assert c["ast_parseable_count"] == 3
    # per_run_summaries contient 3 entrées.
    assert len(agg["per_run_summaries"]) == 3


def test_aggregate_multirun_detects_case_id_mismatch() -> None:
    from app.engine.script_gen_eval_harness import run_harness

    def fake_gen(model, prompt):
        return _GOOD_FREEFORM_SCRIPT

    cases_a = DEFAULT_CASES[:2]
    cases_b = DEFAULT_CASES[1:3]  # ordre différent → premier id différent
    r_a = run_harness(cases=cases_a, model="m", generate_fn=fake_gen)
    r_b = run_harness(cases=cases_b, model="m", generate_fn=fake_gen)

    with pytest.raises(ValueError, match="case_id différent"):
        aggregate_multirun([r_a, r_b], cases=cases_a)


def test_aggregate_multirun_detects_n_cases_mismatch() -> None:
    from app.engine.script_gen_eval_harness import run_harness

    def fake_gen(model, prompt):
        return _GOOD_FREEFORM_SCRIPT

    r_a = run_harness(cases=DEFAULT_CASES[:2], model="m", generate_fn=fake_gen)
    r_b = run_harness(cases=DEFAULT_CASES[:3], model="m", generate_fn=fake_gen)

    with pytest.raises(ValueError, match="len\\(cases\\)="):
        aggregate_multirun([r_a, r_b], cases=DEFAULT_CASES[:2])


def test_run_and_save_multi_persists_aggregated_report(tmp_path: Path) -> None:
    def fake_gen(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    fixed_now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    path, payload = run_and_save_multi(
        n_runs=3,
        cases=DEFAULT_CASES[:2],
        model="qwen2.5-coder:7b",
        generate_fn=fake_gen,
        base_dir=tmp_path,
        now=fixed_now,
    )

    assert path.exists()
    assert path.name == "2026-05-28T093015Z_script_gen_qwen2.5-coder-7b_x3runs.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == payload
    assert on_disk["schema"] == REPORT_SCHEMA_MULTIRUN
    assert on_disk["n_runs"] == 3
    assert on_disk["n_cases"] == 2
    # stdev=0 attendu avec mock déterministe.
    assert on_disk["aggregate"]["mean_score"]["stdev"] == 0.0


def test_run_and_save_multi_persists_scripts_per_run(tmp_path: Path) -> None:
    def fake_gen(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    fixed_now = datetime(2026, 5, 28, 9, 30, 15, tzinfo=timezone.utc)
    path, payload = run_and_save_multi(
        n_runs=3,
        cases=DEFAULT_CASES[:2],
        model="qwen2.5-coder:7b",
        generate_fn=fake_gen,
        base_dir=tmp_path,
        now=fixed_now,
    )

    scripts_base = tmp_path / (
        "2026-05-28T093015Z_script_gen_qwen2.5-coder-7b_x3runs_scripts"
    )
    assert scripts_base.exists()
    # 3 sous-dossiers run0/, run1/, run2/, chacun avec 2 .py.
    for i in range(3):
        run_dir = scripts_base / f"run{i}"
        assert run_dir.exists()
        assert len(list(run_dir.glob("*.py"))) == 2

    # raw_script_path doit pointer sur les bons chemins relatifs.
    for summary in payload["per_run_summaries"]:
        i = summary["run_index"]
        for cr in summary["case_results"]:
            rel = cr.get("raw_script_path")
            assert rel is not None
            assert f"_x3runs_scripts/run{i}/" in rel
            assert (tmp_path / rel).exists()


def test_run_and_save_multi_rejects_n_runs_zero(tmp_path: Path) -> None:
    def fake_gen(model, prompt):
        return _GOOD_FREEFORM_SCRIPT

    with pytest.raises(ValueError, match="n_runs doit être >= 1"):
        run_and_save_multi(
            n_runs=0,
            cases=DEFAULT_CASES[:1],
            model="m",
            generate_fn=fake_gen,
            base_dir=tmp_path,
        )


def test_script_gen_inference_options_constants() -> None:
    """
    Sanity : les constantes H.6.8.b.2 valent bien ce qui a été cadré
    (cf. cadrage §H.6.8.b.2).
    """
    from app.engine.script_gen_eval_harness import SCRIPT_GEN_INFERENCE_OPTIONS

    assert SCRIPT_GEN_INFERENCE_OPTIONS["temperature"] == 0.0
    assert SCRIPT_GEN_INFERENCE_OPTIONS["top_p"] == 1.0
    assert SCRIPT_GEN_INFERENCE_OPTIONS["top_k"] == 1
    assert SCRIPT_GEN_INFERENCE_OPTIONS["seed"] == 42
    assert SCRIPT_GEN_INFERENCE_OPTIONS["num_ctx"] == 8192
    # Pas de clé "format" — script_gen produit du Python markdown.
    assert "format" not in SCRIPT_GEN_INFERENCE_OPTIONS


def test_default_generate_fn_calls_ollama_with_stabilised_options(monkeypatch) -> None:
    """
    Le `_default_generate_fn` H.6.8.b.2 doit transmettre
    SCRIPT_GEN_INFERENCE_OPTIONS et NE PAS forcer format=json.
    """
    import app.engine.script_gen_eval_harness as harness_mod

    captured: dict = {}

    def fake_generate_with_ollama(model, prompt, **kwargs):
        captured["model"] = model
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return "```python\nimport bpy\n```"

    monkeypatch.setattr(harness_mod, "generate_with_ollama", fake_generate_with_ollama)

    harness_mod._default_generate_fn("test-model", "test-prompt")

    assert captured["model"] == "test-model"
    assert captured["prompt"] == "test-prompt"
    assert "options" in captured["kwargs"]
    opts = captured["kwargs"]["options"]
    assert opts["temperature"] == 0.0
    assert opts["seed"] == 42
    # Aucun format JSON imposé.
    assert "format" not in captured["kwargs"]


def test_build_script_gen_generate_fn_overrides_seed(monkeypatch) -> None:
    """
    La factory `build_script_gen_generate_fn(seed)` doit produire un
    `generate_fn` qui passe le seed demandé tout en gardant les autres
    options stabilisées.
    """
    import app.engine.script_gen_eval_harness as harness_mod

    captured: dict = {}

    def fake_generate_with_ollama(model, prompt, **kwargs):
        captured["options"] = kwargs.get("options")
        return "```python\nimport bpy\n```"

    monkeypatch.setattr(harness_mod, "generate_with_ollama", fake_generate_with_ollama)

    gen = harness_mod.build_script_gen_generate_fn(seed=999)
    gen("m", "p")

    opts = captured["options"]
    assert opts["seed"] == 999
    assert opts["temperature"] == 0.0
    assert opts["top_k"] == 1
    # Le global SCRIPT_GEN_INFERENCE_OPTIONS n'a pas été muté.
    assert harness_mod.SCRIPT_GEN_INFERENCE_OPTIONS["seed"] == 42


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


def test_main_smoke_with_runs_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """
    H.6.8.b.2 — CLI multi-run avec `--runs 3`. Vérifie qu'un rapport
    `_x3runs.json` est produit, qu'un verdict stabilité est affiché, et
    qu'un dossier scripts par run est créé.
    """
    import app.engine.script_gen_eval_harness as harness_mod

    def fake_gen(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    monkeypatch.setattr(harness_mod, "_default_generate_fn", fake_gen)

    exit_code = main([
        "--model", "test-model",
        "--base-dir", str(tmp_path),
        "--runs", "3",
    ])
    assert exit_code == 0

    files = list(tmp_path.glob("*_script_gen_test-model_x3runs.json"))
    assert len(files) == 1

    scripts_dirs = list(tmp_path.glob("*_script_gen_test-model_x3runs_scripts"))
    assert len(scripts_dirs) == 1
    for i in range(3):
        run_dir = scripts_dirs[0] / f"run{i}"
        assert run_dir.exists()

    captured = capsys.readouterr()
    assert "script_gen multi-run terminé" in captured.out
    assert "stability_verdict" in captured.out
    # mock déterministe → stdev=0 → STABLE
    assert "STABLE" in captured.out


def test_main_rejects_runs_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """--runs 0 doit retourner code 2 et un message d'erreur."""
    exit_code = main([
        "--model", "test-model",
        "--base-dir", str(tmp_path),
        "--runs", "0",
    ])
    assert exit_code == 2
