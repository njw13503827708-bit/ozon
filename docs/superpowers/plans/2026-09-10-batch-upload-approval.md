# Ozon Batch Upload Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a post-generation, read-only, whole-batch approval gate so no Ozon product write occurs until every product passes pre-upload validation and the operator confirms the batch once.

**Architecture:** A focused `batch_upload_approval` module builds the authoritative review snapshot, classifies blockers and warnings, hashes protected upload inputs, and persists an idempotent authorization record. The existing batch runner stops products after final field/image checks, derives the batch review state, and resumes `ozon_upload` only when the stored authorization hash still matches. FastAPI exposes read and confirm endpoints; the React command center displays a read-only approval dialog.

**Tech Stack:** Python 3.12, FastAPI, JSON files, existing SQLite publication state, React 19, TypeScript 5, Vite 6, Python `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-10-batch-upload-approval-design.md`

## Global Constraints

- The final confirmation applies to the entire batch once; it is never a per-product approval.
- Any failed, incomplete, or hard-blocked product disables approval for the whole batch.
- The final approval view is read-only; edits happen in the existing product workbench.
- No Ozon CREATE, UPDATE, read-back, inventory, warehouse, or activation call may run in development, tests, or offline acceptance.
- Production Ozon writes require a current batch authorization whose hash matches the exact protected upload inputs.
- Never submit inventory fields or call inventory endpoints.
- Preserve `input/source.json`, original images, existing task IDs, payload history, and SQLite publication records.
- Successful, pending, or task-ID-bearing store publications must not be submitted again.
- Every new behavior follows red-green-refactor: write the failing test, run it, implement the minimum, then rerun.

---

### Task 1: Authoritative review snapshot and hash

**Files:**
- Create: `scripts/batch_upload_approval.py`
- Create: `tests/test_batch_upload_approval.py`

**Interfaces:**
- Produces: `build_batch_upload_review(root: Path, batch: dict) -> dict`
- Produces: `compute_batch_review_hash(root: Path, batch: dict) -> str`
- Produces: `authorization_path(root: Path, batch_id: str) -> Path`
- Produces: constants `READY_FOR_BATCH_REVIEW`, `BLOCKED_BEFORE_UPLOAD`, `AWAITING_BATCH_UPLOAD_CONFIRMATION`, and `UPLOAD_AUTHORIZED`
- Review response fields: `schema_version`, `batch_id`, `status`, `can_confirm`, `review_hash`, `product_count`, `sku_count`, `store_count`, `expected_submission_count`, `blocker_count`, `warning_count`, `target_store_ids`, `products`, `risks`, `write_api_calls`, and `inventory_api_calls`

- [ ] **Step 1: Write the failing tests for a ready batch and stable hash**

Create fixtures in `tests/test_batch_upload_approval.py` that build two temporary products with `status.json`, `input/source.json`, `output/sku-run-snapshot.json`, `output/copy-ru.json`, `output/ozon-attributes-final.json`, `output/ozon-upload-config.json`, `output/ozon-draft.json`, `output/image-qc-report.json`, and generated image files. Assert:

```python
review = build_batch_upload_review(root, batch)
self.assertTrue(review["can_confirm"])
self.assertEqual(review["status"], AWAITING_BATCH_UPLOAD_CONFIRMATION)
self.assertEqual(review["product_count"], 2)
self.assertEqual(review["sku_count"], 2)
self.assertEqual(review["blocker_count"], 0)
self.assertEqual(review["write_api_calls"], 0)
self.assertEqual(review["inventory_api_calls"], 0)
self.assertEqual(review["review_hash"], compute_batch_review_hash(root, batch))
self.assertEqual(review["review_hash"], build_batch_upload_review(root, batch)["review_hash"])
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_batch_upload_approval -v
```

Expected: import failure for `scripts.batch_upload_approval` because the module does not exist.

- [ ] **Step 3: Implement deterministic review construction**

In `scripts/batch_upload_approval.py`, use only standard-library JSON, hashing, and `Path` helpers. Canonicalize protected values with sorted keys and compact separators:

