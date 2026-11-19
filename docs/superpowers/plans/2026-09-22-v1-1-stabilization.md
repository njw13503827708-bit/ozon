# JLC Cross-border AI ERP V1.1 Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved V1.1 scope in two gated stages: V1.1A stabilizes and completes the Ozon read-side foundation, then V1.1B adds the bounded Organization, Store Center, Product Center, and legacy-product index work without entering V2.

**Architecture:** Keep the current FastAPI, React/Vite, SQLite, JSON-artifact, and Ozon adapter boundaries. V1.1A repairs the demonstrated V1 defects, adds non-destructive listing reconciliation, persists bounded attribute responses, implements recoverable/cooperatively cancellable jobs, protects compatible credential files, and separates shared Ozon transport from account validation. After its mandatory STOP and approval, V1.1B adds only the approved read-model and indexing work. Reuse the existing batch authorization hash and current repository/client patterns; add no new framework or dependency.

**Tech Stack:** Python 3.12, stdlib `sqlite3`/`urllib`/`unittest`, FastAPI, React 19, TypeScript 5.8, Vite 6, Playwright already present in the workspace.

**Spec:** `D:\Codex\state\attachments\5e66607e-30f9-457a-b5f1-fe6093a03726\已粘贴的文本.txt` (MASTER DEVELOPMENT DIRECTIVE V1.0 supplied for this review); Task 1 places the reviewed canonical copy at `docs/MASTER_DEVELOPMENT_DIRECTIVE.md`.

## Global Constraints

- Current phase is only `V1.1 Stabilization`; do not implement inventory, orders, finance, SaaS tenancy, billing, authentication, AI-provider abstraction, object storage, Windows/Tauri packaging, Kaspi, or Wildberries.
- Preserve the verified V1 abilities: Store/Product/SKU/Listing persistence, approximately 594-product Ozon read-only sync, pagination, retry, asynchronous jobs, product details, attributes, API-key masking, the listing pipeline, image QC, and the manual upload gate.
- Every Ozon CREATE/UPDATE path remains `PREVIEW -> CONFIRM -> EXECUTE`; one whole-batch confirmation is required, the review is read-only, and any failed, unfinished, stale, or unreviewed item blocks the whole upload.
- Development and tests make zero live Ozon calls and zero inventory/warehouse/activation calls. All Ozon test traffic uses injected or mocked transports.
- Do not submit inventory fields. Do not modify `products/`, `batches/`, `runtime/task-db.sqlite3`, historical payloads, Ozon task IDs, publication receipts, or source images.
- Keep `auto_upload` in persisted legacy schemas for compatibility; in V1.1 it means automatic preparation up to the final batch review, never authorization to write.
- Keep SQLite for local V1.1. PostgreSQL, SQLAlchemy, Alembic, Supabase, and multi-tenant schema work belong to later approved phases.
- No new dependency is allowed for V1.1.

## Verified Review Baseline — 2026-09-22

- The ERP/product-sync, batch-gate, Windows setup, and frontend API source files in the supplied ZIP match the active workspace by SHA-256.
- Offline `project_self_check.py`: `PASS`, 18 checks, 0 failures, 1 warning for the already quarantined pre-contract product `P000001`; Ozon writes 0, inventory writes 0.
- Focused run: 90 tests executed, 84 passed, 3 skipped, 3 errors. Two errors are Windows default-codepage reads in `tests/test_multi_store_upload.py`; one is the review extraction directory being unable to create its self-check report, not an application assertion failure.
- The active `.venv\Scripts\python.exe` exists but cannot start because its original Python 3.12 interpreter was removed. `setup_windows.ps1` checks only file existence, so it does not repair this demonstrated failure.
- Current catalog-sync tests intentionally report `SUCCEEDED` when attribute synchronization fails, malformed rows are silently omitted, and `sync_job_items`/`audit_logs` are not populated by the current sync service.
- Full-suite status for this exact ZIP is **NOT VERIFIED** in this plan-only pass; Task 6 must run it after the Windows environment repair and report every remaining failure.

## Review Focus

- Two operators click “同步 Ozon 商品” together: only one job may run per store; both callers receive the same job ID.
- Ozon returns a malformed row, omits a requested detail, or fails an attribute chunk: valid rows remain saved, the failed entities are recorded, and the job ends `PARTIAL`, never falsely `SUCCEEDED`.
- A non-host LAN operator calls the sync mutation: the API returns 403 before reading credentials or starting a worker.
- Product data or selected stores change after batch review: the existing `review_hash` authorization becomes invalid and Ozon CREATE/UPDATE remains at zero.
- Windows uses GBK as the default code page or retains a dead `.venv` launcher: tests read UTF-8 explicitly and setup recreates the environment with the currently verified Python.

---

## V1.1A Core Stabilization

### Task 1: Establish the canonical V1.1 scope and remove contradictory contracts

**Files:**
- Create: `docs/MASTER_DEVELOPMENT_DIRECTIVE.md`
- Create: `tests/test_v1_1_scope_contract.py`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `CURRENT_TASK.md`
- Modify: `PROJECT_HANDOFF.md`

**Interfaces:**
- Consumes: the supplied MASTER DEVELOPMENT DIRECTIVE V1.0.
- Produces: one in-repository source of truth and current-state documents that all name `V1.1 Stabilization` and the batch-level upload confirmation contract.

- [ ] **Step 1: Write the failing scope-contract test**

```python
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class V11ScopeContractTest(unittest.TestCase):
    def test_master_directive_and_current_documents_lock_v1_1(self):
        directive = (ROOT / "docs/MASTER_DEVELOPMENT_DIRECTIVE.md").read_text(encoding="utf-8")
        self.assertIn("MASTER DEVELOPMENT DIRECTIVE V1.0", directive)
        self.assertIn("只允许执行 V1.1", directive)
        self.assertIn("PREVIEW", directive)
        self.assertIn("CONFIRM", directive)
        self.assertIn("EXECUTE", directive)
        for relative in ("AGENTS.md", "README.md", "CURRENT_TASK.md", "PROJECT_HANDOFF.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("docs/MASTER_DEVELOPMENT_DIRECTIVE.md", text, relative)
            self.assertIn("V1.1 Stabilization", text, relative)

    def test_current_documents_do_not_authorize_unreviewed_upload(self):
        combined = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8")
            for relative in ("AGENTS.md", "README.md", "CURRENT_TASK.md", "PROJECT_HANDOFF.md")
        )
        self.assertNotIn("点击运行任务即明确授权当前采集箱批次执行到Ozon创建或更新", combined)
        self.assertNotIn("新建批次默认且强制使用自动提交", combined)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_v1_1_scope_contract -v`

