"""
Tests du moteur de rejeu (app.engine.reproduce) et du handler /reproduce.

Invariants couverts :
- ComfyUI : intégrité du sidecar vérifiée AVANT tout rejeu ; cache busté
  (POST /free + filename_prefix retargeté vers repro/<orig>/<stamp>) ;
  verdicts exact (pixels identiques) / perceptual (dHash ≤ seuil) /
  different / failed (pas d'output) ; écarts d'environnement rapportés ;
  EXHAUSTIVITÉ : le verdict couvre l'union manifest ∪ sidecars (variante
  sans sidecar → failed, sidecar inconnu → refused, mal indexé → refused) ;
  VALIDATION STRUCTURELLE avant tout appel client : index de manifest
  entier strict ≥ 1 et unique, clés de sidecars idem (dupliqué, non
  entier, ≤ 0, bool → refused sans toucher ComfyUI) ; aucun sidecar +
  variantes attendues → rapport par variante failed, pas un refus opaque ;
  handler API : clé non canonique ("01", "abc", "-1") → refused, jamais
  ignorée ni fusionnée en silence.
- Blender : intégrité du scene.py puis gate de sécurité C1a re-auditée à
  chaque rejeu (un scene.py malveillant est refusé même si son hash
  correspond au manifest) ; retarget fail-closed (jamais d'écrasement du
  run original) ; tier 2 sémantique = juge principal ; preview best-effort.
- Dispatcher : matériel manquant → refused, pipeline inconnu → ValueError.
- Handler API : conversion clés str→int, réponse typée.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.engine import repro
from app.engine import reproduce as rep


# ---------------------------------------------------------------------------
# Fixtures communes
# ---------------------------------------------------------------------------

def _gradient(path: Path, *, flip: bool = False, offset: int = 0, size: int = 64) -> None:
    from PIL import Image

    img = Image.new("L", (size, size))
    img.putdata(
        [
            min(255, (size - 1 - x if flip else x) * 255 // (size - 1) + offset)
            for _y in range(size)
            for x in range(size)
        ]
    )
    img.save(path)


def _workflow(seed: int = 7) -> dict:
    return {
        "3": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 30}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "orig-run/object_basic_v1"}},
    }


def _comfyui_manifest(original_image: Path, workflow: dict) -> dict:
    return {
        "manifest_version": 2,
        "pipeline": "comfyui",
        "request_id": "orig-run",
        "repro": {
            "repro_version": repro.REPRO_VERSION,
            "comfyui": {"comfyui_version": "0.25.0", "pytorch_version": "2.11.0"},
            "models": {"checkpoints": [], "upscale_models": []},
            "variants": [
                {
                    "index": 1,
                    "seed": 7,
                    "workflow_sha256": repro.sha256_canonical_json(workflow),
                    "workflow_file": "workflow_resolved_v1.json",
                    "image": {
                        "filename": original_image.name,
                        "sha256": repro.sha256_file(original_image),
                        "pixels_sha256": repro.sha256_image_pixels(original_image),
                        "dhash": repro.dhash_image(original_image),
                    },
                }
            ],
        },
    }


class ComfyMocks:
    """Substituts des appels client ComfyUI, avec capture du workflow soumis."""

    def __init__(self, replay_image: Path | None):
        self.replay_image = replay_image
        self.queued: list[dict] = []
        self.free_calls = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.clients.comfyui_client.free_execution_cache",
            lambda: self._free(),
        )
        monkeypatch.setattr(
            "app.clients.comfyui_client.queue_prompt", lambda wf: self._queue(wf)
        )
        monkeypatch.setattr(
            "app.clients.comfyui_client.wait_for_completion", lambda pid: {"pid": pid}
        )
        monkeypatch.setattr(
            "app.clients.comfyui_client.extract_output_file",
            lambda history: (
                (self.replay_image.name, str(self.replay_image))
                if self.replay_image
                else (None, None)
            ),
        )
        monkeypatch.setattr(
            "app.clients.comfyui_client.get_comfyui_system_info",
            lambda: {"comfyui_version": "0.25.0", "pytorch_version": "2.11.0"},
        )

    def _free(self) -> bool:
        self.free_calls += 1
        return True

    def _queue(self, workflow: dict) -> str:
        self.queued.append(workflow)
        return "pid-replay"


# ---------------------------------------------------------------------------
# ComfyUI
# ---------------------------------------------------------------------------

def test_comfyui_exact_reproduction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tmp_path / "orig.png"
    _gradient(original)
    replay = tmp_path / "replay" / "img.png"
    replay.parent.mkdir()
    _gradient(replay)  # pixels identiques

    workflow = _workflow()
    mocks = ComfyMocks(replay)
    mocks.install(monkeypatch)

    report = rep.reproduce_comfyui(_comfyui_manifest(original, workflow), {1: workflow})

    assert report["verdict"] == rep.VERDICT_EXACT
    assert mocks.free_calls == 1
    # Le filename_prefix soumis est retargeté : cache busté + run original intact.
    submitted_prefix = mocks.queued[0]["9"]["inputs"]["filename_prefix"]
    assert submitted_prefix.startswith("repro/orig-run/")
    assert submitted_prefix != "orig-run/object_basic_v1"
    # Le reste du workflow est inchangé (le seed notamment).
    assert mocks.queued[0]["3"]["inputs"]["seed"] == 7
    assert (replay.parent / rep.REPORT_FILENAME).exists()
    assert report["environment_diffs"] == []


def test_comfyui_perceptual_when_pixels_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tmp_path / "orig.png"
    _gradient(original)
    replay = tmp_path / "replay" / "img.png"
    replay.parent.mkdir()
    _gradient(replay, offset=2)  # bruit léger : pixels ≠, gradient préservé

    workflow = _workflow()
    ComfyMocks(replay).install(monkeypatch)

    report = rep.reproduce_comfyui(_comfyui_manifest(original, workflow), {1: workflow})

    assert report["verdict"] == rep.VERDICT_PERCEPTUAL
    (variant,) = report["variants"]
    assert variant["image"]["dhash_distance"] <= rep.DEFAULT_DHASH_THRESHOLD


def test_comfyui_different_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tmp_path / "orig.png"
    _gradient(original)
    replay = tmp_path / "replay" / "img.png"
    replay.parent.mkdir()
    _gradient(replay, flip=True)

    workflow = _workflow()
    ComfyMocks(replay).install(monkeypatch)

    report = rep.reproduce_comfyui(_comfyui_manifest(original, workflow), {1: workflow})
    assert report["verdict"] == rep.VERDICT_DIFFERENT


def test_comfyui_integrity_mismatch_refuses_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "orig.png"
    _gradient(original)
    workflow = _workflow()
    tampered = _workflow(seed=666)  # sidecar altéré : hash ≠ manifest

    mocks = ComfyMocks(None)
    mocks.install(monkeypatch)

    report = rep.reproduce_comfyui(_comfyui_manifest(original, workflow), {1: tampered})

    assert report["verdict"] == rep.VERDICT_REFUSED
    assert mocks.queued == []  # rien n'a été soumis
    assert mocks.free_calls == 0


def test_comfyui_precreates_replay_dir_before_queueing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Le dossier du rejeu doit naître côté BACKEND avant que ComfyUI (root)
    # n'y écrive — sinon reproduce_report.json devient inécrivable (droits).
    original = tmp_path / "orig.png"
    _gradient(original)
    replay = tmp_path / "replay" / "img.png"
    replay.parent.mkdir()
    _gradient(replay)
    out_root = tmp_path / "comfy-out"
    monkeypatch.setattr("app.clients.comfyui_client.COMFYUI_OUTPUT_DIR", str(out_root))

    workflow = _workflow()
    ComfyMocks(replay).install(monkeypatch)
    rep.reproduce_comfyui(_comfyui_manifest(original, workflow), {1: workflow})

    stamps = list((out_root / "repro" / "orig-run").iterdir())
    assert len(stamps) == 1 and stamps[0].is_dir()


def test_comfyui_no_output_is_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tmp_path / "orig.png"
    _gradient(original)
    workflow = _workflow()
    ComfyMocks(replay_image=None).install(monkeypatch)

    report = rep.reproduce_comfyui(_comfyui_manifest(original, workflow), {1: workflow})

    assert report["verdict"] == rep.VERDICT_FAILED
    assert "cache" in report["variants"][0]["reason"]


def _variant_record(index: int, seed: int, image: Path, workflow: dict) -> dict:
    return {
        "index": index,
        "seed": seed,
        "workflow_sha256": repro.sha256_canonical_json(workflow),
        "workflow_file": f"workflow_resolved_v{index}.json",
        "image": {
            "filename": image.name,
            "sha256": repro.sha256_file(image),
            "pixels_sha256": repro.sha256_image_pixels(image),
            "dhash": repro.dhash_image(image),
        },
    }


def test_comfyui_missing_sidecar_variant_degrades_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un manifest à DEUX variantes rejoué avec UN seul sidecar : la variante
    absente figure au rapport en failed et pèse sur le verdict global — un
    rejeu incomplet ne peut pas conclure « exact » sur la seule variante
    transmise."""
    original = tmp_path / "orig.png"
    _gradient(original)
    replay = tmp_path / "replay" / "img.png"
    replay.parent.mkdir()
    _gradient(replay)  # variante 1 : pixels identiques

    wf1, wf2 = _workflow(), _workflow(seed=8)
    manifest = _comfyui_manifest(original, wf1)
    manifest["repro"]["variants"].append(_variant_record(2, 8, original, wf2))
    ComfyMocks(replay).install(monkeypatch)

    report = rep.reproduce_comfyui(manifest, {1: wf1})

    by_index = {v["index"]: v for v in report["variants"]}
    assert sorted(by_index) == [1, 2]
    assert by_index[1]["verdict"] == rep.VERDICT_EXACT
    assert by_index[2]["verdict"] == rep.VERDICT_FAILED
    assert "no sidecar" in by_index[2]["reason"]
    assert report["verdict"] == rep.VERDICT_FAILED


