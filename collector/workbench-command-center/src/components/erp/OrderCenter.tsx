import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { identifier, option, positivePage, searchText, usePageParams } from "@/lib/erpUrlState";
import { operationsDates } from "@/components/erp/OptimizationWorkbench";
import { loadStoreCenter, loadOrderCenter, loadOrderDetail, startOrderSync, loadOrderJob } from "@/services/workbenchApi";
import type { StoreSummary } from "@/types/store";
import type { OrderData, OrderJob, OrderPosting } from "@/types/optimization";

const statuses: Record<string, string> = {awaiting_registration: "等待登记", acceptance_in_progress: "接收中",
  awaiting_approve: "等待确认", awaiting_packaging: "等待包装", awaiting_deliver: "等待发运",
  arbitration: "仲裁", client_arbitration: "配送仲裁", delivering: "运输中", driver_pickup: "司机配送",
  delivered: "已配送", cancelled: "已取消", not_accepted: "尚未接收", sent_by_seller: "卖家已发送"};
const inputClass = "rounded border border-white/20 bg-slate-900 p-2";

export function OrderCenter() {
  const [query, update] = usePageParams("orders");
  const applied = useMemo(() => {
    const p = new URLSearchParams(query);
    return {store_id: identifier(p.get("store_id")), q: searchText(p.get("q")),
      status: option(p.get("status"), Object.keys(statuses)), page: positivePage(p.get("page")), ...operationsDates(query)};
  }, [query]);
  const [filters, setFilters] = useState(applied);
  const [stores, setStores] = useState<StoreSummary[]>([]), [data, setData] = useState<OrderData | null>(null);
  const [detail, setDetail] = useState<{posting: OrderPosting; source: string; fetched_at: string} | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState("");
  const [reload, setReload] = useState(0), [jobId, setJobId] = useState(""), [job, setJob] = useState<OrderJob | null>(null);
  const [starting, setStarting] = useState(false);
  const detailRequest = useRef(0), scope = useRef(applied.store_id);
  useEffect(() => {let active = true; loadStoreCenter().then(x => active && setStores(x.items)).catch(e => active && setError(String(e))); return () => {active = false;};}, []);
  useEffect(() => {
    setFilters(applied); detailRequest.current++; setDetail(null);
    if (scope.current !== applied.store_id) {setJobId(""); setJob(null); scope.current = applied.store_id;}
  }, [applied]);
  useEffect(() => {
    let active = true; setData(null); setError("");
    if (!applied.store_id) return;
    setLoading(true);
    loadOrderCenter(applied.store_id, applied).then(x => {if (active) setData(x);})
      .catch(e => {if (active) setError(e instanceof Error ? e.message : "本地订单读取失败");})
      .finally(() => {if (active) setLoading(false);});
    return () => {active = false; detailRequest.current++;};
  }, [applied, reload]);
  useEffect(() => {
    if (!jobId || !applied.store_id) return;
    let active = true, timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await loadOrderJob(applied.store_id, jobId);
        if (!active) return;
        setJob(result);
        if (["SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"].includes(result.status)) {setReload(n => n + 1); return;}
        timer = setTimeout(poll, 1500);
      } catch (e) {if (active) setError(e instanceof Error ? e.message : "订单任务状态读取失败");}
    }
    void poll(); return () => {active = false; clearTimeout(timer);};
  }, [jobId, applied.store_id]);
  async function synchronize() {
    if (!applied.store_id || starting) return;
    const store = applied.store_id; setStarting(true); setError("");
    try {const result = await startOrderSync(store, applied.date_from, applied.date_to); if (scope.current === store) {setJobId(result.job_id); setJob(null);}}
    catch (e) {if (scope.current === store) setError(e instanceof Error ? e.message : "订单读取未成功");}
    finally {if (scope.current === store) setStarting(false);}
  }
  async function openDetail(postingNumber: string) {
    const token = ++detailRequest.current; setDetail(null); setError("");
    try {const result = await loadOrderDetail(applied.store_id, postingNumber, applied.date_from, applied.date_to); if (token === detailRequest.current) setDetail(result);}
    catch (e) {if (token === detailRequest.current) setError(e instanceof Error ? e.message : "详情读取失败");}
  }
  return <main className="local-operations-page mx-auto max-w-7xl space-y-4 p-4 text-slate-100">
    <h1 className="text-2xl font-semibold">订单中心</h1><p>自有店铺FBS/rFBS货件及商品明细，只读查询；不发货、不退款、不取消订单，不修改平台。</p>
    <form className="flex flex-wrap items-end gap-3" onSubmit={e => {e.preventDefault(); if (filters.date_from > filters.date_to) {setError("开始日期不能晚于结束日期"); return;} update({...filters, page: 1});}}>
      <label>店铺<select aria-label="订单店铺" className={inputClass} value={filters.store_id} onChange={e => setFilters({...filters, store_id: e.target.value})}><option value="">请选择店铺</option>{stores.map(store => <option key={store.id} value={store.id}>{store.company_name || store.display_name}</option>)}</select></label>
      <label>开始日期<input aria-label="订单开始日期" className={inputClass} type="date" required value={filters.date_from} onChange={e => setFilters({...filters, date_from: e.target.value})} /></label>
      <label>结束日期<input aria-label="订单结束日期" className={inputClass} type="date" required value={filters.date_to} onChange={e => setFilters({...filters, date_to: e.target.value})} /></label>
      <label>状态<select aria-label="订单状态" className={inputClass} value={filters.status} onChange={e => setFilters({...filters, status: e.target.value})}><option value="">全部状态</option>{Object.entries(statuses).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label>搜索<input aria-label="订单搜索" placeholder="货件号、订单号、SKU、货号或商品" className={inputClass} value={filters.q} onChange={e => setFilters({...filters, q: e.target.value})} /></label>
      <Button disabled={!filters.store_id}>查询本地订单</Button>
    </form>
    <div className="flex flex-wrap gap-3"><Button variant="secondary" disabled={!applied.store_id} onClick={() => setReload(n => n + 1)}>刷新本地订单</Button><Button disabled={!data?.read_authorized || starting || Boolean(job && ["QUEUED", "RUNNING"].includes(job.status))} onClick={() => void synchronize()}>{job?.status === "FAILED" || job?.status === "INTERRUPTED" ? "重试自有店铺只读订单同步" : "自有店铺只读订单同步"}</Button></div>
    {data && !data.read_authorized && <p>新增订单接口尚未获准真实读取；可以查询已有缓存，不影响商品内容编辑。</p>}
    {!applied.store_id && <p>请选择店铺后查询本地订单。</p>}
    {error && <p role="alert">{error}<Button variant="secondary" onClick={() => setReload(n => n + 1)}>重试本地读取</Button></p>}
    {job && <p role="status">{job.status}：{job.message}；已读取{job.progress}个货件。</p>}
    {loading && <p role="status">本地订单加载中…</p>}
    {data && <><p>当前查询：{data.query_period.date_from} 至 {data.query_period.date_to}（Europe/Moscow）；来源：{data.source || "未读取"}；时间：{data.fetched_at || "未知"}</p><p>{data.limitation}</p>
      {!data.items.length && <p>{data.availability === "NOT_SYNCED" ? "本店所选周期尚无已成功同步的订单缓存，不能当作平台订单为零。" : data.total > 0 ? "当前页没有货件，请返回上一页。" : applied.q || applied.status ? "没有符合筛选条件的货件。" : "已成功读取的所选周期没有FBS/rFBS货件。"}</p>}
      <div className="overflow-x-auto"><table className="w-full text-left text-sm [&_th]:p-3 [&_td]:p-3"><thead><tr><th>货件号／订单号</th><th>日期与状态</th><th>商品行／数量</th><th>原币种金额</th><th>详情</th></tr></thead><tbody>{data.items.map(posting => <tr key={posting.posting_number} className="border-t border-white/10"><td>{posting.posting_number}<p>{posting.order_number || "订单号未提供"}</p></td><td>{posting.date || "日期未知"}<p>{statuses[posting.status] || posting.status}</p></td><td>{posting.items.map(item => <p key={`${item.sku}:${item.offer_id}`}>{item.name} · {item.offer_id} · SKU {item.sku} × {item.quantity}</p>)}</td><td>{posting.items.map(item => <p key={`${item.sku}:${item.offer_id}`}>{item.total} {item.currency}</p>)}</td><td><Button variant="secondary" onClick={() => void openDetail(posting.posting_number)}>查看货件详情</Button></td></tr>)}</tbody></table></div>
      <p>货件数量：{data.total}，第{data.page}页；货件数、订单数与订购件数不同义。</p><Button variant="secondary" disabled={applied.page <= 1} onClick={() => update({...applied, page: applied.page - 1})}>上一页</Button><Button variant="secondary" disabled={applied.page * data.page_size >= data.total} onClick={() => update({...applied, page: applied.page + 1})}>下一页</Button>
    </>}
    {detail && <section aria-label="订单货件详情" className="space-y-3 rounded border border-white/20 p-4"><h2 className="text-xl">货件 {detail.posting.posting_number}</h2><p>来源：{detail.source} · {detail.fetched_at}</p><p>订单号：{detail.posting.order_number || "未知"} · {statuses[detail.posting.status] || detail.posting.status}</p>{detail.posting.items.map(item => <article key={`${item.sku}:${item.offer_id}`}><p>{item.name} · SKU {item.sku} · 货号 {item.offer_id}</p><p>{item.quantity}件 × {item.price} {item.currency} = {item.total} {item.currency}</p></article>)}<p>不保存或展示收件人姓名、电话、地址。本地金额不代表实收利润。</p><Button variant="secondary" onClick={() => {detailRequest.current++; setDetail(null);}}>关闭详情</Button></section>}
  </main>;
}
