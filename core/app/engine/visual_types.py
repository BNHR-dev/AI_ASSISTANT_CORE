from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


VALID_QUALITIES = ("draft", "final")


@dataclass(frozen=True)
class VisualRequest:
    workflow_id: str
    positive_prompt: str
    negative_prompt: str = "blurry, low quality, distorted, deformed, extra fingers"
    seed: int = 42
    width: int = 1024
    height: int = 1024
    steps: int = 30
    cfg: float = 7.0
    variants_count: int = 1
    quality: str = "draft"

    def __post_init__(self) -> None:
        if self.quality not in VALID_QUALITIES:
            raise ValueError(
                f"Invalid quality {self.quality!r}; expected one of {VALID_QUALITIES}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VisualIntentAnalysis:
    subject_type: str
    render_intent: str
    style_flags: list[str]
    workflow_id: str
    reason: str
    subject_scores: dict[str, int]
    render_scores: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VisualResult:
    status: str
    workflow_id: str
    filename: str | None
    output_path: str | None
    parameters: dict[str, Any]
    raw_response: dict[str, Any] | None = None
    error: str | None = None
    filenames: list[str] | None = None
    output_paths: list[str] | None = None
    variants_count: int = 1
    completed_variants: int = 0
    partial: bool = False
    run_errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)