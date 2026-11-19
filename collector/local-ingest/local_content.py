"""Read-only, deterministic suggestions and local rich-content validation."""
import json
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ozon-field-completion'))
from ozon_field_completion.service import (
    _blocked_tag_terms, _canonical_hashtag,
    _tag_matches_current_product,
)


def keyword_suggestions(product_dir: Path) -> dict:
    items, warnings, seen = [], [], set()
    source_hash = hashlib.sha256()
    for name in ('input/source.json', 'output/title-ru.json', 'output/description-ru.json',
                 'output/copy-ru.json', 'output/ozon-ecommerce-design.json',
                 'output/keyword-research-ru.json', 'output/keywords-ru.json'):
        path = product_dir / name
        source_hash.update(name.encode())
        source_hash.update(path.read_bytes() if path.is_file() else b'absent')
    revision = source_hash.hexdigest()
    context = set()
    # Keyword lists cannot establish their own relevance. Only descriptive copy does.
    for name in ('title-ru.json', 'description-ru.json', 'copy-ru.json', 'ozon-ecommerce-design.json'):
        path = product_dir / 'output' / name
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            continue
        for section in (data, data.get('listing') or {}):
            if not isinstance(section, dict):
                continue
            for field in ('title_ru', 'short_title', 'product_type_ru', 'description_ru', 'usage_scenarios_ru'):
                raw = section.get(field) or []
                for text in ([raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []):
                    if not isinstance(text, str):
                        continue
                    words = re.findall(r'[А-Яа-яЁё]+', text)
                    for start in range(len(words)):
                        for width in range(1, 6):
                            tag = _canonical_hashtag(' '.join(words[start:start + width]))
                            if tag:
                                context.add(tag[1:])
    blocked = _blocked_tag_terms(product_dir)
    # Exact supplied phrases only: no filler bank, translation or invented market data.
    fields = ('title_ru', 'short_title', 'product_type_ru', 'keywords_ru', 'keywords',
              'primary_keywords', 'secondary_keywords', 'usage_scenarios_ru')
    for name in ('title-ru.json', 'copy-ru.json', 'ozon-ecommerce-design.json',
                 'keyword-research-ru.json', 'keywords-ru.json'):
        path = product_dir / 'output' / name
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            continue
        sections = [('', data)]
        if isinstance(data.get('listing'), dict):
            sections.append(('listing.', data['listing']))
        for prefix, section in sections:
            for field in fields:
                values = section.get(field, [])
                scalar = isinstance(values, str)
                if isinstance(values, str):
                    values = [values]
                if not isinstance(values, list):
                    continue
                for index, phrase in enumerate(values):
                    if not isinstance(phrase, str):
                        continue
                    tag = _canonical_hashtag(phrase, blocked)
                    if not tag:
                        warnings.append(f'不符合标签规则，未建议：{phrase}')
                        continue
                    # Empty product context cannot establish relevance of supplied keywords.
                    if not context or not _tag_matches_current_product(tag, context):
                        warnings.append(f'可能无关，缺少商品资料支持，未建议：{phrase}')
                        continue
                    if tag in seen:
                        warnings.append(f'重复候选已合并：{phrase} → {tag}')
                        continue
                    seen.add(tag)
                    suffix = '' if scalar else f'[{index}]'
                    ref = f'products/{product_dir.name}/output/{name}#{prefix}{field}{suffix}'
                    items.append({'tag': tag, 'keyword': phrase, 'original': phrase,
                                  'source_revision': revision,
                                  'source_ref': ref, 'basis': '商品资料推导' if name not in
                                  ('keyword-research-ru.json', 'keywords-ru.json') else '已提供关键词资料（热度未知）',
                                  'reason': '直接复用已有俄文词组；按现有标签规则规范化并与商品资料核对。',
                                  'metrics': {'search_volume': None, 'sales': None, 'rank': None}})
    return {'items': items[:30], 'warnings': list(dict.fromkeys(warnings)),
            'missing': '' if items else '缺少可核对且符合标签长度的俄文商品类型、用途或关键词词组；请先补充资料。',
            'constraints': '最多30个；#加俄文字母；总长度3–30；不填充无关词。'}


def keyword_basis_view(product_dir: Path, draft: dict) -> list:
    candidates = {item['tag']: item for item in keyword_suggestions(product_dir)['items']}
    tags = draft.get('tags') or []
    result = []
    for item in draft.get('keyword_basis') or []:
        if not isinstance(item, dict) or item.get('tag') not in tags:
            continue
        current = candidates.get(item['tag'])
        valid = bool(current and item.get('source_revision') and all(item.get(key) == value for key, value in current.items()))
        result.append({**item, 'valid': valid, 'warning': '' if valid else '关键词依据已失效或缺少来源版本，请重新查看建议并确认，或删除对应标签。'})
    return result


def reconcile_keyword_basis(product_dir: Path, before: dict, payload: dict, final_tags: list) -> dict:
    candidates = {item['tag']: item for item in keyword_suggestions(product_dir)['items']}
    selection = payload.get('keyword_selection', [])
    if not isinstance(selection, list) or any(not isinstance(tag, str) or tag not in candidates or tag not in final_tags for tag in selection):
        raise ValueError('建议标签必须存在于最终标签中，且具备当前商品依据')
    if any(payload.get('keyword_source_revision') != candidates[tag]['source_revision'] for tag in selection):
        raise RuntimeError('关键词来源已变化，请重新查看建议并确认')
    retained = {}
    for item in keyword_basis_view(product_dir, {**before, 'tags': final_tags}):
        if item['tag'] in selection:
            continue
        if not item['valid']:
            raise RuntimeError(item['warning'])
        retained[item['tag']] = {key:value for key,value in item.items() if key not in {'valid', 'warning'}}
    for tag in selection:
        retained[tag] = candidates[tag]
    return {'keyword_selection': list(retained), 'keyword_basis': list(retained.values())}


def validate_rich_content(product_dir: Path, root: Path, value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {'modules'} or not isinstance(value['modules'], list):
        raise ValueError('富内容必须包含图文模块列表')
    if len(value['modules']) > 12:
        raise ValueError('本地富内容最多12个模块')
    modules = []
    for module in value['modules']:
        if not isinstance(module, dict) or set(module) != {'title', 'body', 'image_path'}:
            raise ValueError('富内容模块字段错误')
        if not all(isinstance(module[k], str) for k in module):
            raise ValueError('富内容字段必须是文本')
        if len(module['title']) > 500 or len(module['body']) > 6000:
            raise ValueError('富内容标题或正文超过本地编辑长度限制')
        image_path = module['image_path']
        if image_path:
            image = (root / image_path).resolve()
            owned = product_dir.resolve()
            if not image.is_relative_to(owned) or not image.is_file() or image.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
                raise ValueError('富内容只能使用本商品已有的本地图片')
        modules.append(dict(module))
    return {'modules': modules}
