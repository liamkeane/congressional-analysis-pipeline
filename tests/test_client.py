# mock API responses, test pagination/retry logic

import logging
from email.utils import formatdate
from urllib.parse import parse_qs, urlparse

import pytest

from congress_client import client as client_module
from congress_client.client import CongressAPIError, CongressClient, _RateLimiter


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=None, headers=None):
        self.status_code = status_code
        self.ok = status_code < 400
        self._body = body
        self.text = text if text is not None else str(body)
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("No JSON object could be decoded")
        return self._body


class FakeSession:
    """Stands in for requests.Session; returns queued responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        return self._responses.pop(0)

    def close(self):
        pass


@pytest.fixture
def make_client(monkeypatch):
    # No real sleeping: rate limiter and 429 fallback both go through time.sleep.
    sleeps = []
    monkeypatch.setattr(client_module.time, "sleep", sleeps.append)

    def _make(responses):
        c = CongressClient(api_key="test-key")
        c._session = FakeSession(responses)
        c.sleeps = sleeps
        return c

    return _make


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("CONGRESS_API_KEY", raising=False)
    with pytest.raises(ValueError, match="No API key"):
        CongressClient()


def test_api_key_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("CONGRESS_API_KEY", "env-key")
    assert CongressClient().api_key == "env-key"


# ---------------------------------------------------------------------------
# get_congress
# ---------------------------------------------------------------------------

def test_get_congress_builds_url_and_adds_auth(make_client):
    c = make_client([FakeResponse(body={"bill": {"number": "5"}})])

    result = c.get_congress("/bill/119/s/5", params={"foo": "bar"})

    assert result == {"bill": {"number": "5"}}
    call = c._session.calls[0]
    assert call["url"] == "https://api.congress.gov/v3/bill/119/s/5"
    assert call["params"] == {"foo": "bar", "format": "json", "api_key": "test-key"}


def test_get_congress_passes_full_urls_through(make_client):
    c = make_client([FakeResponse(body={})])
    c.get_congress("https://api.congress.gov/v3/bill/119")
    assert c._session.calls[0]["url"] == "https://api.congress.gov/v3/bill/119"


def test_get_congress_does_not_mutate_caller_params(make_client):
    c = make_client([FakeResponse(body={})])
    params = {"foo": "bar"}
    c.get_congress("/bill", params=params)
    assert params == {"foo": "bar"}


def test_error_response_raises_without_leaking_key(make_client):
    c = make_client([FakeResponse(status_code=404, text="Not found")])

    with pytest.raises(CongressAPIError) as exc_info:
        c.get_congress("/bill/119/s/99999")

    err = exc_info.value
    assert err.status_code == 404
    assert err.url == "https://api.congress.gov/v3/bill/119/s/99999"
    assert "test-key" not in str(err)


def test_error_message_is_truncated(make_client):
    c = make_client([FakeResponse(status_code=500, text="x" * 2000)])
    with pytest.raises(CongressAPIError) as exc_info:
        c.get_congress("/bill")
    assert "x" * 500 in str(exc_info.value)
    assert "x" * 501 not in str(exc_info.value)


def test_invalid_json_raises_api_error(make_client):
    c = make_client([FakeResponse(status_code=200, body=None, text="<html>")])
    with pytest.raises(CongressAPIError, match="not valid JSON"):
        c.get_congress("/bill")


def test_429_after_retries_sleeps_then_retries_once(make_client):
    c = make_client([
        FakeResponse(status_code=429, text="slow down", headers={"Retry-After": "7"}),
        FakeResponse(body={"ok": True}),
    ])

    assert c.get_congress("/bill") == {"ok": True}
    assert len(c._session.calls) == 2
    assert 7.0 in c.sleeps


def test_second_429_raises(make_client):
    c = make_client([
        FakeResponse(status_code=429, text="slow down", headers={"Retry-After": "1"}),
        FakeResponse(status_code=429, text="still slow"),
    ])
    with pytest.raises(CongressAPIError) as exc_info:
        c.get_congress("/bill")
    assert exc_info.value.status_code == 429


# ---------------------------------------------------------------------------
# _retry_after_seconds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "headers, expected",
    [
        ({"Retry-After": "30"}, 30.0),
        ({"Retry-After": "-5"}, 0.0),
        ({}, client_module.DEFAULT_RETRY_AFTER_SECONDS),
        ({"Retry-After": "not a date"}, client_module.DEFAULT_RETRY_AFTER_SECONDS),
    ],
)
def test_retry_after_seconds(headers, expected):
    resp = FakeResponse(headers=headers)
    assert CongressClient._retry_after_seconds(resp) == expected


def test_retry_after_http_date():
    future = formatdate(timeval=client_module.time.time() + 120, usegmt=True)
    resp = FakeResponse(headers={"Retry-After": future})
    assert 115 <= CongressClient._retry_after_seconds(resp) <= 120


def test_retry_after_http_date_in_past_is_zero():
    past = formatdate(timeval=client_module.time.time() - 120, usegmt=True)
    resp = FakeResponse(headers={"Retry-After": past})
    assert CongressClient._retry_after_seconds(resp) == 0.0


# ---------------------------------------------------------------------------
# get_paginated
# ---------------------------------------------------------------------------

NEXT_URL = (
    "https://api.congress.gov/v3/bill/119?offset=250&limit=250"
    "&format=json&api_key=stale-key"
)


def test_paginates_until_next_is_absent(make_client):
    c = make_client([
        FakeResponse(body={"bills": [{"n": 1}, {"n": 2}], "pagination": {"next": NEXT_URL}}),
        FakeResponse(body={"bills": [{"n": 3}], "pagination": {}}),
    ])

    items = list(c.get_paginated("/bill/119", params={"fromDateTime": "2025-01-01T00:00:00Z"}))

    assert items == [{"n": 1}, {"n": 2}, {"n": 3}]

    first, second = c._session.calls
    assert first["url"] == "https://api.congress.gov/v3/bill/119"
    assert first["params"]["limit"] == 250
    assert first["params"]["offset"] == 0

    # `next` is absolute and already carries /v3 -- must not be doubled.
    assert second["url"] == "https://api.congress.gov/v3/bill/119"
    # next's own values win over the original query...
    assert second["params"]["offset"] == "250"
    # ...but filters `next` dropped are carried forward.
    assert second["params"]["fromDateTime"] == "2025-01-01T00:00:00Z"
    # The stale key from `next` is replaced with ours.
    assert second["params"]["api_key"] == "test-key"


def test_stops_on_empty_page_even_if_next_present(make_client):
    c = make_client([
        FakeResponse(body={"bills": [], "pagination": {"next": NEXT_URL}}),
    ])
    assert list(c.get_paginated("/bill/119")) == []
    assert len(c._session.calls) == 1


def test_get_paginated_is_lazy(make_client):
    c = make_client([FakeResponse(body={"bills": [{"n": 1}]})])
    gen = c.get_paginated("/bill")
    assert c._session.calls == []
    next(gen)
    assert len(c._session.calls) == 1


def test_caller_page_limit_is_respected(make_client):
    c = make_client([FakeResponse(body={"bills": []})])
    list(c.get_paginated("/bill", params={"limit": 20}))
    assert c._session.calls[0]["params"]["limit"] == 20


# ---------------------------------------------------------------------------
# _unwrap_items
# ---------------------------------------------------------------------------

def test_unwrap_top_level_list_skips_envelope_keys():
    payload = {
        "pagination": {"count": 1, "next": None},
        "request": {"format": "json"},
        "bills": [{"number": "5"}],
    }
    assert CongressClient._unwrap_items(payload, "/bill") == [{"number": "5"}]


def test_unwrap_ignores_list_valued_envelope_keys():
    payload = {"pagination": [{"bogus": True}], "members": [{"bioguideId": "A1"}]}
    assert CongressClient._unwrap_items(payload, "/member") == [{"bioguideId": "A1"}]


def test_unwrap_nested_bill_subjects():
    payload = {
        "subjects": {
            "legislativeSubjects": [{"name": "Taxation"}],
            "policyArea": {"name": "Economics"},
        },
        "pagination": {"count": 1},
        "request": {},
    }
    assert CongressClient._unwrap_items(payload, "/subjects") == [{"name": "Taxation"}]


def test_unwrap_nested_house_vote_members():
    payload = {
        "houseRollCallVoteMemberVotes": {
            "congress": 119,
            "result": "Passed",
            "results": [{"bioguideID": "A1", "voteCast": "Yea"}],
        },
        "pagination": {"count": 1},
        "request": {},
    }
    assert CongressClient._unwrap_items(payload, "/members") == [
        {"bioguideID": "A1", "voteCast": "Yea"}
    ]


def test_unwrap_prefers_top_level_list_over_nested():
    payload = {"meta": {"inner": [{"wrong": True}]}, "bills": [{"right": True}]}
    assert CongressClient._unwrap_items(payload, "/bill") == [{"right": True}]


def test_unwrap_no_list_returns_empty_and_warns(caplog):
    payload = {"pagination": {}, "request": {}, "bill": {"number": "5"}}
    with caplog.at_level(logging.WARNING, logger=client_module.__name__):
        assert CongressClient._unwrap_items(payload, "/bill/119/s/5") == []
    assert "/bill/119/s/5" in caplog.text


# ---------------------------------------------------------------------------
# _parse_next_url
# ---------------------------------------------------------------------------

def test_parse_next_url_strips_api_key_and_keeps_absolute_path():
    url, params = CongressClient._parse_next_url(NEXT_URL)
    assert url == "https://api.congress.gov/v3/bill/119"
    assert params == {"offset": "250", "limit": "250", "format": "json"}


# ---------------------------------------------------------------------------
# _RateLimiter
# ---------------------------------------------------------------------------

def test_rate_limiter_spaces_calls(monkeypatch):
    clock = [1000.0]
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "sleep", fake_sleep)

    limiter = _RateLimiter(max_per_hour=100, safety_margin=1.0)  # 36s interval

    limiter.wait()  # first call never sleeps
    assert sleeps == []

    clock[0] += 10
    limiter.wait()
    assert sleeps == [pytest.approx(26.0)]

    clock[0] += 100  # already past the interval
    limiter.wait()
    assert len(sleeps) == 1


def test_rate_limiter_applies_safety_margin():
    limiter = _RateLimiter(max_per_hour=5000, safety_margin=0.9)
    assert limiter._min_interval == pytest.approx(3600 / 4500)
