import { chromium } from "@playwright/test";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

const baseUrl = process.env.COMMAND_CENTER_URL || "http://127.0.0.1:5178/";
const localOrigin = new URL(baseUrl).origin;
const isLocalReviewRequest = (url) => url.origin === localOrigin
  && (url.pathname === "/api/erp/reviews" || url.pathname.startsWith("/api/erp/reviews/"));
const draftCollection = /^\/api\/erp\/reviews\/[^/]+\/drafts$/;
const draftItem = /^\/api\/erp\/reviews\/[^/]+\/drafts\/[^/]+$/;
function isLocalDraftWrite(method, address) {
  const url = new URL(address);
  return url.origin === localOrigin && ((method === "POST" && draftCollection.test(url.pathname))
    || (method === "PATCH" && draftItem.test(url.pathname)));
}
if (isLocalDraftWrite("POST", "https://outside.example/api/erp/reviews/review-a/drafts")
  || isLocalDraftWrite("POST", `${localOrigin}/api/erp/reviews/review-a/send`)
  || isLocalDraftWrite("PATCH", `${localOrigin}/api/erp/reviews/review-a/drafts`)
  || isLocalReviewRequest(new URL("https://outside.example/api/erp/reviews/review-a/drafts"))
  || isLocalReviewRequest(new URL(`${localOrigin}/api/erp/reviews-spoof`))) {
  throw new Error("Draft write guard accepted a remote or non-draft endpoint");
}
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
].find(existsSync);
const browser = await chromium.launch({ headless: true, executablePath });
const review = {
  id: "review-a", organization_id: "default", platform: "OZON", store_id: "store-a",
  external_id: "remote-a", product_id: "product-a", listing_id: 1, mapping_status: "MAPPED",
  text: "Arrived damaged", rating: 2, reply_status: "UNREPLIED",
  published_at: "2026-09-25T09:00:00Z", created_at: "2026-09-25T09:00:00Z", updated_at: "2026-09-25T09:00:00Z",
};
const reviewB = { ...review, id: "review-b", store_id: "store-b", product_id: "product-b",
  external_id: "remote-b", text: "Second review" };
const media = [
  { id: 1, media_type: "IMAGE", source_url: "http://127.0.0.1:8999/private.jpg", external_id: null },
  { id: 2, media_type: "IMAGE", source_url: "https://cdn.example.com/public.jpg", external_id: null },
  { id: 3, media_type: "IMAGE", source_url: "https://192.168.1.5/private.jpg", external_id: null },
  { id: 4, media_type: "IMAGE", source_url: "https://localhost/private.jpg", external_id: null },
  { id: 5, media_type: "IMAGE", source_url: "https://localhost./private.jpg", external_id: null },
  { id: 6, media_type: "IMAGE", source_url: "https://foo.internal./private.jpg", external_id: null },
  { id: 7, media_type: "IMAGE", source_url: "https://localhost../private.jpg", external_id: null },
];
const errors = [];

