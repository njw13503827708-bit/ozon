export interface ReviewRow {
  id: string;
  organization_id: string;
  platform: string;
  store_id: string;
  external_id: string | null;
  product_id: string | null;
  listing_id: number | null;
  mapping_status: "MAPPED" | "UNMAPPED";
  text: string;
  rating: number;
  reply_status: "UNKNOWN" | "UNREPLIED" | "REPLIED";
  published_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReviewDetail extends ReviewRow {
  media: { id: number; media_type: "IMAGE" | "VIDEO"; source_url: string; external_id: string | null }[];
}

export interface ReplyDraft {
  id: string;
  review_id: string;
  organization_id: string;
  platform: string;
  store_id: string;
  content: string;
  source: "MANUAL" | "AI_SUGGESTION";
  status: "DRAFT" | "DISCARDED";
  created_at: string;
  updated_at: string;
}

export interface ReviewPage {
  items: ReviewRow[];
  total: number;
  page: number;
  page_size: number;
}
