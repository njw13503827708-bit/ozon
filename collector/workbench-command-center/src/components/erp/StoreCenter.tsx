import { FormEvent, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { loadStoreCenter, validateStoreConnection } from "@/services/workbenchApi";
import type { StoreSummary } from "@/types/store";

const connectionLabels: Record<string, string> = {
  CONNECTED: "已连接", FAILED: "验证未完成", NEEDS_VALIDATION: "待验证",
};

export function StoreCenter() {
  const [stores, setStores] = useState<StoreSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [validating, setValidating] = useState("");
  const [revision, setRevision] = useState(0);
  const [filters, setFilters] = useState({ q: "", connection_status: "", page: 1, page_size: 20 });
  const [applied, setApplied] = useState(filters);
  const [total, setTotal] = useState(0);
  const [unfilteredTotal, setUnfilteredTotal] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    loadStoreCenter(applied).then((response) => {
      if (active) { setStores(response.items); setTotal(response.total); setUnfilteredTotal(response.unfiltered_total); }
    }).catch(() => {
      if (active) setError("店铺读取失败，请检查本地服务后重试。");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [revision, applied]);

  async function validate(store: StoreSummary) {
    setValidating(store.id);
    setError("");
    try {
      const updated = await validateStoreConnection(store.id);
      setStores((current) => current.map((item) => item.id === store.id ? updated : item));
    } catch {
      setError("验证未完成。请在主电脑检查店铺凭证、连接和读取权限后重试。");
      // Refresh persisted probe status after an unsuccessful validation.
      try { setStores((await loadStoreCenter(applied)).items); } catch { /* Keep the validation error visible. */ }
    } finally {
      setValidating("");
    }
  }

  return (
    <main className="store-center mx-auto max-w-6xl space-y-6 p-4 sm:p-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">店铺中心</h1>
          <p className="mt-2 text-sm text-slate-400">查看已入库店铺、连接与商品读取验证记录。</p>
        </div>
        <Button variant="secondary" disabled={loading || !!validating} onClick={() => setRevision((value) => value + 1)}>刷新</Button>
      </header>
      <form onSubmit={(event: FormEvent) => { event.preventDefault(); setApplied({ ...filters, page: 1 }); }} className="flex flex-wrap gap-3">
        <input aria-label="搜索店铺" placeholder="店铺名称" value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} className="rounded bg-slate-900 p-2" />
        <select aria-label="店铺连接状态" value={filters.connection_status} onChange={(event) => setFilters({ ...filters, connection_status: event.target.value })} className="rounded bg-slate-900 p-2"><option value="">全部连接状态</option>{Object.entries(connectionLabels).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select>
        <Button type="submit" disabled={loading}>查询店铺</Button>
      </form>
      {error ? <p role="alert" className="rounded-lg border border-amber-500/40 p-4 text-amber-200">{error}</p> : null}
      {loading ? <p role="status">正在读取店铺…</p> : null}
      {!loading && !error && stores.length === 0 ? <p className="rounded-xl border border-white/10 p-6">{unfilteredTotal === 0 ? "暂无已入库店铺。请先在工作台店铺设置中配置店铺并完成商品同步。" : "没有符合筛选条件的店铺"}</p> : null}
      {!loading ? <div className="overflow-x-auto rounded-xl border border-white/10"><table aria-label="店铺列表" className="w-full text-left text-sm [&_th]:p-3 [&_td]:p-3"><thead className="bg-slate-900"><tr>{["店铺", "公司与身份", "连接与读取权限", "商品与币种", "最近读取", "操作"].map((name) => <th key={name} scope="col">{name}</th>)}</tr></thead><tbody>
        {stores.map((store) => (
          <tr key={store.id} className="border-t border-white/10 align-top">
            <td>
              <h2 className="break-all text-lg font-semibold">{store.display_name}</h2>
              <span className="text-sm text-slate-400">{store.platform} · {store.enabled ? "已启用" : "已停用"}</span>
            </td>
            <td><p>{store.company_name || "公司未验证"}</p><p className="text-xs text-slate-400">Seller ID：{store.seller_id ?? "官方响应未提供"}</p></td>
            <td><p>{connectionLabels[store.connection_status] || "未知"}</p><p>{store.product_read_allowed === true ? "商品读取权限已验证" : "商品读取权限未验证"}</p><p className="text-xs text-slate-400">后台会话绑定尚未启用</p></td>
            <td><p>商品：{store.product_count} · 上架记录：{store.listing_count ?? "未读取"}</p><p>币种：{store.currency || "未知"}</p></td>
            <td className="text-xs text-slate-400"><p>连接：{store.last_connected_at || "无"}</p><p>商品：{store.last_product_sync_at || "无"}</p><p>分析：{store.analytics_fetched_at || "未读取"}</p></td>
            <td>
            <Button className="mt-5" variant="secondary" disabled={!!validating || !store.enabled || store.platform !== "OZON"} onClick={() => void validate(store)}>
              {validating === store.id ? "正在验证…" : "验证连接与读取权限"}
            </Button>
            <p className="mt-3 text-xs text-slate-400">仅主电脑可验证；读取 Ozon 公司信息与商品列表，不修改平台数据。</p>
            <a className="mt-3 inline-block text-sky-300" href={`#analytics?store_id=${encodeURIComponent(store.id)}`}>查看运营分析</a>
            </td>
          </tr>
        ))}
      </tbody></table></div> : null}
      {!loading && total > applied.page_size ? <nav aria-label="店铺分页" className="flex justify-between"><Button disabled={applied.page === 1} onClick={() => setApplied({ ...applied, page: applied.page - 1 })}>上一页</Button><span>共 {total} 个店铺 · 第 {applied.page} 页</span><Button disabled={applied.page * applied.page_size >= total} onClick={() => setApplied({ ...applied, page: applied.page + 1 })}>下一页</Button></nav> : null}
    </main>
  );
}
