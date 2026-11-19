"""Independent local versions/read caches. Initialization is an explicit command."""
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path

from backend.db.erp import DEFAULT_ORGANIZATION_ID
from backend.repositories.erp import now
from scripts.batch_upload_approval import batch_upload_lock


SCHEMA = '''
CREATE TABLE IF NOT EXISTS local_schema(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO local_schema VALUES(1);
CREATE TABLE IF NOT EXISTS versions(
 organization_id TEXT NOT NULL, store_id TEXT NOT NULL, listing_id INTEGER NOT NULL,
 version INTEGER NOT NULL, content_json TEXT NOT NULL, source_revision TEXT NOT NULL,
 saved_at TEXT NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL,
 reviewed_at TEXT, review_json TEXT,
 PRIMARY KEY(organization_id,store_id,listing_id,version));
CREATE TABLE IF NOT EXISTS snapshots(
 id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, kind TEXT NOT NULL, store_id TEXT NOT NULL,
 date_from TEXT NOT NULL, date_to TEXT NOT NULL, fetched_at TEXT NOT NULL,
 data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, kind TEXT NOT NULL, store_id TEXT NOT NULL,
 scope TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT, progress INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
 cancel_requested INTEGER NOT NULL DEFAULT 0);
'''


def decode(row):
    if row is None:
        return None
    item = dict(row)
    for key in ('content', 'review', 'data'):
        if f'{key}_json' in item:
            raw = item.pop(f'{key}_json')
            item[key] = json.loads(raw) if raw else None
    return item


class LocalOperationsUnavailable(RuntimeError):
    pass


