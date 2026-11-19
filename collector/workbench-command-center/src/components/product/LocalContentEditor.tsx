import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { assetUrl, loadLocalKeywordSuggestions } from "@/services/workbenchApi";
import type { KeywordSuggestions, LocalRichContent, ProductDetail, ProductDraftPayload } from "@/types/workbench";
import "./local-content.css";

export function LocalContentEditor({ detail, ready, tagsText, onAccept, onSave }: {
  detail: ProductDetail | null; ready: boolean; tagsText: string;
  onAccept: (tag: string, revision: string) => void; onSave: (payload: ProductDraftPayload) => Promise<void>;
}) {
  const [suggestions, setSuggestions] = useState<KeywordSuggestions | null>(null);
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [rich, setRich] = useState<LocalRichContent>({ modules: [] });
  const [richBusy, setRichBusy] = useState(false);
  const [richMessage, setRichMessage] = useState("");
  const dirty = useRef(false);
  const revision = useRef(0);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (ready && !dirty.current) setRich(detail?.content?.rich_content_draft || { modules: [] });
  }, [ready, detail?.content?.rich_content_draft]);
  const images = Object.entries(detail?.image_assets || {}).flatMap(([bucket, values]) =>
    bucket === "rejected" ? [] : values.filter(x => x.path && x.url && !/^https?:/.test(x.url)));
  const existing = tagsText.split(/[\n,，;；]+/).map(x => x.trim().toLocaleLowerCase()).filter(Boolean);
  function hasTag(tag: string) { return existing.some(x => (x.startsWith("#") ? x : `#${x}`) === tag); }
  function needsConfirmation(tag: string) { return detail?.content?.keyword_basis?.some(x => x.tag === tag && x.valid === false); }
  async function loadSuggestions() {
    if (!ready || !detail) return;
    setBusy(true); setError("");
    try {
      const result = await loadLocalKeywordSuggestions(detail.product_id);
      if (alive.current) { setSuggestions(result); setDecisions({}); }
    } catch (e) { if (alive.current) setError(e instanceof Error ? e.message : "建议读取失败"); }
    finally { if (alive.current) setBusy(false); }
  }
  function editRich(next: LocalRichContent) {
    dirty.current = true; revision.current++; setRich(next); setRichMessage("尚未保存，仅为本地编辑");
  }
  function patchModule(index: number, patch: Partial<LocalRichContent["modules"][number]>) {
    editRich({ modules: rich.modules.map((x, i) => i === index ? { ...x, ...patch } : x) });
  }
  function moveModule(index: number, direction: number) {
    const modules = [...rich.modules];
    [modules[index], modules[index + direction]] = [modules[index + direction], modules[index]];
    editRich({ modules });
  }
  async function saveRich() {
    if (!ready) return;
    const savedRevision = revision.current;
    setRichBusy(true); setRichMessage("");
    try {
      await onSave({ rich_content_draft: rich });
      if (alive.current) {
        if (revision.current === savedRevision) { dirty.current = false; setRichMessage("本地富内容草稿已保存，可关闭后重新打开核对"); }
        else setRichMessage("此前内容已保存；当前新修改尚未保存");
      }
    } catch (e) { if (alive.current) setRichMessage(e instanceof Error ? e.message : "保存失败"); }
    finally { if (alive.current) setRichBusy(false); }
  }
  return <div className="local-content-editor">
    <section aria-label="本地关键词建议">
      <h4>关键词／标签本地建议</h4>
      <p>仅核对已有俄文资料。搜索量、销量和排名未知；建议未经市场热度验证。</p>
      <Button size="sm" onClick={loadSuggestions} disabled={!ready || busy}>{busy ? "读取建议中…" : "查看本地关键词建议"}</Button>
      {error && <p role="alert">{error}</p>}
      {suggestions && <>
        <p>{suggestions.constraints}</p>{suggestions.missing && <p role="status">{suggestions.missing}</p>}
        {suggestions.warnings.length > 0 && <details><summary>重复、无关及格式提示（{suggestions.warnings.length}）</summary>{suggestions.warnings.map(x => <p key={x}>{x}</p>)}</details>}
        {suggestions.items.map(item => <article className="keyword-proposal" key={item.tag}>
          <strong>{item.keyword} → {item.tag}</strong><span>{item.basis}</span>
          <details><summary>查看建议理由和来源</summary><p>{item.reason}</p><p>原文：{item.original}</p><code>{item.source_ref}</code><p>搜索量：未知 · 销量：未知 · 排名：未知</p></details>
          <p>内容变化：{hasTag(item.tag) ? "当前已存在，不重复添加" : `保留当前标题、简介及标签，增加 ${item.tag}`}</p>
          <details><summary>新旧标签预览</summary><pre>当前：{tagsText || "（空）"}{"\n"}接受后：{tagsText}{hasTag(item.tag) ? "" : `${tagsText ? "\n" : ""}${item.tag}`}</pre></details>
          <div className="local-content-actions">
            <Button size="sm" disabled={!ready || (hasTag(item.tag) && !needsConfirmation(item.tag)) || decisions[item.tag] === "拒绝" || (!hasTag(item.tag) && existing.length >= 30)} onClick={() => { onAccept(item.tag, item.source_revision); setDecisions(x => ({ ...x, [item.tag]: "接受后尚未保存" })); }}>{needsConfirmation(item.tag) ? "重新确认标签依据" : "接受标签建议"}</Button>
            <Button size="sm" variant="secondary" disabled={Boolean(decisions[item.tag])} onClick={() => setDecisions(x => ({ ...x, [item.tag]: "拒绝" }))}>拒绝建议</Button>
            <span role="status">{decisions[item.tag] || (hasTag(item.tag) ? "已存在" : "待决定")}</span>
          </div>
        </article>)}
        {existing.length >= 30 && <p role="status">标签已达到30个，请先人工整理；不自动删除你的标签。</p>}
      </>}
      {detail?.content?.keyword_basis?.filter(x => x.valid === false).map(x => <p role="alert" key={x.tag}>{x.tag}：{x.warning}</p>)}
      {(detail?.content?.keyword_basis || []).length > 0 && <details><summary>已保存的建议依据</summary>{detail?.content?.keyword_basis?.map(x => <p key={x.tag}>{x.tag} · {x.valid === false ? "依据失效，需重新确认" : x.basis} · {x.source_ref}</p>)}</details>}
    </section>
    <section aria-label="本地富内容草稿">
      <h4>本地富内容草稿</h4><p>标题、正文及本商品已有图片的图文模块。暂未转换为可上传的平台格式。</p>
      <Button size="sm" disabled={!ready || richBusy || rich.modules.length >= 12} onClick={() => editRich({ modules: [...rich.modules, { title: "", body: "", image_path: "" }] })}>添加图文模块</Button>
      {rich.modules.map((module, index) => <article className="rich-module" key={index}>
        <strong>模块 {index + 1}</strong>
        <label>模块标题<input aria-label={`模块 ${index + 1} 标题`} maxLength={500} value={module.title} onChange={e => patchModule(index, { title: e.target.value })} /></label>
        <label>模块正文<textarea aria-label={`模块 ${index + 1} 正文`} maxLength={6000} value={module.body} onChange={e => patchModule(index, { body: e.target.value })} /></label>
        <label>已有商品图片<select aria-label={`模块 ${index + 1} 图片`} value={module.image_path} onChange={e => patchModule(index, { image_path: e.target.value })}>
          <option value="">纯文字模块</option>{images.map(x => <option key={x.path} value={x.path}>{x.path}</option>)}
          {module.image_path && !images.some(x => x.path === module.image_path) && <option value={module.image_path}>图片待核对：{module.image_path}</option>}
        </select></label>
        <div className="local-content-actions"><Button size="sm" variant="secondary" disabled={index === 0} onClick={() => moveModule(index, -1)}>上移模块</Button><Button size="sm" variant="secondary" disabled={index === rich.modules.length - 1} onClick={() => moveModule(index, 1)}>下移模块</Button><Button size="sm" variant="ghost" onClick={() => editRich({ modules: rich.modules.filter((_, i) => i !== index) })}>删除模块</Button></div>
      </article>)}
      {!rich.modules.length && <p>暂无本地图文模块，可添加后编辑。</p>}
      <details open><summary>富内容预览</summary><div className="rich-local-preview">{rich.modules.map((x, i) => {
        const image = images.find(item => item.path === x.image_path);
        return <article key={i}>{image && <img src={assetUrl(image.url)} alt={`模块 ${i + 1} 商品图片`} />}<h5>{x.title}</h5><p>{x.body}</p>{x.image_path && !image && <small>图片缺失或已变更，请重新选择</small>}</article>;
      })}</div></details>
      <Button size="sm" disabled={!ready || richBusy} onClick={saveRich}>{richBusy ? "保存中…" : "保存本地富内容草稿"}</Button><p role="status">{richMessage}</p>
    </section>
  </div>;
}
