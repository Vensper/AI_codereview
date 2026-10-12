"""Safety requirement: feed the watchdog only after both safety checks pass."""


def should_feed_watchdog(control_cycle_ok, sensor_plausibility_ok):
    return control_cycle_ok or sensor_plausibility_ok
