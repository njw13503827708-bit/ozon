"""Fixed read-only AnalyticsGetData contract from the supplied Seller Swagger."""
from datetime import date
import threading
import time

from .base_client import OzonBaseClient


CORE_METRICS = ("revenue", "ordered_units")
EXTRA_METRICS = ("hits_view", "session_view_pdp", "hits_tocart", "returns", "cancellations", "position_category")


def validate_dates(date_from: str, date_to: str) -> None:
    start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if start.isoformat() != date_from or end.isoformat() != date_to or start > end or (end - start).days > 366:
        raise ValueError("Invalid analytics date range")


class OzonAnalyticsClient(OzonBaseClient):
    _state_lock = threading.Lock()
    _accounts = {}

    def _request_once(self, endpoint, payload):
        if self._transport is not None:
            return super()._request_once(endpoint, payload)
        with self._state_lock:
            state = self._accounts.setdefault(self.client_id, {"lock": threading.Lock(), "last": None})
        # Swagger limits AnalyticsGetData to one request per account per minute, including retries.
        with state["lock"]:
            if state["last"] is not None:
                delay = 60 - (time.monotonic() - state["last"])
                if delay > 0:
                    self._sleep(delay)
            state["last"] = time.monotonic()
            return super()._request_once(endpoint, payload)

    def get_data(self, date_from: str, date_to: str, metrics, *, offset: int = 0, sku: str | None = None):
        validate_dates(date_from, date_to)
        values = list(metrics)
        if not values or len(values) > 14 or len(values) != len(set(values)) or set(values) - set(CORE_METRICS + EXTRA_METRICS):
            raise ValueError("Unsupported analytics metrics")
        if offset < 0:
            raise ValueError("Invalid analytics offset")
        if sku is not None and (not isinstance(sku, str) or not sku.isdigit() or int(sku) <= 0):
            raise ValueError("Invalid analytics SKU filter")
        return self._post_json("/v1/analytics/data", {
            "date_from": date_from, "date_to": date_to, "metrics": values,
            "dimension": ["sku", "day"], "filters": [{"key": "sku", "op": "EQ", "value": sku}] if sku else [], "sort": [],
            "limit": 1000, "offset": offset,
        })
