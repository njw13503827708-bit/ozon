import type { SearchVisibilityAction } from "@/types/workbench";

export type MetricRecord = { source?: string; date_from?: string; date_to?: string };
function isDay(value?: string) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}
export function observedValue(value: unknown, record?: MetricRecord | null): number | null {
  if (!record?.source?.trim() || /unknown|inference/i.test(record.source)
      || !isDay(record.date_from) || !isDay(record.date_to) || record.date_from! > record.date_to!) return null;
  if (value == null || (typeof value === "string" && !value.trim()) || !["number", "string"].includes(typeof value)) return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}
export function observedActionMetric(action: SearchVisibilityAction | null | undefined, metric: "impressions" | "clicks" | "orders" | "query_count") {
  if (action?.evidence?.data_source_status === "title_inference_only" || action?.data_source_status === "title_inference_only") return null;
  if (metric === "impressions" && action?.evidence?.data_source_status === "query_without_count") return null;
  return observedValue(action?.evidence?.totals?.[metric], action?.evidence);
}
export function metricText(value: number | null) {
  return value == null ? "未知" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value);
}
export function searchEvidenceText(action?: SearchVisibilityAction | null) {
  const value = observedActionMetric(action, "impressions");
  return value == null ? "依据：搜索量未知，缺少来源、日期或指标记录；现有建议不代表实测搜索结果。"
    : `依据：${action!.evidence!.source} · ${action!.evidence!.date_from} 至 ${action!.evidence!.date_to} · 搜索展示次数 ${metricText(value)}。`;
}
export function sumObserved(actions: SearchVisibilityAction[], metric: "impressions" | "clicks" | "orders" | "query_count") {
  const values = actions.map((action) => observedActionMetric(action, metric));
  return values.length && values.every((value) => value != null) ? values.reduce<number>((sum, value) => sum + value!, 0) : null;
}
export function sumReference(actions: SearchVisibilityAction[], kind: "top_seerfar_keyword_reverse" | "top_seerfar_keyword_mining") {
  if (!actions.length) return null;
  let total = 0;
  for (const action of actions) {
    const rows = action.evidence?.[kind];
    if (!rows?.length) return null;
    for (const row of rows) {
      const record = row as typeof row & MetricRecord;
      const raw = kind === "top_seerfar_keyword_mining" && "monthly_search_heat" in (row.metrics || {})
        ? (row.metrics as { monthly_search_heat?: number }).monthly_search_heat : row.metrics?.search_count ?? row.count;
      const value = observedValue(raw, record);
      if (value == null) return null;
      total += value;
    }
  }
  return total;
}