```python
def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

The protected hash document must contain the batch ID, ordered product IDs, collection IDs, selected stores, SKU snapshots, final copy, final attributes, upload configs, drafts, and the relative path plus SHA-256 of each referenced generated/accepted image. Never include timestamps or absolute paths in the hash.

The review builder must read final artifacts, not display summaries. Product rows expose final Russian title, category, SKU/offer information, price range, image URLs, attribute coverage, unknown/estimated counts, image QC status, stores, and per-product risks.

- [ ] **Step 4: Add failing blocker classification tests**

Add separate tests that mutate one product at a time and assert `can_confirm` is false for:

```python
cases = (
    ("NEEDS_ATTENTION", "product_not_ready"),
    ("READY_FOR_BATCH_REVIEW", "missing_final_attributes"),
    ("READY_FOR_BATCH_REVIEW", "image_qc_failed"),
    ("READY_FOR_BATCH_REVIEW", "missing_target_store"),
    ("READY_FOR_BATCH_REVIEW", "inventory_field_forbidden"),
)
```

Also assert a non-required estimated field becomes a `warning`, not a blocker.

- [ ] **Step 5: Run the new tests and verify the expected failures**

Run the Task 1 test command. Expected: ready-case tests pass; blocker tests fail because eligibility checks are not implemented.

- [ ] **Step 6: Implement eligibility and risk classification**

Add small single-purpose validators returning risk dictionaries shaped as:

```python
{
    "level": "blocker",
    "code": "image_qc_failed",
    "product_id": "P000101",
    "message": "图片技术检查未通过",
    "return_focus": "images",
}
```

Reject missing final artifacts, failed image QC, non-ready status, missing stores/SKUs/offers, forbidden inventory keys, mismatched product/collection ownership, and unsafe publication state. Treat explicit low-risk estimates and informational repair reports as warnings. Return zero API-call counters unconditionally.

- [ ] **Step 7: Run Task 1 tests and verify GREEN**

Run the focused test command. Expected: all Task 1 tests pass without network access.

- [ ] **Step 8: Commit Task 1**

```powers
git add scripts/batch_upload_approval.py tests/test_batch_upload_approval.py
git commit -m "feat: add batch upload review snapshot"
```

If the extracted ZIP still has no `.git`, record the intended commit message in the execution notes and continue without initializing a replacement repository.

### Task 2: Stop the pipeline at the whole-batch gate

**Files:**
- Modify: `scripts/run_batch.py`
- Modify: `scripts/pipeline_runtime.py`
- Modify: `tests/test_batch_pipeline.py`
- Modify: `tests/test_production_input_boundaries.py`

**Interfaces:**
- Consumes: `build_batch_upload_review(root, batch) -> dict`
- Produces: `mark_batch_review_ready(product_dir: Path, status: dict) -> dict`
- Produces: `batch_upload_is_authorized(root: Path, batch: dict) -> bool`
- Preserves: `PIPELINE_STEPS`, with `ozon_upload` still the final step

- [ ] **Step 1: Write a failing test that prevents pre-confirmation upload**

In `tests/test_batch_pipeline.py`, build a two-product automatic batch whose products have completed `image_qc`. Patch the upload command boundary and run the scheduler/finalizer. Assert:

```python
self.assertEqual(first_status["status"], "READY_FOR_BATCH_REVIEW")
self.assertEqual(second_status["status"], "READY_FOR_BATCH_REVIEW")
self.assertEqual(first_status["next_action"], "ozon_upload")
self.assertEqual(report["status"], "AWAITING_BATCH_UPLOAD_CONFIRMATION")
self.assertEqual(report["submitted_count"], 0)
upload_command.assert_not_called()
```

- [ ] **Step 2: Run the focused test and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_batch_pipeline.BatchPipelineTest.test_automatic_batch_stops_before_upload_until_whole_batch_confirmation -v
```

Expected: upload is scheduled immediately or the new status is missing.

- [ ] **Step 3: Implement the product gate**

Replace the current rule that treats a workbench `auto_upload` batch as continuously authorized. When the next action is `ozon_upload` and no valid batch authorization exists, persist:

```python
status.update({
    "status": "READY_FOR_BATCH_REVIEW",
    "current_step": "batch_upload_review",
    "next_action": "ozon_upload",
    "active_step": None,
    "task_authorized": False,
})
```

Keep `ozon_upload` in `pending_steps`, remove it from `completed_steps`, retain selected stores, and do not clear valid generated artifacts.

- [ ] **Step 4: Implement batch aggregation states**

Update `sync_batch` and `finalize_batch` so that:

