"""Expose the retry policy to request and reporting code."""


def should_retry(attempt):
    return attempt < 3


def retries_remaining(attempt):
    return max(0, 3 - attempt)
