import { chromium } from "@playwright/test";
import { existsSync } from "node:fs";
const executablePath = ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
const browser = await chromium.launch({headless: true, executablePath});
const page = await browser.newPage();
const errors = [];
page.on("pageerror", error => errors.push(error.message));
const store = {id: "fixture-a", display_name: "测试店铺", platform: "OZON", enabled: true, currency: "RUB", product_count: 51, listing_count: 51, connection_status: "CONNECTED"};
await page.route("**/api/erp/stores**", async route => {
  const url = new URL(route.request().url());
  if (!url.pathname.endsWith("/analytics")) return route.fulfill({json: {items:[store],total:1,page:1,page_size:20}});
  const pageNumber = Number(url.searchParams.get("page") || 1);
  const metrics_status = Object.fromEntries(["revenue","ordered_units","hits_view","session_view_pdp","hits_tocart","returns","cancellations","position_category"].map(key => [key,"AVAILABLE"]));
  const row = {sku: pageNumber === 1 ? "1001" : "1002", name:"测试商品", revenue:"12.30",ordered_units:"3", positions:[{day:"2026-10-01",value:"7"}]};
  await route.fulfill({json:{store_id:store.id, date_from:url.searchParams.get("date_from"),date_to:url.searchParams.get("date_to"),timezone:"Europe/Moscow",currency:"RUB",source:"OZON_SELLER_API",fetched_at:"2026-10-02T00:00:00Z",availability:"AVAILABLE",completeness:"COMPLETE",last_refresh:{id:"test-job",status:"FAILED",message:"测试读取失败",finished_at:"2026-10-02T01:00:00Z"},metrics_status,totals:row,items:[row],total:51,page:pageNumber,page_size:50,trend:[{...row,day:"2026-10-01"}],abc:[{...row,class:"A",contribution_percent:"100",cumulative_percent:"100"}],leaders:[row],funnel:{view_to_cart:null}}});
});
try {
  await page.goto("http://127.0.0.1:5178/#stores");
  await page.getByRole("table",{name:"店铺列表"}).getByText("测试店铺",{exact:true}).waitFor();
  await page.getByRole("link",{name:"查看运营分析"}).click();
  await page.getByRole("tabpanel").waitFor();
  await page.getByRole("alert").getByText(/测试读取失败/).waitFor();
  for (const name of ["数据概览","所有指标","销售漏斗","ABC 分析","销售领先产品","搜索位置"]) {
    await page.getByRole("tab",{name,exact:true}).click();
    await page.getByRole("tabpanel",{name,exact:true}).waitFor();
  }
  await page.getByRole("navigation",{name:"搜索位置分页"}).getByRole("button",{name:"下一页"}).click();
  await page.getByRole("tabpanel").getByText("1002",{exact:true}).waitFor();
  await page.getByRole("button",{name:"最近30天",exact:true}).click();
  await page.getByRole("tabpanel").getByText("1001",{exact:true}).waitFor();
  if (errors.length) throw new Error(errors.join("\n"));
  console.log("Store → analytics, six populated tabs, positions pagination and date preset verified (synthetic fixtures; no Ozon calls).");
} finally { await browser.close(); }
