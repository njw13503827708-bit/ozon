"""Bounded public-page capture; outputs the existing ERP collector contract.

No stealth fetcher, credentials, browser profile, proxy rotation or retry.
Public Ozon page positions are observations, never measured sales rankings.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import html as html_module
import math
import re
import socket
import ipaddress
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

MAX_HTML_BYTES = 6 * 1024 * 1024
USER_AGENT = "JLC-ERP-Collector/1.0"
FETCH_LOCK = threading.Lock()


def validate_url(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise ValueError("采集地址无效")
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("只允许无凭证的官方 HTTPS 页面")
    host = parts.hostname
    if host == "detail.1688.com" and re.fullmatch(r"/offer/[0-9]+\.html", parts.path):
        return urlunsplit(("https", host, parts.path, "", ""))
    if host in {"www.ozon.ru", "www.ozon.com"} and (
        parts.path.startswith("/category/") or parts.path in {"/search", "/search/"}
    ):
        return urlunsplit(("https", host, parts.path, parts.query, ""))
    raise ValueError("只支持 detail.1688.com 商品页和 Ozon 公开类目/搜索列表；不访问 Seller 后台")


def _selector(html):
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("页面大小超过6MB或格式无效")
    from scrapling.parser import Selector
    return Selector(html, adaptive=False)


def _text(selector, css):
    return " ".join(selector.css(css).getall()).strip()


def _blocked(page):
    title = _text(page, "title::text")
    if page.css('input[type="password"], #nc_1_wrapper, #cf-challenge-running, .captcha') or re.search(
        r"登录|login|access denied|доступ ограничен|captcha|验证码|滑块验证|security check", title, re.I
    ):
        return {"state": "blocked", "message": "页面要求登录或验证，请在浏览器中由本人处理后通过已有采集插件提交；未绕过验证。"}
    return None


def _objects(page):
    decoder = json.JSONDecoder()
    for script in page.css("script"):
        text = _text(script, "::text")
        if script.attrib.get("type") in {"application/json", "application/ld+json"}:
            try:
                yield json.loads(text)
            except ValueError:
                continue
        for match in re.finditer(r"(?:window\.)?(?:__INIT_DATA__|__INITIAL_STATE__|offerDetailData|iDetailData)\s*=\s*", text):
            try:
                yield decoder.raw_decode(text[match.end():].lstrip())[0]
            except ValueError:
                continue
        # Verified saved-page shape: parse its JSON argument, never execute JS.
        for match in re.finditer(r'\}\)\(window\.contextPath,\s*',text):
            try:
                value=decoder.raw_decode(_numeric_object_keys(text[match.end():].lstrip()))[0]
                data=value['result']['data']; gallery=data['gallery']['fields']; root=data['Root']['fields']['dataJson']
                yield {'offerId':gallery['offerId'],'offerTitle':gallery.get('subject'),'imageList':gallery.get('offerImgList',[]),
                       'skuModel':root.get('skuModel',{}),'price_context':data.get('mainPrice',{}).get('fields',{}).get('pricePreText')}
            except (ValueError,KeyError,TypeError):continue


def _numeric_object_keys(text):
    # The observed context includes unquoted numeric skuWeight keys outside strings.
    result=[];quoted=False;escape=False;position=0
    while position<len(text):
        char=text[position]
        if quoted:
            result.append(char)
            if escape:escape=False
            elif char=='\\':escape=True
            elif char=='"':quoted=False
            position+=1;continue
        if char=='"':quoted=True
        if char in '{,':
            match=re.match(r'([\{,]\s*)(\d+)(\s*:)',text[position:])
            if match:
                result.append(match[1]+'"'+match[2]+'"'+match[3]);position+=match.end();continue
        result.append(char);position+=1
    return ''.join(result)


def _walk(value, depth=0):
    # ponytail: bounded traversal, add explicit adapters when a new site contract is verified.
    if depth > 12:
        return
    if isinstance(value, dict):
        yield value
        for key, child in list(value.items())[:200]:
            if not re.search(r"recommend|related|similar|suggest", key, re.I):
                yield from _walk(child, depth + 1)
    elif isinstance(value, list):
        for child in value[:300]:
            yield from _walk(child, depth + 1)


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def _image(value):
    if isinstance(value, dict):
        value = value.get("imageUrl") or value.get("url") or value.get("originalImageURI")
    if not isinstance(value, str):
        return None
    url = urljoin("https://detail.1688.com/", value)
    parts = urlsplit(url)
    if parts.scheme == "https" and not parts.username and not parts.password and parts.port in (None, 443) and (
        parts.hostname == "alicdn.com" or (parts.hostname or "").endswith(".alicdn.com")
    ):
        return url
    return None


def parse_1688(html, url, captured_at):
    url = validate_url(url)
    if urlsplit(url).hostname != "detail.1688.com":
        raise ValueError("1688采集地址错误")
    page = _selector(html)
    base = {"kind": "1688", "source_url": url, "captured_at": captured_at,
            "source_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest()}
    blocked = _blocked(page)
    if blocked:
        return {**base, **blocked}
    offer_id = re.search(r"/offer/([0-9]+)", url).group(1)
    roots = list(_objects(page))
    identified = [v for root in roots for v in _walk(root) if str(v.get("offerId") or v.get("offer_id") or "") == offer_id]
    # Explicit mismatching product IDs never supply facts for the requested item.
    fallback = [v for root in roots for v in _walk(root) if (v.get("skuMap") or v.get("skuInfoMap"))
                and not v.get("offerId") and not v.get("offer_id")]
    has_identity = any(v.get("offerId") or v.get("offer_id") for root in roots for v in _walk(root))
    product = identified[0] if identified else (fallback[0] if fallback and not has_identity else {})
    nodes = list(_walk(product))
    warnings = []
    title = str(product.get("offerTitle") or product.get("subject") or product.get("productTitle") or "").strip()
    if not title:
        title = _text(page, "h1::text") or page.css('meta[property="og:title"]::attr(content)').get() or "unknown"
    if title == "unknown":
        title = re.sub(r"\s*[-–|]\s*(?:阿里巴巴|1688)\s*$", "", _text(page, "title::text")).strip() or "unknown"
    attrs = []
    for item in product.get("productAttributes") or product.get("attributes") or []:
        if isinstance(item, dict):
            name, value = item.get("name") or item.get("name_cn"), item.get("value") or item.get("value_cn")
            if name and value and len(str(name)) <= 40 and len(str(value)) <= 220:
                attrs.append({"name_cn": str(name), "value_cn": str(value), "source": "script_init_data", "source_text": f"{name}: {value}"})
    images = []
    for value in product.get("imageList") or product.get("images") or []:
        normalized = _image(value)
        if normalized and normalized not in images:
            images.append(normalized)
    if not images:
        normalized = _image(page.css('meta[property="og:image"]::attr(content)').get())
        if normalized:
            images.append(normalized)
    detail_images = []
    for value in product.get("detailImages") or []:
        normalized = _image(value)
        if normalized and normalized not in detail_images:
            detail_images.append(normalized)
    skus = []
    seen = set()
    for model in nodes:
        props, mapping = model.get("skuProps"), model.get("skuMap") or model.get("skuInfoMap")
        if not isinstance(props, list) or not isinstance(mapping, dict):
            continue
        groups = []
        for prop in props:
            if not isinstance(prop, dict):
                continue
            values = prop.get("value") or prop.get("values") or []
            if not isinstance(values, list):
                continue
            group = [(str(v.get("id") or v.get("valueId") or ""),
                      {"name_cn": str(prop.get("prop") or prop.get("name") or "unknown"), "value_cn": str(v.get("name") or "unknown"),
                       "source": "script_init_data", "source_text": str(v.get("name") or "unknown")}, _image(v))
                     for v in values if isinstance(v, dict)]
            if group:
                groups.append(group)
        if not groups:
            continue
        for combo in itertools.islice(itertools.product(*groups), 300):
            ids = [v[0] for v in combo]
            info = None
            for key, value in list(mapping.items())[:300]:
                names=[v[1]['value_cn'] for v in combo]
                parts=re.split(r"[;,:>\\|]+",html_module.unescape(str(key)))
                if isinstance(value,dict) and (ids and all(ids) and parts==ids or parts==names):
                    info = value
                    break
            if not info or not re.fullmatch(r"[0-9]+", str(info.get("skuId") or "")):
                warnings.append("部分规格没有精确匹配的真实skuId，已排除，未生成组合SKU。")
                continue
            sku_id = str(info["skuId"])
            if sku_id in seen:
                continue
            seen.add(sku_id)
            image = _image(info) or next((v[2] for v in combo if v[2]), None)
            price = _number(info.get("discountPrice") if info.get("discountPrice") is not None else info.get("price"))
            stock = _number(info.get("canBookCount"))
            options = [v[1] for v in combo]
            skus.append({"sku_id": sku_id, "sku_name": " / ".join(v["value_cn"] for v in options), "option_values": options,
                         "purchase_price": price, "price_source": "script_init_data" if price is not None else "unknown",
                         "image_url": image or "unknown", "sku_image_missing": image is None,
                         "availability": "out_of_stock" if stock == 0 else "unknown",
                         "source_data": {"skuId": sku_id, "sku_info_key": str(key), "has_real_sku_id": True,
                                         "price_quantity_threshold":_number(info.get('priceAmount')), 'price_context':product.get('price_context') or 'unknown',
                                         "stock_quantity":stock, "stock_state":"observed_zero" if stock==0 else "observed_positive" if stock is not None else "unknown",
                                         "sku_image_source": "sku_specific_image" if _image(info) else "sku_prop_value_image" if image else "missing"}})
    if not skus:
        warnings.append("未读到可验证的真实SKU；请登录后使用已有1688采集插件，不会凭标题生成变体。")
    if not images:
        warnings.append("未读取到本商品主图，禁止用推荐商品图片填补。")
    capture = {"source_platform": "1688", "source_url": url, "captured_at": captured_at,
               "page_title": _text(page, "title::text") or title, "title_cn": title,
               "supplier_name": product.get("companyName") or "unknown", "product_attributes": attrs,
               "price_information": {"currency": "CNY", "price_ranges": [
                   {key:row[key] for key in ('beginAmount','endAmount','price') if key in row}
                   for node in nodes for row in node.get('skuPriceScale',[]) if isinstance(row,dict)], "raw_text": product.get('price_context') or "unknown"},
               "minimum_order_quantity": {"value": _number(product.get("beginAmount")), "unit": "unknown", "raw_text": "unknown"},
               "main_images": [{"url": value, "source_order": i} for i, value in enumerate(images)],
               "detail_images": [{"url": value, "source_order": i} for i, value in enumerate(detail_images)],
               "skus": skus, "field_diagnostics": [{"field": "skus", "strategy": "script_init_data", "hit": bool(skus),
                     "failure_reason": "unknown" if skus else "real SKU data missing", "candidate_count": len(skus)}],
               "capture_warnings": list(dict.fromkeys(warnings)), "plugin_version": "scrapling-0.4.15-erp-v1",
               "raw_snapshot": {"all_raw_skus": skus, "source_sha256": base["source_sha256"], "capture_method": "scrapling"}}
    return {**base, "state": "ready" if skus and title != "unknown" else "incomplete", "capture": capture,
            "warnings": capture["capture_warnings"], "message": "已解析真实SKU，选择规格并沿用原采集入口保存。" if skus else warnings[0]}


def parse_ozon_list(html, url, captured_at):
    url = validate_url(url)
    if "ozon." not in urlsplit(url).hostname:
        raise ValueError("Ozon列表采集地址错误")
    page = _selector(html)
    base = {"kind": "ozon", "source_url": url, "captured_at": captured_at,
            "source_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
            "ranking_basis": "page_order", "sales_verified": False, "period_from": None, "period_to": None}
    blocked = _blocked(page)
    if blocked:
        return {**base, **blocked, "items": []}
    items, seen = [], set()
    widgets = page.css('[data-widget="searchResultsV2"], [data-widget="searchResults"]')
    for widget in widgets:
        for link in widget.css('a[href*="/product/"]'):
            href = urljoin(url, link.attrib.get("href", ""))
            parts = urlsplit(href)
            match = re.fullmatch(r"/product/(?:[^/]*-)?([0-9]+)/?", parts.path)
            if not match or parts.hostname != urlsplit(url).hostname or parts.scheme != "https":
                continue
            product_id = match.group(1)
            title = _text(link, "::text") or link.attrib.get("title") or link.css("img::attr(alt)").get()
            if not title or product_id in seen:
                continue
            seen.add(product_id)
            items.append({"product_id": product_id, "product_url": urlunsplit(("https", parts.hostname, parts.path, "", "")),
                          "title_ru": title, "page_position": len(items) + 1, "ordered_units": None,
                          "ordered_amount_rub": None, "source": "ozon_public_page_observation"})
    return {**base, "state": "ready" if items else "incomplete", "items": items,
            "page_title": _text(page, "h1::text") or _text(page, "title::text"),
            "message": "仅为网页排列观察，销量和统计周期未知；实际销量榜继续使用已有官方报表导入。" if items else "没有解析到公开商品列表；登录、页面结构或渲染尚待核查。"}


def fetch_options():
    return {"timeout": 15, "retries": 0, "follow_redirects": False,
            "stealthy_headers": False, "impersonate": None, "headers": {"User-Agent": USER_AGENT}}


def _render_public(url):
    from scrapling.fetchers import DynamicFetcher
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not edge.is_file():
        raise ValueError("未找到已有Edge浏览器，未自动安装浏览器")
    host = urlsplit(url).hostname

    def setup(page):
        def allow(route):
            request = route.request
            parts = urlsplit(request.url)
            # A fresh context only. Reject navigation away, login and unrelated hosts.
            if request.is_navigation_request():
                approved = request.url == url
            else:
                approved = parts.scheme == "https" and not parts.username and not parts.password and parts.port in (None, 443) and (
                    parts.hostname == host or (host == "detail.1688.com" and (parts.hostname or "").endswith(".alicdn.com"))
                )
            route.continue_() if approved else route.abort()
        page.route("**/*", allow)

    def retain_product_data(page):
        # Preserve only explicit product globals in the rendered DOM for the same parser.
        page.evaluate("""() => {
          const values = ['__INIT_DATA__','__INITIAL_STATE__','offerDetailData','iDetailData']
            .map(name => window[name]).filter(value => value && typeof value === 'object');
          if (!values.length) return;
          try {
            const text = JSON.stringify(values);
            if (text.length > 1000000) return;
            const script = document.createElement('script'); script.type = 'application/json';
            script.textContent = text; document.documentElement.appendChild(script);
          } catch {}
        }""")

    return DynamicFetcher.fetch(url, executable_path=str(edge), headless=True, google_search=False,
                                timeout=20000, wait=1500, network_idle=False, block_ads=True,
                                page_setup=setup, page_action=retain_product_data)


def capture_public(url, *, render=False):
    url = validate_url(url)
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    base = {"source_url": url, "captured_at": captured_at}
    if not FETCH_LOCK.acquire(blocking=False):
        return {**base, "state": "busy", "message": "已有采集任务运行，请完成后再试。"}
    try:
        host = urlsplit(url).hostname
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError("解析到非公网地址，已停止")
        from scrapling.fetchers import Fetcher
        options = fetch_options()
        robots_url = f"https://{host}/robots.txt"
        robots = Fetcher.get(robots_url, **options)
        if robots.status == 200:
            policy = RobotFileParser()
            policy.parse(bytes(robots.body).decode("utf-8", errors="replace").splitlines())
            if not policy.can_fetch(USER_AGENT, url):
                return {**base, "state": "blocked", "http_status": 200, "message": "robots.txt禁止此路径，已停止；可用本人操作的既有采集插件或授权报表。"}
        elif robots.status != 404:
            return {**base, "state": "blocked", "http_status": robots.status, "message": "robots.txt未能核实，未继续读取商品页面。"}
        response = _render_public(url) if render else Fetcher.get(url, **options)
        if response.status != 200 or validate_url(str(response.url)) != url:
            return {**base, "state": "blocked", "http_status": response.status, "message": "页面重定向或访问被限制，已停止；请本人在浏览器核查。"}
        body = bytes(response.body)
        if len(body) > MAX_HTML_BYTES:
            raise ValueError("页面大小超过6MB，已停止")
        html = body.decode(response.encoding or "utf-8", errors="replace")
        parse = parse_1688 if host == "detail.1688.com" else parse_ozon_list
        return {**parse(html, url, captured_at), "http_status": response.status, "capture_method": "public_browser" if render else "public_http"}
    except ImportError:
        return {**base, "state": "unavailable", "message": "未安装采集依赖，请使用采集专用环境 requirements-collector.txt。"}
    except Exception as exc:
        return {**base, "state": "failed", "error_type": type(exc).__name__, "message": "读取失败；未自动重试、未绕过限制，请检查网络或在浏览器操作。"}
    finally:
        FETCH_LOCK.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--html", type=Path, help="Parse a user-saved HTML file without network")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render", action="store_true", help="Use a fresh public browser context; no user login/profile")
    args = parser.parse_args()
    url = validate_url(args.url)
    if args.html:
        if args.html.stat().st_size > MAX_HTML_BYTES:
            raise ValueError("HTML exceeds6MB")
        html = args.html.read_text(encoding="utf-8")
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = (parse_1688 if urlsplit(url).hostname == "detail.1688.com" else parse_ozon_list)(html, url, stamp)
        result["capture_method"] = "user_supplied_html"
    else:
        result = capture_public(url, render=args.render)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"state": result["state"], "message": result.get("message"), "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0 if result["state"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
