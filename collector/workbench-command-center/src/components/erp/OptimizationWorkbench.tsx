import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { identifier, option, positivePage, searchText, usePageParams, validDay } from "@/lib/erpUrlState";
import { loadOptimizationCandidates, loadStoreCenter, loadOptimization, saveOptimization, checkOptimization,
  reviewOptimization, restoreOptimization, optimizationExportUrl, generateOptimization, loadOptimizationTask,
  controlOptimizationTask, buildOptimizationPublication, confirmOptimizationPublication, reconcileOptimizationPublication, manageOptimizationAsset,
  loadAIProviderSettings } from "@/services/workbenchApi";
import type { StoreSummary } from "@/types/store";
import type { OptimizationContent, OptimizationData, OptimizationCandidates, OptimizationSuggestion, OptimizationVersion, OptimizationTask, OptimizationPublicationReview } from "@/types/optimization";

export function operationsDates(query: string) {
  const params = new URLSearchParams(query);
  const today = new Intl.DateTimeFormat("en-CA", {timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit"}).format(new Date());
  const to = params.get("date_to"), from = params.get("date_from");
  if (validDay(from, today) && validDay(to, today) && from! <= to! && (Date.parse(to!) - Date.parse(from!)) / 86400000 <= 366)
    return {date_from: from!, date_to: to!};
  const end = new Date(`${today}T00:00:00Z`); end.setUTCDate(end.getUTCDate() - 1);
  const start = new Date(end); start.setUTCDate(start.getUTCDate() - 6);
  return {date_from: start.toISOString().slice(0, 10), date_to: end.toISOString().slice(0, 10)};
}
const inputClass = "w-full rounded border border-white/20 bg-slate-900 p-2";
const tagsOf = (text: string) => text.split(/[\n,，;；\s]+/).map(x => x.trim()).filter(Boolean);
function editable(version: OptimizationVersion): OptimizationContent {
  const c = version.content;
  return {title_ru: c.title_ru, description_ru: c.description_ru, tags: c.tags,
    rich_content: c.rich_content, baseline_period: c.baseline_period, applied_at: c.applied_at || "", keyword_selection: [], image_selection:c.image_selection};
}

export function OptimizationWorkbench() {
  const [query, update] = usePageParams("optimization");
  const selected = useMemo(() => {
    const p = new URLSearchParams(query), listing = p.get("listing_id");
    return {store_id: identifier(p.get("store_id")), listing_id: listing && /^[1-9]\d{0,9}$/.test(listing) ? Number(listing) : 0,
      q: searchText(p.get("q")), issue: option(p.get("issue"), ["CONTENT_MISSING", "OBSERVED_ZERO", "NO_DATA"]), page: positivePage(p.get("page")), ...operationsDates(query)};
  }, [query]);
  const [stores, setStores] = useState<StoreSummary[]>([]);
  const [products, setProducts] = useState<OptimizationCandidates | null>(null);
  const [search, setSearch] = useState(selected.q);
  const [error, setError] = useState("");
  const [targets,setTargets]=useState<Array<{store_id:string;listing_id:number;version:number;offer_id:string}>>([]);
  const [publication,setPublication]=useState<OptimizationPublicationReview | null>(null);
  const [batchConfirmed,setBatchConfirmed]=useState(false),[batchBusy,setBatchBusy]=useState(false),[batchMessage,setBatchMessage]=useState("");
  const [authorization,setAuthorization]=useState("");
  const batchLock=useRef(false),batchRevision=useRef(0),mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;batchRevision.current++;};},[]);
  function invalidate(store:string,listing:number) {
    batchRevision.current++;setPublication(null);setBatchConfirmed(false);
    setTargets(current=>current.filter(item=>item.store_id!==store || item.listing_id!==listing));
  }
  async function batchAction(kind:"preview"|"confirm"|"readback") {
    if(batchLock.current)return;
    batchLock.current=true;setBatchBusy(true);const revision=batchRevision.current;
    try {
      if(kind==="preview") {
        const value=await buildOptimizationPublication(targets.map(({store_id,listing_id,version})=>({store_id,listing_id,version})));
        if(mounted.current && revision===batchRevision.current){setPublication(value);setBatchConfirmed(false);}
      } else if(kind==="confirm" && publication && batchConfirmed) {
        const value=await confirmOptimizationPublication(publication.review_hash);
        if(mounted.current){setAuthorization(value.authorization_id);setBatchMessage(JSON.stringify(value.items,null,2));setBatchConfirmed(false);}
      } else if(kind==="readback" && authorization) {
        const value=await reconcileOptimizationPublication(authorization);
        if(mounted.current)setBatchMessage(JSON.stringify(value.items,null,2));
      }
    } catch(error){if(mounted.current)setBatchMessage(String(error));}
    finally{batchLock.current=false;if(mounted.current)setBatchBusy(false);}
  }
  useEffect(() => { let active = true; loadStoreCenter().then(x => active && setStores(x.items)).catch(e => active && setError(String(e))); return () => {active = false;}; }, []);
  useEffect(() => { setSearch(selected.q); }, [selected.q]);
  useEffect(() => {
    let active = true; setProducts(null); setError("");
    if (selected.store_id && !selected.listing_id) loadOptimizationCandidates(selected.store_id, selected).then(x => active && setProducts(x)).catch(e => active && setError(String(e)));
    return () => {active = false;};
  }, [selected.store_id, selected.q, selected.page, selected.issue, selected.listing_id, selected.date_from, selected.date_to]);
  return <main className="local-operations-page mx-auto max-w-7xl space-y-5 p-4 text-slate-100">
    <h1 className="text-2xl font-semibold">商品优化工作台</h1>
    <p>选择自有店铺商品，查看来源与诊断，调用后台模型生成独立版本；平台更新须查看最终内容并整批确认。未知热度不会填数。</p>
    <div className="flex flex-wrap gap-3">
      <label>店铺<select aria-label="优化店铺" className={inputClass} value={selected.store_id} onChange={e => update({...selected, store_id: e.target.value, listing_id: "", page: 1})}><option value="">请选择店铺</option>{stores.map(s => <option key={s.id} value={s.id}>{s.company_name || s.display_name}</option>)}</select></label>
      <form className="flex items-end gap-2" onSubmit={e => {e.preventDefault(); update({...selected, q: search, page: 1});}}><label>商品搜索<input aria-label="优化商品搜索" className={inputClass} value={search} onChange={e => setSearch(e.target.value)} /></label><Button>搜索本地商品</Button></form>
      <label>证据筛选<select aria-label="优化问题筛选" className={inputClass} value={selected.issue} onChange={e => update({...selected, issue: e.target.value, listing_id: "", page: 1})}><option value="">全部商品</option><option value="CONTENT_MISSING">缓存内容资料不足</option><option value="OBSERVED_ZERO">已有实测零记录</option><option value="NO_DATA">缺少表现数据</option></select></label>
      {selected.store_id && <Button variant="secondary" onClick={() => update({...selected, listing_id: ""})}>关闭当前商品</Button>}
    </div>
    {error && <p role="alert">{error}</p>}
    {!selected.store_id && <p>请选择店铺。没有表现数据仍可检查内容资料。</p>}
    {selected.store_id && !selected.listing_id && <section aria-label="待优化商品列表" className="space-y-2">
      {products ? <><p>本店{products.total}条匹配Listing；当前第{selected.page}页</p><p>{products.selection_rule}</p>{products.items.map(item => <article key={item.listing_id} className="flex flex-wrap items-center justify-between gap-3 rounded border border-white/10 p-3"><div>{item.title}<p className="text-xs">货号：{item.offer_id} · SKU：{item.platform_skus.join("、") || "未知"} · {item.availability === "NO_DATA" ? "无表现数据" : item.availability}</p>{item.reasons.map((reason, i) => <p className="text-xs text-amber-200" key={i}>{reason}</p>)}</div><Button onClick={() => update({...selected, listing_id: item.listing_id || ""})}>选择并诊断</Button></article>)}{!products.items.length && <p>没有符合条件的本地商品。</p>}<Button disabled={selected.page <= 1} onClick={() => update({...selected, page: selected.page - 1})}>上一页</Button><Button disabled={selected.page * products.page_size >= products.total} onClick={() => update({...selected, page: selected.page + 1})}>下一页</Button></> : <p role="status">商品列表加载中…</p>}
    </section>}
    <section aria-label="最终整批UPDATE" className="space-y-3 rounded border border-white/10 p-4">
      <h2>已审核商品批次（{targets.length}）</h2><p>逐商品保存与审核后加入；本批任一来源或版本失效将阻断全部发送。CREATE另用生产批次确认。</p>
      {targets.map(item=><p key={`${item.store_id}:${item.listing_id}`}>店铺 {item.store_id} · {item.offer_id} · Listing {item.listing_id} · v{item.version} <Button disabled={batchBusy} variant="secondary" onClick={()=>invalidate(item.store_id,item.listing_id)}>移出本批</Button></p>)}
      <Button disabled={!targets.length || batchBusy} onClick={()=>void batchAction("preview")}>准备最终整批只读预览</Button>
      {publication && <><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(publication.items,null,2)}</pre>
        <label className="block"><input aria-label="最终整批UPDATE确认" type="checkbox" disabled={batchBusy} checked={batchConfirmed} onChange={e=>setBatchConfirmed(e.target.checked)} /> 我确认本批全部目标、SKU、UPDATE操作、内容和图片顺序，授权执行这个冻结批次</label>
        <Button disabled={batchBusy || !batchConfirmed} onClick={()=>void batchAction("confirm")}>确认整批并执行原商品UPDATE</Button></>}
      {authorization && <Button disabled={batchBusy} variant="secondary" onClick={()=>void batchAction("readback")}>只读查询处理结果并回读字段图片</Button>}
      {batchMessage && <pre role="status" className="whitespace-pre-wrap break-words">{batchMessage}</pre>}
    </section>
    {selected.store_id && selected.listing_id > 0 && <OptimizationEditor key={`${selected.store_id}:${selected.listing_id}`} storeId={selected.store_id} listingId={selected.listing_id}
      dates={{date_from: selected.date_from, date_to: selected.date_to}} onDates={dates => update({...selected, ...dates})}
      onInvalidate={()=>invalidate(selected.store_id,selected.listing_id)}
      onAdd={version=>{invalidate(selected.store_id,selected.listing_id);setTargets(current=>[...current,{store_id:selected.store_id,listing_id:selected.listing_id,version:version.version,offer_id:version.offer_id}]);}} />}
  </main>;
}

