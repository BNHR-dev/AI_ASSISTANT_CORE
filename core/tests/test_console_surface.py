"""Console V0 — tests ciblés (surface locale + sécurité de la route artefact).

Conventions alignées sur `tests/test_fastapi_surface.py` : TestClient(app) et
mock de `execute_request` via monkeypatch. Aucun appel réel au moteur.
"""
import json

from fastapi.testclient import TestClient

import console
from app.main import app


client = TestClient(app)


# --------------------------------------------------------------------------- #
# Faux résultats (forme réelle du dict renvoyé par execute_request)
# --------------------------------------------------------------------------- #
def _blender_result():
    return {
        "task_type": "blender_script",
        "selected_model": "qwen2.5-coder:7b",
        "reason": "mot 'blender' détecté",
        "decision_path": ["classifier", "blender_script"],
        "decision_trace": ["classifier → blender_script", "executor → done"],
        "duration_ms": 4200,
        "execution_summary": {
            "status": "success",
            "total_steps": 2,
            "successful_step_ids": ["step_blender"],
            "error_step_ids": [],
            "blocked_step_ids": [],
        },
        "plan": [
            {"step_id": "step_blender", "goal": "générer et exécuter la scène"},
        ],
        "step_results": [
            {"step_id": "step_blender", "step_type": "tool_blender",
             "status": "success", "error": None, "duration_ms": 2800},
        ],
        "blender_status": "success",
        "blender_render_path": "outputs/blender/abc123/preview.png",
        "blender_scene_report": {"status": "passed", "violations": []},
        "blender_manifest": {"future": {"creative_intent": {"mood": ["dark"]}}},
    }


def _error_result():
    return {
        "task_type": "blender_script",
        "selected_model": "qwen2.5-coder:7b",
        "reason": "scène demandée",
        "decision_path": ["classifier", "blender_script"],
        "decision_trace": ["classifier → blender_script"],
        "duration_ms": 1200,
        "execution_summary": {
            "status": "failed",
            "total_steps": 1,
            "successful_step_ids": [],
            "error_step_ids": ["step_blender"],
            "blocked_step_ids": [],
        },
        "plan": [{"step_id": "step_blender", "goal": "exécuter la scène"}],
        "step_results": [
            {"step_id": "step_blender", "step_type": "tool_blender",
             "status": "error", "error": "Blender a échoué (returncode 1)",
             "duration_ms": 900},
        ],
        "blender_status": "error",
    }


# --------------------------------------------------------------------------- #
# Page + exécution
# --------------------------------------------------------------------------- #
def test_console_page_served():
    response = client.get("/console")
    assert response.status_code == 200
    assert "AAC Console" in response.text
    assert 'hx-post="/console/run"' in response.text


def test_run_renders_success_result(monkeypatch):
    monkeypatch.setattr(console, "execute_request", lambda message: _blender_result())
    response = client.post("/console/run", data={"message": "scène 3d blender : théière"})
    assert response.status_code == 200
    body = response.text
    assert "blender_script" in body
    assert "qwen2.5-coder:7b" in body
    # rendu Blender exposé via la route artefact protégée
    assert "/console/artifact?path=" in body
    assert "preview.png" in body


def test_run_renders_error_result(monkeypatch):
    monkeypatch.setattr(console, "execute_request", lambda message: _error_result())
    response = client.post("/console/run", data={"message": "scène qui casse"})
    assert response.status_code == 200
    body = response.text
    assert "Erreur" in body
    assert "Blender a échoué" in body


def test_run_handles_engine_exception(monkeypatch):
    def _boom(message):
        raise RuntimeError("moteur indisponible")

    monkeypatch.setattr(console, "execute_request", _boom)
    response = client.post("/console/run", data={"message": "peu importe"})
    assert response.status_code == 200
    assert "RuntimeError" in response.text


def test_run_rejects_empty_message():
    response = client.post("/console/run", data={"message": "   "})
    assert response.status_code == 200
    assert "vide" in response.text.lower()


# --------------------------------------------------------------------------- #
# V1.a — overlay cadrage, fidélité sémantique, bandeau santé
# --------------------------------------------------------------------------- #
def _blender_result_with_observability():
    r = _blender_result()
    r["blender_scene_report"] = {
        "visual_qa": {
            "image_size": [512, 512],
            "checks": {"subject_bbox_detected": {"bbox": [75, 180, 512, 512]}},
        },
        "framing_contract": {
            "screen_bbox": [0.40, 0.22, 0.55, 0.77],
            "framing_divergence": {
                "perceptual_bbox_fraction": [0.146, 0.351, 1.0, 1.0],
                "projected_bbox_fraction": [0.404, 0.229, 0.550, 0.777],
                "iou": 0.1087,
                "diverged": True,
            },
        },
    }
    r["blender_manifest"] = {
        "future": {
            "product_render_intent": {
                "subject": {
                    "kind": "bottle",
                    "label": "flacon de parfum noir",
                    "kind_fidelity": "exact",
                }
            }
        }
    }
    return r


