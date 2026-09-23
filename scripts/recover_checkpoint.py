"""Recovers a partial dataset from a checkpoint file left by a run that
didn't finish (OS restart, crash, power loss). Run this, inspect how far
it got, then either accept the partial data or rerun the scrape.

Usage:
    python scripts/recover_checkpoint.py data/raw_oddsharvester_mlb.checkpoint.ndjson data/raw_oddsharvester_mlb.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.fetchers.sportsbook_history import recover_from_checkpoint

if __name__ == "__main__":
    checkpoint = sys.argv[1] if len(sys.argv) > 1 else "data/raw_oddsharvester_mlb.checkpoint.ndjson"
    output = sys.argv[2] if len(sys.argv) > 2 else "data/raw_oddsharvester_mlb.json"
    recover_from_checkpoint(checkpoint, output)