function OptimizationEditor({storeId, listingId, dates, onDates, onInvalidate, onAdd}: {storeId: string; listingId: number;
  dates: {date_from: string; date_to: string}; onDates: (value: {date_from: string; date_to: string}) => void;
  onInvalidate:()=>void;onAdd:(version:{version:number;offer_id:string})=>void}) {
  const [data, setData] = useState<OptimizationData | null>(null);
  const [draft, setDraft] = useState<OptimizationContent>({title_ru: "", description_ru: "", tags: [], rich_content: {modules: []}});
  const [tagsText, setTagsText] = useState("");
  const [revision, setRevision] = useState("");
  const [localDates, setLocalDates] = useState(dates);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const [checks, setChecks] = useState<Array<{kind: string; reason: string}>>([]);
  const [rejected, setRejected] = useState<string[]>([]), [confirmed, setConfirmed] = useState(false);
  const [failedImages, setFailedImages] = useState<string[]>([]);
  const alive = useRef(true), requests = useRef(0), edits = useRef(0), dirty = useRef(false), initialized = useRef(false);
  const [dirtyView, setDirtyView] = useState(false);
  const [task, setTask] = useState<OptimizationTask | null>(null);
  const [modelOptions, setModelOptions] = useState<string[]>([]), [modelChoice, setModelChoice] = useState("");
  const [generateFields, setGenerateFields] = useState(["title_ru","description_ru","tags","rich_content"]);
  const head = data?.versions[0];
  useEffect(() => {alive.current = true; return () => {alive.current = false; requests.current++;};}, []);
  useEffect(() => {let active=true;loadAIProviderSettings().then(settings=>{if(!active || !alive.current)return;const models=settings.models.filter(model=>!model.startsWith("gpt-image-"));setModelOptions(models);setModelChoice(current=>models.includes(current)?current:models.includes(settings.model)?settings.model:models[0] || "");}).catch(error=>active&&setMessage(String(error)));return()=>{active=false;};}, []);
  useEffect(() => setLocalDates(dates), [dates.date_from, dates.date_to]);
  useEffect(() => {
    if (!task || !["QUEUED","RUNNING","INTERRUPTED"].includes(task.status)) return;
    let active = true;
    const timer = window.setInterval(() => { void loadOptimizationTask(storeId,listingId,task.id).then(value => {if (active && alive.current) setTask(value);}).catch(error => {if(active) setMessage(String(error));}); },2000);
    return () => {active=false;window.clearInterval(timer);};
  },[task?.id,task?.status,storeId,listingId]);
  async function load() {
    const token = ++requests.current; setLoading(true); setMessage("");
    try {
      const result = await loadOptimization(storeId, listingId, dates.date_from, dates.date_to);
      if (!alive.current || token !== requests.current) return;
      setData(result);
      if (!initialized.current || !dirty.current) {
        const content = result.versions[0] ? editable(result.versions[0]) : {title_ru: result.source.title_ru,
          description_ru: result.source.description_ru, tags: tagsOf(result.source.tags_text), rich_content: {modules: []}, applied_at: ""};
        setDraft(content); setTagsText(content.tags.join("\n"));
        setRevision(result.source.source_revision); setChecks(result.versions[0]?.content.checks || []);
        initialized.current = true;
      } else setMessage("本地数据已刷新，未保存编辑已保留；来源变化需重新核对。");
    } catch (e) {if (alive.current && token === requests.current) setMessage(e instanceof Error ? e.message : "读取失败");}
    finally {if (alive.current && token === requests.current) setLoading(false);}
  }
  useEffect(() => {void load();}, [storeId, listingId, dates.date_from, dates.date_to]);
  function edit(patch: Partial<OptimizationContent>) {
    onInvalidate();
    dirty.current = true; edits.current++; setDirtyView(true); setConfirmed(false); setDraft(current => ({...current, ...patch}));
  }
  function payload(): OptimizationContent {return {...draft, tags: tagsOf(tagsText), baseline_period: dates};}
  function hydrate(version: OptimizationVersion) {
    const value = editable(version); setDraft(value); setTagsText(value.tags.join("\n")); setChecks(version.content.checks);
    dirty.current = false; setDirtyView(false); setConfirmed(false); setRevision(version.source_revision);
    setData(current => current ? {...current, versions: [version, ...current.versions.filter(v => v.version !== version.version)]} : current);
  }
  async function action(kind: "save" | "check" | "review" | "restore", version?: number) {
    if (!data || loading || busy) return;
    const captured = edits.current; setBusy(true); setMessage("");
    try {
      if (kind === "check") {
        const result = await checkOptimization(storeId, listingId, head?.version || 0, revision, payload());
        if (alive.current && captured === edits.current) {setChecks(result.checks); setMessage("已检查当前内容；结果仅为资料核对提示，未保存、未发布。");}
      } else if (kind === "save") {
        const result = await saveOptimization(storeId, listingId, head?.version || 0, revision, payload());
        if (alive.current && captured === edits.current) {hydrate(result); setMessage(`已保存独立本地版本 ${result.version}；可关闭后重开，尚未审核/发布。`);}
      } else if (kind === "review" && head && confirmed && !dirty.current) {
        const result = await reviewOptimization(storeId, listingId, head.version, revision);
        if (alive.current && captured === edits.current) {hydrate(result); setMessage("本地人工审核完成，可以导出；没有上传平台。");}
      } else if (kind === "restore" && version && !dirty.current) {
        const result = await restoreOptimization(storeId, listingId, version, head?.version || 0, revision);
        if (alive.current && captured === edits.current) {hydrate(result); setMessage(`已恢复为新本地版本 ${result.version}，需重新审核。`);}
      }
    } catch (e) {if (alive.current) setMessage(e instanceof Error ? e.message : "操作失败；编辑保留");}
    finally {if (alive.current) setBusy(false);}
  }
  function accept(item: OptimizationSuggestion) {
    if (item.field === "tags") {
      const tags = tagsOf(tagsText); if (!tags.includes(item.after)) tags.push(item.after);
      setTagsText(tags.join("\n")); edit({tags, keyword_selection: [...new Set([...(draft.keyword_selection || []), item.id])]});
    } else if (draft[item.field] === item.before) edit({[item.field]: item.after});
    else setMessage("该字段已有人工编辑；请手工合并建议，当前输入已保留。");
  }
  async function startGeneration() {
    if (!data || busy || loading) return;
    setBusy(true);onInvalidate();
    try { const value=await generateOptimization(storeId,listingId,head?.version || 0,revision,generateFields,dates,modelChoice);if(alive.current){setTask(value);setMessage("后台任务已保存；当前人工输入保持独立，结果完成后可逐字段合并。");} }
    catch(error) {if(alive.current)setMessage(String(error));}
    finally {if(alive.current)setBusy(false);}
  }
  if (!data) return <section><p role="status">{loading ? "商品详情加载中，加载后可编辑。" : "商品读取未完成。"}</p>{message && <p role="alert">{message}</p>}<Button onClick={load}>重试本地读取</Button></section>;
  const source = data.source, modules = draft.rich_content.modules;
  function modulePatch(index: number, patch: Partial<typeof modules[number]>) {edit({rich_content: {modules: modules.map((module, i) => i === index ? {...module, ...patch} : module)}});}
  function move(index: number, direction: number) {const next = [...modules]; [next[index], next[index + direction]] = [next[index + direction], next[index]]; edit({rich_content: {modules: next}});}
  return <div className="space-y-5">
    <section className="rounded border border-white/10 p-4" aria-label="表现诊断">
      <h2 className="text-xl">{source.title_ru || source.offer_id}</h2><p>店铺：{source.company_name || source.store_name} · 货号：{source.offer_id} · SKU：{source.platform_skus.join("、") || "未读取"}</p>
      <form className="my-3 flex flex-wrap items-end gap-2" onSubmit={e => {e.preventDefault(); onDates(localDates);}}><label>开始日期<input aria-label="优化开始日期" type="date" className={inputClass} value={localDates.date_from} onChange={e => setLocalDates({...localDates, date_from: e.target.value})} /></label><label>结束日期<input aria-label="优化结束日期" type="date" className={inputClass} value={localDates.date_to} onChange={e => setLocalDates({...localDates, date_to: e.target.value})} /></label><Button>查询本地表现</Button><Button type="button" variant="secondary" onClick={load}>刷新本地来源</Button></form>
      <p>当前查询：{dates.date_from} 至 {dates.date_to}；SKU/日期记录覆盖：{data.diagnosis.coverage.recorded_sku_days}/{data.diagnosis.coverage.requested_sku_days}；{data.diagnosis.availability === "NO_DATA" ? "无表现数据，不能判断低表现" : data.diagnosis.availability === "INSUFFICIENT_COVERAGE" ? "样本/日期覆盖不足，不能确认低表现原因" : "有观测记录，缺少行业比较基准"}</p>
      <p>来源：{data.diagnosis.source || "未读取"}；读取时间：{data.diagnosis.fetched_at || "未知"}</p>
      <div className="my-3 grid gap-3 sm:grid-cols-3">{Object.entries(data.diagnosis.metrics).map(([key, metric]) => <div key={key} className="rounded bg-slate-900 p-3"><p>{metric.label}</p><strong>{metric.value ?? "未知"}</strong><p>{metric.state === "OBSERVED_ZERO" ? "实测零（仅已有记录）" : metric.state === "NO_DATA" ? "未读取/无对应记录" : metric.state}</p></div>)}</div>
      <p>{data.diagnosis.calculation}</p><p>{data.diagnosis.conversion_reason}</p>
      {data.diagnosis.issues.map((issue, i) => <p key={i}>{issue.kind === "CONTENT_MISSING" ? "内容资料不足" : "观测问题"}：{issue.reason}</p>)}
      {data.diagnosis.available_snapshots.map(snapshot => <Button key={`${snapshot.date_from}:${snapshot.date_to}`} variant="secondary" onClick={() => onDates(snapshot)}>选择已有快照 {snapshot.date_from} 至 {snapshot.date_to}</Button>)}
      <details><summary>商品事实、属性与来源</summary><p>{source.source_ref} · {source.fetched_at || "时间未知"}</p><pre className="max-h-64 overflow-auto whitespace-pre-wrap">{JSON.stringify(source.attributes, null, 2)}</pre><p>字段/品牌/规格需按原资料核对；不凭图片推定材质、认证或性能。</p></details>
    </section>
    <fieldset disabled={loading || busy} className="space-y-5 disabled:opacity-60">
      <section aria-label="后台智能优化任务" className="space-y-3 rounded border border-white/10 p-4">
        <h2 className="text-xl">后台智能生成</h2>
        <p>由ERP后端调用模型，不依赖客户安装Codex。缺密钥或账号权限时任务明确停止；请求结果未知时不会自动重复收费。</p>
        <label>文本生成模型<select aria-label="文本生成模型" className={inputClass} value={modelChoice} disabled={!modelOptions.length} onChange={e=>setModelChoice(e.target.value)}><option value="">没有已配置的文本模型</option>{modelOptions.map(model=><option key={model} value={model}>{model}</option>)}</select></label>
        {!modelOptions.length && <p role="alert">当前配置没有可用于文案结构化输出的文本模型；gpt-image 模型不能用于本任务。</p>}
        <div className="flex flex-wrap gap-3">{[["title_ru","标题"],["description_ru","描述"],["tags","关键词候选"],["rich_content","富内容"],["images","图片制作"]].map(([field,label])=><label key={field}><input type="checkbox" checked={generateFields.includes(field)} onChange={e=>setGenerateFields(current=>e.target.checked?[...current,field]:current.filter(x=>x!==field))} /> {label}</label>)}</div>
        <Button disabled={!modelChoice || !generateFields.length || ["QUEUED","RUNNING"].includes(task?.status || "")} onClick={()=>void startGeneration()}>创建智能优化任务</Button>
        {task && <article className="space-y-2 rounded bg-slate-900 p-3">
          <p role="status">{task.status} · {task.progress}% · {task.message}</p><p>任务：{task.id}</p>
          {task.result.strategy && <p>{task.result.strategy.summary}</p>}
          {task.result.provider_evidence && <p>请求模型：{task.result.provider_evidence.model} · 实际用量：{JSON.stringify(task.result.provider_evidence.usage)} · 金额以网关账单为准，未填0</p>}
          <Button variant="secondary" onClick={()=>void controlOptimizationTask(storeId,listingId,task.id,"resume").then(setTask).catch(error=>setMessage(String(error)))}>核查并恢复已保存阶段</Button>
          <Button variant="secondary" onClick={()=>void controlOptimizationTask(storeId,listingId,task.id,"cancel").then(setTask).catch(error=>setMessage(String(error)))}>取消未发送阶段</Button>
          {task.result.content && <details><summary>生成结果与当前输入对比</summary><pre className="max-h-80 overflow-auto whitespace-pre-wrap">{JSON.stringify({当前:draft,生成:task.result.content},null,2)}</pre>
            {(["title_ru","description_ru","tags","rich_content"] as const).map(field=><Button key={field} variant="secondary" onClick={()=>{const value=task.result.content?.[field];if(value!==undefined){edit({[field]:value});if(field==="tags")setTagsText((value as string[]).join("\n"));}}}>人工合并 {field}</Button>)}
          </details>}
          <Button variant="secondary" onClick={()=>void load()}>读取后台新版本（保留未保存输入）</Button>
        </article>}
        {data.assets?.map(asset=><article key={asset.reference}><img className="max-h-80" src={asset.preview_url} alt="本商品后台生成候选，需要人工核对身份" /><p>{asset.qc_state} · {asset.public_url ? "已登记公网引用，仍须整批确认" : "尚无可提交公网引用"}</p>
          <Button variant="secondary" onClick={()=>{if(window.confirm("确认此图片商品身份、颜色、结构、SKU、标记及素材使用权与原图一致？")){void manageOptimizationAsset(storeId,listingId,asset.reference,"review").then(()=>{if(alive.current){onInvalidate();void load();}}).catch(e=>alive.current&&setMessage(String(e)));}}}>人工核对图片身份与权利</Button>
          <Button variant="secondary" disabled={asset.qc_state!=="ACCEPTED_IDENTITY"} onClick={()=>void manageOptimizationAsset(storeId,listingId,asset.reference,"publish").then(()=>{if(alive.current){onInvalidate();void load();}}).catch(e=>alive.current&&setMessage(String(e)))}>验证已授权图片托管</Button>
        </article>)}
        <h3>最终商品选图与顺序</h3><p>第一张为主图。空选图、未审核或无法公开访问的生成图将阻断平台确认。</p>
        {[...source.images,...(data.assets?.map(asset=>asset.reference)||[])].map(reference=><label className="block break-all" key={reference}><input type="checkbox" checked={(draft.image_selection || source.images).includes(reference)} onChange={e=>edit({image_selection:e.target.checked?[...(draft.image_selection || source.images),reference]:(draft.image_selection || source.images).filter(x=>x!==reference)})} /> {reference}</label>)}
        {(draft.image_selection || source.images).map((reference,index)=><div key={reference} className="flex flex-wrap gap-2"><span>第 {index+1} 张</span><Button variant="secondary" disabled={index===0} onClick={()=>{const next=[...(draft.image_selection || source.images)];[next[index-1],next[index]]=[next[index],next[index-1]];edit({image_selection:next});}}>前移</Button><span className="break-all">{reference}</span></div>)}
      </section>
      <section aria-label="关键词与文案建议" className="rounded border border-white/10 p-4">
        <h2 className="text-xl">有依据的建议</h2><p>{data.suggestions.generation_capability}</p><p>搜索量、排名、全市场热度缺少证据时均未知。以下不是完整AI生成结果。</p>
        {data.suggestions.missing && <p>{data.suggestions.missing}</p>}
        {data.suggestions.items.map(item => <article key={item.id} className="my-3 space-y-2 rounded bg-slate-900 p-3"><strong>{item.field === "tags" ? "标签" : item.field === "title_ru" ? "标题" : "描述"}：{item.after}</strong><p>{item.basis} · {item.reason}</p><details><summary>原文、来源与不确定性</summary><pre className="whitespace-pre-wrap">原文：{item.before || "新增标签"}{"\n"}建议：{item.after}</pre><p>{item.source_ref} {item.period && `${item.period.date_from} 至 ${item.period.date_to}`}</p><p>{item.uncertainty}</p></details><Button disabled={rejected.includes(item.id)} onClick={() => accept(item)}>接受此建议</Button><Button variant="secondary" onClick={() => setRejected(current => [...new Set([...current, item.id])])}>拒绝此建议</Button>{rejected.includes(item.id) && <span>已拒绝，未改草稿</span>}</article>)}
        <details><summary>去重/无关/格式提示</summary>{data.suggestions.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</details>
      </section>
      <section aria-label="原商品整批更新确认" className="space-y-3 rounded border border-white/10 p-4">
        <h2 className="text-xl">更新原商品</h2><p>本地保存、平台提交受理和实际字段成功分别显示。无库存、仓库、价格或上下架操作。</p>
        <Button disabled={head?.status!=="REVIEWED" || dirtyView || !head.source_valid} onClick={()=>head && onAdd({version:head.version,offer_id:source.offer_id})}>加入最终已审核UPDATE批次</Button>
      </section>
      <section aria-label="俄文内容编辑" className="space-y-3 rounded border border-white/10 p-4">
        <h2 className="text-xl">编辑当前本地内容</h2><label className="block">俄文标题<input aria-label="优化俄文标题" className={inputClass} maxLength={500} value={draft.title_ru} onChange={e => edit({title_ru: e.target.value})} /></label>
        <label className="block">俄文描述<textarea aria-label="优化俄文描述" className={inputClass} rows={6} maxLength={6000} value={draft.description_ru} onChange={e => edit({description_ru: e.target.value})} /></label>
        <label className="block">关键词／标签（每行一个）<textarea aria-label="优化标签" className={inputClass} rows={4} value={tagsText} onChange={e => {setTagsText(e.target.value); edit({tags: tagsOf(e.target.value)});}} /></label>
        <Button variant="secondary" onClick={() => void action("check")}>检查当前内容</Button>
        {checks.map((check, i) => <p key={i} role="status">{check.kind}：{check.reason}</p>)}
        <p>其他品牌、规格和语义问题仍需人工核对；手工标签只标手工，不获得建议来源。</p>
        <details><summary>已保存的标签依据</summary>{head?.content.keyword_basis.map(item => <p key={item.tag}>{item.tag} · {item.basis} · {head.source_valid ? "当前依据有效" : "来源失效"} · {item.source_ref}</p>)}</details>
      </section>
      <section aria-label="实际商品富内容" className="space-y-3 rounded border border-white/10 p-4">
        <h2 className="text-xl">本商品图文富内容</h2><p>仅使用当前Listing已有图片引用；平台原富内容尚未映射，此处是独立本地模块，不覆盖原内容。</p>
        <Button disabled={modules.length >= 12} onClick={() => edit({rich_content: {modules: [...modules, {title: "", body: "", image_path: ""}]}})}>添加图文模块</Button>
        {modules.map((module, i) => <article key={i} className="space-y-2 rounded bg-slate-900 p-3"><strong>图文模块 {i + 1}</strong><label className="block">模块标题<input aria-label={`优化模块 ${i + 1} 标题`} className={inputClass} maxLength={500} value={module.title} onChange={e => modulePatch(i, {title: e.target.value})} /></label><label className="block">正文<textarea aria-label={`优化模块 ${i + 1} 正文`} className={inputClass} rows={4} maxLength={6000} value={module.body} onChange={e => modulePatch(i, {body: e.target.value})} /></label><label className="block">本商品已有图片<select aria-label={`优化模块 ${i + 1} 图片`} className={inputClass} value={module.image_path} onChange={e => modulePatch(i, {image_path: e.target.value})}><option value="">纯文字模块</option>{source.images.map((url, n) => <option key={url} value={url}>已有图片 {n + 1}</option>)}{data.assets?.map((asset,n)=><option key={asset.reference} value={asset.reference}>生成候选 {n+1}（需核对身份）</option>)}{module.image_path && !source.images.includes(module.image_path) && !data.assets?.some(x=>x.reference===module.image_path) && <option value={module.image_path}>图片来源失效，请重新选择</option>}</select></label><Button variant="secondary" disabled={i === 0} onClick={() => move(i, -1)}>上移</Button><Button variant="secondary" disabled={i === modules.length - 1} onClick={() => move(i, 1)}>下移</Button><Button variant="secondary" onClick={() => edit({rich_content: {modules: modules.filter((_, n) => n !== i)}})}>删除模块</Button></article>)}
        {!source.images.length && <p>当前缓存没有可用图片引用，可编辑纯文字；不能借用其他商品图片。</p>}
        <details open><summary>富内容预览</summary>{modules.map((module, i) => <article key={i} className="my-3"><h3>{module.title}</h3><p className="whitespace-pre-wrap">{module.body}</p>{module.image_path && (source.images.includes(module.image_path) || data.assets?.some(x=>x.reference===module.image_path)) && !failedImages.includes(module.image_path) ? <img className="max-h-80 max-w-full object-contain" src={data.assets?.find(x=>x.reference===module.image_path)?.preview_url || module.image_path} alt={module.title} onError={() => setFailedImages(current => [...new Set([...current, module.image_path])])} /> : module.image_path ? <p role="alert">图片缺失、加载失败或来源变化，请重新选择。</p> : null}</article>)}</details>
      </section>
      <section aria-label="原文与修改对比" className="rounded border border-white/10 p-4"><h2 className="text-xl">原文与修改后对比</h2>{(["title_ru", "description_ru"] as const).map(field => <div key={field} className="my-3 grid gap-3 md:grid-cols-2"><div><strong>原{field === "title_ru" ? "标题" : "描述"}</strong><pre className="whitespace-pre-wrap break-words">{source[field] || "未读取"}</pre></div><div><strong>修改后</strong><pre className="whitespace-pre-wrap break-words">{draft[field] || "尚未填写"}</pre></div></div>)}<p>原标签：{source.tags_text || "未读取"}</p><p>当前标签：{tagsText || "空"}</p></section>
      <section aria-label="保存审核与导出" className="space-y-3 rounded border border-white/10 p-4"><h2 className="text-xl">保存、人工审核、导出</h2>
        <p>{dirtyView ? "有未保存修改" : `当前本地版本：${head?.version || "未保存"} · ${head?.status || "DRAFT"}`}；本地保存不表示平台已生效。</p>
        {revision !== source.source_revision && <p role="alert">来源已变化，保存会停止。<Button variant="secondary" onClick={() => {setRevision(source.source_revision); setConfirmed(false); setMessage("已选择当前来源；需重新确认失效标签依据并核对全部事实。");}}>确认按最新来源重新核对，保留当前编辑</Button></p>}
        {head?.warning && <p role="alert">{head.warning}</p>}
        <Button onClick={() => void action("save")}>保存独立本地版本</Button>
        <label className="block"><input aria-label="人工确认优化内容" type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> 我已核对俄文内容、SKU事实、所有检查提示，并确认所用图片属于该商品且有使用权限。</label>
        <Button disabled={!head || dirtyView || !confirmed || !head.source_valid} onClick={() => void action("review")}>人工审核已保存版本</Button>
        {head?.status === "REVIEWED" && head.source_valid && !dirtyView && <Button asChild><a href={optimizationExportUrl(storeId, listingId, head.version)}>导出俄文优化成果包</a></Button>}
        <p>导出含俄文文案、图文预览、图片引用、版本/依据和人工使用说明；平台格式未验证，没有上传。</p>
        <details><summary>版本历史与恢复上一版</summary>{data.versions.map(version => <article key={version.version} className="my-3 rounded bg-slate-900 p-3"><strong>版本 {version.version} · {version.status}</strong><p>{version.saved_at} · {version.source_valid ? "来源有效" : "来源已失效"}</p><details><summary>查看历史内容</summary><pre className="whitespace-pre-wrap break-words">{JSON.stringify(editable(version), null, 2)}</pre></details><Button disabled={dirtyView} variant="secondary" onClick={() => void action("restore", version.version)}>恢复此版为新草稿</Button></article>)}</details>
        <label className="block">平台实际应用日期（人工记录，可留空）<input aria-label="平台实际应用日期" type="date" className={inputClass} value={draft.applied_at || ""} onChange={e => edit({applied_at: e.target.value})} /></label><p>基线周期：{head?.content.baseline_period?.date_from || dates.date_from} 至 {head?.content.baseline_period?.date_to || dates.date_to}。可选择后续日期查询本地表现；无快照时显示未知，不宣称提升。</p>
      </section>
    </fieldset>
    <p role="status" className="sticky bottom-0 rounded bg-slate-900 p-3">{loading ? "本地资料读取中，编辑暂时锁定…" : busy ? "操作中，当前编辑已保留…" : message || "仅在本地编辑，未修改原始资料或平台。"}</p>
  </div>;
}
