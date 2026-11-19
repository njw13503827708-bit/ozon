export type AnalyticsMetric = "revenue" | "ordered_units" | "hits_view" | "session_view_pdp" | "hits_tocart" | "returns" | "cancellations" | "position_category";
export type AnalyticsRow = Partial<Record<AnalyticsMetric, string | null>> & {
  sku?: string; name?: string; day?: string; class?: string;
  contribution_percent?: string; cumulative_percent?: string;
  positions?: Array<{ day: string; value: string }>;
};
export interface AnalyticsData {
  store_id: string; date_from: string; date_to: string; timezone: string;
  currency: string; source: string; fetched_at: string | null;
  available_snapshots?: Array<{ date_from: string; date_to: string; fetched_at: string; source: string }>;
  availability: string; completeness: string;
  last_refresh?: { id: string; status: string; message: string | null; finished_at: string | null } | null;
  totals: Partial<Record<AnalyticsMetric, string | null>>;
  metrics_status: Partial<Record<AnalyticsMetric, string>>;
  items: AnalyticsRow[]; total: number; page: number; page_size: number;
  trend: AnalyticsRow[]; abc: AnalyticsRow[]; leaders: AnalyticsRow[];
  funnel: { view_to_cart?: string | null; ordered_units_per_cart?: string | null };
}
