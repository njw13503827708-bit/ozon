import { chromium } from "@playwright/test";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";

const baseUrl = process.env.COMMAND_CENTER_URL || "http://127.0.0.1:5178/";
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || [
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  ].find((path) => existsSync(path));
const browser = await chromium.launch({ headless: true, executablePath });
const errors = [];

const item = {
  listing_id: 1,
  product_id: "PRODUCT-A",
  sku_id: "SKU-A",
  sku: "SELLER-SKU-A",
  offer_id: "OFFER-A",
  ozon_product_id: "9001",
  title: "Remote mug",
  store_id: "store-a",
  store: "Ozon 莫斯科",
  image_url: "https://cdn.example.test/mug.jpg",
  price: 1499.5,
  currency: "RUB",
  listing_status: "PARTIAL",
  presence_status: "NOT_SEEN",
  attribute_status: "FAILED",
  last_sync: "2026-09-27T08:00:00+08:00",
};
const secondItem = {
  ...item,
  listing_id: 2,
  product_id: "PRODUCT-B",
  sku_id: "SKU-B",
  sku: "SELLER-SKU-B",
  offer_id: "OFFER-B",
  ozon_product_id: "9002",
  title: "Second product",
};

async function mockStores(page) {
  await page.route("**/api/erp/stores**", (route) => route.fulfill({ status: 200,
    contentType: "application/json", body: JSON.stringify({items: [{id: "store-a", display_name: "Ozon 莫斯科"}], total: 1}) }));
}