Expected: FAIL because `docs/MASTER_DEVELOPMENT_DIRECTIVE.md` is absent and the current documents still contain the old automatic-upload/product-positioning language.

- [ ] **Step 3: Add the directive verbatim and minimally reconcile current documents**

Copy the supplied directive byte-for-byte into `docs/MASTER_DEVELOPMENT_DIRECTIVE.md`. In the four current-state documents:

- identify the product as `JLC Cross-border AI ERP`;
- mark only `V1.1 Stabilization` active;
- preserve the existing local 1688-to-Ozon production pipeline as a V1 module;
- state that `auto_upload` is a backward-compatible internal field and cannot bypass whole-batch review;
- mark V2+ capabilities as `NOT IMPLEMENTED` rather than describing them as current work;
- link `docs/MASTER_DEVELOPMENT_DIRECTIVE.md` once near the top.

Do not rewrite historical dated evidence or old review packages; only current instructions and current-state summaries change.

- [ ] **Step 4: Run the scope test and the offline self-check**

Run: `.venv\Scripts\python.exe -m unittest tests.test_v1_1_scope_contract -v`

Expected: PASS.

Run: `.venv\Scripts\python.exe scripts\project_self_check.py`

Expected: decision `PASS`, `ozon_write_api_calls=0`, `inventory_api_calls=0`.

---

### Task 2: Repair the Windows environment and remove default-codepage test failures

**Files:**
- Modify: `scripts/setup_windows.ps1`
- Modify: `tests/test_windows_setup.py`
- Create: `tests/test_utf8_test_contract.py`
- Modify: every `tests/test_*.py` occurrence of `Path.read_text()` that omits an explicit encoding

**Interfaces:**
- Consumes: installed, runnable `python` selected by `Test-CommandAvailable`.
- Produces: a runnable `.venv\Scripts\python.exe` and UTF-8-stable tests on Chinese Windows.

- [ ] **Step 1: Add RED coverage for a stale virtual environment and implicit text decoding**

Add to `tests/test_windows_setup.py`:

```python
def test_bootstrap_rebuilds_an_existing_but_unrunnable_venv(self):
    script = (ROOT / "scripts/setup_windows.ps1").read_text(encoding="utf-8")
    self.assertIn("Test-CommandAvailable $VenvPython", script)
    self.assertIn("python -m venv --clear .venv", script)
```

Create `tests/test_utf8_test_contract.py`:

```python
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Utf8TestContract(unittest.TestCase):
    def test_tests_do_not_depend_on_the_windows_default_codepage(self):
        offenders = []
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            if re.search(r"\.read_text\(\)", text):
                offenders.append(path.name)
        self.assertEqual([], offenders)
```

- [ ] **Step 2: Run the two tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_windows_setup tests.test_utf8_test_contract -v`

Expected: FAIL because setup accepts a dead launcher and 93 test reads omit UTF-8.

- [ ] **Step 3: Make the minimum Windows bootstrap repair**

Replace the existence-only virtual-environment check with:

```powershell
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-CommandAvailable $VenvPython)) {
    Write-Host "正在创建或修复 Python 环境 ..." -ForegroundColor Cyan
    & python -m venv --clear .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-CommandAvailable $VenvPython)) {
        throw "Python 环境创建失败；请确认 Python 3.12 已完成安装。"
    }
}
```

Keep the existing BOM and package/build sequence unchanged.

- [ ] **Step 4: Make test reads deterministic**

Mechanically change only zero-argument `.read_text()` calls under `tests/test_*.py` to `.read_text(encoding="utf-8")`. Do not reformat or refactor the tests.

- [ ] **Step 5: Verify the demonstrated failures**

Run: `.venv\Scripts\python.exe -m unittest tests.test_windows_setup tests.test_utf8_test_contract tests.test_multi_store_upload -v`

Expected: all tests pass except the three existing explicitly skipped multi-store cases; no `UnicodeDecodeError`.

Run: `powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -SetupOnly`

Expected: the stale environment is repaired, dependency installation and both frontend builds pass, and no Ozon API is called.

---

### Task 3: Protect and deduplicate the Ozon product-sync mutation

**Files:**
- Modify: `backend/api/ozon_products.py`
- Modify: `backend/repositories/erp.py`
- Create: `tests/test_ozon_product_sync_api.py`
- Modify: `tests/test_ozon_product_sync_job.py`

**Interfaces:**
- Consumes: `request.state.operator` set by the existing LAN middleware and `ErpRepository`.
- Produces: host-only `POST /api/erp/stores/{store_id}/products/sync` and `create_or_get_active_sync_job(job_type, store_id) -> tuple[str, bool]`.

- [ ] **Step 1: Write RED API authorization tests**

In `tests/test_ozon_product_sync_api.py`, mount the existing router in a small FastAPI app, set `request.state.operator` from a test middleware, and assert:

```python
def test_non_host_cannot_start_product_sync(self):
    response = self.client.post("/api/erp/stores/store-1/products/sync")
    self.assertEqual(403, response.status_code)
    self.assertEqual([], self.started_workers)

def test_host_can_start_product_sync_without_credentials_in_response(self):
    response = self.host_client.post("/api/erp/stores/store-1/products/sync")
    self.assertEqual(202, response.status_code)
    self.assertEqual({"job_id", "status", "reused"}, set(response.json()))
    self.assertNotIn("secret", response.text.lower())
```

Patch `_store_credentials` and the client transport; the tests must not read a real `.env` file or call Ozon.

- [ ] **Step 2: Write the RED duplicate-launch test**

Add to `tests/test_ozon_product_sync_job.py`:

```python
def test_concurrent_queue_requests_reuse_one_active_job(self):
    from concurrent.futures import ThreadPoolExecutor

    started = []
    release = threading.Event()

    def worker(job_id, store_id):
        started.append((job_id, store_id))
        release.wait(1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda _: queue_catalog_sync(self.repo, "store-1", worker),
            range(8),
        ))
    release.set()
    self.assertEqual(1, len({item["job_id"] for item in results}))
    self.assertEqual(1, len(started))
```

- [ ] **Step 3: Run the API/job tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_ozon_product_sync_api tests.test_ozon_product_sync_job -v`

