import { FormEvent, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { discardReplyDraft, loadReplyDrafts, loadReviewCenter, loadReviewDetail, saveReplyDraft } from "@/services/workbenchApi";
import type { ReplyDraft, ReviewDetail, ReviewPage, ReviewRow } from "@/types/review";

const replyLabels = { UNKNOWN: "平台回复状态未知", UNREPLIED: "平台未回复", REPLIED: "平台已回复" };
const initialFilters = { platform: "", storeId: "", productId: "", rating: "", replyStatus: "", dateFrom: "", dateTo: "" };

function renderableImageUrl(value: string) {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.+$/, "");
    // Review URLs are untrusted; never let the browser load literal private hosts or IPs.
    return url.protocol === "https:" && !url.username && !url.password && host.includes(".")
      && !/^\d+\.\d+\.\d+\.\d+$/.test(host) && !host.startsWith("[")
      && host !== "localhost" && !/\.(local|localhost|localdomain|internal)$/.test(host) ? url.href : null;
  } catch {
    return null;
  }
}

function ReviewCard({ item, onDetail }: { item: ReviewRow; onDetail: (id: string) => void }) {
  return <article className="space-y-3 rounded-xl border border-white/10 bg-slate-900/60 p-4">
    <div className="flex flex-wrap items-center gap-3 text-sm text-slate-300">
      <strong>{item.platform}</strong><span>店铺 {item.store_id}</span><span>商品 {item.product_id || "未关联"}</span>
      <span>{item.rating} / 5 分</span><span>{replyLabels[item.reply_status]}</span>
      <span>{item.published_at ? new Date(item.published_at).toLocaleString() : "日期未知"}</span>
    </div>
    <p className="whitespace-pre-wrap break-words">{item.text || "（无文字评价）"}</p>
    <Button variant="secondary" onClick={() => onDetail(item.id)}>查看评价详情</Button>
  </article>;
}

