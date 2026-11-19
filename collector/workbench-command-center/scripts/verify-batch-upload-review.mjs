import { chromium } from "@playwright/test";
import { createReadStream, existsSync, mkdirSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join, normalize, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const dist = join(root, "dist");
const artifactDir = process.env.BATCH_REVIEW_ARTIFACT_DIR;
if (artifactDir) mkdirSync(artifactDir, { recursive: true });
if (!existsSync(join(dist, "index.html"))) {
  throw new Error("Build the command center before running this verification.");
}

const mime = {
  ".css": "text/css",
  ".html": "text/html",
  ".js": "text/javascript",
  ".svg": "image/svg+xml",
};

const server = createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url || "/", "http://localhost").pathname);
  if (pathname === "/review-image.svg") {
    response.writeHead(200, { "Content-Type": "image/svg+xml" });
    response.end('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="#14532d"/><circle cx="60" cy="60" r="32" fill="#6ee7b7"/></svg>');
    return;
  }
  let file = normalize(join(dist, pathname === "/" ? "index.html" : pathname));
  if (!file.startsWith(dist) || !existsSync(file) || statSync(file).isDirectory()) file = join(dist, "index.html");
  response.writeHead(200, { "Content-Type": mime[extname(file)] || "application/octet-stream" });
  createReadStream(file).pipe(response);
});
await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
const address = server.address();
const baseUrl = `http://127.0.0.1:${address.port}/?task_center=all`;

const hash = (letter) => letter.repeat(64);
const reviewProduct = (productId, overrides = {}) => ({
  product_id: productId,
  collection_id: "COL-100",
  source_title_cn: "户外折叠收纳架",
  title_ru: "Складная полка для хранения",
  category_id: 17028922,
  category_name_zh: "户外收纳架",
  category_name_ru: "Стеллажи для хранения",
  category_path: ["Дом", "Хранение", "Стеллажи для хранения"],
  skus: [
    { sku_id: `${productId}-BLACK`, offer_id: `${productId}-OFFER`, option_text: "Черный", selling_price_rub: 1890 },
    { sku_id: `${productId}-WHITE`, offer_id: `${productId}-OFFER-2`, option_text: "Белый", selling_price_rub: 1990 },
  ],
  sku_count: 2,
  price_range: { min: 1890, max: 1990 },
  image_urls: ["/review-image.svg"],
  attribute_coverage: { total: 12, filled: 11, missing: 1, missing_attribute_ids: [991], unknown_count: 1, estimated_count: 1 },
  unknown_count: 1,
  estimated_count: 1,
  image_qc_status: "passed",
  target_store_ids: ["STORE-RU-1", "STORE-RU-2"],
  risks: [],
  ...overrides,
});
const readyReview = (batchId, letter = "a") => ({
  schema_version: "1.0.0",
  batch_id: batchId,
  status: "AWAITING_BATCH_UPLOAD_CONFIRMATION",
  can_confirm: true,
  review_hash: hash(letter),
  product_count: 1,
  sku_count: 2,
  store_count: 2,
  expected_submission_count: 2,
  blocker_count: 0,
  warning_count: 1,
  target_store_ids: ["STORE-RU-1", "STORE-RU-2"],
  products: [reviewProduct(`P-${batchId.slice(2)}`, {
    risks: [{ level: "warning", code: "unknown_attribute", product_id: `P-${batchId.slice(2)}`, message: "存在未知属性字段", return_focus: "attributes" }],
  })],
  risks: [{ level: "warning", code: "unknown_attribute", product_id: `P-${batchId.slice(2)}`, message: "存在未知属性字段", return_focus: "attributes" }],
  write_api_calls: 0,
  inventory_api_calls: 0,
});
const blockedRisk = { level: "blocker", code: "missing_target_store", product_id: "P-BLOCKED", message: "缺少目标店铺", return_focus: "stores" };
const focusedRisks = [
  { level: "blocker", code: "missing_sku_snapshot", product_id: "P-BLOCKED", message: "缺少SKU运行快照", return_focus: "skus" },
  { level: "blocker", code: "missing_final_copy", product_id: "P-BLOCKED", message: "缺少最终俄文文案", return_focus: "copy" },
  { level: "blocker", code: "missing_final_attributes", product_id: "P-BLOCKED", message: "缺少最终Ozon属性", return_focus: "attributes" },
  { level: "blocker", code: "missing_ozon_draft", product_id: "P-BLOCKED", message: "缺少最终Ozon草稿", return_focus: "draft" },
  { level: "blocker", code: "missing_final_price", product_id: "P-BLOCKED", message: "SKU缺少最终价格", return_focus: "pricing" },
  { level: "blocker", code: "image_qc_failed", product_id: "P-BLOCKED", message: "图片技术检查未通过", return_focus: "images" },
  { level: "blocker", code: "collection_mismatch", product_id: "P-BLOCKED", message: "商品归属或采集箱不匹配", return_focus: "source" },
];
const reviews = {
  "B-READY": readyReview("B-READY", "a"),
  "B-CONFIRM": readyReview("B-CONFIRM", "c"),
  "B-SLOW": readyReview("B-SLOW", "d"),
  "B-BLOCKED": {
    ...readyReview("B-BLOCKED", "b"),
    status: "BLOCKED_BEFORE_UPLOAD",
    can_confirm: false,
    blocker_count: 1 + focusedRisks.length,
    warning_count: 0,
    products: [reviewProduct("P-BLOCKED", { target_store_ids: [], risks: [blockedRisk, ...focusedRisks] })],
    risks: [blockedRisk, ...focusedRisks],
  },
};
const batchCards = Object.values(reviews).map((review) => ({
  batch_id: review.batch_id,
  status: review.status,
  product_count: 1,
  sku_count: 2,
  progress: 100,
  target_store_ids: review.target_store_ids,
  products: review.products.map((product) => ({ product_id: product.product_id, selected_sku_count: 2, status: "READY_FOR_BATCH_REVIEW", current_step: "ozon_upload" })),
}));
const productCards = Object.values(reviews).map((review) => review.products[0]).map((product) => ({
  product_id: product.product_id,
  title_cn: product.source_title_cn,
  title_ru: product.title_ru,
  state: "等待整批确认",
  workflow_bucket: "待处理",
  raw_status: "READY_FOR_BATCH_REVIEW",
  current_step: "ozon_upload",
  progress: 100,
  sku_count: product.sku_count,
  image_count: product.image_urls.length,
  selected_store_count: product.target_store_ids.length,
  thumbnail_url: "/review-image.svg",
}));

