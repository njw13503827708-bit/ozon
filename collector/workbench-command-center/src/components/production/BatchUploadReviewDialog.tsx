import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ImageOff, Loader2, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { assetUrl, confirmBatchUpload, getErrorMessage, loadBatchUploadReview, WorkbenchApiError } from "@/services/workbenchApi";
import type { BatchUploadConfirmResponse, BatchUploadReviewProduct, BatchUploadReviewResponse, BatchUploadRisk } from "@/types/workbench";

const riskGroups: Array<{ level: BatchUploadRisk["level"]; label: string; variant: "danger" | "warning" | "muted" }> = [
  { level: "blocker", label: "阻断项", variant: "danger" },
  { level: "warning", label: "警告", variant: "warning" },
  { level: "info", label: "提示", variant: "muted" },
];

function known(value: unknown) {
  return value === null || value === undefined || value === "" ? "未提供" : String(value);
}

function price(value?: number | null) {
  return typeof value === "number" && Number.isFinite(value) ? `${value} ₽` : "未提供";
}

function priceRange(product: BatchUploadReviewProduct) {
  const min = product.price_range?.min;
  const max = product.price_range?.max;
  if (typeof min !== "number" || typeof max !== "number") return "未提供";
  return min === max ? `${min} ₽` : `${min}–${max} ₽`;
}

function qcLabel(value?: string | null) {
  const normalized = String(value || "").toLowerCase();
  if (["pass", "passed", "ok"].includes(normalized)) return "通过";
  return value ? `未通过（${value}）` : "未提供";
}

