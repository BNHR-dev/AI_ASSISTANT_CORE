"""aac — CLI d'observabilité et de pilotage de l'API AAC locale.

Quatre commandes, un seul fichier, zéro dépendance nouvelle (click + httpx,
déjà dans requirements.txt) :

    aac health               GET /health + /health/runtime + /debug/canonical
    aac inspect "<prompt>"   POST /route   → décision de routage + arbre
    aac execute "<prompt>"   POST /execute → statut, plan/étapes, artefacts
    aac resume <request_id>  POST /resume  → reprend un run interrompu depuis
                             son checkpoint (steps réussis restaurés, le
                             reste ré-exécuté)
    aac reproduce <run>      POST /reproduce → rejoue un run depuis son
                             manifest v2 et compare les artefacts (verdict
                             exact / perceptual / different / failed / refused)

`aac reproduce` prend le dossier d'un run (ou son manifest.json) : le CLI
lit le manifest + le matériel de rejeu À CÔTÉ du manifest (sidecars
workflow_resolved_v<i>.json pour ComfyUI, scene.py pour Blender) et envoie
leur CONTENU au backend — c'est lui qui ré-exécute (il voit ComfyUI et
Blender), pas la machine du CLI.

Options globales (avant la sous-commande) : --base-url (défaut
http://127.0.0.1:8000), --token (défaut : env AAC_API_TOKEN), --json
(réponse brute pour les pipes). `--image` se pose sur inspect/execute.

Codes de sortie : 0 OK · 1 erreur côté API (HTTP non-2xx, exécution en
échec, runtime dégradé, verdict de rejeu non-reproduit) · 2 API
injoignable · 3 authentification refusée.

Usage : `python core/cli.py health` (ou alias shell `aac`).
"""

from __future__ import annotations

import json
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import click
import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
TOKEN_ENV = "AAC_API_TOKEN"

# Codes de sortie : contrat stable pour les scripts qui enchaînent sur le CLI.
EXIT_API_ERROR = 1
EXIT_UNREACHABLE = 2
EXIT_AUTH = 3

CONNECT_TIMEOUT = 5.0
HEALTH_READ_TIMEOUT = 10.0
ROUTE_READ_TIMEOUT = 120.0  # le classifieur peut appeler un modèle local
EXECUTE_READ_TIMEOUT = 900.0  # un rendu ComfyUI/Blender se compte en minutes

# Palettes distinctes : « degraded » côté runtime = backend requis KO (rouge),
# côté exécution = succès partiel (jaune).
RUNTIME_PALETTE = {"ok": "green", "partial": "yellow", "degraded": "red"}
EXEC_PALETTE = {"success": "green", "degraded": "yellow", "empty": "yellow", "failed": "red"}
STEP_GLYPHS = {"success": ("✔", "green"), "error": ("✘", "red"), "blocked": ("⊘", "yellow")}
REPRO_PALETTE = {
    "exact": "green",
    "perceptual": "yellow",
    "different": "red",
    "failed": "red",
    "refused": "red",
    "skipped": "white",
}
# Verdicts considérés comme « reproduit » pour le code de sortie.
REPRO_OK_VERDICTS = ("exact", "perceptual")


@dataclass
class Settings:
    base_url: str
    token: Optional[str]
    as_json: bool


def make_client(base_url: str, token: Optional[str], read_timeout: float) -> httpx.Client:
    """Client HTTP du CLI. Isolé pour être substituable en test (MockTransport)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    timeout = httpx.Timeout(read_timeout, connect=CONNECT_TIMEOUT)
    return httpx.Client(base_url=base_url, headers=headers, timeout=timeout)


def _fail(code: int, message: str) -> None:
    click.secho(message, fg="red", err=True)
    sys.exit(code)


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    json_body: Optional[dict] = None,
) -> dict:
    """Requête + politique d'erreur commune (injoignable / 401 / non-2xx)."""
    try:
        response = client.request(method, path, json=json_body)
    except httpx.TimeoutException:
        _fail(EXIT_UNREACHABLE, f"Délai dépassé sur {method} {path} ({client.base_url}).")
    except httpx.TransportError as exc:
        _fail(
            EXIT_UNREACHABLE,
            f"API injoignable à {client.base_url} — la stack tourne ? (./run.sh) [{exc}]",
        )
    if response.status_code == 401:
        _fail(EXIT_AUTH, f"Authentification refusée (401) — poser {TOKEN_ENV} ou --token.")
    if response.is_error:
        _fail(EXIT_API_ERROR, f"HTTP {response.status_code} sur {method} {path} : {response.text[:300]}")
    return response.json()