let confirmCount = 0;
let conflictNext = false;
let slowReviewRequests = 0;
const requestCounts = new Map();
const postBodies = [];

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function intercept(route) {
  const request = route.request();
  const url = new URL(request.url());
  const path = url.pathname;
  requestCounts.set(path, (requestCounts.get(path) || 0) + 1);
  if (!path.startsWith("/api/")) {
    if (url.hostname === "127.0.0.1") return route.continue();
    return route.abort("blockedbyclient");
  }
  if (path === "/api/workbench/batches") return json(route, { items: batchCards, running_pid: null, queued_count: 0 });
  if (path === "/api/workbench/products") return json(route, { items: productCards, total: productCards.length });
  if (path === "/api/workbench/system-status") return json(route, { state: "idle", label: "空闲", message: "本地测试", codex_ready: true, active_worker_count: 0, batch_running: false, image_slot_concurrency: 1, automatic_retry_limit: 1 });
  if (path === "/api/workbench/settings") return json(route, { auto_mode_enabled: false, can_manage_settings: false });
  if (path === "/api/workbench/shops") return json(route, { items: [] });
  if (path === "/api/workbench/ozon-reference-tasks") return json(route, { items: [] });
  if (path === "/api/workbench/risks") return json(route, { items: [] });
  if (path === "/api/workbench/logs") return json(route, { items: [] });
  if (path === "/api/workbench/market-intelligence/search-visibility/latest") return json(route, { available: false, actions: [], summary: {}, notice: "本地测试" });
  if (path === "/api/workbench/market-intelligence/traffic-performance/latest") return json(route, { available: false, actions: [], summary: {}, notice: "本地测试" });
  const detailMatch = path.match(/^\/api\/workbench\/products\/(P-[A-Z-]+)$/);
  if (detailMatch) {
    const card = productCards.find((item) => item.product_id === detailMatch[1]);
    return json(route, {
      product_id: detailMatch[1], raw_status: "READY_FOR_BATCH_REVIEW", current_step: "ozon_upload",
      source: { title_cn: card?.title_cn }, content: { title_ru: card?.title_ru }, status: { status: "READY_FOR_BATCH_REVIEW", progress: 100 },
      skus: [], stores: [], publications: { stores: {} }, images: [], attributes: [], suggestions: [],
    });
  }
  const reviewMatch = path.match(/^\/api\/workbench\/batches\/(B-[A-Z-]+)\/upload-review$/);
  if (reviewMatch) {
    if (reviewMatch[1] === "B-SLOW") {
      slowReviewRequests += 1;
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 450));
    }
    return json(route, reviews[reviewMatch[1]]);
  }
  const confirmMatch = path.match(/^\/api\/workbench\/batches\/(B-[A-Z-]+)\/confirm-upload$/);
  if (confirmMatch && request.method() === "POST") {
    confirmCount += 1;
    postBodies.push(request.postDataJSON());
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 180));
    if (conflictNext) {
      conflictNext = false;
      reviews[confirmMatch[1]] = { ...reviews[confirmMatch[1]], review_hash: hash("e"), products: [{ ...reviews[confirmMatch[1]].products[0], title_ru: "Авторитетная версия после обновления" }] };
      return json(route, { detail: "review hash is stale" }, 409);
    }
    return json(route, { status: "queued", batch_id: confirmMatch[1], authorization: { review_hash: request.postDataJSON().review_hash }, replayed: false, write_api_calls: 0, inventory_api_calls: 0 });
  }
  return json(route, { detail: `Unhandled intercepted API: ${path}` }, 404);
}

