"""Local listing optimization, review and export. No marketplace write routes."""
import html
import hashlib
import io
import json
import sqlite3
import os
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.repositories.local_operations import LocalOperationsRepository, LocalOperationsUnavailable
from backend.services.product_optimization import ProductOptimizationService
from backend.services.listing_reads import ListingReadService
from backend.integrations.ozon.base_client import OzonApiError
from backend.api.ozon_products import _store_credentials

ROOT = Path(__file__).resolve().parents[2]
router = APIRouter(prefix='/api/erp/stores', tags=['local-product-optimization'])


def handled(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except KeyError:
            raise HTTPException(404, '未找到本店商品或本地版本') from None
        except (LocalOperationsUnavailable, sqlite3.Error):
            raise HTTPException(503, '本地优化/订单存储不可用；没有创建或迁移数据库，当前编辑可保留后重试') from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except RuntimeError as exc:
            if str(exc).startswith(('ERP database', 'ERP schema')):
                raise HTTPException(503, 'ERP来源库未就绪，未自动创建或迁移数据库') from None
            raise HTTPException(409, str(exc)) from None
    return call


def operator(request, *, mutation=False):
    value = getattr(request.state, 'operator', None) or {}
    if value.get('organization_id', 'default') != 'default':
        raise PermissionError('当前工作室不支持外组织访问')
    if mutation and (not value.get('is_host_device') or str(value.get('role') or '').lower() not in {'owner', 'admin', 'operator'}):
        raise PermissionError('只有主电脑有编辑权限的用户可以保存、审核或读取新数据')
    return str(value.get('id') or 'local-reader')


def context(request, store_id, listing_id, *, mutation=False):
    actor = operator(request, mutation=mutation)
    service = ProductOptimizationService(ROOT)
    source = service.source(store_id, listing_id)
    return actor, service, source, LocalOperationsRepository(ROOT)


def protected_edit(function):
    @wraps(function)
    def call(*args,**kwargs):
        from scripts.batch_upload_approval import batch_upload_lock
        with batch_upload_lock(ROOT):return function(*args,**kwargs)
    return call


def period(date_from, date_to):
    if not date_from and not date_to:
        end = datetime.now(timezone(timedelta(hours=3))).date() - timedelta(days=1)
        return (end - timedelta(days=6)).isoformat(), end.isoformat()
    from backend.integrations.ozon.analytics_client import validate_dates
    validate_dates(date_from, date_to)
    return date_from, date_to


def search_for(repo, store_id, content, listing_id):
    selected = content.get('baseline_period')
    if not selected:
        return None
    kind = f'SEARCH:{listing_id}'
    return repo.snapshot(kind, store_id, selected['date_from'], selected['date_to']) or repo.snapshot(kind, store_id, selected['date_from'], selected['date_to'], allow_partial=True)


def view_version(version, source):
    if version is None:
        return None
    return {**version, 'source_valid': version['source_revision'] == source['source_revision'],
            'warning': '' if version['source_revision'] == source['source_revision'] else '原始来源已变化；请重新核对并保存新版本后审核，不能沿用旧依据。'}


def editable_content(content):
    return {key: value for key, value in content.items() if key in {
        'title_ru', 'description_ru', 'tags', 'rich_content', 'baseline_period', 'applied_at', 'image_selection'}}


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0, strict=True)
    source_revision: str = Field(min_length=64, max_length=64)
    content: dict


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_revision: str = Field(min_length=64, max_length=64)
    facts_confirmed: Literal[True]
    images_authorized: Literal[True]
    warnings_reviewed: Literal[True]


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1, strict=True)
    expected_version: int = Field(ge=1, strict=True)
    source_revision: str = Field(min_length=64, max_length=64)


class ReadDataRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['SEARCH', 'PERFORMANCE']
    date_from: str
    date_to: str


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=0, strict=True)
    source_revision: str = Field(min_length=64, max_length=64)
    fields: list[Literal['title_ru','description_ru','tags','rich_content','images']]
    period: dict
    model: str | None = None


class PublicationReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    targets: list[dict]


class PublicationConfirmRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    review_hash: str = Field(min_length=64,max_length=64)
    confirm_entire_batch: Literal[True]


class AssetReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reference: str
    identity_and_rights_confirmed: Literal[True]


@router.post('/{store_id}/listings/{listing_id}/optimization/assets/review')
@handled
def review_generated_asset(store_id: str, listing_id: int, body: AssetReviewRequest, request: Request):
    actor, _, source, _ = context(request,store_id,listing_id,mutation=True)
    from backend.services.listing_image_tools import review_asset
    return review_asset(ROOT,source,body.reference,actor)


@router.post('/{store_id}/listings/{listing_id}/optimization/assets/publish')
@handled
def publish_generated_asset(store_id: str, listing_id: int, body: AssetReviewRequest, request: Request):
    _, _, source, _ = context(request,store_id,listing_id,mutation=True)
    from backend.services.listing_image_tools import publish_asset
    return publish_asset(ROOT,source,body.reference)


@router.post('/optimization-publication/review')
@handled
def review_publication(body: PublicationReviewRequest, request: Request):
    operator(request, mutation=True)
    from backend.services.optimization_publication import build_optimization_review
    return build_optimization_review(ROOT,body.targets)


@router.post('/optimization-publication/confirm')
@handled
def confirm_publication(body: PublicationConfirmRequest, request: Request):
    actor = operator(request, mutation=True)
    from backend.services.optimization_publication import authorize_optimization_update, execute_optimization_update
    authorization = authorize_optimization_update(ROOT,body.review_hash,actor)
    # This is the one final whole-batch user action; no per-product second prompt.
    return execute_optimization_update(ROOT,authorization['authorization_id'])


@router.post('/optimization-publication/{authorization_id}/reconcile')
@handled
def reconcile_publication(authorization_id: str, request: Request):
    operator(request, mutation=True)
    from backend.services.optimization_publication import reconcile_optimization_update
    return reconcile_optimization_update(ROOT,authorization_id)


def run_optimization_task(identifier, *, resume=False):
    from backend.services.optimization_agent import OptimizationAgent
    agent = OptimizationAgent(ROOT)
    def worker():
        try:
            (agent.resume if resume else agent.run)(identifier)
        except Exception:
            agent.repo.update_job(identifier, status='FAILED', message='任务处理失败；已有结果保留，请核查任务阶段后恢复')
    threading.Thread(target=worker, name=f'optimization-{identifier[:8]}', daemon=True).start()


@router.post('/{store_id}/listings/{listing_id}/optimization/generate', status_code=202)
@handled
def generate_optimization(store_id: str, listing_id: int, body: GenerateRequest, request: Request):
    actor, _, _, _ = context(request, store_id, listing_id, mutation=True)
    if set(body.period) != {'date_from','date_to'}:
        raise ValueError('请指定完整统计周期')
    from backend.integrations.ai.provider_config import settings
    from backend.services.optimization_agent import OptimizationAgent
    model = body.model or settings(ROOT)['model']
    job = OptimizationAgent(ROOT).start(store_id, listing_id, body.expected_version, body.source_revision,
                                         body.fields, body.period, actor, model=model)
    if job['created']:
        run_optimization_task(job['id'])
    return job


@router.get('/{store_id}/listings/{listing_id}/optimization/tasks/{job_id}')
@handled
def read_optimization_task(store_id: str, listing_id: int, job_id: str, request: Request):
    context(request, store_id, listing_id)
    from backend.services.optimization_agent import OptimizationAgent
    return OptimizationAgent(ROOT).view(store_id, listing_id, job_id)


@router.post('/{store_id}/listings/{listing_id}/optimization/tasks/{job_id}/resume', status_code=202)
@handled
def resume_optimization_task(store_id: str, listing_id: int, job_id: str, request: Request):
    context(request, store_id, listing_id, mutation=True)
    from backend.services.optimization_agent import OptimizationAgent
    job = OptimizationAgent(ROOT).view(store_id, listing_id, job_id)
    if job['status'] in {'QUEUED','RUNNING','INTERRUPTED','FAILED_CONFIG'}:
        run_optimization_task(job_id, resume=True)
    return job