class LocalOperationsRepository:
    def __init__(self, root):
        self.path = Path(root) / 'runtime/local-operations.sqlite3'

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(SCHEMA)
            db.commit()
        return self.path

    @contextmanager
    def connect(self, *, readonly=True):
        if not self.path.is_file():
            raise LocalOperationsUnavailable('本地优化/订单库未初始化；请运行明确的候选初始化命令')
        with nullcontext() if readonly else batch_upload_lock(self.path.parent.parent,wait_seconds=30):
            db = sqlite3.connect(self.path.resolve().as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                if {row[0] for row in db.execute('SELECT version FROM local_schema')} != {1}:
                    raise LocalOperationsUnavailable('本地优化/订单库版本不匹配')
                yield db
                if not readonly:
                    db.commit()
            finally:
                db.close()

    def versions(self, store_id, listing_id):
        with self.connect() as db:
            return [decode(row) for row in db.execute('''SELECT * FROM versions
                WHERE organization_id=? AND store_id=? AND listing_id=? ORDER BY version DESC''',
                (DEFAULT_ORGANIZATION_ID, store_id, listing_id))]

    def version(self, store_id, listing_id, version):
        with self.connect() as db:
            return decode(db.execute('SELECT * FROM versions WHERE organization_id=? AND store_id=? AND listing_id=? AND version=?',
                (DEFAULT_ORGANIZATION_ID, store_id, listing_id, version)).fetchone())

    def save_version(self, store_id, listing_id, content, source_revision, expected_version, actor):
        with self.connect(readonly=False) as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute('SELECT MAX(version) FROM versions WHERE organization_id=? AND store_id=? AND listing_id=?',
                (DEFAULT_ORGANIZATION_ID, store_id, listing_id)).fetchone()[0] or 0
            if latest != expected_version:
                raise RuntimeError('版本已变化，请重新打开核对；当前未保存内容没有被覆盖')
            version = latest + 1
            db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (DEFAULT_ORGANIZATION_ID, store_id, listing_id, version, json.dumps(content, ensure_ascii=False),
                 source_revision, now(), actor, 'DRAFT', None, None))
        return self.version(store_id, listing_id, version)

    def review_version(self, store_id, listing_id, version, source_revision, actor, confirmation):
        with self.connect(readonly=False) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM versions WHERE organization_id=? AND store_id=? AND listing_id=? AND version=?',
                (DEFAULT_ORGANIZATION_ID, store_id, listing_id, version)).fetchone()
            if row is None:
                raise KeyError('未找到本店本地版本')
            latest = db.execute('SELECT MAX(version) FROM versions WHERE organization_id=? AND store_id=? AND listing_id=?',
                (DEFAULT_ORGANIZATION_ID, store_id, listing_id)).fetchone()[0]
            if latest != version or row['source_revision'] != source_revision:
                raise RuntimeError('版本或商品来源已变化，请重新核对')
            db.execute('UPDATE versions SET status=?,reviewed_at=?,review_json=? WHERE organization_id=? AND store_id=? AND listing_id=? AND version=?',
                ('REVIEWED', now(), json.dumps({'actor': actor, 'confirmation': confirmation}, ensure_ascii=False),
                 DEFAULT_ORGANIZATION_ID, store_id, listing_id, version))
        return self.version(store_id, listing_id, version)

    def save_snapshot(self, kind, store_id, date_from, date_to, data):
        identifier = uuid.uuid4().hex
        with self.connect(readonly=False) as db:
            db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?)',
                (identifier, DEFAULT_ORGANIZATION_ID, kind, store_id, date_from, date_to, now(), json.dumps(data, ensure_ascii=False)))
        return identifier

    def snapshot(self, kind, store_id, date_from, date_to, *, allow_partial=False):
        range_clause = 'date_from>=? AND date_to<=?' if allow_partial else 'date_from<=? AND date_to>=?'
        with self.connect() as db:
            return decode(db.execute(f'''SELECT * FROM snapshots WHERE organization_id=? AND kind=? AND store_id=?
                AND {range_clause} ORDER BY fetched_at DESC,rowid DESC LIMIT 1''',
                (DEFAULT_ORGANIZATION_ID, kind, store_id, date_from, date_to)).fetchone())

    def start_job(self, kind, store_id, scope):
        with self.connect(readonly=False) as db:
            db.execute('BEGIN IMMEDIATE')
            active = "" if kind == 'OPTIMIZATION_GENERATE' else " AND status IN ('QUEUED','RUNNING')"
            row = db.execute("SELECT * FROM jobs WHERE organization_id=? AND kind=? AND store_id=? AND scope=?" + active,
                (DEFAULT_ORGANIZATION_ID, kind, store_id, scope)).fetchone()
            if row:
                return dict(row), False
            identifier = uuid.uuid4().hex
            db.execute('INSERT INTO jobs(id,organization_id,kind,store_id,scope,status,started_at) VALUES(?,?,?,?,?,?,?)',
                (identifier, DEFAULT_ORGANIZATION_ID, kind, store_id, scope, 'QUEUED', now()))
        return self.job(identifier), True

    def job(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=? AND organization_id=?', (identifier, DEFAULT_ORGANIZATION_ID)).fetchone()
        return dict(row) if row else None

    def update_job(self, identifier, *, status, progress=0, message=''):
        with self.connect(readonly=False) as db:
            db.execute('UPDATE jobs SET status=?,progress=?,message=?,finished_at=? WHERE id=? AND organization_id=?',
                (status, progress, message, now() if status in {'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED'} else None,
                 identifier, DEFAULT_ORGANIZATION_ID))

    def cancel_job(self, identifier):
        with self.connect(readonly=False) as db:
            db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=? AND organization_id=?',
                       (identifier, DEFAULT_ORGANIZATION_ID))

    def interrupt_jobs(self):
        with self.connect(readonly=False) as db:
            db.execute("UPDATE jobs SET status='INTERRUPTED',finished_at=?,message='服务重启，读取中断；请人工重试' WHERE organization_id=? AND status IN ('QUEUED','RUNNING')", (now(), DEFAULT_ORGANIZATION_ID))
