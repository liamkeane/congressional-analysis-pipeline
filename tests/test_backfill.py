import json
from datetime import datetime, timezone

import pytest

from backfill import config as config_module
from backfill import run_backfill
from backfill.config import BackfillConfig
from backfill.run_backfill import Backfiller, build_run, parse_args
from backfill.state import BackfillState
from congress_client import CongressAPIError

BATCH_TS = datetime(2026, 10, 24, 15, 30, tzinfo=timezone.utc)


class FakeClient:
    """Serves canned responses by path; unknown paths return empty results."""

    def __init__(self, routes=None, failing=()):
        self.routes = routes or {}
        self.failing = set(failing)
        self.calls = []

    def get_paginated(self, path, params=None):
        self.calls.append(path)
        self._maybe_fail(path)
        return iter(self.routes.get(path, []))

    def get_congress(self, path, params=None):
        self.calls.append(path)
        self._maybe_fail(path)
        return self.routes.get(path, {"path": path})

    def _maybe_fail(self, path):
        if path in self.failing:
            raise CongressAPIError(500, "boom", path)


def make_state(tmp_path, cfg):
    return BackfillState.create(tmp_path / "state", "run-1", cfg.to_dict(), batch_ts=BATCH_TS)


def make_config(tmp_path, **overrides):
    defaults = dict(
        congresses=(118,),
        entities=("bills",),
        bill_subresources=("actions",),
        landing_root=tmp_path / "raw",
    )
    return BackfillConfig(**{**defaults, **overrides})


def read_landed(cfg, source, entity_id, congress=118):
    partition = cfg.landing_root / source / "dt=2026-10-24"
    if congress is not None:
        partition = partition / f"congress={congress}"
    return json.loads((partition / f"{entity_id}.json").read_text())


BILL_ROUTES = {
    "/bill/118": [{"congress": 118, "type": "HR", "number": "1"}, {"congress": 118, "type": "S", "number": "5"}],
    "/bill/118/hr/1/actions": [{"actionCode": "a"}, {"actionCode": "b"}],
    "/bill/118/s/5/actions": [{"actionCode": "c"}],
}


# --------------------------------------------------------------------------
# Bills
# --------------------------------------------------------------------------

def test_bills_land_detail_and_subresources(tmp_path):
    cfg = make_config(tmp_path)
    client = FakeClient(BILL_ROUTES)

    stats = Backfiller(client, cfg, make_state(tmp_path, cfg)).run()

    assert stats.landed == 4 and stats.failed == 0
    assert read_landed(cfg, "bill", "118-hr-1")["payload"] == {"path": "/bill/118/hr/1"}
    assert read_landed(cfg, "bill_actions", "118-hr-1")["payload"] == [{"actionCode": "a"}, {"actionCode": "b"}]
    assert read_landed(cfg, "bill_actions", "118-s-5")["payload"] == [{"actionCode": "c"}]


def test_bill_types_list_each_type_separately(tmp_path):
    cfg = make_config(tmp_path, bill_types=("hr", "s"), bill_subresources=())
    client = FakeClient({
        "/bill/118/hr": [{"type": "HR", "number": "1"}],
        "/bill/118/s": [{"type": "S", "number": "2"}],
    })

    Backfiller(client, cfg, make_state(tmp_path, cfg)).run()

    assert "/bill/118" not in client.calls
    assert {"/bill/118/hr/1", "/bill/118/s/2"} <= set(client.calls)


def test_listing_is_materialized_before_per_item_fetches(tmp_path):
    cfg = make_config(tmp_path, bill_subresources=())
    events = []

    class Recording(FakeClient):
        def get_paginated(self, path, params=None):
            def gen():
                for item in BILL_ROUTES[path]:
                    events.append(("list-item", item["number"]))
                    yield item
            return gen()

        def get_congress(self, path, params=None):
            events.append(("detail", path))
            return {}

    Backfiller(Recording(), cfg, make_state(tmp_path, cfg)).run()

    assert events == [
        ("list-item", "1"),
        ("list-item", "5"),
        ("detail", "/bill/118/hr/1"),
        ("detail", "/bill/118/s/5"),
    ]


def test_limit_caps_each_listing(tmp_path):
    cfg = make_config(tmp_path, limit=1, bill_subresources=())
    client = FakeClient(BILL_ROUTES)

    stats = Backfiller(client, cfg, make_state(tmp_path, cfg)).run()

    assert stats.landed == 1
    assert "/bill/118/s/5" not in client.calls


# --------------------------------------------------------------------------
# Failures and resume
# --------------------------------------------------------------------------

def test_failed_task_is_logged_and_does_not_stop_the_run(tmp_path):
    cfg = make_config(tmp_path)
    state = make_state(tmp_path, cfg)
    client = FakeClient(BILL_ROUTES, failing={"/bill/118/hr/1/actions"})

    stats = Backfiller(client, cfg, state).run()

    assert stats.failed == 1 and stats.landed == 3
    assert not state.is_done("bill_actions:118-hr-1")
    failures = [json.loads(line) for line in (state.run_dir / "failed.log").read_text().splitlines()]
    assert failures[0]["key"] == "bill_actions:118-hr-1"
    assert "boom" in failures[0]["error"]


