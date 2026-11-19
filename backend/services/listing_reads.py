"""SKU-restricted, manual read adapters. No scheduler or marketplace writes."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.integrations.ozon.analytics_client import OzonAnalyticsClient, validate_dates
from backend.integrations.ozon.base_client import OzonBaseClient
from backend.repositories.local_operations import LocalOperationsRepository
from backend.services.analytics_service import AnalyticsService, decimal_value

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'market-intelligence'))
from market_intelligence.ozon_client import OzonAnalyticsReadOnlyClient


def actual_day(raw):
    if not isinstance(raw, str):
        raise ValueError('搜索词返回缺少实际数据周期')
    if 'T' in raw:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('搜索词周期时区不明确')
        return parsed.astimezone(timezone(timedelta(hours=3))).date().isoformat()
    validate_dates(raw, raw)
    return raw


class ListingReadService:
    def __init__(self, root, client_id, api_key, *, transport=None):
        self.repo = LocalOperationsRepository(root)
        self.base = OzonBaseClient(client_id, api_key, transport=transport)
        self.search_client = OzonAnalyticsReadOnlyClient(client_id, api_key, transport=self.base._post_json)
        self.analytics_client = OzonAnalyticsClient(client_id, api_key, transport=transport)

    def search(self, source, date_from, date_to):
        validate_dates(date_from, date_to)
        if not source['platform_skus']:
            raise ValueError('当前商品缺少已核实Ozon SKU，不能读取搜索词')
        start = date_from + 'T00:00:00+03:00'
        end = date_to + 'T23:59:59+03:00'
        records, summaries, actual_period = [], [], None
        for detail in (False, True):
            page, expected_pages = 0, None
            while True:
                function = self.search_client.get_product_query_details if detail else self.search_client.get_product_queries
                payload = {'date_from': start, 'date_to': end, 'page': page, 'page_size': 100}
                if detail:
                    payload['limit_by_sku'] = 15
                response = function(source['platform_skus'], **payload)
                period = response.get('analytics_period')
                if not isinstance(period, dict):
                    raise ValueError('搜索词响应缺少实际周期')
                returned = (actual_day(period.get('date_from')), actual_day(period.get('date_to')))
                if returned[0] < date_from or returned[1] > date_to or returned[0] > returned[1] or (actual_period and actual_period != returned):
                    raise ValueError('搜索词响应周期与查询范围不一致，不能混用')
                actual_period = returned
                pages = response.get('page_count')
                rows = response.get('queries' if detail else 'items')
                if not isinstance(pages, int) or isinstance(pages, bool) or pages < 0 or not isinstance(rows, list):
                    raise ValueError('搜索词分页字段错误')
                if expected_pages is not None and pages != expected_pages:
                    raise ValueError('搜索词总页数发生变化，请重新读取')
                expected_pages = pages
                for row in rows:
                    if not isinstance(row, dict) or str(row.get('sku')) not in source['platform_skus']:
                        raise ValueError('搜索词响应包含非所选商品SKU')
                    if detail and (not isinstance(row.get('query'), str) or not row['query'].strip()):
                        raise ValueError('搜索词文本缺失')
                    safe = {'sku': str(row['sku'])}
                    for field in ('query', 'currency', 'offer_id', 'name'):
                        if isinstance(row.get(field), str):
                            safe[field] = row[field]
                    for field in ('unique_search_users', 'unique_view_users', 'order_count', 'position', 'view_conversion', 'gmv'):
                        value = row.get(field)
                        if value is None:
                            safe[field] = None
                        else:
                            numeric = decimal_value(value)
                            if numeric < 0:
                                raise ValueError('搜索词指标为非法负值')
                            safe[field] = int(numeric) if field in {'unique_search_users', 'unique_view_users', 'order_count'} else str(numeric)
                    target = records if detail else summaries
                    if any(previous.get('sku') == safe['sku'] and (not detail or previous.get('query') == safe['query']) for previous in target):
                        raise ValueError('搜索词分页存在重复记录，不能当作完整结果')
                    target.append(safe)
                page += 1
                if page >= pages:
                    break
        kind = f"SEARCH:{source['listing_id']}"
        self.repo.save_snapshot(kind, source['store_id'], actual_period[0], actual_period[1],
            {'source': 'OZON_SELLER_API/v1/analytics/product-queries/details', 'listing_id': source['listing_id'],
             'requested_skus': source['platform_skus'], 'query_period': {'date_from': date_from, 'date_to': date_to},
             'rows': records, 'summaries': summaries, 'completeness': 'TOP15_PER_SKU',
             'limitation': '每SKU最多15个查询；非全量关键词或全市场热度，人数不是曝光/点击次数。'})
        return self.repo.snapshot(kind, source['store_id'], actual_period[0], actual_period[1])

    def performance(self, source, date_from, date_to):
        validate_dates(date_from, date_to)
        if not source['platform_skus']:
            raise ValueError('商品缺少已核实Ozon SKU，不能读取分析')
        metrics = ('revenue', 'ordered_units', 'hits_view', 'session_view_pdp')
        service = AnalyticsService(None, self.analytics_client)
        rows = []
        for sku in source['platform_skus']:
            current = service._read_pages(date_from, date_to, metrics, None, sku=sku)
            if any(row['sku'] != sku for row in current.values()):
                raise ValueError('分析响应包含非所选SKU，拒绝保存')
            rows.extend(current.values())
        kind = f"PERFORMANCE:{source['listing_id']}"
        self.repo.save_snapshot(kind, source['store_id'], date_from, date_to,
            {'source': 'OZON_SELLER_API/v1/analytics/data', 'listing_id': source['listing_id'],
             'requested_skus': source['platform_skus'], 'rows': rows, 'metrics_status': {key: 'AVAILABLE' for key in metrics},
             'completeness': 'COMPLETE', 'limitation': '仅所选Listing平台SKU；访问/件数口径不同，不推算点击或订单转化。'})
        return self.repo.snapshot(kind, source['store_id'], date_from, date_to)
