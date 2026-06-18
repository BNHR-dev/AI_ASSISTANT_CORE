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
# Racine autorisée pour servir des artefacts (rendus Blender, etc.).
OUTPUTS = (PROJECT_ROOT / "outputs").resolve()

router = APIRouter(prefix="/console", tags=["console"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
    if not resolved.is_relative_to(OUTPUTS):
        return None
    return "/console/artifact?path=" + quote(str(resolved.relative_to(OUTPUTS)))


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
    view_urls = result.get("artifact_view_urls") or []
    if view_urls:
        gallery = list(view_urls)
    else:
        gallery = [
            url
            for url in (
                _artifact_url(p) for p in (result.get("artifact_paths") or [])
            )
            if url
        ]

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
    """La page : formulaire + zone de résultat vide."""
    return templates.TemplateResponse(request, "index.html", {})


@router.get("/health", response_class=HTMLResponse)
def health_strip(request: Request):
    """Bandeau santé de la stack (chargé en différé par HTMX, non bloquant)."""
    try:
        health = get_runtime_health()
    except Exception as exc:  # noqa: BLE001 — un check qui échoue ne casse pas l'UI
        health = {"status": "inconnu", "summary": str(exc), "services": {}}
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
            {"exception": "Demande vide.", "r": None},
        )
    try:
        result = execute_request(message)
    except Exception as exc:  # noqa: BLE001 — la Console ne doit jamais planter
        return templates.TemplateResponse(
            request,
            "result.html",
            {"exception": f"{type(exc).__name__}: {exc}", "r": None},
        )
    return templates.TemplateResponse(request, "result.html", build_view(result))


@router.get("/artifact")
def artifact(path: str):
    """Sert un fichier local sous `outputs/` uniquement."""
    resolved = _safe_resolve(OUTPUTS, path)
    if resolved is None or not resolved.is_file():
        raise HTTPException(status_code=404, detail="artefact introuvable")
    return FileResponse(resolved)


@router.get("/static/{name}")
def static_file(name: str):
    """Sert les fichiers statiques locaux (HTMX vendorisé) — pas de CDN."""
    resolved = _safe_resolve(STATIC_DIR, name)
    if resolved is None or not resolved.is_file():
        raise HTTPException(status_code=404, detail="statique introuvable")
    return FileResponse(resolved)