```python
if failed_or_incomplete_preupload:
    batch_status = "BLOCKED_BEFORE_UPLOAD"
elif all_ready_for_review and not authorization_valid:
    batch_status = "AWAITING_BATCH_UPLOAD_CONFIRMATION"
elif authorization_valid:
    batch_status = "UPLOAD_AUTHORIZED"
```

Do not report a ready-but-unconfirmed product as success. Keep `submitted_count == 0` before authorization.

- [ ] **Step 5: Add failing tests for mixed and restarted batches**

Add tests proving:

- one ready plus one failed product yields `BLOCKED_BEFORE_UPLOAD`;
- no product in that batch executes `ozon_upload`;
- a restarted runner recognizes `READY_FOR_BATCH_REVIEW` as parked, not terminal and not schedulable;
- a matching persisted authorization makes the products schedulable for `ozon_upload`;
- a stale authorization hash parks them again.

- [ ] **Step 6: Run the focused state-machine tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_batch_pipeline tests.test_production_input_boundaries -v
```

Expected: the newly added restart and stale-hash cases fail.

- [ ] **Step 7: Implement authorization-aware resume**

Load the batch authorization through `batch_upload_is_authorized`. Only the matching hash changes parked product statuses to processing with `task_authorized=True`. A stale or missing authorization must never fall back to the previous `auto_upload=True` behavior.

- [ ] **Step 8: Run Task 2 tests and verify GREEN**

Run the Task 2 test command. Expected: all tests pass and patched upload calls remain zero before approval.

- [ ] **Step 9: Commit Task 2**

```bash
git add scripts/run_batch.py scripts/pipeline_runtime.py tests/test_batch_pipeline.py tests/test_production_input_boundaries.py
git commit -m "feat: pause production batches before Ozon upload"
```

### Task 3: Persist idempotent batch authorization and expose API endpoints

**Files:**
- Modify: `scripts/batch_upload_approval.py`
- Modify: `collector/local-ingest/batches_routes.py`
- Create: `tests/test_batch_upload_confirmation_gate.py`

**Interfaces:**
- Consumes: `build_batch_upload_review(root, batch) -> dict`
- Produces: `authorize_batch_upload(root: Path, batch: dict, supplied_hash: str, operator_id: str) -> dict`
- Produces: `GET /api/workbench/batches/{batch_id}/upload-review`
- Produces: `POST /api/workbench/batches/{batch_id}/confirm-upload`
- Confirmation request: `{ "review_hash": "<64 lowercase hex characters>" }`

- [ ] **Step 1: Write failing API contract tests**

Create `tests/test_batch_upload_confirmation_gate.py` using the existing `FakeRequest` and temporary-root patching pattern. Assert the GET endpoint returns a read-only review with zero API-call counters. Assert the POST endpoint:

```python
result = await workbench.confirm_workbench_batch_upload(
    batch_id,
    FakeRequest({"review_hash": review["review_hash"]}),
)
self.assertEqual(result["status"], "queued")
self.assertEqual(result["authorization"]["review_hash"], review["review_hash"])
self.assertEqual(result["write_api_calls"], 0)
self.assertEqual(result["inventory_api_calls"], 0)
launch.assert_called_once()
```

- [ ] **Step 2: Run the API tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_batch_upload_confirmation_gate -v
```

Expected: the endpoint functions do not exist.

- [ ] **Step 3: Implement atomic authorization persistence**

Write authorization to `batches/<batch_id>/upload-authorization.json` using the project's atomic JSON helper. Store:

```python
{
    "schema_version": "1.0.0",
    "batch_id": batch_id,
    "review_hash": current_review_hash,
    "authorized_at": now_iso,
    "authorized_by": operator_id,
    "product_ids": ordered_product_ids,
    "target_store_ids": target_store_ids,
    "status": "authorized",
}
```

Under `BATCH_QUEUE_LOCK`, rebuild the review, require `can_confirm`, compare the supplied hash with `hmac.compare_digest`, write the authorization, update the batch to `UPLOAD_AUTHORIZED`, and enqueue it. Return HTTP 409 for blocked or stale reviews, HTTP 422 for malformed hashes, and HTTP 404 for unknown/unowned batches.

- [ ] **Step 4: Add failing idempotency and stale-hash tests**

Assert:

- repeating the same confirmation returns the stored authorization and does not call `launch_or_enqueue_batch` twice;
- changing `ozon-draft.json` after GET makes POST return HTTP 409;
- changing an image byte after GET makes POST return HTTP 409;
- one failed product makes POST return HTTP 409 and does not create the authorization file;
- an authorization from another batch cannot be reused.

