"""Configure runtime model credentials locally with hidden terminal input."""
import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.integrations.ai.provider_config import configure, endpoint
from scripts.openai_images2 import NoRedirect


def available_models(base_url, key):
    request = urllib.request.Request(endpoint(base_url) + '/models', headers={'Authorization':f'Bearer {key}'})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise ValueError(f'模型列表HTTP {error.code}，请核对网关密钥/权限；未调用收费模型') from None
    except (OSError, ValueError):
        raise ValueError('模型列表无法读取；未调用收费模型') from None
    return sorted({item['id'] for item in payload.get('data', []) if isinstance(item, dict)
                   and isinstance(item.get('id'), str) and key not in item['id']})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--url', default='https://api.openai.com/v1')
    parser.add_argument('--model')
    parser.add_argument('--api-mode', choices=('responses','chat_completions'), default='responses')
    parser.add_argument('--configure-stdin',action='store_true',help='Private JSON pipe from the local password dialog; never a command argument')
    parser.add_argument('--read-models-stdin',action='store_true')
    args = parser.parse_args(argv)
    url = endpoint(args.url)
    private=json.load(sys.stdin) if args.configure_stdin or args.read_models_stdin else {}
    if not isinstance(private,dict) or set(private)-{'api_key','model','models'}:raise ValueError('本机密码窗口输入格式不正确')
    key = str(private.get('api_key','')).strip() if private else getpass.getpass('输入新模型密钥（不回显；不要使用已发到聊天的密钥）：').strip()
    if not key or any(ord(char)<33 or ord(char)>126 for char in key):raise ValueError('密钥没有正确粘贴（为空或包含控制字符）；未请求网关，请使用本机密码窗口')
    if args.read_models_stdin:
        print(json.dumps({'models':available_models(url,key)},ensure_ascii=False));return 0
    model = private.get('model') or args.model
    if not model:
        models = available_models(url, key)
        print('账号模型列表：' + ', '.join(models))
        model = input('输入要使用的文本模型ID：').strip()
        if model not in models:
            raise ValueError('模型不在账号返回的可用列表中；未保存配置')
    result = configure(args.root.resolve(), {'base_url':url,'model':model,'models':private.get('models') or [model],
                                             'api_mode':args.api_mode,'api_key':key})
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