def test_run_renders_framing_overlay(monkeypatch):
    monkeypatch.setattr(
        console, "execute_request", lambda message: _blender_result_with_observability()
    )
    body = client.post("/console/run", data={"message": "flacon"}).text
    # deux rectangles colorés (perceptuel rouge / projeté vert) + score divergence
    assert "#e5484d" in body and "#30a46c" in body
    assert "<svg" in body and "<rect" in body
    assert "IoU 0.11" in body
    assert "diverge : oui" in body


def test_run_renders_semantic_fidelity(monkeypatch):
    monkeypatch.setattr(
        console, "execute_request", lambda message: _blender_result_with_observability()
    )
    body = client.post("/console/run", data={"message": "flacon"}).text
    assert "flacon de parfum noir" in body
    assert "bottle" in body
    assert "fidélité : exact" in body


def test_framing_overlay_falls_back_to_pixel_bbox():
    # Sans framing_divergence : la boîte perceptuelle se déduit des pixels + image_size.
    report = {
        "visual_qa": {
            "image_size": [512, 512],
            "checks": {"subject_bbox_detected": {"bbox": [128, 256, 384, 512]}},
        },
        "framing_contract": {"screen_bbox": [0.4, 0.2, 0.6, 0.8]},
    }
    fr = console._framing_overlay(report)
    assert fr["perceptual"] == {"x": 25.0, "y": 50.0, "w": 50.0, "h": 50.0}
    assert fr["projected"]["x"] == 40.0


def test_health_strip_renders(monkeypatch):
    monkeypatch.setattr(
        console,
        "get_runtime_health",
        lambda: {
            "status": "partial",
            "summary": "ollama ok, comfyui down",
            "services": {
                "ollama": {"ready": True, "required": True},
                "comfyui": {"ready": False, "required": False},
            },
        },
    )
    body = client.get("/console/health").text
    assert "ollama" in body and "comfyui" in body
    assert "partial" in body


def test_health_strip_survives_probe_error(monkeypatch):
    def _boom():
        raise RuntimeError("probe failed")

    monkeypatch.setattr(console, "get_runtime_health", _boom)
    response = client.get("/console/health")
    assert response.status_code == 200
    assert "inconnu" in response.text


# --------------------------------------------------------------------------- #
# V1.b — vue Eval / Benchmark (lecture seule des rapports du harness)
# --------------------------------------------------------------------------- #
_SINGLE_RUN_REPORT = {
    "report_schema_version": "1",
    "timestamp": "2026-06-18T02:00:00+00:00",
    "model": "qwen2.5-coder:7b",
    "total_cases": 2,
    "parse_ok_rate": 1.0,
    "mean_score": 0.94,
    "per_field_accuracy": {},
    "case_scores": [
        {"case_id": "c1", "parse_ok": True, "score": 0.95, "error": None},
        {"case_id": "c2", "parse_ok": False, "score": 0.40, "error": "json error"},
    ],
}

_MULTI_RUN_REPORT = {
    "timestamp": "2026-06-18T03:00:00+00:00",
    "model": "qwen2.5-coder:7b",
    "n_runs": 3,
    "n_cases": 1,
    "aggregate": {
        "parse_ok_rate": {"mean": 0.8333},
        "mean_score": {"mean": 0.88},
    },
    "case_aggregates": [
        {"case_id": "c1", "parse_ok_count": 3, "score": {"mean": 0.90}},
    ],
    "common_errors": [{"error_prefix": "JSONDecodeError", "count": 2}],
}


def test_eval_page_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(console, "EVAL_DIR", tmp_path / "none")
    body = client.get("/console/eval").text
    assert "Aucun rapport" in body
    assert "product_render_eval_runner" in body  # commande pour en générer


def test_eval_page_single_run(monkeypatch, tmp_path):
    (tmp_path / "20260618T0200_qwen.json").write_text(
        json.dumps(_SINGLE_RUN_REPORT), encoding="utf-8"
    )
    monkeypatch.setattr(console, "EVAL_DIR", tmp_path)
    body = client.get("/console/eval").text
    assert "qwen2.5-coder:7b" in body
    assert "100 %" in body          # parse_ok_rate 1.0
    assert "0.94" in body           # mean_score
    assert "c1" in body and "c2" in body
    assert "json error" in body     # erreur de cas affichée