- [ ] **Step 5: Run the API tests and verify RED for new cases**

Run the Task 3 test command. Expected: new idempotency or stale-input cases fail until replay handling is complete.

- [ ] **Step 6: Implement replay-safe enqueue and audit logging**

Use the persisted authorization as the idempotency key. If it already matches and the batch is queued/running/submitted, return the current state without adding another queue item. Append a batch/product audit event containing IDs and hashes but no credentials.

- [ ] **Step 7: Run Task 3 tests and verify GREEN**

Run the Task 3 test command. Expected: all API, stale-hash, and replay tests pass.

- [ ] **Step 8: Commit Task 3**

```bash
git add scripts/batch_upload_approval.py collector/local-ingest/batches_routes.py tests/test_batch_upload_confirmation_gate.py
git commit -m "feat: add whole-batch upload authorization API"
```

### Task 4: Add the read-only React approval experience

**Files:**
- Create: `collector/workbench-command-center/src/components/production/BatchUploadReviewDialog.tsx`
- Modify: `collector/workbench-command-center/src/types/workbench.ts`
- Modify: `collector/workbench-command-center/src/services/workbenchApi.ts`
- Modify: `collector/workbench-command-center/src/components/production/BatchStatusPanel.tsx`
- Modify: `collector/workbench-command-center/src/components/production/BatchDetailDialog.tsx`
- Modify: `collector/workbench-command-center/src/App.tsx`
- Modify: `collector/workbench-command-center/src/styles.css`
- Create: `collector/workbench-command-center/scripts/verify-batch-upload-review.mjs`

**Interfaces:**
- Consumes: `GET .../upload-review` and `POST .../confirm-upload`
- Produces TypeScript types: `BatchUploadRisk`, `BatchUploadReviewProduct`, `BatchUploadReviewResponse`, and `BatchUploadConfirmResponse`
- Produces API helpers: `loadBatchUploadReview(batchId: string)` and `confirmBatchUpload(batchId: string, reviewHash: string)`

- [ ] **Step 1: Write a failing Playwright behavior test for the read-only UI**

In `verify-batch-upload-review.mjs`, serve the built command center locally, intercept the workbench API calls with complete fixture responses, open a batch in `AWAITING_BATCH_UPLOAD_CONFIRMATION`, and assert visible behavior:

```javascript
await expect(page.getByRole("button", { name: "确认整批上传" })).toBeEnabled();
await expect(page.getByRole("button", { name: "返回工作台修改" })).toBeVisible();
await expect(page.locator("[data-batch-upload-review] input")).toHaveCount(0);
await expect(page.locator("[data-batch-upload-review] textarea")).toHaveCount(0);
await expect(page.locator("[data-batch-upload-review] select")).toHaveCount(0);
```

Use a second intercepted review response with one blocker and assert the confirm button is disabled. This exercises the real built React component rather than checking source text.

- [ ] **Step 2: Run the UI contract test and verify RED**

```powershell
pnpm.cmd --dir collector/workbench-command-center run build
node.exe collector/workbench-command-center/scripts/verify-batch-upload-review.mjs
```

Expected: the Playwright script fails because the upload-review action and dialog do not exist.

- [ ] **Step 3: Add response types and API functions**

Define risk levels as the closed union `"blocker" | "warning" | "info"`. Keep all displayed fields optional except `batch_id`, `status`, `can_confirm`, `review_hash`, `products`, and `risks`. Add POST handling that forwards only `review_hash`.

- [ ] **Step 4: Implement the read-only dialog**

The dialog must render:

- batch/product/SKU/store/submission counts;
- grouped blocker, warning, and info risks;
- product cards with final Russian title, category, SKU prices, attribute coverage, images, QC status, and stores;
- a `返回工作台修改` button that closes the dialog and opens/focuses the selected product in the existing drawer;
- a `确认整批上传` button disabled when `!data.can_confirm`, during submission, or when `blocker_count > 0`.

Do not render any input, textarea, select, content-editable field, or local edit state.

- [ ] **Step 5: Wire state and actions in `App.tsx`**

Add separate state for the upload review so the historical `BatchConfirmationDialog` remains unchanged. On successful confirm, close the review dialog, show a success command result, and refresh batches/products/system status. On HTTP 409, keep the dialog open, show that data changed, and reload the review.

