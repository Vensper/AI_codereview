"""AUTOSAR CP R19-11 E2E Profile 1: advance the 0..14 sender counter."""


def next_profile1_counter(current_counter):
    return (current_counter + 1) & 0x0F
