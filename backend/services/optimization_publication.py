"""Frozen UPDATE reviews and existing durable importer intents; no inventory writes."""
import copy
import json
import hashlib
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

from backend.db.erp import DEFAULT_ORGANIZATION_ID
from backend.repositories.local_operations import LocalOperationsRepository
from backend.services.product_optimization import ProductOptimizationService, fingerprint
from scripts.batch_upload_approval import batch_upload_lock
from scripts.pipeline_runtime import write_json_atomic

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'ozon-uploader'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'ozon-adapter'))
from ozon_uploader.service import _submit_import_once, _assert_update_targets
from ozon_uploader.client import OzonWriteClient
from ozon_adapter.config import OzonConfig


def client_for(root, store_id, *, for_read=False):
    if not for_read and os.getenv('ERP_RUNTIME_MODE') != 'LIVE':
        raise RuntimeError('只有LIVE模式和最终整批确认才允许提交；当前未调用平台写入')
    from scripts.workbench_stores import load_registry, read_secret
    registry = load_registry(root)
    shop = next((shop for shop in registry.get('shops',[]) if shop['id']==store_id and shop.get('enabled',True)),None)
    if not shop:
        raise PermissionError('目标店铺未配置或已禁用')
    try:
        values = read_secret(root,shop)
    except OSError:
        raise ValueError('店铺凭证不可读（运行身份与文件属主不匹配），未发请求')
    if not values.get(shop['client_id_env']) or not values.get(shop['api_key_env']):
        raise ValueError('店铺后台凭证缺失，未发请求')
    config = OzonConfig(client_id=values.get(shop['client_id_env'],''),api_key=values.get(shop['api_key_env'],''),shop_name=shop['name'])
    return OzonWriteClient(config,allow_production_write=True)


def _base(root):
    return Path(root)/'runtime/optimization-publication'


def acknowledged_task(work,target):
    path=work/'output/ozon-write-intent.json'
    if not path.is_file():return None
    intent=json.loads(path.read_text(encoding='utf-8'));task=intent.get('task_id')
    if (intent.get('state')=='ACKNOWLEDGED' and intent.get('action')=='update'
            and intent.get('offer_ids')==[target['offer_id']] and intent.get('request_hash')==fingerprint({'items':[target['payload']]})
            and type(task) is int and task>0):return task
    return None


def _preflight(client, item):
    from backend.services.listing_image_tools import public_image_bytes
    for evidence in item.get('image_evidence',[])+item.get('rich_image_evidence',[]):
        if evidence.get('sha256') and hashlib.sha256(public_image_bytes(evidence['public_url'])).hexdigest()!=evidence['sha256']:
            raise RuntimeError('最终图片公网内容已变化，整批未发送')
    live = client.get_products_info([item['offer_id']])
    _assert_update_targets(live,{item['offer_id']:str(item['platform_product_id'])})
    rows = live.get('items') or (live.get('result') or {}).get('items') or []
    row = next(row for row in rows if str(row['offer_id'])==item['offer_id'])
    skus = {str(row.get('sku'))} | {str(source.get('sku')) for source in row.get('sources',[])}
    if not set(item['platform_skus']).issubset(skus):
        raise RuntimeError('平台SKU与冻结目标不一致')
    if Decimal(str(row.get('price'))) != Decimal(item['payload']['price']) or row.get('currency_code') != item['payload']['currency_code']:
        raise RuntimeError('平台原价或币种变化，本次内容更新阻断，不修改价格')
    response=client.get_product_attributes([item['offer_id']])
    rows=response.get('result') or response.get('items') or []
    if isinstance(rows,dict):rows=rows.get('items') or []
    matches=[row for row in rows if str(row.get('offer_id'))==item['offer_id']]
    if len(matches)!=1:raise RuntimeError('原商品属性身份未核实，整批未发送')
    actual=matches[0]
    if actual.get('id') and str(actual['id'])!=str(item['platform_product_id']):raise RuntimeError('平台属性商品ID不一致')
    for key,value in item['payload'].items():
        if key not in {'offer_id','name','price','currency_code','images','attributes'} and actual.get(key)!=value:
            raise RuntimeError(f'平台原始{key}已变化或未提供，不覆盖受保护资料')
    content_ids={4180,4191,23171,11254}
    protected=lambda attributes:[value for value in attributes if value.get('id') not in content_ids]
    if fingerprint(protected(actual.get('attributes',[])))!=fingerprint(protected(item['payload']['attributes'])):
        raise RuntimeError('平台非内容属性已变化，整批未发送，避免覆盖原商品资料')


