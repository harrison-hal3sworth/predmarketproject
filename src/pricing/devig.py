"""
American odds -> de-vigged (fair) probability.

Two steps:
  1. Convert a single American odds value to its raw implied probability
  2. Normalize both sides of a two-way market so they sum to 100%,
     removing the sportsbook's vig
"""

# For 'plus' odds => prob = 100 / (odds + 100)
# For 'minus' odds => prob = abs(odds) / (abs(odds) + 100)
def american_odds_to_prob(odds: int) -> float:
    # Compute probability for 'plus' (underdog) odds
    if odds > 0:
        prob = 100 / (odds + 100)

    # Compute probability for 'negative' (favorite) odds
    else:
        prob = abs(odds) / (abs(odds) + 100)

    return prob



# The two raw probabilities from american_odds_to_prob will sum to
# slightly more than 1.0 (that excess is the vig), must
# divide each by their sum so the pair sums to exactly 1.0
def devig_two_way(prob_a: float, prob_b: float) -> tuple[float, float]:
    sum_probs = prob_a + prob_b
    devig_a = prob_a / sum_probs
    devig_b = prob_b / sum_probs

    return (devig_a, devig_b)