- [ ] **Step 6: Expose review actions from batch panels**

Show `审阅整批上传` for `AWAITING_BATCH_UPLOAD_CONFIRMATION`. Show `查看阻断原因` for `BLOCKED_BEFORE_UPLOAD`. Both open the new dialog; historical `AWAITING_CONFIRMATION` continues opening `BatchConfirmationDialog`.

- [ ] **Step 7: Run UI contract and TypeScript build checks**

```powershell
pnpm.cmd --dir collector/workbench-command-center install --frozen-lockfile
pnpm.cmd --dir collector/workbench-command-center run build
node.exe collector/workbench-command-center/scripts/verify-batch-upload-review.mjs
```

Expected: the TypeScript/Vite build succeeds and both ready and blocked Playwright scenarios pass.

- [ ] **Step 8: Commit Task 4**

```bash
git add collector/workbench-command-center/src collector/workbench-command-center/scripts/verify-batch-upload-review.mjs
git commit -m "feat: add read-only batch upload approval dialog"
```

### Task 5: Invalidate authorization on edits and preserve safe retry behavior

**Files:**
- Modify: `scripts/batch_upload_approval.py`
- Modify: `collector/local-ingest/app.py`
- Modify: `collector/local-ingest/collector_routes.py`
- Modify: `collector/local-ingest/batches_routes.py`
- Modify: `tests/test_batch_upload_confirmation_gate.py`
- Modify: `tests/test_multi_store_upload.py`
- Modify: `tests/test_lan_collaboration.py`

**Interfaces:**
- Produces: `invalidate_batch_upload_authorization(root: Path, batch_id: str, reason: str) -> bool`
- Consumes: existing product edit, image replacement/regeneration, category change, store selection, and failed-store retry routes

- [ ] **Step 1: Write failing invalidation tests**

For each protected mutation route, create a valid authorization, perform the mutation, and assert the authorization is no longer accepted. Cover at minimum:

- product draft save;
- SKU override save;
- category change;
- image replace/delete/regenerate;
- target-store change.

Assert the original source and images remain untouched.

- [ ] **Step 2: Run focused invalidation tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_batch_upload_confirmation_gate tests.test_lan_collaboration -v
```

Expected: at least one mutation leaves the old authorization valid.

- [ ] **Step 3: Implement centralized invalidation**

Do not delete audit evidence. Change the authorization record to:

```python
{
    **authorization,
    "status": "invalidated",
    "invalidated_at": now_iso,
    "invalidated_reason": reason,
}
```

Call the helper after each successful protected mutation. Move affected products back through existing targeted regeneration/invalidation logic; do not force unrelated steps to rerun.

- [ ] **Step 4: Add failing multi-store retry tests**

Assert that after batch approval:

- one store failure does not prevent other stores from submitting;
- retry includes only explicit failed stores;
- stores with success, pending, or a non-empty `task_id` are excluded;
- retry does not require a second whole-batch approval when it uses the same authorized payload hash;
- any payload-changing repair requires a new whole-batch review.

- [ ] **Step 5: Run retry tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_multi_store_upload tests.test_lan_collaboration -v
```

Expected: new payload-changing retry distinction fails before implementation.

- [ ] **Step 6: Implement hash-aware retry rules**

Retain the current store publication authority. Permit same-hash transport retries for failed stores only. Require a fresh authorization whenever title, attributes, images, price, SKU set, offer ID, or target stores change.

- [ ] **Step 7: Run Task 5 tests and verify GREEN**

Run both Task 5 commands. Expected: all invalidation and retry tests pass with zero inventory calls.

- [ ] **Step 8: Commit Task 5**

```bash
git add scripts/batch_upload_approval.py collector/local-ingest/app.py collector/local-ingest/collector_routes.py collector/local-ingest/batches_routes.py tests/test_batch_upload_confirmation_gate.py tests/test_multi_store_upload.py tests/test_lan_collaboration.py
git commit -m "fix: invalidate stale batch upload approvals"
```

### Task 6: Windows setup, offline regression, browser verification, and deliverable

**Files:**
- Modify: `scripts/project_self_check.py`
- Modify: `README.md`
- Modify: `WINDOWS.md`
- Modify: `tests/test_project_self_check_contracts.py`
- Modify: `tests/test_windows_setup.py`
- Create: `docs/BATCH_UPLOAD_APPROVAL.md`
- Create after verification: `outputs/ozon-auto-listing-windows.zip`