# ---------------------------------------------------------------------------
# Helpers de rendu
# ---------------------------------------------------------------------------

def _echo_json(payload: Any) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


def _paint(status: str, palette: dict[str, str]) -> str:
    return click.style(status, fg=palette.get(status, "white"), bold=True)


def _tree(lines: list[str]) -> str:
    if not lines:
        return "└─ (vide)"
    branches = ["├─"] * (len(lines) - 1) + ["└─"]
    return "\n".join(f"{b} {line}" for b, line in zip(branches, lines))


def _kv(label: str, value: Any) -> None:
    click.echo(f"  {label:<14}{value}")


def _shorten(prompt: str) -> str:
    return textwrap.shorten(prompt, width=70, placeholder="…")


def _fmt_ms(ms: int) -> str:
    return f"{ms} ms" if ms < 1000 else f"{ms} ms ({ms / 1000:.1f} s)"


def _render_routing(decision: dict) -> None:
    _kv("tâche", decision.get("task_type"))
    _kv("agent", decision.get("primary_agent"))
    _kv("modèle", decision.get("selected_model"))
    _kv("outil", decision.get("selected_tool") or "—")
    _kv("format", decision.get("output_format"))
    _kv("web", "oui" if decision.get("needs_web") else "non")
    _kv("second appel", decision.get("second_call") or "—")
    _kv("règle", decision.get("matched_rule") or "—")
    _kv("raison", decision.get("reason"))


# ---------------------------------------------------------------------------
# Groupe et commandes
# ---------------------------------------------------------------------------

@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--base-url", default=DEFAULT_BASE_URL, show_default=True, metavar="URL",
              help="URL de l'API AAC.")
@click.option("--token", envvar=TOKEN_ENV, default=None,
              help=f"Bearer token de l'API (défaut : env {TOKEN_ENV}).")
@click.option("--json", "as_json", is_flag=True, help="Réponse JSON brute (pour les pipes).")
@click.pass_context
def aac(ctx: click.Context, base_url: str, token: Optional[str], as_json: bool) -> None:
    """aac — piloter et observer l'API AAC depuis le terminal."""
    ctx.obj = Settings(base_url=base_url, token=token, as_json=as_json)


@aac.command()
@click.pass_obj
def health(cfg: Settings) -> None:
    """Statut de l'API : vie, runtime (services) et frontières canoniques."""
    with make_client(cfg.base_url, cfg.token, HEALTH_READ_TIMEOUT) as client:
        alive = _request(client, "GET", "/health")
        runtime = _request(client, "GET", "/health/runtime")
        canonical = _request(client, "GET", "/debug/canonical")

    if cfg.as_json:
        _echo_json({"health": alive, "runtime": runtime, "canonical": canonical})
    else:
        _render_health(cfg.base_url, alive, runtime, canonical)

    if runtime.get("status") == "degraded":
        sys.exit(EXIT_API_ERROR)


def _render_health(base_url: str, alive: dict, runtime: dict, canonical: dict) -> None:
    click.echo(f"API        {base_url} — {_paint(alive.get('status', '?'), RUNTIME_PALETTE)}")
    click.echo(
        f"Runtime    {_paint(runtime.get('status', '?'), RUNTIME_PALETTE)}"
        f" — {runtime.get('summary', '')}"
        f" (v{runtime.get('version', '?')}, contrôlé {runtime.get('checked_at', '?')})"
    )

    services = runtime.get("services") or {}
    if services:
        click.echo()
        click.echo("Services")
        lines = []
        for name, svc in services.items():
            if svc.get("ready"):
                dot = click.style("●", fg="green")
            elif svc.get("reachable"):
                dot = click.style("●", fg="yellow")
            else:
                dot = click.style("●", fg="red")
            requis = "requis" if svc.get("required") else "optionnel"
            detail = svc.get("reason") or ""
            missing = svc.get("missing") or []
            if missing:
                detail += f" — manquants : {', '.join(missing)}"
            lines.append(f"{dot} {name} ({requis}, {svc.get('role', '?')}) — {detail}")
        click.echo(_tree(lines))

    click.echo()
    click.echo(
        f"Canonique  {_paint(canonical.get('status', '?'), RUNTIME_PALETTE)}"
        f" (v{canonical.get('version', '?')})"
        f" — {len(canonical.get('active_runtime_modules') or [])} modules runtime actifs,"
        f" {len(canonical.get('active_auxiliary_modules') or [])} auxiliaires,"
        f" {len(canonical.get('dormant_modules') or [])} dormants,"
        f" {len(canonical.get('legacy_shims') or [])} shims legacy"
    )
    if canonical.get("rule"):
        click.echo(f"           règle : {canonical['rule']}")


