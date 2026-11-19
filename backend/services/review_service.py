"""Validated Review Center queries and local reply drafts."""
from typing import Literal

from backend.repositories.reviews import ReviewRepository, _timestamp


Platform = Literal["OZON", "WILDBERRIES", "KASPI"]
ReplyStatus = Literal["UNKNOWN", "UNREPLIED", "REPLIED"]


class ReviewService:
    def __init__(self, repository: ReviewRepository):
        self.repository = repository

    def list_reviews(self, **filters):
        start = _timestamp(filters.get("published_from"))
        end = _timestamp(filters.get("published_to"))
        if start and end and start > end:
            raise ValueError("published_from must not exceed published_to")
        return self.repository.list_reviews(**filters)

    def get_review(self, review_id: str):
        return self.repository.get_review(review_id)

    def create_draft(self, review_id: str, store_id: str, content: str, source: str):
        return self.repository.create_draft(review_id, store_id, content, source)

    def list_drafts(self, review_id: str, store_id: str):
        return self.repository.list_drafts(review_id, store_id)

    def update_draft(self, review_id: str, draft_id: str, store_id: str,
                     content: str | None, status: str | None):
        return self.repository.update_draft(review_id, draft_id, store_id, content, status)
