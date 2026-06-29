"""Single source of truth for ComfyUI model names.

scripts/models.manifest is the canonical list. This test locks every other place that
names the checkpoint / upscaler to it, so the historical divergence
(RealVisXL_V5.0_fp16.safetensors vs realvisxlV50_v50Bakedvae.safetensors) cannot return:
manifest == .env.example == docker compose defaults == workflow JSON == client defaults.
"""
import importlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "scripts" / "models.manifest"
ENV_EXAMPLE = CORE / ".env.example"
COMPOSE = REPO_ROOT / "docker" / "docker-compose.app.yml"
WORKFLOWS = CORE / "app" / "workflows" / "comfyui"

LEGACY_NAME = "realvisxlV50_v50Bakedvae.safetensors"

# Ce module verrouille la cohérence des noms de modèles ENTRE des fichiers
# répartis dans tout l'arbre du repo (manifest racine, compose racine,
# .env.example, workflows). Il n'a de sens que si l'arbre complet est présent.
# Lorsqu'on exécute la suite DANS l'image minimale (`/app` = `core/` seul), les
# fichiers racine n'existent pas → on SKIP avec une raison claire au lieu
# d'échouer. La vérification reste active sur l'hôte et en CI (repo complet).
pytestmark = pytest.mark.skipif(
    not (MANIFEST.exists() and COMPOSE.exists() and ENV_EXAMPLE.exists()),
    reason="repo-root consistency files absent (minimal core/ image) — "
           "this cross-tree check runs on the host and in CI.",
)


def _manifest_comfyui() -> dict:
    out = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        f = [x.strip() for x in t.split("|")]
        if f[0] == "comfyui":
            out[f[1]] = f[2]  # subdir -> filename
    return out


def _env_values(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if not t or t.startswith("#") or "=" not in t:
            continue
        k, v = t.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _compose_defaults() -> dict:
    """Extract ${VAR:-default} fallbacks from docker-compose.app.yml."""
    text = COMPOSE.read_text(encoding="utf-8")
    out = {}
    for var in ("COMFYUI_CHECKPOINT_NAME", "COMFYUI_REFINER_CHECKPOINT_NAME", "COMFYUI_UPSCALE_MODEL_NAME"):
        m = re.search(r"\$\{%s:-([^}]+)\}" % re.escape(var), text)
        if m:
            out[var] = m.group(1).strip()
    return out


def test_manifest_has_the_expected_canonical_names():
    m = _manifest_comfyui()
    assert m["checkpoints"] == "RealVisXL_V5.0_fp16.safetensors"
    assert m["upscale_models"] == "4x-UltraSharp.pth"


def test_workflow_json_defaults_match_manifest():
    m = _manifest_comfyui()
    ckpt, upscale = m["checkpoints"], m["upscale_models"]

    draft = json.loads((WORKFLOWS / "generic_draft_v1.json").read_text(encoding="utf-8"))
    final = json.loads((WORKFLOWS / "generic_final_v1.json").read_text(encoding="utf-8"))

    # every CheckpointLoaderSimple uses the manifest checkpoint
    for wf in (draft, final):
        for node in wf.values():
            if node.get("class_type") == "CheckpointLoaderSimple":
                assert node["inputs"]["ckpt_name"] == ckpt
            if node.get("class_type") == "UpscaleModelLoader":
                assert node["inputs"]["model_name"] == upscale


def test_env_example_matches_manifest():
    m = _manifest_comfyui()
    env = _env_values(ENV_EXAMPLE)
    assert env["COMFYUI_CHECKPOINT_NAME"] == m["checkpoints"]
    assert env["COMFYUI_REFINER_CHECKPOINT_NAME"] == m["checkpoints"]
    assert env["COMFYUI_UPSCALE_MODEL_NAME"] == m["upscale_models"]


def test_compose_defaults_match_manifest():
    m = _manifest_comfyui()
    d = _compose_defaults()
    assert d["COMFYUI_CHECKPOINT_NAME"] == m["checkpoints"]
    assert d["COMFYUI_REFINER_CHECKPOINT_NAME"] == m["checkpoints"]
    assert d["COMFYUI_UPSCALE_MODEL_NAME"] == m["upscale_models"]


def test_client_defaults_match_manifest(monkeypatch):
    m = _manifest_comfyui()
    monkeypatch.delenv("COMFYUI_REFINER_CHECKPOINT_NAME", raising=False)
    monkeypatch.delenv("COMFYUI_UPSCALE_MODEL_NAME", raising=False)
    import app.clients.comfyui_client as client
    client = importlib.reload(client)
    assert client.COMFYUI_REFINER_CHECKPOINT_NAME == m["checkpoints"]
    assert client.COMFYUI_UPSCALE_MODEL_NAME == m["upscale_models"]


def test_legacy_name_is_gone_from_production_files():
    targets = [
        WORKFLOWS / "generic_draft_v1.json",
        WORKFLOWS / "generic_final_v1.json",
        CORE / "app" / "clients" / "comfyui_client.py",
        ENV_EXAMPLE,
        MANIFEST,
    ]
    for path in targets:
        assert LEGACY_NAME not in path.read_text(encoding="utf-8"), f"legacy name leaked into {path}"
