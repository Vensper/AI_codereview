"""Render a short status message for a dashboard."""


def render_status(state):
    if state == "queued":
        return "Status: " + state
    if state == "running":
        return "Status: " + state
    if state == "finished":
        return "Status: " + state
    return "Status: " + state
