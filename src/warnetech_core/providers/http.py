"""Shared HTTP plumbing for wire-based providers.

We speak to provider REST APIs with ``httpx`` directly rather than pulling in a
vendor SDK per provider: it keeps the dependency surface small and, more
importantly, keeps timeout/retry semantics identical across providers instead
of inheriting whatever each SDK decided.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..errors import ProviderError, RateLimitError, TimeoutError_

# Statuses where retrying the identical request can plausibly succeed.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class HTTPProviderMixin:
    """Owns a lazily-created ``httpx.AsyncClient`` and normalises failures."""

    name: str = "unset"

    def __init__(
        self,
        *,
        base_url: str,
        headers: dict[str, str],
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = headers
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._headers,
                timeout=self._timeout,
            )
        return self._client

    async def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST JSON and return the decoded body, normalising every failure."""
        try:
            response = await self.client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise TimeoutError_(f"request timed out: {exc}", provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"transport error: {exc}", provider=self.name, retryable=True
            ) from exc

        if response.status_code >= 400:
            raise self._error_for(response)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                f"provider returned non-JSON body: {response.text[:200]}",
                provider=self.name,
                status_code=response.status_code,
            ) from exc

    def _error_for(self, response: httpx.Response) -> ProviderError:
        message = self._extract_error_message(response)
        if response.status_code == 429:
            retry_after = None
            header = response.headers.get("retry-after")
            if header:
                try:
                    retry_after = float(header)
                except ValueError:
                    retry_after = None
            return RateLimitError(message, provider=self.name, retry_after=retry_after)
        return ProviderError(
            message,
            provider=self.name,
            status_code=response.status_code,
            retryable=response.status_code in RETRYABLE_STATUS,
        )

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:300] or response.reason_phrase
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error)
            if isinstance(error, str):
                return error
            if "message" in body:
                return str(body["message"])
        return str(body)[:300]

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