Expected: FAIL because the mutation has no host dependency and every call currently creates a new job/thread.

- [ ] **Step 4: Add the host dependency at the mutation boundary**

In `backend/api/ozon_products.py`, use FastAPI's existing `Request` and `Depends` support:

```python
def require_host_device(request: Request) -> None:
    operator = getattr(request.state, "operator", None) or {}
    if not operator.get("is_host_device"):
        raise HTTPException(status_code=403, detail="只有主电脑可以同步店铺商品")


@router.post("/stores/{store_id}/products/sync", status_code=202)
def start_product_sync(store_id: str, _: None = Depends(require_host_device)) -> dict[str, Any]:
    shop, credentials = _store_credentials(store_id)
    repository = ErpRepository(ROOT)
    repository.upsert_store({
        "id": store_id,
        "display_name": shop.get("display_name") or store_id,
        "currency": shop.get("default_currency_code") or "RUB",
        "enabled": True,
        "connection_status": str(shop.get("validation_status") or "NEEDS_VALIDATION").upper(),
        "credential_ref": f"ozon-adapter/.env.{store_id}",
        "last_connected_at": shop.get("last_validated_at"),
        "last_error": shop.get("last_validation_error"),
    })
    client = OzonProductClient(
        credentials["client_id"], credentials["api_key"],
        base_url=os.getenv("OZON_API_BASE_URL"),
    )
    service = OzonCatalogSyncService(repository, client)
    return queue_catalog_sync(repository, store_id, service.run)
```

Do not restrict the existing read-only product and job GET endpoints in V1.1.

- [ ] **Step 5: Make job creation atomic and idempotent**

Add one repository method using `BEGIN IMMEDIATE`:

```python
def create_or_get_active_sync_job(self, job_type: str, store_id: str) -> tuple[str, bool]:
    with database(self.root) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """SELECT id FROM sync_jobs
               WHERE type=? AND store_id=? AND status IN ('QUEUED','RUNNING')
               ORDER BY started_at DESC LIMIT 1""",
            (job_type, store_id),
        ).fetchone()
        if row:
            return str(row["id"]), False
        job_id = uuid.uuid4().hex
        db.execute(
            """INSERT INTO sync_jobs(id,type,store_id,status,stage,started_at,message)
               VALUES(?,?,?,'QUEUED','QUEUED',?,'等待同步')""",
            (job_id, job_type, store_id, now()),
        )
        return job_id, True
```

Update `queue_catalog_sync` to start a daemon thread only when `created is True`, and return `reused=not created`.

- [ ] **Step 6: Run authorization, concurrency, retry, and secret tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_ozon_product_sync_api tests.test_ozon_product_sync_job tests.test_ozon_product_sync_retry tests.test_ozon_product_sync_timeout -v`

Expected: PASS; test transports only; Ozon writes 0, inventory calls 0.

---

### Task 4: Make product-sync results truthful and auditable

**Files:**
- Modify: `backend/repositories/erp.py`
- Modify: `backend/services/ozon_catalog_sync_service.py`
- Modify: `tests/ozon_sync_support.py`
- Modify: `tests/test_ozon_product_partial_sync.py`
- Modify: `tests/test_ozon_product_sync_attributes.py`
- Create: `tests/test_ozon_product_sync_accounting.py`

**Interfaces:**
- Consumes: Ozon list/detail/attribute responses and the existing `sync_job_items`/`audit_logs` tables.
- Produces: `record_sync_job_item(job_id, entity_id, status, error=None)`, accurate aggregate counts, `PARTIAL` on any entity failure, store `last_sync_at`, and a sanitized completion audit event.

- [ ] **Step 1: Write RED accounting tests**

Cover these literal cases in `tests/test_ozon_product_sync_accounting.py`:

```python
def test_malformed_row_is_recorded_and_job_is_partial(self):
    client = CatalogClient({"": ([products(1, 1)[0], {"offer_id": "missing-id"}], "")})
    job_id = self.create_job()
    OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")
    job = self.repo.get_sync_job(job_id)
    self.assertEqual("PARTIAL", job["status"])
    self.assertEqual(2, job["total"])
    self.assertEqual(1, job["success_count"])
    self.assertEqual(1, job["failure_count"])

def test_missing_detail_and_failed_attributes_are_partial(self):
    client = CatalogClient(
        {"": (products(1, 2), "")},
        omitted_detail_ids={2},
        failed_attribute_offers={"offer-1"},
    )
    job_id = self.create_job()
    OzonCatalogSyncService(self.repo, client).run(job_id, "store-1")
    job = self.repo.get_sync_job(job_id)
    self.assertEqual("PARTIAL", job["status"])
    self.assertEqual(0, job["success_count"])
    self.assertEqual(2, job["failure_count"])
    self.assertIsNotNone(self.repo.get_store("store-1")["last_sync_at"])
```

Also assert that `sync_job_items.error` and `audit_logs.details_json` contain neither the fake Client-Id nor Api-Key.

- [ ] **Step 2: Change the existing attribute-failure expectation to RED**

In `tests/test_ozon_product_sync_attributes.py`, replace the false-success assertion:

```python
self.assertEqual("PARTIAL", self.repo.get_sync_job(job_id)["status"])
```

- [ ] **Step 3: Run the accounting/partial tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_ozon_product_partial_sync tests.test_ozon_product_sync_attributes tests.test_ozon_product_sync_accounting -v`

Expected: FAIL because malformed rows are dropped, attribute failures do not affect job status, and no item/audit rows are written.

- [ ] **Step 4: Record each accepted or failed entity with one shared sanitized error path**

Add the smallest repository methods needed to insert `sync_job_items`, update `stores.last_sync_at`, and append `audit_logs`. In the service, maintain `seen_entities` and `failed_entities` sets. Use stable fallback IDs such as `page:2:row:4` only when Ozon provides no product/offer ID.

Final aggregation must be:

```python
total = len(seen_entities)
failure_count = len(failed_entities)
success_count = total - failure_count
status = "PARTIAL" if failure_count else "SUCCEEDED"
```

Validate requested detail IDs and attribute offer IDs against returned rows; an omitted requested entity is a failure. Keep successfully persisted pages when later pages fail.

- [ ] **Step 5: Add an outer worker failure boundary**

