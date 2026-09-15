# TESTS GENERATED FROM CLAUDE
from src.pricing.devig import american_odds_to_prob, devig_two_way


def test_positive_odds():
    # +130 -> 100 / (130 + 100)
    assert round(american_odds_to_prob(130), 4) == round(100 / 230, 4)


def test_negative_odds():
    # -250 -> 250 / (250 + 100)
    assert round(american_odds_to_prob(-250), 4) == round(250 / 350, 4)


def test_devig_sums_to_one():
    # raw probabilities always sum to slightly more than 1.0 (the vig);
    # after de-vigging they should sum to exactly 1.0
    prob_a = american_odds_to_prob(-150)
    prob_b = american_odds_to_prob(130)
    fair_a, fair_b = devig_two_way(prob_a, prob_b)
    assert round(fair_a + fair_b, 6) == 1.0


def test_known_devig_example():
    # -150 / +130 example from earlier: raw 60.0% / 43.5%, sum 103.5%
    # fair probabilities should come out to ~58.0% / ~42.0%
    prob_a = american_odds_to_prob(-150)
    prob_b = american_odds_to_prob(130)
    fair_a, fair_b = devig_two_way(prob_a, prob_b)
    assert round(fair_a, 3) == 0.580
    assert round(fair_b, 3) == 0.420