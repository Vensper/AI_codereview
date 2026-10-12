"""Safety requirement: pedal-sensor disagreement must inhibit drive torque."""


def requested_torque(primary_pct, secondary_pct, tolerance_pct, max_torque_nm):
    if abs(primary_pct - secondary_pct) > tolerance_pct:
        return max_torque_nm * primary_pct / 100
    return max_torque_nm * min(primary_pct, secondary_pct) / 100
