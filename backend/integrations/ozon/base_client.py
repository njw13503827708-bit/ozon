"""Shared read-only Ozon Seller API transport."""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from . import ozon_endpoints


Transport = Callable[[str, dict[str, Any] | None], dict[str, Any]]


class OzonApiError(RuntimeError):
    def __init__(
        self, endpoint: str, message: str, status_code: int | None = None,
        *, retry_after: float | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.status_code = status_code
        self.retry_after = retry_after
        status = f" HTTP {status_code}" if status_code is not None else ""
        super().__init__(f"Ozon request failed{status} for {endpoint}: {message}")


class OzonBaseClient:
    def __init__(
        self, client_id: str, api_key: str, *, base_url: str | None = None,
        timeout_seconds: float = 30, transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not client_id.strip() or not api_key.strip():
            raise ValueError("Ozon Client-Id and Api-Key are required")
        self._client_id = client_id.strip()
        self._api_key = api_key.strip()
        self.base_url = (base_url or os.getenv("OZON_API_BASE_URL") or ozon_endpoints.DEFAULT_BASE_URL).rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._transport = transport
        self._sleep = sleep

    @property
    def client_id(self) -> str:
        return self._client_id

    def __repr__(self) -> str:
        return f"{type(self).__name__}(credentials=<hidden>)"

    def _safe_message(self, value: Any) -> str:
        message = str(value or "Ozon request failed")
        for secret in (self._api_key, self._client_id):
            message = message.replace(secret, "<hidden>")
        return message[:500]

    def _request_once(self, endpoint: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        if self._transport is not None:
            result = self._transport(endpoint, payload)
            if not isinstance(result, dict):
                raise OzonApiError(endpoint, "response must be a JSON object")
            return result
        headers = {
            "Client-Id": self._client_id,
            "Api-Key": self._api_key,
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{endpoint}",
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read().decode("utf-8", errors="replace")).get("message")
            except (ValueError, AttributeError):
                message = None
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                delay = min(60.0, max(0.0, float(retry_after))) if retry_after else None
            except ValueError:
                delay = None
            raise OzonApiError(
                endpoint, self._safe_message(message or "Ozon rejected the request"),
                exc.code, retry_after=delay,
            ) from None
        except (TimeoutError, urllib.error.URLError) as exc:
            raise OzonApiError(endpoint, self._safe_message(exc)) from None
        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            raise OzonApiError(endpoint, "response was not valid JSON") from None
        if not isinstance(result, dict):
            raise OzonApiError(endpoint, "response must be a JSON object")
        return result

    def _post_json(self, endpoint: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        for attempt in range(1, 4):
            started = time.perf_counter()
            try:
                result = self._request_once(endpoint, payload)
                items = result.get("items")
                if items is None and isinstance(result.get("result"), dict):
                    items = result["result"].get("items")
                logging.info(
                    "OZON API endpoint=%s duration_ms=%d status=200 items=%d attempt=%d",
                    endpoint, int((time.perf_counter() - started) * 1000),
                    len(items) if isinstance(items, list) else 0, attempt,
                )
                return result
            except (TimeoutError, urllib.error.URLError) as exc:
                error = OzonApiError(endpoint, self._safe_message(exc))
            except OzonApiError as exc:
                error = OzonApiError(
                    endpoint, self._safe_message(exc), exc.status_code,
                    retry_after=exc.retry_after,
                )
            except Exception as exc:
                raise OzonApiError(endpoint, self._safe_message(exc)) from None
            logging.warning(
                "OZON API endpoint=%s duration_ms=%d status=%s attempt=%d",
                endpoint, int((time.perf_counter() - started) * 1000),
                error.status_code or "NETWORK", attempt,
            )
            if attempt == 3 or (error.status_code is not None and error.status_code != 429 and error.status_code < 500):
                raise error from None
            self._sleep(error.retry_after if error.retry_after is not None else 2 ** (attempt - 1))
        raise AssertionError("unreachable")