@router.post('/{store_id}/listings/{listing_id}/optimization/tasks/{job_id}/cancel')
@handled
def cancel_optimization_task(store_id: str, listing_id: int, job_id: str, request: Request):
    context(request, store_id, listing_id, mutation=True)
    from backend.services.optimization_agent import OptimizationAgent
    agent = OptimizationAgent(ROOT)
    agent.view(store_id, listing_id, job_id)
    return agent.cancel(job_id)


@router.get('/{store_id}/listings/{listing_id}/optimization')
@handled
def read_optimization(store_id: str, listing_id: int, request: Request, date_from: str = '', date_to: str = ''):
    _, service, source, repo = context(request, store_id, listing_id)
    date_from, date_to = period(date_from, date_to)
    search = search_for(repo, store_id, {'baseline_period': {'date_from': date_from, 'date_to': date_to}}, listing_id)
    from backend.services.listing_image_tools import listing_assets
    return {'source': source, 'diagnosis': service.diagnose(source, date_from, date_to),
            'suggestions': service.suggest(source, search),
            'versions': [view_version(version, source) for version in repo.versions(store_id, listing_id)],
            'search_snapshot': search,
            'assets':listing_assets(ROOT,source),
            'enhanced_read_authorized': os.getenv('CAF_ENHANCED_READ_APPROVED') == '1',
            'platform_write_calls': 0, 'paid_ai_calls': 0}


@router.get('/{store_id}/listings/{listing_id}/optimization/tasks/{job_id}/assets/{asset_name}')
@handled
def read_generated_asset(store_id: str, listing_id: int, job_id: str, asset_name: str, request: Request):
    context(request,store_id,listing_id)
    from backend.services.optimization_agent import OptimizationAgent
    from backend.services.listing_image_tools import asset_for
    job = OptimizationAgent(ROOT).view(store_id,listing_id,job_id)
    asset = asset_for(ROOT,job['context']['source'],f'asset://{job_id}/{asset_name}')
    return FileResponse(ROOT/asset['path'],media_type='image/png',headers={'Cache-Control':'no-store'})


@router.get('/{store_id}/optimization-candidates')
@handled
def read_candidates(store_id: str, request: Request, date_from: str = '', date_to: str = '',
                    q: str = '', issue: str = '', page: int = 1):
    operator(request)
    date_from, date_to = period(date_from, date_to)
    return ProductOptimizationService(ROOT).candidates(store_id, date_from, date_to, q=q, issue=issue, page=page)


@router.post('/{store_id}/listings/{listing_id}/optimization', status_code=201)
@handled
@protected_edit
def save_optimization(store_id: str, listing_id: int, body: SaveRequest, request: Request):
    actor, service, source, repo = context(request, store_id, listing_id, mutation=True)
    if source['source_revision'] != body.source_revision:
        raise RuntimeError('商品来源已变化，请重新核对；没有覆盖当前编辑')
    versions = repo.versions(store_id, listing_id)
    before = versions[0]['content'] if versions else {}
    content = service.validate_content(source, body.content, before, search_for(repo, store_id, body.content, listing_id))
    return view_version(repo.save_version(store_id, listing_id, content, source['source_revision'], body.expected_version, actor), source)


@router.post('/{store_id}/listings/{listing_id}/optimization/check')
@handled
def check_optimization(store_id: str, listing_id: int, body: SaveRequest, request: Request):
    _, service, source, repo = context(request, store_id, listing_id)
    if source['source_revision'] != body.source_revision:
        raise RuntimeError('商品来源已变化，请重新核对')
    versions = repo.versions(store_id, listing_id)
    before = versions[0]['content'] if versions else {}
    content = service.validate_content(source, body.content, before, search_for(repo, store_id, body.content, listing_id))
    return {'checks': content['checks'], 'normalized_tags': content['tags'], 'saved': False, 'platform_write_calls': 0}


@router.get('/{store_id}/listings/{listing_id}/optimization/versions/{version}')
@handled
def read_version(store_id: str, listing_id: int, version: int, request: Request):
    _, _, source, repo = context(request, store_id, listing_id)
    saved = repo.version(store_id, listing_id, version)
    if saved is None:
        raise KeyError(version)
    return view_version(saved, source)


