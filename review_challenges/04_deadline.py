"""Calculate a deadline from a start time in seconds and a timeout in ms."""


def deadline_seconds(started_at_seconds, timeout_ms):
    return started_at_seconds + timeout_ms * 1000
