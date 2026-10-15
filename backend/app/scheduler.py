"""Automatic recrawl scheduling, called by the worker before every claim.

It deliberately shares the worker process; no third service is required.
"""


def enqueue_due(database, interval, now=None):
    return database.schedule_due(interval, now)
