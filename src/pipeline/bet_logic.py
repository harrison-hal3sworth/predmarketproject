"""
Threshold check + paper bet creation — milestone 4.

Takes a snapshot row (from snapshot.py) and decides whether the gap
between the two probabilities is big enough to log a hypothetical bet,
per config['threshold_pct'].
"""

# TODO: def evaluate_snapshot(snapshot_row: dict, config: dict) -> dict | None
#   - compare snapshot_row['gap_pct'] against config['threshold_pct']
#   - if it clears the bar, decide which side to "bet" (the side Kalshi
#     is underpricing relative to the sportsbook) and at what stake
#     (config['flat_stake'] to start — Kelly sizing is a stretch goal)
#   - return a dict shaped for the Paper Bets sheet, or None if no bet

# TODO: decide and document your side-selection logic explicitly —
#   this is the actual "strategy" of the project and belongs in the
#   write-up later, so make sure it's easy to explain in one sentence