def _compile(root, source, version):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'ozon-field-completion'))
    from ozon_field_completion.service import compile_rich_content_modules
    content = version['content']
    fields=set(content.get('changed_fields',['title_ru','description_ru','tags','rich_content','images']))
    from backend.services.listing_image_tools import resolved_image
    selection = content.get('image_selection')
    if selection is None and content.get('generated_images'):
        raise ValueError('已生成图片必须人工选择并保存，不能静默退回原图')
    selection = selection if selection is not None else source['images']
    if not selection:raise ValueError('最终图片为空，不能提交')
    image_evidence = [resolved_image(root,source,ref) for ref in selection]
    modules = copy.deepcopy(content['rich_content']['modules']) if 'rich_content' in fields else []
    rich_evidence = []
    for module in modules:
        if module['image_path']:
            evidence = resolved_image(root,source,module['image_path'])
            rich_evidence.append(evidence); module['image_path']=evidence['public_url']
    if not source['ozon_product_id'] or not source['offer_id'] or not source['platform_skus']:
        raise ValueError('原商品平台ID/offer/SKU不完整，请先只读同步核对；禁止误创建')
    if content.get('ai_unresolved_facts') or any(check['kind'] in {'SPEC_UNVERIFIED','SPEC_CONFLICT','BRAND_UNVERIFIED','CONTENT_MISSING','TAG_UNRELATED'} for check in content.get('checks',[])):
        raise ValueError('内容有未核实事实，整批不能进入平台确认')
    transport = copy.deepcopy(source['original_transport'])
    for name in ('depth','height','width','weight','description_category_id','type_id'):
        if type(transport.get(name)) is not int or transport[name] <= 0:
            raise ValueError(f'原商品必需字段{name}未提供真实正值，不能补0或猜测')
    if transport.get('dimension_unit') not in {'mm','cm','in'} or transport.get('weight_unit') not in {'g','kg','lb'}:
        raise ValueError('原商品尺寸/重量单位未知，不能默认转换')
    if source['price'] is None or Decimal(str(source['price'])) <= 0 or not source['currency']:
        raise ValueError('原价或币种未核实；全量更新不能猜价格')
    transport.pop('barcodes',None)
    # Ozon uses images[0] as primary when this old overriding field is absent.
    transport.pop('primary_image',None)
    transport.update(offer_id=source['offer_id'],name=content['title_ru'],price=format(Decimal(str(source['price'])).normalize(),'f'),
                     currency_code=source['currency'],images=[item['public_url'] for item in image_evidence])
    attributes = copy.deepcopy(source['attributes'])
    values={}
    if 'description_ru' in fields:values[4191]=content['description_ru']
    if 'tags' in fields:values[23171]=' '.join(content['tags'])
    if 'rich_content' in fields:
        values[11254]=json.dumps(compile_rich_content_modules(modules),ensure_ascii=False,separators=(',',':'))
    if 'title_ru' in fields and any(attribute.get('id')==4180 for attribute in attributes):
        values[4180] = content['title_ru']
    for identifier,value in values.items():
        attributes = [attribute for attribute in attributes if attribute.get('id') != identifier]
        attributes.append({'id':identifier,'complex_id':0,'values':[{'value':value}]})
    transport['attributes'] = attributes
    if {'stocks','warehouse_id','new_description_category_id','promotions'} & set(transport):
        raise ValueError('禁止库存、仓库、类目迁移或促销变更')
    return transport,image_evidence,rich_evidence


