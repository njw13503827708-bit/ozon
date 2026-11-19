"""Listing-owned immutable references and registered generation candidates."""
import base64
import hashlib
import io
import json
import re
import os
import shutil
from datetime import datetime, timezone
from urllib.parse import urlsplit
import urllib.request
from pathlib import Path

from PIL import Image
from backend.integrations.ai.provider_config import settings
from backend.services.product_optimization import ProductOptimizationService, fingerprint, allowed_image
from scripts.openai_images2 import edit_registered_images, NoRedirect, MODEL
from scripts.pipeline_runtime import write_json_atomic
from scripts.batch_upload_approval import batch_upload_lock


def download_reference(url):
    if not allowed_image(url):
        raise ValueError('参考图不属于已核实的Ozon来源地址')
    with urllib.request.build_opener(NoRedirect).open(url, timeout=30) as response:
        value = response.read(20 * 1024 * 1024 + 1)
    if len(value) > 20 * 1024 * 1024:
        raise ValueError('参考图片过大')
    return value


def _directory(root, identifier):
    if not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('任务编号无效')
    return Path(root)/'runtime/optimization-tasks'/identifier


def prepare_listing_images(root, job_id, context, image_plan, *, transport=None):
    directory = _directory(root,job_id)
    frozen = json.loads((directory/'context.json').read_text(encoding='utf-8'))
    if frozen.get('source') != context.get('source'):
        raise PermissionError('图片任务不属于当前Listing身份和冻结来源')
    source = context['source']
    current = ProductOptimizationService(root).source(source['store_id'],source['listing_id'])
    if source['source_revision'] != current['source_revision']:
        raise RuntimeError('来源已变化，原图和生成结果保留，不能继续收费')
    if not settings(root)['key']:
        raise ValueError('请安全配置OPENAI_API_KEY；未下载或调用生图')
    if not isinstance(image_plan,list) or not 1 <= len(image_plan) <= 8:
        raise ValueError('生图计划需要1至8张，不能空计划或异常循环')
    registry_path = directory/'assets.json'
    saved = json.loads(registry_path.read_text(encoding='utf-8')) if registry_path.is_file() else []
    assets = []
    for plan in image_plan:
        indexes = plan.get('reference_indexes')
        prompt = plan.get('prompt')
        if (not isinstance(prompt,str) or not prompt.strip() or len(prompt)>31000 or not isinstance(indexes,list)
                or not 1 <= len(indexes) <= 16 or any(type(index) is not int or not 0 <= index < len(source['images']) for index in indexes)):
            raise ValueError('图片计划必须引用当前Listing的实际原图')
        references, images = [], []
        for index in indexes:
            url = source['images'][index]
            original = directory/'assets/original'/f'{index}.image'
            if not original.is_file():
                value = download_reference(url)
                original.parent.mkdir(parents=True,exist_ok=True)
                original.write_bytes(value)
            value = original.read_bytes()
            with Image.open(io.BytesIO(value)) as image:
                mime = {'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}.get(image.format)
                image.verify()
            if not mime:
                raise ValueError('原图格式不受生图接口支持')
            references.append({'path':original.relative_to(root).as_posix(),'source_url':url,'sha256':hashlib.sha256(value).hexdigest()})
            images.append({'image_url':f'data:{mime};base64,'+base64.b64encode(value).decode()})
        prompt += '\nPreserve actual identity, color, structure, quantity, SKU and accessories. Retain all existing markings and third-party watermarks. Do not invent claims. Output 3:4.'
        name = fingerprint([job_id,prompt,references])[:32]+'.png'
        existing = next((item for item in saved if item['name'] == name),None)
        if existing:
            assets.append(asset_for(root,source,existing['reference']))
            continue
        output = directory/'assets/generated'/name
        proof = edit_registered_images(output, {'model':MODEL,'prompt':prompt,'images':images,'n':1,
                'size':'960x1280','quality':'high','output_format':'png'},references,root=Path(root),transport=transport)
        asset = {'name':name,'job_id':job_id,'organization_id':source['organization_id'],'store_id':source['store_id'],
                 'listing_id':source['listing_id'],'source_revision':source['source_revision'],
                 'reference':f'asset://{job_id}/{name}','path':output.relative_to(root).as_posix(),'sha256':proof['sha256'],
                 'preview_url':f"/api/erp/stores/{source['store_id']}/listings/{source['listing_id']}/optimization/tasks/{job_id}/assets/{name}",
                 'references':references,'provider_evidence':proof,'qc_state':'NEEDS_IDENTITY_REVIEW','public_url':None}
        saved.append(asset); write_json_atomic(registry_path,saved); assets.append(asset)
    return assets


def asset_for(root, source, reference):
    match = re.fullmatch(r'asset://([a-f0-9]{32})/([a-f0-9]{32}\.png)',str(reference))
    if not match:
        raise ValueError('生成图引用无效或尚未登记')
    registry = _directory(root,match[1])/'assets.json'
    if not registry.is_file():
        raise ValueError('生成图没有登记，不能当成当前商品图片')
    item = next((item for item in json.loads(registry.read_text(encoding='utf-8')) if item.get('reference') == reference),None)
    if not item or any(item.get(key) != source.get(key) for key in ('organization_id','store_id','listing_id','source_revision')):
        raise ValueError('生成图不属于当前店铺、Listing或来源版本')
    path = Path(root)/item['path']
    allowed = _directory(root,match[1])/'assets/generated'/match[2]
    if path.resolve() != allowed.resolve() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError('生成图文件缺失、被替换或哈希不符')
    return item


def listing_assets(root, source):
    result = []
    base = Path(root)/'runtime/optimization-tasks'
    for path in base.glob('*/assets.json'):
        for item in json.loads(path.read_text(encoding='utf-8')):
            if item.get('store_id') == source['store_id'] and item.get('listing_id') == source['listing_id'] and item.get('source_revision') == source['source_revision']:
                try:
                    result.append(asset_for(root,source,item['reference']))
                except ValueError:
                    continue
    return result


def review_asset(root, source, reference, actor):
    with batch_upload_lock(Path(root)):
        asset = asset_for(root,source,reference)
        registry = _directory(root,asset['job_id'])/'assets.json'
        rows = json.loads(registry.read_text(encoding='utf-8'))
        asset.update(qc_state='ACCEPTED_IDENTITY',identity_review={'actor':actor,'sha256':asset['sha256'],
                     'at':datetime.now(timezone.utc).isoformat(),'method':'HUMAN_IDENTITY_AND_RIGHTS_CONFIRMATION'})
        write_json_atomic(registry,[asset if row['reference']==reference else row for row in rows])
        return asset


def public_image_bytes(url):
    parsed = urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in {None,443} or not (
            allowed_image(url) or (parsed.hostname or '').endswith('.trycloudflare.com')):
        raise ValueError('图片公开地址不属于已核实通道')
    with urllib.request.build_opener(NoRedirect).open(url,timeout=30) as response:
        data = response.read(20*1024*1024+1)
    if len(data)>20*1024*1024:raise ValueError('公开图片过大')
    return data


def resolved_image(root, source, reference):
    if reference in source['images']:
        return {'reference':reference,'public_url':reference,'kind':'ORIGINAL'}
    asset = asset_for(root,source,reference)
    hosting = asset.get('hosting') or {}
    if asset['qc_state']!='ACCEPTED_IDENTITY' or not asset.get('public_url') or hosting.get('sha256')!=asset['sha256']:
        raise ValueError('生成图未完成人工身份审核或公网验证；不能静默提交原图')
    expires = datetime.fromisoformat(hosting.get('expires_at',''))
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        raise ValueError('图片公开通道已过期，旧确认失效')
    return {key:asset[key] for key in ('reference','public_url','sha256','qc_state','hosting','source_revision')}


def publish_asset(root, source, reference):
    if os.getenv('CAF_IMAGE_HOSTING_APPROVED')!='1':
        raise RuntimeError('图片托管未配置/授权；本地生成和纯文本更新仍可用')
    from ozon_uploader.image_channels import start_image_channel
    with batch_upload_lock(Path(root)):
        asset = asset_for(root,source,reference)
        if asset['qc_state']!='ACCEPTED_IDENTITY':raise ValueError('请先人工审核商品身份和素材权利')
        directory = _directory(root,asset['job_id'])
        staging = directory/'output/ozon-image-staging'; staging.mkdir(parents=True,exist_ok=True)
        shutil.copy2(Path(root)/asset['path'],staging/asset['name'])
        manifest = start_image_channel(directory,{'images':[{'staged_name':asset['name'],'sha256':asset['sha256']}]})
        url = manifest['images'][0]['public_url']
        if hashlib.sha256(public_image_bytes(url)).hexdigest()!=asset['sha256']:
            raise ValueError('公网图片内容不一致；尚未登记可提交引用')
        state = json.loads((directory/'output/image-channel-state.json').read_text(encoding='utf-8'))
        expires = state.get('expires_at')
        if not expires:raise ValueError('托管通道没有可核实有效期')
        asset.update(public_url=url,hosting={'sha256':asset['sha256'],'expires_at':expires,
                     'verified_at':datetime.now(timezone.utc).isoformat(),'method':'HTTPS_CONTENT_SHA256'})
        registry = directory/'assets.json'; rows=json.loads(registry.read_text(encoding='utf-8'))
        write_json_atomic(registry,[asset if row['reference']==reference else row for row in rows])
        return asset
