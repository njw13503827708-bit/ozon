"""Explicit trusted endpoints and backend-only private credentials."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from backend.integrations.ozon.credential_store import CredentialStore
from scripts.pipeline_runtime import write_json_atomic

ROOT = Path(__file__).resolve().parents[3]
TRUSTED_HOSTS = {'api.openai.com', 'ai.opendoor.sbs'}
KEY_RECORD = {'id':'ai', 'client_id_env':'OPENAI_TEXT_MODEL', 'api_key_env':'OPENAI_API_KEY'}


def endpoint(value):
    parsed = urlsplit(str(value).strip())
    if (parsed.scheme != 'https' or parsed.hostname not in TRUSTED_HOSTS or parsed.port not in {None,443}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip('/') not in {'','/v1'}):
        raise ValueError('模型地址必须为已明确授权的HTTPS网关，不允许凭证URL、私网或未知地址')
    return f'https://{parsed.hostname}/v1'


def settings(root=None):
    root = Path(root or ROOT)
    path = root / 'config/ai-provider.json'
    config = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    try:
        credentials = CredentialStore(root/'runtime/ai-private').read(KEY_RECORD)
    except OSError:
        credentials = {}
    if not credentials.get('OPENAI_API_KEY'):
        try:
            credentials = CredentialStore(root).read(KEY_RECORD)
        except OSError:
            credentials = {}
    mode = config.get('api_mode', 'responses')
    if mode not in {'responses','chat_completions'}:
        raise ValueError('模型接口模式无效')
    model = credentials.get('OPENAI_TEXT_MODEL', '')
    models = config.get('models') or ([model] if model else [])
    return {'base_url':endpoint(config.get('base_url') or os.getenv('OPENAI_BASE_URL') or 'https://api.openai.com/v1'),
            'api_mode':mode, 'model':model, 'models':models,
            'key':credentials.get('OPENAI_API_KEY', '')}


def public_settings(root=None):
    value = settings(root)
    return {key:item for key,item in value.items() if key != 'key'} | {'key_configured':bool(value['key']), 'image_model':'gpt-image-2'}


def configure(root, payload):
    if not isinstance(payload, dict) or set(payload) - {'base_url','model','models','api_mode','api_key'}:
        raise ValueError('模型配置字段无效')
    base = endpoint(payload.get('base_url', ''))
    model = payload.get('model', '')
    models = payload.get('models', [model])
    mode = payload.get('api_mode', 'responses')
    if not isinstance(model, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}',model) or model.startswith('sk-'):
        raise ValueError('请输入账号支持的文本模型ID，不要把密钥填入模型名称')
    if (not isinstance(models, list) or not models
            or any(not isinstance(item, str) for item in models)
            or len(models) != len(set(models))
            or any(not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,100}',item)
                   or item.startswith(('sk-','gpt-image-')) for item in models) or model not in models):
        raise ValueError('请至少选择一个文本生成模型；图片模型不能用于商品文案生成')
    if mode not in {'responses','chat_completions'}:
        raise ValueError('请选择Responses或Chat Completions接口模式')
    old = settings(root)
    key = payload.get('api_key') or old['key']
    if not isinstance(key, str) or not key.strip():
        raise ValueError('新增配置需要密钥；编辑时留空会保留已有后台密钥')
    CredentialStore(Path(root)/'runtime/ai-private').write(KEY_RECORD, model, key)
    write_json_atomic(Path(root)/'config/ai-provider.json', {'base_url':base, 'api_mode':mode, 'models':models})
    return public_settings(root)