async function verify(name, viewport) {
  const page = await browser.newPage({ viewport });
  await mockStores(page);
  let listFailure = false;
  let detailFailure = false;
  let holdA = null;
  let onHoldA = null;
  page.on("pageerror", (error) => errors.push(`${name}: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error"
      && !message.text().includes("cdn.example.test")
      && !message.text().includes("server responded with a status of 500")) {
      errors.push(`${name}: ${message.text()}`);
    }
  });
  await page.route("**/api/erp/products**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/PRODUCT-B") && detailFailure) {
      await route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"failed"}' });
      return;
    }
    if (path.endsWith("/PRODUCT-A")) {
      if (holdA) {
        const pending = holdA;
        onHoldA();
        await pending;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ product_id: "PRODUCT-A", title: "Remote mug", skus: [{ sku_id: "SKU-A", sku: "SELLER-SKU-A" }], listings: [item] }) });
      return;
    }
    if (path.endsWith("/PRODUCT-B")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ product_id: "PRODUCT-B", title: "Second product", skus: [{ sku_id: "SKU-B", sku: "SELLER-SKU-B" }], listings: [secondItem] }) });
      return;
    }
    if (listFailure) {
      await route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"failed"}' });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [item, secondItem], total: 2, page: 1, page_size: 50 }) });
  });
  await page.route("https://cdn.example.test/**", (route) => route.fulfill({
    status: 200,
    contentType: "image/svg+xml",
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><rect width="96" height="96" fill="#334155"/></svg>',
  }));
  await page.goto(`${baseUrl}#products`, { waitUntil: "domcontentloaded" });
  await page.locator(".product-center").waitFor({ state: "visible" });
  const card = page.locator(".product-center tbody tr").first();
  await card.getByText("Remote mug").waitFor();
  await card.getByText("部分同步").waitFor();
  await card.getByText("本轮未出现").waitFor();
  await card.getByText("属性失败").waitFor();
  await card.getByRole("button", { name: "查看详情" }).click();
  const detail = page.getByRole("region", { name: "商品详情" });
  await detail.waitFor();
  await detail.getByRole("heading", { name: "Remote mug" }).waitFor();
  async function openDelayedA() {
    let release;
    const held = new Promise((resolveHeld) => { onHoldA = resolveHeld; });
    holdA = new Promise((resolveRelease) => { release = resolveRelease; });
    await card.getByRole("button", { name: "查看详情" }).click();
    await held;
    return async () => {
      const response = page.waitForResponse((value) => value.url().endsWith("/PRODUCT-A"));
      const finished = page.waitForEvent("requestfinished", (request) => request.url().endsWith("/PRODUCT-A"));
      release();
      holdA = null;
      await response;
      await finished;
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    };
  }
  let releaseA = await openDelayedA();
  await page.locator(".product-center tbody tr").nth(1).getByRole("button", { name: "查看详情" }).click();
  await detail.getByRole("heading", { name: "Second product" }).waitFor();
  await releaseA();
  if (!await detail.getByRole("heading", { name: "Second product" }).count()) throw new Error(`${name}: late A replaced B`);

  releaseA = await openDelayedA();
  await page.locator(".product-center tbody tr").nth(1).getByRole("button", { name: "查看详情" }).click();
  await detail.getByRole("heading", { name: "Second product" }).waitFor();
  await detail.getByRole("button", { name: "关闭" }).click();
  await releaseA();
  if (await detail.count()) throw new Error(`${name}: late detail reopened after close`);

  releaseA = await openDelayedA();
  await page.getByLabel("搜索商品").fill("new filter");
  await page.getByRole("button", { name: "筛选" }).click();
  await releaseA();
  if (await detail.count()) throw new Error(`${name}: late detail appeared after filter change`);

  detailFailure = true;
  await page.locator(".product-center tbody tr").nth(1).getByRole("button", { name: "查看详情" }).click();
  await page.getByRole("alert").waitFor();
  if (await detail.count()) throw new Error(`${name}: stale product detail remained after failed detail request`);
  listFailure = true;
  await page.getByLabel("搜索商品").fill("retry");
  await page.getByRole("button", { name: "筛选" }).click();
  await page.getByText("商品读取失败，请检查本地服务后重试。").waitFor();
  if (await page.locator(".product-center tbody tr").count()) throw new Error(`${name}: stale product rows remained after failed list request`);
  await page.screenshot({ path: resolve(tmpdir(), `jlc-product-center-${name}.png`), fullPage: true });
  await page.close();
}

async function verifyImageUrls() {
  for (const [imageUrl, allowed] of [
    ["http://127.0.0.1:8999/private", false],
    ["http://127.0.0.1。:8999/private", false],
    ["https://localhost./private", false],
    ["ftp://cdn.example.test/mug.jpg", false],
    ["https://ir.ozone.ru/s3/multimedia-1-r/c600/111.jpg", true],
  ]) {
    const page = await browser.newPage();
    await mockStores(page);
    let unsafeRequests = 0;
    page.on("request", (request) => {
      if (/127\.0\.0\.1:8999|localhost\./.test(request.url())) unsafeRequests += 1;
    });
    await page.route("**/api/erp/products**", (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ items: [{ ...item, image_url: imageUrl }], total: 1, page: 1, page_size: 50 }),
    }));
    await page.route("https://ir.ozone.ru/**", (route) => route.fulfill({
      status: 200, contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg"/>',
    }));
    await page.goto(`${baseUrl}#products`, { waitUntil: "domcontentloaded" });
    const card = page.locator(".product-center tbody tr").first();
    await card.getByText("Remote mug").waitFor();
    await page.waitForTimeout(100);
    const imageCount = await card.locator("img").count();
    if (Boolean(imageCount) !== allowed || unsafeRequests) {
      throw new Error(`Image safety failed for ${imageUrl}: img=${imageCount}, unsafe requests=${unsafeRequests}`);
    }
    await page.close();
  }
}

try {
  await verifyImageUrls();
  await verify("desktop", { width: 1440, height: 900 });
  await verify("mobile", { width: 390, height: 844 });
} finally {
  await browser.close();
}

if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log(`Product Center UI verified: ${baseUrl}`);
console.log(`Screenshots: ${resolve(tmpdir(), "jlc-product-center-desktop.png")}, ${resolve(tmpdir(), "jlc-product-center-mobile.png")}`);
