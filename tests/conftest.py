import sys
from pathlib import Path

# Make the `ingestion` package importable without installing anything.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