@router.post('/{store_id}/listings/{listing_id}/optimization/versions/{version}/review')
@handled
@protected_edit
def review_optimization(store_id: str, listing_id: int, version: int, body: ReviewRequest, request: Request):
    actor, service, source, repo = context(request, store_id, listing_id, mutation=True)
    saved = repo.version(store_id, listing_id, version)
    if saved is None:
        raise KeyError(version)
    if body.source_revision != source['source_revision'] or saved['source_revision'] != source['source_revision']:
        raise RuntimeError('商品来源已变化，必须重新保存并审核')
    clean = editable_content(saved['content'])
    content = service.validate_content(source, clean, saved['content'], search_for(repo, store_id, clean, listing_id))
    rich_selected='rich_content' in content.get('changed_fields',['rich_content'])
    if not content['title_ru'].strip() or not content['description_ru'].strip() or rich_selected and not content['rich_content']['modules']:
        raise ValueError('请完成俄文标题、描述及至少一个富内容模块后审核')
    if any(not module['body'].strip() for module in content['rich_content']['modules']):
        raise ValueError('富内容模块正文为空，请完成后审核')
    from ozon_field_completion.service import compile_rich_content_modules
    blocking = [check for check in content['checks'] if check['kind'] in {'SPEC_UNVERIFIED','SPEC_CONFLICT','BRAND_UNVERIFIED','TAG_UNRELATED','CONTENT_MISSING'}]
    platform = {'platform_ready':not blocking and not saved['content'].get('ai_unresolved_facts'), 'blocking_checks':blocking}
    try:
        if rich_selected:
            compile_rich_content_modules(content['rich_content']['modules'])
            platform['rich_content_schema_valid'] = True
        else:platform.update(rich_content_schema_valid=None,rich_content_not_modified=True)
    except ValueError as error:
        platform.update(platform_ready=False, rich_content_schema_valid=False, rich_content_error=str(error))
    return view_version(repo.review_version(store_id, listing_id, version, source['source_revision'], actor, {**body.model_dump(), **platform}), source)


@router.post('/{store_id}/listings/{listing_id}/optimization/restore', status_code=201)
@handled
@protected_edit
def restore_optimization(store_id: str, listing_id: int, body: RestoreRequest, request: Request):
    actor, service, source, repo = context(request, store_id, listing_id, mutation=True)
    old = repo.version(store_id, listing_id, body.version)
    if old is None:
        raise KeyError(body.version)
    if source['source_revision'] != body.source_revision:
        raise RuntimeError('商品来源已变化，请重新核对')
    clean = editable_content(old['content'])
    content = service.validate_content(source, clean, old['content'], search_for(repo, store_id, clean, listing_id))
    content['restored_from'] = body.version
    return view_version(repo.save_version(store_id, listing_id, content, source['source_revision'], body.expected_version, actor), source)


def export_bundle(source, version):
    content = version['content']
    blocks = []
    for module in content['rich_content']['modules']:
        image = f'<img src="{html.escape(module["image_path"], quote=True)}" alt="{html.escape(module["title"], quote=True)}">' if module['image_path'] else ''
        blocks.append(f'<section>{image}<h2>{html.escape(module["title"])}</h2><p>{html.escape(module["body"])}</p></section>')
    preview = '<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Local content preview</title><style>body{max-width:800px;margin:auto;padding:20px;font-family:Arial}img{max-width:100%}p{white-space:pre-wrap}</style>'
    preview += '<h1>' + html.escape(content['title_ru']) + '</h1><p>' + html.escape(content['description_ru']) + '</p>' + ''.join(blocks) + '</html>'
    files = {'title-ru.txt': content['title_ru'], 'description-ru.txt': content['description_ru'],
        'keywords-ru.txt': '\n'.join(content['tags']), 'preview.html': preview,
        'local-content.json': json.dumps({'source': source, 'version': version, 'platform_format_verified': False}, ensure_ascii=False, indent=2),
        'image-references.json': json.dumps([module['image_path'] for module in content['rich_content']['modules'] if module['image_path']], ensure_ascii=False, indent=2),
        'README.txt': 'LOCAL REVIEWED VERSION — not uploaded.\n本文件是人工使用的本地成果，不是已发布或已验证可上传的Ozon请求。\n1.核对店铺、货号、SKU、版本和事实。\n2.手工复制俄文标题/描述/标签。\n3.HTML供图文预览，图片仅引用本商品已有地址，可能过期。\n4.在平台支持的编辑器中人工处理富内容；本轮未核实官方导入格式。\n5.平台应用后记录实际日期；本地保存不表示生效，不承诺效果。\n'}
    files['sha256.json'] = json.dumps({name: hashlib.sha256(value.encode()).hexdigest() for name, value in files.items()}, indent=2)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value.encode('utf-8'))
    return buffer.getvalue()


