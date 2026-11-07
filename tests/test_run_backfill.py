import json
from datetime import datetime, timezone

import pytest

from ingestion.backfill import run_backfill as backfill
from ingestion.backfill.run_backfill import (
    already_landed,
    land_bill,
    land_bill_list,
    land_bills,
    load_or_create_state,
    run_backfill,
    state_path,
)
from ingestion.congress_client import CongressAPIError
from ingestion.landing.writer import landing_path

BATCH_TS = datetime(2026, 10, 25, 15, 30, tzinfo=timezone.utc)


class FakeClient:
    """
    Stands in for CongressClient: returns canned responses and records every path requested.
    Paths in `fail_paths` raise CongressAPIError instead, like a 500 that outlasted retries.
    """

    def __init__(self, detail=None, actions=(), bill_list=(), fail_paths=()):
        self.detail = detail if detail is not None else FAKE_DETAIL
        self.actions = list(actions)
        self.bill_list = list(bill_list)
        self.fail_paths = set(fail_paths)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        pass

    def _record(self, path):
        self.calls.append(path)
        if path in self.fail_paths:
            raise CongressAPIError(500, "Internal Server Error", path)

    def get_congress(self, path, params=None):
        self._record(path)
        return self.detail

    def get_paginated(self, path, params=None):
        self._record(path)
        if path.endswith("/actions"):
            return iter(self.actions)
        if path.count("/") == 2:  # "/bill/119" is the bill list
            return iter(self.bill_list)
        return iter([])


FAKE_DETAIL = {"bill": {"congress": 119, "type": "HR", "number": "134"}}
FAKE_ACTIONS = [
    {"actionDate": "2025-01-03", "type": "IntroReferral", "text": "Referred to the House Committee on the Judiciary."},
    {"actionDate": "2025-01-03", "type": "IntroReferral", "text": "Introduced in House"},
]
FAKE_BILL_LIST = [
    {"congress": 119, "type": "HR", "number": "1"},
    {"congress": 119, "type": "HR", "number": "2"},
    {"congress": 119, "type": "S", "number": "5"},
]


def read_payload(path):
    return json.loads(path.read_text())["payload"]


def detail_requests(client):
    """Just the bill detail requests, e.g. "/bill/119/hr/1" -- one per bill land_bill was called for."""
    return [p for p in client.calls if p.count("/") == 4]


# ---------------------------------------------------------------------------
# land_bill
# ---------------------------------------------------------------------------

def test_lands_each_source_as_a_separate_file(tmp_path):
    client = FakeClient(FAKE_DETAIL, FAKE_ACTIONS)

    paths = land_bill(client, 119, "hr", 134, BATCH_TS, root=tmp_path)

    assert paths == [
        tmp_path / source / "dt=2026-10-25" / "congress=119" / "119-hr-134.json"
        for source in ("bill", "bill_actions", "bill_cosponsors", "bill_subjects")
    ]
    assert read_payload(paths[0]) == FAKE_DETAIL
    assert read_payload(paths[1]) == FAKE_ACTIONS


def test_requests_the_right_endpoints(tmp_path):
    client = FakeClient(FAKE_DETAIL, FAKE_ACTIONS)

    land_bill(client, 119, "hr", 134, BATCH_TS, root=tmp_path)

    assert client.calls == [
        "/bill/119/hr/134",
        "/bill/119/hr/134/actions",
        "/bill/119/hr/134/cosponsors",
        "/bill/119/hr/134/subjects",
    ]


def test_uppercase_bill_type_is_normalized(tmp_path):
    client = FakeClient(FAKE_DETAIL, FAKE_ACTIONS)

    paths = land_bill(client, 119, "HR", 134, BATCH_TS, root=tmp_path)

    assert client.calls[0] == "/bill/119/hr/134"
    assert paths[0].name == "119-hr-134.json"


def test_bill_with_no_actions_lands_empty_list(tmp_path):
    client = FakeClient(FAKE_DETAIL, [])

    paths = land_bill(client, 119, "hr", 134, BATCH_TS, root=tmp_path)

    assert read_payload(paths[1]) == []


# ---------------------------------------------------------------------------
# Phase 1: land_bill_list
# ---------------------------------------------------------------------------

def test_bill_list_lands_one_file_per_bill(tmp_path):
    client = FakeClient(bill_list=FAKE_BILL_LIST)

    count = land_bill_list(client, 119, BATCH_TS, root=tmp_path)

    assert count == 3
    list_dir = tmp_path / "bill_list" / "dt=2026-10-25" / "congress=119"
    assert sorted(p.name for p in list_dir.iterdir()) == ["119-hr-1.json", "119-hr-2.json", "119-s-5.json"]
    assert read_payload(list_dir / "119-s-5.json") == FAKE_BILL_LIST[2]


# ---------------------------------------------------------------------------
# Phase 2: land_bills
# ---------------------------------------------------------------------------

def test_lands_every_listed_bill(tmp_path):
    land_bill_list(FakeClient(bill_list=FAKE_BILL_LIST), 119, BATCH_TS, root=tmp_path)
    client = FakeClient()

    failed = land_bills(client, 119, BATCH_TS, root=tmp_path)

    assert failed == []
    assert detail_requests(client) == ["/bill/119/hr/1", "/bill/119/hr/2", "/bill/119/s/5"]
    for entity_id in ("119-hr-1", "119-hr-2", "119-s-5"):
        assert already_landed(119, entity_id, BATCH_TS, root=tmp_path)


