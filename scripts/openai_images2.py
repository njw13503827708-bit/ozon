"""Generate registered product-reference candidates; never grant a QC PASS."""
import base64
import hashlib
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image
from scripts.image_asset_boundaries import validate_generated_output, validate_product_reference
from scripts.pipeline_runtime import write_json_atomic

MODEL = "gpt-image-2"
SOURCE = "openai_images2"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def slot_request(product_dir: Path, slot: dict) -> tuple:
    """Validate current inputs for both generation and checkpoint verification."""
    prompt = str(slot.get("prompt") or "").strip()
    if not prompt or len(prompt) > 32000:
        raise ValueError("生图提示词缺失或超出接口限制")
    references = slot.get("reference_images") or slot.get("reference_product_images") or []
    if not isinstance(references, list) or not 1 <= len(references) <= 16:
        raise ValueError("生图需要 1 至 16 张本商品已登记参考图")
    output = validate_generated_output(product_dir, slot.get("output_path"))
    if output.suffix.lower() != ".png":
        raise ValueError("生成候选必须保存为 PNG")
    images, inputs = [], []
    for reference in references:
        path = validate_product_reference(product_dir, reference)
        content = path.read_bytes()
        with Image.open(io.BytesIO(content)) as image:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
            image.verify()
        if not mime:
            raise ValueError("参考图仅支持 PNG/JPEG/WebP")
        data_url = f"data:{mime};base64," + base64.b64encode(content).decode()
        if len(data_url) > 20971520:
            raise ValueError("参考图超出官方接口输入限制")
        images.append({"image_url": data_url})
        inputs.append({"path": str(reference), "sha256": hashlib.sha256(content).hexdigest()})
    prompt += "\nPreserve product identity, structure, color and accessories exactly. Output a 3:4 product image."
    if len(prompt) > 32000:
        raise ValueError("生图提示词超出接口限制")
    body = {"model": MODEL, "prompt": prompt, "images": images, "n": 1,
            "size": "960x1280", "quality": "high", "output_format": "png"}
    return output, body, inputs


def generate_slot(product_dir: Path, slot: dict, *, transport=None) -> dict:
    from backend.integrations.ai.provider_config import settings
    if not settings(product_dir.parent.parent)['key']:
        raise ValueError('请在 ERP 后台安全配置 OPENAI_API_KEY')
    output, body, inputs = slot_request(product_dir, slot)
    proof = edit_registered_images(output, body, inputs, root=product_dir.parent.parent, transport=transport)
    proof.update(product_id=product_dir.name, slot=str(slot.get('slot') or ''), output_path=str(slot['output_path']))
    write_json_atomic(output.with_suffix('.generation.json'), proof)
    return proof


def edit_registered_images(output: Path, body: dict, inputs: list, *, root: Path, transport=None) -> dict:
    """Shared paid request; callers enforce their distinct Product/Listing ownership."""
    from backend.integrations.ai.provider_config import settings
    from scripts.batch_upload_approval import batch_upload_lock
    config = settings(root)
    key = config['key'].strip()
    if not key:
        raise ValueError('请在 ERP 后台安全配置 OPENAI_API_KEY')
    if body.get('model') != MODEL or body.get('n') != 1:
        raise ValueError('本轮生图仅允许已指定的gpt-image-2和一次一张')
    with batch_upload_lock(output.parent / '.locks' / output.stem):
        return _edit_once(output, body, inputs, config, transport)


def _edit_once(output, body, inputs, config, transport):
    from backend.integrations.ai.openai_content import ProviderResultUnknown
    prompt = body['prompt']
    proof_path = output.with_suffix('.generation.json')
    if output.is_file() and proof_path.is_file():
        existing = json.loads(proof_path.read_text(encoding="utf-8"))
        if (existing.get("model") == MODEL and existing.get("references") == inputs
                and existing.get("prompt_sha256") == hashlib.sha256(prompt.encode()).hexdigest()
                and existing.get("sha256") == hashlib.sha256(output.read_bytes()).hexdigest()):
            return existing
    intent_path = output.with_suffix('.generation-intent.json')
    intent = json.loads(intent_path.read_text(encoding='utf-8')) if intent_path.is_file() else {}
    if intent.get('effect') in {'DISPATCHING','RESULT_UNKNOWN','FAILED_OUTPUT'}:
        raise ProviderResultUnknown('上次生图请求结果未知或输出不合格；禁止自动重复收费，请人工核查')
    url = config['base_url'] + '/images/edits'
    intent = {'effect':'DISPATCHING', 'model':MODEL, 'endpoint':url, 'attempts':1,
              'request_sha256':hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest(), 'references':inputs}
    write_json_atomic(intent_path, intent)
    request = urllib.request.Request(url,
        data=json.dumps(body).encode(), headers={"Authorization": f"Bearer {config['key']}", "Content-Type": "application/json"}, method="POST")
    # No automatic paid retry: a timeout can happen after the provider accepted the request.
    try:
        if transport:
            payload = transport(request)
            request_id = payload.get('_request_id')
        else:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=600) as response:
                payload = json.load(response)
                request_id = response.headers.get("x-request-id")
    except urllib.error.HTTPError as exc:
        if exc.code >= 500 or exc.code == 408:
            write_json_atomic(intent_path, {**intent, 'effect':'RESULT_UNKNOWN', 'http_status':exc.code})
            raise ProviderResultUnknown('生图网关超时或服务错误，结果未知；禁止自动重试') from None
        write_json_atomic(intent_path, {**intent, 'effect':'REJECTED', 'http_status':exc.code})
        raise RuntimeError(f"OpenAI 生图 HTTP {exc.code}；未自动重试") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        write_json_atomic(intent_path, {**intent, 'effect':'RESULT_UNKNOWN'})
        raise ProviderResultUnknown("OpenAI 生图连接中断；结果未知，未自动重试") from None
    except ValueError:
        write_json_atomic(intent_path, {**intent, 'effect':'RESULT_UNKNOWN'})
        raise ProviderResultUnknown("OpenAI 返回的响应不是有效 JSON，结果未知，未重试") from None
    try:
        content = base64.b64decode(payload["data"][0]["b64_json"], validate=True)
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            if image.format != "PNG" or width < 900 or height < 1200 or abs(width / height - .75) > .02:
                raise ValueError("生成图片尺寸或格式不合格")
            image.verify()
    except (KeyError, IndexError, TypeError, ValueError, OSError):
        write_json_atomic(intent_path, {**intent, 'effect':'FAILED_OUTPUT'})
        raise RuntimeError("OpenAI 返回的图片无效，未保存候选") from None
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".pending.png")
    temporary.write_bytes(content)
    temporary.replace(output)
    proof = {"status": "NEEDS_REVIEW", "generation_source": SOURCE, "model": MODEL, 'endpoint':url,
             "sha256": hashlib.sha256(content).hexdigest(),
             "prompt_sha256": hashlib.sha256(body["prompt"].encode()).hexdigest(), "references": inputs,
             "request_id": request_id, "usage": payload.get("usage"), "dimensions": {"width": width, "height": height}}
    write_json_atomic(proof_path, proof)
    write_json_atomic(intent_path, {**intent, 'effect':'COMPLETED', 'request_id':request_id, 'usage':payload.get('usage')})
    return proof