export function BatchUploadReviewDialog({
  open,
  batchId,
  onOpenChange,
  onConfirmed,
  onReturnToWorkbench,
}: {
  open: boolean;
  batchId: string;
  onOpenChange: (open: boolean) => void;
  onConfirmed: (result: BatchUploadConfirmResponse) => void;
  onReturnToWorkbench: (productId: string, focus: string) => void;
}) {
  const [data, setData] = useState<BatchUploadReviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const requestVersion = useRef(0);
  const submittingRef = useRef(false);

  async function readReview(id: string, keepError = false) {
    const version = ++requestVersion.current;
    setLoading(true);
    setData(null);
    if (!keepError) setError("");
    try {
      const result = await loadBatchUploadReview(id);
      if (requestVersion.current === version) setData(result);
    } catch (err) {
      if (requestVersion.current === version) setError(getErrorMessage(err));
    } finally {
      if (requestVersion.current === version) setLoading(false);
    }
  }

  useEffect(() => {
    if (!open || !batchId) return undefined;
    void readReview(batchId);
    return () => {
      requestVersion.current += 1;
    };
  }, [batchId, open]);

  function setOpen(next: boolean) {
    if (!next && submittingRef.current) return;
    if (!next) requestVersion.current += 1;
    onOpenChange(next);
  }

  function returnToWorkbench(productId: string, focus: string) {
    setOpen(false);
    onReturnToWorkbench(productId, focus);
  }

  async function confirmUpload() {
    if (submittingRef.current || !data || data.batch_id !== batchId || !data.can_confirm || data.blocker_count !== 0 || loading) return;
    const version = requestVersion.current;
    submittingRef.current = true;
    setSubmitting(true);
    setError("");
    try {
      const result = await confirmBatchUpload(batchId, data.review_hash);
      if (requestVersion.current !== version) return;
      submittingRef.current = false;
      setSubmitting(false);
      onConfirmed(result);
      setOpen(false);
    } catch (err) {
      if (requestVersion.current !== version) return;
      submittingRef.current = false;
      setSubmitting(false);
      if (err instanceof WorkbenchApiError && err.status === 409) {
        setError("审阅资料已变化或批次已被阻断，已重新读取当前权威资料，请重新核对。");
        await readReview(batchId, true);
      } else {
        setError(getErrorMessage(err));
      }
    } finally {
      if (requestVersion.current === version) {
        submittingRef.current = false;
        setSubmitting(false);
      }
    }
  }

  const current = !loading && data?.batch_id === batchId;
  const canConfirm = Boolean(current && data?.can_confirm && data.blocker_count === 0 && !submitting);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="batch-upload-review-dialog">
        <DialogHeader>
          <div className="panel-kicker">最终只读检查</div>
          <DialogTitle>整批上传审阅</DialogTitle>
          <DialogDescription>核对当前整批快照。这里不能修改商品；确认后才会授权本批次进入 Ozon 上传队列。</DialogDescription>
        </DialogHeader>

        {error && <div className="batch-review-error"><AlertTriangle className="h-4 w-4" />{error}</div>}
        {loading ? (
          <div className="batch-review-empty"><Loader2 className="h-5 w-5 animate-spin" />正在读取当前批次资料</div>
        ) : data ? (
          <>
            <div className="batch-review-summary">
              <div><span>批次</span><strong>{known(data.batch_id)}</strong></div>
              <div><span>商品</span><strong>{data.product_count}</strong></div>
              <div><span>SKU</span><strong>{data.sku_count}</strong></div>
              <div><span>店铺</span><strong>{data.store_count}</strong></div>
              <div><span>预计提交</span><strong>{data.expected_submission_count}</strong></div>
            </div>

            <div className="batch-review-state">
              <Badge variant={data.blocker_count ? "danger" : "default"}>
                {data.blocker_count ? `${data.blocker_count} 个阻断项` : "可确认上传"}
              </Badge>
              <span>警告 {data.warning_count}</span>
              <span>整批店铺：{data.target_store_ids.length ? data.target_store_ids.join("、") : "未提供"}</span>
            </div>

            <ScrollArea className="batch-review-scroll">
              <div className="batch-review-content">
                {riskGroups.map((group) => {
                  const items = data.risks.filter((risk) => risk.level === group.level);
                  return items.length ? (
                    <section key={group.level} className={`batch-review-risks ${group.level}`}>
                      <div className="batch-review-section-title"><ShieldAlert className="h-4 w-4" /><strong>{group.label}</strong><Badge variant={group.variant}>{items.length}</Badge></div>
                      {items.map((risk, index) => (
                        <div key={`${risk.product_id}-${risk.code}-${index}`} className="batch-review-risk">
                          <div><strong>{risk.message}</strong><small>{risk.product_id} · {risk.code}</small></div>
                          <Button size="sm" variant="secondary" onClick={() => returnToWorkbench(risk.product_id, risk.return_focus)}>返回工作台修改</Button>
                        </div>
                      ))}
                    </section>
                  ) : null;
                })}

                {data.products.map((product) => (
                  <article key={product.product_id} className="batch-review-product">
                    <div className="batch-review-product-head">
                      <div>
                        <strong>{known(product.title_ru)}</strong>
                        <span>{product.product_id} · 中文：{known(product.source_title_cn)}</span>
                      </div>
                      <Button size="sm" variant="secondary" onClick={() => returnToWorkbench(product.product_id, "overview")}>返回工作台修改</Button>
                    </div>
                    <div className="batch-review-facts">
                      <div><span>最终类目（请人工核对）</span><strong>{known(product.category_name_zh || product.category_name_ru)} · {known(product.category_id)}</strong><small>{product.category_path?.join(" / ") || "类目路径未显示"}</small></div>
                      <div><span>SKU 价格</span><strong>{priceRange(product)}</strong></div>
                      <div><span>属性覆盖</span><strong>{product.attribute_coverage ? `${product.attribute_coverage.filled} / ${product.attribute_coverage.total}` : "未提供"}</strong><small>缺失 {product.attribute_coverage?.missing ?? "未知"} · unknown {product.unknown_count ?? "未知"} · 估算 {product.estimated_count ?? "未知"}</small></div>
                      <div><span>图片质检</span><strong>图片质检：{qcLabel(product.image_qc_status)}</strong></div>
                      <div><span>目标店铺</span><strong>{product.target_store_ids.length ? product.target_store_ids.join("、") : "未提供"}</strong></div>
                    </div>
                    <div className="batch-review-skus">
                      {product.skus.length ? product.skus.map((sku, index) => (
                        <div key={sku.sku_id || `${product.product_id}-${index}`}>
                          <span>{known(sku.option_text || sku.name || sku.sku_id)}</span>
                          <small>{known(sku.offer_id || sku.offer || sku.offer_code || sku.offer_identifier)}</small>
                          <strong>{price(sku.selling_price_rub ?? sku.price)}</strong>
                        </div>
                      )) : <span>未提供 SKU 明细</span>}
                    </div>
                    <div className="batch-review-images">
                      {product.image_urls.length ? product.image_urls.map((url, index) => (
                        <img key={`${url}-${index}`} src={assetUrl(url)} alt={`${product.product_id} 审阅图 ${index + 1}`} />
                      )) : <div><ImageOff className="h-5 w-5" />未提供可审阅图片</div>}
                    </div>
                  </article>
                ))}
              </div>
            </ScrollArea>
          </>
        ) : (
          <div className="batch-review-empty"><AlertTriangle className="h-5 w-5" />没有可展示的当前批次资料</div>
        )}

        <div className="batch-review-actions">
          <span>{data?.blocker_count ? "请返回工作台处理全部阻断项后重新审阅。" : "只确认当前显示的完整批次快照。"}</span>
          <Button onClick={confirmUpload} disabled={!canConfirm}>
            {submitting ? <><Loader2 className="h-4 w-4 animate-spin" />正在确认整批</> : <><CheckCircle2 className="h-4 w-4" />确认整批上传</>}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