function batchRow(page, batchId) {
  return page.locator(".task-center-item.batch", { hasText: batchId });
}

async function openReview(page, batchId, actionText) {
  if (!await page.getByRole("heading", { name: "任务进度中心" }).isVisible()) {
    await page.getByRole("button", { name: "任务中心" }).click();
  }
  await batchRow(page, batchId).getByRole("button", { name: "查看批次" }).click();
  await page.getByRole("dialog").getByRole("button", { name: actionText }).click();
  await page.getByRole("heading", { name: "整批上传审阅" }).waitFor();
  return page.getByRole("dialog");
}

async function returnToFocusedSection(page, dialog, riskMessage, sectionHeading) {
  await dialog.locator(".batch-review-risk", { hasText: riskMessage }).getByRole("button", { name: "返回工作台修改" }).click();
  const drawer = page.locator(".detail-drawer");
  await drawer.waitFor();
  const activeSection = drawer.locator(".active-section", { hasText: sectionHeading });
  await activeSection.getByRole("heading", { name: sectionHeading }).waitFor();
  const position = await activeSection.evaluate(async (section) => {
    const viewport = section.closest("[data-radix-scroll-area-viewport]");
    if (!viewport) return { positioned: false, reason: "missing viewport" };
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const sectionBox = section.getBoundingClientRect();
      const viewportBox = viewport.getBoundingClientRect();
      if (sectionBox.top >= viewportBox.top - 2 && sectionBox.top < viewportBox.bottom) return { positioned: true };
      await new Promise((resolveDelay) => setTimeout(resolveDelay, 50));
    }
    const sectionBox = section.getBoundingClientRect();
    const viewportBox = viewport.getBoundingClientRect();
    return { positioned: false, sectionTop: sectionBox.top, viewportTop: viewportBox.top, viewportBottom: viewportBox.bottom, scrollTop: viewport.scrollTop };
  });
  if (!position.positioned) throw new Error(`${sectionHeading} was active but not scrolled into view: ${JSON.stringify(position)}`);
}