Wrap the background worker in `queue_catalog_sync` so an unexpected uncaught exception durably updates the job to `FAILED`, `stage="FAILED"`, sets `finished_at`, and stores `_safe_error(exc)`. Do not catch `BaseException`; catch `Exception` only.

- [ ] **Step 6: Run all product-sync and repository tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_product_repository tests.test_sku_repository tests.test_marketplace_listing_repository tests.test_ozon_product_list_pagination tests.test_ozon_product_partial_sync tests.test_ozon_product_sync_attributes tests.test_ozon_product_sync_job tests.test_ozon_product_sync_retry tests.test_ozon_product_sync_timeout tests.test_ozon_product_sync_without_analytics tests.test_ozon_product_sync_without_orders tests.test_ozon_product_sync_api tests.test_ozon_product_sync_accounting -v`

Expected: PASS; all transports mocked; Ozon writes 0, inventory calls 0.

---

### Task 5: Preserve the batch write gate and make UI status/copy unambiguous

**Files:**
- Modify: `tests/test_batch_upload_confirmation_gate.py`
- Modify: `collector/local-ingest/app.py`
- Modify: `collector/local-ingest/batches_routes.py`
- Modify: `scripts/pipeline_runtime.py`
- Modify: `scripts/run_batch.py`
- Modify: `collector/local-ingest/static/workbench.js`
- Modify: `collector/workbench-command-center/src/services/workbenchApi.ts`
- Modify: `collector/workbench-command-center/src/hooks/useSearchVisibilityPlan.ts`
- Modify: `collector/workbench-command-center/src/components/command-center/OzonOptimizationStage.tsx`

**Interfaces:**
- Consumes: existing `build_batch_upload_review`, `authorize_batch_upload`, `review_hash`, and product-sync job API.
- Produces: unchanged persisted batch compatibility, explicit final confirmation wording, and bounded read-only catalog polling.

- [ ] **Step 1: Add the RED compatibility-gate assertion**

Extend `tests/test_batch_upload_confirmation_gate.py` with one end-to-end contract using the existing fixture builders:

```python
async def test_auto_upload_field_means_prepare_then_confirm_not_write_authorization(self):
    launched = []
    self.assertTrue(self.batch["auto_upload"])
    with self.app_patches(lambda batch, source: launched.append((dict(batch), source)) or {
        "status": "queued", "batch_id": batch["batch_id"], "queue_position": 1,
    }):
        review = workbench.get_workbench_batch_upload_review(self.batch["batch_id"])
        self.assertEqual([], launched)
        self.assertEqual(0, review["write_api_calls"])
        self.assertFalse((self.root / "batches/B-CONFIRM-1/upload-authorization.json").exists())
        await workbench.confirm_workbench_batch_upload(
            self.batch["batch_id"], FakeRequest({"review_hash": review["review_hash"]})
        )
    self.assertEqual(1, len(launched))
```

Use the test module's existing mocked uploader and authorization helpers; no live API.

- [ ] **Step 2: Run the gate test before wording changes**

Run: `.venv\Scripts\python.exe -m unittest tests.test_batch_upload_confirmation_gate -v`

Expected: the new behavior assertion passes against the current gate. If it fails, stop this task and fix only the shared authorization boundary before changing UI copy.

- [ ] **Step 3: Correct misleading comments and labels without renaming persisted fields**

Use these user-facing meanings everywhere touched:

- `自动处理，提交前整批确认`
- `资料与图片已准备完成；确认后才会提交 Ozon`
- `只读同步 Ozon 商品`

Replace comments that say the Run button authorizes CREATE/UPDATE with comments that say it authorizes unattended preparation up to the final whole-batch review. Keep `auto_upload` JSON keys and existing status values unchanged.

- [ ] **Step 4: Bound frontend job polling and surface partial completion**

In `workbenchApi.ts`, use a ten-minute deadline around the existing 750 ms poll:

```typescript
const deadline = Date.now() + 10 * 60_000;
while (Date.now() < deadline) {
  const job = await readJson<OzonProductSyncJob>(`/api/erp/sync-jobs/${encodeURIComponent(queued.job_id)}`);
  onProgress?.(job);
  if (["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"].includes(job.status)) {
    if (job.status === "FAILED") throw new WorkbenchApiError("sync-job", job.error || "商品同步失败");
    return loadOzonCatalogPlan(storeId);
  }
  await new Promise((resolve) => window.setTimeout(resolve, 750));
}
throw new WorkbenchApiError("sync-job", "同步仍在后台运行，请稍后刷新，不会重复创建任务");
```

When status is `PARTIAL`, keep the synchronized products visible and display the backend job message as a warning. Task 5 does not add cancellation or a new task framework; cooperative cancellation is implemented only in Task 9 at the existing thread/SQLite job boundary.

- [ ] **Step 5: Re-run gate and frontend checks**

Run: `.venv\Scripts\python.exe -m unittest tests.test_batch_upload_approval tests.test_batch_upload_confirmation_gate tests.test_multi_store_upload -v`

Expected: PASS except existing explicit skips; zero live writes.

Run in `collector/workbench-command-center`: `npm.cmd run typecheck`

Expected: PASS.

Run in `collector/workbench-command-center`: `npm.cmd run build`

Expected: PASS; rebuilt `dist` contains the revised wording.

---

### Task 6: Establish the strict V1.1A pre-gate baseline

**Files:**
- Create: `.superpowers/sdd/2026-09-22-v1-1-stabilization/progress.md`
- Create: `.superpowers/sdd/2026-09-22-v1-1-stabilization/v1.1a-verification.md`
- Modify: `CURRENT_TASK.md`
- Modify: `PROJECT_HANDOFF.md`

**Interfaces:**
- Consumes: completed Tasks 1-5 and the current protected workspace data.
- Produces: a zero-failure baseline that Tasks 7-11 must preserve.

- [ ] **Step 1: Record exact changed files and observable contracts**

Record schema, API, UI, tests, builds, Ozon read/write counters, inventory counters, and every skip in `v1.1a-verification.md`. Each skip must include the exact test, reason, and whether it existed in the untouched ZIP baseline.

- [ ] **Step 2: Run the focused Task 1-5 suite**

Run the focused command already listed in Tasks 1-5.

Expected: `failures=0`, `errors=0`; only explicit skips with written reasons; live Ozon calls 0, Ozon writes 0, inventory calls 0.

- [ ] **Step 3: Run the complete backend suite with a strict gate**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -p "test*.py"`

