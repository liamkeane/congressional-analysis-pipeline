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
