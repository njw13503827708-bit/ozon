export interface StoreSummary {
  id: string;
  display_name: string;
  platform: string;
  enabled: boolean;
  seller_id: string | null;
  company_name: string | null;
  connection_status: string;
  product_read_allowed: boolean | null;
  last_connected_at: string | null;
  last_product_sync_at: string | null;
  product_count: number;
  currency: string;
  listing_count: number;
  analytics_fetched_at: string | null;
}
