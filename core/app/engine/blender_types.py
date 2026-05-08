from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BlenderRequest:
    request_id: str
    script_content: str   # code bpy généré, extrait du markdown
    script_path: str      # outputs/blender/<request_id>/scene.py  (imposé système)
    output_path: str      # outputs/blender/<request_id>/scene.blend (imposé système)
    output_dir: str       # outputs/blender/<request_id>/
    timeout: int          # BLENDER_TIMEOUT, défaut 60


@dataclass
class BlenderResult:
    status: str           # success | error | blender_not_found | timeout | no_output
    request_id: str
    script_path: str | None
    output_path: str | None   # chemin vers le .blend produit si success
    output_dir: str | None
    returncode: int | None
    stdout: str | None
    stderr: str | None
    error: str | None
