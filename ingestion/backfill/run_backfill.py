"""
backfill/run_backfill.py

One-off historical load of the congresses in scope into the raw landing
zone. The daily DAG takes over from there, watermarking on updateDate.

For each congress, for each entity type in scope:
  1. enumerate identifiers from the list endpoint (fully, up front)
  2. for each identifier, fetch detail + configured sub-resources and land
     each response as its own raw file

Runs are resumable: every landed (source, entity) is checkpointed, and a
re-run with --resume (or --run-id) skips what's already done and lands into
the same dt= partition. Failures go to the run's failed.log and are left
un-checkpointed, so a resume retries them.

Usage:
    python ingestion/backfill/run_backfill.py
    python ingestion/backfill/run_backfill.py --congress 119 --entities bills --limit 10
    python ingestion/backfill/run_backfill.py --resume
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Callable, Optional

# Allow running as a script without installing anything: put the repo root
# (for `landing`) and ingestion/ (for `congress_client`, `backfill`) on the path.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT, _REPO_ROOT / "ingestion"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import requests  # noqa: E402

from backfill.config import ENTITIES, BackfillConfig  # noqa: E402
from backfill.state import DEFAULT_STATE_ROOT, BackfillState, new_run_id  # noqa: E402
from congress_client import CongressAPIError, CongressClient, endpoints  # noqa: E402
from landing.writer import land_raw  # noqa: E402

logger = logging.getLogger(__name__)

# Keys must match config.BILL_SUBRESOURCES / AMENDMENT_SUBRESOURCES.
BILL_SUBRESOURCES: dict[str, Callable] = {
    "actions": endpoints.get_bill_actions,
    "amendments": endpoints.get_bill_amendments,
    "committees": endpoints.get_bill_committees,
    "cosponsors": endpoints.get_bill_cosponsors,
    "related_bills": endpoints.get_bill_related_bills,
    "subjects": endpoints.get_bill_subjects,
    "summaries": endpoints.get_bill_summaries,
    "text": endpoints.get_bill_text_versions,
    "titles": endpoints.get_bill_titles,
}

AMENDMENT_SUBRESOURCES: dict[str, Callable] = {
    "actions": endpoints.get_amendment_actions,
    "amendments": endpoints.get_amendment_amendments,
    "cosponsors": endpoints.get_amendment_cosponsors,
    "text": endpoints.get_amendment_text_versions,
}

# Errors that fail one task rather than the whole run. Anything else
# (bugs, disk full, ...) should stop the run loudly.
_TASK_ERRORS = (CongressAPIError, requests.RequestException)

_PROGRESS_EVERY = 100


@dataclass
class BackfillStats:
    landed: int = 0
    skipped: int = 0
    failed: int = 0


class Backfiller:
    def __init__(self, client: CongressClient, config: BackfillConfig, state: BackfillState):
        self.client = client
        self.config = config
        self.state = state
        self.stats = BackfillStats()

    def run(self) -> BackfillStats:
        handlers = {
            "bills": self._backfill_bills,
            "amendments": self._backfill_amendments,
            "members": self._backfill_members,
            "committees": self._backfill_committees,
            "house_votes": self._backfill_house_votes,
        }
        for congress in self.config.congresses:
            for entity in self.config.entities:
                logger.info("Congress %s: %s", congress, entity)
                handlers[entity](congress)
        return self.stats

    # ----------------------------------------------------------------
    # Entities
    # ----------------------------------------------------------------

    def _backfill_bills(self, congress: int) -> None:
        bills = []
        for bill_type in self.config.bill_types or (None,):
            bills += self._enumerate(
                f"list:bill:{congress}:{bill_type or 'all'}",
                endpoints.get_bills, self.client, congress, bill_type,
            )

        for i, bill in enumerate(bills, 1):
            bill_type, number = bill["type"].lower(), int(bill["number"])
            entity_id = f"{congress}-{bill_type}-{number}"
            args = (self.client, congress, bill_type, number)

            self._land("bill", congress, entity_id, endpoints.get_bill_detail, *args)
            for name in self.config.bill_subresources:
                self._land(f"bill_{name}", congress, entity_id, BILL_SUBRESOURCES[name], *args)
            self._progress("bills", congress, i, len(bills))

    def _backfill_amendments(self, congress: int) -> None:
        amendments = self._enumerate(
            f"list:amendment:{congress}", endpoints.get_amendments, self.client, congress
        )

        for i, amendment in enumerate(amendments, 1):
            amendment_type, number = amendment["type"].lower(), int(amendment["number"])
            entity_id = f"{congress}-{amendment_type}-{number}"
            args = (self.client, congress, amendment_type, number)

            self._land("amendment", congress, entity_id, endpoints.get_amendment_detail, *args)
            for name in self.config.amendment_subresources:
                self._land(
                    f"amendment_{name}", congress, entity_id, AMENDMENT_SUBRESOURCES[name], *args
                )
            self._progress("amendments", congress, i, len(amendments))

    def _backfill_members(self, congress: int) -> None:
        # current_member=False, or the API returns only members still serving.
        members = self._enumerate(
            f"list:member:{congress}",
            endpoints.get_members_by_congress, self.client, congress, current_member=False,
        )

        # Members span congresses, so they land without a congress partition
        # and are checkpointed once -- later congresses skip anyone already landed.
        for i, member in enumerate(members, 1):
            bioguide_id = member["bioguideId"]
            self._land("member", None, bioguide_id, endpoints.get_member_detail, self.client, bioguide_id)
            self._progress("members", congress, i, len(members))

    def _backfill_committees(self, congress: int) -> None:
        committees = self._enumerate(
            f"list:committee:{congress}", endpoints.get_committees, self.client, congress=congress
        )

        # Like members, committees aren't congress-scoped.
        for i, committee in enumerate(committees, 1):
            chamber, code = committee["chamber"].lower(), committee["systemCode"]
            self._land(
                "committee", None, f"{chamber}-{code}",
                endpoints.get_committee_detail, self.client, chamber, code,
            )
            self._progress("committees", congress, i, len(committees))

    def _backfill_house_votes(self, congress: int) -> None:
        votes = self._enumerate(
            f"list:house_vote:{congress}", endpoints.get_house_votes, self.client, congress
        )

        for i, vote in enumerate(votes, 1):
            session, roll_call = int(vote["sessionNumber"]), int(vote["rollCallNumber"])
            entity_id = f"{congress}-{session}-{roll_call}"
            args = (self.client, congress, session, roll_call)

            self._land("house_vote", congress, entity_id, endpoints.get_house_vote_detail, *args)
            if self.config.include_vote_members:
                self._land(
                    "house_vote_members", congress, entity_id,
                    endpoints.get_house_vote_members, *args,
                )
            self._progress("house votes", congress, i, len(votes))

    # ----------------------------------------------------------------
    # Internals
    # ----------------------------------------------------------------

    def _enumerate(self, key: str, list_fn: Callable, *args, **kwargs) -> list[dict]:
        """
        Materialize a list endpoint before fetching anything per item. List
        endpoints sort by updateDate, so anything updated mid-run jumps to
        the front and shifts every offset behind it -- paging lazily while
        spending hours on detail calls would silently skip records.
        """
        try:
            items = list(islice(list_fn(*args, **kwargs), self.config.limit))
        except _TASK_ERRORS as exc:
            self._fail(key, exc)
            return []
        logger.info("%s: %d items", key, len(items))
        return items

    def _land(
        self, source: str, congress: Optional[int], entity_id: str, fetch: Callable, *args
    ) -> None:
        key = f"{source}:{entity_id}"
        if self.state.is_done(key):
            self.stats.skipped += 1
            return

        try:
            payload = fetch(*args)
            if not isinstance(payload, dict):
                payload = list(payload)  # drain paginated sub-resources
        except _TASK_ERRORS as exc:
            self._fail(key, exc)
            return

        land_raw(payload, source, congress, entity_id, self.state.batch_ts, root=self.config.landing_root)
        self.state.mark_done(key)
        self.stats.landed += 1

    def _fail(self, key: str, exc: Exception) -> None:
        logger.warning("Failed %s: %s", key, exc)
        self.state.record_failure(key, str(exc))
        self.stats.failed += 1

    @staticmethod
    def _progress(label: str, congress: int, i: int, total: int) -> None:
        if i % _PROGRESS_EVERY == 0 or i == total:
            logger.info("Congress %s %s: %d/%d", congress, label, i, total)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

_SCOPE_ARGS = ("congresses", "entities", "bill_types", "limit", "landing_root")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill congress.gov data into raw landing.")
    parser.add_argument("--congress", dest="congresses", type=int, nargs="+")
    parser.add_argument("--entities", nargs="+", choices=ENTITIES)
    parser.add_argument("--bill-types", nargs="+", help="e.g. hr s (default: all types)")
    parser.add_argument("--limit", type=int, help="Max items per list endpoint, for smoke tests")
    parser.add_argument("--landing-root", type=Path)
    parser.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    run = parser.add_mutually_exclusive_group()
    run.add_argument("--run-id", help="Start a run with this id, or resume it if it exists")
    run.add_argument("--resume", action="store_true", help="Resume the most recent run")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def build_run(args: argparse.Namespace) -> tuple[BackfillConfig, BackfillState]:
    run_id = args.run_id
    if args.resume:
        run_id = BackfillState.latest_run_id(args.state_root)
        if run_id is None:
            raise SystemExit(f"No runs to resume under {args.state_root}")

    if run_id and BackfillState.exists(args.state_root, run_id):
        state = BackfillState.load(args.state_root, run_id)
        overridden = [name for name in _SCOPE_ARGS if getattr(args, name) is not None]
        if overridden:
            logger.warning("Resuming %s with its original scope; ignoring %s", run_id, overridden)
        logger.info("Resuming %s (%d tasks already done)", run_id, state.done_count)
        return BackfillConfig.from_dict(state.config), state

    overrides = {name: getattr(args, name) for name in _SCOPE_ARGS if getattr(args, name) is not None}
    for name in ("congresses", "entities", "bill_types"):
        if name in overrides:
            overrides[name] = tuple(overrides[name])
    config = dataclasses.replace(BackfillConfig(), **overrides)
    state = BackfillState.create(args.state_root, run_id or new_run_id(), config.to_dict())
    logger.info("Starting %s", state.run_id)
    return config, state


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config, state = build_run(args)

    with CongressClient() as client:
        stats = Backfiller(client, config, state).run()

    logger.info(
        "Run %s finished: %d landed, %d skipped, %d failed",
        state.run_id, stats.landed, stats.skipped, stats.failed,
    )
    if stats.failed:
        logger.warning(
            "Failures logged to %s; re-run with --run-id %s to retry",
            state.run_dir / "failed.log", state.run_id,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
