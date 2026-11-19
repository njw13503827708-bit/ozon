import io
import unittest
import urllib.error
from unittest.mock import patch

from backend.integrations.ozon.base_client import OzonApiError, OzonBaseClient


class OzonBaseClientTest(unittest.TestCase):
    def test_timeout_retries_three_times_and_scrubs_credentials(self):
        calls = []

        def transport(endpoint, payload):
            calls.append(endpoint)
            raise TimeoutError("client-123 secret-key timed out")

        client = OzonBaseClient("client-123", "secret-key", transport=transport, sleep=lambda _: None)
        with self.assertLogs(level="WARNING") as logs, self.assertRaises(OzonApiError) as caught:
            client._post_json("/v1/seller/info", {})
        self.assertEqual(3, len(calls))
        self.assertNotIn("client-123", str(caught.exception) + str(logs.output) + repr(client))
        self.assertNotIn("secret-key", str(caught.exception) + str(logs.output) + repr(client))

    def test_network_failure_retries_but_nonretryable_4xx_does_not(self):
        attempts = []

        def transport(endpoint, payload):
            attempts.append(endpoint)
            if len(attempts) == 1:
                raise urllib.error.URLError("offline")
            raise OzonApiError(endpoint, "forbidden", 403)

        client = OzonBaseClient("client", "secret", transport=transport, sleep=lambda _: None)
        with self.assertRaises(OzonApiError) as caught:
            client._post_json("/v1/seller/info", {})
        self.assertEqual(403, caught.exception.status_code)
        self.assertEqual(2, len(attempts))

    def test_429_uses_retry_after_and_5xx_is_bounded(self):
        sleeps = []
        attempts = []

        def transport(endpoint, payload):
            attempts.append(endpoint)
            if len(attempts) == 1:
                raise OzonApiError(endpoint, "rate limit", 429, retry_after=3)
            if len(attempts) == 2:
                raise OzonApiError(endpoint, "server error", 503)
            return {"company": {"name": "Test"}}

        client = OzonBaseClient("client", "secret", transport=transport, sleep=sleeps.append)
        self.assertEqual("Test", client._post_json("/v1/seller/info", {})["company"]["name"])
        self.assertEqual([3, 2], sleeps)

    def test_http_error_retry_after_is_read_and_message_is_scrubbed(self):
        error = urllib.error.HTTPError(
            "https://api-seller.ozon.ru/v1/seller/info", 429, "rate limit",
            {"Retry-After": "4"}, io.BytesIO(b'{"message":"secret-key limited"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            client = OzonBaseClient("client", "secret-key", sleep=lambda _: None)
            with self.assertRaises(OzonApiError) as caught:
                client._request_once("/v1/seller/info", {})
        self.assertEqual(4, caught.exception.retry_after)
        self.assertNotIn("secret-key", str(caught.exception))

    def test_invalid_json_and_non_object_response_fail_closed(self):
        client = OzonBaseClient("client", "secret", transport=lambda *_: [])
        with self.assertRaises(OzonApiError):
            client._post_json("/v1/seller/info", {})
        response = io.BytesIO(b"not-json")
        with patch("urllib.request.urlopen", return_value=response):
            client = OzonBaseClient("client", "secret", sleep=lambda _: None)
            with self.assertRaises(OzonApiError):
                client._request_once("/v1/seller/info", {})


if __name__ == "__main__":
    unittest.main()
