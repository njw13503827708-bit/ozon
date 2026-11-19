export interface ErpProductRow {
  ozon_sku: string | null;
  platform_skus: Array<{ sku: string; source: string; shipment_type: string }>;
  listing_id: number | null;
  product_id: string;
  sku_id: string | null;
  sku: string | null;
  offer_id: string | null;
  ozon_product_id: string | null;
  title: string;
  store_id: string | null;
  store: string | null;
  image_url: string | null;
  price: number | null;
  currency: string | null;
  listing_status: string | null;
  presence_status: string | null;
  attribute_status: string | null;
  last_sync: string | null;
}

export interface ErpProductPage {
  items: ErpProductRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface ErpProductDetail {
  product_id: string;
  title: string;
  skus: Array<{ sku_id: string; sku: string | null }>;
  listings: ErpProductRow[];
}
