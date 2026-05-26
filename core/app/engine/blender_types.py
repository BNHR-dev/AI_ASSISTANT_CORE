from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BlenderRequest:
    request_id: str
    script_content: str        # code bpy généré, extrait du markdown
    script_path: str           # outputs/blender/<request_id>/scene.py  (imposé système)
    output_path: str           # outputs/blender/<request_id>/scene.blend (imposé système)
    render_path: str           # outputs/blender/<request_id>/preview.png (imposé système)
    output_dir: str            # outputs/blender/<request_id>/
    timeout: int               # BLENDER_TIMEOUT, défaut 60
    source_prompt: str | None = None      # prompt utilisateur brut — observabilité H.1
    creative_intent: dict | None = None   # intent artistique extrait — observabilité H.3
    template_used: str | None = None      # nom du template appliqué — observabilité H.4.1
    ast_guard: dict | None = None         # rapport AST guard V0 — observabilité H.4.7
    pipeline_path: str = "legacy_llm_bpy_scaffold"  # H.5.3 : chemin emprunté par build_blender_script
                                                    # Valeurs : "product_render_ir_builder" | "legacy_llm_bpy_scaffold"
    product_render_intent: dict | None = None  # H.5.3 : IR product_render extraite si chemin builder utilisé (sinon None)
    # H.5.4.1 — Traçabilité du déclenchement product_render IR (visible dans manifest.future).
    # product_render_ir_attempted        : True si l'extracteur LLM a été lancé pour ce prompt.
    # product_render_extraction_status   : "parsed" | "fallback" | "error" | "skipped" | None
    #                                      "skipped" = template != product_render OU flag IR désactivé.
    #                                      "error"   = exception inattendue dans extractor/builder.
    # product_render_extraction_reason   : message court (None si parsed ou skipped sans raison).
    product_render_ir_attempted: bool = False
    product_render_extraction_status: str | None = None
    product_render_extraction_reason: str | None = None


@dataclass
class BlenderResult:
    status: str           # success | error | blender_not_found | timeout | no_output
    request_id: str
    script_path: str | None
    output_path: str | None   # chemin vers le .blend produit si success
    render_path: str | None   # chemin vers le PNG preview si produit (best-effort)
    output_dir: str | None
    returncode: int | None
    stdout: str | None
    stderr: str | None
    error: str | None
    scene_report: dict | None = None          # rapport structurel best-effort (blender_validator)
    scene_report_path: str | None = None      # chemin vers scene_report.json si écrit
    manifest_path: str | None = None          # chemin vers manifest.json — observabilité H.1
    meta: dict | None = None                  # données enrichies (scene_report, etc.)
    ast_guard: dict | None = None             # rapport AST guard V0 — observabilité H.4.7