def test_failed_listing_is_recorded_and_skips_entity(tmp_path):
    cfg = make_config(tmp_path)
    state = make_state(tmp_path, cfg)
    client = FakeClient(BILL_ROUTES, failing={"/bill/118"})

    stats = Backfiller(client, cfg, state).run()

    assert stats.failed == 1 and stats.landed == 0
    assert "list:bill:118:all" in (state.run_dir / "failed.log").read_text()


def test_resume_skips_done_tasks_and_retries_failures(tmp_path):
    cfg = make_config(tmp_path)
    state = make_state(tmp_path, cfg)
    Backfiller(FakeClient(BILL_ROUTES, failing={"/bill/118/hr/1/actions"}), cfg, state).run()

    resumed = BackfillState.load(tmp_path / "state", "run-1")
    client = FakeClient(BILL_ROUTES)
    stats = Backfiller(client, cfg, resumed).run()

    assert stats.skipped == 3 and stats.landed == 1 and stats.failed == 0
    detail_calls = [c for c in client.calls if c != "/bill/118"]
    assert detail_calls == ["/bill/118/hr/1/actions"]
    assert resumed.batch_ts == BATCH_TS


# --------------------------------------------------------------------------
# Other entities
# --------------------------------------------------------------------------

def test_members_are_unpartitioned_and_deduped_across_congresses(tmp_path):
    cfg = make_config(tmp_path, congresses=(118, 119), entities=("members",))
    client = FakeClient({
        "/member/congress/118": [{"bioguideId": "A000001"}],
        "/member/congress/119": [{"bioguideId": "A000001"}, {"bioguideId": "B000002"}],
    })

    stats = Backfiller(client, cfg, make_state(tmp_path, cfg)).run()

    assert stats.landed == 2 and stats.skipped == 1
    assert client.calls.count("/member/A000001") == 1
    assert read_landed(cfg, "member", "B000002", congress=None)["congress"] is None


def test_committees_house_votes_and_amendments(tmp_path):
    cfg = make_config(
        tmp_path,
        entities=("committees", "house_votes", "amendments"),
        amendment_subresources=("actions",),
    )
    client = FakeClient({
        "/committee/118": [{"chamber": "House", "systemCode": "hsag00"}],
        "/house-vote/118": [{"congress": 118, "sessionNumber": 1, "rollCallNumber": 7}],
        "/amendment/118": [{"congress": 118, "type": "SAMDT", "number": "3"}],
    })

    stats = Backfiller(client, cfg, make_state(tmp_path, cfg)).run()

    assert stats.failed == 0
    assert {
        "/committee/house/hsag00",
        "/house-vote/118/1/7",
        "/house-vote/118/1/7/members",
        "/amendment/118/samdt/3",
        "/amendment/118/samdt/3/actions",
    } <= set(client.calls)
    assert read_landed(cfg, "committee", "house-hsag00", congress=None)
    assert read_landed(cfg, "house_vote_members", "118-1-7") is not None


# --------------------------------------------------------------------------
# Config / CLI
# --------------------------------------------------------------------------

def test_subresource_names_match_config():
    assert set(run_backfill.BILL_SUBRESOURCES) == set(config_module.BILL_SUBRESOURCES)
    assert set(run_backfill.AMENDMENT_SUBRESOURCES) == set(config_module.AMENDMENT_SUBRESOURCES)


@pytest.mark.parametrize("overrides", [
    {"entities": ("bills", "nope")},
    {"bill_subresources": ("actions", "votes")},
    {"bill_types": ("HR",)},
    {"congresses": ()},
    {"limit": 0},
])
def test_config_rejects_invalid_scope(tmp_path, overrides):
    with pytest.raises(ValueError):
        make_config(tmp_path, **overrides)


def test_config_round_trips_through_dict(tmp_path):
    cfg = make_config(tmp_path, bill_types=("hr",), limit=5)
    assert BackfillConfig.from_dict(json.loads(json.dumps(cfg.to_dict()))) == cfg


def test_build_run_creates_then_resumes_with_original_scope(tmp_path):
    state_root = tmp_path / "state"
    raw = tmp_path / "raw"
    args = parse_args([
        "--congress", "119", "--entities", "bills", "--limit", "3",
        "--landing-root", str(raw), "--state-root", str(state_root), "--run-id", "r1",
    ])
    cfg, state = build_run(args)
    assert cfg.congresses == (119,) and cfg.limit == 3 and state.run_id == "r1"

    resumed_cfg, resumed_state = build_run(
        parse_args(["--resume", "--congress", "118", "--state-root", str(state_root)])
    )
    assert resumed_state.run_id == "r1"
    assert resumed_cfg == cfg
    assert resumed_state.batch_ts == state.batch_ts


def test_resume_without_runs_exits(tmp_path):
    with pytest.raises(SystemExit):
        build_run(parse_args(["--resume", "--state-root", str(tmp_path / "none")]))
