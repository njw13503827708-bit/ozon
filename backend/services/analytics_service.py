"""Read-only analytics ingestion and explicit, Decimal-based calculations."""
from decimal import Decimal, InvalidOperation

from backend.integrations.ozon.analytics_client import CORE_METRICS, EXTRA_METRICS, validate_dates
from backend.integrations.ozon.base_client import OzonApiError


def decimal_value(value):
    if value is None or isinstance(value, bool):
        raise ValueError("Missing numeric metric")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Invalid numeric metric") from None
    if not result.is_finite():
        raise ValueError("Invalid numeric metric")
    return result


def abc_analysis(rows, metric, *, complete=True):
    if not complete or not rows or any(row.get(metric) is None for row in rows):
        return []
    ranked = sorted(rows, key=lambda row: (-decimal_value(row[metric]), row["sku"]))
    values = [decimal_value(row[metric]) for row in ranked]
    total = sum(values, Decimal(0))
    if total <= 0 or any(value < 0 for value in values):
        return []
    cumulative = Decimal(0)
    result = []
    for row, value in zip(ranked, values):
        category = "A" if cumulative < Decimal("0.8") else "B" if cumulative < Decimal("0.95") else "C"
        cumulative += value / total
        result.append({**row, "class": category, "contribution_percent": str(value / total * 100),
                       "cumulative_percent": str(cumulative * 100)})
    return result


def funnel(totals):
    def ratio(numerator, denominator):
        if totals.get(numerator) is None or totals.get(denominator) is None:
            return None
        base = decimal_value(totals[denominator])
        return str(decimal_value(totals[numerator]) / base * 100) if base > 0 else None
    # Event-to-event and unit-to-event ratios are named explicitly, not claimed as buyer conversion.
    return {"view_to_cart": ratio("hits_tocart", "hits_view"),
            "ordered_units_per_cart": None}


