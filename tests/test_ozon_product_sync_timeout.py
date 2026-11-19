import unittest

from backend.integrations.ozon.product_client import OzonProductApiError, OzonProductClient


class OzonProductSyncTimeoutTest(unittest.TestCase):
    def test_timeout_is_sanitized_after_three_attempts(self):
        attempts = 0

        def transport(endpoint, payload):
            nonlocal attempts
            attempts += 1
            raise TimeoutError("Api-Key secret-value")

        client = OzonProductClient("client", "secret-value", transport=transport, sleep=lambda _: None)

        with self.assertRaises(OzonProductApiError) as caught:
            client.list_products()
        self.assertEqual(3, attempts)
        self.assertNotIn("secret-value", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
