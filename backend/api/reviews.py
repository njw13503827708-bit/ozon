"""Review Center reads and local-only reply draft routes."""
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.repositories.reviews import ReviewRepository
from backend.services.review_service import Platform, ReplyStatus, ReviewService


ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix="/api/erp/reviews", tags=["erp-reviews"])


class DraftCreate(BaseModel):
    store_id: str
    content: str
    source: Literal["MANUAL", "AI_SUGGESTION"]


class DraftUpdate(BaseModel):
    store_id: str
    content: str | None = None
    status: Literal["DRAFT", "DISCARDED"] | None = None


@router.get("")
def list_reviews(
    platform: Platform | None = None,
    store_id: str | None = None,
    product_id: str | None = None,
    rating: int | None = Query(None, ge=1, le=5),
    reply_status: ReplyStatus | None = None,
    published_from: str | None = None,
    published_to: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    try:
        return ReviewService(ReviewRepository(ROOT)).list_reviews(
            platform=platform, store_id=store_id, product_id=product_id, rating=rating,
            reply_status=reply_status, published_from=published_from, published_to=published_to,
            page=page, page_size=page_size,
        )
    except ValueError:
        raise HTTPException(422, "Invalid review filters") from None


@router.get("/{review_id}")
def get_review(review_id: str):
    review = ReviewService(ReviewRepository(ROOT)).get_review(review_id)
    if review is None:
        raise HTTPException(404, "Review not found")
    return review


@router.post("/{review_id}/drafts", status_code=201)
def create_draft(review_id: str, body: DraftCreate):
    try:
        draft = ReviewService(ReviewRepository(ROOT)).create_draft(
            review_id, body.store_id, body.content, body.source)
    except ValueError:
        raise HTTPException(422, "Reply content is required") from None
    if draft is None:
        raise HTTPException(404, "Review not found in Store")
    return draft


@router.get("/{review_id}/drafts")
def list_drafts(review_id: str, store_id: str):
    drafts = ReviewService(ReviewRepository(ROOT)).list_drafts(review_id, store_id)
    if drafts is None:
        raise HTTPException(404, "Review not found in Store")
    return drafts


@router.patch("/{review_id}/drafts/{draft_id}")
def update_draft(review_id: str, draft_id: str, body: DraftUpdate):
    try:
        draft = ReviewService(ReviewRepository(ROOT)).update_draft(
            review_id, draft_id, body.store_id, body.content, body.status)
    except ValueError:
        raise HTTPException(422, "Invalid reply draft") from None
    if draft is None:
        raise HTTPException(404, "Reply draft not found in Store")
    return draft