@aac.command()
@click.argument("prompt")
@click.option("--image", is_flag=True,
              help="Marque la requête comme accompagnée d'une image (has_image).")
@click.pass_obj
def inspect(cfg: Settings, prompt: str, image: bool) -> None:
    """Décision de routage de l'API pour PROMPT, sans rien exécuter."""
    with make_client(cfg.base_url, cfg.token, ROUTE_READ_TIMEOUT) as client:
        decision = _request(client, "POST", "/route", {"message": prompt, "has_image": image})

    if cfg.as_json:
        _echo_json(decision)
        return

    click.echo(f'Décision de routage — "{_shorten(prompt)}"')
    _render_routing(decision)
    click.echo()
    click.echo("Arbre de décision")
    click.echo(_tree(decision.get("decision_trace") or []))


@aac.command()
@click.argument("prompt")
@click.option("--image", is_flag=True,
              help="Marque la requête comme accompagnée d'une image (has_image).")
@click.pass_obj
def execute(cfg: Settings, prompt: str, image: bool) -> None:
    """Exécute PROMPT de bout en bout : statut, plan, étapes, artefacts."""
    with make_client(cfg.base_url, cfg.token, EXECUTE_READ_TIMEOUT) as client:
        result = _request(client, "POST", "/execute", {"message": prompt, "has_image": image})

    if cfg.as_json:
        _echo_json(result)
    else:
        _render_execution(prompt, result)

    summary = result.get("execution_summary") or {}
    if summary and summary.get("status") not in ("success", "empty"):
        sys.exit(EXIT_API_ERROR)


def _render_execution(prompt: str, result: dict) -> None:
    summary = result.get("execution_summary") or {}
    status = summary.get("status") or "?"

    click.echo(f'Exécution — "{_shorten(prompt)}"')
    total = summary.get("total_steps")
    if total is not None:
        ok = len(summary.get("successful_step_ids") or [])
        _kv("statut", f"{_paint(status, EXEC_PALETTE)} ({ok}/{total} étapes OK)")
    else:
        _kv("statut", _paint(status, EXEC_PALETTE))
    if result.get("duration_ms") is not None:
        _kv("durée", _fmt_ms(result["duration_ms"]))
    if result.get("request_id"):
        _kv("requête", result["request_id"])
    routing = f"{result.get('task_type')} · agent {result.get('primary_agent')} · modèle {result.get('selected_model')}"
    if result.get("selected_tool"):
        routing += f" · outil {result['selected_tool']}"
    _kv("routage", routing)

    plan = result.get("plan") or []
    results_by_id = {r.get("step_id"): r for r in result.get("step_results") or []}
    if plan:
        click.echo()
        click.echo("Plan")
        lines = []
        for step in plan:
            step_result = results_by_id.get(step.get("step_id"), {})
            step_status = step_result.get("status") or step.get("status") or "?"
            glyph, color = STEP_GLYPHS.get(step_status, ("•", "white"))
            who = step.get("tool") or step.get("model") or step.get("agent")
            line = (
                f"{click.style(glyph, fg=color)} {step.get('step_id')}"
                f" · {step.get('step_type', '?')} — {step.get('goal', '')}"
            )
            if who:
                line += f" ({who})"
            if step_result.get("duration_ms") is not None:
                line += f" [{_fmt_ms(step_result['duration_ms'])}]"
            if step_result.get("error"):
                line += click.style(f" — erreur : {step_result['error']}", fg="red")
            lines.append(line)
        click.echo(_tree(lines))

    paths = result.get("artifact_paths") or []
    if not paths and result.get("artifact_path"):
        paths = [result["artifact_path"]]
    if paths:
        click.echo()
        click.echo("Artefacts")
        click.echo(_tree(paths))

    if result.get("output"):
        click.echo()
        click.echo("Sortie")
        click.echo(result["output"])


@aac.command()
@click.argument("request_id")
@click.pass_obj
def resume(cfg: Settings, request_id: str) -> None:
    """Reprend le run REQUEST_ID depuis son checkpoint (state.json)."""
    with make_client(cfg.base_url, cfg.token, EXECUTE_READ_TIMEOUT) as client:
        result = _request(client, "POST", "/resume", {"request_id": request_id})

    if cfg.as_json:
        _echo_json(result)
    else:
        _render_execution(f"reprise du run {request_id}", result)

    summary = result.get("execution_summary") or {}
    if summary and summary.get("status") not in ("success", "empty"):
        sys.exit(EXIT_API_ERROR)


