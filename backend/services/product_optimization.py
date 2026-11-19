"""Listing-scoped facts and evidence-based local optimization; never publishes."""
import hashlib
import json
import re
import sys
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit
from pathlib import Path

from backend.db.erp import DEFAULT_ORGANIZATION_ID, database
from backend.integrations.ozon.analytics_client import validate_dates
from backend.repositories.analytics import AnalyticsRepository
from backend.repositories.local_operations import LocalOperationsRepository
from backend.services.analytics_service import decimal_value

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ozon-field-completion'))
from ozon_field_completion.service import _canonical_hashtag, _tag_matches_current_product


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def allowed_image(url):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and (parsed.hostname or '').endswith('.ozone.ru')
                and not parsed.username and not parsed.password and parsed.port in {None, 443})
    except ValueError:
        return False


class ProductOptimizationService:
    def __init__(self, root):
        self.root = root

    def source(self, store_id, listing_id, organization_id=DEFAULT_ORGANIZATION_ID):
        if organization_id != DEFAULT_ORGANIZATION_ID:
            raise PermissionError('当前工作室不能访问其他组织的商品')
        with database(self.root, readonly=True) as db:
            row = db.execute('''SELECT l.*, p.description_ru, p.brand, k.seller_sku,
                p.organization_id AS product_organization,k.organization_id AS sku_organization,
                s.display_name, s.company_name, s.organization_id AS store_organization
                FROM marketplace_listings l JOIN products p ON p.id=l.product_id
                JOIN stores s ON s.id=l.store_id LEFT JOIN skus k ON k.id=l.sku_id WHERE l.id=?''',
                (listing_id,)).fetchone()
            if row is None:
                raise KeyError('未找到商品Listing')
            if (row['store_id'] != store_id or row['organization_id'] != organization_id or row['store_organization'] != organization_id
                    or row['product_organization'] != organization_id or row['sku_organization'] not in {None, organization_id}):
                raise PermissionError('商品不属于所选店铺或当前工作室')
            platform_skus = [item[0] for item in db.execute('SELECT DISTINCT sku FROM listing_platform_skus WHERE listing_id=?', (listing_id,))]
        raw = json.loads(row['attributes_json'] or '{}')
        if not isinstance(raw, dict):
            raise ValueError('商品属性缓存格式错误')
        attributes = raw.get('attributes') or []
        if not isinstance(attributes, list) or any(not isinstance(item, dict) or not isinstance(item.get('values', []), list)
                or any(not isinstance(value, dict) for value in item.get('values', [])) for item in attributes):
            raise ValueError('商品属性缓存格式错误')
        def attr(identifier):
            return '\n'.join(str(value['value']) for item in attributes if item.get('id') == identifier
                             for value in item.get('values') or [] if isinstance(value.get('value'), str))
        images = json.loads(row['remote_images_json'] or '[]')
        if not isinstance(images, list):
            raise ValueError('商品图片缓存格式错误')
        images = [item.get('url') if isinstance(item, dict) else item for item in images]
        source = {'organization_id': organization_id, 'store_id': store_id, 'listing_id': listing_id,
                  'product_id': row['product_id'], 'offer_id': row['offer_id'], 'seller_sku': row['seller_sku'],
                  'ozon_product_id': row['ozon_product_id'],
                  'platform_skus': sorted(set(str(sku) for sku in platform_skus + [row['ozon_sku']] if sku)),
                  'title_ru': row['remote_title'] or attr(4180),
                  'description_ru': row['description_ru'] or attr(4191),
                  'tags_text': attr(23171), 'brand': row['brand'] or attr(85),
                  'attributes': attributes, 'images': list(dict.fromkeys(item for item in images if allowed_image(item))),
                  'source_ref': f'OZON_SELLER_CACHE/stores/{store_id}/listings/{listing_id}',
                  'category_id': raw.get('description_category_id'), 'type_id': raw.get('type_id'),
                  'original_transport':{key:raw[key] for key in ('barcode','barcodes','color_image','complex_attributes','depth','dimension_unit',
                      'height','pdf_list','primary_image','type_id','weight','weight_unit','width','description_category_id') if key in raw},
                  'price':row['remote_price'], 'currency':row['currency']}
        source['source_revision'] = fingerprint(source)
        source.update({'fetched_at': row['last_sync_at'], 'store_name': row['display_name'],
                       'company_name': row['company_name'], 'blocked_image_count': len(images) - len(source['images']),
                       'generation_capability': '后端模型API已接入；实际生成能力以本机安全配置与任务结果为准，规则建议仍独立标注'})
        return source

    def diagnose(self, source, date_from, date_to, evidence=None):
        validate_dates(date_from, date_to)
        repo = AnalyticsRepository(self.root)
        snapshot = repo.latest(source['store_id'], date_from, date_to) if evidence is None else evidence['snapshot']
        operations = LocalOperationsRepository(self.root)
        if operations.path.is_file():
            enhanced = operations.snapshot(f"PERFORMANCE:{source['listing_id']}", source['store_id'], date_from, date_to)
            if enhanced:
                snapshot = {**enhanced, **enhanced['data']}
        fields = {'hits_view': 'Ozon展示次数', 'session_view_pdp': '商品页访问指标（不是点击次数）',
                  'ordered_units': '订购件数（不是订单数）', 'revenue': '订购金额', 'clicks': '点击次数'}
        metrics = {name: {'label': label, 'value': None, 'state': 'NO_DATA', 'source_field': name}
                   for name, label in fields.items()}
        rows = [] if not snapshot else [row for row in snapshot['rows']
            if str(row.get('sku')) in source['platform_skus'] and date_from <= row.get('day', '') <= date_to]
        recorded = {(str(row.get('sku')), row.get('day')) for row in rows}
        repeated_rows = len(recorded) != len(rows)
        expected = ((date.fromisoformat(date_to) - date.fromisoformat(date_from)).days + 1) * len(source['platform_skus'])
        if repeated_rows:
            for metric in metrics.values():
                metric['state'] = 'INVALID_RECORD'
        elif snapshot:
            for metric in fields:
                if metric == 'clicks' or snapshot['metrics_status'].get(metric) != 'AVAILABLE':
                    continue
                values = [row.get(metric) for row in rows]
                if not values:
                    continue
                try:
                    numbers = [decimal_value(value) for value in values]
                    if any(value < 0 for value in numbers):
                        raise ValueError('Negative metric')
                    total = sum(numbers, Decimal(0))
                except ValueError:
                    metrics[metric]['state'] = 'INVALID_RECORD'
                    continue
                metrics[metric].update(value=str(total), state='OBSERVED_ZERO' if total == 0 else 'OBSERVED',
                                       sample_records=len(values))
        issues = []
        for name, label in (('title_ru', '俄文标题'), ('description_ru', '俄文描述'), ('tags_text', '关键词标签'), ('images', '商品图片')):
            if not source[name]:
                issues.append({'kind': 'CONTENT_MISSING', 'field': name, 'reason': f'已有缓存未提供{label}；请核对和补充，不代表平台原资料必然为空。'})
        for metric in ('hits_view', 'session_view_pdp', 'ordered_units'):
            if metrics[metric]['state'] == 'OBSERVED_ZERO':
                issues.append({'kind': 'OBSERVED_ZERO', 'field': metric, 'reason': f"{metrics[metric]['label']}在已有记录中为0；缺失日未补零，不能据此确认低表现原因。"})
        return {'query_period': {'date_from': date_from, 'date_to': date_to}, 'timezone': 'Europe/Moscow',
                'snapshot_period': {'date_from': snapshot['date_from'], 'date_to': snapshot['date_to']} if snapshot else None,
                'source': snapshot['source'] if snapshot else None, 'fetched_at': snapshot['fetched_at'] if snapshot else None,
                'snapshot_id': snapshot['id'] if snapshot else None, 'metrics': metrics,
                'coverage': {'recorded_sku_days': len(recorded), 'requested_sku_days': expected,
                             'ratio': str(Decimal(len(recorded)) / expected) if expected else None},
                'availability': 'INVALID_DATA' if repeated_rows else 'NO_DATA' if not any(metric['value'] is not None for metric in metrics.values()) else 'INSUFFICIENT_COVERAGE' if len(recorded) < expected else 'OBSERVED',
                'conversion': None, 'conversion_reason': '现有访客/展示与订购件数口径不构成点击到订单转化；不推算CTR/CVR。',
                'assessment': {'exposure': 'NO_BENCHMARK', 'clicks': 'NO_DATA', 'conversion': 'INCOMPATIBLE_METRICS',
                               'content': 'MISSING_FACTS' if any(item['kind'] == 'CONTENT_MISSING' for item in issues) else 'REQUIRES_REVIEW'},
                'issues': issues, 'available_snapshots': repo.available_snapshots(source['store_id']) if evidence is None else evidence['available'],
                'calculation': '匹配本店Listing平台SKU的已有逐日记录合计；缺失SKU/日期未补零，不合并跨店数据。'}

    def candidates(self, store_id, date_from, date_to, *, q='', issue='', page=1, page_size=50):
        validate_dates(date_from, date_to)
        if issue not in {'', 'CONTENT_MISSING', 'OBSERVED_ZERO', 'NO_DATA'} or page < 1 or not 1 <= page_size <= 100 or len(q) > 200:
            raise ValueError('优化候选筛选参数错误')
        from backend.services.order_sync_service import owned_store
        owned_store(self.root, store_id)
        repo = AnalyticsRepository(self.root)
        evidence = {'snapshot': repo.latest(store_id, date_from, date_to), 'available': repo.available_snapshots(store_id)}
        with database(self.root, readonly=True) as db:
            ids = [row[0] for row in db.execute('SELECT id FROM marketplace_listings WHERE store_id=? AND organization_id=? ORDER BY id',
                                              (store_id, DEFAULT_ORGANIZATION_ID))]
        result = []
        # ponytail: linear cached-catalog scan; indexed fact summary if catalog scale warrants it.
        for listing_id in ids:
            source = self.source(store_id, listing_id)
            if q and q.casefold() not in (' '.join(str(source.get(key) or '') for key in ('title_ru', 'offer_id', 'seller_sku')) + ' ' + ' '.join(source['platform_skus'])).casefold():
                continue
            diagnosis = self.diagnose(source, date_from, date_to, evidence)
            kinds = {item['kind'] for item in diagnosis['issues']}
            if issue and not (issue in kinds or issue == 'NO_DATA' and diagnosis['availability'] == 'NO_DATA'):
                continue
            result.append({'listing_id': listing_id, 'store_id': store_id, 'product_id': source['product_id'],
                'title': source['title_ru'], 'offer_id': source['offer_id'], 'platform_skus': source['platform_skus'],
                'availability': diagnosis['availability'], 'reasons': [item['reason'] for item in diagnosis['issues']],
                'metrics': diagnosis['metrics'], 'source_revision': source['source_revision']})
        return {'items': result[(page - 1) * page_size:page * page_size], 'total': len(result),
                'page': page, 'page_size': page_size, 'query_period': {'date_from': date_from, 'date_to': date_to},
                'selection_rule': '仅根据所选店铺缓存资料缺失、实际零记录或无数据筛选；不使用虚构权重或未经确认的行业阈值。'}

    def suggest(self, source, search_snapshot=None):
        items, warnings = [], []
        def add(field, before, after, basis, ref, reason, period=None, observation=None, snapshot_id=None):
            if before == after:
                return
            identity = fingerprint([field, after, ref, source['source_revision']])
            items.append({'id': identity, 'field': field, 'before': before, 'after': after,
                'basis': basis, 'source_ref': ref, 'source_revision': source['source_revision'],
                'reason': reason, 'period': period, 'uncertainty': '仅依据明确原文；未经全市场热度验证，不保证排名或销量提升。'})
            if observation is not None:
                items[-1].update(metrics_evidence={key: observation.get(key) for key in (
                    'unique_search_users', 'unique_view_users', 'order_count', 'position')},
                    source_data_revision=fingerprint([observation, period]), source_snapshot_id=snapshot_id)
        for field in ('title_ru', 'description_ru'):
            before = source.get(field) or ''
            paragraphs = list(dict.fromkeys(re.sub(r'[ \t]+', ' ', line).strip() for line in before.splitlines()))
            after = '\n'.join(paragraphs).strip()
            add(field, before, after, '商品资料推导', source['source_ref'] + '#' + field,
                '去除多余空白和完全重复的段落，保留原有词句及规格；这是格式整理，不是AI语义生成。')
        descriptive = source.get('title_ru', '') + '\n' + source.get('description_ru', '')
        context, phrases = set(), []
        for segment in re.split(r'[,.;\n]', descriptive):
            words = re.findall(r'[А-Яа-яЁё]+', segment)
            if not words:
                continue
            for start in range(len(words)):
                for width in range(1, min(5, len(words) - start) + 1):
                    tag = _canonical_hashtag(' '.join(words[start:start + width]))
                    if tag:
                        context.add(tag[1:])
            # Only complete short, provided phrases, never a generic filler bank.
            generic_heading = re.match(r'\s*(?:цвет|количество|размер|материал|комплектация|объём)\s*:', segment, re.I)
            units_only = all(word.casefold() in {'шт', 'мм', 'см', 'кг', 'г', 'л', 'мл'} for word in words)
            if 1 <= len(words) <= 5 and not generic_heading and not units_only:
                phrases.append((' '.join(words), '商品资料推导', source['source_ref'] + '#title_description', None, None, None))
        if search_snapshot and search_snapshot.get('store_id') == source['store_id']:
            for row in search_snapshot.get('data', {}).get('rows', []):
                if str(row.get('sku')) in source['platform_skus'] and isinstance(row.get('query'), str):
                    observed = search_snapshot.get('data', {}).get('source') == 'OZON_SELLER_API/v1/analytics/product-queries/details'
                    phrases.insert(0, (row['query'], '店铺实测词（非全市场热度）' if observed else '已提供词组（待验证）',
                        f"SEARCH/{search_snapshot['id']}/sku/{row['sku']}",
                        {'date_from': search_snapshot['date_from'], 'date_to': search_snapshot['date_to']}, row,
                        search_snapshot['id']))
        seen = set()
        for phrase, basis, ref, period, observation, snapshot_id in phrases:
            tag = _canonical_hashtag(phrase)
            if not tag or not context or not _tag_matches_current_product(tag, context):
                warnings.append(f'未建议无关或不符合标签规则的词组：{phrase}')
                continue
            if tag in seen:
                warnings.append(f'已合并重复标签：{tag}')
                continue
            seen.add(tag)
            add('tags', '', tag, basis, ref, f'原词组：{phrase}；与当前商品俄文内容核对后按已有标签规则整理。', period, observation, snapshot_id)
        return {'items': items[:30], 'warnings': list(dict.fromkeys(warnings)),
                'missing': '' if items else '没有可可靠生成的改写或符合规则的俄文词组；请人工补充已核实事实。',
                'source_revision': source['source_revision'], 'generation_capability': source['generation_capability']}

    def content_checks(self, source, content):
        result = []
        text = '\n'.join(str(content.get(field) or '') for field in ('title_ru', 'description_ru'))
        text += '\n' + '\n'.join(module.get('title', '') + '\n' + module.get('body', '')
                                  for module in (content.get('rich_content') or {}).get('modules', []))
        words = re.findall(r'[А-Яа-яЁёA-Za-z]+', text.casefold())
        repeated = sorted({word for word in words if len(word) > 3 and words.count(word) >= 4})
        if repeated:
            result.append({'kind': 'STUFFING', 'reason': '重复词可能堆砌，请人工核对：' + ', '.join(repeated)})
        original = source.get('title_ru', '') + ' ' + source.get('description_ru', '') + ' ' + ' '.join(
            str(value.get('value') or '') for attribute in source.get('attributes', []) for value in attribute.get('values') or [])
        for number in sorted(set(re.findall(r'\d+(?:[.,]\d+)?', text)) - set(re.findall(r'\d+(?:[.,]\d+)?', original))):
            result.append({'kind': 'SPEC_UNVERIFIED', 'reason': f'新数值 {number} 未在当前资料中找到；需确认规格和SKU一致。'})
        unit_pattern = r'\d+(?:[.,]\d+)?\s*(?:шт|мл|мм|см|кг|л|г)\b'
        for spec in sorted(set(re.findall(unit_pattern, text.casefold())) - set(re.findall(unit_pattern, original.casefold()))):
            result.append({'kind': 'SPEC_CONFLICT', 'reason': f'规格表达 {spec} 未与当前来源的同单位规格对应；请核对数量/尺寸，不能用其他数字作依据。'})
        tags = content.get('tags') or []
        if len(set(tags)) != len(tags):
            result.append({'kind': 'TAG_DUPLICATE', 'reason': '重复标签会在保存时去重，请核对变化。'})
        context_words = re.findall(r'[а-яё]+', original.casefold())
        for tag in tags:
            body = tag.lstrip('#').casefold()
            if not any(len(word) > 3 and (word in body or body in word) for word in context_words):
                result.append({'kind': 'TAG_UNRELATED', 'reason': f'{tag} 缺少当前原文的相关性依据；手工标签需要人工确认，不会冒充建议来源。'})
        known_brand = str(source.get('brand') or '').casefold()
        for name in ('apple', 'huawei', 'samsung', 'xiaomi', 'nike', 'adidas', 'bosch'):
            if re.search(r'\b' + name + r'\b', text, re.I) and name not in known_brand and name not in original.casefold():
                result.append({'kind': 'BRAND_UNVERIFIED', 'reason': f'品牌 {name} 没有当前资料依据，请核对品牌/兼容关系，不能冒称品牌商品。'})
        if not content.get('description_ru'):
            result.append({'kind': 'CONTENT_MISSING', 'reason': '俄文描述为空，尚未形成完整文案。'})
        if not re.search(r'[А-Яа-яЁё]', content.get('title_ru', '')):
            result.append({'kind': 'CONTENT_MISSING', 'reason': '标题没有俄文内容。'})
        return result

    def validate_content(self, source, payload, prior, search_snapshot=None):
        allowed = {'title_ru', 'description_ru', 'tags', 'keyword_selection', 'rich_content', 'baseline_period', 'applied_at', 'image_selection'}
        if set(payload) - allowed:
            raise ValueError('优化内容包含不允许的字段；依据由服务器重建')
        for field, limit in (('title_ru', 500), ('description_ru', 6000)):
            if not isinstance(payload.get(field), str) or len(payload[field]) > limit:
                raise ValueError('标题或描述格式/长度错误')
        tags = payload.get('tags')
        if not isinstance(tags, list) or len(tags) > 30 or any(not isinstance(tag, str) or _canonical_hashtag(tag) != tag.casefold() for tag in tags):
            raise ValueError('标签必须符合现有俄文规则，最多30个，不允许无关格式填充')
        tags = list(dict.fromkeys(tag.casefold() for tag in tags))
        suggestions = {item['id']: item for item in self.suggest(source, search_snapshot)['items'] if item['field'] == 'tags'}
        selection = payload.get('keyword_selection', [])
        if not isinstance(selection, list) or any(not isinstance(identifier, str) or identifier not in suggestions or suggestions[identifier]['after'] not in tags for identifier in selection):
            raise ValueError('建议选择与最终标签或当前商品来源不一致')
        retained = []
        selected_tags = {suggestions[identifier]['after'] for identifier in selection}
        for basis in prior.get('keyword_basis') or []:
            if basis['tag'] not in tags or basis['tag'] in selected_tags:
                continue
            old_period = basis.get('period')
            old_search = LocalOperationsRepository(self.root).snapshot(f"SEARCH:{source['listing_id']}", source['store_id'],
                old_period['date_from'], old_period['date_to']) if old_period else None
            current = next((item for item in self.suggest(source, old_search)['items'] if item['field'] == 'tags' and item['after'] == basis['tag']), None)
            omitted = {'id', 'source_ref', 'source_snapshot_id'} if old_period else set()
            if not current or basis.get('source_revision') != source['source_revision'] or {key: value for key, value in basis.items() if key not in omitted} != {key: value for key, value in {**current, 'tag': current['after']}.items() if key not in omitted}:
                raise RuntimeError('关键词来源已变化，请重新查看建议并确认，或删除对应标签')
            retained.append(basis)
        retained += [{**suggestions[identifier], 'tag': suggestions[identifier]['after']} for identifier in dict.fromkeys(selection)]
        rich = payload.get('rich_content')
        image_selection = payload.get('image_selection',prior.get('image_selection',source['images']))
        if not isinstance(image_selection,list) or len(image_selection)>30 or any(not isinstance(ref,str) for ref in image_selection) or len(set(image_selection))!=len(image_selection):
            raise ValueError('选图必须是最多30张不重复的当前商品图片')
        for reference in image_selection:
            if reference not in source['images']:
                from backend.services.listing_image_tools import asset_for
                asset_for(self.root,source,reference)
        if not isinstance(rich, dict) or set(rich) != {'modules'} or not isinstance(rich['modules'], list) or len(rich['modules']) > 12:
            raise ValueError('富内容必须是最多12个图文模块')
        for module in rich['modules']:
            if not isinstance(module, dict) or set(module) != {'title', 'body', 'image_path'} or any(not isinstance(value, str) for value in module.values()):
                raise ValueError('富内容模块字段错误')
            if module['image_path'] and str(module['image_path']).startswith('asset://'):
                from backend.services.listing_image_tools import asset_for
                asset_for(self.root,source,module['image_path'])
            elif module['image_path'] and module['image_path'] not in source['images']:
                raise ValueError('富内容图片不属于当前店铺商品')
            if len(module['title']) > 500 or len(module['body']) > 6000:
                raise ValueError('富内容超长或图片不属于当前店铺商品')
        period = payload.get('baseline_period')
        if period is not None:
            if not isinstance(period, dict) or set(period) != {'date_from', 'date_to'} or any(not isinstance(value, str) for value in period.values()):
                raise ValueError('指标基线日期格式错误')
            validate_dates(period['date_from'], period['date_to'])
        applied = payload.get('applied_at', '')
        if not isinstance(applied, str):
            raise ValueError('人工平台应用日期格式错误')
        if applied and (not isinstance(applied, str) or date.fromisoformat(applied).isoformat() != applied):
            raise ValueError('人工平台应用日期格式错误')
        original_tags = {tag.casefold() for tag in re.split(r'[,;\s]+', source.get('tags_text', '')) if tag}
        suggested_tags = {item['tag'] for item in retained}
        origins = [{'tag': tag, 'kind': 'SUGGESTED' if tag in suggested_tags else 'EXISTING_UNVERIFIED' if tag in original_tags else 'MANUAL'} for tag in tags]
        scope=prior.get('changed_fields')
        changed_fields=sorted(set(scope)|{key for key in ('title_ru','description_ru','tags','rich_content','image_selection') if payload.get(key,prior.get(key))!=prior.get(key)}) if isinstance(scope,list) else ['title_ru','description_ru','tags','rich_content','images']
        return {'title_ru': payload['title_ru'], 'description_ru': payload['description_ru'], 'tags': tags, 'tag_origins': origins, 'changed_fields':changed_fields,
                'keyword_basis': retained, 'rich_content': rich, 'baseline_period': period, 'applied_at': applied,
                'generation_source': 'LOCAL_RULES_AND_MANUAL', 'checks': self.content_checks(source, payload), 'image_selection':image_selection}