def test_eval_page_multi_run(monkeypatch, tmp_path):
    (tmp_path / "20260618T0300_qwen_x3runs.json").write_text(
        json.dumps(_MULTI_RUN_REPORT), encoding="utf-8"
    )
    monkeypatch.setattr(console, "EVAL_DIR", tmp_path)
    body = client.get("/console/eval").text
    assert "83 %" in body           # aggregate.parse_ok_rate.mean 0.8333
    assert "0.88" in body           # aggregate.mean_score.mean
    assert "3/3" in body            # parse_ok_count / n_runs


def test_eval_summary_handles_both_shapes():
    s1 = console.eval_summary(_SINGLE_RUN_REPORT)
    assert s1["parse_ok_rate"] == 1.0 and s1["n_cases"] == 2
    assert s1["cases"][0]["parse_ok_label"] == "✅"
    s2 = console.eval_summary(_MULTI_RUN_REPORT)
    assert round(s2["parse_ok_rate"], 2) == 0.83
    assert s2["cases"][0]["parse_ok_label"] == "3/3"


def test_eval_summary_handles_script_gen_shape():
    # Famille script_gen : métriques sous `aggregate`, cas sous `cases`.
    report = {
        "generated_at_utc": "2026-06-18T02:19:50Z",
        "model": "qwen2.5-coder:7b",
        "aggregate": {"n_cases": 2, "mean_score": 0.967, "generation_ok_rate": 1.0},
        "cases": [
            {"case_id": "sphere", "generation_ok": True, "score": 1.0, "error": None},
            {"case_id": "tube", "generation_ok": False, "score": 0.5, "error": "x"},
        ],
    }
    s = console.eval_summary(report)
    assert s["parse_ok_rate"] == 1.0       # mappé depuis generation_ok_rate
    assert round(s["mean_score"], 2) == 0.97
    assert s["n_cases"] == 2
    assert s["cases"][0]["parse_ok_label"] == "✅"
    assert s["cases"][1]["parse_ok_label"] == "❌"


def test_eval_report_load_rejects_traversal(monkeypatch, tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text("{}")
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setattr(console, "EVAL_DIR", reports)
    assert console.load_eval_report("../secret.json") is None


def test_eval_latest_report_selected_by_default(monkeypatch, tmp_path):
    (tmp_path / "20260101T0000_qwen.json").write_text(json.dumps(_SINGLE_RUN_REPORT))
    (tmp_path / "20260618T0300_qwen_x3runs.json").write_text(json.dumps(_MULTI_RUN_REPORT))
    monkeypatch.setattr(console, "EVAL_DIR", tmp_path)
    # le plus récent (tri lexical décroissant) = le multi-run → "3/3" visible
    body = client.get("/console/eval").text
    assert "3/3" in body


# --------------------------------------------------------------------------- #
# Route artefact — sécurité
# --------------------------------------------------------------------------- #
def test_artifact_served_under_outputs(monkeypatch, tmp_path):
    (tmp_path / "blender" / "run1").mkdir(parents=True)
    img = tmp_path / "blender" / "run1" / "preview.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n-fake")
    monkeypatch.setattr(console, "OUTPUTS", tmp_path.resolve())

    response = client.get("/console/artifact", params={"path": "blender/run1/preview.png"})
    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\n-fake"


def test_artifact_rejects_parent_traversal(monkeypatch, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(console, "OUTPUTS", outputs.resolve())

    response = client.get("/console/artifact", params={"path": "../secret.txt"})
    assert response.status_code == 404


def test_artifact_rejects_sibling_prefix_dir(monkeypatch, tmp_path):
    # Voisin de type `outputs_evil` : même préfixe de nom, dehors du périmètre.
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    evil = tmp_path / "outputs_evil"
    evil.mkdir()
    (evil / "loot.txt").write_text("loot")
    monkeypatch.setattr(console, "OUTPUTS", outputs.resolve())

    response = client.get("/console/artifact", params={"path": "../outputs_evil/loot.txt"})
    assert response.status_code == 404


def test_artifact_rejects_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(console, "OUTPUTS", tmp_path.resolve())
    response = client.get("/console/artifact", params={"path": "blender/nope/preview.png"})
    assert response.status_code == 404


def test_artifact_rejects_escaping_symlink(monkeypatch, tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    link = outputs / "link.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("liens symboliques non supportés sur cette plateforme")
    monkeypatch.setattr(console, "OUTPUTS", outputs.resolve())

    response = client.get("/console/artifact", params={"path": "link.txt"})
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Statique (HTMX vendorisé localement, pas de CDN)
# --------------------------------------------------------------------------- #
def test_htmx_served_locally():
    response = client.get("/console/static/htmx.min.js")
    assert response.status_code == 200
    assert b"htmx" in response.content