def test_comfyui_unknown_extra_sidecar_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un sidecar dont l'index n'existe pas dans le manifest ne peut pas être
    authentifié : refused, et le verdict global suit (le pire gagne)."""
    original = tmp_path / "orig.png"
    _gradient(original)
    replay = tmp_path / "replay" / "img.png"
    replay.parent.mkdir()
    _gradient(replay)

    workflow = _workflow()
    ComfyMocks(replay).install(monkeypatch)

    report = rep.reproduce_comfyui(
        _comfyui_manifest(original, workflow), {1: workflow, 3: _workflow(seed=9)}
    )

    by_index = {v["index"]: v for v in report["variants"]}
    assert by_index[1]["verdict"] == rep.VERDICT_EXACT
    assert by_index[3]["verdict"] == rep.VERDICT_REFUSED
    assert report["verdict"] == rep.VERDICT_REFUSED


def test_comfyui_misindexed_sidecars_refused_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deux sidecars intervertis : chaque hash contredit sa variante →
    intégrité refusée des deux côtés, rien n'est soumis à ComfyUI."""
    original = tmp_path / "orig.png"
    _gradient(original)

    wf1, wf2 = _workflow(), _workflow(seed=8)
    manifest = _comfyui_manifest(original, wf1)
    manifest["repro"]["variants"].append(_variant_record(2, 8, original, wf2))
    mocks = ComfyMocks(None)
    mocks.install(monkeypatch)

    report = rep.reproduce_comfyui(manifest, {1: wf2, 2: wf1})

    assert report["verdict"] == rep.VERDICT_REFUSED
    assert all(v["verdict"] == rep.VERDICT_REFUSED for v in report["variants"])
    assert mocks.queued == []