**Interfaces:**
- Self-check produces checks named `batch_upload_gate` and `batch_authorization_integrity`
- Operator documentation describes the exact generate → review → confirm → submit flow

- [ ] **Step 1: Write failing self-check tests**

Add fixtures proving self-check fails when:

- a pre-approval product has `api_write_count > 0`;
- a batch claims `UPLOAD_AUTHORIZED` without an authorization file;
- authorization hash differs from the current review hash;
- the final draft contains inventory keys.

Assert the report never displays credentials.

- [ ] **Step 2: Run self-check tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_project_self_check_contracts tests.test_windows_setup -v
```

Expected: new gate and integrity checks are absent.

- [ ] **Step 3: Extend the offline project self-check**

Use `build_batch_upload_review` without network calls. Report counts for waiting, blocked, authorized, and stale-authorized batches. Fail on any pre-approval write, inventory payload, missing authorization, or stale authorization.

- [ ] **Step 4: Document Windows operation**

Update README and Windows instructions with:

```text
运行批次 → 等待全部商品生成与质检 → 审阅整批上传 →
若阻断则返回工作台修改 → 重新生成 → 确认整批上传 → 查看多店提交结果
```

State explicitly that the confirmation page is read-only, one failure blocks the whole batch, and store credentials are entered locally and excluded from exports.

- [ ] **Step 5: Install project dependencies and build artifacts**

Use the existing setup script when normal system Python/Node are available:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1 -SetupOnly -NoBrowser
```

If the Codex terminal cannot see newly installed commands, rerun with the bundled Python and pnpm paths documented by the workspace dependency loader; do not alter user-wide PATH as a workaround.

- [ ] **Step 6: Run focused safety regression**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_batch_upload_approval tests.test_batch_upload_confirmation_gate tests.test_batch_pipeline tests.test_multi_store_upload tests.test_lan_collaboration -v
```

Expected: all pass; test doubles report Ozon writes 0 and inventory calls 0.

- [ ] **Step 7: Run full Python regression**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test*.py"
```

Expected: zero failures and zero errors. Record pass and skip counts.

- [ ] **Step 8: Build both frontend artifacts**

```powershell
pnpm.cmd --dir collector/workbench-command-center install --frozen-lockfile
pnpm.cmd --dir collector/workbench-command-center run build
pnpm.cmd --dir collector/edge-extension install --no-frozen-lockfile
pnpm.cmd --dir collector/edge-extension run build
```

Expected: both builds exit 0. Re-run `scripts/project_self_check.py`; the existing extension source-mirror check must pass after the build.

- [ ] **Step 9: Run the offline project self-check**

```powershell
.\.venv\Scripts\python.exe scripts/project_self_check.py --full-tests
```

Expected: `decision` is `PASS`, `ozon_write_api_calls` is 0, and `inventory_api_calls` is 0.

- [ ] **Step 10: Start the workbench and verify in a browser**

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir collector\local-ingest --host 127.0.0.1 --port 8765
```

Verify a temporary offline fixture batch at desktop and narrow widths:

- ready batch shows the read-only review and enabled approval button;
- blocked batch shows risks and a disabled approval button;
- no edit controls appear;
- return-to-workbench focuses the relevant product;
- confirming with a stubbed uploader queues once;
- network log contains no Ozon or inventory request.

- [ ] **Step 11: Package the verified deliverable**

Exclude `.venv`, `node_modules`, credentials, logs containing local secrets, runtime PIDs, caches, and temporary test products. Include built frontend `dist`, source, tests, documentation, and an example shop configuration. Create:

```text
C:\Users\PC\Documents\Codex\2026-09-10\https-github-com-apache-ozone-git\outputs\ozon-auto-listing-windows.zip
```

- [ ] **Step 12: Commit documentation and verification support**

```bash
git add scripts/project_self_check.py README.md WINDOWS.md docs/BATCH_UPLOAD_APPROVAL.md tests/test_project_self_check_contracts.py tests/test_windows_setup.py
git commit -m "docs: document batch upload approval workflow"
```

If Git metadata remains unavailable, record all intended commits and changed files in `C:\Users\PC\Documents\Codex\2026-09-10\https-github-com-apache-ozone-git\outputs\VERIFICATION.md` instead of creating a new unrelated repository.