Expected: `failures=0`, `errors=0`. A normal FAILED or ERROR blocks V1.1A. A genuinely unavailable environment capability must be encoded as `skip` or `expectedFailure` with a concrete reason and a baseline/final count; it must not remain a failing test.

- [ ] **Step 4: Run frontend and offline checks**

Run in `collector/workbench-command-center`:

```powershell
npm.cmd run typecheck
npm.cmd run build
```

Run in `collector/edge-extension`: `npm.cmd run build`

Run: `.venv\Scripts\python.exe scripts\project_self_check.py`

Expected: both builds PASS; self-check decision PASS; Ozon writes 0; inventory calls 0.

- [ ] **Step 5: Continue to Task 7 without packaging or phase promotion**

Task 6 is a pre-gate baseline, not a release. Do not create the final ZIP and do not mark V1.1A delivered before Task 11.

---

### Task 7: Listing reconciliation with sync snapshots

**Files:**
- Modify: `backend/db/erp.py`
- Modify: `backend/repositories/erp.py`
- Modify: `backend/services/ozon_catalog_sync_service.py`
- Create: `migrations/005_listing_reconciliation.sql`
- Create: `tests/test_listing_reconciliation.py`
- Create: `tests/test_listing_reconciliation_partial_failure.py`

**Interfaces:**
- Consumes: a durable `sync_jobs.id` and one fully successful catalog-sync result.
- Produces: `marketplace_listings.last_seen_sync_id`, `marketplace_listings.presence_status`, and non-destructive `NOT_SEEN` reconciliation.

- [ ] **Step 1: Write the RED successful-reconciliation test**

```python
def test_complete_sync_marks_missing_old_listing_not_seen(self):
    old = self.seed_listing("old-offer")
    current = self.seed_listing("current-offer")
    job_id = self.run_complete_sync([current])
    rows = {row["offer_id"]: row for row in self.repo.list_store_listings("store-1")}
    self.assertEqual("NOT_SEEN", rows[old]["presence_status"])
    self.assertEqual("SEEN", rows[current]["presence_status"])
    self.assertEqual(job_id, rows[current]["last_seen_sync_id"])
    self.assertIsNotNone(rows[old]["id"])
```

- [ ] **Step 2: Write RED FAILED/PARTIAL protection tests**

```python
def test_partial_sync_does_not_reconcile_missing_listing(self):
    old = self.seed_listing("old-offer")
    self.run_partial_sync([])
    row = self.repo.get_listing("store-1", old)
    self.assertNotEqual("NOT_SEEN", row["presence_status"])
```

Repeat for FAILED. Assert the row is never physically deleted.

- [ ] **Step 3: Run both reconciliation tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_listing_reconciliation tests.test_listing_reconciliation_partial_failure -v`

Expected: FAIL because the columns and reconciliation methods do not exist.

- [ ] **Step 4: Add migration 005 and snapshot writes**

Add:

```sql
ALTER TABLE marketplace_listings ADD COLUMN last_seen_sync_id TEXT;
ALTER TABLE marketplace_listings ADD COLUMN presence_status TEXT NOT NULL DEFAULT 'UNKNOWN';
CREATE INDEX IF NOT EXISTS idx_listings_store_presence
ON marketplace_listings(store_id, presence_status);
```

Fresh-schema creation and Python idempotent migration must contain the same fields. During page persistence, mark observed rows `presence_status='SEEN'` and `last_seen_sync_id=job_id`.

- [ ] **Step 5: Reconcile only after final SUCCEEDED status is known**

Use one repository update:

```sql
UPDATE marketplace_listings
SET presence_status='NOT_SEEN', updated_at=?
WHERE store_id=?
  AND (last_seen_sync_id IS NULL OR last_seen_sync_id<>?)
```

Call it only after all PRODUCT_LIST, detail, and attribute work has completed with final status `SUCCEEDED`. FAILED, PARTIAL, INTERRUPTED, and CANCELLED jobs must never call it.

- [ ] **Step 6: Run reconciliation and existing sync tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_listing_reconciliation tests.test_listing_reconciliation_partial_failure tests.test_ozon_product_list_pagination tests.test_ozon_product_partial_sync -v`

Expected: PASS; no row deletion; Ozon writes 0.

---

### Task 8: Persist Ozon attribute responses

**Files:**
- Modify: `backend/db/erp.py`
- Modify: `backend/repositories/erp.py`
- Modify: `backend/services/ozon_catalog_sync_service.py`
- Create: `migrations/006_listing_attributes.sql`
- Create: `tests/test_ozon_attribute_persistence.py`
- Modify: `tests/test_ozon_product_sync_attributes.py`

**Interfaces:**
- Consumes: real-shaped `/v4/product/info/attributes` item dictionaries.
- Produces: `marketplace_listings.attributes_json`, `attributes_synced_at`, and existing `attributes_status`.

- [ ] **Step 1: Write the RED persistence test**

```python
def test_attribute_response_is_persisted_on_the_matching_listing(self):
    response_item = {
        "offer_id": "offer-1",
        "id": 1,
        "attributes": [{"id": 85, "values": [{"value": "Красный"}]}],
    }
    self.run_sync(attribute_items=[response_item])
    listing = self.repo.get_listing("store-1", "offer-1")
    self.assertEqual(response_item, listing["attributes"])
    self.assertEqual("SYNCED", listing["attributes_status"])
    self.assertIsNotNone(listing["attributes_synced_at"])
```

- [ ] **Step 2: Write RED missing/malformed response tests**

Assert that an omitted requested offer or a response without an identifiable offer becomes `attributes_status='FAILED'`, records a sanitized error, and makes the job PARTIAL without discarding successful attribute rows.

- [ ] **Step 3: Run the attribute tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_ozon_attribute_persistence tests.test_ozon_product_sync_attributes -v`

Expected: FAIL because only status is currently stored.

- [ ] **Step 4: Add migration 006 and minimal JSON persistence**

```sql
ALTER TABLE marketplace_listings ADD COLUMN attributes_json TEXT;
ALTER TABLE marketplace_listings ADD COLUMN attributes_synced_at TEXT;
```

Serialize with `json.dumps(item, ensure_ascii=False, sort_keys=True)`. Parse it back to an `attributes` object in repository/API results. Do not create a normalized `listing_attributes` table in V1.1.

- [ ] **Step 5: Persist returned items, then mark only matched offers SYNCED**

Replace the current blanket `mark_attributes_status(..., group, "SYNCED")` with a repository method that matches each returned `offer_id`, stores the full item, sets `attributes_synced_at`, and returns the matched offer IDs. Missing IDs become per-item failures.

- [ ] **Step 6: Run attribute, accounting, and reconciliation tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_ozon_attribute_persistence tests.test_ozon_product_sync_attributes tests.test_ozon_product_sync_accounting tests.test_listing_reconciliation tests.test_listing_reconciliation_partial_failure -v`

