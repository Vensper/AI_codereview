"""Create an independent subscriber list for each new notification batch."""


def add_subscriber(subscriber_id, subscribers=[]):
    subscribers.append(subscriber_id)
    return subscribers
