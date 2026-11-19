import unittest

from backend.integrations.ozon.account_client import OzonAccountClient
from backend.integrations.ozon.base_client import OzonApiError
from backend.integrations.ozon.ozon_endpoints import SELLER_INFO


class OzonAccountClientTest(unittest.TestCase):
    def test_reads_documented_seller_company_without_invented_seller_id(self):
        calls = []

        def transport(endpoint, payload):
            calls.append((endpoint, payload))
            return {"company": {"name": "ООО Тест", "legal_name": "Общество Тест", "currency": "RUB"}}

        client = OzonAccountClient("client-123", "secret", transport=transport)
        result = client.get_seller_info()
        self.assertEqual([("/v1/seller/info", None)], calls)
        self.assertEqual("client-123", client.client_id)
        self.assertEqual("ООО Тест", result["company"]["name"])
        self.assertEqual("/v1/seller/info", SELLER_INFO)

    def test_missing_company_identity_fails_closed(self):
        for response in ({"result": []}, {"company": {}}, {"company": {"name": 123}}):
            with self.subTest(response=response):
                client = OzonAccountClient("client", "secret", transport=lambda *_: response)
                with self.assertRaises(OzonApiError):
                    client.get_seller_info()


if __name__ == "__main__":
    unittest.main()