class AnalyticsService:
    def __init__(self, repository, client):
        self.repo, self.client = repository, client

    def _read_pages(self, date_from, date_to, metrics, job_id, *, sku=None):
        result, seen = {}, set()
        offset = 0
        while True:
            if job_id and self.repo.erp.get_sync_job(job_id).get("cancel_requested"):
                raise InterruptedError("Analytics cancelled")
            response = self.client.get_data(date_from, date_to, metrics, offset=offset, **({"sku": sku} if sku is not None else {}))
            rows = response.get("result", {}).get("data")
            if not isinstance(rows, list):
                raise ValueError("Invalid analytics response")
            for row in rows:
                dimensions, values = row.get("dimensions"), row.get("metrics")
                if not isinstance(dimensions, list) or len(dimensions) != 2 or not isinstance(values, list) or len(values) != len(metrics):
                    raise ValueError("Invalid analytics row")
                sku, day = str(dimensions[0].get("id") or ""), str(dimensions[1].get("id") or "")
                if not sku or not date_from <= day <= date_to or len(day) != 10 or (sku, day) in seen:
                    raise ValueError("Invalid or repeated analytics dimensions")
                validate_dates(day, day)
                seen.add((sku, day))
                result[(sku, day)] = {"sku": sku, "day": day, "name": str(dimensions[0].get("name") or sku),
                                      **{metric: str(decimal_value(value)) for metric, value in zip(metrics, values)}}
            if len(rows) < 1000:
                return result
            offset += len(rows)
            if job_id:
                self.repo.erp.update_sync_job(job_id, progress=offset, message=f"已读取 {offset} 行分析数据")

    def refresh(self, store_id, date_from, date_to, *, extra_metrics=(), job_id=None):
        validate_dates(date_from, date_to)
        if set(extra_metrics) - set(EXTRA_METRICS):
            raise ValueError("Unsupported analytics metrics")
        status = {metric: "AVAILABLE" if metric in CORE_METRICS else "NOT_REQUESTED" for metric in CORE_METRICS + EXTRA_METRICS}
        metrics = list(CORE_METRICS) + list(extra_metrics)
        try:
            rows = self._read_pages(date_from, date_to, metrics, job_id)
            for metric in extra_metrics:
                status[metric] = "AVAILABLE"
        except OzonApiError as exc:
            if not extra_metrics or exc.status_code not in {400, 403}:
                raise
            rows = self._read_pages(date_from, date_to, CORE_METRICS, job_id)
            for metric in extra_metrics:
                status[metric] = "NO_PERMISSION" if exc.status_code == 403 else "UNAVAILABLE"
        if job_id and self.repo.erp.get_sync_job(job_id).get("cancel_requested"):
            raise InterruptedError("Analytics cancelled")
        return self.repo.save(store_id, date_from, date_to, list(rows.values()), status, job_id)

    def read(self, store_id, date_from, date_to, *, metric="revenue", q="", page=1, page_size=50):
        validate_dates(date_from, date_to)
        if metric not in CORE_METRICS or page < 1 or not 1 <= page_size <= 200:
            raise ValueError("Invalid analytics query")
        store = self.repo.erp.get_store(store_id)
        if store is None:
            raise KeyError(store_id)
        snapshot = self.repo.latest(store_id, date_from, date_to)
        fields = CORE_METRICS + EXTRA_METRICS
        response = {"store_id": store_id, "date_from": date_from, "date_to": date_to,
                    "timezone": "Europe/Moscow", "currency": store["currency"], "currency_source": "STORE_CONFIGURATION",
                    "source": "OZON_SELLER_API", "fetched_at": None, "availability": "NOT_SYNCED",
                    "last_refresh": self.repo.last_refresh(store_id, date_from, date_to),
                    "totals": {key: None for key in fields}, "metrics_status": {key: "NOT_SYNCED" for key in fields},
                    "items": [], "total": 0, "page": page, "page_size": page_size,
                    "trend": [], "abc": [], "leaders": [], "funnel": {}, "completeness": "UNKNOWN"}
        if snapshot is None:
            response["available_snapshots"] = self.repo.available_snapshots(store_id)
            return response
        rows = [row for row in snapshot["rows"] if date_from <= row["day"] <= date_to]
        available = [key for key in fields if snapshot["metrics_status"][key] == "AVAILABLE"]
        additive = [key for key in available if key != "position_category"]
        totals = {key: str(sum((decimal_value(row[key]) for row in rows), Decimal(0))) if key in additive else None for key in fields}
        products, days = {}, {}
        for row in rows:
            product = products.setdefault(row["sku"], {"sku": row["sku"], "name": row["name"], **{key: None for key in fields}})
            daily = days.setdefault(row["day"], {"day": row["day"], **{key: None for key in fields}})
            for key in additive:
                product[key] = str(decimal_value(product[key] or "0") + decimal_value(row[key]))
                daily[key] = str(decimal_value(daily[key] or "0") + decimal_value(row[key]))
            # Category position is an observed per-day value; never sum or invent keyword ranks.
            if "position_category" in available:
                product.setdefault("positions", []).append({"day": row["day"], "value": row["position_category"]})
        all_products = sorted(products.values(), key=lambda row: (-decimal_value(row[metric]), row["sku"]))
        filtered = [row for row in all_products if q.casefold() in f"{row['sku']} {row['name']}".casefold()]
        response.update({"availability": "AVAILABLE" if len(available) == len(fields) else "PARTIAL",
                         "currency": snapshot["currency"],
                         "fetched_at": snapshot["fetched_at"], "completeness": snapshot["completeness"],
                         "metrics_status": snapshot["metrics_status"], "totals": totals,
                         "items": filtered[(page - 1) * page_size:page * page_size], "total": len(filtered),
                         "trend": [days[key] for key in sorted(days)], "abc": abc_analysis(all_products, metric),
                         "leaders": all_products[:20], "funnel": funnel(totals)})
        return response