def build_optimization_review(root, jobs):
    if not isinstance(jobs,list) or not jobs or len(jobs)>100:
        raise ValueError('整批必须包含1至100个已审核目标')
    service = ProductOptimizationService(root); repo = LocalOperationsRepository(root)
    items = []; targets = []; seen = set()
    for target in jobs:
        if not isinstance(target,dict) or set(target)!={'store_id','listing_id','version'}:
            raise ValueError('目标必须是店铺/Listing/版本，不能传平台请求或替换身份')
        source = service.source(target['store_id'],target['listing_id'])
        identity = (source['store_id'],source['listing_id'])
        if identity in seen: raise ValueError('整批包含重复目标')
        seen.add(identity)
        version = repo.version(*identity,target['version'])
        versions = repo.versions(*identity)
        if not version or version['status']!='REVIEWED' or version['version']!=versions[0]['version'] or version['source_revision']!=source['source_revision']:
            raise RuntimeError('存在未完成、未审核、过期或来源变化商品，整批阻断')
        content = version['content']
        checks = service.content_checks(source,content)
        version = {**version,'content':{**content,'checks':checks}}
        payload,image_evidence,rich_evidence = _compile(root,source,version)
        items.append({'organization_id':DEFAULT_ORGANIZATION_ID,**target,'operation':'UPDATE',
                      'offer_id':source['offer_id'],'platform_product_id':source['ozon_product_id'],'platform_skus':source['platform_skus'],
                      'source_revision':source['source_revision'],'content_sha256':fingerprint(content),'payload':payload,
                      'image_evidence':image_evidence,'rich_image_evidence':rich_evidence,'image_changed':payload['images']!=source['images'],
                      'before':{'title_ru':source['title_ru'],'description_ru':source['description_ru'],'tags':source['tags_text']},
                      'after':content,'price_policy':'原价/币种只读透传，发送前核对一致；不调用价格或库存接口'})
        targets.append(target)
    review = {'items':items,'targets':targets,'review_hash':fingerprint(items),'platform_write_calls':0}
    write_json_atomic(_base(root)/'reviews'/f"{review['review_hash']}.json",review)
    return review


def authorize_optimization_update(root, review_hash, actor):
    if not re.fullmatch('[a-f0-9]{64}',str(review_hash)):
        raise ValueError('确认编号无效')
    with batch_upload_lock(Path(root)):
        path = _base(root)/'reviews'/f'{review_hash}.json'
        if not path.is_file(): raise KeyError(review_hash)
        before = json.loads(path.read_text(encoding='utf-8'))
        current = build_optimization_review(root,before['targets'])
        if current['review_hash']!=review_hash: raise RuntimeError('最终内容已变化，旧整批确认失效')
        authorization = {'authorization_id':review_hash,'review_hash':review_hash,'actor':actor,'review':current}
        auth_path = _base(root)/'authorizations'/f'{review_hash}.json'
        if not auth_path.is_file(): write_json_atomic(auth_path,authorization)
        return json.loads(auth_path.read_text(encoding='utf-8'))


