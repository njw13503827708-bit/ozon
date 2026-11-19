import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { identifier, option, positivePage, searchText, usePageParams } from "@/lib/erpUrlState";
import { Button } from "@/components/ui/button";
import { loadErpProductDetail, loadProductCenter, loadStoreCenter } from "@/services/workbenchApi";
import type { StoreSummary } from "@/types/store";
import type { ErpProductDetail, ErpProductPage, ErpProductRow } from "@/types/product";

const listingLabels: Record<string, string> = {
  PARTIAL: "部分同步",
  ONLINE: "在售",
  UNKNOWN: "状态未知",
};
const presenceLabels: Record<string, string> = {
  SEEN: "本轮已出现",
  NOT_SEEN: "本轮未出现",
  UNKNOWN: "出现状态未知",
};
const attributeLabels: Record<string, string> = {
  SYNCED: "属性已同步",
  FAILED: "属性失败",
  PENDING: "属性待同步",
};

function label(labels: Record<string, string>, value: string | null) {
  return labels[value || ""] || value || "未知";
}

function renderableImageUrl(value: string | null) {
  if (!value || /[\\\s]/.test(value)) return null;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.+$/, "");
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password
      && host.includes(".") && !host.startsWith(".") && !host.includes(":")
      && !/^\d+$/.test(host.split(".").at(-1) || "")
      && !/\.(local|localhost|localdomain|internal)$/.test(host) ? url.href : null;
  } catch {
    return null;
  }
}

function ProductRow({ item, onDetail }: { item: ErpProductRow; onDetail: (id: string) => void }) {
  const imageUrl = renderableImageUrl(item.image_url);
  return <tr className="border-b border-white/10 align-top [&>td]:p-3">
    <td><div className="flex h-16 w-16 items-center justify-center overflow-hidden rounded-lg bg-slate-800 text-xs text-slate-500">
      {imageUrl ? <img src={imageUrl} alt={item.title} className="h-full w-full object-cover" /> : "暂无图片"}
    </div></td>
    <td className="min-w-60 max-w-96"><h2 className="font-semibold">{item.title}</h2><p className="mt-2 text-xs text-slate-400">Ozon 商品编号：{item.ozon_product_id || "未知"}</p></td>
    <td className="break-all">{item.store || "未绑定店铺"}</td>
    <td className="break-all"><p>商家 SKU：{item.sku || "未知"}</p><p>货号：{item.offer_id || "未知"}</p><p>Ozon SKU：{[...new Set((item.platform_skus || []).map((sku) => sku.sku).concat(item.ozon_sku || []))].join("、") || "未读取"}</p></td>
    <td className="whitespace-nowrap">{item.price == null ? "价格未知" : `${item.price} ${item.currency || ""}`}</td>
    <td>
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="rounded bg-slate-800 px-2 py-1">{label(listingLabels, item.listing_status)}</span>
        <span className="rounded bg-slate-800 px-2 py-1">{label(presenceLabels, item.presence_status)}</span>
        <span className="rounded bg-slate-800 px-2 py-1">{label(attributeLabels, item.attribute_status)}</span>
      </div>
      <p className="text-xs text-slate-500">最近同步：{item.last_sync || "暂无记录"}</p>
    </td>
    <td><Button variant="secondary" onClick={() => onDetail(item.product_id)}>查看详情</Button>
      {item.store_id && item.listing_id && <Button variant="secondary" asChild><a href={`#optimization?${new URLSearchParams({store_id: item.store_id, listing_id: String(item.listing_id)})}`}>本地优化</a></Button>}
    </td>
  </tr>;
}

