"""Offline contracts: no invented SKUs/sales, and safe public-read boundaries."""
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
URL = "https://detail.1688.com/offer/594898070768.html"
STAMP = "2026-10-06T10:00:00+08:00"
DATA = {
    "offerId": "594898070768", "offerTitle": "铝合金折叠桌", "companyName": "离线测试供应商",
    "productAttributes": [{"name": "材质", "value": "铝合金"}],
    "imageList": ["https://cbu01.alicdn.com/img/main.jpg"],
    "skuProps": [{"prop": "颜色", "value": [
        {"name": "黑色", "id": "10", "imageUrl": "https://cbu01.alicdn.com/img/black.jpg"},
        {"name": "银色", "id": "11", "imageUrl": "https://cbu01.alicdn.com/img/silver.jpg"}]}],
    "skuMap": {"10": {"skuId": "10001", "discountPrice": "39.50", "canBookCount": 0},
               "11": {"skuId": "10002", "discountPrice": "45", "canBookCount": 12}},
}


def document(data):
    return '<html><h1>铝合金折叠桌</h1><script>window.__INIT_DATA__ = ' + json.dumps(data) + ';</script></html>'


class ScraplingCollectorTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.find_spec("scripts.scrapling_collector")
        self.assertIsNotNone(spec, "Scrapling adapter has not been implemented")
        from scripts import scrapling_collector
        self.collector = scrapling_collector
        dns = patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))])
        dns.start()
        self.addCleanup(dns.stop)

    def test_real_sku_ids_keep_exact_variant_price_image_and_zero_stock(self):
        result = self.collector.parse_1688(document(DATA), URL, STAMP)
        self.assertEqual(result["state"], "ready")
        capture = result["capture"]
        self.assertEqual(capture["title_cn"], "铝合金折叠桌")
        self.assertEqual([(s["sku_id"], s["sku_name"], s["purchase_price"]) for s in capture["skus"]],
                         [("10001", "黑色", 39.5), ("10002", "银色", 45.0)])
        self.assertEqual(capture["skus"][0]["availability"], "out_of_stock")
        self.assertEqual(capture["skus"][1]["image_url"], "https://cbu01.alicdn.com/img/silver.jpg")
        from jsonschema import Draft202012Validator
        schema = json.loads((ROOT / "templates/collector-capture.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(capture)), [])

    def test_saved_window_context_exact_name_skus_numeric_keys_and_identity(self):
        product={'gallery':{'fields':{'offerId':'594898070768','subject':'真实多规格商品','offerImgList':['https://cbu01.alicdn.com/img/main.jpg']}},
            'Root':{'fields':{'dataJson':{'skuModel':{'skuProps':[{'prop':'规格','value':[
                {'name':'黑色','imageUrl':'https://cbu01.alicdn.com/img/black.jpg'}, {'name':'白色'}]}],
                'skuInfoMap':{'黑色':{'skuId':123,'price':'46.00','priceAmount':200,'canBookCount':953},
                              '白色':{'skuId':456,'price':None,'canBookCount':0}},
                'skuPriceScale':[{'beginAmount':200,'price':'46.00'}]}}}}}
        body=json.dumps({'result':{'data':product}},ensure_ascii=False)
        body=body[:-1]+',"skuWeight":{123:0.09}}'
        html='<script>window.context=(function(b,d){return d})(window.contextPath,'+body+');</script>'
        result=self.collector.parse_1688(html,URL,STAMP)
        self.assertEqual('ready',result['state'])
        skus=result['capture']['skus']
        self.assertEqual([('123','黑色',46.0),('456','白色',None)],[(s['sku_id'],s['sku_name'],s['purchase_price']) for s in skus])
        self.assertEqual(200,skus[0]['source_data']['price_quantity_threshold'])
        self.assertEqual(953,skus[0]['source_data']['stock_quantity'])
        self.assertEqual('out_of_stock',skus[1]['availability'])
        self.assertEqual([],self.collector.parse_1688(html,URL.replace('594898070768','99999'),STAMP)['capture']['skus'])

    def test_unmatched_combinations_are_not_fabricated_and_recommendations_are_excluded(self):
        data = {**DATA, "skuMap": {"10": {"skuId": "10001", "discountPrice": "39.50"}}}
        data["recommendations"] = [{"offerId": "99999", "offerTitle": "其他商品", "skuMap": {"99": {"skuId": "999999"}}}]
        result = self.collector.parse_1688(document(data), URL, STAMP)
        self.assertEqual([s["sku_id"] for s in result["capture"]["skus"]], ["10001"])
        self.assertTrue(result["warnings"])

    def test_wrong_offer_cannot_supply_product_facts(self):
        result = self.collector.parse_1688(document({**DATA, "offerId": "999999"}), URL, STAMP)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(result["capture"]["skus"], [])

    def test_visible_page_title_is_usable_without_inventing_missing_skus(self):
        result = self.collector.parse_1688("<title>厂家折叠桌 - 阿里巴巴</title>", URL, STAMP)
        self.assertEqual(result["capture"]["title_cn"], "厂家折叠桌")
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(result["capture"]["skus"], [])

    def test_missing_price_and_images_stay_unknown(self):
        data = {**DATA, "imageList": [], "skuProps": [{"prop": "颜色", "value": [{"name": "黑色", "id": "10"}]}],
                "skuMap": {"10": {"skuId": "10001"}}}
        result = self.collector.parse_1688(document(data), URL, STAMP)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["capture"]["main_images"], [])
        self.assertIsNone(result["capture"]["skus"][0]["purchase_price"])
        self.assertEqual(result["capture"]["skus"][0]["image_url"], "unknown")
        self.assertEqual(result["capture"]["skus"][0]["availability"], "unknown")

    def test_changed_sku_structure_does_not_crash_or_invent_variants(self):
        data = {**DATA, "skuProps": ["changed-layout"], "skuMap": {"10": {"skuId": "10001"}}}
        result = self.collector.parse_1688(document(data), URL, STAMP)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(result["capture"]["skus"], [])

    def test_login_and_challenge_return_action_required_without_fake_products(self):
        for html in ('<title>登录 - 1688</title><form action="https://login.1688.com"><input type="password"></form>',
                     '<title>Access denied</title><div id="nc_1_wrapper">请完成滑块验证</div>'):
            result = self.collector.parse_1688(html, URL, STAMP)
            self.assertEqual(result["state"], "blocked")
            self.assertNotIn("capture", result)

    def test_public_page_positions_and_reviews_never_become_sales(self):
        html = '''<h1>Популярные товары</h1><div data-widget="searchResultsV2">
        <article><a href="/product/stol-12345/">Стол</a><span>500 отзывов</span></article>
        <article><a href="/product/stol-12345/">Стол</a></article>
        <article><a href="/product/stul-67890/">Стул</a></article></div>'''
        result = self.collector.parse_ozon_list(html, "https://www.ozon.ru/category/mebel-15000/?sorting=rating", STAMP)
        self.assertEqual([i["product_id"] for i in result["items"]], ["12345", "67890"])
        self.assertEqual([i["page_position"] for i in result["items"]], [1, 2])
        self.assertFalse(result["sales_verified"])
        self.assertIsNone(result["period_from"])
        self.assertTrue(all(i["ordered_units"] is None for i in result["items"]))

    def test_url_boundary_rejects_private_hosts_credentials_seller_and_unapproved_paths(self):
        invalid = ["http://127.0.0.1:8798", "https://detail.1688.com.evil.test/offer/1.html",
                   "https://u:p@detail.1688.com/offer/1.html", "https://seller.ozon.ru/app/dashboard/main",
                   "https://www.ozon.ru/api/private", "https://detail.1688.com:9999/offer/1.html"]
        for url in invalid:
            with self.assertRaises(ValueError, msg=url):
                self.collector.validate_url(url)

    def test_redirect_stops_without_following(self):
        class Response:
            status = 302
            body = b""
            url = URL
        with patch("scrapling.fetchers.Fetcher.get", return_value=Response()) as request:
            result = self.collector.capture_public(URL)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(result["http_status"], 302)

    def test_robot_denial_does_not_request_the_product(self):
        class Response:
            status = 200
            body = b"User-agent: *\nDisallow: /offer/"
        with patch("scrapling.fetchers.Fetcher.get", return_value=Response()) as request:
            result = self.collector.capture_public(URL)
        self.assertEqual(result["state"], "blocked")
        self.assertEqual(request.call_count, 1)
        options = request.call_args.kwargs
        self.assertIs(options.get("follow_redirects"), False)
        self.assertIs(options.get("stealthy_headers"), False)
        self.assertIsNone(options["impersonate"])

    def test_installed_fetcher_accepts_options_without_attempting_real_network(self):
        from scrapling.fetchers import Fetcher
        # Use the real library argument adapter, block only the final network IO.
        with patch("curl_cffi.requests.Session.request", side_effect=AssertionError("IO_REACHED")):
            with self.assertRaisesRegex(AssertionError, "IO_REACHED"):
                Fetcher.get("https://detail.1688.com/robots.txt", **self.collector.fetch_options())

    def test_explicit_dynamic_mode_reuses_parser_only_after_robots_permission(self):
        class Robots:
            status = 200
            body = b"User-agent: *\nAllow: /"
        class Rendered:
            status = 200
            url = URL
            body = document(DATA).encode()
            encoding = "utf-8"
        with patch("scrapling.fetchers.Fetcher.get", return_value=Robots()), patch("scrapling.fetchers.DynamicFetcher.fetch", return_value=Rendered()) as render:
            result = self.collector.capture_public(URL, render=True)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["capture_method"], "public_browser")
        self.assertEqual(render.call_count, 1)
        self.assertNotIn("cookies", render.call_args.kwargs)
        self.assertFalse(render.call_args.kwargs["google_search"])


if __name__ == "__main__":
    unittest.main()