def _forbid_comfyui_calls(monkeypatch: pytest.MonkeyPatch) -> "ComfyMocks":
    """Mocks + system_info piégé : la moindre requête ComfyUI fait échouer
    le test (les refus structurels ne doivent RIEN appeler, pas même
    /system_stats pour les environment_diffs)."""
    mocks = ComfyMocks(None)
    mocks.install(monkeypatch)
    monkeypatch.setattr(
        "app.clients.comfyui_client.get_comfyui_system_info",
        lambda: (_ for _ in ()).throw(AssertionError("must not call ComfyUI")),
    )
    return mocks


@pytest.mark.parametrize("bad_index", ["1", None, 0, -1, True])
def test_comfyui_invalid_manifest_index_refused_without_calls(
    bad_index, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "orig.png"
    _gradient(original)
    workflow = _workflow()
    manifest = _comfyui_manifest(original, workflow)
    manifest["repro"]["variants"][0]["index"] = bad_index
    mocks = _forbid_comfyui_calls(monkeypatch)

    report = rep.reproduce_comfyui(manifest, {1: workflow})

    assert report["verdict"] == rep.VERDICT_REFUSED
    assert "index invalid" in report["error"]
    assert mocks.queued == [] and mocks.free_calls == 0


def test_comfyui_duplicate_manifest_index_refused_without_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deux variantes de même index s'écraseraient en silence dans le
    mapping : refus global explicite, aucun appel client."""
    original = tmp_path / "orig.png"
    _gradient(original)
    wf1, wf2 = _workflow(), _workflow(seed=8)
    manifest = _comfyui_manifest(original, wf1)
    manifest["repro"]["variants"].append(_variant_record(1, 8, original, wf2))
    mocks = _forbid_comfyui_calls(monkeypatch)

    report = rep.reproduce_comfyui(manifest, {1: wf1})

    assert report["verdict"] == rep.VERDICT_REFUSED
    assert "duplicated" in report["error"]
    assert mocks.queued == [] and mocks.free_calls == 0


@pytest.mark.parametrize("bad_key", [0, -3, True])
def test_comfyui_invalid_sidecar_key_refused_engine_side(
    bad_key, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le moteur reste sûr appelé DIRECTEMENT (sans le parsing du handler)."""
    original = tmp_path / "orig.png"
    _gradient(original)
    workflow = _workflow()
    mocks = _forbid_comfyui_calls(monkeypatch)

    report = rep.reproduce_comfyui(
        _comfyui_manifest(original, workflow), {bad_key: workflow}
    )

    assert report["verdict"] == rep.VERDICT_REFUSED
    assert "sidecar index invalid" in report["error"]
    assert mocks.queued == [] and mocks.free_calls == 0


def test_comfyui_no_sidecars_reports_missing_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aucun sidecar fourni mais des variantes attendues : rapport PAR
    variante (failed, « no sidecar »), jamais un rejeu prétendu réussi ni
    un refus opaque — et aucun rejeu ne part."""
    original = tmp_path / "orig.png"
    _gradient(original)
    wf1, wf2 = _workflow(), _workflow(seed=8)
    manifest = _comfyui_manifest(original, wf1)
    manifest["repro"]["variants"].append(_variant_record(2, 8, original, wf2))
    mocks = ComfyMocks(None)
    mocks.install(monkeypatch)

    report = rep.reproduce_run("comfyui", manifest, workflows=None)

    assert report["verdict"] == rep.VERDICT_FAILED
    by_index = {v["index"]: v for v in report["variants"]}
    assert sorted(by_index) == [1, 2]
    assert all(
        v["verdict"] == rep.VERDICT_FAILED and "no sidecar" in v["reason"]
        for v in report["variants"]
    )
    assert mocks.queued == [] and mocks.free_calls == 0


def test_comfyui_environment_diff_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tmp_path / "orig.png"
    _gradient(original)
    replay = tmp_path / "replay" / "img.png"
    replay.parent.mkdir()
    _gradient(replay)

    workflow = _workflow()
    manifest = _comfyui_manifest(original, workflow)
    manifest["repro"]["comfyui"]["comfyui_version"] = "0.20.0"  # enregistré ≠ courant
    ComfyMocks(replay).install(monkeypatch)

    report = rep.reproduce_comfyui(manifest, {1: workflow})

    assert {"field": "comfyui_version", "recorded": "0.20.0", "current": "0.25.0"} in report[
        "environment_diffs"
    ]


# ---------------------------------------------------------------------------
# Blender
# ---------------------------------------------------------------------------

_ORIG_DIR = "/outputs/blender/orig-run"

_SCENE_REPORT = {
    "template_name": "product_render",
    "object_count": 6,
    "status": "passed",
    "violations": [],
}


def _scene_py(body: str = "import bpy\n") -> str:
    return (
        f'OUTPUT_BLEND_PATH = r"{_ORIG_DIR}/scene.blend"\n'
        f'OUTPUT_RENDER_PATH = r"{_ORIG_DIR}/preview.png"\n\n'
        f"{body}"
    )


def _blender_manifest(scene_py: str, *, preview: Path | None = None) -> dict:
    return {
        "manifest_version": 2,
        "pipeline": "blender",
        "request_id": "orig-run",
        "output_dir": _ORIG_DIR,
        "input": {"prompt": "une bouteille"},
        "future": {"template_used": "product_render"},
        "repro": {
            "repro_version": repro.REPRO_VERSION,
            "blender_version": "Blender 5.1.1",
            "scene_py_sha256": repro.sha256_text(scene_py),
            "scene_report_semantic_sha256": repro.semantic_scene_report_hash(_SCENE_REPORT),
            "preview_png": {
                "sha256": repro.sha256_file(preview) if preview else None,
                "pixels_sha256": repro.sha256_image_pixels(preview) if preview else None,
                "dhash": repro.dhash_image(preview) if preview else None,
            },
        },
    }


class BlenderRunMock:
    """Substitut de run_blender_script : capture la requête, fabrique le résultat."""

    def __init__(self, *, status: str = "success", scene_report: dict | None = None,
                 preview_writer=None):
        self.status = status
        self.scene_report = scene_report if scene_report is not None else dict(_SCENE_REPORT)
        self.preview_writer = preview_writer
        self.requests: list = []

    def install(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(
            "app.clients.blender_client.BLENDER_OUTPUT_DIR", str(tmp_path / "blender-out")
        )
        monkeypatch.setattr(
            "app.clients.blender_client.run_blender_script", lambda request: self._run(request)
        )
        monkeypatch.setattr("app.engine.repro.blender_version", lambda: "Blender 5.1.1")

    def _run(self, request):
        from app.engine.blender_types import BlenderResult

        self.requests.append(request)
        if self.preview_writer:
            self.preview_writer(Path(request.render_path))
        return BlenderResult(
            status=self.status,
            request_id=request.request_id,
            script_path=request.script_path,
            output_path=request.output_path if self.status == "success" else None,
            render_path=request.render_path,
            output_dir=request.output_dir,
            returncode=0 if self.status == "success" else 1,
            stdout=None,
            stderr=None,
            error=None if self.status == "success" else "boom",
            scene_report=self.scene_report,
        )


def test_blender_exact_reproduction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = _scene_py()
    mock = BlenderRunMock()
    mock.install(monkeypatch, tmp_path)

    report = rep.reproduce_blender(_blender_manifest(scene), scene)

    assert report["verdict"] == rep.VERDICT_EXACT
    (request,) = mock.requests
    # Retarget : le script exécuté vise le répertoire NEUF, plus l'original.
    assert _ORIG_DIR not in request.script_content
    assert request.output_dir in request.script_content
    assert request.security_gate["status"] == "passed"
    # Le template pilote le correcteur runtime : sans propagation, le rejeu
    # n'exécute pas le même calcul (corrections sautées, preview divergent).
    assert request.template_used == "product_render"
    # Le scene.py retargeté est ÉCRIT sur disque : run_blender_script exécute
    # `--python script_path`, pas script_content.
    assert Path(request.script_path).read_text(encoding="utf-8") == request.script_content
    semantic = next(c for c in report["checks"] if c["name"] == "scene_report_semantic")
    assert semantic["verdict"] == rep.VERDICT_EXACT


def test_blender_integrity_mismatch_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = _scene_py()
    mock = BlenderRunMock()
    mock.install(monkeypatch, tmp_path)

    report = rep.reproduce_blender(_blender_manifest(scene), scene + "# tampered\n")

    assert report["verdict"] == rep.VERDICT_REFUSED
    assert mock.requests == []


def test_blender_security_gate_blocks_even_with_matching_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Un manifest forgé peut hasher un script malveillant : la gate C1a est
    # re-auditée au rejeu et refuse quand même.
    malicious = _scene_py("eval('__import__(\\'os\\')')\n")
    mock = BlenderRunMock()
    mock.install(monkeypatch, tmp_path)

    report = rep.reproduce_blender(_blender_manifest(malicious), malicious)

    assert report["verdict"] == rep.VERDICT_REFUSED
    assert mock.requests == []
    gate_check = next(c for c in report["checks"] if c["name"] == "security_gate")
    assert gate_check["violations"]


def test_blender_missing_original_dir_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = "import bpy\n"  # aucun chemin d'origine à retarget
    manifest = _blender_manifest(scene)
    mock = BlenderRunMock()
    mock.install(monkeypatch, tmp_path)

    report = rep.reproduce_blender(manifest, scene)

    assert report["verdict"] == rep.VERDICT_FAILED
    assert "overwrite" in report["error"]
    assert mock.requests == []


def test_blender_run_failure_is_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = _scene_py()
    BlenderRunMock(status="timeout").install(monkeypatch, tmp_path)

    report = rep.reproduce_blender(_blender_manifest(scene), scene)
    assert report["verdict"] == rep.VERDICT_FAILED


def test_blender_semantic_mismatch_is_different(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _scene_py()
    changed_report = {**_SCENE_REPORT, "object_count": 7}
    BlenderRunMock(scene_report=changed_report).install(monkeypatch, tmp_path)

    report = rep.reproduce_blender(_blender_manifest(scene), scene)
    assert report["verdict"] == rep.VERDICT_DIFFERENT


def test_blender_preview_perceptual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    orig_preview = tmp_path / "orig_preview.png"
    _gradient(orig_preview)

    def write_shifted(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _gradient(path, offset=2)

    scene = _scene_py()
    BlenderRunMock(preview_writer=write_shifted).install(monkeypatch, tmp_path)

    report = rep.reproduce_blender(_blender_manifest(scene, preview=orig_preview), scene)

    assert report["verdict"] == rep.VERDICT_PERCEPTUAL  # sémantique exact, preview dérive
    preview = next(c for c in report["checks"] if c["name"] == "preview_png")
    assert preview["verdict"] == rep.VERDICT_PERCEPTUAL


# ---------------------------------------------------------------------------
# Dispatcher + handler API
# ---------------------------------------------------------------------------

def test_dispatch_refuses_missing_material() -> None:
    assert rep.reproduce_run("comfyui", {})["verdict"] == rep.VERDICT_REFUSED
    assert rep.reproduce_run("blender", {})["verdict"] == rep.VERDICT_REFUSED


def test_dispatch_unknown_pipeline_raises() -> None:
    with pytest.raises(ValueError):
        rep.reproduce_run("maya", {})


def test_api_handler_converts_keys_and_types(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import reproduce as api_reproduce
    from app.schemas import ReproduceRequest

    captured = {}

    def fake_run(pipeline, manifest, *, workflows=None, scene_py=None):
        captured.update(pipeline=pipeline, workflows=workflows)
        return {
            "pipeline": pipeline,
            "verdict": "exact",
            "dhash_threshold": 4,
            "variants": [],
            "environment_diffs": [],
        }

    monkeypatch.setattr("app.main.reproduce_run", fake_run)
    payload = ReproduceRequest(
        pipeline="comfyui", manifest={"request_id": "x"}, workflows={"1": {"3": {}}}
    )
    response = api_reproduce(payload)

    assert response.verdict == "exact"
    assert captured["workflows"] == {1: {"3": {}}}  # clés converties str → int


@pytest.mark.parametrize("bad_key", ["01", "abc", "-1", "", "1.0"])
def test_api_handler_refuses_non_canonical_keys(
    monkeypatch: pytest.MonkeyPatch, bad_key: str
) -> None:
    """Une clé non canonique est REFUSÉE, jamais ignorée : l'ancien
    `int(k) if k.isdigit()` laissait "abc"/"-1" disparaître en silence et
    "01" écraser "1". L'engine n'est même pas appelé."""
    from app.main import reproduce as api_reproduce
    from app.schemas import ReproduceRequest

    engine_calls: list[str] = []
    monkeypatch.setattr(
        "app.main.reproduce_run",
        lambda *args, **kwargs: engine_calls.append("called") or {},
    )

    response = api_reproduce(
        ReproduceRequest(pipeline="comfyui", manifest={}, workflows={bad_key: {}})
    )

    assert response.verdict == "refused"
    assert bad_key in (response.error or "")
    assert engine_calls == []


def test_api_handler_refuses_colliding_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """"1" et "01" convergeraient vers la même variante après int() : la
    forme canonique exigée rend la collision impossible — refus explicite."""
    from app.main import reproduce as api_reproduce
    from app.schemas import ReproduceRequest

    engine_calls: list[str] = []
    monkeypatch.setattr(
        "app.main.reproduce_run",
        lambda *args, **kwargs: engine_calls.append("called") or {},
    )

    response = api_reproduce(
        ReproduceRequest(
            pipeline="comfyui",
            manifest={},
            workflows={"1": {"a": {}}, "01": {"b": {}}},
        )
    )

    assert response.verdict == "refused"
    assert "01" in (response.error or "")
    assert engine_calls == []