def _load_reproduce_material(run_path: Path) -> dict:
    """
    Construit le payload /reproduce depuis un dossier de run (ou son
    manifest.json) : manifest + sidecars workflow (ComfyUI) ou scene.py
    (Blender), lus À CÔTÉ du manifest — jamais via les chemins absolus du
    manifest, qui décrivent le disque du BACKEND (conteneur ≠ hôte).
    """
    manifest_path = run_path / "manifest.json" if run_path.is_dir() else run_path
    run_dir = manifest_path.parent
    if not manifest_path.is_file():
        _fail(EXIT_API_ERROR, f"manifest.json introuvable : {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail(EXIT_API_ERROR, f"manifest illisible : {exc}")

    pipeline = manifest.get("pipeline")
    if manifest.get("manifest_version", 0) < 2 or not manifest.get("repro"):
        _fail(
            EXIT_API_ERROR,
            "ce run n'a pas de bloc repro (manifest v1 ?) — seuls les runs "
            "produits depuis les manifests v2 sont rejouables.",
        )

    payload: dict = {"pipeline": pipeline, "manifest": manifest, "workflows": {}, "scene_py": None}

    if pipeline == "comfyui":
        for variant in manifest["repro"].get("variants") or []:
            sidecar_name = variant.get("workflow_file")
            if not sidecar_name:
                continue
            sidecar_path = run_dir / sidecar_name
            try:
                payload["workflows"][str(variant["index"])] = json.loads(
                    sidecar_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                click.secho(f"sidecar illisible, variante ignorée : {sidecar_path}", fg="yellow", err=True)
        if not payload["workflows"]:
            _fail(EXIT_API_ERROR, "aucun sidecar workflow_resolved_v<i>.json lisible dans le run.")
    elif pipeline == "blender":
        scene_path = run_dir / "scene.py"
        try:
            payload["scene_py"] = scene_path.read_text(encoding="utf-8")
        except OSError:
            _fail(EXIT_API_ERROR, f"scene.py introuvable à côté du manifest : {scene_path}")
    else:
        _fail(EXIT_API_ERROR, f"pipeline inconnu dans le manifest : {pipeline!r}")

    return payload


@aac.command()
@click.argument("run", type=click.Path(exists=True, path_type=Path))
@click.pass_obj
def reproduce(cfg: Settings, run: Path) -> None:
    """Rejoue RUN (dossier de run ou manifest.json) et compare les artefacts."""
    payload = _load_reproduce_material(run)

    with make_client(cfg.base_url, cfg.token, EXECUTE_READ_TIMEOUT) as client:
        report = _request(client, "POST", "/reproduce", payload)

    if cfg.as_json:
        _echo_json(report)
    else:
        _render_reproduce(report)

    if report.get("verdict") not in REPRO_OK_VERDICTS:
        sys.exit(EXIT_API_ERROR)


def _render_reproduce(report: dict) -> None:
    verdict = report.get("verdict") or "?"
    click.echo(f"Rejeu — run {report.get('reproduced_request_id') or '?'} ({report.get('pipeline')})")
    _kv("verdict", _paint(verdict, REPRO_PALETTE))
    if report.get("duration_ms") is not None:
        _kv("durée", _fmt_ms(report["duration_ms"]))
    _kv("seuil dHash", report.get("dhash_threshold"))
    if report.get("error"):
        _kv("erreur", click.style(report["error"], fg="red"))

    lines = []
    for variant in report.get("variants") or []:
        v = variant.get("verdict") or "?"
        line = f"variante {variant.get('index')} : {_paint(v, REPRO_PALETTE)}"
        image = variant.get("image") or {}
        if image.get("dhash_distance") is not None:
            line += f" (distance dHash {image['dhash_distance']})"
        if variant.get("reason"):
            line += f" — {variant['reason']}"
        lines.append(line)
    for check in report.get("checks") or []:
        v = check.get("verdict") or "?"
        line = f"{check.get('name')} : {_paint(v, REPRO_PALETTE)}"
        if check.get("dhash_distance") is not None:
            line += f" (distance dHash {check['dhash_distance']})"
        lines.append(line)
    if lines:
        click.echo()
        click.echo("Comparaisons")
        click.echo(_tree(lines))

    diffs = report.get("environment_diffs") or []
    if diffs:
        click.echo()
        click.echo("Environnement modifié depuis l'enregistrement")
        click.echo(
            _tree([f"{d['field']} : {d['recorded']} → {d['current']}" for d in diffs])
        )

    if report.get("report_path"):
        click.echo()
        _kv("rapport", report["report_path"])


if __name__ == "__main__":
    aac(prog_name="aac")
