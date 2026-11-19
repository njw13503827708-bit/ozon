import { FormEvent, useEffect, useMemo, useState } from "react";
import { identifier, option, positivePage, searchText, usePageParams, validDay } from "@/lib/erpUrlState";
import { Button } from "@/components/ui/button";
import { cancelErpSyncJob, getErrorMessage, loadAnalytics, loadErpSyncJob, loadStoreCenter, refreshAnalytics } from "@/services/workbenchApi";
import type { AnalyticsData, AnalyticsMetric, AnalyticsRow } from "@/types/analytics";
import type { StoreSummary } from "@/types/store";

const labels: Record<AnalyticsMetric, string> = {
  revenue: "订购金额", ordered_units: "订购数量", hits_view: "商品展示次数",
  session_view_pdp: "商品访问（按 SKU/日累加）", hits_tocart: "加购次数",
  returns: "退货数量", cancellations: "取消数量", position_category: "类目搜索位置",
};
const tabs = ["数据概览", "所有指标", "销售漏斗", "ABC 分析", "销售领先产品", "搜索位置"];
const unavailable: Record<string, string> = { NOT_SYNCED: "未读取", NOT_REQUESTED: "未读取", NO_PERMISSION: "扩展指标查询未获权限", UNAVAILABLE: "来源暂不支持本次查询", PARTIAL: "数据不完整" };
function today() { return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()); }
function offsetDay(day: string, offset: number) { const date = new Date(`${day}T00:00:00Z`); date.setUTCDate(date.getUTCDate() + offset); return date.toISOString().slice(0, 10); }
function number(value?: string | null) { return value == null ? "未读取" : value; }