let browser;
try {
  browser = await chromium.launch({ headless: true });
} catch (error) {
  const edge = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
  if (!existsSync(edge)) throw error;
  browser = await chromium.launch({ headless: true, executablePath: edge });
}
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 980 } });
  const page = await context.newPage();
  await page.route("**/*", intercept);
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator(".command-center").waitFor();

  let dialog = await openReview(page, "B-READY", "审阅整批上传");
  await dialog.getByText("Складная полка для хранения").waitFor();
  await dialog.getByText("17028922").waitFor();
  await dialog.getByText("户外收纳架").waitFor();
  await dialog.getByText("Дом / Хранение / Стеллажи для хранения").waitFor();
  await dialog.getByText("1890–1990 ₽").waitFor();
  await dialog.getByText("11 / 12").waitFor();
  await dialog.getByText("图片质检：通过").waitFor();
  await dialog.getByText("STORE-RU-1、STORE-RU-2", { exact: true }).waitFor();
  if (await dialog.locator("input, textarea, select, [contenteditable]").count()) throw new Error("Review dialog contains editing controls.");
  if (artifactDir) await page.screenshot({ path: join(artifactDir, "batch-review-ready-desktop.png"), fullPage: false });
  await returnToFocusedSection(page, dialog, "存在未知属性字段", "类目属性概览");
  if (!await page.locator(".detail-drawer").getByText("P-READY", { exact: false }).first().isVisible()) throw new Error("Return action did not open the requested product.");
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "任务中心" }).click();
  await batchRow(page, "B-SLOW").getByRole("button", { name: "查看批次" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "审阅整批上传" }).click();
  await page.keyboard.press("Escape");
  dialog = await openReview(page, "B-READY", "审阅整批上传");
  await page.waitForTimeout(650);
  if (!await dialog.getByText("B-READY", { exact: true }).isVisible()) throw new Error("A stale review response overwrote the current batch.");
  if (slowReviewRequests !== 1) throw new Error("Stale-response fixture was not exercised.");
  await page.keyboard.press("Escape");

  dialog = await openReview(page, "B-BLOCKED", "查看阻断原因");
  await dialog.getByText("缺少目标店铺").waitFor();
  if (!await dialog.getByRole("button", { name: "确认整批上传" }).isDisabled()) throw new Error("Blocked batch confirmation must be disabled.");
  if (artifactDir) await page.screenshot({ path: join(artifactDir, "batch-review-blocked-desktop.png"), fullPage: false });
  await returnToFocusedSection(page, dialog, "缺少目标店铺", "类目与店铺");
  await page.keyboard.press("Escape");

  for (const [riskMessage, sectionHeading] of [
    ["缺少SKU运行快照", "SKU 商品卡"],
    ["缺少最终俄文文案", "俄文商品资料"],
    ["缺少最终Ozon属性", "类目属性概览"],
    ["缺少最终Ozon草稿", "价格与利润"],
    ["SKU缺少最终价格", "价格与利润"],
    ["图片技术检查未通过", "图片资产区"],
    ["商品归属或采集箱不匹配", "商品卡概览"],
  ]) {
    dialog = await openReview(page, "B-BLOCKED", "查看阻断原因");
    await returnToFocusedSection(page, dialog, riskMessage, sectionHeading);
    await page.keyboard.press("Escape");
  }

  dialog = await openReview(page, "B-CONFIRM", "审阅整批上传");
  conflictNext = true;
  const preConflictGets = requestCounts.get("/api/workbench/batches/B-CONFIRM/upload-review") || 0;
  await dialog.getByRole("button", { name: "确认整批上传" }).click();
  await dialog.getByText(/资料已变化|无法确认|重新读取/).waitFor();
  await dialog.getByText("Авторитетная версия после обновления").waitFor();
  if (!await dialog.isVisible()) throw new Error("409 closed the review dialog.");
  if ((requestCounts.get("/api/workbench/batches/B-CONFIRM/upload-review") || 0) <= preConflictGets) throw new Error("409 did not reload the authoritative review.");

  const beforeRefresh = {
    batches: requestCounts.get("/api/workbench/batches") || 0,
    products: requestCounts.get("/api/workbench/products") || 0,
    system: requestCounts.get("/api/workbench/system-status") || 0,
  };
  const confirmButton = dialog.getByRole("button", { name: "确认整批上传" });
  await confirmButton.evaluate((button) => { button.click(); button.click(); });
  await page.getByRole("button", { name: "正在确认整批" }).waitFor();
  if (!await page.getByRole("button", { name: "正在确认整批" }).isDisabled()) throw new Error("Confirmation was not disabled while submitting.");
  await page.keyboard.press("Escape");
  if (!await dialog.isVisible()) throw new Error("The review closed while confirmation was still pending.");
  await page.getByText(/整批上传已确认/).waitFor();
  if (confirmCount !== 2) throw new Error(`Expected one conflict and one successful POST; received ${confirmCount}.`);
  if (JSON.stringify(postBodies) !== JSON.stringify([{ review_hash: hash("c") }, { review_hash: hash("e") }])) {
    throw new Error(`Unexpected confirmation bodies: ${JSON.stringify(postBodies)}`);
  }
  for (const [key, count] of Object.entries(beforeRefresh)) {
    if ((requestCounts.get(`/api/workbench/${key === "system" ? "system-status" : key}`) || 0) <= count) throw new Error(`Successful confirmation did not refresh ${key}.`);
  }
  dialog = await openReview(page, "B-READY", "审阅整批上传");
  await dialog.getByText("Складная полка для хранения").waitFor();
  if (await dialog.getByRole("button", { name: /确认整批上传|正在确认整批/ }).isDisabled()) throw new Error("A completed submission left the next batch stuck in submitting state.");
  await page.keyboard.press("Escape");
  await context.close();

  const narrow = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const narrowPage = await narrow.newPage();
  await narrowPage.route("**/*", intercept);
  await narrowPage.goto(baseUrl, { waitUntil: "domcontentloaded" });
  const narrowDialog = await openReview(narrowPage, "B-BLOCKED", "查看阻断原因");
  const box = await narrowDialog.boundingBox();
  if (!box || box.x < 0 || box.x + box.width > 390 || box.y < 0 || box.y + box.height > 844) throw new Error(`Narrow review is outside viewport: ${JSON.stringify(box)}`);
  if (!await narrowDialog.getByRole("button", { name: "返回工作台修改" }).first().isVisible()) throw new Error("Narrow return action is not visible.");
  if (await narrowDialog.evaluate((element) => element.scrollWidth > element.clientWidth)) throw new Error("Narrow review has horizontal overflow.");
  if (artifactDir) await narrowPage.screenshot({ path: join(artifactDir, "batch-review-blocked-narrow.png"), fullPage: false });
  await narrow.close();
} finally {
  await browser.close();
  await new Promise((resolveClose) => server.close(resolveClose));
}

console.log("Batch upload review verified: ready, blocked, stale race, 409 reload, exact submit, return focus, and narrow layout.");