@router.get('/{store_id}/listings/{listing_id}/optimization/versions/{version}/export')
@handled
def export_optimization(store_id: str, listing_id: int, version: int, request: Request):
    _, service, source, repo = context(request, store_id, listing_id)
    saved = repo.version(store_id, listing_id, version)
    if saved is None:
        raise KeyError(version)
    if saved['status'] != 'REVIEWED' or saved['source_revision'] != source['source_revision']:
        raise RuntimeError('请先核对当前来源并人工审核版本后导出')
    clean = editable_content(saved['content'])
    service.validate_content(source, clean, saved['content'], search_for(repo, store_id, clean, listing_id))
    return Response(export_bundle(source, saved), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="local-listing-{listing_id}-v{version}.zip"',
        'Cache-Control': 'no-store'})


@router.post('/{store_id}/listings/{listing_id}/optimization/read-data', status_code=202)
@handled
def start_listing_read(store_id: str, listing_id: int, body: ReadDataRequest, request: Request):
    _, _, source, repo = context(request, store_id, listing_id, mutation=True)
    date_from, date_to = period(body.date_from, body.date_to)
    if os.getenv('CAF_ENHANCED_READ_APPROVED') != '1':
        raise PermissionError('新增商品搜索词/分析接口尚未批准；本地编辑和缓存查询仍可用')
    shop, credentials = _store_credentials(store_id)
    if str(shop.get('validation_status') or '').lower() != 'connected':
        raise RuntimeError('店铺凭证尚未连接验证或已变更，请先核对自有店铺连接')
    job, created = repo.start_job(body.kind, store_id, f'{listing_id}:{date_from}:{date_to}')
    if created:
        def worker():
            repo.update_job(job['id'], status='RUNNING', message='正在读取所选商品的自有店铺数据')
            try:
                service = ListingReadService(ROOT, **credentials)
                result = service.search(source, date_from, date_to) if body.kind == 'SEARCH' else service.performance(source, date_from, date_to)
                repo.update_job(job['id'], status='SUCCEEDED', progress=len(result['data']['rows']),
                    message=f"所选商品数据读取完成，实际周期{result['date_from']}至{result['date_to']}；未知指标未补零")
            except Exception as exc:
                code = exc.status_code if isinstance(exc, OzonApiError) else None
                message = '平台权限/订阅不支持，已停止；保留旧缓存' if code in {401, 403} else f'商品数据读取失败（HTTP {code}），保留旧缓存' if code else '商品数据读取或格式校验失败；保留旧缓存，请手工重试'
                repo.update_job(job['id'], status='FAILED', message=message)
        threading.Thread(target=worker, name=f'listing-read-{job["id"][:8]}', daemon=True).start()
    return {'job_id': job['id'], 'reused': not created, 'platform_write_calls': 0}


@router.get('/{store_id}/listings/{listing_id}/optimization/read-jobs/{job_id}')
@handled
def read_listing_job(store_id: str, listing_id: int, job_id: str, request: Request):
    _, _, _, repo = context(request, store_id, listing_id)
    job = repo.job(job_id)
    if job is None:
        raise KeyError(job_id)
    if job['store_id'] != store_id or job['kind'] not in {'SEARCH', 'PERFORMANCE'} or not job['scope'].startswith(f'{listing_id}:'):
        raise PermissionError('读取任务不属于所选店铺商品')
    return job
