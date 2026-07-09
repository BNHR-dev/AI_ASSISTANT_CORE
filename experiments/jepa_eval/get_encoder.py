"""Load the frozen V-JEPA 2.1 ViT-L/16 encoder — pinned commit, reviewed, local source.

The upstream repo (facebookresearch/vjepa2) ships, at the pinned commit, a test
checkpoint URL (`localhost:8300`) instead of Meta's CDN; the official URL sits
commented out right above it. `ensure_vendor()` clones the repo at the pinned
commit and patches that single line back to the official CDN. Loading then uses
`torch.hub.load(..., source="local")`, so no code is fetched at load time.

Do NOT import AAC's `app` package in the same process: the vendored repo has its
own top-level `app` module and torch.hub puts it on sys.path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import torch

REPO_URL = "https://github.com/facebookresearch/vjepa2.git"
PINNED_COMMIT = "204698b45b3712590f06245fbfba32d3be539812"
VENDOR_DIR = Path(__file__).resolve().parent / ".vendor" / "vjepa2"
HUB_MODEL = "vjepa2_1_vit_large_384"
RESOLUTION = 384

_BROKEN_URL_LINE = 'VJEPA_BASE_URL = "http://localhost:8300"'
_OFFICIAL_URL_LINE = 'VJEPA_BASE_URL = "https://dl.fbaipublicfiles.com/vjepa2"'


def ensure_vendor() -> Path:
    if not (VENDOR_DIR / ".git").exists():
        VENDOR_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", REPO_URL, str(VENDOR_DIR)], check=True)
    subprocess.run(
        ["git", "-C", str(VENDOR_DIR), "checkout", "--quiet", PINNED_COMMIT], check=True
    )
    backbones = VENDOR_DIR / "src" / "hub" / "backbones.py"
    text = backbones.read_text(encoding="utf-8")
    if _BROKEN_URL_LINE in text:
        backbones.write_text(text.replace(_BROKEN_URL_LINE, _OFFICIAL_URL_LINE, 1), encoding="utf-8")
    if _OFFICIAL_URL_LINE not in backbones.read_text(encoding="utf-8"):
        raise RuntimeError(
            "VJEPA_BASE_URL patch failed — upstream layout changed, re-review before running"
        )
    return VENDOR_DIR


def load_encoder(device: torch.device) -> torch.nn.Module:
    vendor = ensure_vendor()
    encoder, _predictor = torch.hub.load(str(vendor), HUB_MODEL, source="local")
    encoder.eval().to(device)
    for p in encoder.parameters():
        p.requires_grad_(False)
    return encoder
