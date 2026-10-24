import sys
from pathlib import Path

# Make `congress_client`, `backfill` and `landing` importable without installing anything.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ingestion"))
