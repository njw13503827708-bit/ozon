import unittest

from backend.integrations.ozon.product_client import OzonProductApiError, OzonProductClient


class OzonProductSyncRetryTest(unittest.TestCase):
    def test_network_error_without_http_status_retries(self):
        attempts = 0

        def transport(endpoint, payload):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OzonProductApiError(endpoint, "SSL handshake timed out")
            return {"result": {"items": [], "last_id": ""}}

        client = OzonProductClient("client", "secret", transport=transport, sleep=lambda _: None)

        client.list_products()
        self.assertEqual(2, attempts)

    def test_temporary_failures_retry_then_succeed(self):
        attempts = 0

        def transport(endpoint, payload):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise TimeoutError("temporary")
            return {"result": {"items": [], "last_id": ""}}

        client = OzonProductClient("client", "secret", transport=transport, sleep=lambda _: None)

        self.assertEqual([], client.list_products()["result"]["items"])
        self.assertEqual(3, attempts)


if __name__ == "__main__":
    unittest.main()