Expected: PASS; no normalized attribute table; Ozon writes 0.

---

### Task 9: Recover interrupted jobs and add cooperative cancellation

**Files:**
- Modify: `backend/db/erp.py`
- Modify: `backend/repositories/erp.py`
- Modify: `backend/services/ozon_catalog_sync_service.py`
- Modify: `backend/api/ozon_products.py`
- Modify: `collector/local-ingest/app.py`
- Create: `migrations/007_sync_job_cancellation.sql`
- Create: `tests/test_sync_job_recovery.py`
- Create: `tests/test_sync_job_cancellation.py`

**Interfaces:**
- Consumes: current job status and cooperative checks at page/chunk boundaries.
- Produces: `cancel_requested`, optional `cancel_requested_at`, startup `INTERRUPTED` recovery, and `POST /api/erp/sync-jobs/{job_id}/cancel`.

- [ ] **Step 1: Write RED startup recovery tests**

Seed QUEUED, RUNNING, RETRYING, SUCCEEDED, PARTIAL, and FAILED jobs. Call `interrupt_stale_sync_jobs()` and assert only QUEUED/RUNNING/RETRYING become `INTERRUPTED` with `finished_at` and a restart reason.

- [ ] **Step 2: Write RED cooperative cancellation tests**

```python
def test_cancel_api_only_requests_cancellation(self):
    response = self.host_client.post(f"/api/erp/sync-jobs/{self.job_id}/cancel")
    self.assertEqual(202, response.status_code)
    job = self.repo.get_sync_job(self.job_id)
    self.assertEqual("RUNNING", job["status"])
    self.assertEqual(1, job["cancel_requested"])

def test_worker_cancels_after_current_batch_boundary(self):
    self.client.block_inside_detail_batch()
    self.repo.request_sync_job_cancel(self.job_id)
    self.client.release_detail_batch()
    self.worker.join(2)
    self.assertEqual("CANCELLED", self.repo.get_sync_job(self.job_id)["status"])
```

- [ ] **Step 3: Run recovery/cancellation tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_sync_job_recovery tests.test_sync_job_cancellation -v`

Expected: FAIL because schema, repository, API, and worker checks are absent.

- [ ] **Step 4: Add migration 007**

```sql
ALTER TABLE sync_jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_jobs ADD COLUMN cancel_requested_at TEXT;
```

- [ ] **Step 5: Implement startup recovery and request-only API behavior**

Call `ErpRepository(ROOT).interrupt_stale_sync_jobs()` from the existing FastAPI lifespan before background workers start. The cancel API requires the Task 3 host dependency and may update only `cancel_requested`/`cancel_requested_at`; it must not write `CANCELLED`.

- [ ] **Step 6: Stop workers only at safe boundaries**

Check `cancel_requested` before the first request and after each persisted PRODUCT_LIST page, detail chunk, and attribute chunk. Once the current batch is durably saved, the worker sets `CANCELLED`, `stage='CANCELLED'`, and `finished_at`, then exits without reconciliation.

- [ ] **Step 7: Run recovery, cancellation, duplicate-job, and reconciliation tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_sync_job_recovery tests.test_sync_job_cancellation tests.test_ozon_product_sync_job tests.test_listing_reconciliation_partial_failure -v`

Expected: PASS; direct API cancellation never produces CANCELLED; Ozon writes 0.

---

### Task 10: Introduce CredentialStore and tighten Windows ACLs

**Files:**
- Create: `backend/integrations/ozon/credential_store.py`
- Modify: `scripts/workbench_stores.py`
- Modify: `backend/api/ozon_products.py`
- Create: `tests/test_credential_store.py`
- Create: `tests/test_credential_store_windows_acl.py`
- Modify: `tests/test_store_credentials_security.py` if present; otherwise create it

**Interfaces:**
- Consumes: the existing `ozon-adapter/.env.<store-id>` format.
- Produces: `CredentialStore.read(store)`, `write(store, client_id, api_key)`, `delete(store)`, and owner-only Windows ACL application.

- [ ] **Step 1: Write RED compatibility and secret-boundary tests**

Assert that CredentialStore reads/writes the current environment-key format, returns secrets only to backend callers, and never places secret values in `repr`, exceptions, API responses, logs, or `runtime/erp.sqlite3`.

- [ ] **Step 2: Write the RED Windows ACL test with an injected runner**

```python
def test_windows_write_removes_inheritance_and_grants_current_user_only(self):
    calls = []
    store = CredentialStore(self.root, acl_runner=lambda args: calls.append(args), platform="nt")
    store.write(self.shop, "client-1", "secret-1")
    self.assertEqual("icacls", calls[0][0].lower())
    self.assertIn("/inheritance:r", calls[0])
    self.assertIn("/grant:r", calls[0])
    self.assertNotIn("secret-1", " ".join(calls[0]))
```

- [ ] **Step 3: Run credential tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_credential_store tests.test_credential_store_windows_acl tests.test_store_credentials_security -v`

Expected: FAIL because CredentialStore does not exist.

- [ ] **Step 4: Implement one minimal file-backed CredentialStore**

Keep `.env.<store>` byte compatibility for the legacy uploader. Validate store IDs against path traversal. Write atomically. On POSIX use `chmod(0o600)`; on Windows call `icacls` after the atomic replace to remove inheritance and grant the current account full access. Command arguments may contain only the file path and account name, never secret values.

- [ ] **Step 5: Route backend credential reads/writes through CredentialStore**

Keep `scripts/workbench_stores.py` public functions as compatibility wrappers, but delegate their secret-file work to CredentialStore. Update `_store_credentials` to use the same store. Do not change secret filenames or uploader inputs.

- [ ] **Step 6: Run credential, store, API, and project self-check tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_credential_store tests.test_credential_store_windows_acl tests.test_store_credentials_security tests.test_ozon_product_sync_api tests.test_workbench_gap_fill -v`

Run: `.venv\Scripts\python.exe scripts\project_self_check.py`

