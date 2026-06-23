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

import html
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.engine.executor import execute_request
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
    return {
        "id": d.name,
        "kind": kind,
        "path": str(d),
        "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "—",
        "mtime": mtime,
        "thumb_url": _artifact_url(str(img)) if img else None,
        "manifest": manifest,
    }


def list_runs() -> list[dict]:
    """Tous les runs (2D ComfyUI + 3D Blender), du plus récent au plus ancien.
    Ignore les dossiers techniques (`_eval_reports`, `_trajectories`)."""
    runs: list[dict] = []
    for base, kind in ((COMFYUI_RUNS_DIR, "2d"), (BLENDER_RUNS_DIR, "3d")):
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if d.is_dir() and not d.name.startswith("_"):
                runs.append(_describe_run(d, kind))
    runs.sort(key=lambda r: r["mtime"], reverse=True)
    return runs


def _load_json(p: Path) -> dict | None:
    """Lecture JSON tolérante (fichier absent/corrompu -> None)."""
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


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
def outputs_fragment(request: Request):
    """Fragment Outputs (chargé par HTMX à l'ouverture de l'onglet)."""
    return templates.TemplateResponse(request, "_outputs.html", {"runs": list_runs()})


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
        {"reports": reports, "selected": selected, "summary": summary},
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
async def run(request: Request):
    """Lance une demande et renvoie le fragment de résultat (cible HTMX).

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
    try:
        result = execute_request(message, mode=mode)
    except Exception as exc:  # noqa: BLE001 — la Console ne doit jamais planter
        return templates.TemplateResponse(
            request,
            "result.html",
            {"exception": f"{type(exc).__name__}: {exc}", "r": None},
        )
    return templates.TemplateResponse(request, "result.html", build_view(result))


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
    # Chemin lisible côté HÔTE : dans un conteneur, /outputs est monté sous
    # docker/outputs/ sur la machine (cf. docker/docker-compose.app.yml).
    disp = str(target)
    if os.path.exists("/.dockerenv") and disp.startswith("/outputs"):
        disp = "docker/outputs" + disp[len("/outputs"):]
    label = "📂 Opened" if opened else "📁 On disk at"
    return HTMLResponse(f'<span class="reveal-out">{label} <code>{html.escape(disp)}</code></span>')


@router.get("/static/{name}")
def static_file(name: str):
    """Sert les fichiers statiques locaux (HTMX vendorisé) — pas de CDN."""
    resolved = _safe_resolve(STATIC_DIR, name)
    if resolved is None or not resolved.is_file():
        raise HTTPException(status_code=404, detail="static file not found")
    return FileResponse(resolved)