def test_second_run_skips_bills_already_landed(tmp_path):
    land_bill_list(FakeClient(bill_list=FAKE_BILL_LIST), 119, BATCH_TS, root=tmp_path)
    land_bills(FakeClient(), 119, BATCH_TS, root=tmp_path)
    client = FakeClient()

    failed = land_bills(client, 119, BATCH_TS, root=tmp_path)

    assert failed == []
    assert client.calls == []


def test_partially_landed_bill_is_fetched_again(tmp_path):
    land_bill_list(FakeClient(bill_list=FAKE_BILL_LIST), 119, BATCH_TS, root=tmp_path)
    land_bills(FakeClient(), 119, BATCH_TS, root=tmp_path)
    landing_path("bill_subjects", 119, "119-hr-2", BATCH_TS, tmp_path).unlink()
    client = FakeClient()

    land_bills(client, 119, BATCH_TS, root=tmp_path)

    assert detail_requests(client) == ["/bill/119/hr/2"]


def test_failed_bill_is_reported_and_others_still_land(tmp_path):
    land_bill_list(FakeClient(bill_list=FAKE_BILL_LIST), 119, BATCH_TS, root=tmp_path)
    client = FakeClient(fail_paths={"/bill/119/hr/2/actions"})

    failed = land_bills(client, 119, BATCH_TS, root=tmp_path)

    assert failed == ["119-hr-2"]
    assert already_landed(119, "119-hr-1", BATCH_TS, root=tmp_path)
    assert not already_landed(119, "119-hr-2", BATCH_TS, root=tmp_path)
    assert already_landed(119, "119-s-5", BATCH_TS, root=tmp_path)


def test_rerun_retries_only_the_failed_bill(tmp_path):
    land_bill_list(FakeClient(bill_list=FAKE_BILL_LIST), 119, BATCH_TS, root=tmp_path)
    land_bills(FakeClient(fail_paths={"/bill/119/hr/2"}), 119, BATCH_TS, root=tmp_path)
    client = FakeClient()

    failed = land_bills(client, 119, BATCH_TS, root=tmp_path)

    assert failed == []
    assert detail_requests(client) == ["/bill/119/hr/2"]


def test_limit_only_processes_the_first_n_bills(tmp_path):
    land_bill_list(FakeClient(bill_list=FAKE_BILL_LIST), 119, BATCH_TS, root=tmp_path)
    client = FakeClient()

    land_bills(client, 119, BATCH_TS, root=tmp_path, limit=2)

    assert detail_requests(client) == ["/bill/119/hr/1", "/bill/119/hr/2"]


def test_bug_in_our_code_is_not_swallowed(tmp_path):
    list_dir = tmp_path / "bill_list" / "dt=2026-10-25" / "congress=119"
    list_dir.mkdir(parents=True)
    (list_dir / "119-hr-1.json").write_text(json.dumps({"payload": {"number": "1"}}))  # no "type"

    with pytest.raises(KeyError):
        land_bills(FakeClient(), 119, BATCH_TS, root=tmp_path)


# ---------------------------------------------------------------------------
# State and run_backfill
# ---------------------------------------------------------------------------

def test_new_state_is_saved_and_resumed(tmp_path):
    first = load_or_create_state(119, state_dir=tmp_path)
    second = load_or_create_state(119, state_dir=tmp_path)

    assert second == first
    assert first["list_complete"] is False
    assert state_path(119, tmp_path).exists()


def test_fresh_replaces_saved_state(tmp_path):
    state_path(119, tmp_path).write_text(json.dumps(
        {"congress": 119, "batch_ts": BATCH_TS.isoformat(), "list_complete": True}
    ))

    state = load_or_create_state(119, state_dir=tmp_path, fresh=True)

    assert state["batch_ts"] != BATCH_TS.isoformat()
    assert state["list_complete"] is False


@pytest.fixture
def fake_client(monkeypatch):
    """Makes run_backfill use a FakeClient, returned so the test can inspect its calls."""
    client = FakeClient(bill_list=FAKE_BILL_LIST)
    monkeypatch.setattr(backfill, "CongressClient", lambda: client)
    return client


def test_run_backfill_lands_list_then_bills(tmp_path, fake_client):
    root, state_dir = tmp_path / "raw", tmp_path / "state"

    failed = run_backfill(119, root=root, state_dir=state_dir)

    assert failed == []
    state = json.loads(state_path(119, state_dir).read_text())
    assert state["list_complete"] is True
    batch_ts = datetime.fromisoformat(state["batch_ts"])
    assert already_landed(119, "119-s-5", batch_ts, root=root)


def test_resumed_run_reuses_saved_batch_ts_and_skips_listing(tmp_path, fake_client):
    root, state_dir = tmp_path / "raw", tmp_path / "state"
    # A run that started on an earlier day and finished listing before it stopped.
    state_dir.mkdir()
    state_path(119, state_dir).write_text(json.dumps(
        {"congress": 119, "batch_ts": BATCH_TS.isoformat(), "list_complete": True}
    ))
    land_bill_list(FakeClient(bill_list=FAKE_BILL_LIST[:2]), 119, BATCH_TS, root=root)

    run_backfill(119, root=root, state_dir=state_dir)

    assert "/bill/119" not in fake_client.calls
    assert detail_requests(fake_client) == ["/bill/119/hr/1", "/bill/119/hr/2"]
    assert already_landed(119, "119-hr-1", BATCH_TS, root=root)  # landed in the original dt= folder
