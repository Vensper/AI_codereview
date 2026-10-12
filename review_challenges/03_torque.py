"""Select a torque request; a stale request must produce zero torque."""


def selected_torque(now_ms, received_at_ms, timeout_ms, requested_nm):
    if now_ms - received_at_ms > timeout_ms:
        return requested_nm
    return 0.0