Expected: PASS; no secret in database/API/log/exception/frontend; Ozon writes 0.

DPAPI and Windows Credential Manager remain deferred.

---

### Task 11: Extract OzonBaseClient and verify seller identity with OzonAccountClient

**Files:**
- Create: `backend/integrations/ozon/base_client.py`
- Create: `backend/integrations/ozon/account_client.py`
- Modify: `backend/integrations/ozon/ozon_endpoints.py`
- Modify: `backend/integrations/ozon/product_client.py`
- Create: `backend/services/store_service.py`
- Create: `tests/test_ozon_base_client.py`
- Create: `tests/test_ozon_account_client.py`
- Create: `tests/test_store_real_validation.py`

**Interfaces:**
- Produces: shared retry/timeout/sanitized-error/logging behavior; `OzonAccountClient.get_seller_info()`; `StoreService.validate_credentials()` requiring both seller identity and product-read permission.
- Consumes: CredentialStore from Task 10 and `OzonProductClient.list_products(limit=1)`.

- [ ] **Step 1: Verify the current official Seller API contract before coding**

Use the current official Ozon Seller API documentation/OpenAPI, not a category-tree response or a third-party summary. Record the documentation URL, access date, operation ID, request method, response fields used, and the product-list permission probe in `v1.1a-verification.md`. The expected identity operation is `POST /v1/seller/info`; if the official contract differs at execution time, ledger the exact endpoint ruling before implementation.

- [ ] **Step 2: Write RED BaseClient tests**

Cover timeout, network failure, HTTP 429/5xx bounded retry, non-retryable 4xx, `Retry-After`, invalid JSON, credential scrubbing, bounded error length, and credential-free `repr`/logs.

- [ ] **Step 3: Write RED AccountClient and store validation tests**

```python
def test_store_validation_requires_seller_identity_and_product_read(self):
    account = FakeAccountClient(company={"id": "seller-1", "name": "ООО Тест"})
    products = FakeProductClient(read_allowed=True)
    result = StoreService(self.repo, self.credentials, account, products).validate_credentials("store-1")
    self.assertEqual("seller-1", result["seller_id"])
    self.assertTrue(result["product_read_allowed"])

def test_category_tree_success_cannot_mark_store_connected(self):
    with self.assertRaises(ProductReadPermissionError):
        StoreService(self.repo, self.credentials, FakeAccountClient.valid(), FakeProductClient(read_allowed=False)).validate_credentials("store-1")
```

- [ ] **Step 4: Run BaseClient/AccountClient/store tests and verify RED**

Run: `.venv\Scripts\python.exe -m unittest tests.test_ozon_base_client tests.test_ozon_account_client tests.test_store_real_validation -v`

Expected: FAIL because BaseClient, AccountClient, and StoreService do not exist.

- [ ] **Step 5: Move shared transport behavior into OzonBaseClient**

Move the existing ProductClient HTTPS, timeout, retry, sanitation, and logging code without changing product request payloads. `OzonProductClient` subclasses or composes OzonBaseClient; do not retain a second retry loop.

- [ ] **Step 6: Implement AccountClient and real validation**

Add the verified seller-info endpoint constant. Normalize only documented seller ID/company fields. Store validation succeeds only when seller identity succeeds and a one-item PRODUCT_LIST read succeeds. Do not call category-tree for identity and do not expose credentials.

