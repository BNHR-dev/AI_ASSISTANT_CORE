"""Encode the labeled dataset and measure conform/degraded separation.

Scores: cosine similarity of each render to the centroid of its case's conforming
embeddings — leave-one-out for conforming renders (the tested image never feeds its
own centroid), full conform centroid for degraded ones.

Primary metric (decided before seeing any result): AUC over within-case
(conform, degraded) pairs, aggregated across cases — scores are case-relative,
so cross-case pairs would mix case difficulty into the metric. The pooled cross-case
AUC is reported alongside for transparency.

Usage: .venv/bin/python encode_and_score.py [--embedder vjepa|pixel|histogram]

`--embedder` swaps the representation, nothing else — the two trivial baselines
answer the "so what?" question: would raw pixels or color histograms separate
these defects just as well as the world-model embedding?
  vjepa      — frozen V-JEPA 2.1 ViT-L/16, mean-pooled patch tokens (default)
  pixel      — the image itself, downscaled to 64x64 RGB and flattened
  histogram  — 32-bin per-channel RGB color histogram (96 dims)

Reads  ../../docker/outputs/blender/_jepa_eval/ (host view of the container outputs)
Writes results/report[_<embedder>].json + SUMMARY[_<embedder>].md (committed).
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path

import torch
from PIL import Image

from get_encoder import HUB_MODEL, PINNED_COMMIT, RESOLUTION, load_encoder

HERE = Path(__file__).resolve().parent
DATASET_ROOT = HERE.parent.parent / "docker" / "outputs" / "blender" / "_jepa_eval"
RESULTS_DIR = HERE / "results"

# Pre-registered thresholds (vault spec, decided 2026-07-02 — before any run).
THRESHOLDS = {"real_signal": 0.80, "weak_signal": 0.60}

MEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


def image_tensor(path: Path, device: torch.device) -> torch.Tensor:
    img = Image.open(path).convert("RGB").resize((RESOLUTION, RESOLUTION), Image.BICUBIC)
    frame = torch.frombuffer(bytearray(img.tobytes()), dtype=torch.uint8)
    frame = frame.view(RESOLUTION, RESOLUTION, 3).permute(2, 0, 1).float() / 255.0
    frame = (frame - MEAN) / STD
    return frame.unsqueeze(1).unsqueeze(0).to(device)  # (1, C, T=1, H, W)


def cosine(a: torch.Tensor, b: torch.Tensor) -> float:
    return torch.nn.functional.cosine_similarity(a, b, dim=0).item()


def make_embedder(name: str):
    """Return (embed_fn(path) -> 1-D cpu tensor, embedder_info dict)."""
    if name == "vjepa":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        encoder = load_encoder(device)

        def embed(path: Path) -> torch.Tensor:
            with torch.inference_mode():
                tokens = encoder(image_tensor(path, device))
            return tokens.mean(dim=1).squeeze(0).cpu()

        return embed, {
            "embedder": "vjepa", "hub_model": HUB_MODEL, "pinned_commit": PINNED_COMMIT,
            "resolution": RESOLUTION, "pooling": "mean over patch tokens",
            "frames": 1, "frozen": True,
        }

    if name == "pixel":

        def embed(path: Path) -> torch.Tensor:
            img = Image.open(path).convert("RGB").resize((64, 64), Image.BICUBIC)
            return torch.frombuffer(bytearray(img.tobytes()), dtype=torch.uint8).float() / 255.0

        return embed, {"embedder": "pixel",
                       "detail": "64x64 RGB flattened (12288 dims), cosine on raw pixels"}

    if name == "histogram":

        def embed(path: Path) -> torch.Tensor:
            hist = torch.tensor(Image.open(path).convert("RGB").histogram(), dtype=torch.float32)
            hist = hist.view(3, 32, 8).sum(dim=2).flatten()  # 32 bins per channel
            return hist / hist.sum()

        return embed, {"embedder": "histogram",
                       "detail": "32-bin per-channel RGB color histogram (96 dims), cosine"}

    raise ValueError(f"unknown embedder: {name}")


def auc_from_pairs(conform: list[float], degraded: list[float]) -> tuple[float, int]:
    """Mann-Whitney AUC: P(conform score > degraded score), ties count half."""
    wins = 0.0
    pairs = 0
    for c in conform:
        for d in degraded:
            pairs += 1
            if c > d:
                wins += 1.0
            elif c == d:
                wins += 0.5
    return (wins / pairs if pairs else float("nan")), pairs


def verdict(auc: float) -> str:
    if auc >= THRESHOLDS["real_signal"]:
        return "real signal (AUC >= 0.80) — keep the metric, document it"
    if auc >= THRESHOLDS["weak_signal"]:
        return "weak signal (0.60 <= AUC < 0.80) — document and investigate before concluding"
    return "negative (AUC < 0.60) — documented as-is; a publishable lesson"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--embedder", choices=("vjepa", "pixel", "histogram"), default="vjepa")
    args = parser.parse_args()

    dataset = json.loads((DATASET_ROOT / "dataset.json").read_text(encoding="utf-8"))
    entries = dataset["entries"]

    embed, embedder_info = make_embedder(args.embedder)

    # 1. Encode every image (sequential, deterministic order).
    t0 = time.perf_counter()
    embeddings: dict[tuple[str, str], torch.Tensor] = {}
    for entry in entries:
        case_id, variant = entry["case_id"], entry["variant"]
        embeddings[(case_id, variant)] = embed(DATASET_ROOT / case_id / variant / "preview.png")
    t_encode = time.perf_counter() - t0

    # 2. Per-case scores.
    by_case: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_case[entry["case_id"]].append(entry)

    scored: list[dict] = []
    for case_id, case_entries in sorted(by_case.items()):
        conform = [e for e in case_entries if e["label"] == "conform"]
        degraded = [e for e in case_entries if e["label"] == "degraded"]
        conform_embs = {e["variant"]: embeddings[(case_id, e["variant"])] for e in conform}

        for e in conform:
            others = [emb for v, emb in conform_embs.items() if v != e["variant"]]
            centroid = torch.stack(others).mean(dim=0)
            scored.append({**e, "jepa_score": cosine(embeddings[(case_id, e["variant"])], centroid)})
        full_centroid = torch.stack(list(conform_embs.values())).mean(dim=0)
        for e in degraded:
            scored.append({**e, "jepa_score": cosine(embeddings[(case_id, e["variant"])], full_centroid)})

    # 3. AUCs — within-case pairs (primary), pooled (transparency), per defect, per case.
    def within_case_auc(defect: str | None = None) -> tuple[float, int]:
        wins, pairs = 0.0, 0
        for case_id in by_case:
            c_scores = [s["jepa_score"] for s in scored
                        if s["case_id"] == case_id and s["label"] == "conform"]
            d_scores = [s["jepa_score"] for s in scored
                        if s["case_id"] == case_id and s["label"] == "degraded"
                        and (defect is None or s["variant"] == defect)]
            a, p = auc_from_pairs(c_scores, d_scores)
            if p:
                wins += a * p
                pairs += p
        return (wins / pairs if pairs else float("nan")), pairs

    primary_auc, primary_pairs = within_case_auc()
    pooled_auc, _ = auc_from_pairs(
        [s["jepa_score"] for s in scored if s["label"] == "conform"],
        [s["jepa_score"] for s in scored if s["label"] == "degraded"],
    )
    per_defect = {}
    def family_level(variant: str) -> tuple[str, int]:
        # Strict "_iN" suffix — "deg_intruder" itself contains "_i".
        match = re.match(r"^(.+)_i(\d)$", variant)
        return (match.group(1), int(match.group(2))) if match else (variant, 4)

    degraded_variants = sorted(
        {s["variant"] for s in scored if s["label"] == "degraded"}, key=family_level
    )
    for defect_variant in degraded_variants:
        auc, pairs = within_case_auc(defect_variant)
        caught = [s["contract_verdict"]["contract_caught"] for s in scored
                  if s["variant"] == defect_variant]
        per_defect[defect_variant] = {
            "auc": auc,
            "pairs": pairs,
            "contract_caught_rate": sum(caught) / len(caught) if caught else None,
        }
    per_case = {}
    for case_id in sorted(by_case):
        c = [s["jepa_score"] for s in scored if s["case_id"] == case_id and s["label"] == "conform"]
        d = [s["jepa_score"] for s in scored if s["case_id"] == case_id and s["label"] == "degraded"]
        auc, _ = auc_from_pairs(c, d)
        per_case[case_id] = {"auc": auc, "conform_mean": sum(c) / len(c) if c else None,
                             "degraded_mean": sum(d) / len(d) if d else None}

    is_baseline = args.embedder != "vjepa"
    report = {
        "experiment": "A — V-JEPA learned metric next to deterministic contracts"
        + (" — TRIVIAL BASELINE" if is_baseline else ""),
        "model": embedder_info,
        "dataset": {"images": len(scored),
                    "conform": sum(1 for s in scored if s["label"] == "conform"),
                    "degraded": sum(1 for s in scored if s["label"] == "degraded"),
                    "cases": len(by_case)},
        "thresholds_preregistered": THRESHOLDS,
        "auc_primary_within_case": primary_auc,
        "auc_primary_pairs": primary_pairs,
        "auc_pooled": pooled_auc,
        "per_defect": per_defect,
        "per_case": per_case,
        "verdict": ("baseline — context for the learned metric; pre-registered "
                    "thresholds do not apply") if is_baseline else verdict(primary_auc),
        "encode_seconds_total": round(t_encode, 1),
        "scores": [{k: s[k] for k in ("case_id", "variant", "label", "jepa_score")}
                   | {"contract_caught": s["contract_verdict"]["contract_caught"]}
                   for s in scored],
    }

    suffix = "" if args.embedder == "vjepa" else f"_{args.embedder}"
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f"report{suffix}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    embedder_desc = (
        f"`{HUB_MODEL}` (frozen, commit `{PINNED_COMMIT[:12]}`), {RESOLUTION}px, mean-pooled"
        if args.embedder == "vjepa"
        else f"trivial baseline — {embedder_info['detail']}"
    )
    lines = [
        f"# Experiment A — results ({args.embedder})",
        "",
        f"- Embedder: {embedder_desc}",
        f"- Dataset: {report['dataset']['images']} images "
        f"({report['dataset']['conform']} conform / {report['dataset']['degraded']} degraded, "
        f"{report['dataset']['cases']} cases)",
        "",
        f"## Primary AUC (within-case pairs): **{primary_auc:.3f}**",
        f"Pooled AUC (cross-case, transparency): {pooled_auc:.3f}",
        "",
        f"**Verdict against pre-registered thresholds: {report['verdict']}**",
        "",
        "| Defect | AUC | contract sees it |",
        "|---|---|---|",
    ]
    for name, d in per_defect.items():
        rate = d["contract_caught_rate"]
        lines.append(f"| {name} | {d['auc']:.3f} | {'' if rate is None else f'{rate:.0%}'} |")
    lines += ["", "| Case | AUC | conform mean | degraded mean |", "|---|---|---|---|"]
    for case_id, c in per_case.items():
        lines.append(f"| {case_id} | {c['auc']:.3f} | {c['conform_mean']:.4f} | {c['degraded_mean']:.4f} |")
    (RESULTS_DIR / f"SUMMARY{suffix}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[{args.embedder}] encoded {len(scored)} images in {t_encode:.1f}s")
    print(f"PRIMARY AUC (within-case) = {primary_auc:.3f} over {primary_pairs} pairs")
    print(f"pooled AUC = {pooled_auc:.3f}")
    for name, d in per_defect.items():
        print(f"  {name:14s} AUC={d['auc']:.3f}  contract_caught={d['contract_caught_rate']}")
    print(f"verdict: {report['verdict']}")
    print(f"report: {RESULTS_DIR / f'report{suffix}.json'}")


if __name__ == "__main__":
    main()
