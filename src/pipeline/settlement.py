"""
Settlement — milestone 5.

After a game concludes, look up the result and close out any paper bet
that was placed on it.
"""

# TODO: def get_game_result(matched_pair: dict) -> str | None
#   - Kalshi markets settle to 'yes'/'no' once determined — check the
#     market's `result` field (see the Market schema: status becomes
#     'finalized'/'determined' and `result` is 'yes' or 'no')
#   - alternative: TheOddsAPI's /historical/settlements is Business-tier
#     only, so Kalshi's own result field is probably your free path

# TODO: def settle_bet(bet_row: dict, result: str) -> dict
#   - compute won/lost, payout, profit_loss

# TODO: def run_settlement_job(config: dict) -> None
#   - find open (unsettled) bets in the Paper Bets sheet whose game has
#     concluded, settle them, update running_bankroll