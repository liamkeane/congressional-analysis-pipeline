import json
from datetime import datetime, timezone

from ingestion.backfill.run_backfill import land_bill

BATCH_TS = datetime(2026, 10, 25, 15, 30, tzinfo=timezone.utc)


class FakeClient:
    """Stands in for CongressClient: returns canned responses and records every path requested."""

    def __init__(self, detail, actions):
        self.detail = detail
        self.actions = actions
        self.calls = []

    def get_congress(self, path, params=None):
        self.calls.append(path)
        return self.detail

    def get_paginated(self, path, params=None):
        self.calls.append(path)
        return iter(self.actions)


FAKE_DETAIL = {"bill": {"congress": 119, "type": "HR", "number": "134"}}
FAKE_ACTIONS = [
    {"actionDate": "2025-01-03", "type": "IntroReferral", "text": "Referred to the House Committee on the Judiciary."},
    {"actionDate": "2025-01-03", "type": "IntroReferral", "text": "Introduced in House"},
]


def read_payload(path):
    return json.loads(path.read_text())["payload"]


def test_lands_detail_and_actions_as_separate_files(tmp_path):
    client = FakeClient(FAKE_DETAIL, FAKE_ACTIONS)

    detail_path, actions_path = land_bill(client, 119, "hr", 134, BATCH_TS, root=tmp_path)

    assert detail_path == tmp_path / "bill" / "dt=2026-10-25" / "congress=119" / "119-hr-134.json"
    assert actions_path == tmp_path / "bill_actions" / "dt=2026-10-25" / "congress=119" / "119-hr-134.json"
    assert read_payload(detail_path) == FAKE_DETAIL
    assert read_payload(actions_path) == FAKE_ACTIONS


def test_requests_the_right_endpoints(tmp_path):
    client = FakeClient(FAKE_DETAIL, FAKE_ACTIONS)

    land_bill(client, 119, "hr", 134, BATCH_TS, root=tmp_path)

    assert client.calls == ["/bill/119/hr/134", "/bill/119/hr/134/actions"]


def test_uppercase_bill_type_is_normalized(tmp_path):
    client = FakeClient(FAKE_DETAIL, FAKE_ACTIONS)

    detail_path, _ = land_bill(client, 119, "HR", 134, BATCH_TS, root=tmp_path)

    assert client.calls[0] == "/bill/119/hr/134"
    assert detail_path.name == "119-hr-134.json"


def test_bill_with_no_actions_lands_empty_list(tmp_path):
    client = FakeClient(FAKE_DETAIL, [])

    _, actions_path = land_bill(client, 119, "hr", 134, BATCH_TS, root=tmp_path)

    assert read_payload(actions_path) == []
