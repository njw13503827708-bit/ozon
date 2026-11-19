"""Listing-scoped model stages, independent versions and non-replaying recovery."""
import json
import re
from contextlib import ExitStack
from pathlib import Path

from backend.integrations.ai.openai_content import generate_content, configuration, ProviderResultUnknown, InvalidModelOutput
from backend.repositories.local_operations import LocalOperationsRepository
from backend.services.product_optimization import ProductOptimizationService, fingerprint
from scripts.pipeline_runtime import write_json_atomic
from scripts.batch_upload_approval import batch_upload_lock, BatchUploadBusy

FIELDS = {'title_ru', 'description_ru', 'tags', 'rich_content', 'images'}


class OptimizationAgent:
    def __init__(self, root):
        self.root = Path(root)
        self.repo = LocalOperationsRepository(root)
        self.products = ProductOptimizationService(root)

    def task_dir(self, identifier):
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise ValueError('任务编号无效')
        return self.root / 'runtime/optimization-tasks' / identifier

    def read(self, identifier, name):
        path = self.task_dir(identifier) / name
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}

    def start(self, store_id, listing_id, expected_version, source_revision, fields, period, actor, model=None):
        source = self.products.source(store_id, listing_id)
        if source_revision != source['source_revision']:
            raise RuntimeError('商品来源已变化；请重新核对')
        if not isinstance(fields, list) or not fields or len(set(fields)) != len(fields) or set(fields) - FIELDS:
            raise ValueError('请选择合法且不重复的优化字段')
        versions = self.repo.versions(store_id, listing_id)
        config = configuration(self.root)
        model = model or config['model']
        if model and (model not in config.get('models', [config['model']]) or model.startswith('gpt-image-')):
            raise ValueError('请选择已配置的文本生成模型；图片模型不能用于商品文案生成')
        context = {'source': source, 'expected_version': expected_version, 'fields': sorted(fields), 'period': period,
                   'operation': 'UPDATE', 'model': model, 'actor': actor,
                   'diagnosis': self.products.diagnose(source, period['date_from'], period['date_to'])}
        context['provider'] = {key:config[key] for key in ('base_url','api_mode','model')}
        scope = fingerprint({key:value for key,value in context.items() if key not in {'diagnosis','actor'}})
        # Completed/unknown frozen jobs are also reused; no second paid request on double-click.
        job, created = self.repo.start_job('OPTIMIZATION_GENERATE', store_id, scope)
        if created:
            if (versions[0]['version'] if versions else 0) != expected_version:
                self.repo.update_job(job['id'], status='VERSION_CHANGED', message='版本已变化，未调用模型')
                raise RuntimeError('版本已变化，未调用模型')
            write_json_atomic(self.task_dir(job['id']) / 'context.json', context)
            write_json_atomic(self.task_dir(job['id']) / 'state.json', {'stage':'READ', 'effect':'NOT_SENT'})
        return {**self.view(store_id, listing_id, job['id']), 'created':created}

    def view(self, store_id, listing_id, identifier):
        job = self.repo.job(identifier)
        context = self.read(identifier, 'context.json')
        if not job:
            raise KeyError(identifier)
        source = context.get('source') or {}
        if job['kind'] != 'OPTIMIZATION_GENERATE' or job['store_id'] != store_id or source.get('listing_id') != listing_id:
            raise PermissionError('任务不属于当前店铺或商品')
        self.products.source(store_id, listing_id)
        return {**job, 'context':context, 'stage':self.read(identifier, 'state.json'),
                'result':self.read(identifier, 'result.json'), 'version':self.read(identifier, 'version.json')}

    def result(self, identifier):
        context = self.read(identifier, 'context.json')
        source = context['source']
        return self.view(source['store_id'], source['listing_id'], identifier)

    def cancel(self, identifier):
        self.repo.cancel_job(identifier)
        return self.result(identifier)

    def finish(self, identifier, status, message):
        self.repo.update_job(identifier, status=status, progress=100, message=message)
        return self.result(identifier)

    def resume(self, identifier):
        state = self.read(identifier, 'state.json')
        if state.get('effect') == 'DISPATCHING' and not self.read(identifier, 'result.json'):
            return self.finish(identifier, 'RESULT_UNKNOWN', '请求可能已发送；禁止重复生成或收费')
        return self.run(identifier)

    def run(self, identifier):
        job = self.repo.job(identifier)
        if not job:
            raise KeyError(identifier)
        if job['status'] not in {'QUEUED', 'RUNNING', 'INTERRUPTED', 'FAILED_CONFIG'}:
            return self.result(identifier)
        directory = self.task_dir(identifier)
        directory.mkdir(parents=True, exist_ok=True)
        lock = ExitStack()
        try:
            lock.enter_context(batch_upload_lock(directory))
        except BatchUploadBusy:
            lock.close()
            return self.result(identifier)
        try:
            context = self.read(identifier, 'context.json')
            source = context['source']
            if self.repo.job(identifier)['cancel_requested']:
                return self.finish(identifier, 'CANCELLED', '未发送的阶段已取消；已发出的请求不能撤回')
            result = self.read(identifier, 'result.json')
            state = self.read(identifier, 'state.json')
            if not result:
                if state.get('effect') == 'DISPATCHING':
                    return self.finish(identifier, 'RESULT_UNKNOWN', '已发送请求没有结果记录，未自动重试')
                config = configuration(self.root)
                if not config['key_configured'] or not context['model']:
                    return self.finish(identifier, 'FAILED_CONFIG', '请安全配置OPENAI_API_KEY和OPENAI_TEXT_MODEL；未发送请求')
                write_json_atomic(directory / 'diagnosis.json', context['diagnosis'])
                self.repo.update_job(identifier, status='RUNNING', progress=35, message='后端模型正在生成；当前编辑保持独立')
                write_json_atomic(directory / 'state.json', {'stage':'GENERATE', 'effect':'DISPATCHING', 'attempts':1})
                try:
                    result = generate_content(context, model=context['model'], root=self.root, expected_provider=context['provider'])
                except ProviderResultUnknown:
                    return self.finish(identifier, 'RESULT_UNKNOWN', '模型请求结果未知；不能自动重复收费')
                except InvalidModelOutput as error:
                    write_json_atomic(directory / 'provider-evidence.json', error.evidence)
                    return self.finish(identifier, 'FAILED_OUTPUT', '模型结果未通过校验，用量证据已保留，不自动再生成')
                except (RuntimeError, ValueError):
                    return self.finish(identifier, 'FAILED_PROVIDER', '模型请求被拒绝或配置无效；未自动重试')
                write_json_atomic(directory / 'result.json', result)
                write_json_atomic(directory / 'state.json', {'stage':'VALIDATE', 'effect':'COMPLETED', 'attempts':1})
            current = self.products.source(source['store_id'], source['listing_id'])
            if current['source_revision'] != source['source_revision']:
                return self.finish(identifier, 'SOURCE_CHANGED', '来源已变化，生成结果已保留，未覆盖当前版本')
            versions = self.repo.versions(source['store_id'], source['listing_id'])
            if (versions[0]['version'] if versions else 0) != context['expected_version']:
                return self.finish(identifier, 'VERSION_CHANGED', '已有人工新版本，生成结果已保留，未覆盖')
            if self.repo.job(identifier)['cancel_requested']:
                return self.finish(identifier, 'CANCELLED', '生成结果已保留，未发送后续步骤')
            if 'images' in context['fields']:
                from backend.services.listing_image_tools import prepare_listing_images
                if not result.get('image_plan'):
                    return self.finish(identifier, 'IMAGES_BLOCKED', '没有有效图片方案，文案结果保留，不能冒充图片已完成')
                write_json_atomic(directory/'state.json', {'stage':'IMAGES','effect':'COMPLETED_TEXT'})
                try:
                    result['assets'] = prepare_listing_images(self.root,identifier,context,result['image_plan'])
                except ProviderResultUnknown:
                    return self.finish(identifier, 'RESULT_UNKNOWN', '图片调用结果未知，文案和已生成图片保留；禁止自动重复收费')
                except (ValueError, RuntimeError, OSError):
                    return self.finish(identifier, 'IMAGES_BLOCKED', '图片来源、权限或配置尚未满足；保留文案与阶段证据')
                write_json_atomic(directory/'result.json',result)
            before = versions[0]['content'] if versions else {}
            defaults={**source,'tags':source['tags_text'].split(),'rich_content':{'modules':[]}}
            content = {key:before.get(key, defaults.get(key,''))
                       for key in ('title_ru', 'description_ru', 'tags', 'rich_content')}
            for key in context['fields']:
                if key in result['content']:
                    content[key] = result['content'][key]
            content['baseline_period'] = context['period']
            try:
                clean = self.products.validate_content(source, content, before)
            except (ValueError, RuntimeError):
                return self.finish(identifier, 'REVIEW_BLOCKED', '模型内容与当前商品字段或依据不一致，结果已保留，未自动再收费')
            clean.update(generation_source='BACKEND_MODEL_API', generation_job_id=identifier,
                         changed_fields=sorted(set(context['fields']) | (set(before.get('changed_fields',[])) if before else set())),
                         provider_evidence=result['provider_evidence'], ai_strategy=result['strategy'],
                         ai_unresolved_facts=result['unresolved_facts'], image_plan=result['image_plan'])
            if result.get('assets'):
                clean['generated_images'] = result['assets']
            if 'tags' in context['fields']:
                clean['tag_origins'] = [{'tag':tag, 'kind':'AI_CANDIDATE','source_revision':source['source_revision'], 'market_metrics':None} for tag in clean['tags']]
            blocking = {'SPEC_UNVERIFIED', 'SPEC_CONFLICT', 'BRAND_UNVERIFIED', 'CONTENT_MISSING', 'TAG_UNRELATED'}
            write_json_atomic(directory / 'validation.json', {'checks':clean['checks'], 'unresolved_facts':result['unresolved_facts']})
            if result['unresolved_facts'] or any(check['kind'] in blocking for check in clean['checks']):
                return self.finish(identifier, 'REVIEW_BLOCKED', '生成结果已保存，但事实校验未通过；请核对来源，不能提交')
            try:
                version = self.repo.save_version(source['store_id'], source['listing_id'], clean, source['source_revision'], context['expected_version'], context['actor'])
            except RuntimeError:
                return self.finish(identifier, 'VERSION_CHANGED', '保存时版本已变化，生成结果保留待人工合并')
            write_json_atomic(directory / 'version.json', version)
            write_json_atomic(directory / 'state.json', {'stage':'REVIEW', 'effect':'COMPLETED', 'attempts':1})
            return self.finish(identifier, 'REVIEW_REQUIRED', '智能结果已保存为独立版本；尚未平台提交，需要整批人工确认')
        finally:
            lock.close()
