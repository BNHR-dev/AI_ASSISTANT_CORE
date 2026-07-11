"""
Console V0 — exécution locale et observabilité.

Une UI web minimale, locale, posée *au-dessus* de l'API : elle appelle le
service existant `execute_request(...)` (le même que `/execute`) et présente
le résultat. Elle ne duplique aucune logique de routage/exécution et ne prend
aucune décision métier — uniquement de la préparation de vue et du rendu.

Hors périmètre V0 : auth, persistance, annulation, WebSocket, streaming,
exposition internet. Voir la note portfolio « Console — UI dédiée ».
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.engine.executor import execute_request
from app.engine.reproduce import reproduce_run
from app.engine.run_events import EVENTS_FILENAME, get_run_events_dir
from app.engine.runtime_debug import get_runtime_health

# Chemins ancrés sur ce fichier, indépendants du répertoire courant.
PROJECT_ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = PROJECT_ROOT / "console_templates"
STATIC_DIR = (PROJECT_ROOT / "console_static").resolve()
def _dir_from_env(var: str, default: Path) -> Path:
    """Dossier depuis une variable d'env (chemin ABSOLU) ; sinon `default`."""
    raw = os.getenv(var, "").strip()
    return Path(raw).resolve() if raw and os.path.isabs(raw) else default.resolve()


# Dossier outputs/ local du backend (rétrocompat / défaut).
OUTPUTS = (PROJECT_ROOT / "outputs").resolve()
# Dossiers de runs par pipeline = LES MÊMES que ceux où le backend écrit
# (COMFYUI_OUTPUT_DIR / BLENDER_OUTPUT_DIR). Permet de pointer la Console sur une
# archive rangée (ex. ~/projects/AAC_Outputs/ComfyUI/Linux).
COMFYUI_RUNS_DIR = _dir_from_env("COMFYUI_OUTPUT_DIR", OUTPUTS / "comfyui")
BLENDER_RUNS_DIR = _dir_from_env("BLENDER_OUTPUT_DIR", OUTPUTS / "blender")
# Racines autorisées pour servir des fichiers (vignettes) et ouvrir des dossiers.
_SERVE_ROOTS = list({OUTPUTS, COMFYUI_RUNS_DIR, BLENDER_RUNS_DIR})
# Rapports d'eval (sous le dossier des runs Blender).
EVAL_DIR = BLENDER_RUNS_DIR / "_eval_reports"

