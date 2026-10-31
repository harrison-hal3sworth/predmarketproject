# Contains Claude generated code

"""
csv_writer.py

Writes the rows produced by src/pipeline/historical_snapshot.py to a
CSV file. Deliberately minimal — this is storage, not analysis:
no derived columns, no filtering, no aggregation. If you want
Excel/xlsx instead of CSV, swap in openpyxl here; the row schema
doesn't change either way.
"""

import csv
from pathlib import Path


def write_rows(rows: list[dict], output_path: str) -> Path:
    """Writes a list of flat dicts to CSV, using the union of all keys
    across rows as the header (so one row missing a field, e.g. no
    sportsbook match, doesn't break the write — it just leaves that
    cell blank)."""
    if not rows:
        raise ValueError("No rows to write")

    fieldnames = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {path}")
    return path
