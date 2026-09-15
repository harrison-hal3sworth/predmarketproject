"""
Entry point. Ties snapshot.run_snapshot_job and settlement.run_settlement_job
together on a schedule.
"""

# TODO: load config/config.yaml
# TODO: use APScheduler (or just cron + `python -m src.scheduler --once`
#   for simplicity) to run:
#     - run_snapshot_job periodically (how often depends on how close to
#       game time you want to catch the "closing line" — every 5-10 min?)
#     - run_settlement_job once or twice a day, well after games end