def execute_optimization_update(root, authorization_id):
    if not re.fullmatch('[a-f0-9]{64}',str(authorization_id)):
        raise ValueError('授权编号无效')
    directory = _base(root)/'authorizations'
    result_path = directory/f'{authorization_id}.result.json'
    with batch_upload_lock(Path(root)):
        if result_path.is_file(): return json.loads(result_path.read_text(encoding='utf-8'))
        path = directory/f'{authorization_id}.json'
        if not path.is_file(): raise PermissionError('缺少最终整批确认')
        auth = json.loads(path.read_text(encoding='utf-8'))
        current = build_optimization_review(root,auth['review']['targets'])
        if current['review_hash']!=auth['review_hash']: raise RuntimeError('版本、来源或内容已变化，授权失效')
        clients = {}
        for item in current['items']:
            work = _base(root)/'writes'/fingerprint([authorization_id,item['store_id'],item['listing_id']])
            if (work/'result.json').is_file() or (work/'output/ozon-write-intent.json').is_file():
                continue
            client = client_for(root,item['store_id'])
            _preflight(client,item)
            clients[(item['store_id'],item['listing_id'])] = client
        if build_optimization_review(root,current['targets'])['review_hash']!=auth['review_hash']:
            raise RuntimeError('平台前置核对期间内容已变化，整批未发送')
        results = []
        for item in current['items']:
            work = _base(root)/'writes'/fingerprint([authorization_id,item['store_id'],item['listing_id']])
            work.mkdir(parents=True,exist_ok=True)
            (work/'output').mkdir(exist_ok=True)
            saved = work/'result.json'
            if saved.is_file():
                results.append(json.loads(saved.read_text(encoding='utf-8')));continue
            intent_path = work/'output/ozon-write-intent.json'
            if intent_path.is_file():
                result = {**{key:item[key] for key in ('store_id','listing_id','version')},'status':'RESULT_UNKNOWN',
                          'message':'存在先前提交意图，先回查，不重发'}
                task=acknowledged_task(work,item)
                if task:result.update(task_id=task,status='REMOTE_PENDING',target=item,message='恢复已受理任务编号，只读核对，不重放提交')
            else:
                result = {**{key:item[key] for key in ('store_id','listing_id','version')},'status':'FAILED'}
                try:
                    client = clients[(item['store_id'],item['listing_id'])]
                    if build_optimization_review(root,current['targets'])['review_hash']!=auth['review_hash']:
                        raise RuntimeError('发送前内容已变化')
                    response = _submit_import_once(work,client,[item['payload']],'update',confirmed_backend=True)
                    result.update(status='REMOTE_PENDING',task_id=response['result']['task_id'],target=item,
                                  message='本地提交已受理；平台处理及字段回读尚未成功')
                except Exception:
                    result.update(status='RESULT_UNKNOWN' if intent_path.is_file() else 'FAILED',
                                  message='请求结果未知，请先回查，禁止盲目重发' if intent_path.is_file() else '身份、权限、原价或前置条件失败；未发送更新')
            write_json_atomic(saved,result);results.append(result)
        result = {'authorization_id':authorization_id,'items':results}
        write_json_atomic(result_path,result)
        return result


