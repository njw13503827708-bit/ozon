"""SQLite persistence for marketplace reviews and local reply drafts."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from backend.db.erp import DEFAULT_ORGANIZATION_ID, database
from backend.services.review_analysis import ReviewAnalysisResult


def _timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Timestamp must include timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("Timestamp must include timezone") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Media source must be a public HTTP URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Media source must be a public HTTP URL")
    return value


class ReviewRepository:
    def __init__(self, root: Path):
        self.root = Path(root)

    def upsert_review(self, item: Mapping[str, Any]) -> str:
        rating = item.get("rating")
        if type(rating) is not int or not 1 <= rating <= 5:
            raise ValueError("rating must be an integer from 1 to 5")
        if not isinstance(item.get("text"), str):
            raise ValueError("text is required")
        media = item.get("media", ())
        if not isinstance(media, (tuple, list)):
            raise ValueError("media must be a list")
        normalized_media = []
        for source in media:
            if not isinstance(source, Mapping) or source.get("media_type") not in {"IMAGE", "VIDEO"}:
                raise ValueError("Invalid media type")
            normalized_media.append((source.get("external_id"), source["media_type"], _public_url(source.get("source_url"))))
        with database(self.root) as db:
            store = db.execute("SELECT organization_id, platform FROM stores WHERE id=?", (item["store_id"],)).fetchone()
            if store is None:
                raise ValueError("Unknown Store")
            organization_id, platform = store
            if item.get("organization_id", organization_id) != organization_id or item.get("platform", platform) != platform:
                raise ValueError("Review Store scope mismatch")
            external_id = item.get("external_id") or None
            existing = db.execute(
                "SELECT id,product_id,listing_id,reply_status,published_at FROM reviews WHERE organization_id=? AND platform=? AND store_id=? AND external_id=?",
                (organization_id, platform, item["store_id"], external_id),
            ).fetchone() if external_id is not None else None
            product_id = item.get("product_id", existing["product_id"] if existing else None)
            listing_id = item.get("listing_id", existing["listing_id"] if existing else None)
            reply_status = item.get("reply_status", existing["reply_status"] if existing else "UNKNOWN")
            published_at = _timestamp(item.get("published_at", existing["published_at"] if existing else None))
            if reply_status not in {"UNKNOWN", "UNREPLIED", "REPLIED"}:
                raise ValueError("Invalid reply status")
            if product_id is not None and not db.execute(
                "SELECT 1 FROM products WHERE id=? AND organization_id=?", (product_id, organization_id)
            ).fetchone():
                raise ValueError("Review Product scope mismatch")
            if listing_id is not None and not db.execute(
                "SELECT 1 FROM marketplace_listings WHERE id=? AND product_id=? AND store_id=? AND platform=? AND organization_id=?",
                (listing_id, product_id, item["store_id"], platform, organization_id)
            ).fetchone():
                raise ValueError("Review Listing scope mismatch")
            review_id = existing["id"] if existing else str(uuid.uuid4())
            stamp = _now()
            if existing:
                db.execute("""UPDATE reviews SET text=?, rating=?, product_id=?, listing_id=?, mapping_status=?,
                           reply_status=?, published_at=?, updated_at=? WHERE id=?""",
                           (item["text"], rating, product_id, listing_id, "MAPPED" if product_id else "UNMAPPED",
                            reply_status, published_at, stamp, review_id))
            else:
                db.execute("""INSERT INTO reviews(id,organization_id,platform,store_id,external_id,product_id,
                           listing_id,mapping_status,text,rating,reply_status,published_at,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (review_id, organization_id, platform, item["store_id"], external_id, product_id,
                            listing_id, "MAPPED" if product_id else "UNMAPPED", item["text"], rating,
                            reply_status, published_at, stamp, stamp))
            if "media" in item:
                db.execute("DELETE FROM review_media WHERE review_id=?", (review_id,))
                db.executemany("""INSERT INTO review_media(review_id,external_id,media_type,source_url,created_at)
                               VALUES(?,?,?,?,?)""",
                               [(review_id, external_id, media_type, url, stamp)
                                for external_id, media_type, url in normalized_media])
        return review_id

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            row = db.execute("""SELECT id,organization_id,platform,store_id,external_id,product_id,
                              listing_id,mapping_status,text,rating,reply_status,published_at,created_at,updated_at
                              FROM reviews WHERE id=? AND organization_id=?""",
                             (review_id, DEFAULT_ORGANIZATION_ID)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["media"] = [dict(media) for media in db.execute(
                "SELECT id,external_id,media_type,source_url FROM review_media WHERE review_id=? ORDER BY id", (review_id,)
            )]
            return result

    def list_reviews(self, *, platform: str | None = None, store_id: str | None = None,
                     product_id: str | None = None, rating: int | None = None,
                     reply_status: str | None = None, published_from: str | None = None,
                     published_to: str | None = None, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        clauses = ["organization_id=?"]
        params: list[Any] = [DEFAULT_ORGANIZATION_ID]
        for column, value in (("platform", platform), ("store_id", store_id), ("product_id", product_id),
                              ("rating", rating), ("reply_status", reply_status)):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        for op, value in ((">=", published_from), ("<=", published_to)):
            if value is not None:
                clauses.append(f"published_at{op}?")
                params.append(_timestamp(value))
        where = " AND ".join(clauses)
        with database(self.root, readonly=True) as db:
            total = db.execute(f"SELECT COUNT(*) FROM reviews WHERE {where}", params).fetchone()[0]
            rows = [dict(row) for row in db.execute(
                f"""SELECT id,organization_id,platform,store_id,external_id,product_id,listing_id,
                    mapping_status,text,rating,reply_status,published_at,created_at,updated_at
                    FROM reviews WHERE {where} ORDER BY published_at DESC, id LIMIT ? OFFSET ?""",
                [*params, page_size, (page - 1) * page_size],
            )]
        return {"items": rows, "total": total, "page": page, "page_size": page_size}

    def _scoped_review(self, db, review_id: str, store_id: str):
        return db.execute(
            "SELECT organization_id,platform,store_id FROM reviews WHERE id=? AND organization_id=? AND store_id=?",
            (review_id, DEFAULT_ORGANIZATION_ID, store_id),
        ).fetchone()

    def save_analysis(self, review_id: str, store_id: str,
                      result: ReviewAnalysisResult) -> dict[str, Any] | None:
        with database(self.root) as db:
            review = self._scoped_review(db, review_id, store_id)
            if review is None:
                return None
            analysis_id = str(uuid.uuid4())
            db.execute("""INSERT INTO review_analyses
                       (id,review_id,organization_id,platform,store_id,sentiment,topic,issue_type,
                        ai_summary,suggested_reply,confidence,analysis_status,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (analysis_id, review_id, review["organization_id"], review["platform"], store_id,
                        result.sentiment, result.topic, result.issue_type, result.ai_summary,
                        result.suggested_reply, result.confidence, result.analysis_status, _now()))
            return dict(db.execute("SELECT * FROM review_analyses WHERE id=?", (analysis_id,)).fetchone())

    def list_analyses(self, review_id: str, store_id: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            if self._scoped_review(db, review_id, store_id) is None:
                return None
            return {"items": [dict(row) for row in db.execute(
                """SELECT * FROM review_analyses WHERE review_id=? AND organization_id=? AND store_id=?
                   ORDER BY created_at DESC, id""", (review_id, DEFAULT_ORGANIZATION_ID, store_id)
            )]}

    def create_draft(self, review_id: str, store_id: str, content: str, source: str) -> dict[str, Any] | None:
        if not isinstance(content, str) or not content.strip() or source not in {"MANUAL", "AI_SUGGESTION"}:
            raise ValueError("Invalid reply draft")
        with database(self.root) as db:
            review = self._scoped_review(db, review_id, store_id)
            if review is None:
                return None
            draft_id, stamp = str(uuid.uuid4()), _now()
            db.execute("""INSERT INTO review_reply_drafts
                       (id,review_id,organization_id,platform,store_id,content,source,status,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                       (draft_id, review_id, review["organization_id"], review["platform"], store_id,
                        content.strip(), source, "DRAFT", stamp, stamp))
            return dict(db.execute("SELECT * FROM review_reply_drafts WHERE id=?", (draft_id,)).fetchone())

    def list_drafts(self, review_id: str, store_id: str) -> dict[str, Any] | None:
        with database(self.root, readonly=True) as db:
            if self._scoped_review(db, review_id, store_id) is None:
                return None
            return {"items": [dict(row) for row in db.execute(
                """SELECT * FROM review_reply_drafts WHERE review_id=? AND organization_id=? AND store_id=?
                   ORDER BY created_at DESC, id""", (review_id, DEFAULT_ORGANIZATION_ID, store_id)
            )]}

    def update_draft(self, review_id: str, draft_id: str, store_id: str,
                     content: str | None, status: str | None) -> dict[str, Any] | None:
        if content is not None and (not isinstance(content, str) or not content.strip()):
            raise ValueError("Invalid reply draft")
        if status is not None and status not in {"DRAFT", "DISCARDED"}:
            raise ValueError("Invalid reply draft")
        if content is None and status is None:
            raise ValueError("No draft change")
        with database(self.root) as db:
            if self._scoped_review(db, review_id, store_id) is None:
                return None
            current = db.execute("""SELECT * FROM review_reply_drafts WHERE id=? AND review_id=?
                              AND organization_id=? AND store_id=?""",
                              (draft_id, review_id, DEFAULT_ORGANIZATION_ID, store_id)).fetchone()
            if current is None:
                return None
            db.execute("""UPDATE review_reply_drafts SET content=?, status=?, updated_at=? WHERE id=?""",
                       (content.strip() if content is not None else current["content"],
                        status if status is not None else current["status"], _now(), draft_id))
            return dict(db.execute("SELECT * FROM review_reply_drafts WHERE id=?", (draft_id,)).fetchone())
