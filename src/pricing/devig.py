"""
American odds -> de-vigged (fair) probability.

Two steps:
  1. Convert a single American odds value to its raw implied probability
  2. Normalize both sides of a two-way market so they sum to 100%,
     removing the sportsbook's vig
"""

from scipy.optimize import brentq
import numpy as np

# For 'plus' odds => prob = 100 / (odds + 100)
# For 'minus' odds => prob = abs(odds) / (abs(odds) + 100)
def american_odds_to_prob(odds: int) -> float:
    # For 'plus' odds => prob = 100 / (odds + 100)
    # For 'minus' odds => prob = abs(odds) / (abs(odds) + 100)
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
def devig_two_way(prob_a: float, prob_b: float, de_vig_strat: str) -> tuple[float, float]:
    match de_vig_strat:
        case "proportional":
            sum_probs = prob_a + prob_b
            devig_a = prob_a / sum_probs
            devig_b = prob_b / sum_probs

            return (devig_a, devig_b)

        case "additive":
            sum_probs = prob_a + prob_b
            overround = sum_probs - 1

            devig_a = prob_a - (overround / 2)
            devig_b = prob_b - (overround / 2)

            return (devig_a, devig_b)
        
        case "shin":
            sum_probs = prob_a + prob_b

            z = brentq(lambda z: (np.sqrt(z**2 + 4 * (1 - z) * prob_a**2 / sum_probs) - z) / (2 * (1 - z))
                            + (np.sqrt(z**2 + 4 * (1 - z) * prob_b**2 / sum_probs) - z) / (2 * (1 - z))
                            - 1,
                            0, 0.999,
                            )

            devig_a = (np.sqrt(z**2 + 4 * (1 - z) * prob_a**2 / sum_probs) - z) / (2 * (1 - z))
            devig_b = (np.sqrt(z**2 + 4 * (1 - z) * prob_b**2 / sum_probs) - z) / (2 * (1 - z))

            return (devig_a, devig_b)

        case "power":
            k = brentq(lambda x: prob_a**x + prob_b**x - 1, 0.01, 20)

            devig_a = prob_a**k
            devig_b = prob_b**k

            return (devig_a, devig_b)

        case "cheung":
            c = np.sqrt((prob_a / (1 - prob_a)) * (prob_b / (1 - prob_b)))

            devig_a = prob_a / (c + prob_a - c * prob_a)
            devig_b = prob_b / (c + prob_b - c * prob_b)

            return (devig_a, devig_b)

        case _:
            raise ValueError("Invalid choice of de-vigging method.")
