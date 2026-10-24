"""
backfill/state.py

Checkpointing for resumable backfill runs. Each run gets a directory:

    {state_root}/{run_id}/
        run.json     -- batch_ts + the config the run was started with
        done.log     -- one completed task key per line, append-only
        failed.log   -- one JSON object per failed task (key, error, at)

A resumed run reuses batch_ts and config, so everything it lands goes into
the same dt= partition with the same scope as the original attempt.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_STATE_ROOT = Path(".backfill_state")


def new_run_id(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"backfill-{now:%Y%m%dT%H%M%SZ}"


class BackfillState:
    def __init__(
        self, run_dir: Path, run_id: str, batch_ts: datetime, config: dict, done: set[str]
    ):
        self.run_dir = run_dir
        self.run_id = run_id
        self.batch_ts = batch_ts
        self.config = config
        self._done = done

    # ----------------------------------------------------------------
    # Lifecycle
    # ----------------------------------------------------------------

    @classmethod
    def create(
        cls,
        state_root: Path,
        run_id: str,
        config: dict,
        batch_ts: Optional[datetime] = None,
    ) -> "BackfillState":
        run_dir = Path(state_root) / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        batch_ts = batch_ts or datetime.now(timezone.utc)
        meta = {"run_id": run_id, "batch_ts": batch_ts.isoformat(), "config": config}
        (run_dir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return cls(run_dir, run_id, batch_ts, config, set())

    @classmethod
    def load(cls, state_root: Path, run_id: str) -> "BackfillState":
        run_dir = Path(state_root) / run_id
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        done_path = run_dir / "done.log"
        done = set()
        if done_path.exists():
            # A crash mid-append can leave a partial last line; it just won't
            # match any key, so that task is redone.
            done = {line.strip() for line in done_path.read_text(encoding="utf-8").splitlines()}
            done.discard("")
        return cls(run_dir, run_id, datetime.fromisoformat(meta["batch_ts"]), meta["config"], done)

    @staticmethod
    def exists(state_root: Path, run_id: str) -> bool:
        return (Path(state_root) / run_id / "run.json").exists()

    @staticmethod
    def latest_run_id(state_root: Path) -> Optional[str]:
        """Most recently started run, by its recorded batch_ts."""
        runs = []
        for meta_path in Path(state_root).glob("*/run.json"):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            runs.append((datetime.fromisoformat(meta["batch_ts"]), meta["run_id"]))
        return max(runs)[1] if runs else None

    # ----------------------------------------------------------------
    # Checkpoints
    # ----------------------------------------------------------------

    @property
    def done_count(self) -> int:
        return len(self._done)

    def is_done(self, key: str) -> bool:
        return key in self._done

    def mark_done(self, key: str) -> None:
        # Open-append-close per key: negligible next to a rate-limited HTTP
        # call, and nothing is lost if the process is killed.
        with open(self.run_dir / "done.log", "a", encoding="utf-8") as f:
            f.write(key + "\n")
        self._done.add(key)

    def record_failure(self, key: str, error: str) -> None:
        entry = {"key": key, "error": error, "at": datetime.now(timezone.utc).isoformat()}
        with open(self.run_dir / "failed.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
