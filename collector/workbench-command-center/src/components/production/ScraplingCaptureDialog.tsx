import { useRef, useState } from "react";
import { Download, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { getErrorMessage, importScrapling, previewScrapling, searchCategories, type ScraplingPreview } from "@/services/workbenchApi";
import type { CategoryCandidate } from "@/types/workbench";

export function ScraplingCaptureDialog() {
  const [open, setOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [html, setHtml] = useState<string | undefined>();
  const [render, setRender] = useState(false);
  const [result, setResult] = useState<ScraplingPreview | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [categories, setCategories] = useState<CategoryCandidate[]>([]);
  const [category, setCategory] = useState<CategoryCandidate | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [savedProduct, setSavedProduct] = useState("");
  const generation = useRef(0);

  function invalidate() {
    generation.current += 1;
    setResult(null); setSelected([]); setCategory(null); setCategories([]); setSavedProduct("");
  }

  async function capture() {
    invalidate();
    const revision = generation.current;
    setBusy(true); setMessage(html === undefined ? "正在核查公开页面访问和采集资料" : "正在解析本人保存的HTML，未发出外部请求");
    try {
      const value = await previewScrapling(url, html, render);
      if (generation.current !== revision) return;
      setResult(value); setMessage(value.message); setQuery(value.capture?.title_cn || "");
    } catch (error) {
      if (generation.current === revision) setMessage(getErrorMessage(error));
    } finally {
      if (generation.current === revision) setBusy(false);
    }
  }

  async function findCategories() {
    const revision = generation.current;
    setBusy(true); setCategory(null);
    try {
      const value = await searchCategories(query);
      if (generation.current !== revision) return;
      setCategories(value.items); setMessage(value.items.length ? "请选择最终Ozon类目，只使用已有本地规则" : "没有符合条件的本地类目");
    } catch (error) {
      if (generation.current === revision) setMessage(getErrorMessage(error));
    } finally { if (generation.current === revision) setBusy(false); }
  }

  async function save() {
    if (!result?.capture_id || !category || !selected.length) return;
    const revision = generation.current;
    setBusy(true);
    try {
      const value = await importScrapling(result.capture_id, selected, Number(category.category_id), Number(category.type_id));
      if (generation.current !== revision) return;
      setSavedProduct(value.product_id); setMessage(`已保存到本地采集箱：${value.product_id}；未生成或上传平台商品。`);
    } catch (error) {
      if (generation.current === revision) setMessage(getErrorMessage(error));
    } finally { if (generation.current === revision) setBusy(false); }
  }

  function download() {
    if (!result) return;
    const href = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {type: "application/json;charset=utf-8"}));
    const link = document.createElement("a"); link.href = href; link.download = `capture-${result.capture_id || "result"}.json`; link.click();
    window.setTimeout(() => URL.revokeObjectURL(href), 1000);
  }

  return <Dialog open={open} onOpenChange={(value) => { setOpen(value); if (!value) { generation.current += 1; setBusy(false); } }}>
    <DialogTrigger asChild><Button variant="secondary" size="sm" title="1688 / Ozon网页采集"><Search className="h-4 w-4" />网页采集</Button></DialogTrigger>
    <DialogContent className="max-h-[90vh] w-[min(900px,96vw)] overflow-y-auto">
      <DialogHeader><DialogTitle>1688 / Ozon网页采集</DialogTitle><DialogDescription>采集真实商品资料。Ozon网页排序仅作为观察，销量及统计周期没有证据时显示未知。</DialogDescription></DialogHeader>
      <label className="grid gap-2">商品或列表网址<input aria-label="采集网址" className="w-full rounded border p-2" value={url} disabled={busy} onChange={(event) => { setUrl(event.target.value); invalidate(); }} placeholder="https://detail.1688.com/offer/…html 或 Ozon类目/搜索页" /></label>
      <label className="grid gap-2">可选：本人保存的HTML（离线解析）<input aria-label="上传保存的HTML" type="file" accept=".html,.htm,text/html" disabled={busy} onChange={async (event) => {
        const file = event.target.files?.[0]; invalidate(); const revision = generation.current;
        setHtml(undefined);
        if (!file) return;
        if (file.size > 6 * 1024 * 1024) { setMessage("HTML文件超过6MB"); return; }
        setBusy(true); setMessage("正在读取HTML文件");
        try {
          const text = await file.text(); if (generation.current === revision) setHtml(text);
        } catch (error) {
          if (generation.current === revision) setMessage(getErrorMessage(error));
        } finally { if (generation.current === revision) setBusy(false); }
      }} /></label>
      {html !== undefined && <Button variant="secondary" onClick={() => { setHtml(undefined); invalidate(); }} disabled={busy}>移除HTML，改为读取公开网页</Button>}
      {html === undefined && <label className="flex items-start gap-2"><input type="checkbox" checked={render} disabled={busy} onChange={(event) => { setRender(event.target.checked); invalidate(); }} />渲染动态页面（占用更多CPU/内存，使用独立未登录浏览器；不绕过验证）</label>}
      <div className="flex flex-wrap gap-2"><Button onClick={capture} disabled={busy || !url.trim()}>{busy ? "处理中…" : html === undefined ? "读取公开网页" : "解析已保存HTML"}</Button>
        <Button variant="secondary" onClick={download} disabled={!result}><Download className="h-4 w-4" />导出采集结果</Button></div>
      <p role="status" className="break-words">{message}</p>
      {result && <p className="text-sm break-words">来源：<a href={result.source_url} target="_blank" rel="noreferrer">{result.source_url}</a><br />采集时间：{result.captured_at} · 方法：{result.capture_method === "user_supplied_html" ? "本人保存HTML，未在线核验" : result.capture_method === "public_browser" ? "公开动态页面，未使用本人登录" : "公开网页读取"} · 请求状态：{result.http_status ?? "离线"}</p>}
      {result?.warnings?.map((warning) => <p key={warning} className="text-amber-200">{warning}</p>)}
      {result?.capture && <>
        <h3>{result.capture.title_cn}</h3><p>供应商：{result.capture.supplier_name}</p>
        <p>{result.capture.product_attributes.map((attribute) => `${attribute.name_cn}：${attribute.value_cn}`).join("；") || "商品属性未知"}</p>
        <fieldset disabled={busy || !!savedProduct}><legend>选择真实SKU（最多10个）</legend>
          {result.capture.skus.map((sku) => <label key={sku.sku_id} className="flex items-start gap-2 border-b py-2">
            <input type="checkbox" checked={selected.includes(sku.sku_id)} disabled={sku.availability === "out_of_stock" || (!selected.includes(sku.sku_id) && selected.length >= 10)} onChange={(event) => setSelected(event.target.checked ? [...selected, sku.sku_id] : selected.filter((id) => id !== sku.sku_id))} />
            <span className="break-words">{sku.sku_name} · SKU {sku.sku_id} · 采购价 {sku.purchase_price === null ? "未知" : `¥${sku.purchase_price}`} · 报价数量条件 {sku.source_data?.price_quantity_threshold ?? "未知"} · 页面可订数量 {sku.source_data?.stock_quantity ?? "未知"} · {sku.availability === "out_of_stock" ? "实测零，不可选择" : "当前可售状态未核实（不等同库存为零）"}<br />
              {sku.image_url !== "unknown" ? <a href={sku.image_url} target="_blank" rel="noreferrer">核对本规格图片</a> : "专属图片缺失，后续必须人工绑定"}</span>
          </label>)}
        </fieldset>
        <label className="grid gap-2">搜索最终Ozon类目<input aria-label="搜索最终类目" value={query} onChange={(event) => { setQuery(event.target.value); setCategory(null); }} disabled={busy || !!savedProduct} className="rounded border p-2" /></label>
        <Button variant="secondary" onClick={findCategories} disabled={busy || !!savedProduct || !query.trim()}>搜索本地类目</Button>
        <select aria-label="选择最终类目" value={category ? `${category.category_id}:${category.type_id}` : ""} onChange={(event) => setCategory(categories.find((item) => `${item.category_id}:${item.type_id}` === event.target.value) || null)} disabled={busy || !!savedProduct} className="w-full rounded border p-2">
          <option value="">请选择最终类目</option>{categories.map((item) => { const path = item.path_zh || item.path || []; return <option key={`${item.category_id}:${item.type_id}`} value={`${item.category_id}:${item.type_id}`}>{Array.isArray(path) ? path.join(" / ") : path} · {item.name_zh || item.name}</option>; })}
        </select>
        <Button onClick={save} disabled={busy || !!savedProduct || result.state !== "ready" || !selected.length || !category}>保存到本地采集箱</Button>
        {savedProduct && <a href={`?task_center=inbox&product_id=${encodeURIComponent(savedProduct)}#workbench`}>打开已采集商品</a>}
      </>}
      {result?.items && <><p>排序依据：网页排列；销量：未知；销量统计周期：未知。不会写入官方销量榜。</p>
        <ol>{result.items.map((item) => <li key={item.product_id} className="py-2"><a href={item.product_url} target="_blank" rel="noreferrer">网页位置 {item.page_position} · {item.title_ru}</a><br />商品ID {item.product_id} · 销量 {item.ordered_units ?? "未知"}</li>)}</ol></>}
    </DialogContent>
  </Dialog>;
}
