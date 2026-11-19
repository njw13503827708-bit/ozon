"""Read-only normalized review page boundary for future marketplace adapters."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NormalizedReviewMedia:
    media_type: str
    source_url: str
    external_id: str | None = None


@dataclass(frozen=True)
class NormalizedReview:
    external_id: str | None
    text: str
    rating: int
    published_at: str | None = None
    reply_status: str = "UNKNOWN"
    media: tuple[NormalizedReviewMedia, ...] = ()


@dataclass(frozen=True)
class ReviewPage:
    items: tuple[NormalizedReview, ...]
    next_cursor: str | None


class ReviewReadAdapter(Protocol):
    def read_page(self, store_id: str, cursor: str | None, limit: int) -> ReviewPage: ...
