import sys
from pathlib import Path

# Make `congress_client` importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))