export function ProductCenter() {
  const detailVersion = useRef(0);
  const [query, updateParams] = usePageParams("products");
  const applied = useMemo(() => {
    const p = new URLSearchParams(query);
    return { q: searchText(p.get("q")), storeId: identifier(p.get("store_id")),
      listingStatus: option(p.get("listing_status"), ["ONLINE", "PARTIAL"]),
      presenceStatus: option(p.get("presence_status"), ["SEEN", "NOT_SEEN"]),
      attributeStatus: option(p.get("attribute_status"), ["SYNCED", "FAILED", "PENDING"]) };
  }, [query]);
  const page = positivePage(new URLSearchParams(query).get("page"));
  const [filters, setFilters] = useState(applied);
  useEffect(() => { setFilters(applied); detailVersion.current += 1; setDetail(null); }, [applied]);
  function apply(next = applied, nextPage = 1) {
    updateParams({ q: next.q, store_id: next.storeId, listing_status: next.listingStatus,
      presence_status: next.presenceStatus, attribute_status: next.attributeStatus, page: nextPage });
  }
  const [stores, setStores] = useState<StoreSummary[]>([]);
  const [data, setData] = useState<ErpProductPage | null>(null);
  const [detail, setDetail] = useState<ErpProductDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    loadStoreCenter().then((response) => { if (active) setStores(response.items); })
      .catch(() => { if (active) setError("店铺筛选读取失败，请检查本地服务。"); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setData(null);
    loadProductCenter({ ...applied, page }).then((response) => {
      if (active) setData(response);
    }).catch(() => {
      if (active) setError("商品读取失败，请检查本地服务后重试。");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [applied, page]);

  function submit(event: FormEvent) {
    event.preventDefault();
    detailVersion.current += 1;
    setDetail(null);
    apply(filters);
  }

  async function openDetail(productId: string) {
    const version = ++detailVersion.current;
    setError("");
    setDetail(null);
    try {
      const response = await loadErpProductDetail(productId);
      if (version === detailVersion.current) setDetail(response);
    } catch {
      if (version === detailVersion.current) setError("商品详情读取失败。");
    }
  }

  function closeDetail() {
    detailVersion.current += 1;
    setDetail(null);
  }

  const hasNext = Boolean(data && data.page * data.page_size < data.total);
  return <main className="product-center mx-auto max-w-7xl space-y-6 p-4 sm:p-8">
    <header>
      <h1 className="text-2xl font-semibold">商品中心</h1>
      <p className="mt-2 text-sm text-slate-400">查看店铺已同步商品、商家货号和全部平台 SKU。当前显示本地同步记录，不代表远程全店同步已完成。</p>
    </header>
    <form onSubmit={submit} className="grid gap-3 rounded-xl border border-white/10 p-4 sm:grid-cols-2 lg:grid-cols-5">
      <select aria-label="店铺筛选" value={filters.storeId} onChange={(event) => setFilters({ ...filters, storeId: event.target.value })} className="rounded-md border border-white/10 bg-slate-900 px-3 py-2"><option value="">全部店铺与本地商品</option>{stores.map((store) => <option key={store.id} value={store.id}>{store.display_name}</option>)}</select>
      <input aria-label="搜索商品" value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} placeholder="商品名称 / 商家或 Ozon SKU / 货号" className="rounded-md border border-white/10 bg-slate-900 px-3 py-2" />
      <select aria-label="Listing 状态" value={filters.listingStatus} onChange={(event) => setFilters({ ...filters, listingStatus: event.target.value })} className="rounded-md border border-white/10 bg-slate-900 px-3 py-2"><option value="">Listing 全部</option><option value="ONLINE">在售</option><option value="PARTIAL">部分同步</option></select>
      <select aria-label="出现状态" value={filters.presenceStatus} onChange={(event) => setFilters({ ...filters, presenceStatus: event.target.value })} className="rounded-md border border-white/10 bg-slate-900 px-3 py-2"><option value="">出现状态全部</option><option value="SEEN">本轮已出现</option><option value="NOT_SEEN">本轮未出现</option></select>
      <select aria-label="属性状态" value={filters.attributeStatus} onChange={(event) => setFilters({ ...filters, attributeStatus: event.target.value })} className="rounded-md border border-white/10 bg-slate-900 px-3 py-2"><option value="">属性状态全部</option><option value="SYNCED">属性已同步</option><option value="FAILED">属性失败</option><option value="PENDING">属性待同步</option></select>
      <Button type="submit" disabled={loading}>筛选</Button>
    </form>
    {error ? <p role="alert" className="rounded-lg border border-amber-500/40 p-4 text-amber-200">{error}</p> : null}
    {loading ? <p role="status">正在读取商品…</p> : null}
    {!loading && !error && data?.items.length === 0 ? <p className="rounded-xl border border-white/10 p-6">暂无符合条件的已入库商品。</p> : null}
    {!loading && data ? <><p className="text-sm text-slate-400">共 {data.total} 条商品或上架记录</p><div className="overflow-x-auto rounded-xl border border-white/10"><table aria-label="店铺商品" className="w-full text-left text-sm"><thead className="bg-slate-900 [&_th]:p-3"><tr>{["图片", "商品", "店铺", "SKU 与货号", "价格", "状态与同步时间", "操作"].map((title) => <th key={title} scope="col">{title}</th>)}</tr></thead><tbody>{data.items.map((item) => <ProductRow key={item.listing_id ?? item.product_id} item={item} onDetail={(id) => void openDetail(id)} />)}</tbody></table></div></> : null}
    {!loading && data && data.total > data.page_size ? <nav aria-label="商品分页" className="flex items-center justify-between"><Button variant="secondary" disabled={page === 1} onClick={() => { closeDetail(); apply(applied, Math.max(1, page - 1)); }}>上一页</Button><span>{page} / {Math.ceil(data.total / data.page_size)}</span><Button variant="secondary" disabled={!hasNext} onClick={() => { closeDetail(); apply(applied, page + 1); }}>下一页</Button></nav> : null}
    {detail ? <section aria-label="商品详情" className="rounded-xl border border-sky-500/30 bg-slate-900 p-5">
      <div className="flex items-start justify-between gap-4"><div><h2 className="text-xl font-semibold">{detail.title}</h2><p className="mt-1 break-all text-sm text-slate-400">Product ID: {detail.product_id}</p></div><Button variant="secondary" onClick={closeDetail}>关闭</Button></div>
      <p className="mt-4 text-sm">SKU：{detail.skus.map((sku) => sku.sku || sku.sku_id).join("、") || "无"}</p>
      <p className="mt-2 text-sm">Listing：{detail.listings.map((listing) => listing.offer_id || listing.listing_id).join("、") || "无"}</p>
      <div className="mt-4 space-y-3">{detail.listings.map((listing) => <div key={listing.listing_id} className="rounded-lg border border-white/10 p-3"><p>{listing.store} · 货号：{listing.offer_id} · {listing.price == null ? "价格未知" : `${listing.price} ${listing.currency || ""}`}</p><p>Ozon 商品编号：{listing.ozon_product_id || "未知"}</p><ul className="mt-2 text-sm text-slate-400">{(listing.platform_skus || []).map((sku) => <li key={`${sku.sku}:${sku.source}:${sku.shipment_type}`}>Ozon SKU：{sku.sku} · 来源：{sku.source} {sku.shipment_type}</li>)}</ul></div>)}</div>
    </section> : null}
  </main>;
}
