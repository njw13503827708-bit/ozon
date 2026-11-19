"""Read-only FBS snapshots, privacy-minimal normalization and local queries."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.db.erp import DEFAULT_ORGANIZATION_ID, database
from backend.repositories.erp import ErpRepository
from backend.repositories.local_operations import LocalOperationsRepository
from backend.integrations.ozon.analytics_client import validate_dates
from backend.services.analytics_service import decimal_value


def owned_store(root, store_id):
    store = ErpRepository(root).get_store(store_id)
    if store is None:
        raise KeyError(store_id)
    if store['organization_id'] != DEFAULT_ORGANIZATION_ID or store['platform'] != 'OZON':
        raise PermissionError('不能访问外组织或非Ozon店铺数据')
    return store


def normalize_posting(posting):
    if not isinstance(posting, dict) or not isinstance(posting.get('posting_number'), str) or not posting['posting_number']:
        raise ValueError('订单货件标识格式错误')
    products = posting.get('products')
    if not isinstance(products, list) or not products:
        raise ValueError('订单商品行缺失')
    result = {'posting_number': posting['posting_number'], 'order_number': str(posting.get('order_number') or posting.get('order_id') or ''),
              'status': str(posting.get('status') or 'UNKNOWN'), 'created_at': posting.get('created_at'),
              'in_process_at': posting.get('in_process_at'), 'date': None, 'items': []}
    stamp = posting.get('created_at') or posting.get('in_process_at')
    if stamp:
        try:
            parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                result['date'] = parsed.astimezone(timezone(timedelta(hours=3))).date().isoformat()
        except (ValueError, TypeError):
            raise ValueError('订单日期格式错误') from None
    seen = {}
    for product in products:
        if not isinstance(product, dict) or isinstance(product.get('sku'), bool) or not str(product.get('sku') or '').isdigit():
            raise ValueError('订单SKU格式错误')
        quantity = product.get('quantity')
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise ValueError('订单数量必须为正整数')
        raw_price = product.get('price')
        if not isinstance(raw_price, dict) or not isinstance(raw_price.get('currency'), str) or not raw_price['currency']:
            raise ValueError('v4订单价格或币种缺失')
        price = decimal_value(raw_price.get('amount'))
        if price < 0:
            raise ValueError('订单价格不能为负')
        item = {'sku': str(product['sku']), 'offer_id': str(product.get('offer_id') or ''),
                'name': str(product.get('name') or ''), 'quantity': quantity,
                'price': str(price), 'currency': raw_price['currency'], 'total': str(price * quantity)}
        key = (item['sku'], item['offer_id'])
        if key in seen and seen[key] != item:
            raise ValueError('重复SKU商品行相互冲突，请重新读取')
        seen[key] = item
    result['items'] = list(seen.values())
    return result


class OrderSyncService:
    def __init__(self, root, client=None):
        self.root, self.client = root, client
        self.repo = LocalOperationsRepository(root)

    def run(self, store_id, date_from, date_to, job_id=None):
        owned_store(self.root, store_id)
        validate_dates(date_from, date_to)
        records, cursors, cursor = {}, set(), ''
        if job_id:
            self.repo.update_job(job_id, status='RUNNING', message='正在读取自有店铺FBS/rFBS货件')
        while True:
            if job_id and self.repo.job(job_id)['cancel_requested']:
                raise InterruptedError('订单读取取消')
            response = self.client.list_postings(date_from, date_to, cursor=cursor)
            if not isinstance(response.get('has_next'), bool) or not isinstance(response.get('postings'), list):
                raise ValueError('v4订单响应字段不符合已核实契约')
            for posting in response['postings']:
                record = normalize_posting(posting)
                key = record['posting_number']
                if key in records and records[key] != record:
                    raise ValueError('分页货件记录冲突，请重新读取')
                records[key] = record
            if job_id:
                self.repo.update_job(job_id, status='RUNNING', progress=len(records), message=f'已读取{len(records)}个货件，尚未完成全部页面')
            if not response['has_next']:
                break
            next_cursor = response.get('cursor')
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in cursors:
                raise ValueError('订单下一页游标缺失或重复；没有覆盖上次完整缓存')
            cursors.add(next_cursor)
            cursor = next_cursor
        if job_id and self.repo.job(job_id)['cancel_requested']:
            raise InterruptedError('订单读取取消')
        identifier = self.repo.save_snapshot('ORDERS', store_id, date_from, date_to,
            {'rows': list(records.values()), 'source': 'OZON_SELLER_API/v4/posting/fbs/list',
             'completeness': 'COMPLETE', 'timezone': 'Europe/Moscow', 'shipping_modes': ['FBS', 'rFBS']})
        if job_id:
            self.repo.update_job(job_id, status='SUCCEEDED', progress=len(records), message=f'完整读取{len(records)}个FBS/rFBS货件；未执行平台写操作')
        return identifier

    def read(self, store_id, date_from, date_to, *, q='', status='', page=1, page_size=50):
        owned_store(self.root, store_id)
        validate_dates(date_from, date_to)
        if page < 1 or not 1 <= page_size <= 100 or len(q) > 200:
            raise ValueError('订单查询参数错误')
        snapshot = self.repo.snapshot('ORDERS', store_id, date_from, date_to)
        response = {'store_id': store_id, 'query_period': {'date_from': date_from, 'date_to': date_to},
                    'availability': 'NOT_SYNCED', 'items': [], 'total': 0, 'page': page, 'page_size': page_size,
                    'source': None, 'fetched_at': None, 'snapshot_id': None, 'snapshot_period': None,
                    'shipping_modes': ['FBS', 'rFBS'], 'limitation': 'FBO尚未核实/接入；订单中心不执行发货、退款、取消等平台写操作。'}
        if snapshot is None:
            return response
        rows = [row for row in snapshot['data']['rows'] if row['date'] is None or date_from <= row['date'] <= date_to]
        filtered = [row for row in rows if (not status or row['status'] == status) and (not q or q.casefold() in str(row).casefold())]
        response.update(availability='AVAILABLE', source=snapshot['data']['source'], fetched_at=snapshot['fetched_at'],
            snapshot_id=snapshot['id'], snapshot_period={'date_from': snapshot['date_from'], 'date_to': snapshot['date_to']},
            total=len(filtered), items=sorted(filtered, key=lambda row: (row['created_at'] or '', row['posting_number']), reverse=True)[(page - 1) * page_size:page * page_size])
        with database(self.root, readonly=True) as db:
            mappings = {}
            for row in db.execute('''SELECT l.id, l.offer_id,l.ozon_sku,p.sku FROM marketplace_listings l
                LEFT JOIN listing_platform_skus p ON p.listing_id=l.id
                WHERE l.store_id=? AND l.organization_id=?''', (store_id, DEFAULT_ORGANIZATION_ID)):
                for sku in (row['ozon_sku'], row['sku']):
                    if sku:
                        mappings.setdefault(str(sku), set()).add(row['id'])
        for posting in response['items']:
            for item in posting['items']:
                matches = sorted(mappings.get(item['sku'], set()))
                item['listing_id'] = matches[0] if len(matches) == 1 else None
                item['mapping_status'] = 'MATCHED' if len(matches) == 1 else 'AMBIGUOUS' if matches else 'NOT_FOUND'
        return response
