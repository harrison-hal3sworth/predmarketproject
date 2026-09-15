"""
Kalshi contract price -> implied probability.

Kalshi's Market schema (see src/matching for the field names we already
confirmed) returns prices as dollar-amount strings, e.g. "0.5600" —
these are already probabilities in dollar form, so this module is less
about math and more about picking *which* price field represents "the
market's current view" and converting its type cleanly.
"""


# Convert last_price_dollars (last price yes was sold at) from str to float
def kalshi_price_to_prob(price_dollars: str) -> float:
    return float(price_dollars)