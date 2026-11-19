import unittest
from dataclasses import dataclass


class ReviewAdapterContractTest(unittest.TestCase):
    def test_fake_page_reader_returns_normalized_reviews_and_opaque_cursor(self):
        from backend.integrations.reviews import ReviewPage, NormalizedReview, NormalizedReviewMedia, ReviewReadAdapter

        @dataclass
        class FakeAdapter:
            seen: list

            def read_page(self, store_id: str, cursor: str | None, limit: int) -> ReviewPage:
                self.seen.append((store_id, cursor, limit))
                return ReviewPage(items=(NormalizedReview(external_id="r-1", text="Nice", rating=5,
                                                          reply_status="UNREPLIED",
                                                          published_at="2026-09-25T09:00:00+00:00",
                                                          media=(NormalizedReviewMedia(media_type="IMAGE",
                                                                                       source_url="https://cdn.example.test/one.jpg"),)),),
                                  next_cursor="opaque-next")

        adapter: ReviewReadAdapter = FakeAdapter([])
        page = adapter.read_page("ozon", "opaque-prev", 50)
        self.assertEqual([("ozon", "opaque-prev", 50)], adapter.seen)
        self.assertEqual(("r-1", "Nice", 5), (page.items[0].external_id, page.items[0].text, page.items[0].rating))
        self.assertEqual("UNREPLIED", page.items[0].reply_status)
        self.assertEqual("https://cdn.example.test/one.jpg", page.items[0].media[0].source_url)
        self.assertEqual("opaque-next", page.next_cursor)


if __name__ == "__main__":
    unittest.main()
