"""
congress_client/client.py

Low-level HTTP client for the congress.gov API. Handles:
  - auth (api_key as a query param)
  - rate limiting (5,000 requests/hour per key, enforced client-side)
  - retries with backoff (transient errors + 429s, respecting Retry-After)
  - pagination (follows `pagination.next` and unwraps the response envelope)

Everything in endpoints.py is built on top of the two public methods here:
`get_congress()` for single requests, `get_paginated()` for list endpoints.

"""

from __future__ import annotations

import logging
import os
import time
from typing import Iterator, Optional
from urllib.parse import urlparse, parse_qs

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

load_dotenv()  # reads .env into environment variables, if present

BASE_URL = "https://api.congress.gov/v3"

# congress.gov's stated ceiling. Kept slightly conservative on purpose --
# see _RateLimiter below.
MAX_REQUESTS_PER_HOUR = 5_000


class CongressAPIError(Exception):
    """Raised for non-retryable HTTP errors from the API."""

    def __init__(self, status_code: int, message: str, url: str):
        self.status_code = status_code
        self.url = url
        super().__init__(f"[{status_code}] {message} ({url})")


class _RateLimiter:
    """
    Simple leaky-bucket throttle: enforces a minimum interval between
    requests so that (sustained over an hour) we stay under the API's cap.
    """

    def __init__(self, max_per_hour: int = MAX_REQUESTS_PER_HOUR, safety_margin: float = 0.9):
        # safety_margin leaves headroom below the stated cap for clock drift,
        # retries, and any other process sharing the same key.
        effective_max = max(1, int(max_per_hour * safety_margin))
        self._min_interval = 3600.0 / effective_max
        self._last_call_at: Optional[float] = None

    def wait(self) -> None:
        if self._last_call_at is not None:
            elapsed = time.monotonic() - self._last_call_at
            remaining = self._min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_call_at = time.monotonic()


class CongressClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = BASE_URL,
        max_requests_per_hour: int = MAX_REQUESTS_PER_HOUR,
        timeout: float = 30.0,
        page_limit: int = 250,  # API max per page
    ):
        
        self.api_key = api_key or os.environ.get("CONGRESS_API_KEY")
        if not self.api_key:
            raise ValueError(
                "No API key provided. Pass api_key= or set CONGRESS_API_KEY."
            )

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.page_limit = page_limit

        self._rate_limiter = _RateLimiter(max_requests_per_hour)
        self._session = self._build_session()

    # ----------------------------------------------------------------
    # Session / retry setup
    # ----------------------------------------------------------------

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=5,
            backoff_factor=1.5,  # 1.5s, 3s, 6s, 12s, 24s
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def get_congress(self, path: str, params: Optional[dict] = None) -> dict:
        """
        Single request against `path` (relative, e.g. "/bill/119/s/5").
        Returns the parsed JSON body as-is (full envelope, including
        `pagination`/`request` keys if present) -- callers that need the
        unwrapped resource should use get_paginated for list endpoints,
        or index into the known top-level key for detail endpoints.
        """
        url = self._full_url(path)
        query = dict(params or {})
        query.setdefault("format", "json")
        query["api_key"] = self.api_key

        self._rate_limiter.wait()

        response = self._session.get(url, params=query, timeout=self.timeout)

        if response.status_code == 429:
            # Retry adapter should normally absorb this, but handle the
            # case where retries are exhausted.
            retry_after = int(response.headers.get("Retry-After", 60))
            logger.warning("Rate limited after retries exhausted; sleeping %ss", retry_after)
            time.sleep(retry_after)
            response = self._session.get(url, params=query, timeout=self.timeout)

        if not response.ok:
            raise CongressAPIError(response.status_code, response.text[:500], url)

        return response.json()

    def get_paginated(self, path: str, params: Optional[dict] = None) -> Iterator[dict]:
        """
        Yields individual items from a list endpoint, following
        `pagination.next` until exhausted. Unwraps the envelope
        automatically -- callers get bare item dicts (e.g. each bill),
        not the `{"bills": [...], "pagination": {...}}` wrapper.
        """
        query = dict(params or {})
        query.setdefault("limit", self.page_limit)
        query.setdefault("offset", 0)

        next_path: Optional[str] = path
        next_params: Optional[dict] = query

        while next_path is not None:
            payload = self.get_congress(next_path, params=next_params)

            items = self._unwrap_items(payload, context_path=next_path)
            for item in items:
                yield item

            next_url = payload.get("pagination", {}).get("next")
            if not next_url:
                break

            # `next` is typically a full URL with its own limit/offset (and
            # api_key already stripped by the API) -- extract path + params
            # rather than assuming offset arithmetic, since that's what the
            # API actually hands us and is more robust to it changing.
            next_path, next_params = self._parse_next_url(next_url)

    # ----------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------

    def _full_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _unwrap_items(payload: dict, context_path: str) -> list:
        """
        List endpoints wrap results under a resource-named key that varies
        by endpoint ("bills", "members", "actions", "cosponsors", ...).
        Rather than hardcode every key name, take the first list-valued
        entry that isn't `pagination` or `request`.
        """
        for key, value in payload.items():
            if key in ("pagination", "request"):
                continue
            if isinstance(value, list):
                return value

        logger.warning("No list payload found in response for %s", context_path)
        return []

    @staticmethod
    def _parse_next_url(next_url: str) -> tuple[str, dict]:
        parsed = urlparse(next_url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        # api_key gets re-added by get(); drop it here if present so we
        # don't accidentally carry a stale/mismatched one.
        params.pop("api_key", None)
        return parsed.path, params