function MetricTable({ rows, metrics }: { rows: AnalyticsRow[]; metrics: AnalyticsMetric[] }) {
  return <div className="overflow-x-auto rounded-lg border border-white/10"><table className="w-full text-left text-sm [&_th]:p-3 [&_td]:p-3"><thead className="bg-slate-900"><tr><th scope="col">商品 / SKU / 日期</th>{metrics.map((metric) => <th key={metric} scope="col">{labels[metric]}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.sku || row.day}:${index}`} className="border-t border-white/10"><td>{row.name || row.day || row.sku}<p className="text-xs text-slate-400">{row.sku}</p></td>{metrics.map((metric) => <td key={metric}>{number(row[metric])}</td>)}</tr>)}</tbody></table>{rows.length === 0 ? <p className="p-4 text-slate-400">暂无该日期区间的数据。</p> : null}</div>;
}

export function AnalyticsCenter() {
  const [query, updateParams] = usePageParams("analytics");
  const parsed = useMemo(() => {
    const p = new URLSearchParams(query), end = today();
    const from = p.get("date_from"), to = p.get("date_to");
    const valid = validDay(from, end) && validDay(to, end) && from! <= to!
      && (Date.parse(to!) - Date.parse(from!)) / 86400000 <= 366;
    return { storeId: identifier(p.get("store_id")), tab: option(p.get("tab"), ["0", "1", "2", "3", "4", "5"]) || "0",
      filters: { date_from: valid ? from! : offsetDay(end, -6), date_to: valid ? to! : end,
        metric: option(p.get("metric"), ["revenue", "ordered_units"]) || "revenue", q: searchText(p.get("q")), page: positivePage(p.get("page")) } };
  }, [query]);
  const [stores, setStores] = useState<StoreSummary[]>([]);
  const [defaultStore, setDefaultStore] = useState("");
  const storeId = parsed.storeId || defaultStore;
  const applied = parsed.filters;
  const tab = Number(parsed.tab);
  const [filters, setFilters] = useState(applied);
  useEffect(() => setFilters(applied), [applied]);
  function setApplied(next: typeof applied, nextStore = storeId, nextTab = tab) {
    updateParams({ ...next, store_id: nextStore, tab: nextTab });
  }
  function setTab(next: number) { setApplied(applied, storeId, next); }
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [jobId, setJobId] = useState("");
  const [starting, setStarting] = useState(false);
  const [jobMessage, setJobMessage] = useState("");
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    loadStoreCenter().then((response) => {
      if (!active) return;
      setStores(response.items);
      setDefaultStore(response.items[0]?.id || "");
      if (!response.items.length) setLoading(false);
    }).catch((err) => { if (active) { setError(getErrorMessage(err)); setLoading(false); } });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!storeId) return;
    let active = true;
    setLoading(true);
    setError("");
    setData(null);
    loadAnalytics(storeId, applied).then((response) => { if (active) setData(response); })
      .catch((err) => { if (active) setError(getErrorMessage(err)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [storeId, applied, revision]);

  useEffect(() => {
    if (!jobId) return;
    let active = true;
    let busy = false;
    async function poll() {
      if (busy) return;
      busy = true;
      try {
        const job = await loadErpSyncJob(jobId);
        if (!active) return;
        setJobMessage(job.message || job.status);
        if (["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED"].includes(job.status)) {
          setJobId("");
          setRevision((value) => value + 1);
          if (job.status === "FAILED") setJobMessage(job.error || "读取失败，已有快照保留。");
        }
      } catch (err) { if (active) setJobMessage(getErrorMessage(err)); }
      finally { busy = false; }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [jobId]);

  function submit(event: FormEvent) { event.preventDefault(); setApplied({ ...filters, page: 1 }); }
  function preset(days: number) { const next = { ...filters, date_from: offsetDay(today(), -(days - 1)), date_to: today(), page: 1 }; setFilters(next); setApplied(next); }
  async function refresh() {
    setStarting(true);
    setError("");
    try {
      const response = await refreshAnalytics(storeId, applied.date_from, applied.date_to);
      setJobId(response.job_id);
      setJobMessage("正在读取；官方分析接口每个账号每分钟最多一个请求，分页会在后台继续。");
    } catch (err) { setError(getErrorMessage(err)); }
    finally { setStarting(false); }
  }
  const metricKeys = Object.keys(labels) as AnalyticsMetric[];
  const metric = applied.metric as AnalyticsMetric;
  const hasData = data && data.availability !== "NOT_SYNCED";
  return <main className="analytics-center mx-auto max-w-7xl space-y-5 p-4 sm:p-8">
    <header><h1 className="text-2xl font-semibold">店铺运营分析</h1><p className="mt-2 text-sm text-slate-400">分析所选店铺的订购和商品表现；订购金额不是实收或利润。</p></header>
    <form onSubmit={submit} className="flex flex-wrap items-end gap-3 rounded-xl border border-white/10 p-4">
      <label>店铺<select aria-label="分析店铺" value={storeId} disabled={!!jobId || starting} onChange={(event) => { setApplied({ ...filters, page: 1 }, event.target.value); }} className="block rounded bg-slate-900 p-2">{stores.map((store) => <option key={store.id} value={store.id}>{store.display_name}</option>)}</select></label>
      <label>开始日期<input type="date" aria-label="分析开始日期" value={filters.date_from} max={today()} onChange={(event) => setFilters({ ...filters, date_from: event.target.value })} className="block rounded bg-slate-900 p-2" required /></label>
      <label>结束日期<input type="date" aria-label="分析结束日期" value={filters.date_to} min={filters.date_from} max={today()} onChange={(event) => setFilters({ ...filters, date_to: event.target.value })} className="block rounded bg-slate-900 p-2" required /></label>
      <label>排序口径<select aria-label="分析排序口径" value={filters.metric} onChange={(event) => setFilters({ ...filters, metric: event.target.value })} className="block rounded bg-slate-900 p-2"><option value="revenue">订购金额</option><option value="ordered_units">订购数量</option></select></label>
      <input aria-label="分析商品搜索" placeholder="商品或 Ozon SKU" value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} className="rounded bg-slate-900 p-2" />
      <Button type="submit" disabled={loading}>查询本地数据</Button>
      <Button type="button" variant="secondary" disabled={!storeId || !!jobId || starting} onClick={() => void refresh()}>从 Ozon 只读刷新</Button>
    </form>
    <div className="flex flex-wrap gap-2">{[["今天", 1], ["最近7天", 7], ["最近30天", 30], ["最近90天", 90]].map(([name, days]) => <Button key={name} variant="secondary" onClick={() => preset(Number(days))}>{name}</Button>)}<Button variant="secondary" onClick={() => { const yesterday = offsetDay(today(), -1); const next = { ...filters, date_from: yesterday, date_to: yesterday, page: 1 }; setFilters(next); setApplied(next); }}>昨天</Button></div>
    {error ? <p role="alert" className="rounded-lg border border-amber-500/40 p-3 text-amber-200">{error}</p> : null}
    {jobMessage ? <p role="status" className="text-sm text-sky-200">{jobMessage} {jobId ? <Button variant="secondary" onClick={() => void cancelErpSyncJob(jobId).catch((err) => setError(getErrorMessage(err)))}>取消读取</Button> : null}</p> : null}
    <div role="tablist" aria-label="运营分析页面" className="flex flex-wrap gap-2">{tabs.map((name, index) => <button key={name} role="tab" aria-selected={index === tab} onClick={() => setTab(index)} className={`rounded-lg px-4 py-2 ${index === tab ? "bg-sky-700" : "bg-slate-900"}`}>{name}</button>)}</div>
    {loading ? <p role="status">正在读取本地分析数据…</p> : null}
    <p className="text-sm text-slate-400">当前查询日期：{applied.date_from} 至 {applied.date_to}</p>
    {data ? <p className="text-sm text-slate-400">来源：{hasData ? "Ozon Seller API" : "尚无覆盖所选日期的来源快照"} · 查询日期：{data.date_from} 至 {data.date_to} · 时区：{data.timezone} · 数据时间：{data.fetched_at || "未读取"} · 币种：{data.currency}（店铺配置） · {data.completeness === "COMPLETE" ? "当前快照分页完整" : "尚无完整快照"}</p> : null}
    {data?.last_refresh && ["FAILED", "CANCELLED", "INTERRUPTED"].includes(data.last_refresh.status) ? <p role="alert" className="rounded-lg border border-amber-500/40 p-3 text-amber-200">最近读取：{data.last_refresh.status} · {data.last_refresh.message || "读取未完成"} · {data.last_refresh.finished_at || "结束时间未知"}。下方保留上次成功快照，请核对数据时间。</p> : null}
    {!loading && !hasData ? <p className="rounded-lg border border-white/10 p-5">没有覆盖当前查询日期的完整快照；指标未知，不能按 0 解读。商品目录同步不会自动生成分析指标。</p> : null}
    {!loading && !hasData && data?.available_snapshots?.length ? <div aria-label="已有分析快照" className="flex flex-wrap gap-2"><p className="w-full text-sm text-slate-400">可选择已有快照日期，仅查询本地数据：</p>{data.available_snapshots.map((snapshot) => <Button key={`${snapshot.date_from}:${snapshot.date_to}`} variant="secondary" onClick={() => setApplied({ ...applied, date_from: snapshot.date_from, date_to: snapshot.date_to, page: 1 })}>使用快照日期 {snapshot.date_from} 至 {snapshot.date_to}</Button>)}</div> : null}
    {!loading && hasData && data ? <section role="tabpanel" aria-label={tabs[tab]} className="space-y-4">
      {tab === 0 ? <><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{metricKeys.filter((key) => key !== "position_category").map((key) => <div key={key} className="rounded-xl border border-white/10 p-4"><h2 className="text-sm text-slate-400">{labels[key]}</h2><p className="mt-2 text-2xl">{number(data.totals[key])}{key === "revenue" && data.totals[key] != null ? ` ${data.currency}` : ""}</p>{data.metrics_status[key] !== "AVAILABLE" ? <p className="mt-1 text-xs text-amber-200">{unavailable[data.metrics_status[key] || "NOT_SYNCED"] || "未读取"}</p> : null}</div>)}</div><h2>每日订购趋势</h2><MetricTable rows={data.trend} metrics={["revenue", "ordered_units"]} /></> : null}
      {tab === 1 ? <><MetricTable rows={data.items} metrics={metricKeys.filter((key) => key !== "position_category")} /><nav aria-label="分析分页" className="flex justify-between"><Button disabled={data.page <= 1} onClick={() => setApplied({ ...applied, page: data.page - 1 })}>上一页</Button><span>共 {data.total} 个 SKU · 第 {data.page} 页</span><Button disabled={data.page * data.page_size >= data.total} onClick={() => setApplied({ ...applied, page: data.page + 1 })}>下一页</Button></nav></> : null}
      {tab === 2 ? <><MetricTable rows={[data.totals]} metrics={["hits_view", "session_view_pdp", "hits_tocart", "ordered_units"]} /><p>展示到加购事件比：{data.funnel.view_to_cart == null ? "不可计算（缺数据或零分母）" : `${data.funnel.view_to_cart}%`}</p><p className="text-sm text-slate-400">访问会话、加购事件与订购件数口径不同，不据此推算购买转化率；跨 SKU 累加访问不是去重人数。</p></> : null}
      {tab === 3 ? <><p>按{labels[metric]}贡献排序，A 累计约 80%、B 至 95%、C 其余；跨越阈值的商品计入前一档。</p>{data.abc.length ? <div className="overflow-x-auto"><table className="w-full text-left text-sm [&_th]:p-3 [&_td]:p-3"><thead><tr><th>商品 / SKU</th><th>{labels[metric]}</th><th>分类</th><th>贡献</th><th>累计贡献</th></tr></thead><tbody>{data.abc.map((row) => <tr key={row.sku}><td>{row.name}<p>{row.sku}</p></td><td>{number(row[metric])}</td><td>{row.class}</td><td>{row.contribution_percent}%</td><td>{row.cumulative_percent}%</td></tr>)}</tbody></table></div> : <p>未生成分类：暂无正贡献数据或数据不完整。</p>}</> : null}
      {tab === 4 ? <><p>所选店铺、所选日期按{labels[metric]}排名；此处不是全市场销量排名。</p><MetricTable rows={data.leaders} metrics={["revenue", "ordered_units"]} /></> : null}
      {tab === 5 ? <><p className="text-sm text-slate-400">下方仅展示官方返回的类目搜索位置，不能等同于某个关键词排名。</p>{data.metrics_status.position_category === "AVAILABLE" ? <div className="overflow-x-auto"><table className="w-full text-left text-sm [&_th]:p-3 [&_td]:p-3"><thead><tr><th>SKU</th><th>日期</th><th>类目搜索位置</th></tr></thead><tbody>{data.items.flatMap((row) => (row.positions || []).map((position) => <tr key={`${row.sku}:${position.day}`}><td>{row.sku}</td><td>{position.day}</td><td>{position.value}</td></tr>))}</tbody></table></div> : <p>{unavailable[data.metrics_status.position_category || "NOT_SYNCED"] || "当前来源没有可用位置数据"}</p>}</> : null}
      {tab === 5 ? <nav aria-label="搜索位置分页" className="flex justify-between"><Button disabled={data.page <= 1} onClick={() => setApplied({ ...applied, page: data.page - 1 })}>上一页</Button><span>共 {data.total} 个 SKU · 第 {data.page} 页</span><Button disabled={data.page * data.page_size >= data.total} onClick={() => setApplied({ ...applied, page: data.page + 1 })}>下一页</Button></nav> : null}
    </section> : null}
  </main>;
}
