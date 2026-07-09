"""Smoke test: load the frozen V-JEPA 2.1 ViT-L encoder and embed one real preview.

Usage: .venv/bin/python encode_smoke.py <image.png>
Prints embedding shape, norm, dtype, device and timing — nothing else.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import torch
from PIL import Image

from get_encoder import RESOLUTION, load_encoder

# ImageNet normalization — what the V-JEPA preprocessors apply.
MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


def image_to_clip(path: Path, num_frames: int, device: torch.device) -> torch.Tensor:
    """A still image as a clip of `num_frames` identical frames: (1, C, T, H, W)."""
    img = Image.open(path).convert("RGB").resize((RESOLUTION, RESOLUTION), Image.BICUBIC)
    frame = torch.frombuffer(bytearray(img.tobytes()), dtype=torch.uint8)
    frame = frame.view(RESOLUTION, RESOLUTION, 3).permute(2, 0, 1).float() / 255.0
    frame = (frame - MEAN) / STD
    clip = frame.unsqueeze(1).repeat(1, num_frames, 1, 1)  # (C, T, H, W)
    return clip.unsqueeze(0).to(device)


def main() -> None:
    image_path = Path(sys.argv[1])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    t0 = time.perf_counter()
    encoder = load_encoder(device)
    t_load = time.perf_counter() - t0

    # V-JEPA 2.1 has a dedicated single-image path (img_temporal_dim_size=1);
    # fall back to a tubelet-sized clip if T=1 is rejected.
    tokens = None
    for num_frames in (1, 2):
        clip = image_to_clip(image_path, num_frames, device)
        try:
            t0 = time.perf_counter()
            with torch.inference_mode():
                tokens = encoder(clip)
            t_encode = time.perf_counter() - t0
            break
        except Exception as exc:  # noqa: BLE001 — smoke test, report and try next
            print(f"T={num_frames} rejected: {type(exc).__name__}: {exc}")
    if tokens is None:
        sys.exit("encoder accepted neither T=1 nor T=2 — investigate before going further")

    if isinstance(tokens, (tuple, list)):
        print(f"encoder returned {len(tokens)} tensors; shapes: {[tuple(t.shape) for t in tokens]}")
        tokens = tokens[-1]
    embedding = tokens.mean(dim=1).squeeze(0)  # mean-pool patch tokens
    print(f"model         : vjepa2_1_vit_large_384 (frozen) on {device}, T={num_frames}")
    print(f"load time     : {t_load:.1f}s | encode time: {t_encode:.2f}s")
    print(f"tokens shape  : {tuple(tokens.shape)}")
    print(f"embedding     : dim={embedding.shape[0]} norm={embedding.norm().item():.3f} dtype={embedding.dtype}")
    if device.type == "cuda":
        print(f"VRAM peak     : {torch.cuda.max_memory_allocated() / 1e9:.2f} GB")


if __name__ == "__main__":
    main()