async function verify(name, viewport) {
  const page = await browser.newPage({ viewport });
  const writes = [];
  const privateRequests = [];
  let empty = false;
  let failed = false;
  let drafts = [];
  const draftB = { id: "draft-b", review_id: "review-b", organization_id: "default", platform: "OZON",
    store_id: "store-b", content: "B editing text", source: "MANUAL", status: "DRAFT",
    created_at: "2026-09-29T00:00:00Z", updated_at: "2026-09-29T00:00:00Z" };
  let detailGate = null;
  let saveGate = null;
  function holdNext(kind) {
    let start, release;
    const started = new Promise((resolve) => { start = resolve; });
    const released = new Promise((resolve) => { release = resolve; });
    if (kind === "detail") detailGate = { start, released };
    else saveGate = { start, released };
    return { started, release: () => release() };
  }
  page.on("pageerror", (error) => errors.push(`${name}: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().includes("status of 500")) errors.push(`${name}: ${message.text()}`);
  });
  page.on("request", (request) => {
    if (request.method() !== "GET") writes.push({ method: request.method(), url: request.url() });
    if (/127\.0\.0\.1:8999|192\.168\.1\.5|localhost\.*\/private|foo\.internal\.*\/private/.test(request.url())) privateRequests.push(request.url());
  });
  await page.route("https://cdn.example.com/**", (route) => route.fulfill({
    status: 200, contentType: "image/svg+xml",
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><rect width="96" height="96" fill="#334155"/></svg>',
  }));
  await page.route(isLocalReviewRequest, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (failed && request.method() === "GET" && path === "/api/erp/reviews") {
      await route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"failed"}' });
      return;
    }
    if (path === "/api/erp/reviews") {
      const items = empty ? [] : [review, reviewB];
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items, total: items.length, page: 1, page_size: 50 }) });
      return;
    }
    if (path === "/api/erp/reviews/review-a") {
      if (detailGate) {
        const gate = detailGate;
        detailGate = null;
        gate.start();
        await gate.released;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...review, media }) });
      return;
    }
    if (path === "/api/erp/reviews/review-b") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...reviewB, media: [] }) });
      return;
    }
    if (path === "/api/erp/reviews/review-b/drafts") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [draftB] }) });
      return;
    }
    if (path === "/api/erp/reviews/review-a/drafts") {
      if (request.method() === "POST") {
        if (saveGate) {
          const gate = saveGate;
          saveGate = null;
          gate.start();
          await gate.released;
        }
        const body = request.postDataJSON();
        if (body.store_id !== "store-a") throw new Error("Wrong draft Store scope");
        const draft = { id: "draft-a", review_id: review.id, organization_id: "default", platform: "OZON",
          store_id: "store-a", content: body.content.trim(), source: body.source, status: "DRAFT",
          created_at: "2026-09-29T00:00:00Z", updated_at: "2026-09-29T00:00:00Z" };
        drafts = [draft];
        await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(draft) });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: drafts }) });
      return;
    }
    if (path === "/api/erp/reviews/review-a/drafts/draft-a" && request.method() === "PATCH") {
      drafts = [{ ...drafts[0], ...request.postDataJSON(), updated_at: "2026-09-29T00:01:00Z" }];
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(drafts[0]) });
      return;
    }
    throw new Error(`Unexpected Review API request: ${request.method()} ${path}`);
  });
  await page.goto(`${baseUrl}#reviews`, { waitUntil: "domcontentloaded" });
  if (!(await page.title())) throw new Error(`${name}: missing title`);
  if (!page.url().endsWith("#reviews")) throw new Error(`${name}: wrong URL`);
  await page.getByRole("heading", { name: "评价中心" }).waitFor();
  await page.getByText("Arrived damaged").waitFor();
  await page.getByLabel("平台").first().selectOption("OZON");
  await page.getByLabel("店铺 ID").fill("store-a");
  await page.getByLabel("商品 ID").fill("product-a");
  await page.getByLabel("评分").selectOption("2");
  await page.getByLabel("平台回复状态").selectOption("UNREPLIED");
  await page.getByLabel("开始日期").fill("2026-09-25");
  await page.getByLabel("结束日期").fill("2026-09-25");
  await Promise.all([
    page.waitForRequest((request) => request.url().includes("/api/erp/reviews?")
      && request.url().includes("product_id=product-a") && request.url().includes("rating=2")
      && request.url().includes("published_to=2026-09-25T23%3A59%3A59.999999Z")),
    page.getByRole("button", { name: "筛选评价" }).click(),
  ]);
  await page.getByRole("button", { name: "查看评价详情" }).first().click();
  await page.getByRole("region", { name: "评价详情" }).waitFor();
  await page.getByRole("img", { name: "评价图片 2" }).waitFor();
  if (privateRequests.length) throw new Error(`${name}: private media was requested`);
  await page.getByLabel("回复草稿内容").fill("We can help you locally");
  await page.getByRole("button", { name: "保存本地草稿" }).click();
  await page.getByText("We can help you locally").waitFor();
  await page.getByText("平台未回复").first().waitFor();
  await page.getByRole("button", { name: "编辑草稿" }).click();
  await page.getByLabel("回复草稿内容").fill("Updated local reply");
  await page.getByRole("button", { name: "更新本地草稿" }).click();
  await page.getByText("Updated local reply").waitFor();
  await page.getByRole("button", { name: "丢弃草稿" }).click();
  await page.getByText("已丢弃").waitFor();

  await page.getByRole("button", { name: "关闭" }).click();
  const slowA = holdNext("detail");
  await page.getByRole("button", { name: "查看评价详情" }).first().click();
  await slowA.started;
  await page.getByRole("button", { name: "查看评价详情" }).nth(1).click();
  await page.getByRole("region", { name: "评价详情" }).getByText("Second review").waitFor();
  const lateA = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/erp/reviews/review-a");
  slowA.release();
  await lateA;
  await page.waitForTimeout(100);
  if (!(await page.getByRole("region", { name: "评价详情" }).getByText("Second review").count()))
    throw new Error(`${name}: slow A detail overwrote B`);

  const filteredA = holdNext("detail");
  await page.getByRole("button", { name: "查看评价详情" }).first().click();
  await filteredA.started;
  await page.getByRole("button", { name: "筛选评价" }).click();
  await page.getByRole("button", { name: "查看评价详情" }).first().waitFor();
  const lateFilteredA = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/erp/reviews/review-a");
  filteredA.release();
  await lateFilteredA;
  await page.waitForTimeout(100);
  if (await page.getByRole("region", { name: "评价详情" }).count())
    throw new Error(`${name}: slow A detail repopulated after filter`);

  await page.getByRole("button", { name: "查看评价详情" }).first().click();
  await page.getByRole("region", { name: "评价详情" }).getByText("Arrived damaged").waitFor();
  await page.getByLabel("回复草稿内容").fill("Pending A draft");
  const slowSave = holdNext("save");
  await page.getByRole("button", { name: "保存本地草稿" }).click();
  await slowSave.started;
  await page.getByRole("button", { name: "查看评价详情" }).nth(1).click();
  await page.getByRole("region", { name: "评价详情" }).getByText("Second review").waitFor();
  await page.getByLabel("回复草稿内容").fill("B editing text");
  const lateSave = page.waitForResponse((response) => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/erp/reviews/review-a/drafts");
  slowSave.release();
  await lateSave;
  await page.waitForTimeout(50);
  if (await page.getByLabel("回复草稿内容").inputValue() !== "B editing text"
    || await page.getByRole("region", { name: "评价详情" }).getByText("Pending A draft").count())
    throw new Error(`${name}: late A save changed B draft editor or list`);

  if (writes.some((entry) => !isLocalDraftWrite(entry.method, entry.url))) throw new Error(`${name}: draft workflow wrote outside local Review API: ${JSON.stringify(writes)}`);
  if (!writes.some((entry) => entry.method === "POST") || !writes.some((entry) => entry.method === "PATCH"))
    throw new Error(`${name}: draft create/update were not exercised`);
  await page.screenshot({ path: resolve(tmpdir(), `jlc-review-center-${name}.png`), fullPage: true });
  empty = true;
  await page.getByRole("button", { name: "筛选评价" }).click();
  await page.getByText("暂无符合条件的评价").waitFor();
  failed = true;
  await page.getByRole("button", { name: "筛选评价" }).click();
  await page.getByRole("alert").waitFor();
  if (await page.getByText("Arrived damaged").count()) throw new Error(`${name}: stale review survived failed list`);
  await page.close();
}

try {
  await verify("desktop", { width: 1440, height: 900 });
  await verify("mobile", { width: 390, height: 844 });
} finally {
  await browser.close();
}
if (errors.length) throw new Error(errors.join("\n"));
console.log(`Review Center UI verified: ${baseUrl}`);
console.log(`Screenshots: ${resolve(tmpdir(), "jlc-review-center-desktop.png")}, ${resolve(tmpdir(), "jlc-review-center-mobile.png")}`);
