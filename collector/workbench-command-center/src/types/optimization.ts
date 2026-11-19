export type RichModule = { title: string; body: string; image_path: string };
export type OptimizationContent = {
  title_ru: string; description_ru: string; tags: string[];
  rich_content: { modules: RichModule[] };
  baseline_period?: { date_from: string; date_to: string } | null;
  applied_at?: string;
  keyword_selection?: string[];
  image_selection?: string[];
};
export type OptimizationSuggestion = {
  id: string; field: "title_ru" | "description_ru" | "tags"; before: string; after: string;
  source_ref: string; source_revision: string; reason: string; basis: string; uncertainty: string;
  period: { date_from: string; date_to: string } | null;
};
export type OptimizationVersion = {
  version: number; source_revision: string; source_valid: boolean; warning: string;
  saved_at: string; reviewed_at?: string; status: "DRAFT" | "REVIEWED";
  content: OptimizationContent & { keyword_basis: Array<OptimizationSuggestion & {tag: string}>;
    checks: Array<{kind: string; reason: string}> };
};
export type OptimizationTask = {
  id: string; status: string; progress: number; message: string;
  context: {source: {source_revision: string}; expected_version: number};
  result: {content?: OptimizationContent; strategy?: {summary: string}; provider_evidence?: {model: string; usage: Record<string, unknown>; cost: number | null}};
  version: OptimizationVersion | Record<string, never>;
};
export type AIProviderSettings = {base_url: string; api_mode: "responses" | "chat_completions"; model: string;
  models: string[]; key_configured: boolean; image_model: string};
export type OptimizationPublicationReview = {review_hash: string; items: Array<{store_id: string; listing_id: number; version: number; offer_id: string; operation: string; before: unknown; after: unknown; price_policy: string}>};
export type OptimizationData = {
  assets?: Array<{reference: string; preview_url: string; qc_state: string; public_url?: string | null}>;
  source: {store_id: string; listing_id: number; product_id: string; offer_id: string; seller_sku: string;
    title_ru: string; description_ru: string; tags_text: string; brand: string; images: string[];
    platform_skus: string[]; attributes: Array<{id: number; values: Array<{value?: string}>}>;
    source_revision: string; source_ref: string; fetched_at: string | null;
    generation_capability: string; company_name: string; store_name: string};
  diagnosis: {query_period: {date_from: string; date_to: string}; availability: string;
    source: string | null; fetched_at: string | null; calculation: string;
    coverage: {recorded_sku_days: number; requested_sku_days: number; ratio: string | null};
    conversion: string | null; conversion_reason: string;
    metrics: Record<string, {label: string; value: string | null; state: string}>;
    issues: Array<{kind: string; field: string; reason: string}>;
    available_snapshots: Array<{date_from: string; date_to: string; fetched_at: string}>};
  suggestions: {items: OptimizationSuggestion[]; warnings: string[]; missing: string; generation_capability: string};
  versions: OptimizationVersion[];
};
export type OrderItem = {sku: string; offer_id: string; name: string; quantity: number;
  price: string; currency: string; total: string; listing_id?: number | null; mapping_status?: string};
export type OrderPosting = {posting_number: string; order_number: string; status: string;
  date: string | null; created_at: string | null; items: OrderItem[]};
export type OrderData = {store_id: string; query_period: {date_from: string; date_to: string};
  availability: string; source: string | null; fetched_at: string | null; total: number;
  page: number; page_size: number; items: OrderPosting[]; limitation: string; read_authorized: boolean};
export type OrderJob = {id: string; status: string; progress: number; message: string};
export type OptimizationCandidates = {items: Array<{listing_id: number; store_id: string; product_id: string;
  title: string; offer_id: string; platform_skus: string[]; availability: string; reasons: string[]}>;
  total: number; page: number; page_size: number; selection_rule: string};
