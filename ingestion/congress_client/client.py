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
from email.utils import parsedate_to_datetime
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

# Fallback sleep when a 429 arrives without a usable Retry-After header.
DEFAULT_RETRY_AFTER_SECONDS = 60

# Envelope keys that are metadata, never the resource payload.
_ENVELOPE_KEYS = ("pagination", "request")


class CongressAPIError(Exception):
    """Raised for non-retryable HTTP errors from the API."""

    def __init__(self, status_code: int, message: str, url: str):
        self.status_code = status_code
        self.url = url
        super().__init__(f"[{status_code}] {message} ({url})")


class _RateLimiter:
    """
    Enforces a minimum interval between requests so that over an hour we stay under the API's cap.
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
    # Lifecycle
    # ----------------------------------------------------------------

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "CongressClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

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
        session.headers.update({"Accept": "application/json"})
        return session

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def get_congress(self, path: str, params: Optional[dict] = None) -> dict:
        """
        Single request against `path` (relative, e.g. "/bill/119/s/5", or a
        full URL as handed back in `pagination.next`).
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
            retry_after = self._retry_after_seconds(response)
            logger.warning("Rate limited after retries exhausted; sleeping %ss", retry_after)
            time.sleep(retry_after)
            self._rate_limiter.wait()
            response = self._session.get(url, params=query, timeout=self.timeout)

        if not response.ok:
            raise CongressAPIError(response.status_code, response.text[:500], url)

        try:
            return response.json()
        except ValueError as exc:
            raise CongressAPIError(
                response.status_code, f"Response was not valid JSON: {exc}", url
            ) from exc

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
            if not next_url or not items:
                break

            # `next` is typically a full URL with its own limit/offset (and
            # api_key already stripped by the API) -- extract path + params
            # rather than assuming offset arithmetic, since that's what the
            # API actually hands us and is more robust to it changing.
            # Original filters are layered underneath in case `next` drops
            # any of them (e.g. fromDateTime); `next`'s own values win.
            next_path, parsed_params = self._parse_next_url(next_url)
            next_params = {**query, **parsed_params}

    # ----------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------

    def _full_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _retry_after_seconds(response: requests.Response) -> float:
        """Retry-After may be delta-seconds or an HTTP date; handle both."""
        header = response.headers.get("Retry-After")
        if not header:
            return DEFAULT_RETRY_AFTER_SECONDS
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
        try:
            retry_at = parsedate_to_datetime(header)
            return max(0.0, retry_at.timestamp() - time.time())
        except (TypeError, ValueError):
            return DEFAULT_RETRY_AFTER_SECONDS

    @staticmethod
    def _unwrap_items(payload: dict, context_path: str) -> list:
        """
        List endpoints wrap results under a resource-named key that varies
        by endpoint ("bills", "members", "actions", "cosponsors", ...).
        Rather than hardcode every key name, take the first list-valued
        entry that isn't `pagination` or `request`.

        A few endpoints nest the list one level deeper inside an object
        (e.g. bill subjects -> {"subjects": {"legislativeSubjects": [...],
        "policyArea": {...}}}, house vote members ->
        {"houseRollCallVoteMemberVotes": {..., "results": [...]}}). If no
        top-level list exists, fall back to the first list found one level
        down. Sibling scalar/object fields at that level are dropped; they
        are available from the corresponding detail endpoint.
        """
        for key, value in payload.items():
            if key in _ENVELOPE_KEYS:
                continue
            if isinstance(value, list):
                return value

        for key, value in payload.items():
            if key in _ENVELOPE_KEYS or not isinstance(value, dict):
                continue
            for inner_value in value.values():
                if isinstance(inner_value, list):
                    return inner_value

        logger.warning("No list payload found in response for %s", context_path)
        return []

    @staticmethod
    def _parse_next_url(next_url: str) -> tuple[str, dict]:
        """
        Split `pagination.next` into (url, params). The URL is returned
        absolute -- its path already includes the `/v3` prefix, so joining
        a bare path onto base_url would double it up.
        """
        parsed = urlparse(next_url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        # api_key gets re-added by get_congress(); drop it here if present
        # so we don't accidentally carry a stale/mismatched one.
        params.pop("api_key", None)
        url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return url, params