- [ ] **Step 7: Run all integration/store/product client tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_ozon_base_client tests.test_ozon_account_client tests.test_store_real_validation tests.test_ozon_product_sync_retry tests.test_ozon_product_sync_timeout tests.test_ozon_product_list_pagination -v`

Expected: PASS; mocked transports only; Ozon writes 0.

---

## STOP — V1.1A DELIVERY GATE

Do not enter Task 12 without explicit user approval after reviewing the V1.1A Delivery Report.

**Required outputs:**
- Create: `docs/reports/2026-09-22-v1.1a-delivery-report.md`
- Update: `.superpowers/sdd/2026-09-22-v1-1-stabilization/progress.md`
- Update: `.superpowers/sdd/2026-09-22-v1-1-stabilization/v1.1a-verification.md`

**Mandatory gate:**

1. `.venv\Scripts\python.exe -m unittest discover -s tests -p "test*.py"` reports `failures=0`, `errors=0`.
2. Frontend `npm.cmd run typecheck` PASS.
3. Frontend `npm.cmd run build` PASS.
4. Edge extension build PASS.
5. `project_self_check.py` PASS.
6. Every skip lists an exact reason and baseline/final count. Ordinary FAILED/ERROR results are never accepted as historical debt.
7. Ozon CREATE/UPDATE calls 0 and inventory calls 0 during V1.1A verification.
8. The Delivery Report lists changed files, migrations 005-007, API/UI changes, test/build evidence, skip diff, known issues, security checks, and zero-write counters.

After producing the report, STOP and wait for approval.

---

## V1.1B ERP Read Models

> Superseded for V1.1B planning by `docs/superpowers/plans/2026-09-25-v1.1b-unified-review.md`. Tasks 12–16 below are preserved there and extended with the approved unified marketplace and Review Center scope. V1.1A Tasks 1–11 above are unchanged.

### Task 12: Organization groundwork

**Files:**
- Modify: `backend/db/erp.py`
- Modify: `backend/repositories/erp.py`
- Create: `migrations/008_default_organization.sql`
- Create: `tests/test_organization_groundwork.py`

**Interfaces:**
- Produces: `organizations` and non-null `stores.organization_id` with a seeded Default Organization.

- [ ] Write RED tests proving existing and new stores always reference the Default Organization.
- [ ] Run `.venv\Scripts\python.exe -m unittest tests.test_organization_groundwork -v` and verify RED.
- [ ] Add `organizations(id, name, created_at, updated_at)`, seed `id='default'`, add `stores.organization_id TEXT NOT NULL DEFAULT 'default'`, and backfill every store.
- [ ] Update repository store writes to supply `organization_id='default'` when omitted and reject explicit null.
- [ ] Run organization and store repository tests; expect PASS and zero null organizations.

Do not add users, JWT, sessions, memberships, or RBAC.

---

### Task 13: Store Center V2

**Files:**
- Modify: `backend/services/store_service.py`
- Create: `backend/api/stores.py`
- Modify: `collector/local-ingest/app.py`
- Create: `collector/workbench-command-center/src/components/erp/StoreCenter.tsx`
- Modify: `collector/workbench-command-center/src/services/workbenchApi.ts`
- Modify: `collector/workbench-command-center/src/types/workbench.ts`
- Modify: `collector/workbench-command-center/src/App.tsx`
- Create: `tests/test_store_center_api.py`

**Interfaces:**
- Consumes: Task 11 `OzonAccountClient`, Task 10 CredentialStore, store/product/job repositories.
- Produces: real Store Center data for Seller ID, company, connection status, product-read permission, last connected, last product sync, and product count.

- [ ] Write RED API tests for every displayed field and a recursive assertion that Client-Id/Api-Key values never appear.
- [ ] Run `tests.test_store_center_api` and verify RED.
- [ ] Inject `OzonAccountClient` into StoreService; do not instantiate transport clients inside UI routes.
- [ ] Implement real `/api/erp/stores` read and validation endpoints backed by SQLite and CredentialStore references only.
- [ ] Implement the first-level Store Center page with business labels and no raw secrets/JSON.
- [ ] Run API tests, frontend typecheck, and frontend build; expect PASS.

---

### Task 14: Product Center V1

**Files:**
- Modify: `backend/api/ozon_products.py`
- Modify: `backend/repositories/erp.py`
- Create: `collector/workbench-command-center/src/components/erp/ProductCenter.tsx`
- Modify: `collector/workbench-command-center/src/services/workbenchApi.ts`
- Modify: `collector/workbench-command-center/src/types/workbench.ts`
- Modify: `collector/workbench-command-center/src/App.tsx`
- Create: `tests/test_product_center_api.py`
- Create: `collector/workbench-command-center/scripts/verify-product-center.mjs`

**Interfaces:**
- Produces: real `/api/erp/products` and `/api/erp/products/{product_id}` plus a first-level Product Center page.

- [ ] Write RED API tests asserting image, Product ID, SKU, Offer ID, Ozon Product ID, title, store, price, listing status, presence status, attribute status, and last sync.
- [ ] Write a RED production-data contract proving the page has no mock/fallback product array.
- [ ] Implement repository joins and global product endpoints without exposing filesystem paths or secrets.
- [ ] Implement Product Center using only `/api/erp/products`; loading, empty, error, PARTIAL, NOT_SEEN, and attribute states must be visible.
- [ ] Run API tests, `npm.cmd run typecheck`, `npm.cmd run build`, and `node scripts/verify-product-center.mjs`; expect PASS.

---

### Task 15: Legacy product index

**Files:**
- Create: `backend/services/legacy_product_index_service.py`
- Create: `scripts/index_legacy_products.py`
- Create: `tests/test_legacy_product_index.py`
- Create: `tests/test_legacy_product_index_idempotency.py`

**Interfaces:**
- Consumes: existing `products/<product_id>/input` and `output` artifacts read-only.
- Produces: distinct Product, SKU, and Listing records in ERP SQLite.

- [ ] Write a RED fixture test that hashes every source artifact before and after indexing and asserts identical hashes.
- [ ] Write a RED idempotency test that runs indexing twice and asserts identical Product/SKU/Listing counts and IDs.
- [ ] Implement only the current artifact formats named in AGENTS.md; unknown/legacy-incompatible products are reported and skipped, never rewritten.
- [ ] Keep Product, SKU, and Marketplace Listing as separate records and preserve store/offer uniqueness.
- [ ] Run both index tests and the Product Center API tests; expect PASS and zero source writes.

---

### Task 16: V1.1B final verification and delivery

**Files:**
- Create: `docs/reports/2026-09-22-v1.1b-delivery-report.md`
- Create: `.superpowers/sdd/2026-09-22-v1-1-stabilization/v1.1b-verification.md`
- Modify: `CURRENT_TASK.md`
- Modify: `PROJECT_HANDOFF.md`

**Interfaces:**
- Consumes: approved V1.1A and completed Tasks 12-15.
- Produces: final source ZIP plus V1.1A/V1.1B Delivery Reports.

- [ ] Run the complete backend suite; require `failures=0`, `errors=0`.
- [ ] Run frontend typecheck/build and Edge build; require PASS.
- [ ] Run `project_self_check.py`; require PASS.
- [ ] List every skip with exact reason and V1.1A/V1.1B baseline/final diff.
- [ ] Request explicit user approval before the one real Ozon store read-only synchronization. Without approval, do not call Ozon and keep this required check pending.
- [ ] After approval, run only Seller Identity, one-item Product Read permission, and read-only product synchronization; verify no CREATE/UPDATE/inventory/warehouse/activation call.
- [ ] Verify Store Center and Product Center against the synchronized read model and save screenshots.
- [ ] Produce a credential-free complete source ZIP excluding `.env*`, `runtime/`, `products/`, `batches/`, `logs/`, `.venv/`, `node_modules/`, `__pycache__/`, `*.pyc`, task IDs, and publication receipts.
- [ ] Extract the ZIP into a fresh directory and rerun full tests, builds, and self-check; require the same zero-failure results.
- [ ] Record ZIP entry count and SHA-256 in the V1.1B Delivery Report.
- [ ] Output both `docs/reports/2026-09-22-v1.1a-delivery-report.md` and `docs/reports/2026-09-22-v1.1b-delivery-report.md`, then STOP.

## STOP — V1.1B FINAL GATE

Do not enter V2. Delivery requires:

1. complete source ZIP;
2. V1.1A Delivery Report;
3. V1.1B Delivery Report;
4. backend `failures=0`, `errors=0` in both the working copy and fresh ZIP extraction;
5. all builds and project self-check PASS;
6. real Ozon verification limited to user-approved read-only calls;
7. Ozon CREATE/UPDATE count 0 and inventory/warehouse/activation call count 0.

## Explicitly Deferred

- Normalized `listing_attributes` tables; V1.1 stores the bounded raw attribute response JSON on the listing.
- DPAPI and Windows Credential Manager; V1.1 retains `.env.<store>` with restricted ACLs.
- Users, JWT, sessions, memberships, RBAC, and cross-organization authorization.
- PostgreSQL, SQLAlchemy, Alembic, Supabase, and production SaaS deployment.
- Inventory, price-write, order, finance, warehouse, fulfilment, archive/delete, and activation operations.
- Background queue infrastructure beyond the existing cooperative thread/SQLite job boundary.
- Kaspi, Wildberries, Tauri, billing, email, object storage, and AI-provider abstraction.