export function ReviewCenter() {
  const detailVersion = useRef(0);
  const [filters, setFilters] = useState(initialFilters);
  const [applied, setApplied] = useState(initialFilters);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<ReviewPage | null>(null);
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [drafts, setDrafts] = useState<ReplyDraft[]>([]);
  const [content, setContent] = useState("");
  const [editingId, setEditingId] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [draftError, setDraftError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setData(null);
    loadReviewCenter({ ...applied, page }).then((response) => {
      if (active) setData(response);
    }).catch(() => {
      if (active) setError("评价读取失败，请检查本地服务后重试。");
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [applied, page]);

  function submit(event: FormEvent) {
    event.preventDefault();
    detailVersion.current += 1;
    setDetail(null);
    setDrafts([]);
    setContent("");
    setEditingId("");
    setDetailLoading(false);
    setSaving(false);
    setPage(1);
    setApplied({ ...filters });
  }

  async function openDetail(reviewId: string) {
    const version = ++detailVersion.current;
    setDetail(null);
    setDrafts([]);
    setContent("");
    setEditingId("");
    setSaving(false);
    setDraftError("");
    setDetailLoading(true);
    try {
      const review = await loadReviewDetail(reviewId);
      if (version !== detailVersion.current) return;
      const result = await loadReplyDrafts(review.id, review.store_id);
      if (version !== detailVersion.current) return;
      setDetail(review);
      setDrafts(result.items);
    } catch {
      if (version === detailVersion.current) setDraftError("评价详情或本地草稿读取失败。");
    } finally {
      if (version === detailVersion.current) setDetailLoading(false);
    }
  }

  function closeDetail() {
    detailVersion.current += 1;
    setDetail(null);
    setDrafts([]);
    setDetailLoading(false);
    setSaving(false);
  }

  async function saveDraft(event: FormEvent) {
    event.preventDefault();
    if (!detail || !content.trim()) return;
    const version = detailVersion.current;
    const draftId = editingId;
    setSaving(true);
    setDraftError("");
    try {
      const saved = await saveReplyDraft(detail.id, detail.store_id, content, draftId || undefined);
      if (version !== detailVersion.current) return;
      setDrafts((current) => draftId ? current.map((item) => item.id === saved.id ? saved : item) : [saved, ...current]);
      setContent("");
      setEditingId("");
    } catch {
      if (version === detailVersion.current) setDraftError("本地草稿保存失败，请重试。");
    } finally {
      if (version === detailVersion.current) setSaving(false);
    }
  }

  async function discard(draft: ReplyDraft) {
    if (!detail) return;
    const version = detailVersion.current;
    setSaving(true);
    setDraftError("");
    try {
      const updated = await discardReplyDraft(detail.id, detail.store_id, draft.id);
      if (version !== detailVersion.current) return;
      setDrafts((current) => current.map((item) => item.id === updated.id ? updated : item));
      if (editingId === updated.id) { setEditingId(""); setContent(""); }
    } catch {
      if (version === detailVersion.current) setDraftError("本地草稿更新失败，请重试。");
    } finally {
      if (version === detailVersion.current) setSaving(false);
    }
  }

  return <main className="review-center mx-auto max-w-7xl space-y-6 p-4 sm:p-8">
    <header><h1 className="text-2xl font-semibold">评价中心</h1><p className="mt-2 text-sm text-slate-400">查看本地已入库评价。回复草稿仅保存在本地，不会发送给客户。</p></header>
    <form onSubmit={submit} className="grid gap-3 rounded-xl border border-white/10 p-4 sm:grid-cols-2 lg:grid-cols-4">
      <label className="grid gap-1 text-sm">平台<select value={filters.platform} onChange={(event) => setFilters({ ...filters, platform: event.target.value })} className="rounded-md border border-white/10 bg-slate-900 px-3 py-2"><option value="">全部平台</option><option value="OZON">Ozon</option><option value="WILDBERRIES">Wildberries</option><option value="KASPI">Kaspi</option></select></label>
      <label className="grid gap-1 text-sm">店铺 ID<input value={filters.storeId} onChange={(event) => setFilters({ ...filters, storeId: event.target.value })} className="rounded-md border border-white/10 bg-slate-900 px-3 py-2" /></label>
      <label className="grid gap-1 text-sm">商品 ID<input value={filters.productId} onChange={(event) => setFilters({ ...filters, productId: event.target.value })} className="rounded-md border border-white/10 bg-slate-900 px-3 py-2" /></label>
      <label className="grid gap-1 text-sm">评分<select value={filters.rating} onChange={(event) => setFilters({ ...filters, rating: event.target.value })} className="rounded-md border border-white/10 bg-slate-900 px-3 py-2"><option value="">全部评分</option>{[1, 2, 3, 4, 5].map((rating) => <option key={rating} value={rating}>{rating} 分</option>)}</select></label>
      <label className="grid gap-1 text-sm">平台回复状态<select value={filters.replyStatus} onChange={(event) => setFilters({ ...filters, replyStatus: event.target.value })} className="rounded-md border border-white/10 bg-slate-900 px-3 py-2"><option value="">全部状态</option><option value="UNKNOWN">未知</option><option value="UNREPLIED">未回复</option><option value="REPLIED">已回复</option></select></label>
      <label className="grid gap-1 text-sm">开始日期<input type="date" value={filters.dateFrom} onChange={(event) => setFilters({ ...filters, dateFrom: event.target.value })} className="rounded-md border border-white/10 bg-slate-900 px-3 py-2" /></label>
      <label className="grid gap-1 text-sm">结束日期<input type="date" value={filters.dateTo} onChange={(event) => setFilters({ ...filters, dateTo: event.target.value })} className="rounded-md border border-white/10 bg-slate-900 px-3 py-2" /></label>
      <Button type="submit" disabled={loading}>筛选评价</Button>
      <Button type="button" variant="secondary" disabled={loading} onClick={() => setApplied({ ...applied })}>刷新本地评价</Button>
    </form>
    {error && <p role="alert" className="rounded-lg border border-amber-500/40 p-4 text-amber-200">{error}</p>}
    {loading && <p role="status">正在读取评价…</p>}
    {!loading && !error && data?.items.length === 0 && <p className="rounded-xl border border-white/10 p-6">{data.total > 0 ? "当前页没有评价，请返回上一页。" : Object.values(applied).some(Boolean) ? "没有符合筛选条件的评价。请调整筛选条件。" : "本地暂无已入库评价。此页面只展示已保存的本地评价。"}</p>}
    {!loading && <div className="space-y-4">{data?.items.map((item) => <ReviewCard key={item.id} item={item} onDetail={(id) => void openDetail(id)} />)}</div>}
    {!loading && data && data.total > data.page_size && <nav aria-label="评价分页" className="flex items-center justify-between"><Button variant="secondary" disabled={page === 1} onClick={() => setPage((value) => value - 1)}>上一页</Button><span>{page} / {Math.ceil(data.total / data.page_size)}</span><Button variant="secondary" disabled={page * data.page_size >= data.total} onClick={() => setPage((value) => value + 1)}>下一页</Button></nav>}
    {detailLoading && <p role="status">正在读取评价详情…</p>}
    {draftError && <p role="alert" className="rounded-lg border border-amber-500/40 p-4 text-amber-200">{draftError}</p>}
    {detail && <section aria-label="评价详情" className="space-y-4 rounded-xl border border-sky-500/30 bg-slate-900 p-5">
      <div className="flex items-start justify-between gap-4"><h2 className="text-xl font-semibold">评价详情</h2><Button variant="secondary" onClick={closeDetail}>关闭</Button></div>
      <p>{detail.platform} · 店铺 {detail.store_id} · 商品 {detail.product_id || "未关联"} · {detail.rating} / 5 分 · {replyLabels[detail.reply_status]}</p>
      <p className="whitespace-pre-wrap break-words">{detail.text || "（无文字评价）"}</p>
      {detail.media.length > 0 && <div className="flex flex-wrap gap-3">{detail.media.map((media, index) => {
        const url = media.media_type === "IMAGE" ? renderableImageUrl(media.source_url) : null;
        return url ? <img key={media.id} src={url} alt={`评价图片 ${index + 1}`} className="h-24 w-24 rounded object-cover" />
          : <span key={media.id} className="rounded border border-white/10 p-3 text-sm text-slate-400">媒体 {index + 1}：无法安全预览</span>;
      })}</div>}
      <div className="border-t border-white/10 pt-4">
        <h3 className="font-semibold">本地回复草稿</h3>
        <p className="mt-1 text-sm text-slate-400">保存和丢弃只影响本地草稿；平台回复状态不会改变。</p>
        <form onSubmit={(event) => void saveDraft(event)} className="mt-3 space-y-3">
          <label className="grid gap-1 text-sm">回复草稿内容<textarea required value={content} onChange={(event) => setContent(event.target.value)} className="min-h-24 rounded-md border border-white/10 bg-slate-950 px-3 py-2" /></label>
          <Button type="submit" disabled={saving || !content.trim()}>{editingId ? "更新本地草稿" : "保存本地草稿"}</Button>
        </form>
        <div className="mt-4 space-y-3">{drafts.map((draft) => <article key={draft.id} className="rounded-lg border border-white/10 p-3">
          <p className="whitespace-pre-wrap break-words">{draft.content}</p>
          <p className="mt-2 text-xs text-slate-400">{draft.status === "DISCARDED" ? "已丢弃" : "本地草稿"} · {draft.source === "MANUAL" ? "人工" : "AI 建议"} · {draft.updated_at}</p>
          {draft.status === "DRAFT" && <div className="mt-2 flex gap-2"><Button variant="secondary" disabled={saving} onClick={() => { setEditingId(draft.id); setContent(draft.content); }}>编辑草稿</Button><Button variant="secondary" disabled={saving} onClick={() => void discard(draft)}>丢弃草稿</Button></div>}
        </article>)}</div>
      </div>
    </section>}
  </main>;
}