def reconcile_optimization_update(root, authorization_id):
    if not re.fullmatch('[a-f0-9]{64}',str(authorization_id)):
        raise ValueError('授权编号无效')
    directory = _base(root)/'authorizations'
    path = directory/f'{authorization_id}.json'
    if not path.is_file(): raise PermissionError('没有本工作室已确认的提交记录')
    authorization = json.loads(path.read_text(encoding='utf-8'))
    results = []
    for target in authorization['review']['items']:
        ProductOptimizationService(root).source(target['store_id'],target['listing_id'])
        work = _base(root)/'writes'/fingerprint([authorization_id,target['store_id'],target['listing_id']])
        saved = work/'result.json'
        if not saved.is_file():
            results.append({'store_id':target['store_id'],'listing_id':target['listing_id'],'status':'NOT_SENT'});continue
        record = json.loads(saved.read_text(encoding='utf-8'))
        try:
            client = client_for(root,target['store_id'],for_read=True)
            task_id = record.get('task_id') or acknowledged_task(work,target)
            if task_id:record['task_id']=task_id
            selected = None
            if task_id:
                processing = client.get_import_info(task_id)
                write_json_atomic(work/'processing.json',processing)
                rows = (processing.get('result') or {}).get('items') or []
                matches = [row for row in rows if str(row.get('offer_id'))==target['offer_id']]
                selected = matches[0] if len(matches)==1 else None
                record.update(processing_status=selected.get('status') if selected else 'TARGET_RECEIPT_MISSING',
                              processing_identity_matches=bool(selected and str(selected.get('product_id'))==str(target['platform_product_id'])))
                if selected and (selected.get('errors') or str(selected.get('status')).lower() in {'failed','error'}):
                    record.update(status='FAILED_REMOTE',message='平台处理失败；结果已保留，不自动重发')
                    write_json_atomic(saved,record);results.append(record);continue
            info = client.get_products_info([target['offer_id']])
            _assert_update_targets(info,{target['offer_id']:str(target['platform_product_id'])})
            rows = info.get('items') or (info.get('result') or {}).get('items') or []
            live = next(row for row in rows if str(row.get('offer_id'))==target['offer_id'])
            skus = {str(live.get('sku'))} | {str(item.get('sku')) for item in live.get('sources',[])}
            if not set(target['platform_skus']).issubset(skus):
                raise RuntimeError('回读SKU不属于冻结目标')
            attributes = client.get_product_attributes([target['offer_id']])
            write_json_atomic(work/'readback.json',{'info':info,'attributes':attributes})
            attr_rows = attributes.get('result') or attributes.get('items') or []
            if isinstance(attr_rows,dict): attr_rows = attr_rows.get('items') or []
            current = next(row for row in attr_rows if str(row.get('offer_id'))==target['offer_id'])
            values = {item['id']:'\n'.join(str(v.get('value','')) for v in item.get('values',[])) for item in current.get('attributes',[])}
            expected = {item['id']:'\n'.join(str(v.get('value','')) for v in item.get('values',[])) for item in target['payload']['attributes']}
            field_results = {'title':live.get('name')==target['payload']['name']}
            changed=set(target['after'].get('changed_fields',['description_ru','tags','rich_content']))
            for identifier,label,field in ((4191,'description','description_ru'),(23171,'tags','tags'),(11254,'rich_content','rich_content')):
                if field not in changed:continue
                if identifier==11254:
                    try: field_results[label] = fingerprint(json.loads(values.get(identifier,'')))==fingerprint(json.loads(expected[identifier]))
                    except (ValueError,KeyError): field_results[label] = False
                else: field_results[label] = values.get(identifier)==expected.get(identifier)
            matched = all(field_results.values())
            image_results = {'verified':True,'state':'NO_IMAGE_CHANGE','items':[]}
            if target.get('image_changed'):
                from backend.services.listing_image_tools import public_image_bytes
                urls = live.get('images') or []
                primary = live.get('primary_image') or []
                if isinstance(primary,str):primary=[primary]
                urls = list(dict.fromkeys(primary+urls))
                observed = []
                for index,evidence in enumerate(target['image_evidence']):
                    value = urls[index] if index<len(urls) else None
                    try:
                        matches = bool(value and (hashlib.sha256(public_image_bytes(value)).hexdigest()==evidence['sha256'] if evidence.get('sha256') else value==evidence['public_url']))
                    except (ValueError,OSError):matches=False
                    observed.append({'position':index,'reference':evidence['reference'],'observed_url':value,'verified':matches})
                image_results = {'verified':len(urls)==len(target['image_evidence']) and all(row['verified'] for row in observed),
                                 'state':'CONTENT_AND_ORDER_READBACK','items':observed}
                matched = matched and image_results['verified']
            processed = bool(selected and record.get('processing_identity_matches') and selected.get('status')=='imported')
            skipped = bool(selected and record.get('processing_identity_matches') and selected.get('status')=='skipped')
            status = 'SUCCEEDED' if matched and processed else 'SKIPPED_NO_CHANGE' if matched and skipped else 'OBSERVED_MATCH_NO_RECEIPT' if matched and not task_id else 'REMOTE_PENDING'
            record.update(field_results=field_results, status=status,
                          message='已只读回查实际字段；任务受理与字段成功分别记录', image_results=image_results, feedback_period=target['after'].get('baseline_period'))
            write_json_atomic(saved,record)
        except Exception:
            record.update(readback_error='只读核对失败或字段尚未一致；不自动提交或重试写入')
            write_json_atomic(saved,record)
        results.append(record)
    result = {'authorization_id':authorization_id,'items':results,'write_api_calls':0}
    write_json_atomic(directory/f'{authorization_id}.result.json',result)
    return result