router = APIRouter(prefix="/console", tags=["console"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --------------------------------------------------------------------------- #
# Runs asynchrones (5 v2) — registre des résultats + flux SSE
# --------------------------------------------------------------------------- #
# Résultats des runs lancés en arrière-plan par CETTE Console. En mémoire,
# borné : la Console est locale et mono-process (contrat V0 conservé) ; la
# vérité durable reste sur disque (events.jsonl, state.json, manifests).
_RESULTS: OrderedDict[str, dict] = OrderedDict()
_RESULTS_MAX = 50

# request_id nomme un dossier sous outputs/runs/ : charset strict, jamais de
# séparateur de chemin (anti-traversal, même garde d'esprit que /artifact).
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")

# Un run Blender/ComfyUI se compte en minutes ; au-delà, le flux s'arrête
# proprement et le client tente le résultat (plutôt qu'un socket zombie).
_STREAM_TIMEOUT_SECONDS = 45 * 60
_STREAM_POLL_SECONDS = 0.5


def _store_result(request_id: str, entry: dict) -> None:
    _RESULTS[request_id] = entry
    _RESULTS.move_to_end(request_id)
    while len(_RESULTS) > _RESULTS_MAX:
        _RESULTS.popitem(last=False)


def _run_in_background(request_id: str, runner) -> None:
    """Exécute `runner()` et range résultat OU exception dans le registre —
    la Console ne perd jamais l'issue d'un run qu'elle a lancé."""
    try:
        _store_result(request_id, {"result": runner()})
    except Exception as exc:  # noqa: BLE001 — l'issue doit atterrir dans l'UI
        _store_result(request_id, {"exception": f"{type(exc).__name__}: {exc}"})


def _render_timeline_row(event: dict) -> str:
    """Une ligne de timeline (même partial que le détail de run)."""
    row = _event_row(event, max_step_ms=0)
    return templates.get_template("_tl_row.html").render(row=row)


async def _sse_event_stream(request: Request, request_id: str, *, tail: bool):
    """Suit events.jsonl du run et pousse chaque événement en SSE.

    `tail=True` (reprise) : ne rejoue pas l'historique, ne pousse que les
    événements POSTÉRIEURS à la connexion. Fin de flux (`done`) sur
    run.finished, sur résultat déjà rangé au registre (run éclair ou crash
    avant tout événement), sur déconnexion client, ou au timeout.
    """
    events_file = get_run_events_dir().resolve() / request_id / EVENTS_FILENAME
    offset = events_file.stat().st_size if (tail and events_file.is_file()) else 0
    deadline = asyncio.get_event_loop().time() + _STREAM_TIMEOUT_SECONDS

    def _sse(event_name: str, payload: str) -> str:
        data = "".join(f"data: {line}\n" for line in payload.splitlines() or [""])
        return f"event: {event_name}\n{data}\n"

    while True:
        if await request.is_disconnected():
            return
        if asyncio.get_event_loop().time() > deadline:
            yield _sse("done", "timeout")
            return

        finished = False
        if events_file.is_file():
            try:
                with events_file.open("r", encoding="utf-8") as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset = f.tell()
            except OSError:
                chunk = ""
            for line in chunk.splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield _sse("row", _render_timeline_row(event))
                    if event.get("kind") == "run.finished":
                        finished = True

        if finished:
            yield _sse("done", "finished")
            return
        if request_id in _RESULTS:
            # Résultat rangé sans run.finished visible : run éclair (events
            # désactivés) ou exception avant le premier événement.
            yield _sse("done", "result-ready")
            return
        await asyncio.sleep(_STREAM_POLL_SECONDS)


def _under_serve_roots(p: Path) -> bool:
    """Le chemin résolu est-il sous une des racines servables ? (anti-traversal)"""
    return any(p.is_relative_to(root) for root in _SERVE_ROOTS)


def _safe_resolve(base: Path, raw: str) -> Path | None:
    """Résout `raw` sous `base` ou renvoie None.

    `resolve()` suit les liens symboliques : un lien qui sort de `base` aboutit
    donc hors de `base` et est rejeté par `is_relative_to`. Couvre aussi `../`
    et les voisins de type `outputs_evil` (comparaison par composants).
    """
    try:
        resolved = (base / raw).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_relative_to(base):
        return None
    return resolved


def _artifact_url(fs_path: str | None) -> str | None:
    """URL Console pour un fichier local sous `outputs/`, sinon None.

    Sert UNIQUEMENT les artefacts locaux (ex. rendu Blender). Les images
    ComfyUI passent par leurs propres `artifact_view_urls` (voir build_view).
    """
    if not fs_path:
        return None
    p = Path(fs_path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    try:
        resolved = p.resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not _under_serve_roots(resolved):
        return None
    return "/console/artifact?path=" + quote(str(resolved))


def _bbox_pct(frac) -> dict | None:
    """[x0,y0,x1,y1] en fractions 0–1 → pourcentages pour l'overlay SVG."""
    if not frac or len(frac) != 4:
        return None
    x0, y0, x1, y1 = frac
    return {
        "x": round(x0 * 100, 2),
        "y": round(y0 * 100, 2),
        "w": round((x1 - x0) * 100, 2),
        "h": round((y1 - y0) * 100, 2),
    }


def _framing_overlay(scene_report) -> dict | None:
    """Les deux cadrages à superposer sur le rendu : perceptuel (🔴 pixels)
    vs projeté (🟢 géométrie). Données déjà présentes dans le scene_report ;
    la Console ne fait que les mettre en forme."""
    if not isinstance(scene_report, dict):
        return None
    fc = scene_report.get("framing_contract") or {}
    vq = scene_report.get("visual_qa") or {}
    fd = fc.get("framing_divergence") or {}

    perceptual = fd.get("perceptual_bbox_fraction")
    if not perceptual:
        bbox = ((vq.get("checks") or {}).get("subject_bbox_detected") or {}).get("bbox")
        size = vq.get("image_size")
        if bbox and size and len(bbox) == 4 and len(size) == 2 and size[0] and size[1]:
            w, h = size[0], size[1]
            perceptual = [bbox[0] / w, bbox[1] / h, bbox[2] / w, bbox[3] / h]

    projected = fd.get("projected_bbox_fraction") or fc.get("screen_bbox")

    red = _bbox_pct(perceptual)
    green = _bbox_pct(projected)
    if not red and not green:
        return None
    return {
        "perceptual": red,
        "projected": green,
        "iou": fd.get("iou"),
        "diverged": fd.get("diverged"),
    }


def _semantic_fidelity(manifest) -> dict | None:
    """Sujet déclaré + fidélité (exact/approximate) : le « théière → cube »
    devient explicite. Source : manifest.future.product_render_intent.subject."""
    if not isinstance(manifest, dict):
        return None
    subj = (
        ((manifest.get("future") or {}).get("product_render_intent") or {}).get("subject")
        or {}
    )
    if not subj.get("kind") and not subj.get("label"):
        return None
    return {
        "kind": subj.get("kind"),
        "label": subj.get("label"),
        "kind_fidelity": subj.get("kind_fidelity"),
    }


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def list_eval_reports() -> list[str]:
    """Noms des rapports d'eval, du plus récent au plus ancien (tri lexical =
    chronologique par convention du runner)."""
    if not EVAL_DIR.is_dir():
        return []
    return sorted(
        (p.name for p in EVAL_DIR.glob("*.json") if p.is_file()), reverse=True
    )


def load_eval_report(name: str) -> dict | None:
    """Charge un rapport, en restant strictement sous EVAL_DIR."""
    resolved = _safe_resolve(EVAL_DIR, name)
    if resolved is None or resolved.suffix != ".json" or not resolved.is_file():
        return None
    try:
        return json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def eval_summary(r: dict) -> dict:
    """Normalise un rapport en une vue d'affichage, tolérant aux deux familles
    de harness :
    - product_render : `parse_ok_rate` (scalaire ou multi-run `aggregate.*.mean`),
      cas sous `case_scores` / `case_aggregates` ;
    - script_gen     : `generation_ok_rate` + `mean_score` sous `aggregate`,
      cas sous `cases`.
    Lecture seule : la Console ne recalcule aucune métrique."""
    agg = r.get("aggregate") or {}

    def metric(*keys):
        for src in (agg, r):
            for key in keys:
                block = src.get(key)
                if isinstance(block, dict) and "mean" in block:
                    return _num(block.get("mean"))
                if _num(block) is not None:
                    return _num(block)
        return None

    n_runs = r.get("n_runs")
    raw_cases = (
        r.get("case_scores") or r.get("case_aggregates") or r.get("cases") or []
    )
    cases = []
    for c in raw_cases:
        if "parse_ok_count" in c:  # product_render multi-run
            cnt = c.get("parse_ok_count")
            ok_label = f"{cnt}/{n_runs}" if (cnt is not None and n_runs) else str(cnt)
        elif "parse_ok" in c:  # product_render single-run
            ok_label = "✅" if c.get("parse_ok") else "❌"
        elif "generation_ok" in c:  # script_gen
            ok_label = "✅" if c.get("generation_ok") else "❌"
        else:
            ok_label = "—"
        score = c.get("score")
        score = _num(score.get("mean")) if isinstance(score, dict) else _num(score)
        cases.append(
            {"case_id": c.get("case_id"), "parse_ok_label": ok_label,
             "score": score, "error": c.get("error")}
        )

    n_cases = (
        _num(agg.get("n_cases")) or r.get("total_cases") or r.get("n_cases")
        or len(cases)
    )
    return {
        "model": r.get("model"),
        "timestamp": r.get("timestamp") or r.get("generated_at_utc"),
        "n_cases": n_cases,
        "n_runs": n_runs,
        "parse_ok_rate": metric("parse_ok_rate", "generation_ok_rate"),
        "mean_score": metric("mean_score"),
        "cases": cases,
        "common_errors": r.get("common_errors") or [],
    }


# --------------------------------------------------------------------------- #
# Outputs — historique des runs sur disque (lecture du registre par run)
# --------------------------------------------------------------------------- #
def _describe_run(d: Path, kind: str) -> dict:
    """Décrit un dossier de run : id, type, date, chemin, vignette, manifest.
    Lecture seule ; tolérant aux dossiers incomplets."""
    try:
        mtime = d.stat().st_mtime
    except OSError:
        mtime = 0
    manifest = None
    mf = d / "manifest.json"
    if mf.is_file():
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None
    # Vignette : preview.png (3D) sinon la 1re image PNG du dossier.
    img = None
    if kind == "3d":
        preview = d / "preview.png"
        img = preview if preview.is_file() else None
    if img is None:
        pngs = sorted(d.glob("*.png"))
        img = pngs[0] if pngs else None
    prompt = None
    if isinstance(manifest, dict):
        prompt = (manifest.get("input") or {}).get("prompt")
    return {
        "id": d.name,
        "kind": kind,
        "path": str(d),
        "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "—",
        "mtime": mtime,
        "thumb_url": _artifact_url(str(img)) if img else None,
        "manifest": manifest,
        "prompt": prompt,
    }


def list_runs() -> list[dict]:
    """Tous les runs (2D ComfyUI + 3D Blender), du plus récent au plus ancien.
    Ignore les dossiers techniques (`_eval_reports`, `_trajectories`) et les
    rejeux (`repro*` — des copies de vérification, pas des runs à part entière)."""
    runs: list[dict] = []
    for base, kind in ((COMFYUI_RUNS_DIR, "2d"), (BLENDER_RUNS_DIR, "3d")):
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("repro"):
                runs.append(_describe_run(d, kind))
    runs.sort(key=lambda r: r["mtime"], reverse=True)
    return runs


# --------------------------------------------------------------------------- #
# Recherche (5 v2b) — tokens normalisés sur les prompts
# --------------------------------------------------------------------------- #
# Matching en pur Python plutôt qu'un index FTS5 : la liste des runs (et
# leurs manifests) est DÉJÀ chargée en mémoire à chaque affichage d'Outputs,
# et à l'échelle locale (10³ runs) le scan coûte des millisecondes. Un index
# persistant ajouterait invalidation, backfill et syntaxe de requête pour un
# gain nul aujourd'hui — le contrat de route (?q=) ne changera pas si un
# vrai moteur devient nécessaire.

def _fold_text(text: str) -> str:
    """Minuscule + accents retirés + ponctuation aplatie (même esprit que
    normalize_text du classifieur : « théière » ⇔ « theiere »)."""
    import unicodedata

    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^\w\s]", " ", text)


def filter_runs(query: str, runs: list[dict]) -> list[dict]:
    """Runs dont le prompt contient TOUS les tokens de la requête (préfixe
    inclus : « mont » matche « montre » — recherche au fil de la frappe).
    Requête vide → tout ; l'ordre (plus récent d'abord) est préservé."""
    tokens = _fold_text(query).split()
    if not tokens:
        return runs
    matched = []
    for run in runs:
        haystack = _fold_text(f"{run.get('prompt') or ''} {run['id']}")
        if all(token in haystack for token in tokens):
            matched.append(run)
    return matched


# --------------------------------------------------------------------------- #
# Comparaison (5 v2b) — deux runs côte à côte + diff des blocs repro
# --------------------------------------------------------------------------- #

def _repro_of(detail: dict) -> dict:
    manifest = detail.get("manifest")
    if not isinstance(manifest, dict):
        return {}
    repro_block = manifest.get("repro")
    return repro_block if isinstance(repro_block, dict) else {}


def _first_variant(repro_block: dict) -> dict:
    variants = repro_block.get("variants") or []
    return variants[0] if variants and isinstance(variants[0], dict) else {}


def _compare_fields(detail: dict) -> dict[str, str | None]:
    """Les champs comparables d'un run, extraits de son manifest. Chaque
    valeur est une chaîne d'affichage (hash raccourci) ou None si absente."""
    manifest = detail.get("manifest") if isinstance(detail.get("manifest"), dict) else {}
    repro_block = _repro_of(detail)
    variant = _first_variant(repro_block)
    image = variant.get("image") if isinstance(variant.get("image"), dict) else {}
    comfy = repro_block.get("comfyui") if isinstance(repro_block.get("comfyui"), dict) else {}
    models = repro_block.get("models") if isinstance(repro_block.get("models"), dict) else {}
    checkpoints = ", ".join(
        f"{m.get('name')} ({_short_hash(m.get('sha256')) or 'sha —'})"
        for m in models.get("checkpoints") or []
    ) or None

    seeds = ", ".join(
        str(v.get("seed")) for v in repro_block.get("variants") or [] if v.get("seed") is not None
    ) or None
    engine = repro_block.get("blender_version") or (
        f"ComfyUI {comfy['comfyui_version']}" if comfy.get("comfyui_version") else None
    )
    preview = repro_block.get("preview_png") if isinstance(repro_block.get("preview_png"), dict) else {}

    return {
        "prompt": (manifest.get("input") or {}).get("prompt"),
        "pipeline": manifest.get("pipeline"),
        "status": manifest.get("status"),
        "seed": seeds,
        "engine": engine,
        "torch": comfy.get("pytorch_version"),
        "AAC commit": _short_hash(repro_block.get("aac_git_commit"), keep=9),
        "checkpoint": checkpoints,
        "workflow hash": _short_hash(variant.get("workflow_sha256")),
        "scene hash (semantic)": _short_hash(repro_block.get("scene_report_semantic_sha256")),
        "image pixels": _short_hash(image.get("pixels_sha256") or preview.get("pixels_sha256")),
    }


def build_compare_view(detail_a: dict, detail_b: dict) -> dict:
    """Vue de comparaison : les deux détails + lignes de diff alignées.
    Une ligne est « changed » si les deux valeurs existent et diffèrent —
    un champ absent d'un côté (pipelines différents) n'est pas un écart."""
    fields_a = _compare_fields(detail_a)
    fields_b = _compare_fields(detail_b)
    rows = []
    for label in fields_a:
        a_value, b_value = fields_a[label], fields_b[label]
        if a_value is None and b_value is None:
            continue
        rows.append({
            "label": label,
            "a": a_value,
            "b": b_value,
            "changed": a_value is not None and b_value is not None and a_value != b_value,
        })

    # Distance perceptuelle entre les deux images (dHash des manifests).
    from app.engine import repro as repro_utils

    def _dhash(detail: dict) -> str | None:
        block = _repro_of(detail)
        variant_image = _first_variant(block).get("image") or {}
        preview = block.get("preview_png") if isinstance(block.get("preview_png"), dict) else {}
        return variant_image.get("dhash") or preview.get("dhash")

    distance = repro_utils.dhash_distance(_dhash(detail_a), _dhash(detail_b))

    return {"a": detail_a, "b": detail_b, "rows": rows, "dhash_distance": distance}


def _load_json(p: Path) -> dict | None:
    """Lecture JSON tolérante (fichier absent/corrompu -> None)."""
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Timeline — lecture du journal d'événements du run (events.jsonl)
# --------------------------------------------------------------------------- #
def _load_run_events(request_id: str) -> list[dict]:
    """Événements du run, dans l'ordre d'écriture. Lecture seule, tolérante :
    journal absent (runs antérieurs au chantier events) → liste vide."""
    events_file = get_run_events_dir().resolve() / request_id / EVENTS_FILENAME
    if not events_file.is_file():
        return []
    events = []
    try:
        for line in events_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
    except OSError:
        return []
    return events


def _event_row(event: dict, max_step_ms: int) -> dict:
    """Une ligne de timeline : heure, famille (pour la couleur), libellé court,
    durée + barre proportionnelle pour les fins de step."""
    kind = event.get("kind") or "?"
    data = event.get("data") or {}
    family = kind.split(".", 1)[0]  # run | route | plan | step

    label = ""
    if kind == "route.decided":
        label = " · ".join(
            str(x) for x in (data.get("task_type"), data.get("selected_model")) if x
        )
    elif kind == "plan.built":
        steps = data.get("steps") or []
        label = f"{len(steps)} step{'s' if len(steps) != 1 else ''} · {data.get('strategy') or ''}".strip(" ·")
    elif family == "step":
        label = str(data.get("step_id") or "")
        if kind == "step.finished" and data.get("status"):
            label += f" → {data['status']}"
    elif kind == "run.finished":
        label = str((data.get("execution_summary") or {}).get("status") or "")
    elif kind == "run.resumed":
        restored = data.get("restored_step_ids") or []
        label = f"restored {len(restored)} step{'s' if len(restored) != 1 else ''}"

    duration_ms = data.get("duration_ms") if kind in ("step.finished", "run.finished") else None
    ts = str(event.get("ts") or "")
    time_short = ts[11:23] if len(ts) >= 23 else ts

    is_error = (
        kind == "step.blocked"
        or (kind == "step.finished" and data.get("status") in ("error", "blocked"))
        or (kind == "run.finished" and label in ("failed", "degraded"))
    )
    bar_pct = None
    if kind == "step.finished" and isinstance(duration_ms, int) and max_step_ms > 0:
        bar_pct = max(1, round(duration_ms / max_step_ms * 100))

    return {
        "time": time_short,
        "kind": kind,
        "family": family,
        "label": label,
        "duration_ms": duration_ms,
        "bar_pct": bar_pct,
        "error": is_error,
        "error_text": (data.get("error") or "") if is_error else "",
    }


def _timeline_view(request_id: str) -> dict | None:
    """Vue timeline du run : lignes + durée totale. None si aucun journal."""
    events = _load_run_events(request_id)
    if not events:
        return None
    max_step_ms = max(
        (
            (e.get("data") or {}).get("duration_ms") or 0
            for e in events
            if e.get("kind") == "step.finished"
        ),
        default=0,
    )
    total_ms = next(
        (
            (e.get("data") or {}).get("duration_ms")
            for e in reversed(events)
            if e.get("kind") == "run.finished"
        ),
        None,
    )
    return {
        "rows": [_event_row(e, max_step_ms) for e in events],
        "total_ms": total_ms,
    }


# --------------------------------------------------------------------------- #
# Provenance — badges lisibles d'abord, hashes bruts dans un repli
# --------------------------------------------------------------------------- #
def _short_hash(value: str | None, keep: int = 10) -> str | None:
    """`46e82a…88a51ab` : identifiable et copiable, sans mur d'hexadécimal."""
    if not value:
        return None
    return value if len(value) <= 2 * keep else f"{value[:keep]}…{value[-6:]}"


def _latest_reproduce_verdict(run_dir: Path, run_id: str) -> dict | None:
    """Verdict du dernier rejeu de ce run, si un rapport existe sur disque.

    ComfyUI range ses rejeux sous `<runs>/repro/<run_id>/<stamp>/`, Blender
    sous `<runs>/repro-<stamp>/` (le rapport porte `reproduced_request_id`).
    Balayage borné aux dossiers les plus récents : lecture seule, best-effort.
    """
    candidates: list[Path] = []
    comfy_repro = run_dir.parent / "repro" / run_id
    if comfy_repro.is_dir():
        candidates += list(comfy_repro.glob("*/reproduce_report.json"))
    blender_root = run_dir.parent
    repro_dirs = sorted(
        (d for d in blender_root.glob("repro-*") if d.is_dir()),
        key=lambda d: d.stat().st_mtime if d.exists() else 0,
        reverse=True,
    )[:25]
    candidates += [d / "reproduce_report.json" for d in repro_dirs]

    best: dict | None = None
    best_at = ""
    for path in candidates:
        report = _load_json(path)
        if not report:
            continue
        if report.get("reproduced_request_id") not in (run_id, None):
            continue
        created = str(report.get("created_at") or "")
        if created >= best_at:
            best, best_at = report, created
    if not best:
        return None
    return {"verdict": best.get("verdict"), "created_at": best_at[:16].replace("T", " ")}


def _provenance_view(manifest: dict | None, run_dir: Path, run_id: str) -> dict | None:
    """Le bloc repro du manifest, mis en forme : badges (seed, versions,
    commit) d'abord, hashes bruts en liste repliable. None si manifest v1."""
    if not isinstance(manifest, dict):
        return None
    repro = manifest.get("repro")
    if not isinstance(repro, dict):
        return None

    badges: list[dict] = []
    variants = repro.get("variants") or []
    seeds = [v.get("seed") for v in variants if v.get("seed") is not None]
    if seeds:
        badges.append({"label": "seed", "value": ", ".join(str(s) for s in seeds)})
    if repro.get("blender_version"):
        badges.append({"label": "engine", "value": repro["blender_version"]})
    comfy = repro.get("comfyui") or {}
    if comfy.get("comfyui_version"):
        badges.append({"label": "engine", "value": f"ComfyUI {comfy['comfyui_version']}"})
    if comfy.get("pytorch_version"):
        badges.append({"label": "torch", "value": comfy["pytorch_version"]})
    if repro.get("aac_git_commit"):
        badges.append({"label": "commit", "value": repro["aac_git_commit"][:9]})

    hashes: list[dict] = []

    def _add_hash(label: str, value: str | None) -> None:
        if value:
            hashes.append({"label": label, "short": _short_hash(value), "full": value})

    _add_hash("scene.py", repro.get("scene_py_sha256"))
    _add_hash("scene (semantic)", repro.get("scene_report_semantic_sha256"))
    preview = repro.get("preview_png") or {}
    _add_hash("preview pixels", preview.get("pixels_sha256"))
    _add_hash("preview dHash", preview.get("dhash"))
    for variant in variants:
        i = variant.get("index")
        _add_hash(f"workflow v{i}", variant.get("workflow_sha256"))
        image = variant.get("image") or {}
        _add_hash(f"image v{i} pixels", image.get("pixels_sha256"))
        _add_hash(f"image v{i} dHash", image.get("dhash"))
    for subdir, entries in (repro.get("models") or {}).items():
        for entry in entries or []:
            _add_hash(f"{subdir}/{entry.get('name')}", entry.get("sha256"))

    return {
        "badges": badges,
        "hashes": hashes,
        "last_reproduce": _latest_reproduce_verdict(run_dir, run_id),
    }


# --------------------------------------------------------------------------- #
# Reproduce — rejouer un run depuis la Console (même moteur que POST /reproduce)
# --------------------------------------------------------------------------- #
def _gather_reproduce_material(run_dir: Path, manifest: dict) -> dict:
    """Matériel de rejeu lu À CÔTÉ du manifest (sidecars workflow / scene.py),
    comme le CLI — jamais via les chemins absolus du manifest."""
    workflows: dict[int, dict] = {}
    for variant in (manifest.get("repro") or {}).get("variants") or []:
        name = variant.get("workflow_file")
        index = variant.get("index")
        if not name or not isinstance(index, int):
            continue
        sidecar = _safe_resolve(run_dir, name)
        workflow = _load_json(sidecar) if sidecar else None
        if isinstance(workflow, dict):
            workflows[index] = workflow

    scene_py = None
    scene_path = run_dir / "scene.py"
    if scene_path.is_file():
        try:
            scene_py = scene_path.read_text(encoding="utf-8")
        except OSError:
            scene_py = None

    return {"workflows": workflows, "scene_py": scene_py}


def build_run_detail(run_dir: Path) -> dict | None:
    """Vue détail d'un run sur disque (lecture seule).

    Réutilise les helpers d'overlay de cadrage et de fidélité ; ne recalcule
    aucune métrique. `run_dir` est supposé déjà validé sous une racine servie.
    """
    if not run_dir.is_dir():
        return None
    if run_dir.parent == BLENDER_RUNS_DIR:
        kind = "3d"
    elif run_dir.parent == COMFYUI_RUNS_DIR:
        kind = "2d"
    else:
        has_3d = (run_dir / "scene.blend").exists() or (run_dir / "scene_report.json").exists()
        kind = "3d" if has_3d else "2d"

    manifest = _load_json(run_dir / "manifest.json")
    scene_report = _load_json(run_dir / "scene_report.json")
    intent = _load_json(run_dir / "intent.json")

    preview = run_dir / "preview.png"
    render_url = _artifact_url(str(preview)) if preview.is_file() else None
    gallery = [
        u for u in (_artifact_url(str(p)) for p in sorted(run_dir.glob("*.png")) if p != preview)
        if u
    ]
    try:
        mtime = run_dir.stat().st_mtime
    except OSError:
        mtime = 0

    return {
        "id": run_dir.name,
        "kind": kind,
        "path": str(run_dir),
        "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "—",
        "render_url": render_url,
        "gallery": gallery,
        "manifest": manifest,
        "scene_report": scene_report,
        "intent": intent,
        "framing": _framing_overlay(scene_report),
        "semantic": _semantic_fidelity(manifest),
        "timeline": _timeline_view(run_dir.name),
        "provenance": _provenance_view(manifest, run_dir, run_dir.name),
        "can_reproduce": bool(isinstance(manifest, dict) and manifest.get("repro")),
    }


def build_view(result: dict) -> dict:
    """Prépare une vue d'affichage à partir du dict renvoyé par le service.

    Préparation de présentation pure (jointures, URLs d'artefacts). Aucune
    décision métier : on ne lit que ce que `execute_request` a déjà décidé.
    """
    plan_by_id = {
        step.get("step_id"): step for step in (result.get("plan") or [])
    }
    steps = []
    for sr in result.get("step_results") or []:
        plan_step = plan_by_id.get(sr.get("step_id"), {})
        steps.append({**sr, "goal": plan_step.get("goal")})

    # Normalisation séparée : images ComfyUI vs artefacts locaux Blender.
    # Prefer serving the local files through the Console (/console/artifact): the raw
    # ComfyUI view URLs point at the internal service name (e.g. comfyui:8188), which
    # the host browser cannot reach on the Docker path. The PNGs live under
    # COMFYUI_OUTPUT_DIR (a serve root), so the Console serves them same-origin.
    gallery = [
        url
        for url in (_artifact_url(p) for p in (result.get("artifact_paths") or []))
        if url
    ]
    if not gallery:
        gallery = list(result.get("artifact_view_urls") or [])

    render_url = _artifact_url(result.get("blender_render_path"))

    is_blender = bool(
        result.get("blender_status") or result.get("blender_render_path")
    )
    is_image = bool(
        result.get("artifact_type") == "image" or gallery
    )

    summary = result.get("execution_summary") or {}
    step_errors = [
        {"step_id": sr.get("step_id"), "error": sr.get("error")}
        for sr in (result.get("step_results") or [])
        if sr.get("status") in {"error", "blocked"} and sr.get("error")
    ]
    has_error = bool(
        step_errors
        or summary.get("error_step_ids")
        or summary.get("blocked_step_ids")
        or summary.get("status") in {"failed", "degraded", "empty"}
    )

    return {
        "r": result,
        "steps": steps,
        "gallery": gallery,
        "render_url": render_url,
        "is_blender": is_blender,
        "is_image": is_image,
        "summary": summary,
        "step_errors": step_errors,
        "has_error": has_error,
        "is_paused": summary.get("status") == "paused",
        "framing": _framing_overlay(result.get("blender_scene_report")),
        "semantic": _semantic_fidelity(result.get("blender_manifest")),
        "exception": None,
    }


@router.get("", response_class=HTMLResponse)
def page(request: Request):
    """Console single-page : toutes les sections vivent dans le DOM et sont
    basculées en JS (onglets). Aucun rechargement -> une génération en cours ou
    un résultat affiché n'est jamais perdu en changeant de menu."""
    return templates.TemplateResponse(request, "index.html", {})


@router.get("/outputs", response_class=HTMLResponse)
def outputs_fragment(request: Request, q: str | None = None):
    """Fragment Outputs (chargé par HTMX à l'ouverture de l'onglet).
    `q` filtre par tokens du prompt (recherche au fil de la frappe)."""
    runs = list_runs()
    total = len(runs)
    if q:
        runs = filter_runs(q, runs)
    return templates.TemplateResponse(
        request, "_outputs.html", {"runs": runs, "q": q or "", "total": total}
    )


@router.get("/compare", response_class=HTMLResponse)
def compare_runs(request: Request, sel: list[str] = Query(default=[])):
    """Deux runs côte à côte + diff de leurs blocs repro (modale HTMX).

    `sel` = chemins des runs cochés dans Outputs. Même garde de chemins que
    /run et /artifact ; il en faut EXACTEMENT deux.
    """
    selected = sel or []
    if len(selected) != 2:
        return HTMLResponse(
            '<div class="modal-backdrop" onclick="if(event.target===this)this.remove()">'
            '<div class="modal"><div class="modal-head"><span class="badge">⇄ Compare</span>'
            '<button class="ghost modal-close" onclick="this.closest(\'.modal-backdrop\').remove()">✕</button></div>'
            f'<p class="muted">Check exactly two runs to compare ({len(selected)} selected).</p>'
            "</div></div>"
        )

    details = []
    for raw in selected:
        try:
            resolved = Path(raw).resolve()
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(status_code=404, detail="run not found")
        if not _under_serve_roots(resolved) or not resolved.is_dir():
            raise HTTPException(status_code=404, detail="run not found")
        detail = build_run_detail(resolved)
        if detail is None:
            raise HTTPException(status_code=404, detail="run not found")
        details.append(detail)

    return templates.TemplateResponse(
        request, "_compare.html", build_compare_view(details[0], details[1])
    )


@router.get("/run", response_class=HTMLResponse)
def run_detail(request: Request, path: str):
    """Détail d'un run (modale chargée par HTMX depuis les cartes Outputs).

    `path` est résolu puis contraint aux racines de sortie servies (même garde
    que /artifact et /reveal) : aucune lecture hors `outputs/`.
    """
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=404, detail="run not found")
    if not _under_serve_roots(resolved) or not resolved.is_dir():
        raise HTTPException(status_code=404, detail="run not found")
    detail = build_run_detail(resolved)
    if detail is None:
        raise HTTPException(status_code=404, detail="run not found")
    return templates.TemplateResponse(request, "_run_detail.html", detail)


@router.get("/eval", response_class=HTMLResponse)
def eval_view(request: Request, file: str | None = None):
    """Vue Eval/Benchmark : lit les rapports du harness et les présente."""
    reports = list_eval_reports()
    selected = None
    summary = None
    if reports:
        selected = file if file in reports else reports[0]
        raw = load_eval_report(selected)
        summary = eval_summary(raw) if raw else None
    return templates.TemplateResponse(
        request,
        "_eval.html",
        {
            "reports": reports,
            "selected": selected,
            "summary": summary,
            "eval_dir": str(EVAL_DIR),
        },
    )


@router.get("/health", response_class=HTMLResponse)
def health_strip(request: Request):
    """Bandeau santé de la stack (chargé en différé par HTMX, non bloquant)."""
    try:
        health = get_runtime_health()
    except Exception as exc:  # noqa: BLE001 — un check qui échoue ne casse pas l'UI
        health = {"status": "unknown", "summary": str(exc), "services": {}}
    return templates.TemplateResponse(request, "_health.html", {"health": health})


@router.post("/run", response_class=HTMLResponse)
async def run(request: Request, background_tasks: BackgroundTasks):
    """Lance une demande EN ARRIÈRE-PLAN et renvoie le fragment de trace
    en direct (5 v2) : la page n'est plus bloquée pendant le run, la
    timeline se remplit via SSE, le résultat final arrive au signal `done`.

    Le corps `urlencoded` est parsé à la main (`parse_qs`) pour éviter la
    dépendance python-multipart qu'exige `request.form()`.
    """
    raw = (await request.body()).decode("utf-8", errors="replace")
    parsed = parse_qs(raw, keep_blank_values=True)
    message = (parsed.get("message", [""])[0]).strip()
    if not message:
        return templates.TemplateResponse(
            request,
            "result.html",
            {"exception": "Empty request.", "r": None},
        )
    # 2D quality toggle: a checked "final" box appends the --final token that the
    # visual pipeline already understands (RealVisXL + refiner). No-op elsewhere.
    if parsed.get("final", [""])[0] and "--final" not in message:
        message = f"{message} --final"
    # Forced mode from the active tab (2D -> image_generation, 3D -> blender_script).
    # Absent/empty -> "auto" (the Run tab lets the router decide).
    mode = (parsed.get("mode", ["auto"])[0]).strip() or "auto"
    # 4B — "Review before…" checkbox: pause ahead of the tool step, the
    # result fragment then shows an Approve & continue button (/console/resume).
    pause = bool(parsed.get("pause", [""])[0])

    request_id = str(uuid4())
    background_tasks.add_task(
        _run_in_background,
        request_id,
        lambda: execute_request(
            message, mode=mode, pause_before_tools=pause, request_id=request_id
        ),
    )
    return templates.TemplateResponse(
        request, "_live_run.html", {"request_id": request_id, "tail": False}
    )


@router.post("/resume", response_class=HTMLResponse)
def resume_from_console(request: Request, request_id: str, background_tasks: BackgroundTasks):
    """Approuve et reprend un run en pause (ou interrompu) — cible HTMX.

    Même moteur que POST /resume (API) et `aac resume` (CLI), lancé en
    arrière-plan comme POST /console/run ; le fragment de trace s'abonne en
    mode `tail` (l'historique du run figure déjà dans la modale/le résultat,
    seuls les événements de la reprise sont poussés).
    """
    from app.engine.executor import resume_request

    if not _REQUEST_ID_RE.match(request_id):
        raise HTTPException(status_code=404, detail="unknown run")

    _RESULTS.pop(request_id, None)  # l'issue précédente (pause) est périmée
    background_tasks.add_task(
        _run_in_background, request_id, lambda: resume_request(request_id)
    )
    return templates.TemplateResponse(
        request, "_live_run.html", {"request_id": request_id, "tail": True}
    )


@router.get("/stream/{request_id}")
async def stream_run_events(request: Request, request_id: str, tail: int = 0):
    """Flux SSE des événements d'un run (consommé par _live_run.html)."""
    if not _REQUEST_ID_RE.match(request_id):
        raise HTTPException(status_code=404, detail="unknown run")
    return StreamingResponse(
        _sse_event_stream(request, request_id, tail=bool(tail)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/run-result/{request_id}", response_class=HTMLResponse)
def run_result(request: Request, request_id: str):
    """Fragment de résultat final d'un run lancé par cette Console."""
    if not _REQUEST_ID_RE.match(request_id):
        raise HTTPException(status_code=404, detail="unknown run")
    entry = _RESULTS.get(request_id)
    if entry is None:
        return templates.TemplateResponse(
            request,
            "result.html",
            {"exception": f"Run {request_id} is still executing (or this Console "
                          "was restarted since). The artifacts land in Outputs "
                          "either way.", "r": None},
        )
    if "exception" in entry:
        return templates.TemplateResponse(
            request, "result.html", {"exception": entry["exception"], "r": None}
        )
    return templates.TemplateResponse(
        request, "result.html", build_view(entry["result"])
    )


@router.post("/reproduce", response_class=HTMLResponse)
def reproduce_from_console(request: Request, path: str):
    """Rejoue un run et renvoie le fragment de verdict (cible HTMX).

    Même moteur que `POST /reproduce` (API) et `aac reproduce` (CLI) — la
    Console est un troisième client de la même logique, appelée ici en
    process (le backend voit déjà ComfyUI/Blender et les dossiers de runs).
    Même garde de chemins que /run et /artifact. Synchrone comme POST
    /console/run (contrat V0) : une vraie re-génération prend ~1 min.
    """
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=404, detail="run not found")
    if not _under_serve_roots(resolved) or not resolved.is_dir():
        raise HTTPException(status_code=404, detail="run not found")

    manifest = _load_json(resolved / "manifest.json")
    if not isinstance(manifest, dict) or not manifest.get("repro"):
        return templates.TemplateResponse(
            request,
            "_reproduce_result.html",
            {"report": None, "exception": "This run has no repro block (pre-v2 manifest) — only runs captured since manifests v2 can be replayed."},
        )

    pipeline = manifest.get("pipeline")
    material = _gather_reproduce_material(resolved, manifest)
    try:
        report = reproduce_run(
            pipeline,
            manifest,
            workflows=material["workflows"],
            scene_py=material["scene_py"],
        )
    except Exception as exc:  # noqa: BLE001 — la Console ne doit jamais planter
        return templates.TemplateResponse(
            request,
            "_reproduce_result.html",
            {"report": None, "exception": f"{type(exc).__name__}: {exc}"},
        )
    return templates.TemplateResponse(
        request, "_reproduce_result.html", {"report": report, "exception": None}
    )


@router.get("/artifact")
def artifact(path: str):
    """Sert un fichier sous une des racines de sortie autorisées."""
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=404, detail="artifact not found")
    if not _under_serve_roots(resolved) or not resolved.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(resolved)


@router.post("/reveal", response_class=HTMLResponse)
def reveal(path: str) -> HTMLResponse:
    """Localise le dossier d'un run, et l'ouvre si l'hôte a un GUI.

    Usage local uniquement (console en loopback). On vise toujours un DOSSIER
    (jamais un fichier exécutable). Cross-OS : `os.startfile` (Windows), `open`
    (macOS), `xdg-open` (Linux). TOUT est best-effort : si l'ouverture échoue
    (pas de `DISPLAY`, ou backend dans un conteneur sans GUI), ce n'est PAS une
    erreur — on renvoie simplement le chemin sur disque pour que l'utilisateur
    y aille lui-même.
    """
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=404, detail="path not found")
    if not _under_serve_roots(resolved) or not resolved.exists():
        raise HTTPException(status_code=404, detail="path not found")
    target = resolved if resolved.is_dir() else resolved.parent
    opened = False
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))  # noqa: S606  (chemin validé sous outputs/)
            opened = True
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opened = True
        else:
            subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opened = True
    except OSError:
        opened = False  # pas de GUI (conteneur) -> on se contente d'afficher le chemin
    # Chemin lisible côté HÔTE. En conteneur, /outputs est monté sur le disque hôte ;
    # run.sh passe le chemin ABSOLU via AAC_HOST_OUTPUTS_DIR (sinon repli relatif).
    disp = str(target)
    host_root = os.getenv("AAC_HOST_OUTPUTS_DIR", "").strip()
    if disp.startswith("/outputs"):
        if host_root:
            disp = host_root.rstrip("/") + disp[len("/outputs"):]
        elif os.path.exists("/.dockerenv"):
            disp = "docker/outputs" + disp[len("/outputs"):]
    label = "📂 Opened it —" if opened else "📁 On disk —"
    return HTMLResponse(
        f'<span class="reveal-out">{label} <code>{html.escape(disp)}</code> '
        f'<button type="button" class="ghost copy-btn" data-p="{html.escape(disp, quote=True)}" '
        f'onclick="navigator.clipboard.writeText(this.dataset.p)">📋 copy</button></span>'
    )


@router.get("/static/{name}")
def static_file(name: str):
    """Sert les fichiers statiques locaux (HTMX vendorisé) — pas de CDN."""
    resolved = _safe_resolve(STATIC_DIR, name)
    if resolved is None or not resolved.is_file():
        raise HTTPException(status_code=404, detail="static file not found")
    return FileResponse(resolved)
