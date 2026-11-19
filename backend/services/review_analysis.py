"""Inert contract for a future review analyzer."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol


AnalysisStatus = Literal["PENDING", "COMPLETED", "FAILED"]


@dataclass(frozen=True)
class ReviewAnalysisResult:
    analysis_status: AnalysisStatus
    sentiment: str | None = None
    topic: str | None = None
    issue_type: str | None = None
    ai_summary: str | None = None
    suggested_reply: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if self.analysis_status not in {"PENDING", "COMPLETED", "FAILED"}:
            raise ValueError("Invalid analysis status")
        if self.confidence is not None and (
            type(self.confidence) not in (int, float)
            or not math.isfinite(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise ValueError("confidence must be between 0 and 1")


class ReviewAnalyzer(Protocol):
    def analyze(self, review: Mapping[str, Any]) -> ReviewAnalysisResult: ...
