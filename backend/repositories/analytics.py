"""Immutable successful analytics snapshots, scoped to the persisted Store."""
import json
import uuid

from backend.db.erp import database
from backend.repositories.erp import ErpRepository, now


class AnalyticsRepository:
    def __init__(self, root):
        self.root = root
        self.erp = ErpRepository(root)

    def save(self, store_id, date_from, date_to, rows, metrics_status, job_id=None):
        store = self.erp.get_store(store_id)
        if store is None or store["platform"] != "OZON":
            raise ValueError("Unknown Ozon store")
        identifier = uuid.uuid4().hex
        with database(self.root) as db:
            db.execute("INSERT INTO analytics_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                identifier, store["organization_id"], store_id, store["platform"], date_from, date_to,
                "OZON_SELLER_API", store["currency"], now(), json.dumps(metrics_status),
                json.dumps(rows, ensure_ascii=False), "COMPLETE", job_id,
            ))
        return identifier

    def latest(self, store_id, date_from, date_to):
        with database(self.root, readonly=True) as db:
            row = db.execute("""SELECT a.* FROM analytics_snapshots a JOIN stores s ON s.id=a.store_id
                WHERE a.store_id=? AND a.organization_id=s.organization_id AND a.platform=s.platform
                AND a.date_from<=? AND a.date_to>=? AND a.completeness='COMPLETE'
                ORDER BY a.fetched_at DESC, a.rowid DESC LIMIT 1""", (store_id, date_from, date_to)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["rows"] = json.loads(result.pop("rows_json"))
        result["metrics_status"] = json.loads(result.pop("metrics_status_json"))
        return result

    def available_snapshots(self, store_id):
        with database(self.root, readonly=True) as db:
            rows = db.execute("""SELECT a.date_from, a.date_to, MAX(a.fetched_at) AS fetched_at, a.source
                FROM analytics_snapshots a JOIN stores s ON s.id=a.store_id
                WHERE a.store_id=? AND a.organization_id=s.organization_id AND a.platform=s.platform
                AND a.completeness='COMPLETE'
                GROUP BY a.date_from, a.date_to, a.source ORDER BY fetched_at DESC LIMIT 20""", (store_id,)).fetchall()
        return [dict(row) for row in rows]

    def last_refresh(self, store_id, date_from, date_to):
        with database(self.root, readonly=True) as db:
            row = db.execute("""SELECT j.id, j.status, j.message, j.finished_at FROM sync_jobs j
                JOIN stores s ON s.id=j.store_id AND s.organization_id=j.organization_id AND s.platform=j.platform
                WHERE j.store_id=? AND j.type='OZON_ANALYTICS_SYNC' AND j.entity_id IN (?,?)
                ORDER BY j.started_at DESC, j.rowid DESC LIMIT 1""",
                (store_id, f"{date_from}:{date_to}:0", f"{date_from}:{date_to}:1")).fetchone()
        return dict(row) if row else None
